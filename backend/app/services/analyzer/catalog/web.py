"""Web (TypeScript / JavaScript / templates): React screens (React Router JSX + object routes, Next.js pages / app
router), their fields, validation (attributes, react-hook-form, zod, yup, Ant Design rules), buttons, navigation and
backend calls (fetch, axios, API clients and generated SDKs, followed through imported modules / hooks); Next.js API
routes, Express and NestJS endpoints; OpenAPI / Swagger specs; Thymeleaf / JSP / FreeMarker templates; TypeScript DTO
interfaces and enums.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

import yaml

from app.services.analyzer import parsers
from app.services.analyzer.catalog.model import Catalog, Context, Item, join_paths, snippet
from app.services.analyzer.catalog.structure import (
    match_close,
    parse_structure,
    simple_type,
    split_top,
    string_values,
    strip_comments,
)

TS = ("TypeScript", "JavaScript")
EXTS = (".tsx", ".ts", ".jsx", ".js", ".mjs", ".vue", "/index.tsx", "/index.ts", "/index.jsx", "/index.js")
FIELD_TAGS = ("input", "textarea", "select", "TextField", "Input", "Select", "Checkbox", "Switch", "Radio", "RadioGroup", "DatePicker", "TimePicker", "Autocomplete",
              "NumberInput", "PasswordInput", "Textarea", "Form.Item", "FormField", "Field", "Controller", "InputNumber", "Slider", "Rating", "Combobox", "MultiSelect",
              "TextInput", "DateInput", "PinInput", "OutlinedInput", "FilledInput", "Upload", "Dropzone")
BUTTON_TAGS = ("button", "Button", "IconButton", "LoadingButton", "SubmitButton", "Fab")
HTTP_VERBS = ("get", "post", "put", "delete", "patch", "head")


def _resolve_import(ctx: Context, from_path: str, spec: str) -> str | None:
    if spec.startswith("."):
        base = str(PurePosixPath(from_path).parent / spec)
        parts = []
        for part in base.split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part not in (".", ""):
                parts.append(part)
        base = "/".join(parts)
        candidates = [base] + [base + ext for ext in EXTS]
    elif spec.startswith(("@/", "~/", "src/", "@app/")):
        rest = spec.split("/", 1)[1] if "/" in spec else ""
        file = ctx.by_path.get(from_path)
        roots = [p for p in {(file.container + "/src") if file and file.container else "src", "src", (file.container if file else "")} if p is not None]
        candidates = []
        for root in roots:
            base = f"{root}/{rest}".lstrip("/")
            candidates += [base] + [base + ext for ext in EXTS]
    else:
        return None
    for candidate in candidates:
        if candidate in ctx.by_path:
            return candidate
    return None


def _imports(text: str) -> list[tuple[list[str], str]]:
    out = []
    for m in re.finditer(r"import\s+(?:type\s+)?([\w$*{}\s,]+?)\s+from\s+['\"]([^'\"]+)['\"]", text):
        names = re.findall(r"[\w$]+", m.group(1).replace(" as ", " "))
        out.append(([n for n in names if n not in ("type",)], m.group(2)))
    for m in re.finditer(r"(?:const|let)\s+(\w+)\s*=\s*(?:React\.)?lazy\s*\(\s*\(\s*\)\s*=>\s*import\s*\(\s*['\"]([^'\"]+)['\"]", text):
        out.append(([m.group(1)], m.group(2)))
    return out


# --- HTTP calls ------------------------------------------------------------------------------------------------------

_CALL_PATTERNS = [
    re.compile(r"\bfetch\s*\(\s*(`[^`]*`|'[^']*'|\"[^\"]*\"|[\w.$]+(?:\s*\+\s*(?:`[^`]*`|'[^']*'|\"[^\"]*\"|[\w.$]+))*)\s*(?:,\s*\{([^}]{0,400}))?"),
    re.compile(r"\b([\w$]+)\s*\.\s*(get|post|put|delete|patch|head)\s*(?:<[^>()]*>)?\s*\(\s*(`[^`]*`|'[^']*'|\"[^\"]*\"|[\w.$]+\s*\+\s*(?:`[^`]*`|'[^']*'|\"[^\"]*\"))"),
    re.compile(r"\b(?:axios|request|http|api|client|\$http)\s*\(\s*\{([^}]{0,600})\}"),
    re.compile(r"\bmethod\s*:\s*['\"](\w+)['\"]\s*,\s*url\s*:\s*(`[^`]*`|'[^']*'|\"[^\"]*\")"),
    re.compile(r"\burl\s*:\s*(`[^`]*`|'[^']*'|\"[^\"]*\")\s*,\s*method\s*:\s*['\"](\w+)['\"]"),
    re.compile(r"\buse(?:SWR|SWRImmutable|SWRInfinite)\s*\(\s*(`[^`]*`|'[^']*'|\"[^\"]*\")"),
    re.compile(r"\.\s*(get|post|put|delete|patch)\s*(?:<[^()]*?>)?\s*\(\s*\{"),
    re.compile(r"\b(\w*fetch\w*|apiCall|callApi|apiRequest|sendRequest|httpFetch|apiClient)\s*(?:<[^>]*>)?\s*\(\s*([\w.$]+)\s*,\s*(`[^`]*`|'[^']*'|\"[^\"]*\")(?:\s*,\s*(\{[\s\S]*?\}))?\s*\)", re.I),
]
_RECEIVERS_SKIP = {"map", "set", "params", "searchParams", "headers", "formData", "cache", "localStorage", "sessionStorage", "router", "Object", "Reflect", "this", "props",
                   "state", "store", "cookies", "query", "form", "data", "result", "response", "res", "req", "app", "express", "Router", "weakMap", "url", "URLSearchParams"}


def _url_text(expr: str) -> str:
    expr = expr.strip()
    parts = re.split(r"\s*\+\s*", expr)
    out = ""
    for part in parts:
        values = string_values(part)
        if values and part[:1] in "`'\"":
            out += values[0]
        else:
            out += "${" + part + "}"
    return out


def http_calls_in(text: str, base_line: int = 1) -> list[dict]:
    out = []
    clean = strip_comments(text)
    for index, pattern in enumerate(_CALL_PATTERNS):
        for m in pattern.finditer(clean):
            line = base_line + clean.count("\n", 0, m.start())
            if index == 0:
                url = _url_text(m.group(1))
                options = m.group(2) or ""
                mm = re.search(r"method\s*:\s*['\"`](\w+)", options)
                method = mm.group(1).upper() if mm else "GET"
                via = "fetch"
            elif index == 1:
                if m.group(1) in _RECEIVERS_SKIP:
                    continue
                url = _url_text(m.group(3))
                method = m.group(2).upper()
                via = f"{m.group(1)}.{m.group(2)}"
            elif index == 2:
                body = m.group(1)
                um = re.search(r"url\s*:\s*(`[^`]*`|'[^']*'|\"[^\"]*\")", body)
                mm = re.search(r"method\s*:\s*['\"](\w+)", body)
                if not um:
                    continue
                url = _url_text(um.group(1))
                method = (mm.group(1) if mm else "GET").upper()
                via = "request config"
            elif index == 3:
                method, url, via = m.group(1).upper(), _url_text(m.group(2)), "request config"
            elif index == 4:
                url, method, via = _url_text(m.group(1)), m.group(2).upper(), "request config"
            elif index == 5:
                url, method, via = _url_text(m.group(1)), "GET", "useSWR"
            elif index == 6:
                obj_start = m.end() - 1
                obj = clean[obj_start : match_close(clean, obj_start) + 1]
                um = re.search(r"\burl\s*:\s*(`[^`]*`|'[^']*'|\"[^\"]*\")", obj)
                if not um:
                    continue
                url, method, via = _url_text(um.group(1)), m.group(1).upper(), "generated client"
            else:
                base_expr = m.group(2)
                path = _url_text(m.group(3))
                url = "${" + base_expr + "}" + (path if path.startswith("/") else "/" + path)
                options = m.group(4) or ""
                mm = re.search(r"method\s*:\s*['\"`](\w+)", options)
                method = mm.group(1).upper() if mm else "GET"
                via = m.group(1)
            if not re.search(r"^(?:\$\{[^}]*\})?/|^https?://|^\$\{[^}]*\}/|/api\b", url) or re.match(r"^\./|^\.\./", url) or len(url) > 300:
                continue
            if re.search(r"\.(png|jpe?g|svg|gif|css|ico|woff2?|json|html?)(\?|$)", url) and "/api" not in url:
                continue
            out.append({"method": method, "url": url, "via": via, "line": line})
    seen, unique = set(), []
    for call in out:
        key = (call["method"], call["url"], call["line"])
        if key not in seen:
            seen.add(key)
            unique.append(call)
    return unique


def _function_ranges(text: str, language: str) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    try:
        result = parsers.parse(text, language)
        out = [(s.name, s.line_start, s.line_end) for s in result.symbols if s.kind in ("function", "method")]
    except Exception:  # noqa: BLE001
        pass
    clean = strip_comments(text)
    for m in re.finditer(r"^\s+([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?\([\s\S]*?\)\s*(?::\s*[^=]+)?=>\s*\{", clean, re.M):
        brace_start = m.end() - 1
        brace_end = match_close(clean, brace_start)
        start_line = clean.count("\n", 0, m.start(1)) + 1
        end_line = clean.count("\n", 0, brace_end) + 1
        out.append((m.group(1), start_line, end_line))
    return out


class HttpIndex:
    """Per file: function name -> HTTP calls inside it (innermost function wins)."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.by_file: dict[str, dict[str, list[dict]]] = {}
        self.base_urls: dict[str, str] = {}
        for file in ctx.code_files(*TS, "Vue"):
            if len(file.text) > 400_000:
                continue
            calls = http_calls_in(file.text)
            bm = re.search(r"baseURL\s*[:=]\s*(`[^`]*`|'[^']*'|\"[^\"]*\")", file.text) or re.search(r"\bBASE\s*:\s*(`[^`]*`|'[^']*'|\"[^\"]*\")", file.text)
            if bm:
                self.base_urls[file.path] = _url_text(bm.group(1))
            if not calls:
                continue
            ranges = _function_ranges(file.text, "TypeScript" if file.language == "Vue" else file.language)
            grouped: dict[str, list[dict]] = {}
            for call in calls:
                owner = ""
                best = None
                for name, start, end in ranges:
                    if start <= call["line"] <= end and (best is None or end - start < best):
                        owner, best = name, end - start
                grouped.setdefault(owner, []).append(call)
            self.by_file[file.path] = grouped

    def reachable(self, path: str, depth: int = 4) -> list[dict]:
        """HTTP calls made by a file directly and through the functions it uses from imported modules / hooks.
        Barrel files (index.ts re-exports) pass the caller's text through, so usage is checked against real callers."""
        out = [dict(c, file=path) for calls in self.by_file.get(path, {}).values() for c in calls]
        seen = {path}
        frontier = [(path, self.ctx.by_path[path].text if path in self.ctx.by_path else "")]
        for _ in range(depth):
            nxt = []
            for current, ref_text in frontier:
                cur_file = self.ctx.by_path.get(current)
                resolved_specs = {
                    t for _, spec in _imports(cur_file.text if cur_file else "")
                    if (t := _resolve_import(self.ctx, current, spec))
                }
                for target in (self.ctx.imports_of.get(current, set()) | resolved_specs):
                    if target in seen or target not in self.ctx.by_path:
                        continue
                    seen.add(target)
                    grouped = self.by_file.get(target, {})
                    for name, calls in grouped.items():
                        if not name or re.search(rf"\b{re.escape(name)}\s*[(<]|\b{re.escape(name)}\b\s*[,)}}]", ref_text):
                            out += [dict(c, file=target, function=name) for c in calls]
                    target_text = self.ctx.by_path[target].text or ""
                    barrel = len(target_text) < 60000 and not re.sub(r"export\s+(?:type\s+)?(?:\*(?:\s+as\s+\w+)?|\{[^}]*\})\s+from\s+['\"][^'\"]+['\"];?|import\s[^;\n]*;?", "", strip_comments(target_text)).strip()
                    nxt.append((target, ref_text if barrel else (ref_text[-20000:] + "\n" + target_text[:40000])))
            frontier = nxt
        return out[:80]


# --- React -----------------------------------------------------------------------------------------------------------


def _tag_end(text: str, start: int) -> int:
    """Index of the '>' closing a JSX opening tag starting at ``start`` ('<'). Braces and strings are skipped."""
    i, n, depth = start + 1, len(text), 0
    while i < n:
        ch = text[i]
        if ch in "\"'`" and depth == 0 or ch in "\"'`":
            m = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`').match(text, i)
            if m:
                i = m.end()
                continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == ">" and depth == 0:
            return i
        i += 1
    return n - 1


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf"(?<![\w-]){re.escape(name)}\s*=\s*(\"[^\"]*\"|'[^']*'|\{{)", attrs)
    if not m:
        return "" if re.search(rf"(?<![\w-]){re.escape(name)}(?=[\s/>]|$)", attrs) else None
    if m.group(1) == "{":
        start = m.end() - 1
        end = match_close(attrs, start)
        inner = attrs[start + 1 : end].strip()
        values = string_values(inner)
        return values[0] if values and inner[:1] in "'\"`" else "{" + inner + "}"
    return m.group(1)[1:-1]


def _route_paths(ctx: Context, file) -> list[dict]:
    text = file.text
    clean = strip_comments(text)
    routes: list[dict] = []
    if "<Route" in clean:
        stack: list[str] = []
        for m in re.finditer(r"<Route\b|</Route\s*>", clean):
            if m.group(0).startswith("</"):
                if stack:
                    stack.pop()
                continue
            end = _tag_end(clean, m.start())
            attrs = clean[m.end() : end]
            self_closing = clean[end - 1] == "/"
            path = _attr(attrs, "path") or ""
            if _attr(attrs, "index") is not None and not path:
                path = ""
            full = path if path.startswith("/") else join_paths(*(stack + [path])) if (stack or path) else "/"
            element = _attr(attrs, "element") or _attr(attrs, "component") or _attr(attrs, "Component") or ""
            comp = re.search(r"<\s*([A-Z][\w.]*)", element or "") or re.match(r"\{?\s*([A-Z][\w.]*)", element or "")
            if comp and comp.group(1) not in ("Navigate", "Outlet", "Suspense", "Fragment", "React.Fragment"):
                routes.append({"path": full, "component": comp.group(1), "line": clean.count("\n", 0, m.start()) + 1, "file": file.path})
            if not self_closing:
                stack.append(path if not path.startswith("/") else path)
    if re.search(r"\bpath\s*:\s*['\"`]", clean) and re.search(r"(createBrowserRouter|createHashRouter|createMemoryRouter|useRoutes|RouteObject|routes\s*[:=]|children\s*:\s*\[)", clean):
        children_ranges = []
        for m in re.finditer(r"children\s*:\s*\[", clean):
            start = m.end() - 1
            children_ranges.append((start, match_close(clean, start)))
        depth_at = _depths(clean)
        path_hits = [(m.start(), m.group(1)) for m in re.finditer(r"\bpath\s*:\s*['\"`]([^'\"`]*)['\"`]", clean)]
        owners = {}
        for start, end in children_ranges:
            owner = None
            for pos, value in path_hits:
                if pos < start and depth_at[pos] == depth_at[start]:
                    owner = (pos, value)
            if owner:
                owners[(start, end)] = owner[1]
        for pos, value in path_hits:
            parents = [owners[r] for r in sorted(owners) if r[0] < pos < r[1]]
            full = value if value.startswith("/") else join_paths(*(parents + [value]))
            window = clean[pos : pos + 400]
            obj_end = window.find("}") if "}" in window else len(window)
            comp = re.search(r"(?:element|Component|component)\s*:\s*<?\s*([A-Z][\w.]*)", window[: max(obj_end, 200)])
            lazy = re.search(r"lazy\s*:\s*\(\s*\)\s*=>\s*import\s*\(\s*['\"]([^'\"]+)", window)
            routes.append({"path": full, "component": comp.group(1) if comp else "", "module": lazy.group(1) if lazy else "", "line": clean.count("\n", 0, pos) + 1, "file": file.path})
    for m in re.finditer(r"createFileRoute\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", clean):
        route = "/" + "/".join(seg for seg in m.group(1).split("/") if seg and not seg.startswith("_") and not (seg.startswith("(") and seg.endswith(")")))
        route = re.sub(r"\$(\w+)", r"{\1}", route)
        routes.append({"path": route, "component": "", "line": clean.count("\n", 0, m.start()) + 1, "file": file.path, "self": True})
    return routes


def _depths(text: str) -> list[int]:
    depths = [0] * (len(text) + 1)
    depth = 0
    for i, ch in enumerate(text):
        depths[i] = depth
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    depths[len(text)] = depth
    return depths


def _next_route(path: str) -> str | None:
    p = "/" + path
    m = re.search(r"/(?:src/)?pages/(.+)\.(?:tsx|jsx|ts|js)$", p)
    if m:
        route = m.group(1)
        if route.startswith(("_app", "_document", "_error", "api/")) or "/_" in route:
            return None
        route = re.sub(r"/?index$", "", route)
        return "/" + re.sub(r"\[\.\.\.(\w+)\]", r"{\1*}", re.sub(r"\[(\w+)\]", r"{\1}", route))
    m = re.search(r"/(?:src/)?app/(?:(.+)/)?page\.(?:tsx|jsx|ts|js)$", p)
    if m:
        route = m.group(1) or ""
        parts = [part for part in route.split("/") if part and not (part.startswith("(") and part.endswith(")")) and not part.startswith("@")]
        return "/" + "/".join(re.sub(r"\[\.\.\.(\w+)\]", r"{\1*}", re.sub(r"\[(\w+)\]", r"{\1}", part)) for part in parts)
    return None


def _component_name(text: str, path: str) -> str:
    m = re.search(r"export\s+default\s+(?:async\s+)?function\s+([A-Z]\w*)", text) or re.search(r"export\s+default\s+(?:memo\(|React\.memo\()?([A-Z]\w*)", text)
    if m:
        return m.group(1)
    m = re.search(r"export\s+(?:const|function)\s+([A-Z]\w*)", text)
    return m.group(1) if m else PurePosixPath(path).stem


def _jsx_text(fragment: str) -> str:
    fragment = re.sub(r"<[^>]*>", " ", fragment)
    texts = [t.strip() for t in re.split(r"\{[^}]*\}", fragment) if t.strip()]
    if texts:
        return re.sub(r"\s+", " ", " ".join(texts))[:80]
    expr = re.search(r"\{\s*t\(\s*['\"]([^'\"]+)['\"]", fragment)
    return expr.group(1) if expr else ""


def react_widgets(text: str) -> dict:
    clean = strip_comments(text)
    fields, actions, links, forms = [], [], [], []
    schema = _schema_rules(clean)
    for m in re.finditer(r"<(" + "|".join(re.escape(t) for t in FIELD_TAGS) + r")(?=[\s/>])", clean):
        end = _tag_end(clean, m.start())
        attrs = clean[m.end() : end]
        tag = m.group(1)
        line = clean.count("\n", 0, m.start()) + 1
        name = _attr(attrs, "name") or _attr(attrs, "id") or ""
        register = re.search(r"\{\s*\.\.\.\s*register\s*\(\s*['\"`]([\w.\[\]]+)['\"`]\s*(?:,\s*(\{[\s\S]*?\}))?\s*\)\s*\}", attrs)
        rules: list[str] = []
        if register:
            name = register.group(1)
            if register.group(2):
                rules += _rhf_rules(register.group(2))
        if not name:
            value = _attr(attrs, "value") or _attr(attrs, "checked") or ""
            vm = re.search(r"\{\s*(?:\w+\.)?(\w+)\s*\}", value or "")
            name = vm.group(1) if vm else ""
        label = _attr(attrs, "label") or _attr(attrs, "placeholder") or _attr(attrs, "aria-label") or ""
        if tag == "Form.Item":
            rule_attr = _attr(attrs, "rules") or ""
            if "required: true" in rule_attr or "required:true" in rule_attr:
                rules.append("required")
            for rm in re.finditer(r"(min|max|len|pattern|type)\s*:\s*([^,}]+)", rule_attr):
                rules.append(f"{rm.group(1)} {rm.group(2).strip()}")
            for msg in re.findall(r"message\s*:\s*['\"`]([^'\"`]+)", rule_attr):
                rules.append(f"\"{msg}\"")
        for attr_name in ("required", "isRequired"):
            if _attr(attrs, attr_name) is not None and _attr(attrs, attr_name) not in ("{false}",):
                rules.append("required")
        for attr_name in ("minLength", "maxLength", "min", "max", "pattern", "step", "accept"):
            value = _attr(attrs, attr_name)
            if value:
                rules.append(f"{attr_name} {value}")
        input_type = _attr(attrs, "type") or ("textarea" if tag.lower() == "textarea" else "select" if "select" in tag.lower() else "checkbox" if "check" in tag.lower() else
                                               "switch" if "switch" in tag.lower() else "date" if "date" in tag.lower() else "text")
        if input_type in ("hidden",):
            continue
        if input_type in ("submit", "button"):
            actions.append({"label": _attr(attrs, "value") or "Submit", "widget": tag, "handler": _attr(attrs, "onClick") or "", "kind": input_type, "line": line})
            continue
        key = name.split(".")[-1] if name else ""
        if key in schema:
            rules += schema[key]
        fields.append({"name": name or label or tag, "widget": tag, "type": input_type, "label": label, "required": "required" in rules or any(r.startswith("required") for r in rules),
                       "validations": sorted(set(rules)), "line": line})
    for m in re.finditer(r"<(" + "|".join(BUTTON_TAGS) + r")(?=[\s>])", clean):
        end = _tag_end(clean, m.start())
        attrs = clean[m.end() : end]
        if clean[end - 1] == "/":
            label = _attr(attrs, "aria-label") or _attr(attrs, "title") or m.group(1)
        else:
            close = clean.find(f"</{m.group(1)}>", end)
            label = _jsx_text(clean[end + 1 : close if close > 0 else end + 200]) or _attr(attrs, "aria-label") or ""
        handler = _attr(attrs, "onClick") or ""
        kind = _attr(attrs, "type") or ""
        actions.append({"label": label or m.group(1), "widget": m.group(1), "handler": handler[:160], "kind": kind, "disabled": (_attr(attrs, "disabled") or "")[:120],
                        "line": clean.count("\n", 0, m.start()) + 1})
    for m in re.finditer(r"<form\b|<Form\b", clean):
        end = _tag_end(clean, m.start())
        attrs = clean[m.end() : end]
        forms.append({"on_submit": (_attr(attrs, "onSubmit") or _attr(attrs, "onFinish") or "")[:160], "action": _attr(attrs, "action") or "", "method": (_attr(attrs, "method") or "").upper(),
                      "line": clean.count("\n", 0, m.start()) + 1})
    for m in re.finditer(r"<(?:Link|NavLink|RouterLink|a)\b[^>]*?\b(?:to|href)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|\{\s*[`'\"]([^`'\"]*)[`'\"])", clean):
        target = m.group(1) or m.group(2) or m.group(3) or ""
        if target.startswith(("/", "${")) and not target.startswith("//"):
            links.append({"target": target, "via": "link", "line": clean.count("\n", 0, m.start()) + 1})
    for m in re.finditer(r"\b(?:navigate|router\.(?:push|replace)|history\.push|redirect)\s*\(\s*(?:\{\s*to\s*:\s*)?[`'\"]([^`'\"]+)[`'\"]", clean):
        links.append({"target": m.group(1), "via": "navigate", "line": clean.count("\n", 0, m.start()) + 1})
    title = ""
    tm = re.search(r"<(?:h1|h2|Typography[^>]*variant=[\"']h[1-4][\"']|Heading|Title)[^>]*>([^<{]{2,80})<", clean) or re.search(r"(?:document\.title\s*=|<title>|title\s*:\s*)['\"`]?([^'\"`<]{2,80})", clean)
    if tm:
        title = tm.group(1).strip()
    state = re.findall(r"const\s+\[\s*(\w+)\s*,\s*set\w+\s*\]\s*=\s*(?:React\.)?useState", clean)
    queries = re.findall(r"use(?:Query|SuspenseQuery|Mutation|InfiniteQuery)\s*\(\s*\{?\s*(?:queryKey|mutationKey)?\s*:?\s*\[\s*['\"`]([^'\"`]+)", clean)
    return {"fields": fields, "actions": actions, "navigation": links, "forms": forms, "title": title, "state": state[:30], "queries": sorted(set(queries))[:20]}


def _rhf_rules(options: str) -> list[str]:
    rules = []
    if re.search(r"required\s*:\s*(true|['\"`])", options):
        message = re.search(r"required\s*:\s*['\"`]([^'\"`]+)", options)
        rules.append("required" + (f" — \"{message.group(1)}\"" if message else ""))
    for name in ("minLength", "maxLength", "min", "max", "pattern"):
        m = re.search(rf"{name}\s*:\s*(\{{[^}}]*\}}|/[^/]+/\w*|[\w.]+)", options)
        if m:
            value = re.search(r"value\s*:\s*([^,}]+)", m.group(1))
            rules.append(f"{name} {value.group(1).strip() if value else m.group(1)}")
    if "validate" in options:
        rules.append("custom validate()")
    return rules


def _schema_rules(text: str) -> dict[str, list[str]]:
    """zod / yup object schemas: field -> rules."""
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"^\s*['\"]?(\w+)['\"]?\s*:\s*(z|yup|Yup|Joi)\s*\.\s*([\s\S]*?)(?=,\s*$|,\s*\n\s*['\"]?\w+['\"]?\s*:|\n\s*\}\s*\))", text, re.M):
        chain = re.sub(r"(\"[^\"]*\"|'[^']*'|`[^`]*`)|\s+", lambda x: x.group(1) or "", m.group(3))
        rules = []
        base = re.match(r"(\w+)\(", chain)
        if base:
            rules.append(base.group(1))
        for rm in re.finditer(r"\.(\w+)\(([^()]*)\)", chain):
            name, arg = rm.group(1), rm.group(2)
            if name in ("optional", "nullable", "trim", "transform", "default"):
                if name == "optional":
                    rules.append("optional")
                continue
            msg = string_values(arg)
            plain = (" " + arg.split(",")[0]) if arg and arg[:1] not in "\"'`" else ""
            rules.append(f"{name}{plain}" + (f" — \"{msg[-1]}\"" if msg else ""))
        if m.group(2) in ("yup", "Yup") and "required" in chain:
            rules.append("required")
        if m.group(2) == "z" and "optional" not in chain and "nullable" not in chain:
            rules.append("required")
        out.setdefault(m.group(1), []).extend(rules)
    return out


def _screen_files(ctx: Context, routes: list[dict]) -> dict[str, dict]:
    screens: dict[str, dict] = {}
    nextjs = bool(re.search(r"(^|\s)next(\s|$)", ctx.dep_names)) or any(f.name.startswith("next.config.") for f in ctx.files)
    for route in routes:
        target = None
        if route.get("self"):
            target = route["file"]
        elif route.get("module"):
            target = _resolve_import(ctx, route["file"], route["module"])
        elif route.get("component"):
            file = ctx.by_path[route["file"]]
            for names, spec in _imports(file.text):
                if route["component"].split(".")[0] in names:
                    target = _resolve_import(ctx, route["file"], spec)
                    break
            if target is None and re.search(rf"(?:function|const)\s+{re.escape(route['component'])}\b", file.text):
                target = route["file"]
        if target:
            entry = screens.setdefault(target, {"routes": [], "route_lines": []})
            entry["routes"].append(re.sub(r":(\w+)", r"{\1}", route["path"]))
            entry["route_lines"].append({"path": route["path"], "file": route["file"], "line": route["line"]})
    for file in ctx.code_files(*TS):
        if not re.search(r"\.(tsx|jsx)$", file.path) or re.search(r"\.(test|spec|stories)\.(tsx|jsx)$", file.path):
            continue
        route = _next_route(file.path) if nextjs else None
        if route is not None:
            screens.setdefault(file.path, {"routes": [], "route_lines": []})["routes"].append(route)
            continue
        has_jsx = "return" in file.text and "<" in file.text
        if not has_jsx:
            continue
        if re.search(r"(Page|Screen|View|Tab|Dashboard|Portal|Workspace|Console)\.(tsx|jsx)$", file.name):
            if not re.search(r"(^|/)(ui|hooks|lib|utils|icons?)/", file.path):
                screens.setdefault(file.path, {"routes": [], "route_lines": []})
                continue
        if re.search(r"(^|/)(pages|screens|views|routes|containers)/", file.path):
            if not re.search(r"(^|/)(ui|hooks|lib|utils|icons?)/", file.path):
                screens.setdefault(file.path, {"routes": [], "route_lines": []})
                continue
        if re.search(r"(^|/)App\.(tsx|jsx)$", file.path):
            if "<Route" not in file.text:
                screens.setdefault(file.path, {"routes": ["/"], "route_lines": []})
            for names, spec in _imports(file.text):
                target = _resolve_import(ctx, file.path, spec)
                if target and target in ctx.by_path and re.search(r"\.(tsx|jsx)$", target):
                    if re.search(r"(^|/)(ui|common|Common|layout|Layout|icons?|hooks|lib|utils|context|providers?)/", target):
                        continue
                    if any(re.search(rf"<{re.escape(n)}[\s/>]", file.text) for n in names) and re.search(r"(Page|Screen|View|Tab|Dashboard|Panel|Modal)\.(tsx|jsx)$", target):
                        screens.setdefault(target, {"routes": [], "route_lines": []})
    return screens


def _react(ctx: Context, cat: Catalog, http: HttpIndex) -> None:
    routes: list[dict] = []
    for file in ctx.code_files(*TS):
        if file.text and ("<Route" in file.text or "path:" in file.text or "createFileRoute" in file.text) and len(file.text) < 300_000:
            try:
                routes += _route_paths(ctx, file)
            except (IndexError, ValueError):
                continue
    screens = _screen_files(ctx, routes)
    for path, info in screens.items():
        file = ctx.by_path.get(path)
        if file is None or not file.text:
            continue
        widgets = react_widgets(file.text)
        name = _component_name(file.text, path)
        calls = http.reachable(path)
        children = []
        for names, spec in _imports(file.text):
            target = _resolve_import(ctx, path, spec)
            if target and target in ctx.by_path and re.search(r"\.(tsx|jsx)$", target):
                used = [n for n in names if re.search(rf"<{re.escape(n)}[\s/>]", file.text)]
                if used:
                    children.append({"name": used[0], "path": target})
        for child in children[:12]:
            child_path = child["path"]
            if re.search(r"(^|/)(ui|common|Common|layout|Layout|icons?)/", child_path) or child_path in screens:
                continue
            child_file = ctx.by_path.get(child_path)
            if child_file is None:
                continue
            extra = react_widgets(child_file.text)
            for key in ("fields", "actions", "forms"):
                widgets[key] += [dict(w, section=child["name"]) for w in extra[key]]
            calls += [dict(c, component=child["name"]) for c in http.reachable(child_path)]
        merged: dict[str, dict] = {}
        for field in widgets["fields"]:
            key = re.sub(r"[^a-z0-9]", "", str(field.get("name", "")).lower())
            if key in merged:
                merged[key]["validations"] = sorted(set(merged[key].get("validations", [])) | set(field.get("validations", [])))
                merged[key]["required"] = merged[key].get("required") or field.get("required")
                merged[key]["label"] = merged[key].get("label") or field.get("label")
            else:
                merged[key] = dict(field)
        widgets["fields"] = list(merged.values())
        hooks = sorted(set(re.findall(r"\b(use[A-Z]\w+)\s*\(", file.text)) - {"useState", "useEffect", "useMemo", "useCallback", "useRef", "useContext", "useReducer", "useNavigate",
                                                                                "useParams", "useLocation", "useSearchParams", "useTranslation", "useForm", "useQueryClient", "useLayoutEffect", "useId", "useTransition"})
        details = {"platform": "Web", "type": "Next.js page" if (_next_route(path) is not None and not info["routes"]) else "React route" if info["routes"] else "React page",
                   "title": widgets["title"] or re.sub(r"(Page|Screen|View|Route)$", "", name), "routes": sorted(set(info["routes"])), "route_definitions": info["route_lines"][:10],
                   "fields": widgets["fields"], "actions": widgets["actions"], "forms": widgets["forms"], "navigation": widgets["navigation"], "state": widgets["state"],
                   "data_queries": widgets["queries"], "hooks": hooks[:30], "components": children[:40],
                   "backend_calls": [{k: c[k] for k in ("method", "url", "via", "line", "file") if k in c} | ({"function": c["function"]} if c.get("function") else {}) for c in calls]}
        item = cat.add(Item("ui", "screen", f"ui:{path}", name, path, 1, file.text.count("\n") + 1, file.language, "React" if "react" in ctx.dep_names or "React" in file.text[:3000] else file.language,
                            ", ".join(details["routes"]) or path, details, code=snippet(file.text, 1, 160, 160)))
        for call in calls:
            cat.pending_http.append((item.key, call["method"], call["url"], call["line"], call.get("file", path)))
        for target in widgets["navigation"]:
            cat.pending_screens.append((item.key, target["target"], "navigates to"))


# --- server-side TypeScript -----------------------------------------------------------------------------------------


def _express(ctx: Context, cat: Catalog) -> None:
    prefixes: dict[str, str] = {}
    for file in ctx.code_files(*TS):
        for m in re.finditer(r"\b(?:app|router|server|api|v\d)\s*\.\s*use\s*\(\s*['\"`]([^'\"`]+)['\"`]\s*,\s*(?:[\w.]+\s*,\s*)*(\w+)\s*\)", file.text):
            for names, spec in _imports(file.text):
                if m.group(2) in names:
                    target = _resolve_import(ctx, file.path, spec)
                    if target:
                        prefixes[target] = m.group(1)
            for rm in re.finditer(rf"(?:const|let|var)\s+{re.escape(m.group(2))}\s*=\s*require\s*\(\s*['\"]([^'\"]+)['\"]", file.text):
                target = _resolve_import(ctx, file.path, rm.group(1))
                if target:
                    prefixes[target] = m.group(1)
    for file in ctx.code_files(*TS):
        if file.layer == "ui" or not re.search(r"\b(express|Router\(\)|fastify|koa-router|hono|Hono)\b", file.text):
            continue
        clean = strip_comments(file.text)
        for m in re.finditer(r"\b(app|router|server|api|fastify|r|routes)\s*\.\s*(get|post|put|delete|patch|all)\s*\(\s*['\"`]([^'\"`]+)['\"`]\s*,", clean):
            line = clean.count("\n", 0, m.start()) + 1
            close = match_close(clean, clean.rfind("(", 0, m.end()))
            args = clean[m.end() : close]
            handlers = [a.strip() for a in split_top(args)]
            middlewares = [h for h in handlers[:-1] if re.match(r"^[\w.]+(\(.*\))?$", h)][:6]
            handler = handlers[-1] if handlers else ""
            path = join_paths(prefixes.get(file.path, ""), m.group(3))
            verb = m.group(2).upper() if m.group(2) != "all" else "ANY"
            rules = []
            for mw in middlewares:
                if re.search(r"auth|jwt|passport|protect|requireRole|isAdmin|guard", mw, re.I):
                    rules.append({"kind": "security", "condition": mw, "outcome": "requires authentication / authorisation", "line": line})
                if re.search(r"valid|celebrate|schema|zod|joi|check\(|body\(", mw, re.I):
                    rules.append({"kind": "validation", "condition": mw, "outcome": "validates the request", "line": line})
            named_handler = bool(re.match(r"^[\w.]+$", handler))
            handler_label = handler if named_handler else "(inline handler)"
            body_text = "" if named_handler else handler
            rules += _ts_rules(body_text, line)
            status = sorted({int(s) for s in re.findall(r"\.status\s*\(\s*(\d{3})", body_text)})
            name = f"{verb} {path}"
            item = cat.add(Item("api", "endpoint", f"api:{name}", name, file.path, line, line + body_text.count("\n"), file.language, "Express",
                                f"{m.group(1)}.{m.group(2)}('{m.group(3)}', {', '.join(middlewares + [handler_label])})",
                                {"method": verb, "path": path, "style": "REST", "handler": {"function": handler if re.match(r"^[\w.]+$", handler) else "(inline)", "path": file.path, "line": line},
                                 "middlewares": middlewares, "params": [{"in": "path", "name": p, "type": "string", "required": True} for p in re.findall(r":(\w+)", m.group(3))],
                                 "rules": rules, "status_codes": status or [200], "request": None, "response": {"kind": "body"},
                                 "security": {"expressions": [mw for mw in middlewares if re.search(r"auth|jwt|passport|protect|role|admin", mw, re.I)]}},
                                code=snippet(file.text, line, line + 40, 40)))
            for call in http_calls_in(body_text, line):
                cat.pending_http.append((item.key, call["method"], call["url"], call["line"], file.path))


def _ts_rules(body: str, base_line: int) -> list[dict]:
    out = []
    for m in re.finditer(r"\bif\s*\(", body):
        close = match_close(body, m.end() - 1)
        condition = re.sub(r"\s+", " ", body[m.end() : close]).strip()
        rest = body[close + 1 : close + 300]
        status = re.search(r"status\s*\(\s*(\d{3})\s*\)[\s\S]{0,80}?(?:json|send)\s*\(\s*(\{[^}]*\}|['\"`][^'\"`]*['\"`])", rest)
        thrown = re.search(r"throw\s+new\s+(\w+)\s*\(\s*(['\"`][^'\"`]*['\"`])?", rest)
        if status:
            out.append({"kind": "guard", "condition": condition[:200], "outcome": f"responds {status.group(1)} {(string_values(status.group(2)) or [''])[0]}".strip(), "line": base_line + body.count("\n", 0, m.start())})
        elif thrown:
            out.append({"kind": "guard", "condition": condition[:200], "outcome": f"throws {thrown.group(1)}" + (f": \"{string_values(thrown.group(2))[0]}\"" if thrown.group(2) else ""),
                        "line": base_line + body.count("\n", 0, m.start())})
    return out[:20]


def _nest(ctx: Context, cat: Catalog) -> None:
    for file in ctx.code_files("TypeScript"):
        if "@Controller(" not in file.text or len(file.text) > 300_000:
            continue
        try:
            fs = parse_structure(file.text, "TypeScript", file.path)
        except (IndexError, ValueError, RecursionError):
            continue
        for decl in fs.all_types():
            ctrl = decl.ann("Controller")
            if ctrl is None:
                continue
            base = ctrl.string("", "path")
            guards = [a.args for a in decl.annotations if a.name in ("UseGuards", "Roles", "Auth")]
            for method in decl.methods():
                mapping = method.ann("Get", "Post", "Put", "Delete", "Patch", "All", "Head", "Options")
                if mapping is None:
                    continue
                verb = mapping.name.upper() if mapping.name != "All" else "ANY"
                path = join_paths("/" + base if base else "", mapping.string("", "path"))
                params = []
                request = None
                for p in method.params:
                    pa = p.ann("Param", "Query", "Body", "Headers", "Req", "Res", "Session", "UploadedFile")
                    where = {"Param": "path", "Query": "query", "Body": "body", "Headers": "header", "UploadedFile": "part"}.get(pa.name if pa else "", "context")
                    params.append({"in": where, "name": (pa.string("") if pa else "") or p.name, "type": p.type, "required": where in ("path", "body")})
                    if where == "body":
                        request = _ts_dto(ctx, simple_type(p.type), file)
                status = method.ann("HttpCode")
                name = f"{verb} {path}"
                item = cat.add(Item("api", "endpoint", f"api:{name}", name, file.path, method.line, method.end_line, "TypeScript", "NestJS",
                                    f"{decl.name}.{method.name}(...): {method.type}",
                                    {"method": verb, "path": path, "style": "REST", "handler": {"class": decl.name, "method": method.name, "path": file.path, "line": method.line},
                                     "params": params, "request": request, "response": _ts_dto(ctx, simple_type(re.sub(r"^Promise<(.*)>$", r"\1", method.type or "")), file) | {"kind": "body"},
                                     "status_codes": [int(status.args)] if status and status.args.isdigit() else [201 if verb == "POST" else 200],
                                     "security": {"expressions": guards + [a.args for a in method.annotations if a.name in ("UseGuards", "Roles", "Auth", "Public")]},
                                     "rules": _ts_rules(method.body, method.body_line or method.line)},
                                    code=snippet(file.text, method.line - 2, method.end_line, 60)))
                types = {p.name: simple_type(p.type) for p in decl.params}
                for ctor in [m for m in decl.methods() if m.kind == "constructor"]:
                    types.update({p.name: simple_type(p.type) for p in ctor.params})
                for cm in re.finditer(r"this\.(\w+)\.(\w+)\s*\(", method.body):
                    if cm.group(1) in types:
                        cat.pending_calls.append((item.key, types[cm.group(1)], cm.group(2), method.line))


def _ts_dto(ctx: Context, name: str, near) -> dict:
    out = {"type": name, "name": name, "fields": []}
    if not name:
        return out
    info = ctx.extra_ts_types.get(name) if hasattr(ctx, "extra_ts_types") else None
    if info:
        out.update({"path": info["path"], "line": info["line"], "fields": info["fields"]})
    return out


def ts_types(ctx: Context) -> dict[str, dict]:
    """Exported TypeScript interfaces / type aliases / classes with their fields (class-validator decorators read)."""
    out: dict[str, dict] = {}
    for file in ctx.code_files("TypeScript"):
        if len(file.text) > 400_000 or not re.search(r"\b(interface|type|class)\s+[A-Z]", file.text):
            continue
        clean = strip_comments(file.text)
        for m in re.finditer(r"(?:export\s+)?(?:declare\s+)?(interface|type|class)\s+([A-Z]\w*)(?:\s*<[^>{=]*>)?\s*(?:extends\s+[^{=]+?|implements\s+[^{=]+?)?\s*(=\s*)?\{", clean):
            if m.group(1) == "type" and not m.group(3):
                continue
            start = clean.find("{", m.end() - 1)
            end = match_close(clean, start)
            body = clean[start + 1 : end]
            fields = []
            for fm in re.finditer(r"((?:@\w+\([^)]*\)\s*)*)(?:readonly\s+|public\s+|private\s+)?['\"]?([\w$]+)['\"]?\s*(\?)?\s*[!]?:\s*([^;\n]+)", body):
                decorators = re.findall(r"@(\w+)\(([^)]*)\)", fm.group(1))
                if body[: fm.start()].count("{") != body[: fm.start()].count("}"):
                    continue
                validations = [f"{d}({a})" if a else d for d, a in decorators if d.startswith(("Is", "Min", "Max", "Length", "Matches", "Array", "Validate"))]
                fields.append({"name": fm.group(2), "type": fm.group(4).strip().rstrip(",;")[:120], "required": not fm.group(3) and "IsOptional" not in fm.group(1) and "| null" not in fm.group(4) and "| undefined" not in fm.group(4),
                               "validations": validations})
            if fields:
                out.setdefault(m.group(2), {"path": file.path, "line": clean.count("\n", 0, m.start()) + 1, "fields": fields[:80], "kind": m.group(1)})
    return out


def _ts_shared(ctx: Context, cat: Catalog, types: dict[str, dict]) -> None:
    count = 0
    for name, info in types.items():
        file = ctx.by_path.get(info["path"])
        if file is None or file.is_test:
            continue
        if not (re.search(r"(Dto|DTO|Request|Response|Payload|Model|Entity|Input|Output|Params|Form|Schema|Public|Create|Update)$", name) or re.search(r"(^|/)(types|models|dto|dtos|api|schemas|interfaces|client)/|types?\.(gen\.)?ts$|models?\.ts$", info["path"])):
            continue
        if name.endswith("Props") or count >= 300:
            continue
        count += 1
        cat.add(Item("shared", "dto", f"dto:{name}", name, info["path"], info["line"], info["line"] + len(info["fields"]) + 1, "TypeScript", "TypeScript", f"{info['kind']} {name}",
                     {"fields": info["fields"], "declared_as": info["kind"]}, code=snippet(file.text, info["line"], info["line"] + len(info["fields"]) + 2, 60)))
        cat.type_keys.setdefault(name, f"dto:{name}")
    for file in ctx.code_files("TypeScript"):
        for m in re.finditer(r"(?:export\s+)?(?:const\s+)?enum\s+([A-Z]\w*)\s*\{([^}]*)\}", file.text):
            values = [v.split("=")[0].strip() for v in m.group(2).split(",") if v.strip()]
            line = file.text.count("\n", 0, m.start()) + 1
            cat.add(Item("shared", "enum", f"enum:{m.group(1)}", m.group(1), file.path, line, line + m.group(0).count("\n"), "TypeScript", "TypeScript", f"enum {m.group(1)}",
                         {"values": values[:200]}, code=m.group(0)[:2000]))


def _next_api(ctx: Context, cat: Catalog) -> None:
    for file in ctx.code_files(*TS):
        p = "/" + file.path
        m = re.search(r"/(?:src/)?pages/(api/.+)\.(?:ts|js)$", p)
        m2 = re.search(r"/(?:src/)?app/(?:(.+)/)?route\.(?:ts|js)$", p)
        if not (m or m2):
            continue
        if m:
            route = "/" + re.sub(r"\[(\w+)\]", r"{\1}", re.sub(r"/?index$", "", m.group(1)))
            verbs = sorted(set(re.findall(r"req\.method\s*===?\s*['\"](\w+)['\"]|case\s+['\"](GET|POST|PUT|DELETE|PATCH)['\"]", file.text)))
            verbs = sorted({a or b for a, b in verbs}) or ["ANY"]
        else:
            parts = [x for x in (m2.group(1) or "").split("/") if x and not x.startswith("(")]
            route = "/" + "/".join(re.sub(r"\[(\w+)\]", r"{\1}", x) for x in parts)
            verbs = sorted(set(re.findall(r"export\s+(?:async\s+)?(?:function|const)\s+(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b", file.text))) or ["ANY"]
        for verb in verbs:
            name = f"{verb} {route}"
            cat.add(Item("api", "endpoint", f"api:{name}", name, file.path, 1, file.text.count("\n") + 1, file.language, "Next.js", f"{file.path}",
                         {"method": verb, "path": route, "style": "REST", "handler": {"function": verb, "path": file.path, "line": 1},
                          "params": [{"in": "path", "name": x, "type": "string", "required": True} for x in re.findall(r"\{(\w+)\}", route)],
                          "rules": _ts_rules(file.text, 1), "status_codes": sorted({int(s) for s in re.findall(r"status\s*[:(]\s*(\d{3})", file.text)}) or [200]},
                         code=snippet(file.text, 1, 80, 80)))


# --- OpenAPI ---------------------------------------------------------------------------------------------------------


def openapi_ops(ctx: Context) -> dict[str, dict]:
    """operationId -> {method, path, details} from every OpenAPI / Swagger document (used by contract-first controllers)."""
    out: dict[str, dict] = {}
    for spec_item in _openapi_specs(ctx):
        if spec_item["details"].get("operation_id"):
            out.setdefault(spec_item["details"]["operation_id"], spec_item)
    return out


def _openapi(ctx: Context, cat: Catalog) -> None:
    implemented = {i.details.get("openapi", {}).get("operation_id") for i in cat.by_kind("endpoint") if i.details.get("openapi")}
    for spec_item in _openapi_specs(ctx):
        details = spec_item["details"]
        if details.get("operation_id") and details["operation_id"] in implemented:
            continue
        name = f"{spec_item['method']} {spec_item['path']}"
        existing = cat.items.get(f"api:{name}")
        if existing is not None:
            existing.details["openapi"] = details
            if not existing.summary and details.get("summary"):
                existing.summary, existing.summary_source = details["summary"], "openapi"
            continue
        cat.add(Item("api", "endpoint", f"api:{name}", name, details["spec"], spec_item["line"], spec_item["line"], "YAML", "OpenAPI", details.get("operation_id") or details.get("summary", "")[:120],
                     {"method": spec_item["method"], "path": spec_item["path"], "style": "REST (spec only)", "params": details["params"], "request": details["request"],
                      "response": details["responses"].get("200") or details["responses"].get("201") or {}, "status_codes": sorted(int(c) for c in details["responses"] if str(c).isdigit()),
                      "openapi": details, "from_spec": True}, summary=details.get("summary", ""), summary_source="openapi" if details.get("summary") else ""))


def _openapi_specs(ctx: Context) -> list[dict]:
    if hasattr(ctx, "_openapi_cache"):
        return ctx._openapi_cache
    found: list[dict] = []
    for file in ctx.files:
        if not file.text or file.language not in ("YAML", "JSON") or not re.search(r"^\s*[\"']?(openapi|swagger)[\"']?\s*:", file.text[:2000], re.M):
            continue
        try:
            spec = yaml.safe_load(file.text) if file.language == "YAML" else json.loads(file.text)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
            continue
        schemas = (spec.get("components") or {}).get("schemas") or spec.get("definitions") or {}
        base = ""
        if spec.get("basePath"):
            base = spec["basePath"]
        for path, operations in list(spec["paths"].items())[:600]:
            if not isinstance(operations, dict):
                continue
            for verb, op in operations.items():
                if verb.lower() not in HTTP_VERBS + ("options",) or not isinstance(op, dict):
                    continue
                full = join_paths(base, path)
                params = [{"in": p.get("in", ""), "name": p.get("name", ""), "type": (p.get("schema") or {}).get("type", p.get("type", "")), "required": bool(p.get("required")),
                           "description": (p.get("description") or "")[:200]} for p in op.get("parameters", []) or [] if isinstance(p, dict) and "name" in p]
                request = None
                body = op.get("requestBody") or {}
                content = (body.get("content") or {}) if isinstance(body, dict) else {}
                for ctype, media in content.items():
                    request = {"content_type": ctype, **_schema_fields((media or {}).get("schema") or {}, schemas)}
                    break
                responses = {}
                for code, resp in (op.get("responses") or {}).items():
                    if isinstance(resp, dict):
                        schema = next(iter(((resp.get("content") or {}).values())), {}).get("schema") if resp.get("content") else resp.get("schema")
                        responses[str(code)] = {"description": (resp.get("description") or "")[:200], **(_schema_fields(schema or {}, schemas) if schema else {})}
                line = file.text.count("\n", 0, max(0, file.text.find(path))) + 1
                spec_details = {"summary": op.get("summary", ""), "description": (op.get("description") or "")[:1000], "operation_id": op.get("operationId", ""), "tags": op.get("tags", []),
                                "params": params, "request": request, "responses": responses, "security": op.get("security", spec.get("security", [])), "spec": file.path}
                found.append({"method": verb.upper(), "path": full, "line": line, "details": spec_details})
    ctx._openapi_cache = found
    return found


def _schema_fields(schema: dict, schemas: dict, depth: int = 0) -> dict:
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref", "")
    if ref:
        name = ref.split("/")[-1]
        target = schemas.get(name, {})
        result = _schema_fields(target, schemas, depth + 1) if depth < 3 else {}
        return {"type": name, "name": name, **{k: v for k, v in result.items() if k not in ("type", "name")}}
    if schema.get("allOf") or schema.get("oneOf") or schema.get("anyOf"):
        merged: dict = {"type": "object", "fields": []}
        for part in schema.get("allOf") or schema.get("oneOf") or schema.get("anyOf"):
            sub = _schema_fields(part or {}, schemas, depth)
            names = {f["name"] for f in merged["fields"]}
            merged["fields"] += [f for f in sub.get("fields", []) if f["name"] not in names]
        return merged
    if schema.get("type") == "array":
        inner = _schema_fields(schema.get("items") or {}, schemas, depth + 1)
        return {**inner, "collection": True, "type": f"{inner.get('type', 'object')}[]"}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields = []
    for name, prop in list(props.items())[:80]:
        prop = prop or {}
        constraints = [f"{k} {prop[k]}" for k in ("minLength", "maxLength", "minimum", "maximum", "pattern", "format", "enum") if k in prop]
        field = {"name": name, "type": prop.get("type") or (prop.get("$ref", "").split("/")[-1]) or ("array" if "items" in prop else ""), "required": name in required, "validations": constraints}
        if depth < 2 and (prop.get("$ref") or prop.get("properties")):
            nested = _schema_fields(prop, schemas, depth + 1)
            if nested.get("fields"):
                field["fields"] = nested["fields"]
        fields.append(field)
    return {"type": schema.get("type", "object"), "fields": fields}


# --- server-rendered templates ---------------------------------------------------------------------------------------

TEMPLATE_DIRS = re.compile(r"(^|/)(templates|WEB-INF|views|jsp)/")
TEMPLATE_EXTS = re.compile(r"\.(html|htm|jsp|jspx|ftl|ftlh|mustache|hbs|vm|peb|jte|ejs|j2|jinja|jinja2|njk|vue|svelte)$", re.I)


def _messages(ctx: Context) -> dict[str, str]:
    out: dict[str, str] = {}
    for file in ctx.files:
        if file.text and re.match(r"^messages(_en)?\.properties$", file.name):
            for m in re.finditer(r"^\s*([\w.-]+)\s*[=:]\s*(.+)$", file.text, re.M):
                out.setdefault(m.group(1), m.group(2).strip())
    return out


def _resolve_relative_path(ctx: Context, base_file: str, rel_ref: str) -> str:
    rel_clean = rel_ref.split("?")[0].split("#")[0].strip()
    if not rel_clean or rel_clean.startswith(("http://", "https://", "mailto:", "tel:", "javascript:")):
        return ""
    parent = PurePosixPath(base_file).parent
    parts: list[str] = []
    for seg in (parent / rel_clean).parts:
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
        else:
            parts.append(seg)
    candidate = "/".join(parts)
    if candidate in ctx.by_path:
        return candidate
    for ext in ("", ".js", ".ts", ".mjs", ".html"):
        if candidate + ext in ctx.by_path:
            return candidate + ext
    return ""


def _is_spa_shell_only(text: str, has_spa_screens: bool) -> bool:
    if not has_spa_screens:
        return False
    if re.search(r"<(form|input|select|textarea|button|table|canvas|dialog|nav)\b|\b(th:|ng-|\*ngFor|v-model|v-for|hx-)", text, re.I):
        return False
    if re.search(r"<a\b[^>]*\bhref=", text, re.I):
        return False
    return bool(re.search(r'id=["\'](root|app|__next)["\']', text, re.I))


def _templates(ctx: Context, cat: Catalog, http: HttpIndex) -> None:
    messages = _messages(ctx)
    has_spa_screens = any(i.kind == "screen" for i in cat.items.values())
    html_links_pending: list[tuple[str, str]] = []
    for file in ctx.files:
        if not file.text or file.is_test or not TEMPLATE_EXTS.search(file.path):
            continue
        in_template_dir = bool(TEMPLATE_DIRS.search(file.path))
        if not in_template_dir:
            if re.search(r"(^|/)(docs?|documentation|javadoc|apidocs|site|reports?|coverage|fixtures)/|readme.*\.(html|htm)$", file.path, re.I):
                continue
            if file.path.endswith((".html", ".htm")) and _is_spa_shell_only(file.text, has_spa_screens):
                continue
        if in_template_dir:
            rel = re.split(r"(?:^|/)(?:templates|WEB-INF/views|WEB-INF/jsp|WEB-INF|views|jsp)/", file.path, maxsplit=1)[-1]
        else:
            rel = file.path
        view = re.sub(r"\.(html|htm|jsp|jspx|ftl|ftlh|mustache|hbs|vm|peb|jte|ejs|j2|jinja|jinja2|njk|vue|svelte)$", "", rel, flags=re.I)
        text = file.text
        fragment = bool(re.search(r"(^|/)(fragments?|layouts?|partials?|includes?|common)/", rel)) or ("th:fragment" in text and "<form" not in text and "<table" not in text)
        title_key = re.search(r"<h[12][^>]*th:text=\"#\{([\w.]+)\}\"", text)
        title = messages.get(title_key.group(1), "") if title_key else ""
        if not title:
            for candidate_re in (r"<h[12][^>]*>\s*([^<{$]{2,80}?)\s*</h[12]>", r"<title[^>]*>\s*([^<]{2,80})"):
                m_t = re.search(candidate_re, text, re.I)
                if m_t:
                    cleaned_t = re.sub(r"&nbsp;|&#160;", " ", m_t.group(1), flags=re.I).strip()
                    if cleaned_t:
                        title = cleaned_t
                        break
        title = title or (re.search(r"<h[12][^>]*th:text=\"[^\"]*'([^']{2,60})'", text) or [None, ""])[1]
        fields, actions, forms, links = [], [], [], []
        for m in re.finditer(r"<(input|select|textarea|form:input|form:select|form:textarea|form:password|form:checkbox|form:radiobutton|form:hidden)\b([^>]*)>", text):
            attrs = m.group(2)
            if "th:replace" in attrs or "th:insert" in attrs:
                continue
            name = (
                re.search(
                    r"th:field=\"\*\{(\w+(?:\.\w+)*)\}\"|\bformControlName=\"([\w.]+)\"|\[\(ngModel\)\]=\"([\w.]+)\"|\bv-model=\"([\w.]+)\"|\bpath=\"([\w.]+)\"|\bname=\"([\w.\[\]]+)\"|\bid=\"([\w-]+)\"",
                    attrs,
                )
                or [None]
            )
            name_value = next((g for g in name.groups() if g), "") if hasattr(name, "groups") else ""
            input_type = (re.search(r"\btype=\"(\w+)\"", attrs) or [None, m.group(1).split(":")[-1]])[1]
            if input_type in ("hidden",) or m.group(1) == "form:hidden":
                continue
            if input_type in ("submit", "button"):
                actions.append({"label": (re.search(r"value=\"([^\"]*)\"", attrs) or [None, "Submit"])[1], "widget": "input", "kind": input_type, "line": text.count("\n", 0, m.start()) + 1})
                continue
            rules = [r for r in ("required",) if re.search(r"\brequired\b", attrs)] + [f"{k} {v}" for k, v in re.findall(r"\b(maxlength|minlength|pattern|min|max)=\"([^\"]*)\"", attrs)]
            fields.append({"name": name_value or m.group(1), "widget": m.group(1), "type": input_type, "label": (re.search(r"placeholder=\"([^\"]*)\"", attrs) or [None, ""])[1],
                           "required": "required" in rules, "validations": rules, "line": text.count("\n", 0, m.start()) + 1})
        for m in re.finditer(r"::\s*(input|inputField|select|selectField|textarea)\s*\(\s*(?:'([^']*)'|#\{([\w.]+)\})\s*,\s*'([^']*)'(?:\s*,\s*(?:'([^']*)'|\$\{(\w+)\}))?", text):
            label = m.group(2) or messages.get(m.group(3) or "", m.group(3) or "")
            kind = m.group(5) or ("select" if "select" in m.group(1) else "text")
            fields.append({"name": m.group(4), "widget": f"fragment {m.group(1)}", "type": kind, "label": label, "required": False, "options": m.group(6) or "",
                           "validations": ["server-side bean validation (errors shown inline)"], "line": text.count("\n", 0, m.start()) + 1})
        # Canvas & interactive display elements in standalone HTML / games / dashboards
        for m in re.finditer(r"<canvas\b([^>]*)>", text, re.I):
            cid = (re.search(r'\bid=["\']([\w-]+)["\']', m.group(1)) or [None, "canvas"])[1]
            fields.append({"name": cid, "widget": "canvas", "type": "canvas", "label": cid, "required": False, "validations": [], "line": text.count("\n", 0, m.start()) + 1})
        for m in re.finditer(r'<(?:span|div)\b[^>]*\bid=["\'](\w*(?:Display|Score|Lives|Level|Status|Timer|Count|Value|Total)\w*)["\']', text):
            did = m.group(1)
            fields.append({"name": did, "widget": "display", "type": "readonly", "label": did, "required": False, "validations": [], "line": text.count("\n", 0, m.start()) + 1})
        for m in re.finditer(r"<(?:form|form:form)\b([^>]*)>", text):
            attrs = m.group(1)
            action = (re.search(r"th:action=\"@\{([^}\"]*)\}|\baction=\"([^\"]*)\"", attrs) or [None])
            action_value = next((g for g in action.groups() if g is not None), "") if hasattr(action, "groups") else ""
            method = ((re.search(r"\bmethod=\"(\w+)\"", attrs) or [None, "GET"])[1]).upper()
            obj = (re.search(r"th:object=\"\$\{(\w+)\}\"|modelAttribute=\"(\w+)\"", attrs) or [None])
            forms.append({"action": re.sub(r"\(.*\)$", "", action_value), "method": method, "object": next((g for g in obj.groups() if g), "") if hasattr(obj, "groups") else "",
                          "line": text.count("\n", 0, m.start()) + 1})
        for m in re.finditer(r"<button\b([^>]*)>([\s\S]{0,200}?)</button>", text):
            label = re.sub(r"<[^>]+>|\s+", " ", m.group(2)).strip() or (re.search(r"th:text=\"[^']*'([^']*)'|\bid=\"([\w-]+)\"", m.group(1)) or [None, "Button", ""])[1] or "Button"
            keys = re.findall(r"#\{(\w+)\}", m.group(1))
            if keys and messages:
                label = " / ".join(messages.get(k, k) for k in keys)
            actions.append({"label": label[:60], "widget": "button", "kind": (re.search(r"type=\"(\w+)\"", m.group(1)) or [None, "button" if not forms else "submit"])[1], "line": text.count("\n", 0, m.start()) + 1})
        for m in re.finditer(r"th:href=\"@\{([^}\"]+)\}|<a\b[^>]*\bhref=\"([^\"]+)\"|<c:url\s+value=\"([^\"]+)\"|spring:url\s+value=\"([^\"]+)\"|\brouterLink=\"([^\"]+)\"|<router-link[^>]*\bto=\"([^\"]+)\"", text):
            target = re.sub(r"\(.*\)$", "", next(g for g in m.groups() if g))
            if not target or target.startswith(("#", "javascript:", "mailto:", "tel:", "http://", "https://")):
                continue
            if not target.startswith(("/webjars", "/resources", "/css", "/js", "/images", "/static")) and not re.search(r"\.(css|js|png|jpg|svg|ico)$", target):
                links.append({"target": target, "via": "link", "line": text.count("\n", 0, m.start()) + 1})
        includes = sorted(set(re.findall(r"th:(?:replace|insert|include)=\"~?\{?\s*([\w/-]+)\s*::", text) + [a or b for a, b in re.findall(r"<jsp:include\s+page=\"([^\"]+)\"|<%@\s*include\s+file=\"([^\"]+)\"", text)]))
        script_refs = []
        backend_calls = list(http_calls_in(text, 1))
        for sm in re.finditer(r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']', text, re.I):
            resolved_script = _resolve_relative_path(ctx, file.path, sm.group(1))
            if resolved_script:
                script_refs.append(resolved_script)
                backend_calls.extend(http.reachable(resolved_script))
        if file.path.endswith(".component.html"):
            ts_companion = file.path[:-5] + ".ts"
            if ts_companion in ctx.by_path:
                script_refs.append(ts_companion)
                backend_calls.extend(http.reachable(ts_companion))
        if file.path.endswith(".vue"):
            t_type, fw = "Vue component", "Vue"
        elif file.path.endswith(".svelte"):
            t_type, fw = "Svelte component", "Svelte"
        elif file.path.endswith(".component.html") or re.search(r"\*ngFor|\[\(ngModel\)\]|formControlName", text):
            t_type, fw = "Angular template", "Angular"
        elif "th:" in text:
            t_type, fw = "Thymeleaf template", "Thymeleaf"
        elif file.path.endswith((".jsp", ".jspx")):
            t_type, fw = "JSP", "JSP"
        elif in_template_dir:
            t_type, fw = "Server template", "Server template"
        else:
            t_type, fw = "HTML screen", "HTML"
        display_name = (title.strip() if title and not in_template_dir else "") or view
        details = {"platform": "Web", "type": t_type,
                   "view": view, "title": title.strip() or PurePosixPath(file.path).stem, "fields": fields, "actions": actions, "forms": forms, "navigation": links,
                   "includes": includes[:20], "scripts": script_refs[:10], "fragment": fragment,
                   "backend_calls": backend_calls[:20],
                   "model_attributes": sorted(set(re.findall(r"\$\{(\w+)[.}]", text)))[:30]}
        kind = "component" if fragment else "screen"
        item = cat.add(Item("ui", kind, f"ui:{file.path}", display_name, file.path, 1, text.count("\n") + 1,
                            "Vue" if file.path.endswith(".vue") else "HTML" if file.path.endswith((".html", ".htm")) else "JSP",
                            fw, f"view \"{view}\"", details, code=snippet(text, 1, 140, 140)))
        cat.extra.setdefault("views", {})[view] = item.key
        cat.extra.setdefault("views", {})[PurePosixPath(file.path).stem] = item.key
        for form in forms:
            if form["action"]:
                cat.pending_http.append((item.key, form["method"] or "GET", form["action"], form["line"], file.path))
            else:
                cat.extra.setdefault("self_posting_views", []).append((item.key, view, form["method"]))
        for link in links:
            target_href = link["target"]
            if target_href.endswith((".html", ".htm")):
                resolved_html = _resolve_relative_path(ctx, file.path, target_href)
                if resolved_html:
                    html_links_pending.append((item.key, f"ui:{resolved_html}"))
                    continue
            if target_href.startswith("/"):
                cat.pending_http.append((item.key, "GET", target_href, link["line"], file.path))
                cat.pending_screens.append((item.key, target_href, "navigates to"))
        for call in backend_calls:
            cat.pending_http.append((item.key, call["method"], call["url"], call["line"], call.get("file", file.path)))
        for s_ref in script_refs:
            cat.extra.setdefault("html_scripts", []).append((item.key, s_ref))
    for src_key, dst_key in html_links_pending:
        if dst_key in cat.items:
            cat.link(src_key, dst_key, "navigates to")


def _js_modules(ctx: Context, cat: Catalog, http: HttpIndex) -> None:
    """Extracts JS/TS classes and storage/service modules (including vanilla JS/HTML apps and Angular @Injectable services)."""
    script_to_screens: dict[str, set[str]] = {}
    for screen_key, entry_script in cat.extra.get("html_scripts", []):
        queue = [entry_script]
        visited = set()
        while queue:
            cur = queue.pop(0)
            if cur in visited:
                continue
            visited.add(cur)
            script_to_screens.setdefault(cur, set()).add(screen_key)
            cur_file = ctx.by_path.get(cur)
            if cur_file and cur_file.text:
                for _names, spec in _imports(cur_file.text):
                    nxt = _resolve_import(ctx, cur, spec) or _resolve_relative_path(ctx, cur, spec)
                    if nxt and nxt not in visited:
                        queue.append(nxt)

    for file in ctx.code_files(*TS):
        if file.is_test or re.search(r"\.(tsx|jsx)$", file.path) or f"ui:{file.path}" in cat.items:
            continue
        clean = strip_comments(file.text)
        # Check for exported ES classes or Angular @Injectable / @Component classes
        for m in re.finditer(r"(?:(@Injectable|@Component)\s*\([\s\S]{0,400}?\)\s*)?(?:export\s+(?:default\s+)?)?class\s+([A-Z]\w*)(?:\s+extends\s+([\w.]+))?\s*\{", clean):
            decorator = m.group(1) or ""
            cls_name = m.group(2)
            base_cls = m.group(3) or ""
            start = clean.find("{", m.end() - 1)
            end = match_close(clean, start)
            body = clean[start + 1 : end]
            line = clean.count("\n", 0, m.start()) + 1
            end_line = clean.count("\n", 0, end) + 1
            methods = []
            for mm in re.finditer(r"^\s*(?:async\s+)?([a-zA-Z_]\w*)\s*\(([^)]*)\)\s*(?::\s*[^{]+)?\{", body, re.M):
                mname = mm.group(1)
                if mname in ("if", "for", "while", "switch", "catch", "constructor"):
                    continue
                methods.append({"name": mname, "signature": f"{mname}({mm.group(2).strip()})", "line": line + body.count("\n", 0, mm.start())})
            if not methods and not decorator:
                continue
            is_storage = bool(re.search(r"(Storage|Repository|Store|Cache|Dao|Database)", cls_name, re.I)) or "localStorage" in body or "indexedDB" in body
            is_ui_scene = bool(re.search(r"(Scene|Renderer|View|UI|Input|Controller|Component)$", cls_name)) or decorator == "@Component"
            layer = "data" if is_storage else "ui" if is_ui_scene else "service"
            kind = "repository" if is_storage else "component" if is_ui_scene else "service"
            prefix = "repo" if is_storage else "ui" if is_ui_scene else "svc"
            item_key = f"{prefix}:{file.path}:{cls_name}"
            fw = "Angular" if decorator else ("Phaser" if "Phaser" in base_cls or "Scene" in base_cls else file.language)
            item = cat.add(Item(layer, kind, item_key, cls_name, file.path, line, end_line, file.language, fw,
                                f"class {cls_name}" + (f" extends {base_cls}" if base_cls else ""),
                                {"class": cls_name, "extends": base_cls, "methods": methods[:40], "path": file.path},
                                code=snippet(file.text, line, end_line, 80)))
            cat.type_keys.setdefault(cls_name, item.key)
            for scr_key in script_to_screens.get(file.path, set()):
                cat.link(scr_key, item.key, "uses")
            for call in http.reachable(file.path):
                cat.pending_http.append((item.key, call["method"], call["url"], call["line"], call.get("file", file.path)))

        # Check for exported object singletons (e.g. export const Storage = { getHighScore: () => ... })
        for m in re.finditer(r"export\s+const\s+([A-Z]\w*)\s*=\s*\{", clean):
            obj_name = m.group(1)
            if obj_name in cat.type_keys:
                continue
            start = clean.find("{", m.end() - 1)
            end = match_close(clean, start)
            body = clean[start + 1 : end]
            line = clean.count("\n", 0, m.start()) + 1
            end_line = clean.count("\n", 0, end) + 1
            methods = [{"name": fn, "signature": f"{fn}()", "line": line} for fn in re.findall(r"(\w+)\s*:\s*(?:async\s*)?\([^)]*\)\s*=>", body)]
            if not methods:
                continue
            is_storage = bool(re.search(r"(Storage|Repository|Store|Cache)", obj_name, re.I)) or "localStorage" in body
            layer = "data" if is_storage else "service"
            kind = "repository" if is_storage else "service"
            prefix = "repo" if is_storage else "svc"
            item = cat.add(Item(layer, kind, f"{prefix}:{file.path}:{obj_name}", obj_name, file.path, line, end_line, file.language, file.language,
                                f"const {obj_name}", {"class": obj_name, "methods": methods[:40], "path": file.path},
                                code=snippet(file.text, line, end_line, 60)))
            cat.type_keys.setdefault(obj_name, item.key)
            for scr_key in script_to_screens.get(file.path, set()):
                cat.link(scr_key, item.key, "uses")


def extract(ctx: Context, cat: Catalog) -> None:
    types = ts_types(ctx) if any(f.language == "TypeScript" for f in ctx.files) else {}
    ctx.extra_ts_types = types
    http = HttpIndex(ctx)
    _openapi(ctx, cat)
    _nest(ctx, cat)
    _express(ctx, cat)
    _next_api(ctx, cat)
    _react(ctx, cat, http)
    _templates(ctx, cat, http)
    _js_modules(ctx, cat, http)
    _ts_shared(ctx, cat, types)
    # API client modules (generated SDKs, axios services) as client items so screens can be traced to them
    endpoint_paths = {ep.path for ep in cat.by_kind("endpoint")}
    for path, grouped in http.by_file.items():
        file = ctx.by_path.get(path)
        if file is None or file.is_test or path in endpoint_paths:
            continue
        for function, calls in grouped.items():
            for call in calls[:20]:
                if not function:
                    continue
                key = f"client:{call['method']} {call['url']}"
                if key in cat.items:
                    continue
                item = cat.add(Item("ui", "api_client", key, f"{call['method']} {call['url']}", path, call["line"], call["line"], file.language, call["via"].split(".")[0],
                                    f"{function}()", {"client": PurePosixPath(path).stem, "operation": function, "method": call["method"], "path": call["url"], "via": call["via"],
                                                      "base_url": http.base_urls.get(path, "")}, code=snippet(file.text, call["line"] - 3, call["line"] + 10, 14)))
                cat.pending_http.append((item.key, call["method"], call["url"], call["line"], path))
