"""Apache Camel route extractor.

Extracts Apache Camel routes from:
1. Java & Kotlin RouteBuilder DSL (extends RouteBuilder / EndpointRouteBuilder, from(...), rest(...))
2. Spring XML / Blueprint XML / Camel XML (<camelContext>, <routes>, <route>, <from uri="...">, <rest>)
3. Camel YAML DSL (- route:, - from:, - rest:)

Categorizes ingress into the Layer Catalogue:
- HTTP / REST ingress -> layer="api", kind="endpoint"
- Messaging ingress (Kafka, JMS, RabbitMQ, SQS, etc.) -> layer="service", kind="listener"
- Scheduled timers (timer, quartz, cron) -> layer="service", kind="job"
- Internal routes (direct, seda) -> layer="service", kind="service"

And links routes to:
- Declaring RouteBuilder classes
- Destination message topics / queues (publishes)
- Downstream direct/seda routes (calls)
- Called Java services / beans (calls)
- Database tables (SQL / JPA endpoints)
"""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

import yaml
from app.services.analyzer.catalog import sql as sqlmod
from app.services.analyzer.catalog.model import Catalog, Context, Item, SourceFile, snippet

MESSAGING_SCHEMES = {
    "kafka", "jms", "activemq", "artemis", "rabbitmq", "amqp", "sqs", "aws2-sqs",
    "google-pubsub", "azure-servicebus", "paho", "mqtt", "file", "ftp", "sftp", "aws2-s3",
    "ibmmq", "sjms", "sjms2", "stomp", "pulsar",
}
HTTP_SCHEMES = {
    "platform-http", "servlet", "undertow", "jetty", "netty-http", "rest", "rest-api", "cxf", "spring-ws",
}
TIMER_SCHEMES = {"timer", "quartz", "cron", "scheduler"}
INTERNAL_SCHEMES = {"direct", "direct-vm", "seda", "vm"}


def _parse_uri(uri: str) -> tuple[str, str, dict[str, str]]:
    """Splits a Camel URI into (scheme, destination/path, options_dict)."""
    uri = uri.strip().strip("\"'")
    if not uri or ":" not in uri:
        return "", uri, {}
    scheme, rest = uri.split(":", 1)
    scheme = scheme.strip().lower()
    # Strip leading // if present (e.g. http:// or kafka://)
    if rest.startswith("//"):
        rest = rest[2:]
    dest, _, query = rest.partition("?")
    options: dict[str, str] = {}
    if query:
        for part in query.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                options[k.strip()] = urllib.parse.unquote_plus(v.strip())
            elif part.strip():
                options[part.strip()] = ""
    return scheme, dest.strip(), options


def _line_at(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def extract(ctx: Context, cat: Catalog) -> None:
    has_camel = (
        "camel" in ctx.dep_names
        or any("org.apache.camel" in (f.text[:3000] if f.language in ("Java", "Kotlin") else "") for f in ctx.files[:300])
        or any(re.search(r"xmlns(?::\w+)?=[\"']http://camel\.apache\.org", f.text[:2000]) for f in ctx.files if f.name.endswith(".xml"))
    )
    if not has_camel:
        return

    direct_routes: dict[str, str] = {}  # "direct_name" -> item_key

    # 1. Java & Kotlin RouteBuilder extractors
    for file in ctx.code_files("Java", "Kotlin", "Groovy"):
        if "from(" not in file.text and "rest(" not in file.text and "RouteBuilder" not in file.text:
            continue
        _extract_java_routes(ctx, cat, file, direct_routes)

    # 2. XML Camel routes
    for file in ctx.files:
        if not file.name.endswith(".xml") or not file.text:
            continue
        if "<route" not in file.text and "<routes" not in file.text and "<camelContext" not in file.text:
            continue
        _extract_xml_routes(ctx, cat, file, direct_routes)

    # 3. YAML Camel routes
    for file in ctx.files:
        if not file.name.endswith((".camel.yaml", ".camel.yml", ".routes.yaml", ".routes.yml")) and (
            not file.name.endswith((".yaml", ".yml")) or ("uri:" not in file.text or "from:" not in file.text)
        ):
            continue
        _extract_yaml_routes(ctx, cat, file, direct_routes)


# --- Java / Kotlin DSL extractor -------------------------------------------------------------------------------------


def _extract_java_routes(ctx: Context, cat: Catalog, file: SourceFile, direct_routes: dict[str, str]) -> None:
    text = file.text
    # Check declaring class name
    class_match = re.search(r"\bclass\s+(\w+)(?:[^{]*?extends\s+([^{]+))?", text)
    class_name = class_match.group(1) if class_match else file.stem

    # Extract REST DSL blocks: rest("...").get("...").to("...")
    _extract_java_rest_dsl(ctx, cat, file, class_name)

    # Extract standard routes: from("uri")...
    # Match from("...", fromF("...", or from(ENDPOINT_CONSTANT)
    from_pattern = re.compile(
        r'(?:^|[\s;{}])from\s*\(\s*(?:(["\'][^"\']+["\'])|([A-Za-z0-9_.]+))\s*\)'
    )

    for m in from_pattern.finditer(text):
        start_idx = m.start()
        line = _line_at(text, start_idx)
        raw_uri_expr = m.group(1) or m.group(2)
        raw_uri = _resolve_literal(ctx, raw_uri_expr)
        if not raw_uri:
            continue

        # Lookahead for route chain up to terminating ';'
        end_stmt = text.find(";", start_idx)
        stmt = text[start_idx : end_stmt if end_stmt != -1 else start_idx + 1000]
        end_line = _line_at(text, start_idx + len(stmt))

        # routeId
        route_id_m = re.search(r'\.routeId\s*\(\s*(?:(["\'][^"\']+["\'])|([A-Za-z0-9_.]+))\s*\)', stmt)
        route_id = _resolve_literal(ctx, route_id_m.group(1) or route_id_m.group(2)) if route_id_m else ""

        # targets .to("..."), .toD("..."), .toF("...")
        targets = []
        for to_m in re.finditer(r'\.(?:to|toD|toF|wireTap)\s*\(\s*(?:(["\'][^"\']+["\'])|([A-Za-z0-9_.]+))', stmt):
            tgt_uri = _resolve_literal(ctx, to_m.group(1) or to_m.group(2))
            if tgt_uri:
                targets.append(tgt_uri)

        # called beans / methods: .bean(MyService.class, "method") or .bean("beanName", "method")
        beans = []
        for b_m in re.finditer(r'\.bean\s*\(\s*([^,\)]+)(?:,\s*["\'](\w+)["\'])?', stmt):
            raw_bean = b_m.group(1).strip()
            method = b_m.group(2) or ""
            bean_name = re.sub(r'\.class$', '', raw_bean).strip("\"'")
            beans.append((bean_name, method))

        item = _create_route_item(cat, file, raw_uri, route_id, targets, beans, class_name, line, end_line, stmt)
        if item:
            _link_route(ctx, cat, item, class_name, raw_uri, targets, beans, direct_routes)


def _extract_java_rest_dsl(ctx: Context, cat: Catalog, file: SourceFile, class_name: str) -> None:
    text = file.text
    # Matches rest("path")...
    rest_pattern = re.compile(r'rest\s*\(\s*(?:["\']([^"\']*)["\'])?\s*\)')
    for m in rest_pattern.finditer(text):
        base_path = m.group(1) or ""
        if not base_path.startswith("/") and base_path:
            base_path = "/" + base_path
        start_idx = m.start()
        end_stmt = text.find(";", start_idx)
        stmt = text[start_idx : end_stmt if end_stmt != -1 else start_idx + 2000]

        # Scan for verbs: .get("..."), .post("..."), etc.
        verb_pattern = re.compile(r'\.(get|post|put|delete|patch|head|options)\s*\(\s*(?:["\']([^"\']*)["\'])?\s*\)')
        for vm in verb_pattern.finditer(stmt):
            verb = vm.group(1).upper()
            sub_path = vm.group(2) or ""
            if sub_path and not sub_path.startswith("/"):
                sub_path = "/" + sub_path
            full_path = (base_path.rstrip("/") + sub_path) or "/"
            line = _line_at(text, start_idx + vm.start())

            # Find target inside sub-expression
            sub_stmt = stmt[vm.start():]
            next_verb = verb_pattern.search(sub_stmt, 1)
            verb_block = sub_stmt[: next_verb.start()] if next_verb else sub_stmt
            to_m = re.search(r'\.(?:to|toD)\s*\(\s*["\']([^"\']+)["\']', verb_block)
            target = to_m.group(1).strip() if to_m else ""

            name = f"{verb} {full_path}"
            item = cat.add(
                Item(
                    "api",
                    "endpoint",
                    f"endpoint:camel:{verb}:{full_path}",
                    name,
                    file.path,
                    line,
                    line,
                    file.language,
                    "Apache Camel",
                    f"rest(\"{base_path}\").{verb.lower()}(\"{sub_path}\")",
                    {
                        "http_method": verb,
                        "path": full_path,
                        "framework": "Apache Camel REST DSL",
                        "target": target,
                        "class": class_name,
                    },
                    code=snippet(file.text, line - 1, line + 5, 20),
                )
            )
            if class_name:
                cat.link(item.key, f"svc:{class_name}", "member of")
            if target:
                scheme, dest, _ = _parse_uri(target)
                if scheme in ("direct", "seda"):
                    cat.link(item.key, f"svc:camel:{dest}", "routes to", target)


# --- XML DSL extractor -----------------------------------------------------------------------------------------------


def _extract_xml_routes(ctx: Context, cat: Catalog, file: SourceFile, direct_routes: dict[str, str]) -> None:
    try:
        # Strip XML namespaces for simple element lookup
        xml_clean = re.sub(r'\sxmlns(?::\w+)?=[\'"][^\'"]*[\'"]', '', file.text)
        root = ET.fromstring(xml_clean)
    except Exception:  # noqa: BLE001
        return

    # 1. <rest path="..."> blocks
    for rest in root.iter("rest"):
        base_path = rest.attrib.get("path", "")
        if base_path and not base_path.startswith("/"):
            base_path = "/" + base_path
        for verb_elem in rest:
            verb = verb_elem.tag.upper()
            if verb in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                sub_path = verb_elem.attrib.get("uri", "")
                if sub_path and not sub_path.startswith("/"):
                    sub_path = "/" + sub_path
                full_path = (base_path.rstrip("/") + sub_path) or "/"
                to_elem = verb_elem.find("to")
                target = to_elem.attrib.get("uri", "") if to_elem is not None else ""
                line = file.text[: file.text.find(sub_path or base_path or verb)].count("\n") + 1
                name = f"{verb} {full_path}"
                item = cat.add(
                    Item(
                        "api",
                        "endpoint",
                        f"endpoint:camel:{verb}:{full_path}",
                        name,
                        file.path,
                        line,
                        line,
                        "XML",
                        "Apache Camel",
                        f"<rest path=\"{base_path}\"><{verb.lower()} uri=\"{sub_path}\"/>",
                        {"http_method": verb, "path": full_path, "framework": "Apache Camel XML", "target": target},
                        code=snippet(file.text, max(1, line - 2), line + 6, 20),
                    )
                )
                if target:
                    scheme, dest, _ = _parse_uri(target)
                    if scheme in ("direct", "seda"):
                        cat.link(item.key, f"svc:camel:{dest}", "routes to", target)

    # 2. <route id="..."> blocks
    for route in root.iter("route"):
        route_id = route.attrib.get("id", "")
        from_elem = route.find("from")
        if from_elem is None:
            continue
        raw_uri = from_elem.attrib.get("uri", "")
        if not raw_uri:
            continue

        targets = []
        for to_elem in route.iter("to"):
            if to_elem.attrib.get("uri"):
                targets.append(to_elem.attrib["uri"])
        for to_elem in route.iter("toD"):
            if to_elem.attrib.get("uri"):
                targets.append(to_elem.attrib["uri"])

        beans = []
        for bean_elem in route.iter("bean"):
            ref = bean_elem.attrib.get("ref") or bean_elem.attrib.get("beanType") or ""
            method = bean_elem.attrib.get("method", "")
            if ref:
                beans.append((ref, method))

        line = file.text[: file.text.find(raw_uri)].count("\n") + 1
        item = _create_route_item(cat, file, raw_uri, route_id, targets, beans, file.stem, line, line + 10, "")
        if item:
            _link_route(ctx, cat, item, "", raw_uri, targets, beans, direct_routes)


# --- YAML DSL extractor ----------------------------------------------------------------------------------------------


def _extract_yaml_routes(ctx: Context, cat: Catalog, file: SourceFile, direct_routes: dict[str, str]) -> None:
    try:
        data = yaml.safe_load(file.text)
    except Exception:  # noqa: BLE001
        return

    items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        # - route:
        route_data = entry.get("route") or (entry if "from" in entry else None)
        if route_data and isinstance(route_data, dict):
            route_id = str(route_data.get("id") or "")
            from_data = route_data.get("from")
            raw_uri = ""
            if isinstance(from_data, dict):
                raw_uri = str(from_data.get("uri") or "")
            elif isinstance(from_data, str):
                raw_uri = from_data

            if not raw_uri:
                continue

            targets = []
            beans = []
            steps = []
            if isinstance(from_data, dict) and "steps" in from_data:
                steps = from_data.get("steps") or []
            elif "steps" in route_data:
                steps = route_data.get("steps") or []

            for step in steps:
                if isinstance(step, dict):
                    if "to" in step:
                        t = step["to"]
                        uri = t.get("uri") if isinstance(t, dict) else t
                        if uri:
                            targets.append(str(uri))
                    if "bean" in step:
                        b = step["bean"]
                        ref = b.get("ref") or b.get("beanType") if isinstance(b, dict) else b
                        method = b.get("method", "") if isinstance(b, dict) else ""
                        if ref:
                            beans.append((str(ref), str(method)))

            line = file.text[: file.text.find(raw_uri)].count("\n") + 1
            item = _create_route_item(cat, file, raw_uri, route_id, targets, beans, file.stem, line, line + 10, "")
            if item:
                _link_route(ctx, cat, item, "", raw_uri, targets, beans, direct_routes)

        # - rest:
        rest_data = entry.get("rest")
        if isinstance(rest_data, dict):
            base_path = str(rest_data.get("path") or "")
            if base_path and not base_path.startswith("/"):
                base_path = "/" + base_path
            for verb in ("get", "post", "put", "delete", "patch"):
                if verb in rest_data:
                    verb_info = rest_data[verb]
                    sub_path = ""
                    target = ""
                    if isinstance(verb_info, dict):
                        sub_path = str(verb_info.get("path") or verb_info.get("uri") or "")
                        target = str(verb_info.get("to") or "")
                    full_path = (base_path.rstrip("/") + (("/" + sub_path) if sub_path and not sub_path.startswith("/") else sub_path)) or "/"
                    line = file.text[: file.text.find(base_path or verb)].count("\n") + 1
                    name = f"{verb.upper()} {full_path}"
                    item = cat.add(
                        Item(
                            "api",
                            "endpoint",
                            f"endpoint:camel:{verb.upper()}:{full_path}",
                            name,
                            file.path,
                            line,
                            line,
                            "YAML",
                            "Apache Camel",
                            f"rest: {base_path} {verb}: {sub_path}",
                            {"http_method": verb.upper(), "path": full_path, "framework": "Apache Camel YAML", "target": target},
                            code=snippet(file.text, max(1, line - 2), line + 6, 20),
                        )
                    )
                    if target:
                        scheme, dest, _ = _parse_uri(target)
                        if scheme in ("direct", "seda"):
                            cat.link(item.key, f"svc:camel:{dest}", "routes to", target)


# --- Item creation and linking ---------------------------------------------------------------------------------------


def _create_route_item(
    cat: Catalog,
    file: SourceFile,
    uri: str,
    route_id: str,
    targets: list[str],
    beans: list[tuple[str, str]],
    class_name: str,
    line: int,
    end_line: int,
    stmt: str,
) -> Item | None:
    scheme, dest, options = _parse_uri(uri)
    if not scheme:
        return None

    code_snippet = snippet(file.text, line, end_line, 40) if stmt else ""

    # 1. HTTP Endpoint Ingress
    if scheme in HTTP_SCHEMES:
        method = options.get("httpMethodRestrict", "ANY").upper()
        if "," in method:
            methods = [m.strip() for m in method.split(",")]
        else:
            methods = [method]
        path = dest if dest.startswith("/") else f"/{dest}"
        first_item = None
        for m in methods:
            name = f"{m} {path}"
            item = cat.add(
                Item(
                    "api",
                    "endpoint",
                    f"endpoint:camel:{m}:{path}",
                    name,
                    file.path,
                    line,
                    end_line,
                    file.language,
                    "Apache Camel",
                    f"from(\"{uri}\")",
                    {
                        "http_method": m,
                        "path": path,
                        "uri": uri,
                        "route_id": route_id,
                        "scheme": scheme,
                        "destinations": targets,
                        "beans": [{"name": b[0], "method": b[1]} for b in beans],
                        "class": class_name,
                    },
                    code=code_snippet,
                )
            )
            if not first_item:
                first_item = item
        return first_item

    # 2. Messaging Listener Ingress
    if scheme in MESSAGING_SCHEMES:
        name = route_id or f"{scheme}:{dest}"
        item = cat.add(
            Item(
                "service",
                "listener",
                f"listener:camel:{name}",
                name,
                file.path,
                line,
                end_line,
                file.language,
                "Apache Camel",
                f"from(\"{uri}\")",
                {
                    "class": class_name,
                    "transport": scheme,
                    "destinations": [dest],
                    "route_id": route_id,
                    "uri": uri,
                    "options": options,
                    "targets": targets,
                    "calls": [f"{b[0]}.{b[1]}" if b[1] else b[0] for b in beans],
                },
                code=code_snippet,
            )
        )
        return item

    # 3. Scheduled Trigger Ingress
    if scheme in TIMER_SCHEMES:
        name = route_id or f"{scheme}:{dest}"
        sched: dict[str, str] = {}
        if scheme == "timer" and "period" in options:
            sched["period"] = options["period"]
        if "cron" in options:
            sched["cron"] = options["cron"]
        item = cat.add(
            Item(
                "service",
                "job",
                f"job:camel:{name}",
                name,
                file.path,
                line,
                end_line,
                file.language,
                "Apache Camel",
                f"from(\"{uri}\")",
                {
                    "class": class_name,
                    "trigger": scheme,
                    "schedule": sched,
                    "human": f"Every {sched.get('period', 'N/A')}ms" if "period" in sched else sched.get("cron", "scheduled"),
                    "route_id": route_id,
                    "uri": uri,
                    "targets": targets,
                    "calls": [f"{b[0]}.{b[1]}" if b[1] else b[0] for b in beans],
                },
                code=code_snippet,
            )
        )
        return item

    # 4. Internal direct / seda or general route
    name = route_id or f"{scheme}:{dest}"
    item = cat.add(
        Item(
            "service",
            "service",
            f"svc:camel:{name}",
            name,
            file.path,
            line,
            end_line,
            file.language,
            "Apache Camel",
            f"from(\"{uri}\")",
            {
                "class": class_name,
                "route_id": route_id,
                "scheme": scheme,
                "dest": dest,
                "uri": uri,
                "targets": targets,
                "methods": [{"name": name, "signature": f"route {name}"}],
                "calls": [f"{b[0]}.{b[1]}" if b[1] else b[0] for b in beans],
            },
            code=code_snippet,
        )
    )
    return item


def _link_route(
    ctx: Context,
    cat: Catalog,
    item: Item,
    class_name: str,
    ingress_uri: str,
    targets: list[str],
    beans: list[tuple[str, str]],
    direct_routes: dict[str, str],
) -> None:
    in_scheme, in_dest, _ = _parse_uri(ingress_uri)
    if in_scheme in ("direct", "seda"):
        direct_routes[in_dest] = item.key

    # Link to declaring RouteBuilder class
    if class_name:
        cat.link(item.key, f"svc:{class_name}", "member of")

    # Link to ingress messaging topic
    if in_scheme in MESSAGING_SCHEMES and in_dest:
        cat.pending_topics.append((item.key, {"kind": in_scheme, "name": in_dest}, "consumes"))

    # Link to destinations (.to(...))
    for tgt in targets:
        scheme, dest, _ = _parse_uri(tgt)
        if not scheme:
            continue
        if scheme in ("direct", "seda"):
            target_key = direct_routes.get(dest) or f"svc:camel:{scheme}:{dest}"
            cat.link(item.key, target_key, "calls", f"routes to {scheme}:{dest}")
        elif scheme in MESSAGING_SCHEMES:
            cat.pending_topics.append((item.key, {"kind": scheme, "name": dest}, "publishes"))
        elif scheme in ("sql", "jdbc", "mybatis", "jpa"):
            # Check for SQL table references inside SQL component
            tables = sqlmod.tables_in(dest)
            if tables:
                item.details.setdefault("tables", []).extend(list(tables.keys()))
            for tbl, ops in tables.items():
                cat.pending_tables.append((item.key, tbl, "/".join(ops), ""))
            cat.link(item.key, f"data:{scheme}", "operates on", f"{scheme} {dest[:40]}")
        elif scheme in ("http", "https", "http4", "ahc", "netty-http"):
            cat.link(item.key, f"ext:{scheme}", "calls external", tgt[:60])

    # Link to called Java beans/services
    for bean_name, method in beans:
        target_svc = cat.type_keys.get(bean_name) or f"svc:{bean_name}"
        cat.link(item.key, target_svc, "calls", method or "process")


def _resolve_literal(ctx: Context, expr: str) -> str:
    """Extracts string literal or constant value from an argument expression."""
    if not expr:
        return ""
    expr = expr.strip()
    # "literal"
    if (expr.startswith('"') and expr.endswith('"')) or (expr.startswith("'") and expr.endswith("'")):
        return expr[1:-1]
    # Class.CONSTANT or CONSTANT
    if "." in expr:
        _, member = expr.rsplit(".", 1)
        if member in ctx.constants:
            return ctx.constants[member]
    elif expr in ctx.constants:
        return ctx.constants[expr]
    return ""
