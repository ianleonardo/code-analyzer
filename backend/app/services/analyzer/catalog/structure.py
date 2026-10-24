"""Structural parser for Java, Kotlin, Swift and TypeScript declarations.

Unlike the symbol extractor in ``parsers.py`` it keeps string literals (annotation values such as
``@GetMapping("/owners/{id}")`` are needed), understands body-less interface methods (Spring Data repositories,
Retrofit and Feign clients), annotations with arguments, parameters with their annotations, fields, record /
primary-constructor components and enum constants. Line numbers are 1-based.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

_STRING = r'"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`'
_LEX = re.compile(_STRING + r"|//[^\n]*|/\*[\s\S]*?\*/")
_TOK = re.compile(_STRING + r"|[{}()\[\];\n]")
_STR_AT = re.compile(_STRING)
_STR_VALUE = re.compile(r'"""([\s\S]*?)"""|"((?:\\.|[^"\\\n])*)"|\'((?:\\.|[^\'\\\n])*)\'|`((?:\\.|[^`\\])*)`')

NEWLINE_LANGUAGES = {"Kotlin", "Swift", "TypeScript", "JavaScript", "Scala", "Groovy", "Dart"}


def strip_comments(text: str) -> str:
    """Comments become spaces (newlines kept, so offsets and line numbers stay valid); strings are kept."""

    def repl(match: re.Match) -> str:
        value = match.group(0)
        if value.startswith("//") or value.startswith("/*"):
            return re.sub(r"[^\n]", " ", value)
        return value

    return _LEX.sub(repl, text)


def string_values(text: str) -> list[str]:
    """The literal values of every string in ``text`` (escapes left as written)."""
    out = []
    for match in _STR_VALUE.finditer(text):
        value = next((g for g in match.groups() if g is not None), "")
        out.append(value)
    return out


def match_close(text: str, index: int) -> int:
    """Index of the bracket closing the one at ``index`` (strings skipped); len(text) - 1 when unbalanced."""
    opening = text[index]
    closing = {"(": ")", "[": "]", "{": "}", "<": ">"}[opening]
    depth = 0
    i = index
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            m = _STR_AT.match(text, i)
            if m:
                i = m.end()
                continue
        if opening == "<" and ch == "-" and i + 1 < n and text[i + 1] == ">":
            i += 2
            continue
        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1


def split_top(text: str, sep: str = ",") -> list[str]:
    """Split on ``sep`` outside brackets and strings."""
    parts, depth, start, i, n = [], 0, 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            m = _STR_AT.match(text, i)
            if m:
                i = m.end()
                continue
        if ch == "-" and i + 1 < n and text[i + 1] == ">":
            i += 2
            continue
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth = max(0, depth - 1)
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


# --- annotations ----------------------------------------------------------------------------------------------------


@dataclass
class Annotation:
    name: str
    args: str = ""

    def _parts(self) -> list[tuple[str, str]]:
        out = []
        for part in split_top(self.args):
            m = re.match(r"^([A-Za-z_]\w*)\s*=(?!=)\s*([\s\S]*)$", part)
            out.append((m.group(1), m.group(2).strip()) if m else ("", part))
        return out

    def raw(self, *keys: str) -> str | None:
        """Raw text of the first argument named in ``keys``; '' means the positional (value) argument."""
        parts = self._parts()
        for key in keys:
            for name, value in parts:
                if name == key or (key == "" and name == "" and value):
                    return value
        return None

    def strings(self, *keys: str) -> list[str]:
        value = self.raw(*keys)
        return string_values(value) if value else []

    def string(self, *keys: str) -> str:
        values = self.strings(*keys)
        return values[0] if values else ""

    def idents(self, *keys: str) -> list[str]:
        """Identifiers such as RequestMethod.POST -> POST or Owner.class / Owner::class -> Owner."""
        value = self.raw(*keys)
        if not value:
            return []
        value = re.sub(_STRING, "", value)
        names = re.findall(r"([A-Za-z_]\w*)(?:\.class|::class)", value)
        if names:
            return names
        return [part.split(".")[-1] for part in re.findall(r"[A-Za-z_][\w.]*", value)]

    def text(self) -> str:
        return f"@{self.name}({self.args})" if self.args else f"@{self.name}"


_ANN_START = re.compile(r"\s*@(?:(?:field|get|set|param|property|receiver|setparam|delegate|file):)?([A-Za-z_][\w.]*)")


def take_annotations(text: str, index: int = 0) -> tuple[list[Annotation], int]:
    annotations: list[Annotation] = []
    n = len(text)
    while True:
        m = _ANN_START.match(text, index)
        if not m or m.group(1) == "interface":
            break
        j = m.end()
        k = j
        while k < n and text[k] in " \t":
            k += 1
        args = ""
        if k < n and text[k] == "(":
            close = match_close(text, k)
            args = text[k + 1 : close].strip()
            j = close + 1
        annotations.append(Annotation(m.group(1).split(".")[-1], args))
        index = j
    return annotations, index


# --- declarations ---------------------------------------------------------------------------------------------------


@dataclass
class Param:
    name: str
    type: str = ""
    annotations: list[Annotation] = field(default_factory=list)
    default: str = ""
    label: str = ""

    def ann(self, *names: str) -> Annotation | None:
        return next((a for a in self.annotations if a.name in names), None)


@dataclass
class Member:
    kind: str  # method, constructor, field, enum_constants, other
    name: str
    annotations: list[Annotation] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    type: str = ""  # return type or field type
    params: list[Param] = field(default_factory=list)
    body: str = ""
    body_line: int = 0
    line: int = 0
    end_line: int = 0
    header: str = ""
    initializer: str = ""

    def ann(self, *names: str) -> Annotation | None:
        return next((a for a in self.annotations if a.name in names), None)

    def has(self, *names: str) -> bool:
        return any(a.name in names for a in self.annotations)

    @property
    def public(self) -> bool:
        return not ({"private", "protected", "fileprivate", "internal"} & set(self.modifiers))


@dataclass
class TypeDecl:
    name: str
    kind: str  # class, interface, enum, record, object, struct, protocol, extension, annotation, actor
    annotations: list[Annotation] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    bases: list[tuple[str, list[str]]] = field(default_factory=list)  # (simple name, generic args)
    params: list[Param] = field(default_factory=list)  # record components / primary constructor
    members: list[Member] = field(default_factory=list)
    types: list["TypeDecl"] = field(default_factory=list)
    enum_constants: list[str] = field(default_factory=list)
    line: int = 0
    end_line: int = 0
    header: str = ""
    outer: str = ""
    body: str = ""
    body_line: int = 0

    def ann(self, *names: str) -> Annotation | None:
        return next((a for a in self.annotations if a.name in names), None)

    def has(self, *names: str) -> bool:
        return any(a.name in names for a in self.annotations)

    def base_names(self) -> list[str]:
        return [b[0] for b in self.bases]

    def methods(self) -> list[Member]:
        return [m for m in self.members if m.kind in ("method", "constructor")]

    def fields(self) -> list[Member]:
        return [m for m in self.members if m.kind == "field"]


@dataclass
class FileStructure:
    path: str
    language: str
    package: str = ""
    imports: list[str] = field(default_factory=list)
    types: list[TypeDecl] = field(default_factory=list)
    functions: list[Member] = field(default_factory=list)
    fields: list[Member] = field(default_factory=list)
    clean: str = ""

    def all_types(self) -> list[TypeDecl]:
        out: list[TypeDecl] = []
        stack = list(self.types)
        while stack:
            item = stack.pop(0)
            out.append(item)
            stack[0:0] = item.types
        return out


_MODIFIERS = (
    r"public|private|protected|internal|static|final|abstract|open|override|sealed|inner|inline|suspend|lateinit|const|"
    r"operator|infix|tailrec|external|vararg|noinline|crossinline|actual|expect|synchronized|native|transient|volatile|"
    r"strictfp|default|readonly|async|export|declare|fileprivate|mutating|nonmutating|weak|unowned|lazy|dynamic|"
    r"convenience|required|indirect|nonisolated|non-sealed|"
    r"enum(?=\s+class\b)|data(?=\s+(?:class|object)\b)|annotation(?=\s+class\b)|companion(?=\s+object\b)|value(?=\s+class\b)|"
    r"fun(?=\s+interface\b)|class(?=\s+(?:func|var|let)\b)|unowned\(safe\)|private\(set\)|public\(set\)|internal\(set\)"
)
_MODS = re.compile(rf"\s*((?:(?:{_MODIFIERS})\s+)*)")
_TYPE_KW = re.compile(r"(class|interface|enum|record|object|struct|protocol|extension|actor|@interface)\b\s*([A-Za-z_][\w.]*)?")
_KT_FUN = re.compile(r"fun\s+(?:<[^>]*>\s*)?(?:[\w.<>?, ]+\.)?([A-Za-z_]\w*|`[^`]+`)\s*\(")
_SW_FUNC = re.compile(r"(?:func\s+([A-Za-z_]\w*|[^\s(<]+)\s*(?:<[^>]*>)?\s*\(|(init)[?!]?\s*(?:<[^>]*>)?\s*\(|(subscript)\s*\()")
_JAVA_FUNC = re.compile(r"(?:<[^;{}()]*?>\s*)?((?:[\w$.?]+(?:\s*<[^;{}()]*>)?(?:\s*\[\s*\])*\s+)*?)([A-Za-z_$][\w$]*)\s*\(")
_TS_FUNC = re.compile(r"(?:(?:get|set)\s+)?(?:\*\s*)?([A-Za-z_$][\w$]*)\s*[?!]?\s*(?:<[^()]*>)?\s*\(")
_KS_FIELD = re.compile(r"(val|var|let)\s+([A-Za-z_]\w*|`[^`]+`)\s*(?::\s*([^=\n{]+?))?\s*(?:(=|by\b)\s*([\s\S]*))?$")
_JAVA_FIELD = re.compile(r"((?:[\w$.?]+(?:\s*<[^;{}()]*>)?(?:\s*\[\s*\])*))\s+([A-Za-z_$][\w$]*)\s*(?:=\s*([\s\S]*?))?\s*;?\s*$")
_TS_FIELD = re.compile(r"([A-Za-z_$][\w$]*|'[^']+'|\"[^\"]+\")\s*([?!]?)\s*(?::\s*([\s\S]+?))?\s*(?:=\s*([\s\S]*?))?\s*[;,]?\s*$")
_CONTROL = {"if", "for", "while", "switch", "catch", "return", "new", "throw", "else", "do", "try", "synchronized", "when", "guard", "case", "super", "this"}


class _Parser:
    def __init__(self, text: str, language: str, path: str = ""):
        self.language = language
        self.path = path
        self.clean = strip_comments(text)
        self.newlines = [m.start() for m in re.finditer("\n", self.clean)]
        self.toks = [(m.start(), m.group(0)) for m in _TOK.finditer(self.clean) if len(m.group(0)) == 1]
        self.tok_pos = [t[0] for t in self.toks]
        self.nl = language in NEWLINE_LANGUAGES

    def line(self, pos: int) -> int:
        return bisect_right(self.newlines, pos - 1) + 1 if pos > 0 else 1

    # Splits a region into members: (start, end, block_open, block_close)
    def split(self, a: int, b: int) -> list[tuple[int, int, int | None, int | None]]:
        clean, out = self.clean, []
        lo, hi = bisect_left(self.tok_pos, a), bisect_left(self.tok_pos, b)
        start, paren, depth = a, 0, 0
        bo: int | None = None
        bc: int | None = None
        for k in range(lo, hi):
            pos, ch = self.toks[k]
            if ch in "([":
                paren += 1
            elif ch in ")]":
                paren = max(0, paren - 1)
            elif ch == "{":
                if depth == 0 and paren == 0 and bo is None:
                    bo = pos
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
                if depth == 0 and paren == 0 and bo is not None and bc is None:
                    bc = pos
                    nxt = self._next_char(pos + 1, b)
                    if nxt not in (";", ",", ")", ".", "(", "?", "!") and not (self.nl and nxt in (":", "=")):
                        out.append((start, pos + 1, bo, bc))
                        start, bo, bc = pos + 1, None, None
            elif ch == ";":
                if depth == 0 and paren == 0:
                    out.append((start, pos + 1, bo, bc))
                    start, bo, bc = pos + 1, None, None
            elif ch == "\n" and self.nl and depth == 0 and paren == 0 and (bo is None or bc is not None):
                if self._ends_statement(start, pos, b):
                    out.append((start, pos, bo, bc))
                    start, bo, bc = pos + 1, None, None
        if clean[start:b].strip():
            out.append((start, b, bo, bc))
        return [m for m in out if clean[m[0] : m[1]].strip(" \t\n;")]

    def _next_char(self, pos: int, limit: int) -> str:
        clean = self.clean
        while pos < limit and clean[pos] in " \t\r\n":
            pos += 1
        return clean[pos] if pos < limit else ""

    def _ends_statement(self, start: int, pos: int, limit: int) -> bool:
        seg = self.clean[start:pos].strip()
        if not seg:
            return False
        if re.match(r"(?:import|package)\s", seg.splitlines()[-1].strip()):
            return True
        anns, end = take_annotations(seg)
        if anns and not seg[end:].strip():
            return False  # annotations (possibly multi-line) attach to the declaration below
        if seg.endswith(("=", ",", "(", "[", ".", ":", "->", "&&", "||", "+", "-", "*", "/", "?", "|", "&", "<")):
            return False
        nxt_pos = pos + 1
        clean = self.clean
        while nxt_pos < limit and clean[nxt_pos] in " \t\r\n":
            nxt_pos += 1
        if nxt_pos >= limit:
            return True
        nxt = clean[nxt_pos]
        if nxt in ".?:={)]-&|+*/>":
            return not (nxt == "-" and clean[nxt_pos : nxt_pos + 2] != "->") or False
        word = re.match(r"[A-Za-z_]+", clean[nxt_pos : nxt_pos + 20])
        if word and word.group(0) in ("where", "else", "catch", "finally", "get", "set", "throws", "extends", "implements", "with", "as"):
            return False
        return True

    def parse(self) -> FileStructure:
        fs = FileStructure(self.path, self.language, clean=self.clean)
        m = re.search(r"^[ \t]*package\s+([\w.]+)", self.clean, re.M)
        if m:
            fs.package = m.group(1)
        fs.imports = re.findall(r"^[ \t]*import\s+(?:static\s+)?([\w.*]+)", self.clean, re.M)
        types, members = self._region(0, len(self.clean), "", top=True)
        fs.types = types
        fs.functions = [m for m in members if m.kind in ("method", "constructor")]
        fs.fields = [m for m in members if m.kind == "field"]
        return fs

    def _region(self, a: int, b: int, outer: str, top: bool = False, enum: bool = False) -> tuple[list[TypeDecl], list[Member]]:
        types: list[TypeDecl] = []
        members: list[Member] = []
        first = True
        for start, end, bo, bc in self.split(a, b):
            raw = self.clean[start:end]
            lead = len(raw) - len(raw.lstrip())
            start += lead
            header_end = bo if bo is not None else end
            header = self.clean[start:header_end]
            if top and re.match(r"\s*(package|import)\b", header):
                continue
            if enum and first:
                first = False
                constants = self._enum_constants(self.clean[start:end].rstrip("; \n\t"), bo, bc)
                if constants is not None:
                    members.append(Member("enum_constants", "", line=self.line(start), end_line=self.line(end), header=header.strip(), initializer=",".join(constants)))
                    continue
            first = False
            decl = self._declaration(start, end, header, bo, bc, outer)
            if isinstance(decl, TypeDecl):
                types.append(decl)
            elif decl is not None:
                members.append(decl)
        return types, members

    def _enum_constants(self, header: str, bo: int | None, bc: int | None) -> list[str] | None:
        text = header.strip()
        if self.language == "Swift":
            return None
        anns, idx = take_annotations(text)
        text = text[idx:].strip()
        if not text or not re.match(r"^[A-Za-z_]\w*\s*(?:\(|,|\{|$|=)", text):
            return None
        if re.match(r"^(?:public|private|protected|static|final|abstract|fun|val|var|const|override|internal|init|companion)\b", text):
            return None
        if bo is not None and re.match(r"^[A-Za-z_]\w*\s*\(", text) and not re.search(r",", text):
            # a single constant with a body, or actually a constructor / method: methods have a return type before the name
            if self.language == "Java" and not re.match(r"^[A-Z_][A-Z0-9_]*\b", text):
                return None
        names = []
        for part in split_top(text):
            m = re.match(r"^(?:@\w+(?:\([^)]*\))?\s*)*([A-Za-z_]\w*)", part)
            if m:
                names.append(m.group(1))
        return names

    def _declaration(self, start: int, end: int, header: str, bo: int | None, bc: int | None, outer: str):
        anns, idx = take_annotations(header)
        rest = header[idx:]
        mods_match = _MODS.match(rest)
        modifiers = mods_match.group(1).split() if mods_match else []
        rest = rest[mods_match.end() :] if mods_match else rest
        rest_stripped = rest.strip()
        line = self.line(start + idx + (mods_match.end() if mods_match else 0) + (len(rest) - len(rest.lstrip())))
        end_line = self.line(end - 1)
        body = self.clean[bo + 1 : bc] if bo is not None and bc is not None else ""
        body_line = self.line(bo) if bo is not None else 0
        tm = _TYPE_KW.match(rest_stripped)
        if tm and (bo is not None or tm.group(1) in ("interface", "class", "record", "struct", "protocol", "object", "enum")):
            if not (tm.group(1) == "object" and self.language not in ("Kotlin", "Scala")) and not (tm.group(1) == "struct" and self.language not in ("Swift",)):
                return self._type(tm, rest_stripped, anns, modifiers, line, end_line, bo, bc, header, outer)
        lang = self.language
        if lang == "Swift" and re.match(r"case\b", rest_stripped):
            names = [re.match(r"\s*([A-Za-z_]\w*)", p).group(1) for p in split_top(rest_stripped[4:]) if re.match(r"\s*[A-Za-z_]", p)]
            return Member("enum_constants", "", anns, modifiers, line=line, end_line=end_line, header=rest_stripped, initializer=",".join(names))
        fm = None
        kind = "method"
        if lang == "Kotlin":
            fm = _KT_FUN.match(rest_stripped)
            if not fm:
                cm = re.match(r"constructor\s*\(", rest_stripped)
                if cm:
                    kind, fm = "constructor", cm
        elif lang == "Swift":
            fm = _SW_FUNC.match(rest_stripped)
            if fm and (fm.group(2) or fm.group(3)):
                kind = "constructor" if fm.group(2) else "method"
        elif lang in ("TypeScript", "JavaScript"):
            fm = re.match(r"(?:async\s+)?(?:function\s*\*?\s*)?([A-Za-z_$][\w$]*)\s*(?:<[^()]*>)?\s*\(", rest_stripped)
            if fm and fm.group(1) == "constructor":
                kind = "constructor"
        else:
            fm = _JAVA_FUNC.match(rest_stripped)
            if fm and (fm.group(2) in _CONTROL or (fm.group(1) or "").strip() in ("new", "return", "throw")):
                fm = None
            if fm and not (fm.group(1) or "").strip():
                kind = "constructor" if fm.group(2) == outer.split(".")[-1] else "method"
                if kind == "method" and lang == "Java":
                    fm = None  # a call statement, not a declaration
        if fm:
            name_group = next((g for g in fm.groups() if g), "")
            if lang == "Java":
                name_group = fm.group(2)
            name = (name_group or "").strip("`")
            if lang == "Swift" and kind == "constructor":
                name = "init"
            open_paren = fm.end() - 1
            close = match_close(rest_stripped, open_paren)
            params = parse_params(rest_stripped[open_paren + 1 : close], lang)
            tail = rest_stripped[close + 1 :]
            ret = ""
            initializer = ""
            if lang == "Java":
                ret = (fm.group(1) or "").strip()
            elif lang in ("Kotlin", "TypeScript", "JavaScript"):
                rm = re.match(r"\s*:\s*([^={]+?)\s*(?:=\s*([\s\S]*))?$", tail)
                if rm:
                    ret = rm.group(1).strip()
                    initializer = (rm.group(2) or "").strip()
                else:
                    em = re.match(r"\s*=\s*([\s\S]*)$", tail)
                    if em:
                        initializer = em.group(1).strip()
            elif lang == "Swift":
                rm = re.search(r"->\s*([^{]+?)\s*(?:where\b[^{]*)?$", tail.strip())
                if rm:
                    ret = rm.group(1).strip()
            if lang == "Java" and "throws" in tail:
                pass
            if initializer:
                if bo is not None:
                    initializer = initializer + self.clean[bo:end]
                body = initializer
                body_line = line
            return Member(kind, name, anns, modifiers, ret, params, body, body_line, line, end_line, header.strip()[:600], initializer)
        if lang in ("Kotlin", "Swift"):
            vm = _KS_FIELD.match(rest_stripped)
            if vm:
                init = (vm.group(5) or "").strip()
                if init and bo is not None:
                    init += self.clean[bo:end]
                return Member("field", vm.group(2).strip("`"), anns, modifiers + [vm.group(1)], (vm.group(3) or "").strip(), [], body, body_line, line, end_line, header.strip()[:600], init)
            return None
        if lang in ("TypeScript", "JavaScript"):
            vm = _TS_FIELD.match(rest_stripped)
            if vm:
                return Member("field", vm.group(1).strip("'\""), anns, modifiers + (["optional"] if vm.group(2) == "?" else []), (vm.group(3) or "").strip(), [], body, body_line, line, end_line, header.strip()[:600], (vm.group(4) or "").strip())
            return None
        jm = _JAVA_FIELD.match(rest_stripped)
        if jm and jm.group(1).split(".")[-1] not in _CONTROL and jm.group(2) not in _CONTROL:
            return Member("field", jm.group(2), anns, modifiers, jm.group(1).strip(), [], "", 0, line, end_line, header.strip()[:600], (jm.group(3) or "").strip())
        return None

    def _type(self, tm: re.Match, rest: str, anns, modifiers, line, end_line, bo, bc, header, outer) -> TypeDecl:
        kind = tm.group(1)
        if kind == "@interface":
            kind = "annotation"
        if kind == "class" and "enum" in modifiers:
            kind = "enum"
        if kind == "class" and "annotation" in modifiers:
            kind = "annotation"
        name = (tm.group(2) or ("Companion" if "companion" in modifiers else "")).strip()
        after = rest[tm.end() :]
        # generics
        after = after.lstrip()
        if after.startswith("<"):
            close = match_close(after, 0)
            after = after[close + 1 :]
        params: list[Param] = []
        after_stripped = after.lstrip()
        # Kotlin: class Foo @Inject constructor(...)
        ctor = re.match(r"(?:@\w+(?:\([^)]*\))?\s*)*(?:(?:private|protected|internal|public)\s+)?constructor\s*(?=\()", after_stripped)
        if ctor:
            after_stripped = after_stripped[ctor.end():]
        if after_stripped.startswith("("):
            close = match_close(after_stripped, 0)
            params = parse_params(after_stripped[1:close], self.language)
            after = after_stripped[close + 1 :]
        bases_text = after.strip()
        bases_text = re.sub(r"\bwhere\b[\s\S]*$", "", bases_text).strip()
        bases_text = re.sub(r"^(?::|extends\b|implements\b)", "", bases_text).strip()
        bases_text = re.sub(r"\b(extends|implements|permits|with)\b", ",", bases_text)
        bases: list[tuple[str, list[str]]] = []
        if "permits" in after:
            bases_text = bases_text.split("permits")[0]
        for part in split_top(bases_text):
            part = re.sub(r"\([\s\S]*\)\s*$", "", part.strip())  # Kotlin super constructor call
            part = re.sub(r"\bby\b[\s\S]*$", "", part).strip()
            gm = re.match(r"^([\w.]+)\s*(?:<([\s\S]*)>)?", part)
            if gm:
                args = [simple_type(a) for a in split_top(gm.group(2) or "")]
                bases.append((gm.group(1).split(".")[-1], args))
        decl = TypeDecl(name, kind, anns, modifiers, bases, params, line=line, end_line=end_line, header=header.strip()[:600], outer=outer)
        if bo is not None and bc is not None:
            decl.body = self.clean[bo + 1 : bc]
            decl.body_line = self.line(bo)
            qualified = f"{outer}.{name}" if outer else name
            nested, members = self._region(bo + 1, bc, qualified, enum=kind == "enum")
            decl.types = nested
            decl.members = members
            for member in members:
                if member.kind == "enum_constants":
                    decl.enum_constants += [c for c in member.initializer.split(",") if c]
        return decl


def parse_params(text: str, language: str) -> list[Param]:
    params: list[Param] = []
    for part in split_top(text):
        anns, idx = take_annotations(part)
        rest = part[idx:].strip()
        rest = re.sub(r"^(?:final|val|var|private|protected|public|internal|override|vararg|readonly|inout|let)\s+", "", rest)
        rest = re.sub(r"^(?:final|val|var|private|protected|public|internal|override|vararg|readonly)\s+", "", rest)
        default = ""
        if language in ("Kotlin", "Swift", "TypeScript", "JavaScript", "Scala", "Dart") or ":" in rest.split("=")[0] and language != "Java":
            eq = split_top(rest, "=")
            if len(eq) > 1:
                rest, default = eq[0], "=".join(eq[1:]).strip()
            if ":" in rest:
                before, _, after = rest.partition(":")
                words = before.split()
                label = ""
                name = words[-1] if words else before.strip()
                if language == "Swift" and len(words) >= 2:
                    label = words[0]
                params.append(Param(name.strip("?`"), after.strip(), anns, default, label))
            elif rest:
                params.append(Param(rest.strip("?"), "", anns, default))
            continue
        tokens = rest.replace("...", "[] ").split()
        if not tokens:
            continue
        name = tokens[-1]
        type_ = " ".join(tokens[:-1])
        params.append(Param(name, type_, anns, default))
    return params


def simple_type(type_text: str) -> str:
    """Outer simple type name: Optional<List<Owner>> -> Optional; com.x.Owner -> Owner; Owner? -> Owner."""
    text = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", type_text or "").strip()
    text = text.rstrip("?!").strip()
    m = re.match(r"^\[\s*([\w.]+)", text)
    if m:
        return m.group(1).split(".")[-1]
    m = re.match(r"^([\w.$]+)", text)
    return m.group(1).split(".")[-1] if m else text


def type_args(type_text: str) -> list[str]:
    text = (type_text or "").strip()
    i = text.find("<")
    if i < 0:
        return []
    close = match_close(text, i)
    return [a.strip() for a in split_top(text[i + 1 : close])]


def unwrap_type(type_text: str, wrappers: set[str]) -> tuple[str, list[str]]:
    """Peels wrapper types; returns (innermost type text, list of wrappers peeled)."""
    peeled: list[str] = []
    text = (type_text or "").strip()
    for _ in range(6):
        outer = simple_type(text)
        if outer in wrappers:
            args = type_args(text)
            if not args:
                break
            peeled.append(outer)
            text = args[-1] if outer in ("Map", "HashMap") else args[0]
            continue
        sm = re.match(r"^\[\s*([\s\S]+?)\s*\]\s*[?!]?$", text)  # Swift [Owner]
        if sm:
            peeled.append("Array")
            text = sm.group(1)
            continue
        if text.endswith("[]"):
            peeled.append("Array")
            text = text[:-2].strip()
            continue
        break
    return text, peeled


def parse_structure(text: str, language: str, path: str = "") -> FileStructure:
    return _Parser(text, language, path).parse()


def parse_block_members(text: str, language: str) -> list[Member]:
    """Members of a single block body (e.g. a TypeScript interface body)."""
    parser = _Parser(text, language)
    _, members = parser._region(0, len(parser.clean), "")
    return members
