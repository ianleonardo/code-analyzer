"""Stage 3 (tier 1): syntactic extraction of symbols, imports, calls, inheritance and complexity per file.

Python is parsed with the standard-library AST (exact). Other languages use declarative pattern extractors with
brace / indentation scope tracking and a nested-scope guard, so calls inside local closures are attributed to the
enclosing top-level symbol rather than leaking to the module. When the optional `tree_sitter_language_pack` package
is installed, its parsers refine the symbol boundaries for the languages it supports.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "function", "new", "else", "do", "try", "typeof", "sizeof",
    "await", "yield", "throw", "case", "delete", "void", "in", "of", "instanceof", "super", "this", "elif", "with",
    "assert", "lambda", "not", "and", "or", "print", "using", "lock", "foreach", "synchronized", "match", "when",
    "select", "from", "where", "insert", "update", "values", "set", "exec", "def", "class", "import", "go", "defer",
    "func", "fn", "let", "var", "const", "static", "public", "private", "protected", "internal", "elseif", "unless",
    "until", "loop", "end", "begin", "rescue", "ensure", "require", "include", "extends", "implements", "raise",
}


@dataclass
class Symbol:
    name: str
    kind: str  # class, interface, struct, enum, trait, function, method, constructor, type, record, program, paragraph, procedure, table, view
    line_start: int
    line_end: int
    parent: str = ""
    complexity: int = 1
    signature: str = ""
    exported: bool = False
    bases: list[str] = field(default_factory=list)  # extends / implements
    injects: list[str] = field(default_factory=list)  # constructor / field injection types
    decorators: list[str] = field(default_factory=list)

    @property
    def qualified(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name


@dataclass
class ImportRef:
    module: str
    names: list[str] = field(default_factory=list)
    line: int = 0
    level: int = 0  # Python relative-import level
    kind: str = "import"  # import, require, dynamic, include, copy, use, mod, call


@dataclass
class ParseResult:
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    calls: dict[str, int] = field(default_factory=dict)  # called name -> count
    member_refs: set[str] = field(default_factory=set)  # identifiers used as Type / pkg.Name references
    complexity: int = 1
    namespace: str = ""  # package / namespace / module declared by the file
    annotations: set[str] = field(default_factory=set)
    routes: list[str] = field(default_factory=list)
    has_main: bool = False
    parse_mode: str = "pattern"  # ast, pattern, tree-sitter
    errors: int = 0


# --- shared helpers -----------------------------------------------------------------------------------------------

_BRANCH = re.compile(r"\b(if|elif|else if|for|foreach|while|case|catch|except|when|unless|until|and|or)\b|&&|\|\||\?\?|(?<![?.])\?(?![.?:])")
_CALL = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(")
_METHOD_CALL = re.compile(r"\.\s*([A-Za-z_$][\w$]*)\s*\(")
_TYPE_REF = re.compile(r"\b([A-Z][A-Za-z0-9_]{2,})\b")
_STRINGS = re.compile(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`", re.S)
_ROUTE = re.compile(
    r"""(?:@(?:Get|Post|Put|Delete|Patch|Request)Mapping|@(?:Get|Post|Put|Delete|Patch|All)|@(?:app|router|bp|blueprint|api)\.(?:get|post|put|delete|patch|route)|
         \b(?:app|router|server|r|e|g|api)\.(?:get|post|put|delete|patch|route|handle|HandleFunc|GET|POST|PUT|DELETE)|
         @Path|\[Http(?:Get|Post|Put|Delete|Patch)|\[Route|path)\s*\(\s*(?:value\s*=\s*)?["'`]([^"'`]{1,160})["'`]""",
    re.X,
)


def strip_comments_and_strings(text: str, language: str) -> str:
    """Blank out comments and string literals (keeping newlines) so patterns do not match inside them."""
    def blank(match: re.Match) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    if language in ("Python", "Ruby", "Shell", "Perl", "R", "Elixir"):
        text = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', blank, text)
        text = _STRINGS.sub(lambda m: m.group(0)[0] + " " * (len(m.group(0)) - 2) + m.group(0)[-1] if "\n" not in m.group(0) else blank(m), text)
        text = re.sub(r"#[^\n]*", blank, text)
        return text
    if language == "SQL" or language == "PL/SQL":
        text = re.sub(r"/\*[\s\S]*?\*/", blank, text)
        return re.sub(r"--[^\n]*", blank, text)
    text = re.sub(r"/\*[\s\S]*?\*/", blank, text)
    text = _STRINGS.sub(lambda m: m.group(0)[0] + " " * (len(m.group(0)) - 2) + m.group(0)[-1] if "\n" not in m.group(0) else blank(m), text)
    text = re.sub(r"(?<![:\"'])//[^\n]*", blank, text)
    return text


def branch_count(code: str) -> int:
    return len(_BRANCH.findall(code))


def _count_calls(code: str, into: dict[str, int]) -> None:
    for match in _CALL.finditer(code):
        name = match.group(1)
        if name not in KEYWORDS and len(name) > 1:
            into[name] = into.get(name, 0) + 1
    for match in _METHOD_CALL.finditer(code):
        name = match.group(1)
        if name not in KEYWORDS and len(name) > 1:
            into[name] = into.get(name, 0) + 1


def block_end(lines: list[str], start: int) -> int:
    """Index of the line closing the brace block that opens at or after `start` (comment/string-free lines)."""
    depth = 0
    opened = False
    for index in range(start, min(len(lines), start + 5000)):
        for char in lines[index]:
            if char == "{":
                depth += 1
                opened = True
            elif char == "}":
                depth -= 1
                if opened and depth <= 0:
                    return index
        if not opened and index > start + 3:
            # declaration without a body (abstract method, interface member, prototype)
            return start
        if not opened and lines[index].rstrip().endswith(";"):
            return index
    return min(len(lines) - 1, start)


def indent_end(lines: list[str], start: int, keyword_end: bool = False) -> int:
    """End of an indentation (or `end`-terminated) block that begins at `start`."""
    base = len(lines[start]) - len(lines[start].lstrip())
    last = start
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base:
            if keyword_end and line.strip().startswith("end"):
                return index
            return last
        last = index
    return last


# --- Python (exact) -----------------------------------------------------------------------------------------------


class _PyVisitor(ast.NodeVisitor):
    def __init__(self, result: ParseResult) -> None:
        self.result = result
        self.stack: list[str] = []
        self.function_depth = 0

    @staticmethod
    def _complexity(node: ast.AST) -> int:
        score = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert)):
                score += 1
            elif isinstance(child, ast.BoolOp):
                score += max(1, len(child.values) - 1)
            elif isinstance(child, ast.comprehension):
                score += 1 + len(child.ifs)
            elif hasattr(ast, "match_case") and isinstance(child, ast.match_case):
                score += 1
        return score

    @staticmethod
    def _name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Call):
            return _PyVisitor._name(node.func)
        if isinstance(node, ast.Subscript):
            return _PyVisitor._name(node.value)
        return ""

    def _decorators(self, node) -> list[str]:
        names = []
        for decorator in node.decorator_list:
            try:
                names.append(ast.unparse(decorator)[:160])
            except Exception:  # noqa: BLE001
                names.append(self._name(decorator))
        return names

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [self._name(base) for base in node.bases if self._name(base)]
        decorators = self._decorators(node)
        symbol = Symbol(
            node.name, "class", node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno,
            ".".join(self.stack), self._complexity(node), f"class {node.name}({', '.join(bases)})",
            not node.name.startswith("_") and not self.stack, bases, [], decorators,
        )
        if self.function_depth == 0:
            self.result.symbols.append(symbol)
        self.result.annotations.update(d.split("(")[0] for d in decorators)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _function(self, node) -> None:
        decorators = self._decorators(node)
        in_class = bool(self.stack) and self.function_depth == 0
        kind = "method" if in_class else "function"
        if node.name == "__init__":
            kind = "constructor"
        try:
            args = ast.unparse(node.args)
        except Exception:  # noqa: BLE001
            args = ""
        injects: list[str] = []
        for arg in node.args.args + node.args.kwonlyargs:
            if arg.annotation is not None:
                ann = self._name(arg.annotation)
                if ann and ann[:1].isupper():
                    injects.append(ann)
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
            if isinstance(default, ast.Call) and self._name(default.func) == "Depends" and default.args:
                injects.append(self._name(default.args[0]))
        if self.function_depth == 0:
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            self.result.symbols.append(
                Symbol(
                    node.name, kind, node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno,
                    ".".join(self.stack), self._complexity(node), f"{prefix} {node.name}({args[:200]})",
                    not node.name.startswith("_"), [], injects if kind in ("constructor", "function", "method") else [],
                    decorators,
                )
            )
        for decorator in decorators:
            self.result.annotations.add(decorator.split("(")[0])
            match = re.search(r"\.(get|post|put|delete|patch|route|api_route|websocket)\(\s*['\"]([^'\"]+)", decorator)
            if match:
                self.result.routes.append(f"{match.group(1).upper()} {match.group(2)}")
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.result.imports.append(ImportRef(alias.name, [], node.lineno, 0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.result.imports.append(ImportRef(node.module or "", [alias.name for alias in node.names], node.lineno, node.level))

    def visit_Call(self, node: ast.Call) -> None:
        name = self._name(node.func)
        if name:
            self.result.calls[name] = self.result.calls.get(name, 0) + 1
        if name in ("import_module", "__import__") and node.args and isinstance(node.args[0], ast.Constant):
            self.result.imports.append(ImportRef(str(node.args[0].value), [], node.lineno, 0, "dynamic"))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id[:1].isupper():
            self.result.member_refs.add(node.id)

    def visit_If(self, node: ast.If) -> None:
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)
        ):
            self.result.has_main = True
        self.generic_visit(node)


def parse_python(text: str) -> ParseResult:
    result = ParseResult(parse_mode="ast")
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        fallback = _parse_generic(text, "Python")
        fallback.errors = 1
        return fallback
    visitor = _PyVisitor(result)
    visitor.visit(tree)
    result.complexity = _PyVisitor._complexity(tree)
    return result


# --- pattern extractors -------------------------------------------------------------------------------------------

_JS_IMPORT = re.compile(r"""(?:^|\n)\s*(?:import|export)\s+(?:type\s+)?(?:[\w*{}\s,$]+?\s+from\s+)?["']([^"']+)["']""")
_JS_REQUIRE = re.compile(r"""\brequire\s*\(\s*["']([^"']+)["']\s*\)""")
_JS_DYNAMIC = re.compile(r"""\bimport\s*\(\s*["']([^"']+)["']\s*\)""")
_JS_IMPORT_NAMES = re.compile(r"""import\s+(?:type\s+)?(?:(\w+)\s*,?\s*)?(?:\{([^}]*)\})?\s*(?:\*\s+as\s+(\w+))?\s*from\s+["']([^"']+)["']""")
_JS_CLASS = re.compile(r"^[ \t]*(export\s+)?(default\s+)?(abstract\s+)?(class|interface|enum)\s+([A-Za-z_$][\w$]*)(?:\s*<[^{]*?>)?(?:\s+extends\s+([\w$.<>,\s]+?))?(?:\s+implements\s+([\w$.<>,\s]+?))?\s*\{", re.M)
_JS_TYPE = re.compile(r"^[ \t]*(export\s+)?type\s+([A-Za-z_$][\w$]*)\s*(?:<[^=]*>)?\s*=", re.M)
_JS_FUNC = re.compile(r"^[ \t]*(export\s+)?(default\s+)?(async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*(?:<[^(]*>)?\s*\(", re.M)
_JS_ARROW = re.compile(r"^[ \t]*(export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::\s*[^=]+)?=\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=]+)?=>", re.M)
_JS_FUNC_EXPR = re.compile(r"^[ \t]*(export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function\b", re.M)
_JS_METHOD = re.compile(r"^\s+(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|protected|static|readonly|async|override|abstract|get|set)\s+)*\*?\s*([A-Za-z_$][\w$]*)\s*(?:<[^(]*>)?\s*\(([^)]*)\)\s*(?::\s*[^{;]+)?\{", re.M)
_TS_DECORATOR = re.compile(r"@([A-Z]\w*)\s*\(")


def parse_javascript(text: str, language: str) -> ParseResult:
    result = ParseResult()
    clean = strip_comments_and_strings(text, language)
    lines = clean.splitlines()
    for regex, kind in ((_JS_IMPORT, "import"), (_JS_REQUIRE, "require"), (_JS_DYNAMIC, "dynamic")):
        for match in regex.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            result.imports.append(ImportRef(match.group(1), [], line, 0, kind))
    for match in _JS_IMPORT_NAMES.finditer(text):
        names = [match.group(1)] if match.group(1) else []
        if match.group(2):
            names += [part.split(" as ")[0].strip() for part in match.group(2).split(",") if part.strip()]
        for item in result.imports:
            if item.module == match.group(4) and not item.names:
                item.names = [name.replace("type ", "").strip() for name in names if name]
                break
    result.annotations.update(_TS_DECORATOR.findall(clean))
    classes: list[tuple[int, int, str]] = []
    for match in _JS_CLASS.finditer(clean):
        start = clean.count("\n", 0, match.start(5))
        end = block_end(lines, start)
        bases = []
        for group in (match.group(6), match.group(7)):
            if group:
                bases += [re.sub(r"<.*", "", part).strip().split(".")[-1] for part in group.split(",") if part.strip()]
        kind = match.group(4)
        result.symbols.append(Symbol(match.group(5), kind, start + 1, end + 1, "", 1, lines[start].strip()[:200], bool(match.group(1)), bases))
        classes.append((start, end, match.group(5)))
    for match in _JS_TYPE.finditer(clean):
        start = clean.count("\n", 0, match.start())
        result.symbols.append(Symbol(match.group(2), "type", start + 1, start + 1, "", 1, lines[start].strip()[:200], bool(match.group(1))))
    for regex, name_group, exported_group in ((_JS_FUNC, 4, 1), (_JS_ARROW, 2, 1), (_JS_FUNC_EXPR, 2, 1)):
        for match in regex.finditer(clean):
            start = clean.count("\n", 0, match.start(name_group))
            if any(c_start < start <= c_end for c_start, c_end, _ in classes):
                continue
            if _nested(lines, start):
                continue
            end = block_end(lines, start) if "{" in "".join(lines[start : start + 3]) else start
            body = "\n".join(lines[start : end + 1])
            result.symbols.append(
                Symbol(match.group(name_group), "function", start + 1, end + 1, "", 1 + branch_count(body),
                       lines[start].strip()[:200], bool(match.group(exported_group)))
            )
    for c_start, c_end, class_name in classes:
        segment = lines[c_start + 1 : c_end]
        class_indent = None
        for offset, line in enumerate(segment):
            match = _JS_METHOD.match(line)
            if not match or match.group(1) in KEYWORDS:
                continue
            indent = len(line) - len(line.lstrip())
            if class_indent is None:
                class_indent = indent
            if indent > class_indent:
                continue  # nested-scope guard
            index = c_start + 1 + offset
            end = block_end(lines, index)
            body = "\n".join(lines[index : end + 1])
            name = match.group(1)
            injects = []
            if name == "constructor":
                injects = re.findall(r":\s*([A-Z][\w$]*)", match.group(2) or "")
            result.symbols.append(
                Symbol(name, "constructor" if name == "constructor" else "method", index + 1, end + 1, class_name,
                       1 + branch_count(body), line.strip()[:200], not name.startswith("#") and "private" not in line, [], injects)
            )
    _count_calls(clean, result.calls)
    result.member_refs.update(_TYPE_REF.findall(clean))
    result.complexity = 1 + branch_count(clean)
    result.routes += [m.group(1) for m in _ROUTE.finditer(text)][:50]
    return result


def _nested(lines: list[str], index: int) -> bool:
    """A declaration indented deeper than the file's top level sits inside another function or block."""
    line = lines[index]
    return len(line) - len(line.lstrip()) >= 4 and not line.lstrip().startswith("export")


# Java, Kotlin, C#, Scala, Groovy, Swift, Dart, PHP, C/C++ ------------------------------------------------------------

_JAVA_PACKAGE = re.compile(r"^[ \t]*package\s+([\w.]+)\s*;?", re.M)
_JAVA_IMPORT = re.compile(r"^[ \t]*import\s+(static\s+)?(\w+(?:\.\w+)*)(\.\*)?\s*;?", re.M)
_CS_NAMESPACE = re.compile(r"^[ \t]*namespace\s+([\w.]+)", re.M)
_CS_USING = re.compile(r"^[ \t]*using\s+(?:static\s+)?(?:\w+\s*=\s*)?([\w.]+)\s*;", re.M)
_PHP_NAMESPACE = re.compile(r"^[ \t]*namespace\s+([\w\\]+)\s*;", re.M)
_PHP_USE = re.compile(r"^[ \t]*use\s+([\w\\]+)(?:\s+as\s+\w+)?\s*;", re.M)
_PHP_INCLUDE = re.compile(r"""\b(?:require|include)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*)?["']([^"']+)["']""")
_C_INCLUDE = re.compile(r"""^\s*#\s*include\s*["<]([^">]+)[">]""", re.M)
_TYPE_DECL = re.compile(
    r"^[ \t]*(?:@[\w.]+(?:\([^)\n]*\))?[ \t]*)*(?:(?:public|private|protected|internal|static|final|abstract|sealed|partial|open|data|inline|value|enum|annotation|export|default|readonly|unsafe|fileprivate)\s+)*"
    r"(class|interface|enum|record|struct|object|trait|protocol|extension)\s+([A-Za-z_]\w*)"
    r"(?:\s*<[^{>]*(?:<[^>]*>)?[^{>]*>)?(?:\s*\([^)]*\))?"
    r"(?:\s*(?::|extends|implements|with)\s*([^{]+?))?\s*(?:where[^{]*)?\{",
    re.M,
)
_JAVA_METHOD = re.compile(
    r"^[ \t]*(?:@[\w.]+(?:\([^)\n]*\))?[ \t]*)*"
    r"(?:(?:public|private|protected|internal|static|final|abstract|synchronized|native|virtual|override|async|sealed|partial|extern|unsafe|open|suspend|inline|operator|default|new)[ \t]+)*"
    r"(?:fun[ \t]+(?:<[^>\n]+>[ \t]*)?(?:[\w.]+\.)?|func[ \t]+|function[ \t]+|def[ \t]+)?"
    r"(?:[\w<>\[\],.?]+[ \t]+){0,6}?([A-Za-z_]\w*)[ \t]*\(([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*)\)[ \t]*"
    r"(?::[ \t]*[\w<>\[\],.?][\w<>\[\],.? \t]*)?(?:throws[ \t]+[\w., \t]+)?(?:[ \t]*where[^{\n]*)?\s*\{",
    re.M,
)
_ANNOTATION = re.compile(r"@([A-Z]\w*)|\[([A-Z]\w*)(?:\(|\])")


def parse_brace_language(text: str, language: str) -> ParseResult:
    result = ParseResult()
    clean = strip_comments_and_strings(text, language)
    lines = clean.splitlines()
    namespace_match = (_CS_NAMESPACE if language in ("C#", "VB.NET", "F#") else _PHP_NAMESPACE if language == "PHP" else _JAVA_PACKAGE).search(text)
    if namespace_match:
        result.namespace = namespace_match.group(1).replace("\\", ".")
    if language in ("C#", "VB.NET", "F#"):
        for match in _CS_USING.finditer(text):
            result.imports.append(ImportRef(match.group(1), [], text.count("\n", 0, match.start()) + 1, 0, "using"))
    elif language == "PHP":
        for match in _PHP_USE.finditer(text):
            result.imports.append(ImportRef(match.group(1).replace("\\", "."), [], text.count("\n", 0, match.start()) + 1, 0, "use"))
        for match in _PHP_INCLUDE.finditer(text):
            result.imports.append(ImportRef(match.group(1), [], text.count("\n", 0, match.start()) + 1, 0, "include"))
    elif language in ("C", "C++", "C/C++ Header", "Objective-C"):
        for match in _C_INCLUDE.finditer(text):
            result.imports.append(ImportRef(match.group(1), [], text.count("\n", 0, match.start()) + 1, 0, "include"))
    else:
        for match in _JAVA_IMPORT.finditer(text):
            module = match.group(2) + (".*" if match.group(3) else "")
            result.imports.append(ImportRef(module, [], text.count("\n", 0, match.start()) + 1, 0, "import"))
    for match in _ANNOTATION.finditer(clean if language not in ("C#",) else text):
        name = match.group(1) or match.group(2)
        if name:
            result.annotations.add(name)
    types: list[tuple[int, int, str]] = []
    for match in _TYPE_DECL.finditer(clean):
        start = clean.count("\n", 0, match.start(1))
        end = block_end(lines, start)
        bases = []
        if match.group(3):
            raw = re.sub(r"<[^>]*>", "", match.group(3))
            raw = re.sub(r"\b(extends|implements|with)\b", ",", raw)
            bases = [part.strip().split("(")[0].split(".")[-1] for part in raw.split(",") if part.strip()]
            bases = [base for base in bases if re.match(r"^[A-Za-z_]\w*$", base)]
        parent = next((name for t_start, t_end, name in types if t_start < start <= t_end), "")
        kind = match.group(1)
        exported = "private" not in lines[start] and "internal" not in lines[start]
        result.symbols.append(Symbol(match.group(2), kind, start + 1, end + 1, parent, 1, lines[start].strip()[:200], exported, bases))
        types.append((start, end, match.group(2)))
    for match in _JAVA_METHOD.finditer(clean):
        name = match.group(1)
        if name in KEYWORDS or name in ("class", "interface", "enum", "record", "struct"):
            continue
        start = clean.count("\n", 0, match.start(1))
        # skip type declarations matched as methods and control statements
        stripped = lines[start].strip()
        if re.match(r"^(?:else\s+)?(if|for|while|switch|catch|using|lock|foreach|synchronized|return|new)\b", stripped):
            continue
        owner = ""
        for t_start, t_end, t_name in types:
            if t_start < start <= t_end:
                owner = t_name  # innermost wins because types are sorted by start
        if not owner and language in ("Java", "C#", "Scala"):
            # top-level code in these languages is only inside types; guard against lambdas
            pass
        if _inside_symbol_body(result.symbols, start + 1, owner):
            continue  # nested-scope guard: local functions/lambdas stay with their enclosing method
        end = block_end(lines, start)
        body = "\n".join(lines[start : end + 1])
        params = match.group(2) or ""
        is_constructor = owner and name == owner or name in ("constructor", "__construct", "init")
        injects = re.findall(r"\b([A-Z][\w]*)(?:<[^>]*>)?\s+[a-z_]\w*\s*(?:,|$)", params) if is_constructor else []
        if is_constructor and language in ("Kotlin", "Swift", "Dart", "TypeScript"):
            injects = re.findall(r":\s*([A-Z]\w*)", params)
        exported = not re.search(r"\bprivate\b", lines[start])
        result.symbols.append(
            Symbol(name, "constructor" if is_constructor else ("method" if owner else "function"), start + 1, end + 1, owner,
                   1 + branch_count(body), stripped[:200], exported, [], injects)
        )
    # Field injection: @Autowired / @Inject / @Resource fields
    for match in re.finditer(r"@(?:Autowired|Inject|Resource)\s+(?:private|protected|public)?\s*(?:final\s+)?([A-Z]\w*)", clean):
        owner = next((name for t_start, t_end, name in types if t_start <= clean.count("\n", 0, match.start()) <= t_end), "")
        for symbol in result.symbols:
            if symbol.name == owner and symbol.kind in ("class", "object"):
                symbol.injects.append(match.group(1))
    # Kotlin / Scala / TS-style primary constructors: class Foo(private val repo: Repo)
    for match in re.finditer(r"class\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)", clean):
        for symbol in result.symbols:
            if symbol.name == match.group(1) and symbol.kind == "class":
                symbol.injects += re.findall(r":\s*([A-Z]\w*)", match.group(2))
    if re.search(r"\bstatic\s+void\s+main\s*\(|\bfun\s+main\s*\(|static\s+(?:async\s+)?(?:Task\s+|void\s+|int\s+)main\s*\(|\bint\s+main\s*\(", clean, re.I):
        result.has_main = True
    _count_calls(clean, result.calls)
    result.member_refs.update(_TYPE_REF.findall(clean))
    result.complexity = 1 + branch_count(clean)
    result.routes += [m.group(1) for m in _ROUTE.finditer(text)][:50]
    return result


def _inside_symbol_body(symbols: list[Symbol], line: int, owner: str) -> bool:
    for symbol in symbols:
        if symbol.kind in ("method", "function", "constructor") and symbol.parent == owner and symbol.line_start < line <= symbol.line_end:
            return True
    return False


# Go ---------------------------------------------------------------------------------------------------------------

_GO_PACKAGE = re.compile(r"^[ \t]*package\s+(\w+)", re.M)
_GO_IMPORT_BLOCK = re.compile(r"import\s*\(([^)]*)\)", re.S)
_GO_IMPORT_ONE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"', re.M)
_GO_FUNC = re.compile(r"^func\s+(?:\(\s*\w*\s*\*?\s*([\w]+)(?:\[[^\]]*\])?\s*\)\s*)?([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(", re.M)
_GO_TYPE = re.compile(r"^(?:type\s+|\t)([A-Za-z_]\w*)\s+(struct|interface)\s*\{", re.M)


def parse_go(text: str) -> ParseResult:
    result = ParseResult()
    clean = strip_comments_and_strings(text, "Go")
    lines = clean.splitlines()
    package = _GO_PACKAGE.search(text)
    result.namespace = package.group(1) if package else ""
    for block in _GO_IMPORT_BLOCK.finditer(text):
        base = text.count("\n", 0, block.start()) + 1
        for offset, raw in enumerate(block.group(1).splitlines()):
            match = re.search(r'(?:([\w.]+)\s+)?"([^"]+)"', raw)
            if match:
                alias = [match.group(1)] if match.group(1) else []
                result.imports.append(ImportRef(match.group(2), alias, base + offset, 0, "import"))
    for match in _GO_IMPORT_ONE.finditer(text):
        result.imports.append(ImportRef(match.group(1), [], text.count("\n", 0, match.start()) + 1, 0, "import"))
    for match in _GO_TYPE.finditer(clean):
        start = clean.count("\n", 0, match.start())
        end = block_end(lines, start)
        body = "\n".join(lines[start + 1 : end])
        embedded = re.findall(r"^[ \t]*\*?([A-Z]\w*)\s*$", body, re.M)
        result.symbols.append(Symbol(match.group(1), match.group(2), start + 1, end + 1, "", 1, lines[start].strip()[:200], match.group(1)[:1].isupper(), embedded))
    for match in _GO_FUNC.finditer(clean):
        start = clean.count("\n", 0, match.start())
        end = block_end(lines, start)
        body = "\n".join(lines[start : end + 1])
        receiver, name = match.group(1) or "", match.group(2)
        if name == "main" and result.namespace == "main":
            result.has_main = True
        injects = re.findall(r"\*?([A-Z]\w*)\b", lines[start].split("(", 2)[-1]) if name.startswith("New") else []
        result.symbols.append(Symbol(name, "method" if receiver else "function", start + 1, end + 1, receiver, 1 + branch_count(body), lines[start].strip()[:200], name[:1].isupper(), [], injects[:6]))
    _count_calls(clean, result.calls)
    result.member_refs.update(re.findall(r"\b\w+\.([A-Z]\w*)", clean))
    result.member_refs.update(_TYPE_REF.findall(clean))
    result.complexity = 1 + branch_count(clean)
    result.routes += [m.group(1) for m in _ROUTE.finditer(text)][:50]
    return result


# Rust -------------------------------------------------------------------------------------------------------------

_RS_USE = re.compile(r"^[ \t]*(?:pub\s+)?use\s+([\w:]+)(?:::\{([^}]*)\})?", re.M)
_RS_MOD = re.compile(r"^[ \t]*(?:pub\s+)?mod\s+(\w+)\s*;", re.M)
_RS_FN = re.compile(r"^[ \t]*(pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:const\s+)?(?:unsafe\s+)?fn\s+(\w+)", re.M)
_RS_TYPE = re.compile(r"^[ \t]*(pub(?:\([^)]*\))?\s+)?(struct|enum|trait|union)\s+(\w+)", re.M)
_RS_IMPL = re.compile(r"^[ \t]*impl(?:<[^>]*>)?\s+(?:([\w:]+)(?:<[^>]*>)?\s+for\s+)?([\w:]+)", re.M)


def parse_rust(text: str) -> ParseResult:
    result = ParseResult()
    clean = strip_comments_and_strings(text, "Rust")
    lines = clean.splitlines()
    for match in _RS_USE.finditer(text):
        names = [part.strip() for part in (match.group(2) or "").split(",") if part.strip()]
        result.imports.append(ImportRef(match.group(1), names, text.count("\n", 0, match.start()) + 1, 0, "use"))
    for match in _RS_MOD.finditer(text):
        result.imports.append(ImportRef(match.group(1), [], text.count("\n", 0, match.start()) + 1, 0, "mod"))
    impls: list[tuple[int, int, str, str]] = []
    for match in _RS_IMPL.finditer(clean):
        start = clean.count("\n", 0, match.start())
        impls.append((start, block_end(lines, start), (match.group(2) or "").split("::")[-1], (match.group(1) or "").split("::")[-1]))
    for match in _RS_TYPE.finditer(clean):
        start = clean.count("\n", 0, match.start())
        end = block_end(lines, start) if "{" in lines[start] or (start + 1 < len(lines) and "{" in lines[start + 1]) else start
        name = match.group(3)
        bases = [trait for _, _, target, trait in impls if target == name and trait]
        result.symbols.append(Symbol(name, match.group(2), start + 1, end + 1, "", 1, lines[start].strip()[:200], bool(match.group(1)), bases))
    for match in _RS_FN.finditer(clean):
        start = clean.count("\n", 0, match.start())
        owner = next((target for i_start, i_end, target, _ in impls if i_start < start <= i_end), "")
        if not owner and _nested(lines, start):
            continue
        end = block_end(lines, start)
        body = "\n".join(lines[start : end + 1])
        if match.group(2) == "main":
            result.has_main = True
        result.symbols.append(Symbol(match.group(2), "method" if owner else "function", start + 1, end + 1, owner, 1 + branch_count(body), lines[start].strip()[:200], bool(match.group(1))))
    _count_calls(clean, result.calls)
    result.member_refs.update(_TYPE_REF.findall(clean))
    result.complexity = 1 + branch_count(clean)
    return result


# Ruby -------------------------------------------------------------------------------------------------------------


def parse_ruby(text: str) -> ParseResult:
    result = ParseResult()
    clean = strip_comments_and_strings(text, "Ruby")
    lines = clean.splitlines()
    for match in re.finditer(r"""^\s*(require|require_relative|load)\s*\(?\s*["']([^"']+)["']""", text, re.M):
        result.imports.append(ImportRef(match.group(2), [], text.count("\n", 0, match.start()) + 1, 0, match.group(1)))
    stack: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)(class|module)\s+([A-Z][\w:]*)(?:\s*<\s*([A-Z][\w:]*))?", line)
        if match:
            end = indent_end(lines, index, keyword_end=True)
            parent = stack[-1][1] if stack and stack[-1][0] >= index else ""
            bases = [match.group(4).split("::")[-1]] if match.group(4) else []
            result.symbols.append(Symbol(match.group(3).split("::")[-1], "class" if match.group(2) == "class" else "module", index + 1, end + 1, parent, 1, line.strip()[:200], True, bases))
            stack = [item for item in stack if item[0] >= index]
            stack.append((end, match.group(3).split("::")[-1]))
            continue
        match = re.match(r"^(\s*)def\s+(self\.)?([\w?!=]+)", line)
        if match:
            end = indent_end(lines, index, keyword_end=True)
            owner = next((name for stop, name in reversed(stack) if stop >= index), "")
            body = "\n".join(lines[index : end + 1])
            result.symbols.append(Symbol(match.group(3), "method" if owner else "function", index + 1, end + 1, owner, 1 + branch_count(body), line.strip()[:200], not match.group(3).startswith("_")))
    _count_calls(clean, result.calls)
    result.member_refs.update(_TYPE_REF.findall(clean))
    result.complexity = 1 + branch_count(clean)
    return result


# SQL --------------------------------------------------------------------------------------------------------------

_SQL_OBJECT = re.compile(r"\bcreate\s+(?:or\s+replace\s+)?(?:global\s+temporary\s+)?(table|view|procedure|function|package\s+body|package|trigger|index|sequence)\s+(?:if\s+not\s+exists\s+)?([\w.\"\[\]]+)", re.I)


def parse_sql(text: str) -> ParseResult:
    result = ParseResult()
    clean = strip_comments_and_strings(text, "SQL")
    lines = clean.splitlines()
    for match in _SQL_OBJECT.finditer(clean):
        start = clean.count("\n", 0, match.start())
        kind = match.group(1).lower().replace("package body", "package")
        name = match.group(2).strip('"[]').split(".")[-1]
        end = start
        for index in range(start, min(len(lines), start + 400)):
            if ";" in lines[index] or re.match(r"^[ \t]*(go|/)\s*$", lines[index], re.I):
                end = index
                if kind not in ("procedure", "function", "package", "trigger"):
                    break
                if re.search(r"\bend\b\s*\w*\s*;", lines[index], re.I):
                    break
        result.symbols.append(Symbol(name, kind, start + 1, end + 1, "", 1, lines[start].strip()[:200], True))
    tables = set(re.findall(r"\b(?:from|join|into|update)\s+([A-Za-z_][\w.]*)", clean, re.I))
    result.member_refs.update(table.split(".")[-1] for table in tables)
    for name in re.findall(r"\b(?:exec(?:ute)?|call)\s+([A-Za-z_][\w.]*)", clean, re.I):
        result.calls[name.split(".")[-1]] = result.calls.get(name.split(".")[-1], 0) + 1
    result.complexity = 1 + branch_count(clean)
    return result


# COBOL / RPG / CL (IBM i, mainframe) ---------------------------------------------------------------------------------


def parse_cobol(text: str) -> ParseResult:
    result = ParseResult()
    lines = text.splitlines()
    program = re.search(r"PROGRAM-ID\.\s*([\w-]+)", text, re.I)
    current_section = ""
    for index, raw in enumerate(lines):
        line = raw[6:72] if len(raw) > 6 and raw[:6].strip().isdigit() or (len(raw) > 6 and raw[:6] == " " * 6) else raw
        if len(raw) > 6 and raw[6:7] in ("*", "/"):
            continue
        match = re.match(r"^\s{0,4}([A-Z0-9][\w-]*)\s+SECTION\.", line, re.I)
        if match:
            current_section = match.group(1)
            result.symbols.append(Symbol(match.group(1), "section", index + 1, index + 1, program.group(1) if program else "", 1, line.strip()[:200], True))
            continue
        match = re.match(r"^\s{0,4}([A-Z0-9][\w-]*)\.\s*$", line, re.I)
        if match and match.group(1).upper() not in ("PROCEDURE", "DIVISION", "EXIT", "GOBACK"):
            result.symbols.append(Symbol(match.group(1), "paragraph", index + 1, index + 1, current_section or (program.group(1) if program else ""), 1, line.strip()[:200], True))
        for copy in re.findall(r"\bCOPY\s+([\w-]+)", line, re.I):
            result.imports.append(ImportRef(copy, [], index + 1, 0, "copy"))
        for call in re.findall(r"\bCALL\s+['\"]([\w-]+)['\"]", line, re.I):
            result.imports.append(ImportRef(call, [], index + 1, 0, "call"))
            result.calls[call] = result.calls.get(call, 0) + 1
        for perform in re.findall(r"\bPERFORM\s+([\w-]+)", line, re.I):
            result.calls[perform] = result.calls.get(perform, 0) + 1
    # close paragraph ranges
    ordered = sorted(result.symbols, key=lambda item: item.line_start)
    for current, following in zip(ordered, ordered[1:] + [None], strict=False):
        current.line_end = (following.line_start - 1) if following else len(lines)
        current.complexity = 1 + len(re.findall(r"\b(IF|EVALUATE|WHEN|UNTIL|VARYING)\b", "\n".join(lines[current.line_start - 1 : current.line_end]), re.I))
    if program:
        result.symbols.insert(0, Symbol(program.group(1), "program", 1, len(lines), "", 1, f"PROGRAM-ID. {program.group(1)}", True))
        result.namespace = program.group(1)
    result.member_refs.update(re.findall(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+([A-Z][\w]*)", text))
    result.complexity = 1 + len(re.findall(r"\b(IF|EVALUATE|WHEN|UNTIL|VARYING)\b", text, re.I))
    return result


def parse_rpg(text: str) -> ParseResult:
    result = ParseResult()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.search(r"\bdcl-proc\s+(\w+)", line, re.I)
        if match:
            end = next((i for i in range(index, len(lines)) if re.search(r"\bend-proc\b", lines[i], re.I)), index)
            body = "\n".join(lines[index : end + 1])
            result.symbols.append(Symbol(match.group(1), "procedure", index + 1, end + 1, "", 1 + len(re.findall(r"\b(if|elseif|when|dow|dou|for)\b", body, re.I)), line.strip()[:200], bool(re.search(r"\bexport\b", line, re.I))))
        match = re.search(r"\bbegsr\s+(\w+)", line, re.I)
        if match:
            end = next((i for i in range(index, len(lines)) if re.search(r"\bendsr\b", lines[i], re.I)), index)
            result.symbols.append(Symbol(match.group(1), "subroutine", index + 1, end + 1, "", 1, line.strip()[:200], False))
        for copy in re.findall(r"/(?:copy|include)\s+([\w/,.]+)", line, re.I):
            result.imports.append(ImportRef(copy.split(",")[-1].split("/")[-1], [], index + 1, 0, "copy"))
        for call in re.findall(r"\bCALLP?\s*\(?\s*['\"]?(\w+)", line, re.I):
            result.calls[call] = result.calls.get(call, 0) + 1
            result.imports.append(ImportRef(call, [], index + 1, 0, "call"))
    result.member_refs.update(re.findall(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+([A-Z][\w]*)", text, re.I))
    result.complexity = 1 + len(re.findall(r"\b(if|elseif|when|dow|dou|for)\b", text, re.I))
    return result


# Fallback ----------------------------------------------------------------------------------------------------------


def _parse_generic(text: str, language: str) -> ParseResult:
    result = ParseResult()
    clean = strip_comments_and_strings(text, language)
    lines = clean.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^[ \t]*(?:def|function|func|fn|sub|proc|procedure)\s+([A-Za-z_][\w.]*)", line, re.I)
        if match:
            end = indent_end(lines, index)
            result.symbols.append(Symbol(match.group(1), "function", index + 1, end + 1, "", 1 + branch_count("\n".join(lines[index : end + 1])), line.strip()[:200], True))
        match = re.match(r"^[ \t]*class\s+([A-Za-z_]\w*)", line)
        if match:
            end = indent_end(lines, index)
            result.symbols.append(Symbol(match.group(1), "class", index + 1, end + 1, "", 1, line.strip()[:200], True))
    for match in re.finditer(r"^[ \t]*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", text, re.M):
        result.imports.append(ImportRef(match.group(1) or match.group(2), [], text.count("\n", 0, match.start()) + 1))
    _count_calls(clean, result.calls)
    result.complexity = 1 + branch_count(clean)
    return result


def _vue_script(text: str) -> tuple[str, str]:
    match = re.search(r"<script([^>]*)>([\s\S]*?)</script>", text)
    if not match:
        return "", "JavaScript"
    language = "TypeScript" if re.search(r"lang\s*=\s*[\"']ts", match.group(1)) else "JavaScript"
    prefix = "\n" * text.count("\n", 0, match.start(2))
    return prefix + match.group(2), language


BRACE_LANGUAGES = {"Java", "Kotlin", "C#", "Scala", "Groovy", "Swift", "Dart", "PHP", "C", "C++", "C/C++ Header", "Objective-C", "VB.NET", "F#"}


def parse(text: str, language: str) -> ParseResult:
    if language == "Python":
        return parse_python(text)
    if language in ("JavaScript", "TypeScript"):
        return parse_javascript(text, language)
    if language in ("Vue", "Svelte"):
        script, script_language = _vue_script(text)
        result = parse_javascript(script, script_language) if script else ParseResult()
        result.complexity = max(result.complexity, 1)
        return result
    if language in BRACE_LANGUAGES:
        return parse_brace_language(text, language)
    if language == "Go":
        return parse_go(text)
    if language == "Rust":
        return parse_rust(text)
    if language == "Ruby":
        return parse_ruby(text)
    if language in ("SQL", "PL/SQL"):
        return parse_sql(text)
    if language in ("COBOL", "COBOL Copybook"):
        return parse_cobol(text)
    if language in ("RPG", "CL"):
        return parse_rpg(text)
    if language in ("Shell", "Perl", "Lua", "Elixir", "R", "PowerShell", "Groovy", "Julia"):
        return _parse_generic(text, language)
    return ParseResult(complexity=1)
