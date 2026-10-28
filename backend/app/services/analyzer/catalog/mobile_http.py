"""HTTP calls the URI resolver can see: OkHttp, Volley, Ktor, Fuel, ``URL``/``HttpURLConnection`` (Kotlin / Java) and
Alamofire, ``URLSession`` / ``URLRequest`` (Swift), including calls that go through small wrapper functions.

Real apps rarely write ``api.get("/v2/users/1")`` inline.  The URL is a constant in another file, built by a helper, passed
through a repository, a use case and a generic ``ApiClient.get(path)`` before it reaches ``Request.Builder().url(...)``.  So:

1. every HTTP sink is found and its URL expression evaluated with the shared resolver (constants, templates, enums, helper
   functions, BuildConfig / xcconfig base URLs);
2. a function whose URL is built from one of its own parameters is a *wrapper*: nothing is emitted for its body, instead each
   call site is evaluated with the actual arguments (wrapper of wrapper included, a few levels deep);
3. the result is an ``api_client`` item (``integration`` outside mobile apps) keyed ``client:METHOD path`` - the same shape
   Retrofit and Moya clients use - linked from the class that makes the call, so screen -> view model -> repository -> call
   traces keep working.

Whatever stays unknown is kept: ``{id}`` path parameters, an unresolved base URL, and a list of unresolved expressions
(``cat.extra["unresolved_calls"]``) so the UI can say exactly why a call could not be linked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.analyzer.catalog.camel_egress import mask_url, version_of
from app.services.analyzer.catalog.model import Catalog, Context, Item, SourceFile, snippet
from app.services.analyzer.catalog.structure import match_close, split_top, strip_comments
from app.services.analyzer.catalog.uris import Resolved, UriResolver, resolver_for, split_url

URLISH = re.compile(r"(?i)^(url|uri|path|endpoint|route|api|link|resource|href|address|target|apipath|apiurl|fullurl|requesturl|relativeurl|urlstring|pathstring)$|(url|uri|path|endpoint)$")
VERBS = ("get", "post", "put", "delete", "patch", "head", "options")
MAX_ROUNDS = 3
MAX_SITES = 400


@dataclass
class Sink:
    framework: str
    url_expr: str
    method_expr: str = ""
    offset: int = 0


@dataclass
class Wrapper:
    name: str
    owner: str
    file: SourceFile
    member: object
    template: Resolved
    method: str
    framework: str
    url_params: set = field(default_factory=set)
    line: int = 0


def _call_args(text: str, open_at: int) -> tuple[list[str], int]:
    close = match_close(text, open_at)
    return split_top(text[open_at + 1 : close]), close


def _verb(text: str) -> str:
    text = (text or "").strip().strip("\"'")
    text = re.sub(r"^.*[.]", "", text).upper()
    return text if text.lower() in VERBS else ""


# --- sinks ----------------------------------------------------------------------------------------------------------


def find_sinks(body: str, language: str) -> list[Sink]:
    body = strip_comments(body or "")
    out: list[Sink] = []
    if language == "Swift":
        for m in re.finditer(r"\b(?:AF|Alamofire|[sS]ession|[mM]anager|sessionManager|AF\.session)\s*\.\s*(request|upload|download|streamRequest)\s*\(", body):
            args, close = _call_args(body, m.end() - 1)
            if not args or re.match(r"^(?:request|urlRequest|req)\b", args[0]):
                continue
            first = re.sub(r"^(?:_|url|convertible|to)\s*:\s*", "", args[0]) if not args[0].startswith('"') else args[0]
            method = ""
            mm = re.search(r"method\s*:\s*([^,)]+)", ", ".join(args[1:]))
            method = '"GET"'
            if mm:
                method = mm.group(1).strip()
            elif m.group(1) == "upload":
                method = '"POST"'
            out.append(Sink("Alamofire", first, method, m.start()))
        for m in re.finditer(r"\bURLRequest\s*\(\s*url\s*:", body):
            args, close = _call_args(body, body.index("(", m.start()))
            if args:
                expr = re.sub(r"^url\s*:\s*", "", args[0])
                mm = re.search(r"httpMethod\s*=\s*([^\n;]+)", body)
                out.append(Sink("URLSession", expr, mm.group(1).strip() if mm else '"GET"', m.start()))
        for m in re.finditer(r"\.\s*(?:dataTask|data|downloadTask|uploadTask|dataTaskPublisher)\s*\(\s*(?:with|for|from)\s*:", body):
            args, _close = _call_args(body, body.index("(", m.start()))
            expr = re.sub(r"^(?:with|for|from)\s*:\s*", "", args[0]) if args else ""
            if expr and not re.match(r"^(?:request|req|urlRequest|r)\b", expr):
                out.append(Sink("URLSession", expr, '"GET"', m.start()))
        return out
    for m in re.finditer(r"Request\.Builder\s*\(\s*\)", body):
        build = re.search(r"\.\s*build\s*\(\s*\)", body[m.end() : m.end() + 2500])
        chain = body[m.end() : m.end() + build.start()] if build else body[m.end() : m.end() + 800]
        um = re.search(r"\.\s*url\s*\(", chain)
        if not um:
            continue
        args, _close = _call_args(chain, um.end() - 1)
        method = ""
        mm = re.search(r"\.\s*method\s*\(\s*([^,)]+)", chain)
        if mm:
            method = mm.group(1)
        else:
            vm = re.search(r"\.\s*(get|post|put|delete|patch|head)\s*\(", chain)
            method = '"%s"' % (vm.group(1).upper() if vm else "GET")
        if args:
            out.append(Sink("OkHttp", args[0], method, m.start()))
    for m in re.finditer(r"\b(?:StringRequest|JsonObjectRequest|JsonArrayRequest|JsonRequest|InputStreamRequest|ImageRequest)\s*(?:<[^>]*>)?\s*\(", body):
        args, _close = _call_args(body, m.end() - 1)
        if not args:
            continue
        if re.match(r"^(?:Request\.)?Method\.\w+$|^\d$", args[0]) and len(args) > 1:
            out.append(Sink("Volley", args[1], args[0], m.start()))
        else:
            out.append(Sink("Volley", args[0], '"GET"', m.start()))
    for m in re.finditer(r"(?<![\w.])(?:new\s+)?URL\s*\(", body):
        args, close = _call_args(body, m.end() - 1)
        if args and "string" not in args[0][:7]:
            rest = body[close : close + 600]
            mm = re.search(r"(?:requestMethod\s*=|setRequestMethod\s*\()\s*\"(\w+)\"", body)
            if re.search(r"openConnection|openStream", rest) or mm:
                out.append(Sink("HttpURLConnection", args[0], '"%s"' % (mm.group(1) if mm else "GET"), m.start()))
    for m in re.finditer(r"\b(?:\w*[cC]lient|http|ktor|apiClient|HttpClient)\s*\.\s*(get|post|put|delete|patch|head|options)\s*(?:<[^>]*>)?\s*\(", body):
        args, _close = _call_args(body, m.end() - 1)
        if args and not args[0].startswith("{") and "->" not in args[0] and not re.match(r"^\w+\s*=\s*\{", args[0]):
            first = re.sub(r"^(?:urlString|url)\s*=\s*", "", args[0])
            out.append(Sink("Ktor", first, '"%s"' % m.group(1).upper(), m.start()))
    for m in re.finditer(r"\b(?:\w*[cC]lient|http|ktor|apiClient|HttpClient)\s*\.\s*(get|post|put|delete|patch|head|options|request)\s*(?:<[^>]*>)?\s*\{", body):
        close = match_close(body, m.end() - 1)
        block = body[m.end() : close]
        um = re.search(r"\burl\s*\(", block)
        if um:
            args, _c = _call_args(block, um.end() - 1)
            mm = re.search(r"method\s*=\s*HttpMethod\.(\w+)", block)
            if args:
                out.append(Sink("Ktor", args[0], '"%s"' % (mm.group(1) if mm else m.group(1)).upper(), m.start()))
    for m in re.finditer(r"\b(?:Fuel|Rx2AndroidNetworking|AndroidNetworking|Rx3AndroidNetworking|khttp|Khttp)\s*\.\s*(get|post|put|delete|patch|head)\s*\(", body):
        args, _c = _call_args(body, m.end() - 1)
        if args:
            out.append(Sink("Fuel / Fast Android Networking", args[0], '"%s"' % m.group(1).upper(), m.start()))
    return out


# --- the extractor --------------------------------------------------------------------------------------------------


def _methods(ctx: Context, file: SourceFile):
    fs = ctx.structures.get(file.path)
    if fs is None:
        return
    for member in fs.functions:
        yield member, ""
    for decl in fs.all_types():
        for member in decl.members:
            if member.kind == "method" and (member.body or member.initializer):
                yield member, decl.name


def _normalise_method(text: str) -> str:
    text = (text or "").strip().strip("\"'")
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]  # {POST}: a verb constant the resolver could not evaluate; {method}: a parameter -> not a verb
    return _verb(text)


class Extractor:
    def __init__(self, ctx: Context, cat: Catalog):
        self.ctx, self.cat = ctx, cat
        self.resolver: UriResolver = resolver_for(ctx)
        self.mobile = bool(ctx.android) or any(f.language == "Swift" for f in ctx.files)
        self.files = [f for f in ctx.code_files("Kotlin", "Java", "Swift") if f.text and (f.language != "Java" or not f.path.endswith("package-info.java"))]
        self.wrappers: list[Wrapper] = []
        self.seen_wrappers: set[tuple] = set()
        self.members_by_file: dict[str, list] = {}
        self.unresolved: list[dict] = []
        self.made: set[str] = set()
        for f in self.files:
            ms = [(m, owner) for m, owner in _methods(ctx, f)]
            self.members_by_file[f.path] = sorted(ms, key=lambda t: (t[0].end_line or t[0].line) - t[0].line)

    def member_at(self, file: SourceFile, line: int):
        for member, owner in self.members_by_file.get(file.path, []):
            if member.line <= line <= max(member.end_line, member.line):
                return member, owner
        return None, ""

    def caller_key(self, file: SourceFile, line: int) -> str:
        best, span = "", 10**9
        for item in self.cat.items.values():
            if item.path == file.path and item.kind not in ("config", "manifest", "api_client", "integration", "constants", "topic", "table", "dto", "enum", "exception"):
                if item.line_start <= line <= max(item.line_end, item.line_start):
                    size = max(item.line_end, item.line_start) - item.line_start
                    if size < span:
                        best, span = item.key, size
        return best

    # 1. sinks and wrappers -----------------------------------------------------------------

    def run(self) -> None:
        for file in self.files:
            if not re.search(r"Request\.Builder|StringRequest|JsonObjectRequest|JsonArrayRequest|JsonRequest|\bURL\s*\(|Ktor|HttpClient|[cC]lient\s*\.|Fuel|AndroidNetworking|Alamofire|\bAF\b|URLSession|URLRequest|khttp|Session\.|[mM]anager\s*\.", file.text):
                continue
            for member, owner in self.members_by_file.get(file.path, []):
                body = member.body or member.initializer or ""
                if not body:
                    continue
                for sink in find_sinks(body, file.language):
                    self.handle_sink(file, member, owner, sink, body)
        for _ in range(MAX_ROUNDS):
            pending = [w for w in self.wrappers if (w.owner, w.name, w.template.text) not in self.seen_wrappers]
            if not pending:
                break
            for wrapper in pending:
                self.seen_wrappers.add((wrapper.owner, wrapper.name, wrapper.template.text))
                self.call_sites(wrapper)
        if self.unresolved:
            self.cat.extra["unresolved_calls"] = self.unresolved[:300]

    def handle_sink(self, file: SourceFile, member, owner: str, sink: Sink, body: str) -> None:
        line = (member.body_line or member.line) + body.count("\n", 0, sink.offset)
        resolved = self.resolver.resolve_in(sink.url_expr, member, file, owner)
        method = _normalise_method(self.resolver.resolve_in(sink.method_expr, member, file, owner).text) if sink.method_expr else ""
        text = resolved.text.strip()
        if not text:
            return
        param_names = {p.name for p in member.params}
        refs = {p for p in param_names if "{" + p + "}" in text}
        method_param = next((p for p in param_names if sink.method_expr and "{" + p + "}" in self.resolver.resolve_in(sink.method_expr, member, file, owner).text), "")
        wrap = {p for p in refs if URLISH.search(p) or re.match(r"^\{" + re.escape(p) + r"\}", split_url(text)[1] or text)}
        if wrap:
            self.wrappers.append(Wrapper(member.name, owner, file, member, resolved, method or ("{" + method_param + "}" if method_param else ""), sink.framework, wrap | ({method_param} if method_param else set()), line))
            return
        self.emit(file, line, owner, member.name, text, method, sink.framework, resolved, caller="", via="")

    # 2. wrapper call sites -----------------------------------------------------------------

    def call_sites(self, wrapper: Wrapper) -> None:
        name = wrapper.name
        pattern = re.compile(rf"(?<![\w])(?:[\w?!.]+\s*\.\s*)?{re.escape(name)}\s*(?:<[^>]*>)?\s*\(")
        count = 0
        for file in self.files:
            if name + "(" not in file.text and name + " (" not in file.text:
                continue
            if wrapper.owner and wrapper.owner not in file.text and file.path != wrapper.file.path:
                continue
            text = strip_comments(file.text)
            for m in pattern.finditer(text):
                if count > MAX_SITES:
                    return
                line = text.count("\n", 0, m.start()) + 1
                if file.path == wrapper.file.path and wrapper.member.line <= line <= max(wrapper.member.end_line, wrapper.member.line):
                    continue
                if re.search(r"\b(?:fun|func)\s+(?:<[^>]*>\s*)?$", text[max(0, m.start() - 40) : m.start()]):
                    continue  # the declaration itself
                open_at = m.end() - 1
                args, close = _call_args(text, open_at)
                after = text[close + 1 : close + 40]
                if file.language == "Java" and re.match(r"^\s*(?:throws\s+[\w., ]+)?\s*\{", after) and re.search(r"\w\s+$", text[max(0, m.start() - 40) : m.start()]):
                    continue  # a Java declaration, not a call
                caller_member, caller_owner = self.member_at(file, line)
                self.apply(wrapper, file, line, args, caller_member, caller_owner)
                count += 1

    def bind(self, member, args: list[str]) -> dict[str, str]:
        named: dict[str, str] = {}
        positional: list[str] = []
        for a in args:
            m = re.match(r"^(\w+)\s*(?::|=)\s*(.+)$", a, re.S) if not a.startswith(('"', "'")) else None
            if m and any(p.name == m.group(1) or p.label == m.group(1) for p in member.params):
                named[m.group(1)] = m.group(2)
            else:
                positional.append(a)
        out: dict[str, str] = {}
        free = [p for p in member.params if p.name not in named and p.label not in named]
        for p, a in zip(free, positional):
            out[p.name] = a
        for p in member.params:
            key = p.name if p.name in named else p.label if p.label in named else ""
            if key:
                out[p.name] = named[key]
        return out

    def apply(self, wrapper: Wrapper, file: SourceFile, line: int, args: list[str], caller_member, caller_owner: str) -> None:
        if len(args) > len(wrapper.member.params) or len([p for p in wrapper.member.params if not p.default]) > len(args):
            return
        bound = self.bind(wrapper.member, args)
        for p in wrapper.member.params:
            if p.name not in bound and p.default:
                bound[p.name] = p.default  # `method: HTTPMethod = .get`
        text = wrapper.template.text
        unresolved = list(wrapper.template.unresolved)
        resolved_parts, total_parts = wrapper.template.resolved_parts, wrapper.template.total_parts
        method = wrapper.method
        for pname, expr in bound.items():
            if pname not in wrapper.url_params and "{" + pname + "}" not in text and "{" + pname + "}" not in method:
                continue
            value = (self.resolver.resolve_in(expr, caller_member, file, caller_owner) if caller_member is not None else self.resolver.resolve(expr, file, caller_owner))
            is_method = "{" + pname + "}" in method and "{" + pname + "}" not in text
            text = text.replace("{" + pname + "}", value.text)
            method = method.replace("{" + pname + "}", value.text)
            if is_method:
                continue  # an HTTP verb constant (`.post`) is not part of the URL
            for u in value.unresolved:
                if u not in unresolved:
                    unresolved.append(u)
            resolved_parts += value.resolved_parts
            total_parts += value.total_parts
        method = _normalise_method(method)
        combined = Resolved(text=text, unresolved=unresolved, resolved_parts=resolved_parts, total_parts=total_parts)
        # the caller passes one of its own URL parameters on: it is a wrapper itself
        if caller_member is not None:
            own = {p.name for p in caller_member.params}
            passed = {p for p in own if "{" + p + "}" in text and URLISH.search(p)}
            if passed:
                self.wrappers.append(Wrapper(caller_member.name, caller_owner, file, caller_member, combined, method or wrapper.method, wrapper.framework, passed, line))
                return
        for pname in wrapper.url_params:
            if pname not in bound or not URLISH.search(pname):
                continue
            value = self.resolver.resolve_in(bound[pname], caller_member, file, caller_owner).text if caller_member is not None else self.resolver.resolve(bound[pname], file, caller_owner).text
            if not (value.startswith(("/", "http")) or "/" in value):
                return  # `cache.get(key)`: the argument is not a URL / path, so this is not an HTTP call
        path_part = split_url(text)[1]
        if not (path_part.startswith("/") or re.match(r"^https?://", text)):
            if text and not text.startswith("{"):
                return  # an ordinary call that happens to share the name (map.get(key))
            self.unresolved.append({"path": file.path, "line": line, "expr": ", ".join(args)[:200], "partial": text[:200], "unresolved": unresolved[:6], "via": f"{wrapper.owner}.{wrapper.name}".strip(".")})
            return
        caller = self.caller_key(file, line)
        positions = [wrapper.template.text.find("{" + p + "}") for p in wrapper.url_params if "{" + p + "}" in wrapper.template.text and URLISH.search(p)]
        prefix = wrapper.template.text[: min(positions)] if positions else ""
        self.emit(file, line, caller_owner or wrapper.owner, caller_member.name if caller_member is not None else wrapper.name, text, method, wrapper.framework, combined, caller=caller,
                  via=f"{wrapper.owner}.{wrapper.name}".strip("."), base_prefix=prefix)

    # 3. items ------------------------------------------------------------------------------

    def emit(self, file: SourceFile, line: int, owner: str, operation: str, text: str, method: str, framework: str, resolved: Resolved, caller: str, via: str,
             base_prefix: str = "") -> None:
        if base_prefix and text.startswith(base_prefix) and re.match(r"^https?://", base_prefix):
            base = base_prefix.rstrip("/")
            path = text[len(base_prefix):]
            path = path if path.startswith("/") else "/" + path
        else:
            base, path = split_url(text)
        if not path or (not path.startswith("/") and not base):
            if not text.startswith("{"):
                return
            self.unresolved.append({"path": file.path, "line": line, "expr": text[:200], "partial": text[:200], "unresolved": resolved.unresolved[:6], "via": via})
            return
        path = re.sub(r"\?.*$", "", path)
        path = re.sub(r"/{2,}", "/", path) or "/"
        verb = (method or "ANY").upper()
        key = f"client:{verb} {path}"
        item = self.cat.items.get(key)
        mobile = self.mobile and (file.language == "Swift" or self.ctx.android)
        if item is None:
            variants = [v for v in resolved.variants if v]
            details = {
                "client": owner or file.stem, "method": verb if method else "", "path": path, "base_url": mask_url(base), "operation": operation, "params": [],
                "framework": framework, "via": via or framework, "resolved_url": mask_url(text), "unresolved": resolved.unresolved, "confidence": resolved.fraction,
                "base_variants": [mask_url(split_url(v)[0]) for v in variants if split_url(v)[0]][:4], "version": version_of(path),
            }
            item = self.cat.add(Item("ui" if mobile else "service", "api_client" if mobile else "integration", key, f"{verb} {path}", file.path, line, line, file.language, framework,
                                     f"{owner}.{operation}", details, code=snippet(file.text, max(1, line - 3), line + 8, 14)))
        else:
            if base and not item.details.get("base_url"):
                item.details["base_url"] = mask_url(base)
            item.details["confidence"] = max(item.details.get("confidence", 0), resolved.fraction)
        if item.key not in self.made:
            self.made.add(item.key)
            if owner and operation:
                self.cat.type_members.setdefault(owner, {}).setdefault(operation, item.key)
        caller = caller or self.caller_key(file, line)
        if caller and caller != item.key:
            self.cat.link(caller, item.key, "calls", f"{verb} {path}")


def extract(ctx: Context, cat: Catalog) -> None:
    if not any(f.language in ("Kotlin", "Java", "Swift") for f in ctx.files):
        return
    for file in ctx.files:
        ctx.structure(file)
    Extractor(ctx, cat).run()
