"""Camel routes -> the HTTP calls they make to other services, with full URLs, plus the route graph that ties every
ingress (REST DSL endpoint, servlet, Kafka listener, timer) to the downstream calls it can reach.

What it understands, beyond the literal ``.to("http://host/path")``:

* property placeholders ``{{key}}``, ``{{key:default}}``, ``{{env:NAME}}``, ``${key}`` and ``${properties:key}``, per profile;
* simple-language parts ``${header.id}`` / ``${exchangeProperty.x}`` / ``${body}`` (kept as ``{id}`` path parameters, or
  substituted when an upstream ``setHeader`` set them to a constant);
* ``toD``, ``toF``, ``enrich``, ``wireTap``, ``recipientList`` and ``routingSlip`` (also with several URIs);
* ``setHeader(Exchange.HTTP_METHOD | HTTP_PATH | HTTP_URI | HTTP_QUERY)`` and ``removeHeaders("CamelHttp*")``;
* ``bridgeEndpoint=true`` pass-through (the egress path is the base path + the ingress path) and the HTTP method inherited
  from the ingress endpoint;
* ``rest:get:/x?host=...`` producers;
* ``direct:`` / ``seda:`` / ``direct-vm:`` / ``vm:`` hops (also ``direct:${header.op}`` fan-out), so a v1 and a v2 route
  that share one ``direct:`` route both end at the same downstream call.

Each distinct downstream call becomes an ``integration`` item (the same shape Feign clients use), so the landscape linker
matches it to the endpoint of the service on the other side without any Camel-specific code.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from app.services.analyzer.catalog import config as configmod
from app.services.analyzer.catalog.model import Catalog, Context, Item, SourceFile, snippet
from app.services.analyzer.catalog.structure import match_close, split_top, strip_comments
from app.services.analyzer.catalog.uris import Resolved, join_url, resolver_for

HTTP_PRODUCERS = {"http", "https", "http4", "https4", "netty-http", "netty4-http", "ahc", "vertx-http", "undertow", "jetty", "cxfrs", "rest", "rest-openapi"}
INTERNAL = {"direct", "direct-vm", "seda", "vm"}
HTTP_INGRESS = {"platform-http", "servlet", "undertow", "jetty", "netty-http", "rest", "rest-api", "cxf", "cxfrs"}
CAMEL_OPTIONS = {"bridgeendpoint", "throwexceptiononfailure", "httpmethod", "connecttimeout", "sockettimeout", "copyheaders", "disablestreamcache",
                 "authmethod", "authusername", "authpassword", "proxyhost", "proxyport", "httpclient", "cookiehandler", "mapheaders", "matchonuriprefix",
                 "okstatuscoderange", "followredirects", "synchronous", "transferexception", "chunked", "headerfilterstrategy", "host", "producerapi", "produces", "consumes",
                 "sslcontextparameters", "useglobalsslcontextparameters", "x509hostnameverifier", "clientconnectionmanager", "httpbinding", "bindingmode", "contextpath"}
HEADER_ALIASES = {
    "exchange.http_method": "CamelHttpMethod", "exchange.http_path": "CamelHttpPath", "exchange.http_uri": "CamelHttpUri", "exchange.http_query": "CamelHttpQuery",
    "exchange.content_type": "Content-Type", "exchange.http_response_code": "CamelHttpResponseCode", "exchange.http_url": "CamelHttpUrl",
    "httpconstants.http_method": "CamelHttpMethod", "httpconstants.http_path": "CamelHttpPath", "httpconstants.http_uri": "CamelHttpUri", "httpconstants.http_query": "CamelHttpQuery",
}
VERSION = re.compile(r"(?i)(?:^|/)(v\d+(?:[._]\d+)?|\d+\.\d+)(?=/|$)")
STEP_CALL = re.compile(r"\.\s*(to|toD|toF|enrich|pollEnrich|wireTap|recipientList|routingSlip|setHeader|setHeaders|removeHeaders|removeHeader|choice|when|otherwise|end|endChoice|inOut|inOnly)\s*\(")


@dataclass
class Step:
    kind: str  # to / toD / enrich / wireTap / recipientList / routingSlip / setHeader / removeHeaders / when / otherwise / end / choice
    expr: str = ""  # uri, header value, condition or pattern
    name: str = ""  # header name
    language: str = ""  # constant / simple / header / ... (XML, YAML) ; "java" for an unparsed Java expression
    line: int = 0
    args: list[str] = field(default_factory=list)


@dataclass
class Route:
    key: str
    file: SourceFile
    line: int
    ingress: str  # raw from(...) uri, or "rest:METHOD:/path" for REST DSL verbs
    route_id: str = ""
    owner: str = ""
    steps: list[Step] = field(default_factory=list)
    method: str = ""  # HTTP ingress method
    path: str = ""  # HTTP ingress path (relative to the REST / servlet context)
    scheme: str = ""
    dest: str = ""
    java: bool = False
    code: str = ""
    prefix: bool = False  # matchOnUriPrefix: the route serves everything below its path


@dataclass
class Egress:
    route: Route
    method: str = ""
    method_source: str = ""
    scheme: str = ""
    base_url: str = ""
    host: str = ""
    path: str = ""
    raw: str = ""
    unresolved: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    passthrough: bool = False
    target_hint: str = ""
    condition: str = ""
    line: int = 0
    fuzzy: bool = False
    query: dict = field(default_factory=dict)
    fraction: float = 1.0
    keys: list[str] = field(default_factory=list)
    inherit_method: bool = False


# --- step extraction ------------------------------------------------------------------------------------------------


def _header_name(text: str) -> str:
    text = (text or "").strip()
    inner = re.match(r"^(?:constant|simple)\s*\(\s*(.*)\)$", text)
    text = inner.group(1) if inner else text
    low = text.lower().replace("org.apache.camel.", "").replace("org.apache.camel.component.http.", "")
    if low in HEADER_ALIASES:
        return HEADER_ALIASES[low]
    m = re.match(r"^[\"'](.*)[\"']$", text)
    return m.group(1) if m else text


def steps_from_java(stmt: str, base_line: int = 1) -> list[Step]:
    """Ordered Camel DSL steps of one ``from(...)...;`` / ``rest(...)...;`` statement."""
    stmt = strip_comments(stmt)
    out: list[Step] = []
    for m in STEP_CALL.finditer(stmt):
        kind = m.group(1)
        open_at = m.end() - 1
        close = match_close(stmt, open_at)
        args = split_top(stmt[open_at + 1 : close])
        line = base_line + stmt.count("\n", 0, m.start())
        step = Step(kind=kind, line=line, language="java", args=args)
        if kind in ("to", "toD", "enrich", "pollEnrich", "wireTap", "recipientList", "routingSlip", "inOut", "inOnly") and args:
            step.expr = args[0]
            if kind in ("recipientList", "routingSlip") and len(args) > 1:
                step.args = args
        elif kind == "toF":
            step.kind, step.expr, step.args = "to", "String.format(" + ", ".join(args) + ")", args
        elif kind == "setHeader" and len(args) >= 2:
            step.name, step.expr = _header_name(args[0]), args[1]
        elif kind in ("removeHeaders", "removeHeader") and args:
            step.name = _header_name(args[0])
        elif kind == "when" and args:
            step.expr = args[0]
        out.append(step)
    return out


def _unwrap_java(expr: str) -> tuple[str, str]:
    """``simple("x")`` -> (simple, "x"); ``constant(X)`` -> (constant, X); ``header("n")`` -> (header, "n"); a literal -> (constant, literal)."""
    expr = (expr or "").strip()
    m = re.match(r"^(simple|constant|header|exchangeProperty|jsonpath|xpath|body|method|groovy|jq|datasonnet|ref|variable)\s*\((.*)\)$", expr, re.S)
    if m:
        args = split_top(m.group(2))
        return m.group(1), (args[0] if args else "")
    return "constant", expr


def _text_of(el: ET.Element) -> str:
    return "".join(el.itertext()).strip()


def steps_from_xml(route: ET.Element, text: str) -> list[Step]:
    out: list[Step] = []

    def line_of(el: ET.Element) -> int:
        uri = el.attrib.get("uri") or el.attrib.get("name") or ""
        idx = text.find(uri) if uri else -1
        return text.count("\n", 0, idx) + 1 if idx >= 0 else 0

    def walk(el: ET.Element) -> None:
        for child in el:
            tag = child.tag
            if tag in ("to", "toD", "enrich", "pollEnrich", "wireTap"):
                uri = child.attrib.get("uri") or ""
                language = ""
                if not uri:
                    for sub in child:
                        if sub.tag in ("constant", "simple", "header"):
                            uri, language = _text_of(sub), sub.tag
                            break
                out.append(Step(kind=tag if tag != "pollEnrich" else "enrich", expr=uri, language=language or "constant", line=line_of(child)))
            elif tag in ("recipientList", "routingSlip"):
                language, value = "simple", child.attrib.get("uri") or ""
                for sub in child:
                    if sub.tag in ("constant", "simple", "header", "jsonpath", "xpath", "method", "groovy", "ref", "exchangeProperty"):
                        language, value = sub.tag, _text_of(sub)
                        break
                out.append(Step(kind=tag, expr=value, language=language, line=line_of(child), args=[child.attrib.get("delimiter", ",")]))
            elif tag in ("setHeader", "setProperty", "setExchangeProperty"):
                name = _header_name(child.attrib.get("name") or child.attrib.get("headerName") or "")
                language, value = "constant", child.attrib.get("value") or ""
                for sub in child:
                    language, value = sub.tag, _text_of(sub)
                    break
                if tag == "setHeader":
                    out.append(Step(kind="setHeader", name=name, expr=value, language=language, line=line_of(child)))
            elif tag in ("removeHeaders", "removeHeader"):
                out.append(Step(kind=tag, name=child.attrib.get("pattern") or child.attrib.get("name") or _text_of(child), line=line_of(child)))
            elif tag == "choice":
                out.append(Step(kind="choice"))
                walk(child)
                out.append(Step(kind="end"))
                continue
            elif tag == "when":
                cond = ""
                for sub in child:
                    if sub.tag in ("simple", "header", "jsonpath", "xpath", "constant", "groovy", "method"):
                        cond = _text_of(sub)
                        break
                out.append(Step(kind="when", expr=cond))
            elif tag == "otherwise":
                out.append(Step(kind="otherwise"))
            walk(child)

    walk(route)
    return out


def steps_from_yaml(steps: list) -> list[Step]:
    out: list[Step] = []

    def uri_of(value) -> str:
        return str(value.get("uri") or "") if isinstance(value, dict) else str(value or "")

    def walk(items: list) -> None:
        for step in items or []:
            if not isinstance(step, dict):
                continue
            for key, value in step.items():
                if key in ("to", "toD", "enrich", "wireTap"):
                    out.append(Step(kind=key, expr=uri_of(value), language="constant"))
                elif key == "setHeader" and isinstance(value, dict):
                    expr = value.get("simple") or value.get("constant") or value.get("expression") or ""
                    if isinstance(expr, dict):
                        expr = expr.get("expression") or ""
                    language = "simple" if value.get("simple") else "constant"
                    out.append(Step(kind="setHeader", name=_header_name(str(value.get("name") or "")), expr=str(expr), language=language))
                elif key in ("removeHeaders", "removeHeader"):
                    out.append(Step(kind=key, name=str(value.get("pattern") if isinstance(value, dict) else value)))
                elif key in ("recipientList", "routingSlip") and isinstance(value, dict):
                    expr = value.get("simple") or value.get("constant") or ""
                    out.append(Step(kind=key, expr=str(expr), language="simple" if value.get("simple") else "constant", args=[str(value.get("delimiter") or ",")]))
                elif key == "choice" and isinstance(value, dict):
                    out.append(Step(kind="choice"))
                    for branch in value.get("when") or []:
                        cond = branch.get("simple") if isinstance(branch, dict) else ""
                        out.append(Step(kind="when", expr=str(cond or "")))
                        walk(branch.get("steps") or [])
                    if isinstance(value.get("otherwise"), dict):
                        out.append(Step(kind="otherwise"))
                        walk(value["otherwise"].get("steps") or [])
                    out.append(Step(kind="end"))

    walk(steps)
    return out


# --- properties -----------------------------------------------------------------------------------------------------


class Props:
    """Property values from every config file, per profile, for ``{{key}}`` / ``${key}`` expansion."""

    def __init__(self, ctx: Context):
        self.values: dict[str, list[tuple[str, str]]] = {}
        for entry in getattr(ctx, "config_entries", []) or []:
            self._add(entry["key"], entry.get("profile") or "default", entry.get("raw", entry.get("value", "")))
        for file in ctx.files:
            if not file.text or file.is_test or not file.name.endswith((".properties", ".cfg")) or configmod._is_spring_config(file.path):
                continue
            profile = configmod._profile(file.path)
            for key, value, _line, _comment in configmod.parse_properties(file.text):
                self._add(key, profile, value)
        for key, value in ctx.config_values.items():
            self._add(key, "default", value)

    def _add(self, key: str, profile: str, value: str) -> None:
        bucket = self.values.setdefault(key, [])
        if (profile, value) not in bucket:
            bucket.append((profile, value))

    def lookup(self, key: str, profile: str) -> str | None:
        vals = self.values.get(key) or []
        for p, v in vals:
            if p == profile:
                return v
        for p, v in vals:
            if p in ("default", ""):
                return v
        return vals[0][1] if vals else None

    def profiles_for(self, keys: list[str]) -> list[str]:
        found = {p for k in keys for p, _ in self.values.get(k, []) if p not in ("default", "")}
        return sorted(found)

    def expand(self, text: str, extra_env: dict[str, str] | None = None) -> tuple[str, list[str], list[str], list[str]]:
        """-> (text for the default profile, other profiles' texts, unresolved keys, keys used)."""
        keys: list[str] = []
        missing: list[str] = []

        def run(profile: str, record: bool) -> str:
            def sub(m: re.Match) -> str:
                raw = m.group(1).strip()
                optional = raw.startswith("?")
                raw = raw.lstrip("?")
                raw = re.sub(r"^properties:", "", raw)
                if raw.startswith(("env:", "sys:", "env-var:", "sys-prop:")):
                    parts = raw.split(":", 2)
                    name = parts[1]
                    default = parts[2] if len(parts) > 2 else None
                    value = self.lookup(name, profile) or (extra_env or {}).get(name)
                    if value is None:
                        value = default
                    if record:
                        keys.append(name)
                    if value is None:
                        if record:
                            missing.append(name)
                        return "{" + name + "}"
                    return value
                key, _, default = raw.partition(":")
                if record:
                    keys.append(key)
                value = self.lookup(key, profile)
                if value is None:
                    value = default if default else None
                if value is None:
                    if record and not optional:
                        missing.append(key)
                    return "{" + key + "}"
                return value

            out = text
            for _ in range(3):
                nxt = re.sub(r"\{\{([^{}]+)\}\}", sub, out)
                nxt = re.sub(r"\$\{(?!(?:header|headers|exchangeProperty|exchange|in|out|body|bodyAs|date|bean|camelId|camelContext|file|id|routeId|variable|jsonpath|xpath|ref|random|collate|hostname|messageHistory|sys|env|null)\b)([^{}]+)\}", sub, nxt)
                if nxt == out:
                    break
                out = nxt
            return out

        base = run("default", True)
        variants = []
        for profile in self.profiles_for(keys):
            alt = run(profile, False)
            if alt != base and alt not in variants:
                variants.append(alt)
        return base, variants[:4], missing, keys


# --- helpers --------------------------------------------------------------------------------------------------------


def _dynamic(text: str, headers: dict[str, tuple[str, str]]) -> tuple[str, list[str]]:
    """Simple-language ``${header.id}`` -> ``{id}`` (or the constant an upstream setHeader gave it)."""
    notes: list[str] = []

    def sub(m: re.Match) -> str:
        inner = m.group(1).strip()
        mh = re.match(r"^(?:in\.)?headers?[.\[]\s*['\"]?([\w.-]+)['\"]?\]?", inner)
        if mh and mh.group(1) in headers and headers[mh.group(1)][0] == "constant" and "$" not in headers[mh.group(1)][1]:
            return headers[mh.group(1)][1]
        name = re.split(r"[.:\[]", inner)[-1].strip(" ]'\"") or "value"
        name = re.sub(r"\W", "", name) or "value"
        if name not in notes:
            notes.append(name)
        return "{" + name + "}"

    return re.sub(r"\$(?:simple)?\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", sub, text or ""), notes


SECRET_KEY = re.compile(r"(?i)(key|token|secret|pass|pwd|auth|credential|signature|sig$)")


def mask_url(text: str) -> str:
    """Credentials never leave the analysis: ``user:pw@`` userinfo and secret-looking query values are masked."""
    text = re.sub(r"(//)[^/@\s]+:[^/@\s]*@", r"\1••••@", text or "")
    return re.sub(r"([?&](?:[\w.-]*))=([^&\s\"')]+)", lambda m: m.group(0) if not SECRET_KEY.search(m.group(1)) else f"{m.group(1)}=••••", text)


def target_hint(keys: list[str], host: str) -> str:
    """A service name hint: the config key's stem (``dbe.account.url`` -> ``dbe-account``) or the host's first label."""
    for key in keys:
        stem = re.sub(r"[._]+", "-", key).lower()
        stem = re.sub(r"-(url|uri|host|hostname|endpoint|base|baseurl|base-url|address|server|root|path)$", "", stem)
        stem = re.sub(r"^(camel|app|service|services|api|endpoints?|remote|downstream|backend|url)-", "", stem)
        if stem and len(stem) > 2:
            return stem
    host = (host or "").split(":")[0]
    if host and not host.startswith("{") and not re.match(r"^\d+(\.\d+){3}$", host) and host not in ("localhost",):
        return host.split(".")[0]
    return ""


def version_of(*paths: str) -> str:
    for path in paths:
        m = VERSION.search(path or "")
        if m:
            return m.group(1).lower().replace("_", ".")
    return ""


def parse_http_uri(uri: str) -> tuple[str, str, str, dict[str, str], str]:
    """-> (scheme, host, path, options, rest_method).  ``rest:post:/x?host=h`` and ``http://h:1/x?a=b`` forms."""
    uri = (uri or "").strip().strip("\"'")
    uri = re.sub(r"^RAW\((.*)\)$", r"\1", uri)
    if ":" not in uri:
        return "", "", uri, {}, ""
    scheme, rest = uri.split(":", 1)
    scheme = scheme.lower()
    body, _, query = rest.partition("?")
    options: dict[str, str] = {}
    for part in query.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            options[k.strip()] = v.strip()
        elif part.strip():
            options[part.strip()] = ""
    if scheme == "rest":
        parts = body.split(":", 1)
        method, path = (parts[0], parts[1]) if len(parts) == 2 and re.match(r"^(get|post|put|delete|patch|head|options)$", parts[0], re.I) else ("", body)
        host = options.get("host", "")
        return scheme, host, "/" + path.lstrip("/") if path else "/", options, method.upper()
    if scheme == "rest-openapi":
        return scheme, options.get("host", ""), "", options, ""
    body = body.lstrip("/") if rest.startswith("//") else body
    host, slash, path = body.partition("/")
    if "{" in host and "}" not in host:  # a placeholder broke the split
        host, slash, path = body, "", ""
    return scheme, host, ("/" + path if slash else ""), options, ""


def rest_context_path(ctx: Context, props: Props) -> str:
    """Spring context path + Camel servlet mapping + restConfiguration contextPath: the prefix of every Camel REST endpoint."""
    parts: list[str] = []
    for key in ("server.servlet.context-path", "server.context-path"):
        value = props.lookup(key, "default")
        if value:
            parts.append(value)
            break
    for key in ("camel.servlet.mapping.context-path", "camel.component.servlet.mapping.context-path"):
        value = props.lookup(key, "default")
        if value:
            parts.append(value.replace("/*", ""))
            break
    value = props.lookup("camel.rest.context-path", "default")
    if not value:
        for file in ctx.files:
            if file.is_test or not file.text:
                continue
            if file.language in ("Java", "Kotlin", "Groovy"):
                m = re.search(r"restConfiguration\s*\(\s*[^)]*\)[\s\S]{0,400}?\.contextPath\s*\(\s*\"([^\"]+)\"", file.text)
            elif file.name.endswith(".xml"):
                m = re.search(r"<restConfiguration[^>]*\bcontextPath=\"([^\"]+)\"", file.text)
            else:
                m = None
            if m:
                value = m.group(1)
                break
    if value:
        parts.append(value)
    joined = ""
    for p in parts:
        p = props.expand(p)[0]
        joined = "/" + "/".join(x for x in (joined + "/" + p).split("/") if x)
    return joined.rstrip("/")


# --- analysis -------------------------------------------------------------------------------------------------------


class Analyzer:
    def __init__(self, ctx: Context, cat: Catalog, routes: list[Route]):
        self.ctx, self.cat, self.routes = ctx, cat, routes
        self.props = Props(ctx)
        self.resolver = resolver_for(ctx)
        self.by_name: dict[str, list[Route]] = {}
        for route in routes:
            if route.scheme in INTERNAL and route.dest:
                self.by_name.setdefault(route.dest, []).append(route)
        self.egress: dict[int, list[Egress]] = {}
        self.calls: dict[int, list[tuple[str, bool, str]]] = {}  # route index -> [(direct name, fuzzy, condition)]

    # evaluating a step's expression into text ---------------------------------

    def _java(self, expr: str, route: Route) -> Resolved:
        return self.resolver.resolve(expr, route.file, route.owner)

    def _value(self, step: Step, route: Route, headers: dict) -> tuple[str, str, list[str], list[str]]:
        """-> (text, language, unresolved names, variants).  Property placeholders are expanded, simple-language parts become {name}."""
        language, expr = step.language, step.expr
        unresolved: list[str] = []
        variants: list[str] = []
        if language == "java":
            language, inner = _unwrap_java(expr)
            if language in ("header", "exchangeProperty", "variable"):
                name = re.sub(r"^[\"']|[\"']$", "", inner.strip())
                return "{" + re.sub(r"\W", "", name) + "}", language, [], []
            if language in ("jsonpath", "xpath", "body", "method", "groovy", "jq", "ref", "datasonnet"):
                return "{" + (re.sub(r"\W", "", language)) + "}", language, [], []
            resolved = self._java(inner, route)
            text, unresolved, variants = resolved.text, list(resolved.unresolved), list(resolved.variants)
        else:
            text = expr
        text, props_variants, missing, _keys = self.props.expand(text)
        variants += [_dynamic(v.split("?", 1)[0], headers)[0] for v in props_variants]
        unresolved += [m for m in missing if m not in unresolved]
        text, dynamic = _dynamic(text, headers)
        return text, language, unresolved, variants

    def _keys_in(self, text: str) -> list[str]:
        simple = re.compile(r"^(?:header|headers|exchangeProperty|exchange|in|out|body|bodyAs|date|bean|camelId|camelContext|file|id|routeId|variable|jsonpath|xpath|ref|random|hostname|null)\b")
        found = [k.split(":")[0].lstrip("?") for k in re.findall(r"\{\{([^{}]+)\}\}", text)] + re.findall(r"\$\{([\w.-]+)\}", text)
        return [k for k in found if not simple.match(k)]

    # walking one route ---------------------------------------------------------

    def analyze(self) -> None:
        for index, route in enumerate(self.routes):
            headers: dict[str, tuple[str, str]] = {}
            egress: list[Egress] = []
            calls: list[tuple[str, bool, str]] = []
            conditions: list[str] = []
            cleared_http = False
            for step in route.steps:
                if step.kind == "choice":
                    conditions.append("")
                elif step.kind == "when":
                    text = step.expr
                    if step.language == "java":
                        text = " ".join(_unwrap_java(step.expr)[1].split())
                    if conditions:
                        conditions[-1] = text
                    else:
                        conditions.append(text)
                elif step.kind == "otherwise":
                    if conditions:
                        conditions[-1] = "otherwise"
                elif step.kind in ("end", "endChoice"):
                    if conditions:
                        conditions.pop()
                elif step.kind == "setHeader":
                    text, language, _u, _v = self._value(step, route, headers)
                    headers[step.name] = (language if language in ("constant", "simple") else "dynamic", text.strip().strip("\"'"))
                elif step.kind in ("removeHeaders", "removeHeader"):
                    pattern = (step.name or "").strip("\"'")
                    if re.match(r"^(CamelHttp\*|Camel\*|\*)$", pattern) or pattern.startswith("CamelHttp"):
                        cleared_http = True
                        for name in list(headers):
                            if name.startswith("CamelHttp") or pattern in ("*", "Camel*"):
                                headers.pop(name, None)
                elif step.kind in ("to", "toD", "enrich", "wireTap", "recipientList", "routingSlip", "inOut", "inOnly"):
                    condition = next((c for c in reversed(conditions) if c), "")
                    text, language, unresolved, variants = self._value(step, route, headers)
                    delim = (step.args[0].strip("\"'") if step.kind in ("recipientList", "routingSlip") and step.args and step.language != "java" else ",")
                    if step.kind in ("recipientList", "routingSlip") and step.language == "java" and len(step.args) > 1:
                        delim = step.args[1].strip("\"'")
                    uris = [u.strip() for u in text.split(delim or ",") if u.strip()] if step.kind in ("recipientList", "routingSlip") else [text.strip()]
                    for uri in uris:
                        self._one(route, step, uri, unresolved, variants, headers, condition, cleared_http, egress, calls)
            self.egress[index] = egress
            self.calls[index] = calls

    def _one(self, route: Route, step: Step, uri: str, unresolved: list[str], variants: list[str], headers: dict, condition: str,
             cleared_http: bool, egress: list[Egress], calls: list[tuple[str, bool, str]]) -> None:
        uri = uri.strip()
        scheme = uri.split(":", 1)[0].lower() if ":" in uri else ""
        if scheme in INTERNAL:
            dest = uri.split(":", 1)[1].lstrip("/").split("?", 1)[0]
            calls.append((dest, "{" in dest, condition))
            return
        if scheme not in HTTP_PRODUCERS and not uri.startswith("{"):
            return
        raw_scheme, host, path, options, rest_method = parse_http_uri(uri)
        if raw_scheme not in HTTP_PRODUCERS and not uri.startswith("{"):
            return
        host = re.sub(r"^[^@/]*@", "", host)  # credentials in the URL are never kept
        low = {k.lower(): v for k, v in options.items()}
        bridge = low.get("bridgeendpoint", "").lower() == "true"
        method, source = "", ""
        explicit = headers.get("CamelHttpMethod")
        if explicit and explicit[1]:
            method, source = re.sub(r".*[.]", "", explicit[1]).upper(), "setHeader"
        elif low.get("httpmethod"):
            method, source = low["httpmethod"].upper(), "httpMethod option"
        elif rest_method:
            method, source = rest_method, "rest producer"
        inherit = not method and route.method != ""
        uri_header = headers.get("CamelHttpUri")
        extra_path = headers.get("CamelHttpPath")
        if uri_header and uri_header[1] and not bridge:
            alt_scheme, alt_host, alt_path, _o, _m = parse_http_uri(uri_header[1])
            if alt_scheme:
                raw_scheme, host, path = alt_scheme, alt_host, alt_path
            else:
                path = uri_header[1]
        if extra_path and extra_path[1]:
            path = join_url(path or "/", extra_path[1])
        passthrough = bridge and not cleared_http and not extra_path and route.scheme in HTTP_INGRESS | {"rest"} and bool(route.path)
        text_for_keys = step.expr
        keys = self._keys_in(text_for_keys)
        host = host or ""
        placeholder_host = host.startswith("{")
        base = f"{'https' if raw_scheme.startswith('https') else 'http'}://{host}" if host and not placeholder_host else ""
        unresolved = list(unresolved)
        if placeholder_host:
            name = host.strip("{}")
            if name not in unresolved:
                unresolved.append(name)
        total = 1 + len(unresolved)
        host, raw_uri_text = mask_url(host), mask_url(step.expr.strip()[:300])
        variants = [mask_url(v) for v in variants]
        base = mask_url(base)
        e = Egress(route=route, method=method, method_source=source or ("inherited from the ingress endpoint" if inherit else "not set (Camel: GET without body, else POST)"),
                   scheme=raw_scheme, base_url=base, host=host, path=("/" + path.lstrip("/")) if path else ("/" if base else ""), raw=raw_uri_text,
                   unresolved=unresolved, variants=variants, passthrough=passthrough, target_hint=target_hint(keys, host), condition=condition, line=step.line or route.line,
                   fuzzy="{" in (path or ""), query={k: ("••••" if SECRET_KEY.search(k) else v) for k, v in options.items() if k.lower() not in CAMEL_OPTIONS},
                   fraction=round(max(0.0, 1 - len(unresolved) / total), 2), keys=keys, inherit_method=inherit)
        if path.startswith("{") and "/" not in path.strip("/") and not base:
            e.path = "/" + path.lstrip("/")
        egress.append(e)

    # graph -----------------------------------------------------------------------

    def reachable(self, start: int, limit: int = 12) -> list[tuple[int, str, bool]]:
        """Routes reachable from route ``start`` through direct:/seda: hops -> [(route index, condition, fuzzy)]."""
        index_of = {id(r): i for i, r in enumerate(self.routes)}
        out: list[tuple[int, str, bool]] = [(start, "", False)]
        seen = {start}
        queue = [(start, "", False)]
        while queue and len(out) < 200:
            current, cond, fuzzy = queue.pop(0)
            for dest, is_fuzzy, condition in self.calls.get(current, []):
                matches = self.by_name.get(dest, [])
                if not matches and is_fuzzy:
                    pattern = re.compile("^" + re.sub(r"\\\{[^}]*\\\}", ".*", re.escape(dest)) + "$")
                    matches = [r for name, rs in self.by_name.items() if pattern.match(name) for r in rs]
                for route in matches:
                    i = index_of[id(route)]
                    if i in seen:
                        continue
                    seen.add(i)
                    nxt = (i, condition or cond, fuzzy or is_fuzzy)
                    out.append(nxt)
                    queue.append(nxt)
        return out


def finish(ctx: Context, cat: Catalog) -> None:
    routes: list[Route] = cat.extra.pop("camel_routes", [])
    if not routes:
        return
    analyzer = Analyzer(ctx, cat, routes)
    analyzer.analyze()
    context_path = rest_context_path(ctx, analyzer.props)
    # REST endpoints get their context path (servlet mapping / restConfiguration) so their full URL is known
    for route in routes:
        item = cat.items.get(route.key)
        if item is None or item.kind != "endpoint" or not route.path:
            continue
        item.details["path"] = route.path
        if context_path:
            item.details["context_path"] = context_path
            item.details["url"] = join_url(context_path, route.path)
        version = version_of(route.path, context_path)
        if version:
            item.details["version"] = version
        item.details["route_id"] = item.details.get("route_id") or route.route_id
    made = 0
    for index, route in enumerate(routes):
        reach = analyzer.reachable(index)
        for r_index, condition, fuzzy in reach:
            for e in analyzer.egress.get(r_index, []):
                made += _materialize(cat, route, routes[r_index], e, condition or e.condition, fuzzy, context_path)
    # `routes to` links from REST endpoints that name a direct route by its direct name
    for index, route in enumerate(routes):
        item = cat.items.get(route.key)
        if item is None:
            continue
        for dest, _fuzzy, _cond in analyzer.calls.get(index, []):
            for target in analyzer.by_name.get(dest, []):
                if target.key in cat.items and target.key != route.key:
                    cat.link(route.key, target.key, "routes to", f"direct:{dest}")
    cat.extra["camel_egress_count"] = made


def _materialize(cat: Catalog, ingress: Route, owner: Route, e: Egress, condition: str, fuzzy: bool, context_path: str) -> int:
    """One integration item per distinct downstream call; every ingress that reaches it links to it."""
    method = e.method
    path = e.path
    method_source = e.method_source
    if e.inherit_method and ingress.method:
        method, method_source = ingress.method, f"inherited from {ingress.method} {ingress.path}"
    if e.passthrough and ingress.path:
        path = join_url(e.path or "/", "/{path}" if ingress.prefix else ingress.path)
        method_source += "; path passed through (bridgeEndpoint)"
    if not path:
        path = "/"
    version = version_of(path, ingress.path)
    verb = (method or "ANY").upper()
    host_part = e.base_url or e.host or e.target_hint or "?"
    key = f"out:camel:{host_part}:{verb} {path}"
    item = cat.items.get(key)
    if item is None:
        confidence = e.fraction
        details = {
            "client": "Camel", "method": verb if method else "", "path": path, "base_url": e.base_url, "target": e.target_hint, "via": "Camel route " + (owner.route_id or owner.ingress),
            "operation": owner.route_id or owner.ingress, "route_id": owner.route_id, "uri": e.raw, "scheme": e.scheme, "host": e.host,
            "resolved_url": (e.base_url + path) if e.base_url else path, "unresolved": e.unresolved, "variants": e.variants, "method_source": method_source,
            "passthrough": e.passthrough, "condition": condition, "version": version, "confidence": confidence, "query": e.query, "config_keys": e.keys,
            "ingress": [], "framework": "Apache Camel",
        }
        item = cat.add(Item("service", "integration", key, f"{verb} {path}", owner.file.path, e.line, e.line, owner.file.language, "Apache Camel",
                            f"{owner.ingress} → {e.raw}"[:300], details, code=snippet(owner.file.text, max(1, e.line - 3), e.line + 6, 14) if owner.file.text else ""))
        cat.link(owner.key, item.key, "calls", f"{verb} {path}")
    if ingress.key not in item.details["ingress"]:
        item.details["ingress"].append(ingress.key)
    if ingress.key != owner.key or ingress.key in cat.items:
        cat.link(ingress.key, item.key, "calls", f"{verb} {path}" + (" · " + condition if condition else "") + (" (dynamic route)" if fuzzy else ""))
    return 1
