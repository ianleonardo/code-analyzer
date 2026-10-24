"""C4 diagrams in Mermaid syntax, built deterministically from the graph (no LLM guessing of structure).

L1 Context   - the system, its users and external systems (databases, brokers, external APIs)
L2 Container - deployable units / workspace packages and data stores, with relations from cross-container edges
L3 Component - modules (top folders) inside each container, with aggregated dependency relations
L4 Dynamic   - the dominant request path from an entry point down through the layers
plus a layer flowchart (api → service → data) used by the topology view.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath

LAYER_ORDER = ("ui", "api", "service", "data", "shared")


def _id(value: str, prefix: str = "n") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if len(cleaned) > 40:
        digest = hashlib.sha1(value.encode()).hexdigest()[:8]
        cleaned = f"{cleaned[-30:]}_{digest}"
    return f"{prefix}_{cleaned}"


def _label(value: str, limit: int = 80) -> str:
    """Mermaid-safe label: no quotes, brackets or line breaks."""
    text = re.sub(r"[\"`<>{}\[\]\n\r]+", " ", str(value or "")).strip()
    text = text.replace(";", ",")
    return (text[: limit - 1] + "…") if len(text) > limit else text


def fix_mermaid(source: str) -> str:
    """Deterministic repair of common grammar problems (unbalanced quotes, stray characters, empty labels)."""
    fixed_lines = []
    for line in source.splitlines():
        if line.count('"') % 2:
            line = line.replace('"', "'")
        line = re.sub(r"\(\s*,", "(", line)
        fixed_lines.append(line.rstrip())
    return "\n".join(fixed_lines)


def _module_of(path: str, container_path: str) -> str:
    rel = path[len(container_path) + 1 :] if container_path and path.startswith(container_path + "/") else path
    parts = PurePosixPath(rel).parts
    if len(parts) <= 1:
        return "(root)"
    skip = {"src", "main", "java", "kotlin", "app", "lib", "pkg", "internal", "source", "sources"}
    picked = []
    for part in parts[:-1]:
        if part.lower() in skip and not picked:
            continue
        picked.append(part)
        if len(picked) == 2 or (len(picked) == 1 and part.lower() not in {"com", "org", "net", "io"}):
            break
    return "/".join(picked) or parts[0]


def build(repo_name: str, summary: str, macro, files: list[dict], edges: list, containers_by_file: dict[str, str]) -> dict:
    by_path = {item["path"]: item for item in files}
    containers = [c for c in macro.containers if c.kind != "aggregator"] or macro.containers
    externals = macro.external_systems[:12]

    # ----- L1 Context -----
    has_ui = any(item["layer"] == "ui" for item in files if item["is_source"])
    has_api = any(item["layer"] == "api" for item in files if item["is_source"])
    ctx = ["C4Context", f"  title System Context - {_label(repo_name)}"]
    if has_ui or has_api:
        ctx.append('  Person(user, "User", "Uses the system through its UI or API")')
    ctx.append(f'  System(system, "{_label(repo_name)}", "{_label(summary or macro.architecture_style, 120)}")')
    for ext in externals:
        kind = "SystemDb_Ext" if ext["type"] in ("database", "cache", "storage", "search") else ("SystemQueue_Ext" if ext["type"] == "messaging" else "System_Ext")
        ctx.append(f'  {kind}({_id(ext["name"], "ext")}, "{_label(ext["name"])}", "{_label(ext["type"])}")')
    if has_ui or has_api:
        ctx.append('  Rel(user, system, "Uses", "HTTPS")')
    for ext in externals:
        verb = {"database": "Reads/writes", "cache": "Caches in", "messaging": "Publishes/consumes", "external api": "Calls"}.get(ext["type"], "Uses")
        ctx.append(f'  Rel(system, {_id(ext["name"], "ext")}, "{verb}")')
    context = "\n".join(ctx)

    # ----- L2 Container -----
    cross: Counter = Counter()
    for edge in edges:
        a, b = containers_by_file.get(edge.source), containers_by_file.get(edge.target)
        if a is not None and b is not None and a != b:
            cross[(a, b)] += 1
    con = ["C4Container", f"  title Containers - {_label(repo_name)}"]
    if has_ui or has_api:
        con.append('  Person(user, "User")')
    con.append(f'  System_Boundary(sys, "{_label(repo_name)}") {{')
    for container in containers:
        tech = ", ".join(fw["name"] for fw in container.frameworks if fw["category"] in ("web", "ui", "cli", "orm"))[:60] or container.ecosystem
        description = container.description or container.kind
        con.append(f'    Container({_id(container.path or "root", "c")}, "{_label(container.name)}", "{_label(tech)}", "{_label(description, 100)}")')
    con.append("  }")
    for ext in externals:
        kind = "ContainerDb_Ext" if ext["type"] in ("database", "cache", "storage", "search") else ("ContainerQueue_Ext" if ext["type"] == "messaging" else "System_Ext")
        con.append(f'  {kind}({_id(ext["name"], "ext")}, "{_label(ext["name"])}", "{_label(ext["type"])}")')
    front = [c for c in containers if c.kind in ("frontend", "web application")]
    for container in front or containers[:1]:
        if has_ui or has_api:
            con.append(f'  Rel(user, {_id(container.path or "root", "c")}, "Uses", "HTTPS")')
    for (a, b), count in cross.most_common(20):
        con.append(f'  Rel({_id(a or "root", "c")}, {_id(b or "root", "c")}, "Depends on", "{count} edges")')
    if front and len(containers) > 1 and not cross:
        backends = [c for c in containers if c.kind == "service"]
        for f in front:
            for b in backends[:3]:
                con.append(f'  Rel({_id(f.path or "root", "c")}, {_id(b.path or "root", "c")}, "Calls API", "HTTP/JSON")')
    for ext in externals:
        users = _containers_using(ext, containers)
        for container in users[:3]:
            con.append(f'  Rel({_id(container.path or "root", "c")}, {_id(ext["name"], "ext")}, "Uses")')
    container_diagram = "\n".join(con)

    # ----- L3 Components (per container) -----
    components: dict[str, str] = {}
    for container in containers:
        prefix = container.path
        members = [item for item in files if containers_by_file.get(item["path"]) == prefix and item["is_source"] and not item["is_test"] and item["category"] == "code"]
        if not members:
            continue
        modules: dict[str, list[dict]] = defaultdict(list)
        for item in members:
            modules[_module_of(item["path"], prefix)].append(item)
        ranked = sorted(modules.items(), key=lambda kv: -sum(f["pagerank"] for f in kv[1]))[:18]
        keep = {name for name, _ in ranked}
        lines = ["C4Component", f"  title Components - {_label(container.name)}", f'  Container_Boundary(b, "{_label(container.name)}") {{']
        for name, items in ranked:
            layer = Counter(f["layer"] for f in items).most_common(1)[0][0]
            roles = ", ".join(role for role, _ in Counter(f["role"] for f in items).most_common(2))
            lines.append(f'    Component({_id(name, "m")}, "{_label(name, 50)}", "{layer}", "{_label(f"{len(items)} files - {roles}", 90)}")')
        lines.append("  }")
        module_edges: Counter = Counter()
        for edge in edges:
            if containers_by_file.get(edge.source) != prefix or containers_by_file.get(edge.target) != prefix:
                continue
            a, b = _module_of(edge.source, prefix), _module_of(edge.target, prefix)
            if a != b and a in keep and b in keep:
                module_edges[(a, b)] += 1
        for (a, b), count in module_edges.most_common(30):
            lines.append(f'  Rel({_id(a, "m")}, {_id(b, "m")}, "uses", "{count}")')
        components[container.name] = "\n".join(lines)

    # ----- L4 Dynamic: dominant path from an API/UI entry through the layers -----
    adjacency: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for edge in edges:
        if edge.kind in ("IMPORTS", "CALLS", "INJECTS"):
            adjacency[edge.source].append((edge.target, edge.weight))
    starts = sorted((item for item in files if item["layer"] in ("api", "ui") and item["is_source"] and not item["is_test"] and adjacency.get(item["path"])), key=lambda f: -(f["fan_out"] + f["complexity"] / 10))
    dynamic = ""
    if starts:
        path = [starts[0]["path"]]
        rank = {"ui": 0, "api": 1, "service": 2, "data": 3, "shared": 4}
        current = path[0]
        for _ in range(6):
            options = [(t, w) for t, w in adjacency.get(current, []) if t not in path and t in by_path and not by_path[t]["is_test"]]
            if not options:
                break
            here = rank.get(by_path[current]["layer"], 4)
            options.sort(key=lambda tw: (rank.get(by_path[tw[0]]["layer"], 4) < here, -tw[1], -by_path[tw[0]]["pagerank"]))
            nxt = options[0][0]
            path.append(nxt)
            current = nxt
        if len(path) > 1:
            lines = ["C4Dynamic", "  title Dominant request path"]
            if by_path[path[0]]["layer"] in ("ui", "api"):
                lines.append('  Person(user, "User")')
            for item in path:
                info = by_path[item]
                lines.append(f'  Component({_id(item, "f")}, "{_label(PurePosixPath(item).name, 40)}", "{info["layer"]}", "{_label(info["role"], 60)}")')
            if by_path[path[0]]["layer"] in ("ui", "api"):
                lines.append(f'  Rel(user, {_id(path[0], "f")}, "Request")')
            for a, b in zip(path, path[1:], strict=False):
                lines.append(f'  Rel({_id(a, "f")}, {_id(b, "f")}, "calls")')
            dynamic = "\n".join(lines)

    # ----- Layer flow (flowchart) -----
    layer_counts = Counter(item["layer"] for item in files if item["is_source"] and not item["is_test"])
    layer_edges: Counter = Counter()
    for edge in edges:
        a, b = by_path.get(edge.source), by_path.get(edge.target)
        if a and b and a["layer"] != b["layer"] and not a["is_test"] and not b["is_test"]:
            layer_edges[(a["layer"], b["layer"])] += 1
    flow = ["flowchart TB"]
    for layer in LAYER_ORDER:
        if layer_counts.get(layer):
            flow.append(f'  {layer}["{layer.upper()} · {layer_counts[layer]} files"]')
    for (a, b), count in layer_edges.most_common(20):
        if layer_counts.get(a) and layer_counts.get(b):
            flow.append(f"  {a} -->|{count}| {b}")
    for layer in LAYER_ORDER:
        if layer_counts.get(layer):
            flow.append(f"  class {layer} layer_{layer}")
    layers = "\n".join(flow)

    return {
        "context": fix_mermaid(context),
        "container": fix_mermaid(container_diagram),
        "components": {name: fix_mermaid(text) for name, text in components.items()},
        "dynamic": fix_mermaid(dynamic),
        "layers": layers,
        "containers": [{"name": c.name, "path": c.path} for c in containers],
    }


def _containers_using(ext: dict, containers) -> list:
    hits = []
    for container in containers:
        if any(fw["name"] == ext["name"] for fw in container.frameworks):
            hits.append(container)
    return hits or (containers[:1] if ext["type"] != "external api" else [])
