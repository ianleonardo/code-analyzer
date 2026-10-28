"""URI / string expression resolver shared by the mobile, Camel and server-side client extractors.

Real code rarely writes a URL as one literal.  This module evaluates the expression the way a reader would:

* literals, ``+`` concatenation, Kotlin ``$x`` / ``${x}`` templates and Swift ``\\(x)`` interpolation;
* constants and ``static``/``object``/``companion`` members, also when they are built from other constants
  (``const val USERS = "$V2/users"``, ``ACCOUNTS = BASE + V2 + "/accounts"``);
* enum constants with arguments (``Ep.LOGIN.path``) and Swift raw values (``Endpoint.users.rawValue``);
* small helper functions (``fun user(id) = "$USERS/$id"``) with the call-site arguments substituted;
* ``String.format`` / ``"%s".format``, ``Uri.parse(x)``, ``URL(string:)``, ``Uri.Builder`` / ``HttpUrl.Builder`` /
  ``appendingPathComponent`` chains;
* ``BuildConfig.X`` (Gradle ``buildConfigField``), xcconfig / Info.plist keys and Spring ``${placeholders}``.

Whatever cannot be known statically stays visible as ``{name}`` and is reported in ``Resolved.unresolved`` so the
linker can lower its confidence and the UI can say why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.analyzer.catalog.model import Context, SourceFile
from app.services.analyzer.catalog.structure import TypeDecl, match_close, split_top, strip_comments

MAX_DEPTH = 8
NOOP_SUFFIX = re.compile(
    r"(?:\.(?:toString|trim|lowercase|uppercase|lowercased|absoluteString|toExternalForm|asString|orEmpty|toUri|toHttpUrl|toHttpUrlOrNull|toURL|encoded|path)\(\)"
    r"|\.(?:trimEnd|trimStart|trim|removeSuffix|removePrefix|dropLast|dropWhile|replace|replaceFirst|substring|substringBefore|substringAfter|trimmingCharacters|stripSuffix)\([^()]*(?:\([^()]*\)[^()]*)*\)"
    r"|!!|\?\?\s*\"\"|(?<=[\w)\]])[!?](?=$|\.))$")
BASE_NAMES = re.compile(r"(?i)(base|host|server|domain|origin|gateway|root|endpoint[s]?$|api_?url|api_?host|baseurl|url$|uri$)")
BASE_ENV = re.compile(r"(?i)(base|host|server|domain|origin|gateway|root|api_?url|api_?host)")
PLACEHOLDER = re.compile(r"^\{[^{}]*\}")


@dataclass
class Resolved:
    """A resolved expression.  ``text`` keeps ``{name}`` wildcards for what is only known at run time."""

    text: str = ""
    unresolved: list[str] = field(default_factory=list)
    resolved_parts: int = 0
    total_parts: int = 0
    variants: list[str] = field(default_factory=list)  # other values (flavours / profiles) when more than one exists

    @property
    def fraction(self) -> float:
        return 1.0 if not self.total_parts else round(self.resolved_parts / self.total_parts, 2)

    @property
    def complete(self) -> bool:
        return not self.unresolved

    def add(self, other: "Resolved") -> None:
        self.text += other.text
        self.unresolved += [u for u in other.unresolved if u not in self.unresolved]
        self.resolved_parts += other.resolved_parts
        self.total_parts += other.total_parts
        self.variants += [v for v in other.variants if v not in self.variants]


@dataclass
class Env:
    file: SourceFile | None = None
    owner: str = ""  # enclosing type name
    scope: dict[str, str] = field(default_factory=dict)  # parameters / locals already known
    depth: int = 0
    seen: tuple = ()

    def child(self, **kw) -> "Env":
        data = {"file": self.file, "owner": self.owner, "scope": dict(self.scope), "depth": self.depth + 1, "seen": self.seen}
        data.update(kw)
        return Env(**data)


# --- helpers --------------------------------------------------------------------------------------------------------


def _strip_parens(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")") and match_close(text, 0) == len(text) - 1:
        text = text[1:-1].strip()
    return text


def _is_string(term: str) -> bool:
    return bool(re.match(r'^(?:"""[\s\S]*"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')$', term.strip()))


def _unquote(term: str) -> str:
    term = term.strip()
    if term.startswith('"""'):
        return term[3:-3]
    return term[1:-1]


def _last_name(text: str) -> str:
    names = re.findall(r"[A-Za-z_]\w*", re.sub(r"\([^()]*\)", "", text))
    return names[-1] if names else "value"


def _placeholder(name: str) -> str:
    return "{" + name + "}"


def _balanced_call(term: str) -> tuple[str, str] | None:
    """``name(args)`` -> (name, args) when the closing bracket is the last character."""
    m = re.match(r"^([A-Za-z_][\w.?!]*)\s*\(", term)
    if not m:
        return None
    open_at = m.end() - 1
    if match_close(term, open_at) != len(term) - 1:
        return None
    return m.group(1), term[open_at + 1 : -1]


def join_url(*parts: str) -> str:
    out = ""
    for part in parts:
        if not part:
            continue
        if not out:
            out = part
        elif re.match(r"^https?://", part):
            out = part
        else:
            out = out.rstrip("/") + "/" + part.lstrip("/")
    return out


def split_url(text: str) -> tuple[str, str]:
    """``https://host/x`` -> (``https://host``, ``/x``); a leading ``{baseUrl}`` placeholder is the base, too."""
    text = (text or "").strip()
    m = re.match(r"^(https?://[^/?#]*)(.*)$", text)
    if m:
        return m.group(1), m.group(2) or "/"
    m = PLACEHOLDER.match(text)
    if m and BASE_ENV.search(m.group(0)):
        return "", text[m.end() :] or "/"
    return "", text


# --- the resolver ---------------------------------------------------------------------------------------------------


class UriResolver:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.members: dict[str, dict[str, tuple]] = {}  # type name -> member name -> (Member, file)
        self.funcs: dict[str, list[tuple]] = {}  # function name -> [(Member, file, owner)]
        self.enums: dict[str, dict[str, dict]] = {}  # enum -> constant -> {"args": [...], "raw": str, "params": [...]}
        self.build_fields: dict[str, list[str]] = {}
        self._index()
        self._scan_build_fields()

    # -- indexes ---------------------------------------------------------------

    def _index(self) -> None:
        for file in self.ctx.files:
            fs = self.ctx.structures.get(file.path)
            if fs is None:
                continue
            for member in fs.fields:
                self.members.setdefault("", {}).setdefault(member.name, (member, file))
            for member in fs.functions:
                self._add_func(member, file, "")
            for decl in fs.all_types():
                bucket = self.members.setdefault(decl.name, {})
                for member in decl.members:
                    if member.kind == "field" and member.name:
                        bucket.setdefault(member.name, (member, file))
                    elif member.kind == "method":
                        self._add_func(member, file, decl.name)
                if decl.kind == "enum":
                    self.enums.setdefault(decl.name, self._enum_values(decl, file))

    def _add_func(self, member, file: SourceFile, owner: str) -> None:
        expr = self._expression_of(member)
        if expr is not None and member.name:
            self.funcs.setdefault(member.name, []).append((member, file, owner, expr))

    @staticmethod
    def _expression_of(member) -> str | None:
        """The returned expression of a function / computed property: ``= expr``, ``{ expr }`` or the single ``return expr``."""
        text = strip_comments(member.body or member.initializer or "").strip().rstrip(";").strip()
        if not text:
            return None
        returns = list(re.finditer(r"(?:^|[\n;{}])\s*return\s+([^;\n]+(?:\n\s+[+.][^;\n]*)*)", text))
        if len(returns) == 1:
            return returns[0].group(1).strip()
        if returns or re.match(r"^(?:if|when|switch|guard|for|while|try)\b", text):
            return None
        if "\n" not in text:
            return text
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines and not re.match(r"^(?:val|var|let|final|String)\b", lines[-1]) and len(lines) <= 8 and any(re.match(r"^(?:val|var|let)\b", ln) for ln in lines[:-1]):
            return lines[-1]  # Kotlin / Swift: locals, then the value of the last expression
        if len(lines) <= 3 and all(ln[:1] in "+.\"" or i == 0 for i, ln in enumerate(lines)):
            return " ".join(lines)
        return None

    @staticmethod
    def _enum_values(decl: TypeDecl, file: SourceFile) -> dict[str, dict]:
        """constant -> {args, raw, params, props}; props are computed properties / ``when (this)`` / ``switch self`` tables."""
        out: dict[str, dict] = {}
        body = strip_comments(decl.body or "")
        params: list[str] = []
        if file.language == "Swift":
            for m in re.finditer(r"\bcase\s+(\w+)\s*=\s*(\"(?:\\.|[^\"\\])*\")", body):
                out[m.group(1)] = {"args": [m.group(2)], "raw": _unquote(m.group(2)), "params": ["rawValue"], "props": {}}
            for m in re.finditer(r"^\s*case\s+([\w\s,]+?)\s*$", body, re.M):
                for name in re.split(r"\s*,\s*", m.group(1).strip()):
                    if re.match(r"^\w+$", name) and name not in out:
                        out[name] = {"args": [], "raw": name, "params": [], "props": {}}
        else:
            hp = re.search(r"\((.*)\)\s*(?::|\{|$)", decl.header or "")
            if hp:
                params = [re.sub(r"^(?:private\s+|public\s+)?(?:val|var)\s+", "", p.split(":")[0].strip()).strip() for p in split_top(hp.group(1))]
            ctor = next((m for m in decl.members if m.kind == "constructor"), None)
            if ctor is not None and not params:
                params = [p.name for p in ctor.params]
            head = body.split(";", 1)[0] if ";" in body else body
            for part in split_top(head):
                m = re.match(r"^(?:@\w+(?:\([^)]*\))?\s*)*([A-Za-z_]\w*)\s*(?:\((.*)\))?\s*(?:\{[\s\S]*\})?$", part.strip(), re.S)
                if m and re.match(r"^[A-Z][A-Z0-9_]*$|^[a-z]\w*$", m.group(1)) and not re.match(r"^(?:val|var|fun|private|public|override|companion)$", m.group(1)):
                    out[m.group(1)] = {"args": split_top(m.group(2) or ""), "raw": m.group(1), "params": params, "props": {}}
        for member in decl.members:
            if member.kind not in ("field", "method") or not member.name or not member.body:
                continue
            text = strip_comments(member.body)
            for m in re.finditer(r"\bcase\s+((?:\.\w+\s*,?\s*)+):\s*(?:return\s+)?([^\n]+)", text):
                for const in re.findall(r"\.(\w+)", m.group(1)):
                    out.setdefault(const, {"args": [], "raw": const, "params": [], "props": {}})["props"][member.name] = m.group(2).strip()
            for m in re.finditer(r"^\s*(\w+(?:\s*,\s*\w+)*)\s*->\s*([^\n]+)", text, re.M):
                for const in re.split(r"\s*,\s*", m.group(1)):
                    if const in out:
                        out[const]["props"][member.name] = m.group(2).strip()
        return out

    def _scan_build_fields(self) -> None:
        def put(key: str, value: str) -> None:
            value = (value or "").strip().strip('"').strip("'")
            if key and value:
                bucket = self.build_fields.setdefault(key, [])
                if value not in bucket:
                    bucket.append(value)

        for file in self.ctx.files:
            name = file.name
            if not file.text:
                continue
            if name in ("build.gradle", "build.gradle.kts"):
                for m in re.finditer(r"buildConfigField\s*\(?\s*[\"']String[\"']\s*,\s*[\"'](\w+)[\"']\s*,\s*(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')", file.text):
                    value = m.group(2)[1:-1].replace('\\"', '"').strip('"')
                    put(m.group(1), value)
                for m in re.finditer(r"resValue\s*\(?\s*[\"']string[\"']\s*,\s*[\"'](\w+)[\"']\s*,\s*[\"']([^\"']*)[\"']", file.text):
                    put(m.group(1), m.group(2))
            elif name.endswith(".xcconfig"):
                for line in file.text.splitlines():
                    m = re.match(r"^\s*([A-Za-z_]\w*)(?:\[[^\]]*\])?\s*=\s*(.+?)\s*(?://.*)?$", line)
                    if m:
                        put(m.group(1), m.group(2).replace("/$()/", "//").replace("$()", ""))
            elif name == "Info.plist":
                for m in re.finditer(r"<key>(\w+)</key>\s*<string>([^<]*)</string>", file.text):
                    put(m.group(1), m.group(2))
            elif name in ("gradle.properties", "config.properties", ".env", "Config.plist"):
                for m in re.finditer(r"^\s*([A-Za-z_][\w.]*)\s*[=:]\s*(.+?)\s*$", file.text, re.M):
                    put(m.group(1), m.group(2))
        # `$(OTHER)` references inside xcconfig values
        for key, values in list(self.build_fields.items()):
            self.build_fields[key] = [re.sub(r"\$\((\w+)\)", lambda m: (self.build_fields.get(m.group(1)) or [m.group(0)])[0], v) for v in values]

    # -- evaluation ------------------------------------------------------------

    def resolve(self, expr: str, file: SourceFile | None = None, owner: str = "", scope: dict[str, str] | None = None) -> Resolved:
        env = Env(file=file, owner=owner, scope=dict(scope or {}))
        return self._expr(expr, env)

    def _expr(self, expr: str, env: Env) -> Resolved:
        out = Resolved()
        expr = (expr or "").strip().rstrip(";")
        if not expr:
            return out
        if env.depth > MAX_DEPTH:
            out.text = _placeholder(_last_name(expr))
            out.unresolved.append(_last_name(expr))
            out.total_parts = 1
            return out
        cond = re.match(r"^if\s*\((.*?)\)\s*(.+?)\s+else\s+(.+)$", expr, re.S)
        ternary = None if cond or _is_string(expr) else re.match(r"^([^?]+?)\s\?\s(.+?)\s:\s(.+)$", expr)
        pick = cond or ternary
        if pick:
            first, second = (pick.group(2), pick.group(3))
            a, b = self._expr(first, env), self._expr(second, env)
            a.variants = [v for v in [b.text] + a.variants if v and v != a.text]
            return a
        terms = split_top(_strip_parens(expr), "+") if "+" in expr else [expr]
        for term in terms:
            out.add(self._term(term, env))
        return out

    def _term(self, term: str, env: Env) -> Resolved:
        term = _strip_parens(term)
        out = Resolved()
        if not term:
            return out
        if _is_string(term):
            return self._template(_unquote(term), env, term.startswith('"""') or term.startswith('"'))
        if re.match(r"^-?\d+(?:\.\d+)?[LlFfDd]?$", term):
            out.text = term.rstrip("LlFfDd")
            return out
        stripped = term
        for _ in range(4):
            m = NOOP_SUFFIX.search(stripped)
            if not m or m.start() == 0:
                break
            stripped = stripped[: m.start()]
        if stripped != term and stripped:
            return self._term(stripped, env)
        term = term.rstrip("!?") if re.match(r"^[\w.]+[!?]+$", term) else term
        # Spring placeholder or Info.plist lookups
        m = re.match(r"^.*forInfoDictionaryKey\s*:\s*\"(\w+)\".*$", term) or re.match(r"^.*environment\[\s*\"(\w+)\"\s*\].*$", term) or re.match(r"^System\.getenv\(\s*\"(\w+)\"\s*\)$", term)
        if m and m.group(1) in self.build_fields:
            return self._found(self.build_fields[m.group(1)])
        call = _balanced_call(term)
        if call:
            return self._call(call[0], call[1], term, env)
        chain = self._chain_with_calls(term, env)
        if chain is not None:
            return chain
        if re.match(r"^[A-Za-z_][\w.]*$", term):
            return self._ident(term, env)
        if re.search(r"\.(?:format|plus)\(", term):
            m = re.match(r"^(.*?)\.(format|plus)\((.*)\)$", term, re.S)
            if m:
                return self._call(m.group(2), m.group(3), term, env, receiver=m.group(1))
        out.text = _placeholder(_last_name(term))
        out.unresolved.append(_last_name(term))
        out.total_parts = 1
        return out

    @staticmethod
    def _found(values: list[str]) -> Resolved:
        return Resolved(text=values[0], resolved_parts=1, total_parts=1, variants=list(values[1:]))

    def _template(self, text: str, env: Env, interpolating: bool) -> Resolved:
        """Kotlin / Groovy ``$x`` ``${x}`` and Swift ``\\(x)`` inside a string literal."""
        out = Resolved()
        lang = env.file.language if env.file else ""
        i, n, last = 0, len(text), 0
        buf = ""
        while i < n:
            ch = text[i]
            if lang == "Swift" and ch == "\\" and i + 1 < n and text[i + 1] == "(":
                end = match_close(text, i + 1)
                inner = text[i + 2 : end]
                buf += text[last:i]
                out.text += buf
                buf = ""
                out.add(self._interp(inner, env))
                i = end + 1
                last = i
                continue
            if lang in ("Kotlin", "Groovy", "Scala", "") and interpolating and ch == "$" and i + 1 < n and (text[i + 1] == "{" or re.match(r"[A-Za-z_]", text[i + 1])):
                buf += text[last:i]
                out.text += buf
                buf = ""
                if text[i + 1] == "{":
                    end = match_close(text, i + 1)
                    inner = text[i + 2 : end]
                    i = end + 1
                else:
                    m = re.match(r"[A-Za-z_]\w*", text[i + 1 :])
                    inner = m.group(0)
                    i += 1 + len(inner)
                last = i
                out.add(self._interp(inner, env))
                continue
            i += 1
        out.text += text[last:]
        out.text = re.sub(r"\\(.)", lambda m: m.group(1) if m.group(1) in "\"'\\/" else m.group(0), out.text)
        return out

    def _interp(self, inner: str, env: Env) -> Resolved:
        part = self._expr(inner, env.child())
        # a run-time value inside a path stays a {param}; do not count it as unresolved configuration
        return part

    def _call(self, name: str, args: str, term: str, env: Env, receiver: str = "") -> Resolved:
        arg_list = split_top(args)
        simple = name.split(".")[-1]
        if simple == "format" and (receiver or name in ("String.format", "String.Companion.format", "format", "MessageFormat.format")) or name in ("String", "NSString") and arg_list and arg_list[0].startswith("format"):
            parts = list(arg_list)
            if receiver:
                fmt_expr = receiver
            else:
                fmt_expr = re.sub(r"^format\s*:\s*", "", parts.pop(0)) if parts else ""
            fmt = self._expr(fmt_expr, env.child())
            values = [self._expr(re.sub(r"^\w+\s*:\s*", "", a) if not _is_string(a) else a, env.child()) for a in parts]
            res = Resolved(unresolved=list(fmt.unresolved), resolved_parts=fmt.resolved_parts, total_parts=fmt.total_parts)
            counter = {"i": 0}

            def take(k: int) -> str:
                if k < len(values):
                    for u in values[k].unresolved:
                        if u not in res.unresolved:
                            res.unresolved.append(u)
                    res.resolved_parts += values[k].resolved_parts
                    res.total_parts += values[k].total_parts
                    return values[k].text
                return "{arg}"

            def sub(m: re.Match) -> str:
                if m.group(1):
                    return take(int(m.group(1)) - 1)
                k = counter["i"]
                counter["i"] += 1
                return take(k)

            text = re.sub(r"%(?:(\d+)\$)?[-#+ 0,(]*\d*(?:\.\d+)?[sdfxXeEgGb@]", sub, fmt.text)
            res.text = re.sub(r"\{(\d+)\}", lambda m: take(int(m.group(1))), text) if name == "MessageFormat.format" else text
            return res
        if name in ("Uri.parse", "URI.create", "URL", "URI", "HttpUrl.parse", "HttpUrl.get", "URLComponents", "Uri.fromFile") and arg_list:
            return self._expr(re.sub(r"^(?:string|url)\s*:\s*", "", arg_list[0]), env.child())
        if simple == "plus" and receiver and arg_list:
            res = self._expr(receiver, env.child())
            res.add(self._expr(arg_list[0], env.child()))
            return res
        return self._user_call(name, arg_list, term, env)

    def scope_for(self, member, file: SourceFile, owner: str, scope: dict[str, str] | None = None) -> dict[str, str]:
        """Parameters (as ``{name}`` unless given) plus the local ``val x = expr`` / ``let x = expr`` / ``String x = expr`` of the body."""
        values = {p.name: _placeholder(p.name) for p in member.params}
        values.update(scope or {})
        env = Env(file=file, owner=owner, scope=values, depth=1)
        body = strip_comments(member.body or "")
        for m in re.finditer(r"(?:^|[\n;{])\s*(?:final\s+)?(?:val|var|let|String|var\s+String)\s+(\w+)\s*(?::\s*[\w<>?.]+)?\s*=\s*([^;\n]+(?:\n\s+[+.][^;\n]*)*)", body):
            name, expr = m.group(1), m.group(2).strip()
            if name in (scope or {}):
                continue
            res = self._expr(expr, env.child(scope=dict(values)))
            values[name] = res.text
        return values

    def resolve_in(self, expr: str, member, file: SourceFile, owner: str = "", scope: dict[str, str] | None = None) -> Resolved:
        """Resolve an expression found inside ``member`` (its parameters and locals are known)."""
        values = self.scope_for(member, file, owner, scope)
        return self._expr(expr, Env(file=file, owner=owner, scope=values, depth=0))

    def _user_call(self, name: str, arg_list: list[str], term: str, env: Env) -> Resolved:
        simple = name.split(".")[-1].rstrip("?!")
        out = Resolved()
        candidates = self.funcs.get(simple, [])
        if "." in name:
            qualifier = name.rsplit(".", 1)[0].split(".")[-1].rstrip("?!")
            narrowed = [c for c in candidates if c[2] == qualifier]
            candidates = narrowed if (narrowed or qualifier[:1].isupper()) else candidates
        elif env.owner:
            candidates = [c for c in candidates if c[2] == env.owner] or candidates
        candidates = [c for c in candidates if len([p for p in c[0].params if not p.default]) <= len(arg_list) <= len(c[0].params)]
        marker = (simple, tuple(arg_list))
        if candidates and env.depth < MAX_DEPTH and marker not in env.seen:
            member, file, owner, expr = candidates[0]
            named, positional = {}, []
            for a in arg_list:
                m = re.match(r"^(\w+)\s*:\s*(.+)$", a, re.S) if not _is_string(a) else None
                if m and any(p.name == m.group(1) or p.label == m.group(1) for p in member.params):
                    named[m.group(1)] = m.group(2)
                else:
                    positional.append(a)
            values: dict[str, Resolved] = {}
            free = [p for p in member.params if p.name not in named and p.label not in named]
            for p, a in zip(free, positional):
                values[p.name] = self._expr(a, env.child())
            for p in member.params:
                key = p.name if p.name in named else p.label if p.label in named else ""
                if key:
                    values[p.name] = self._expr(named[key], env.child())
            scope = {k: v.text for k, v in values.items()}
            inner_env = Env(file=file, owner=owner, scope=self.scope_for(member, file, owner, scope), depth=env.depth + 1, seen=env.seen + (marker,))
            inner = self._expr(expr, inner_env)
            for v in values.values():
                for u in v.unresolved:
                    if u not in inner.unresolved:
                        inner.unresolved.append(u)
            inner.resolved_parts += 1
            inner.total_parts += 1
            return inner
        out.text = _placeholder(simple)
        out.unresolved.append(simple + "()")
        out.total_parts = 1
        return out

    def _chain_with_calls(self, term: str, env: Env) -> Resolved | None:
        """``base.appendPath(a).appendPath(b)`` style builders and ``x.plus(y)``."""
        pieces = split_top(term, ".") if "." in term and "(" in term else []
        if len(pieces) < 2:
            return None
        appenders = {"appendPath", "appendEncodedPath", "addPathSegment", "addPathSegments", "addEncodedPathSegment", "appendingPathComponent", "appendPathComponent", "path", "appendingPathExtension"}
        if not any(re.match(rf"^(?:{'|'.join(appenders)})\s*\(", p) for p in pieces):
            builder = [p for p in pieces if re.match(r"^(scheme|authority|host|port|encodedPath)\s*\(", p)]
            if not builder:
                return None
        res = Resolved()
        scheme = host = port = ""
        path_parts: list[str] = []
        builder_names = appenders | {"scheme", "authority", "host", "port", "encodedPath", "appendQueryParameter", "addQueryParameter", "query", "build", "buildUpon", "toString", "toUrl", "url", "addQueryItem"}
        first = next((i for i, p in enumerate(pieces) if re.match(r"^\w+\s*\(", p)), 0)
        if re.match(r"^(\w+)", pieces[first]).group(1) in builder_names:
            head_pieces, rest = pieces[:first], pieces[first:]
        else:
            head_pieces, rest = pieces[: first + 1], pieces[first + 1 :]
        head = ".".join(head_pieces)
        if head and not re.match(r"^(Uri\.Builder|HttpUrl\.Builder|URLComponents|Builder)\s*\(", head):
            head_val = self._term(head, env.child())
            res.unresolved = head_val.unresolved
            res.resolved_parts, res.total_parts = head_val.resolved_parts, head_val.total_parts
            path_parts.append(head_val.text)
        pieces = [""] + rest
        for piece in pieces[1:]:
            m = re.match(r"^(\w+)\s*\((.*)\)$", piece, re.S)
            if not m:
                continue
            fn, arg = m.group(1), m.group(2)
            if fn in ("scheme", "authority", "host", "port"):
                v = self._expr(arg, env.child())
                res.add(Resolved(unresolved=v.unresolved, resolved_parts=v.resolved_parts, total_parts=v.total_parts))
                if fn == "scheme":
                    scheme = v.text
                elif fn in ("authority", "host"):
                    host = v.text
                else:
                    port = v.text
            elif fn in appenders:
                v = self._expr(arg, env.child())
                res.add(Resolved(unresolved=v.unresolved, resolved_parts=v.resolved_parts, total_parts=v.total_parts))
                path_parts.append(v.text)
            elif fn in ("appendQueryParameter", "addQueryParameter", "query", "build", "buildUpon", "toString", "toUrl", "url", "addQueryItem"):
                continue
            else:
                return None
        authority = f"{host}:{port}" if host and port else host
        prefix = f"{scheme or 'https'}://{authority}" if authority else ""
        res.text = join_url(prefix, *path_parts) if (prefix or path_parts) else ""
        return res

    def _ident(self, chain: str, env: Env) -> Resolved:
        out = Resolved(total_parts=1)
        chain = re.sub(r"^(?:this|self|Self|super)\.", "", chain)
        chain = re.sub(r"\.Companion\b", "", chain)
        parts = chain.split(".")
        # 1. parameters and locals
        if parts[0] in env.scope and len(parts) == 1:
            out.text, out.resolved_parts = env.scope[parts[0]], 1
            return out
        # 2. enum constants  Ep.LOGIN.path / Ep.LOGIN / Endpoint.users.rawValue
        for idx in range(len(parts) - 1, 0, -1):
            enum = self.enums.get(parts[idx - 1])
            if enum and parts[idx] in enum:
                value = self._enum_value(enum[parts[idx]], parts[idx + 1] if idx + 1 < len(parts) else "", env)
                if value is not None:
                    out.text, out.resolved_parts = value, 1
                    return out
        # 3. members of the enclosing type (or top level), then Type.member
        if len(parts) == 1:
            order = [(env.owner, parts[0]), ("", parts[0])]
        else:
            order = [(parts[-2], parts[-1])]
        for owner_name, member_name in order:
            found = self.members.get(owner_name, {}).get(member_name)
            if found is not None:
                value = self._member_value(found, owner_name, env)
                if value is not None:
                    value.total_parts = 1
                    value.resolved_parts = 1 if value.complete else 0
                    return value
        # 4. BuildConfig / Info.plist style fields
        if parts[-1] in self.build_fields and (len(parts) == 1 or parts[0] in ("BuildConfig", "R", "Secrets", "Keys", "Env", "Config", "Environment", "AppConfig", "Constants", "Configuration")):
            return self._found(self.build_fields[parts[-1]])
        # 5. constants indexed by the Context (also qualified)
        for key in (chain, ".".join(parts[-2:]), parts[-1]):
            if key in self.ctx.constants:
                out.text, out.resolved_parts = str(self.ctx.constants[key]), 1
                return out
        # 6. configuration values (Spring properties)
        if chain in self.ctx.config_values:
            out.text, out.resolved_parts = str(self.ctx.config_values[chain]), 1
            return out
        out.text = _placeholder(parts[-1])
        out.unresolved.append(parts[-1])
        return out

    def _enum_value(self, info: dict, prop: str, env: Env) -> str | None:
        params, args, props = info.get("params") or [], info.get("args") or [], info.get("props") or {}
        if prop in props:
            return self._expr(props[prop], env.child()).text
        if prop == "rawValue":
            return str(info.get("raw")) if info.get("raw") is not None else None
        if prop in ("name", "toString"):
            return str(info.get("raw") or "")
        if not prop:
            return self._expr(args[0], env.child()).text if args else str(info.get("raw") or "")
        if prop in params and params.index(prop) < len(args):
            return self._expr(args[params.index(prop)], env.child()).text
        if args and prop in ("path", "url", "uri", "value", "endpoint", "route", "pathString"):
            return self._expr(args[0], env.child()).text
        return None

    def _member_value(self, found: tuple, owner_name: str, env: Env) -> Resolved | None:
        member, file = found
        key = (owner_name, member.name)
        if key in env.seen or env.depth > MAX_DEPTH:
            return None
        expr = member.initializer or ""
        if not expr and member.body:
            expr = self._expression_of(member) or ""
        if not expr:
            return None
        return self._expr(expr, Env(file=file, owner=owner_name, scope={}, depth=env.depth + 1, seen=env.seen + (key,)))


# --- convenience ----------------------------------------------------------------------------------------------------


def resolver_for(ctx: Context) -> UriResolver:
    cached = getattr(ctx, "_uri_resolver", None)
    if cached is None:
        for file in ctx.files:  # make sure every structured file is parsed (the index uses the parsed structures)
            ctx.structure(file)
        cached = UriResolver(ctx)
        ctx._uri_resolver = cached  # type: ignore[attr-defined]
    return cached
