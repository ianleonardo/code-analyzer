"""Cross-layer linking once every extractor has run: calls between classes, UI -> API matching (screens, Retrofit /
Swift / TypeScript clients, server templates), endpoint -> view rendering, Spring Security rules -> endpoints, DTOs,
exceptions, topics, data access (CRUD) and transitive backend calls for mobile screens.
"""

from __future__ import annotations

import fnmatch
import re
from collections import defaultdict, deque

from app.services.analyzer.catalog import jvm
from app.services.analyzer.catalog import sql as sqlmod
from app.services.analyzer.catalog.model import Catalog, Context, Item, normalise_path, snippet
from app.services.analyzer.catalog.structure import simple_type

DTO_NAME = re.compile(r"(Dto|DTO|Request|Response|Form|Command|Payload|Resource|Body|Params|Criteria|Filter|Result|Summary|Details|View|Event|Message|Model)$")


class EndpointIndex:
    def __init__(self, cat: Catalog):
        self.entries = []
        for item in cat.by_kind("endpoint"):
            segs = [s for s in normalise_path(item.details.get("path", "")).split("/") if s]
            self.entries.append((item, item.details.get("method", "ANY"), segs))

    @staticmethod
    def _seg_eq(a: str, b: str) -> bool:
        return a == b or a == "{}" or b == "{}"

    def match(self, method: str, url: str) -> Item | None:
        segs = [s for s in normalise_path(url).split("/") if s]
        if not segs:
            return None
        best, best_score = None, 0.0
        for item, verb, ep in self.entries:
            if not ep:
                continue
            score = 0.0
            if len(ep) == len(segs) and all(self._seg_eq(a, b) for a, b in zip(ep, segs, strict=True)):
                score = 3 + sum(1 for a, b in zip(ep, segs, strict=True) if a == b and a != "{}") * 0.1
            else:
                shorter, longer = (ep, segs) if len(ep) < len(segs) else (segs, ep)
                tail = longer[-len(shorter) :]
                literal = sum(1 for s in shorter if s != "{}")
                if all(self._seg_eq(a, b) for a, b in zip(shorter, tail, strict=True)) and literal >= 1 and (len(shorter) >= 2 or literal == len(shorter)):
                    score = 1.5 + literal * 0.1
            if not score:
                continue
            if verb == method:
                score += 0.5
            elif verb != "ANY" and method not in ("ANY", ""):
                score -= 1.2
            if score > best_score:
                best, best_score = item, score
        return best if best_score >= 1.4 else None


def _container(ctx: Context, cat: Catalog, key: str) -> str:
    item = cat.items.get(key)
    file = ctx.by_path.get(item.path) if item else None
    return file.container if file else ""


def _pick(ctx: Context, cat: Catalog, name: str, source_key: str, default: str | None) -> str | None:
    """When several items share a simple name, prefer the one in the caller's container (module / app)."""
    keys = cat.type_keys.alts.get(name, [])
    if len(keys) <= 1:
        return default
    here = _container(ctx, cat, source_key)
    same = [k for k in keys if _container(ctx, cat, k) == here]
    return same[0] if same else default


def _target_for(cat: Catalog, type_name: str, method: str, implementations: dict[str, str], ctx: Context | None = None, source: str = "") -> str | None:
    members = cat.type_members.get(type_name, {})
    if ctx is not None and len(cat.type_keys.alts.get(type_name, [])) > 1:
        here = _container(ctx, cat, source)
        if method in members and _container(ctx, cat, members[method]) == here:
            return members[method]
        picked = _pick(ctx, cat, type_name, source, None)
        if picked:
            return picked
    if method in members:
        return members[method]
    if type_name in implementations:
        impl = implementations[type_name]
        impl_item = cat.items.get(impl)
        impl_class = impl_item.details.get("class", "") if impl_item else ""
        if method in cat.type_members.get(impl_class, {}):
            return cat.type_members[impl_class][method]
        return impl
    return cat.type_keys.get(type_name)


def _dto(ctx: Context, cat: Catalog, type_name: str, near_path: str = "") -> str | None:
    if f"dto:{type_name}" in cat.items:
        return f"dto:{type_name}"
    existing = cat.type_keys.get(type_name)
    if existing:
        return existing
    near = ctx.by_path.get(near_path)
    ref = ctx.type(type_name, near)
    if ref is None or ref.decl.kind not in ("class", "record", "interface", "struct", "object") or ref.decl.has("Entity"):
        return None
    fields = jvm.fields_of(ctx, ref)
    if not fields:
        return None
    item = cat.add(Item("shared", "dto", f"dto:{type_name}", type_name, ref.file.path, ref.decl.line, ref.decl.end_line, ref.file.language, "",
                        ref.decl.header.split("{")[0].strip()[:240], {"fields": fields, "declared_as": ref.decl.kind, "annotations": [a.text()[:120] for a in ref.decl.annotations]},
                        code=jvm._class_excerpt(ref.file.text, ref.decl, 80)))
    cat.type_keys.setdefault(type_name, item.key)
    return item.key


def _ant_match(pattern: str, path: str) -> bool:
    if pattern in ("/**", "**"):
        return True
    p = re.sub(r"\{[^}]*\}", "*", pattern)
    path_norm = re.sub(r"\{[^}]*\}", "x", path)
    regex = "^" + re.escape(p).replace(r"/\*\*", "(?:/.*)?").replace(r"\*\*", ".*").replace(r"\*", "[^/]*") + "/?$"
    return bool(re.match(regex, path_norm)) or fnmatch.fnmatch(path_norm, p)


def link(ctx: Context, cat: Catalog) -> None:
    implementations: dict[str, str] = {}
    for key, interface in cat.pending_implements:
        if interface not in cat.type_keys or cat.type_keys[interface] == key:
            implementations.setdefault(interface, key)
        if interface in cat.type_keys and cat.type_keys[interface] != key:
            cat.link(cat.type_keys[interface], key, "implemented by")
    # conventional DTOs (JVM / Swift) even when no endpoint references them
    dto_count = 0
    for name, refs in ctx.types.items():
        if dto_count > 400:
            break
        ref = refs[0]
        if ref.file.is_test or ref.decl.kind not in ("class", "record", "struct") or ref.decl.has("Entity", "Table", "Embeddable", "Service", "Component", "Configuration", "RestController", "Controller"):
            continue
        codable = ref.file.language == "Swift" and ({"Codable", "Decodable", "Encodable"} & set(ref.decl.base_names()))
        serializable = ref.decl.has("Serializable", "JsonClass")
        if DTO_NAME.search(name) and not name.endswith(("ViewModel", "ViewController")) or codable or serializable or re.search(r"(^|/)(dto|dtos|payload|request|response|api/model|network/model|core/model)s?/", ref.file.path):
            if name.endswith(("View",)) and ref.file.language == "Swift" and not codable:
                continue
            if _dto(ctx, cat, name, ref.file.path):
                dto_count += 1
    for key, type_name, method, line in cat.pending_calls:
        target = _target_for(cat, type_name, method, implementations, ctx, key)
        if target and target in cat.items and target != key and cat.items[target].kind not in ("table", "dto", "enum", "exception", "constants", "config", "topic"):
            cat.link(key, target, "calls", f"{type_name}.{method}")
    for key, type_name, role in cat.pending_types:
        if role in ("request", "response"):
            target = _dto(ctx, cat, type_name, cat.items[key].path if key in cat.items else "")
            if target:
                cat.link(key, target, role)
        elif role == "throws":
            target = cat.type_keys.get(type_name)
            if target:
                cat.link(key, target, "throws")
        else:
            target = _pick(ctx, cat, type_name, key, cat.type_keys.get(type_name)) or (implementations.get(type_name))
            if target and target != key:
                cat.link(key, target, "uses")
    views = cat.extra.get("views", {})
    for key, view in cat.pending_views:
        target = views.get(view) or views.get(view.lstrip("/"))
        if target:
            cat.link(key, target, "renders", view)
    index = EndpointIndex(cat)
    for key, path in cat.pending_redirects:
        target = index.match("GET", path)
        if target:
            cat.link(key, target.key, "redirects to", path)
    rendered_by: dict[str, list[Item]] = defaultdict(list)
    for link_ in list(cat.links):
        if link_.kind == "renders" and link_.source in cat.items:
            rendered_by[link_.target].append(cat.items[link_.source])
    for screen_key, _view, method in cat.extra.get("self_posting_views", []):
        for endpoint in rendered_by.get(screen_key, []):
            target = index.match(method or "POST", endpoint.details.get("path", ""))
            if target and target.details.get("method") in (method, "ANY"):
                cat.link(screen_key, target.key, "submits", f"{method} {endpoint.details.get('path')}")
    for screen_key, endpoints in rendered_by.items():
        screen = cat.items.get(screen_key)
        if screen is not None:
            screen.details["rendered_by"] = sorted({e.name for e in endpoints})
            screen.details.setdefault("routes", sorted({e.details.get("path", "") for e in endpoints if e.details.get("method") in ("GET", "ANY")}))
    for key, method, url, line, file in cat.pending_http:
        target = index.match(method, url)
        source = cat.items.get(key)
        if source is None:
            continue
        if target is not None:
            cat.link(key, target.key, "calls", f"{method} {url}")
        client_key = f"client:{method} {url}"
        if client_key in cat.items and client_key != key and source.kind != "api_client":
            cat.link(key, client_key, "calls", f"{method} {url}")
        for call in source.details.get("backend_calls", []):
            if call.get("url") == url and call.get("method") == method and target is not None:
                call["endpoint"] = target.key
    for client in cat.by_kind("api_client", "integration"):
        if client.framework == "Apache Camel" and not re.match(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", client.details.get("base_url") or "localhost"):
            continue  # a Camel route calling another host is a call to another service, never to this repository's own REST endpoint
        target = index.match(client.details.get("method", ""), client.details.get("path", ""))
        if target is not None:
            cat.link(client.key, target.key, "calls", client.name)
            client.details["endpoint"] = target.key
    # topics
    for key, event, relation in cat.pending_topics:
        name = event.get("name", "")
        if not name:
            continue
        topic_key = f"topic:{event.get('kind', 'event')}:{name}"
        if topic_key not in cat.items:
            source = cat.items.get(key)
            cat.add(Item("shared", "topic", topic_key, name, source.path if source else "", event.get("line", 0), event.get("line", 0), "", event.get("kind", "event"),
                         f"{event.get('kind', 'event')} {name}", {"transport": event.get("kind", "event"), "name": name}))
        if relation == "publishes":
            cat.link(key, topic_key, "publishes")
        else:
            cat.link(topic_key, key, "delivers to")
    # data access
    for key, table_key, ops, detail in cat.pending_tables:
        target = f"table:{table_key}"
        if target not in cat.items:
            alt = f"view:{table_key}"
            if alt in cat.items:
                target = alt
            else:
                continue
        cat.link(key, target, "accesses", ops)
    for key, procedure in cat.pending_routines:
        target = f"routine:{procedure.split('.')[-1].lower()}"
        if target in cat.items:
            cat.link(key, target, "calls procedure", procedure)
    # screen -> screen navigation
    by_route: dict[str, str] = {}
    for screen in cat.by_kind("screen"):
        for route in screen.details.get("routes", []) or []:
            by_route.setdefault(normalise_path(route), screen.key)
    for key, target, relation in cat.pending_screens:
        target_key = cat.type_keys.get(target)
        if target_key is None and target.startswith("/"):
            target_key = by_route.get(normalise_path(target))
        if target_key and target_key in cat.items and cat.items[target_key].kind == "screen":
            cat.link(key, target_key, relation, target)
    for screen in cat.by_kind("screen", "component"):
        if screen.details.get("backend_calls"):
            continue
        calls = []
        for link_ in cat.links:
            if link_.source == screen.key and link_.kind in ("calls", "submits") and link_.target in cat.items and cat.items[link_.target].kind == "endpoint":
                endpoint = cat.items[link_.target]
                calls.append({"method": endpoint.details.get("method", ""), "url": endpoint.details.get("path", ""), "via": "form submit" if link_.kind == "submits" else "link / form action",
                              "endpoint": endpoint.key})
        if calls:
            screen.details["backend_calls"] = calls
    _field_validations(cat)
    _security(cat)
    _mobile_backend(cat)
    _crud(cat)
    _precise_crud(cat)
    _consumers(cat)


def _field_validations(cat: Catalog) -> None:
    """Screen fields inherit the server-side rules of the request type they are submitted to."""
    for link_ in cat.links:
        if link_.kind not in ("submits", "calls"):
            continue
        screen, endpoint = cat.items.get(link_.source), cat.items.get(link_.target)
        if screen is None or endpoint is None or screen.kind != "screen" or endpoint.kind != "endpoint":
            continue
        request = endpoint.details.get("request") or {}
        server = {f["name"]: f for f in request.get("fields", [])}
        for field in screen.details.get("fields", []):
            rule = server.get(str(field.get("name", "")).split(".")[-1])
            if rule:
                field["server_validations"] = rule.get("validations", [])
                field["required"] = field.get("required") or rule.get("required", False)
                field["bound_to"] = f"{endpoint.name} → {request.get('name', '')}.{rule['name']}"


def _security(cat: Catalog) -> None:
    """Each security configuration contributes its first matching rule (configurations are often profile-specific)."""
    for key in cat.security_items:
        sec = cat.items.get(key)
        if sec is None:
            continue
        rules = sec.details.get("rules", [])
        for endpoint in cat.by_kind("endpoint"):
            path, verb = endpoint.details.get("path", ""), endpoint.details.get("method", "")
            for rule in rules:
                if rule.get("method") and verb not in (rule["method"], "ANY"):
                    continue
                if any(_ant_match(pattern, path) for pattern in rule.get("patterns", [])):
                    security = endpoint.details.setdefault("security", {})
                    entry = {"access": rule["access"], "roles": rule.get("roles", []), "pattern": ", ".join(rule.get("patterns", [])), "config": sec.name, "line": rule.get("line", 0)}
                    security.setdefault("http_rules", []).append(entry)
                    security.setdefault("http_rule", entry)
                    cat.link(key, endpoint.key, "protects", f"{rule['access']}{('(' + ', '.join(rule.get('roles', [])) + ')') if rule.get('roles') else ''}")
                    break


def _mobile_backend(cat: Catalog) -> None:
    """Screens that reach API clients through view models / repositories get those calls listed."""
    out_links: dict[str, list] = defaultdict(list)
    for link_ in cat.links:
        out_links[link_.source].append(link_)
    for screen in cat.by_kind("screen"):
        if screen.details.get("platform") not in ("Android", "iOS"):
            continue
        found = []
        seen = {screen.key}
        queue = deque([(screen.key, [])])
        while queue:
            current, chain = queue.popleft()
            if len(chain) > 4:
                continue
            for link_ in out_links.get(current, []):
                if link_.target in seen or link_.kind not in ("uses", "calls", "implemented by"):
                    continue
                seen.add(link_.target)
                target = cat.items.get(link_.target)
                if target is None:
                    continue
                if target.kind in ("api_client", "endpoint", "integration"):
                    found.append({"method": target.details.get("method", ""), "url": target.details.get("path", ""), "via": " → ".join(chain + [target.details.get("operation") or target.name]),
                                  "client": target.key, "endpoint": target.details.get("endpoint", target.key if target.kind == "endpoint" else "")})
                elif target.kind in ("view_model", "service", "repository", "query"):
                    queue.append((target.key, chain + [target.name]))
        if found:
            screen.details["backend_calls"] = found[:40]


def _crud(cat: Catalog) -> None:
    incoming: dict[str, list] = defaultdict(list)
    for link_ in cat.links:
        incoming[link_.target].append(link_)
    for table in cat.by_kind("table", "view"):
        matrix: dict[str, set[str]] = defaultdict(set)
        accessors = []
        for link_ in incoming.get(table.key, []):
            if link_.kind != "accesses":
                continue
            ops = [op for op in link_.detail.split("/") if op in "CRUD" and op]
            source = cat.items.get(link_.source)
            if source is None:
                continue
            accessors.append({"key": source.key, "name": source.name, "kind": source.kind, "ops": ops})
            # who reaches this accessor: repository -> service / endpoint / job (2 hops up)
            reached = {source.key}
            frontier = [source.key]
            upstream = []
            for _ in range(6):
                nxt = []
                for node in frontier:
                    for up in incoming.get(node, []):
                        if up.kind in ("calls", "declares", "runs", "member of", "implemented by") and up.source not in reached:
                            reached.add(up.source)
                            item = cat.items.get(up.source)
                            if item is not None and item.kind in ("service", "endpoint", "job", "listener", "view_model", "screen", "repository"):
                                upstream.append(item)
                            nxt.append(up.source)
                frontier = nxt
            if source.kind == "repository" or source.kind == "query":
                repo_ops = ops
                for item in upstream:
                    called = [l_.detail.split(".")[-1] for l_ in cat.links if l_.source == item.key and l_.target in (source.key,) and l_.kind == "calls"]
                    item_ops = sorted({op for name in called for op in sqlmod.crud_of_method(name)} or set(repo_ops), key="CRUD".index) if called else repo_ops
                    for op in item_ops:
                        matrix[op].add(f"{item.kind}:{item.name}")
            for op in ops:
                matrix[op].add(f"{source.kind}:{source.name}")
        # repository-level calls (save / findById ...) recorded as calls to the repository item
        for repo_link in incoming.get(table.key, []):
            pass
        table.details["crud"] = {op: sorted(names)[:40] for op, names in sorted(matrix.items(), key=lambda kv: "CRUD".index(kv[0]))}
        table.details["accessed_by"] = accessors[:60]
    for repo in cat.by_kind("repository"):
        table_key = repo.details.get("table")
        if not table_key or f"table:{table_key}" not in cat.items:
            continue
        table = cat.items[f"table:{table_key}"]
        for link_ in incoming.get(repo.key, []):
            if link_.kind != "calls":
                continue
            ops = sqlmod.crud_of_method(link_.detail.split(".")[-1])
            caller = cat.items.get(link_.source)
            if caller is None:
                continue
            crud = table.details.setdefault("crud", {})
            for op in ops:
                names = set(crud.get(op, []))
                names.add(f"{caller.kind}:{caller.name}")
                crud[op] = sorted(names)[:40]
        table.details["crud"] = {op: table.details["crud"][op] for op in "CRUD" if op in table.details.get("crud", {})}


def _precise_crud(cat: Catalog) -> None:
    """CRUD per actor by following method-level calls: endpoint -> service method (-> implementation) -> repository
    method / query -> tables. Replaces the class-level approximation whenever the chain can be followed."""
    queries: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for query in cat.by_kind("query"):
        owner, method = query.details.get("repository", ""), query.details.get("method", "")
        if owner and method:
            queries[(owner, method)].append(query)
    method_calls: dict[tuple[str, str], list[str]] = {}
    classes: dict[str, Item] = {}
    for item in cat.by_kind("service", "repository", "view_model", "job"):
        name = item.details.get("class") or item.name
        classes.setdefault(name, item)
        for method, calls in (item.details.get("method_calls") or {}).items():
            method_calls[(name, method)] = calls
        for entry in item.details.get("methods", []):
            if entry.get("calls") and (name, entry["name"]) not in method_calls:
                method_calls[(name, entry["name"])] = entry["calls"]
    impls: dict[str, list[str]] = defaultdict(list)
    for key, interface in cat.pending_implements:
        item = cat.items.get(key)
        if item is not None:
            impls[interface].append(item.details.get("class") or item.name)
    memo: dict[tuple[str, str], set[tuple[str, str]]] = {}

    def ops_for(type_name: str, method: str, depth: int, trail: frozenset) -> set[tuple[str, str]]:
        key = (type_name, method)
        if key in memo:
            return memo[key]
        if depth > 8 or key in trail:
            return set()
        trail = trail | {key}
        out: set[tuple[str, str]] = set()
        for query in queries.get(key, []):
            for table, ops in (query.details.get("tables") or {}).items():
                out |= {(table, op) for op in ops if op in "CRUD"}
        repo = classes.get(type_name)
        if not queries.get(key) and repo is not None and repo.kind == "repository" and repo.details.get("table") and not impls.get(type_name):
            out |= {(repo.details["table"], op) for op in sqlmod.crud_of_method(method)}
        for call in method_calls.get(key, []):
            target_type, _, target_method = call.rpartition(".")
            if target_type and target_method:
                out |= ops_for(target_type, target_method, depth + 1, trail)
        for impl in impls.get(type_name, []):
            out |= ops_for(impl, method, depth + 1, trail)
        memo[key] = out
        return out

    matrix: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for endpoint in cat.by_kind("endpoint"):
        for call in endpoint.details.get("calls", []):
            for table, op in ops_for(call["type"], call["method"], 0, frozenset()):
                matrix[table][op].add(f"endpoint:{endpoint.name}")
    for item in cat.by_kind("job", "listener"):
        for call in item.details.get("calls", []):
            target_type, _, target_method = call.rpartition(".")
            for table, op in ops_for(target_type, target_method, 0, frozenset()):
                matrix[table][op].add(f"{item.kind}:{item.name}")
    for item in cat.by_kind("service", "view_model"):
        name = item.details.get("class") or item.name
        for entry in item.details.get("methods", []):
            for table, op in ops_for(name, entry["name"], 0, frozenset()):
                matrix[table][op].add(f"{item.kind}:{item.name}")
    for table_key, ops in matrix.items():
        table = cat.items.get(f"table:{table_key}")
        if table is None:
            continue
        crud = {op: set(names) for op, names in (table.details.get("crud") or {}).items()}
        # replace class-level guesses for actors the precise walk could follow; keep repository / query entries
        precise_actors = {name for names in ops.values() for name in names}
        for op in list(crud):
            crud[op] = {n for n in crud[op] if not n.startswith(("endpoint:", "service:", "job:", "listener:", "view_model:")) or n in precise_actors and n in ops.get(op, set())}
        for op, names in ops.items():
            crud.setdefault(op, set()).update(names)
        table.details["crud"] = {op: sorted(crud[op])[:60] for op in "CRUD" if crud.get(op)}


def _consumers(cat: Catalog) -> None:
    """Endpoints learn which screens / clients call them; services learn which endpoints use them."""
    incoming: dict[str, list] = defaultdict(list)
    for link_ in cat.links:
        incoming[link_.target].append(link_)
    for item in cat.by_kind("endpoint", "service", "repository", "dto", "job", "api_client", "config", "exception"):
        users = []
        seen_users = set()
        for link_ in incoming.get(item.key, []):
            source = cat.items.get(link_.source)
            if source is not None and (source.key, link_.kind) not in seen_users:
                seen_users.add((source.key, link_.kind))
                users.append({"key": source.key, "name": source.name, "kind": source.kind, "relation": link_.kind})
        if users:
            item.details["used_by"] = users[:60] if item.kind != "config" else item.details.get("used_by", [])
            if item.kind == "config":
                item.details["used_by_items"] = users[:30]


__all__ = ["link", "EndpointIndex", "snippet", "simple_type"]
