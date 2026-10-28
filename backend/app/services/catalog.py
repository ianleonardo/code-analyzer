"""Layer catalogue service: build (from the pipeline or offline from stored files), persist, read, trace, ERD, CRUD
matrix, export (Markdown / Excel) and comparison between two analyses.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict, deque

from app import db
from app.errors import AppError
from app.permissions import require
from app.services import llm, llm_settings
from app.services.analyzer import catalog as catalog_engine
from app.services.analyzer.catalog import enrich
from app.services.analyzer.catalog.model import Catalog, Item, SourceFile
from app.services.analyzer.languages import classify_path

LAYERS = ("api", "service", "data", "ui", "shared")
LAYER_LABELS = {"api": "API", "service": "Service", "data": "Data", "ui": "UI", "shared": "Shared"}
KIND_LABELS = {
    "endpoint": "Endpoints", "transaction": "Transactions", "service": "Services", "program": "Programs", "job": "Scheduled / batch jobs", "listener": "Listeners",
    "database": "Databases", "table": "Tables", "view": "Views", "file": "Files / datasets", "repository": "Repositories / DAOs", "query": "Queries",
    "procedure": "Procedures / packages", "trigger": "Triggers", "screen": "Screens", "component": "Template fragments", "view_model": "View models",
    "api_client": "API clients", "dto": "DTOs / types", "copybook": "Copybooks", "enum": "Enums", "exception": "Exceptions", "config": "Configuration keys",
    "config_class": "Configuration classes", "security": "Security", "integration": "Outbound integrations", "constants": "Constants", "utility": "Utilities",
    "bean_config": "Bean configuration", "manifest": "App manifests", "topic": "Topics / events", "mapper": "Mappers",
}
TRACE_KINDS = {"calls", "submits", "renders", "uses", "accesses", "declares", "runs", "implemented by", "calls procedure", "publishes", "delivers to", "member of", "redirects to", "starts"}


# --- build ----------------------------------------------------------------------------------------------------------


def from_pipeline(files: dict[str, dict], macro_result, graph_result, log=lambda _m: None) -> Catalog:
    sources = [
        SourceFile(f["path"], f["language"], f["category"], f["layer"], f["role"], bool(f["is_test"]), f["container"], f["text"])
        for f in files.values() if f.get("text") is not None and f["category"] in ("code", "markup", "script", "config", "doc", "style") and not f.get("is_generated")
    ]
    dependencies = [dict(d, container=c.name, manifest=c.manifest, ecosystem=c.ecosystem) for c in macro_result.containers for d in c.dependencies]
    edges = [(e.source, e.target, e.kind) for e in graph_result.edges]
    return catalog_engine.build(sources, dependencies, edges, [fw["name"] for fw in macro_result.frameworks], log)


def enrichment_cache(repo_id: str) -> dict[str, dict]:
    rows = db.fetchall(
        """
        SELECT c.content_hash, c.summary, c.details_json FROM analysis_catalog c JOIN analyses a ON a.analysis_id = c.analysis_id
        WHERE a.repo_id = ? AND c.summary_source = 'llm' AND c.content_hash <> ''
        """,
        [repo_id],
    )
    out = {}
    for row in rows:
        try:
            rules = json.loads(row["details_json"] or "{}").get("llm_rules", [])
        except ValueError:
            rules = []
        out[row["content_hash"]] = {"summary": row["summary"], "rules": rules}
    return out


def enrich_catalog(cat: Catalog, repo_id: str, log=lambda _m: None) -> int:
    limit = int(llm_settings.settings_row().get("llm_catalog_limit") or 0) if llm.configured() else 0
    return enrich.enrich(cat, limit, enrichment_cache(repo_id), log)


def chunk_rows(cat: Catalog) -> list[dict]:
    """Catalogue facts as retrievable chunks so Chat with Analyst can answer 'which API writes the OWNERS table?'."""
    rows = []
    for item in cat.items.values():
        if item.kind in ("config",) and not item.details.get("used_by"):
            continue
        text = describe(item, [(lk.kind, lk.target) for lk in cat.links if lk.source == item.key][:25], [(lk.kind, lk.source) for lk in cat.links if lk.target == item.key][:25])
        rows.append({"chunk_id": str(uuid.uuid4()), "path": item.path or f"catalog/{item.layer}", "scope": f"{LAYER_LABELS[item.layer]} {item.kind}: {item.name}"[:1000],
                     "kind": "catalog", "line_start": item.line_start, "line_end": item.line_end, "content": text[:6000],
                     "content_hash": item.content_hash(), "embedding": None, "priority": 0.8 if item.kind in ("endpoint", "screen", "table", "service") else 0.3})
    return rows


def describe(item: Item, out_links: list[tuple[str, str]], in_links: list[tuple[str, str]]) -> str:
    d = item.details
    lines = [f"[{LAYER_LABELS[item.layer]} layer · {item.kind}] {item.name}", f"Location: {item.path}:{item.line_start}", f"Signature: {item.signature}"]
    if item.summary:
        lines.append(f"Purpose: {item.summary}")
    if item.kind == "endpoint":
        params = ", ".join(f"{p.get('in')}:{p.get('name')}{'*' if p.get('required') else ''}" for p in d.get("params", []) if p.get("in") != "context")
        lines.append(f"Parameters: {params or 'none'}")
        request = d.get("request") or {}
        if request.get("fields"):
            lines.append(f"Request body {request.get('name', '')}: " + "; ".join(f"{f['name']} {f.get('type', '')}{' required' if f.get('required') else ''} {', '.join(f.get('validations', []))}".strip() for f in request["fields"][:30]))
        response = d.get("response") or {}
        lines.append(f"Response: {response.get('type', '')} {' views ' + ', '.join(response.get('views', [])) if response.get('views') else ''} status {d.get('status_codes')}")
        security = d.get("security") or {}
        if security:
            lines.append(f"Security: {json.dumps(security, default=str)[:300]}")
    if d.get("rules"):
        lines.append("Rules: " + " | ".join(f"if {r.get('condition')} then {r.get('outcome')} (line {r.get('line')})" for r in d["rules"][:12]))
    if d.get("llm_rules"):
        lines.append("Business rules: " + " | ".join(f"{r['rule']} (line {r.get('line') or '?'})" for r in d["llm_rules"]))
    if item.kind in ("table",):
        lines.append("Columns: " + ", ".join(f"{c['name']} {c.get('type') or c.get('java_type', '')}{' PK' if c.get('pk') else ''}{' NOT NULL' if not c.get('nullable', True) else ''}" for c in d.get("columns", [])[:60]))
        if d.get("crud"):
            lines.append("CRUD: " + "; ".join(f"{op}: {', '.join(names[:12])}" for op, names in d["crud"].items()))
    if item.kind == "query":
        lines.append(f"SQL ({d.get('dialect')}): {d.get('sql', '')[:1500]}")
    if item.kind in ("screen",):
        lines.append("Fields: " + "; ".join(f"{f.get('label') or f.get('name')} ({f.get('type')}){' required' if f.get('required') else ''}" for f in d.get("fields", [])[:30]))
        lines.append("Actions: " + ", ".join(a.get("label", "") for a in d.get("actions", [])[:20]))
        if d.get("backend_calls"):
            lines.append("Backend calls: " + ", ".join(f"{c.get('method')} {c.get('url')}" for c in d["backend_calls"][:20]))
        if d.get("routes"):
            lines.append("Routes: " + ", ".join(d["routes"]))
    if item.kind in ("service", "view_model", "repository"):
        lines.append("Methods: " + "; ".join(m.get("signature", m.get("name", "")) for m in d.get("methods", [])[:30]))
    if item.kind == "job":
        lines.append(f"Schedule: {d.get('human') or json.dumps(d.get('schedule', {}))}")
        if d.get("steps"):
            lines.append("Steps: " + "; ".join(f"{s.get('name')} runs {s.get('program') or s.get('proc')}" + (f" (DDs {', '.join(dd.get('name', '') + '=' + dd.get('dsn', '') for dd in s.get('dds', []) if dd.get('dsn'))})" if s.get("dds") else "")
                                               for s in d["steps"][:20]))
    if item.kind == "program":
        lines.append(f"{d.get('dialect')} program, {d.get('role')}; entry parameters: {', '.join(d.get('entry', [])) or 'none'}; {d.get('loc')} lines; complexity {d.get('complexity')}")
        if d.get("calls"):
            lines.append("Calls programs: " + ", ".join(f"{c.get('target') or c.get('variable')} ({c.get('via')})" for c in d["calls"][:25]))
        if d.get("files"):
            lines.append("Files: " + "; ".join(f"{f.get('name')} -> {f.get('target') or f.get('external')} {''.join(f.get('ops', []))}" for f in d["files"][:20]))
        if d.get("sql"):
            lines.append("SQL: " + "; ".join(f"{s.get('operation')} {', '.join(s.get('tables', {}))}" for s in d["sql"][:20]))
        if d.get("units"):
            lines.append("Paragraphs / procedures: " + ", ".join(u["name"] for u in d["units"][:60]))
    if item.kind == "transaction":
        lines.append(f"CICS transaction {d.get('transid')} runs program {d.get('program')} ({d.get('source')}){' — ' + d['description'] if d.get('description') else ''}")
    if item.kind == "copybook":
        if d.get("records"):
            lines.append("Record layout: " + "; ".join(f"{r.get('level')} {r.get('name')} {r.get('pic', '')}".strip() for r in d["records"][:40]))
        if d.get("dclgen"):
            lines.append("DCLGEN for DB2 table(s): " + ", ".join(t["table"] for t in d["dclgen"]))
    if item.kind == "file":
        lines.append(f"{d.get('organization')} {item.name}; DD names {', '.join(d.get('ddnames', []))}; accessed by " + ", ".join(f"{a.get('name')} ({''.join(a.get('ops', []))})" for a in d.get("accessed_by", [])[:20]))
    if item.kind == "config":
        lines.append("Values: " + "; ".join(f"{v['profile']}={v['value']}" for v in d.get("values", [])[:8]))
    if item.kind == "database":
        lines.append(f"Engine {d.get('engine')} ({d.get('category')}); ORM {', '.join(d.get('orm', []))}; connections " + "; ".join(f"{c['profile']}: {c['url']}" for c in d.get("connections", [])[:5]))
    if out_links:
        lines.append("Links to: " + ", ".join(f"{k} {t}" for k, t in out_links))
    if in_links:
        lines.append("Linked from: " + ", ".join(f"{k} {s}" for k, s in in_links))
    return "\n".join(lines)


def persist(analysis_id: str, cat: Catalog) -> None:
    db.exec_sql("DELETE FROM analysis_catalog WHERE analysis_id = ?", [analysis_id])
    db.exec_sql("DELETE FROM analysis_catalog_links WHERE analysis_id = ?", [analysis_id])
    rows = []
    for item in cat.items.values():
        rows.append((str(uuid.uuid4()), analysis_id, item.layer, item.kind, item.key[:1000], item.name[:1000], item.path[:1000], item.line_start, item.line_end,
                     item.language[:40], item.framework[:80], item.signature[:4000], json.dumps(item.details, default=str).replace("\x00", ""), item.summary[:4000],
                     item.summary_source[:20], item.content_hash()))
    db.insert_many("analysis_catalog", ["item_id", "analysis_id", "layer", "kind", "item_key", "name", "path", "line_start", "line_end", "language", "framework",
                                        "signature", "details_json", "summary", "summary_source", "content_hash"], rows, chunk=300)
    db.insert_many("analysis_catalog_links", ["link_id", "analysis_id", "source_key", "target_key", "kind", "detail"],
                   [(str(uuid.uuid4()), analysis_id, lk.source[:1000], lk.target[:1000], lk.kind[:40], lk.detail[:500]) for lk in cat.links], chunk=1000)


CATALOG_VERSION = 3


def _set_stats(analysis_id: str, cat: Catalog, produced: int) -> None:
    row = db.fetchone("SELECT stats_json FROM analyses WHERE analysis_id = ?", [analysis_id])
    try:
        stats = json.loads(row["stats_json"] or "{}") if row else {}
    except ValueError:
        stats = {}
    stats["catalog"] = {**cat.extra.get("stats", {}), "llm_explained": produced, "version": CATALOG_VERSION}
    db.exec_sql("UPDATE analyses SET stats_json = ? WHERE analysis_id = ?", [json.dumps(stats, default=str), analysis_id])


def _rebuild_analysis(analysis: dict) -> None:
    analysis_id = analysis["analysis_id"]
    rows = db.fetchall(
        """
        SELECT f.path, f.language, f.layer, f.role, f.is_test, f.is_generated, f.container, c.content
        FROM analysis_files f JOIN analysis_file_contents c ON c.file_id = f.file_id
        WHERE f.analysis_id = ?
        """,
        [analysis_id],
    )
    files = []
    for row in rows:
        if row["is_generated"]:
            continue
        lang, category = classify_path(row["path"])
        files.append(SourceFile(row["path"], row["language"] or lang, category, row["layer"], row["role"], bool(row["is_test"]), row["container"] or "", row["content"] or ""))
    dependencies = db.fetchall("SELECT name, version, scope, ecosystem, container, manifest FROM analysis_dependencies WHERE analysis_id = ?", [analysis_id])
    edges = [(r["source_path"], r["target_path"], r["kind"]) for r in db.fetchall("SELECT source_path, target_path, kind FROM analysis_edges WHERE analysis_id = ?", [analysis_id])]
    macro = _loads(analysis.get("macro_json"), {})
    cat = catalog_engine.build(files, dependencies, edges, [fw.get("name", "") for fw in macro.get("frameworks", [])])
    produced = enrich_catalog(cat, analysis["repo_id"])
    persist(analysis_id, cat)
    chunks = chunk_rows(cat)
    db.exec_sql("DELETE FROM analysis_chunks WHERE analysis_id = ? AND kind = 'catalog'", [analysis_id])
    db.insert_many("analysis_chunks", ["chunk_id", "analysis_id", "path", "scope", "kind", "line_start", "line_end", "content", "content_hash", "embedding"],
                   [(r["chunk_id"], analysis_id, r["path"], r["scope"], r["kind"], r["line_start"], r["line_end"], r["content"], r["content_hash"], None) for r in chunks], chunk=200)
    from app.services import retrieval

    retrieval.forget(analysis_id)
    _set_stats(analysis_id, cat, produced)


def rebuild(user: dict, analysis_id: str) -> dict:
    """Rebuilds the catalogue offline from the files stored with the analysis (no clone needed)."""
    require(user, "repositories", "write")
    analysis = _completed(analysis_id)
    _rebuild_analysis(analysis)
    return summary(user, analysis_id, _rebuilt=True)


# --- reads ----------------------------------------------------------------------------------------------------------


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _completed(analysis_id: str) -> dict:
    row = db.fetchone("SELECT * FROM analyses WHERE analysis_id = ?", [analysis_id])
    if row is None:
        raise AppError(404, "Analysis not found")
    if row["status"] != "completed":
        raise AppError(409, f"The analysis is {row['status']}; results are available once it completes")
    return row


def summary(user: dict, analysis_id: str, *, _rebuilt: bool = False) -> dict:
    require(user, "repositories", "read")
    analysis = _completed(analysis_id)
    if not _rebuilt:
        cat_stats = _loads(analysis.get("stats_json"), {}).get("catalog", {})
        has_py = bool(db.scalar("SELECT 1 FROM analysis_files WHERE analysis_id = ? AND language = 'Python' AND is_test = FALSE LIMIT 1", [analysis_id]))
        has_py_cat = bool(db.scalar("SELECT 1 FROM analysis_catalog WHERE analysis_id = ? AND language = 'Python' LIMIT 1", [analysis_id]))
        missing_py_routes = bool(
            db.scalar(
                """
                SELECT 1 FROM analysis_files f
                WHERE f.analysis_id = ? AND f.language = 'Python' AND f.role = 'Controller / Route Handler'
                  AND NOT EXISTS (
                      SELECT 1 FROM analysis_catalog c
                      WHERE c.analysis_id = f.analysis_id AND c.path = f.path AND c.kind = 'endpoint'
                  )
                LIMIT 1
                """,
                [analysis_id],
            )
        )
        if cat_stats.get("version") != CATALOG_VERSION or (has_py and not has_py_cat) or missing_py_routes or bool(cat_stats.get("errors")):
            _rebuild_analysis(analysis)
            analysis = _completed(analysis_id)
    counts = db.fetchall("SELECT layer, kind, count(*) AS n FROM analysis_catalog WHERE analysis_id = ? GROUP BY layer, kind", [analysis_id])
    layers = {layer: {"label": LAYER_LABELS[layer], "total": 0, "kinds": []} for layer in LAYERS}
    for row in counts:
        if row["layer"] in layers:
            layers[row["layer"]]["total"] += int(row["n"])
            layers[row["layer"]]["kinds"].append({"kind": row["kind"], "label": KIND_LABELS.get(row["kind"], row["kind"]), "count": int(row["n"])})
    for layer in layers.values():
        layer["kinds"].sort(key=lambda k: list(KIND_LABELS).index(k["kind"]) if k["kind"] in KIND_LABELS else 99)
    stats = _loads(analysis.get("stats_json"), {}).get("catalog", {})
    databases = [{"name": r["name"], "item_id": r["item_id"]} for r in db.fetchall("SELECT name, item_id FROM analysis_catalog WHERE analysis_id = ? AND kind = 'database' ORDER BY name", [analysis_id])]
    return {"built": bool(counts), "layers": layers, "stats": stats, "databases": databases, "links": int(db.scalar("SELECT count(*) FROM analysis_catalog_links WHERE analysis_id = ?", [analysis_id]) or 0)}


def _badge(row: dict, details: dict) -> dict:
    kind = row["kind"]
    if kind == "endpoint":
        return {"method": details.get("method", ""), "style": details.get("style", ""), "secured": bool(details.get("security")), "rules": len(details.get("rules", [])) + len(details.get("llm_rules", []))}
    if kind == "table":
        return {"columns": len(details.get("columns", [])), "entity": details.get("entity", ""), "crud": "".join(details.get("crud", {}).keys())}
    if kind == "screen":
        return {"platform": details.get("platform", ""), "fields": len(details.get("fields", [])), "calls": len(details.get("backend_calls", [])), "routes": details.get("routes", [])[:2]}
    if kind in ("service", "view_model", "repository"):
        return {"methods": len(details.get("methods", []))}
    if kind == "query":
        return {"operation": details.get("operation", ""), "dialect": details.get("dialect", "")}
    if kind == "job":
        return {"schedule": details.get("human", "")}
    if kind == "program":
        return {"dialect": details.get("dialect", ""), "role": details.get("role", ""), "units": len(details.get("units", [])), "complexity": details.get("complexity", 0)}
    if kind == "transaction":
        return {"program": details.get("program", ""), "resolved": bool(details.get("resolved"))}
    if kind == "file":
        return {"organization": details.get("organization", ""), "accessors": len(details.get("accessed_by", []))}
    if kind == "copybook":
        return {"fields": len(details.get("records", [])), "dclgen": len(details.get("dclgen", []))}
    if kind == "listener":
        dest = details.get("destinations", [])
        return {"transport": details.get("transport", ""), "destination": dest[0] if dest else ""}
    if kind == "topic":
        return {"transport": details.get("transport", "")}
    if kind == "config":
        return {"secret": details.get("secret", False), "category": details.get("category", ""), "used": len(details.get("used_by", []))}
    if kind in ("api_client", "integration"):
        return {"method": details.get("method", ""), "matched": bool(details.get("endpoint"))}
    return {}


def items(user: dict, analysis_id: str, layer: str = "", kind: str = "", q: str = "", limit: int = 500, offset: int = 0) -> dict:
    require(user, "repositories", "read")
    _completed(analysis_id)
    where, params = ["analysis_id = ?"], [analysis_id]
    if layer:
        where.append("layer = ?")
        params.append(layer)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if q.strip():
        where.append("(lower(name) LIKE ? OR lower(path) LIKE ? OR lower(signature) LIKE ? OR lower(summary) LIKE ?)")
        needle = f"%{q.strip().lower()}%"
        params += [needle] * 4
    total = int(db.scalar(f"SELECT count(*) FROM analysis_catalog WHERE {' AND '.join(where)}", params) or 0)
    rows = db.fetchall(
        f"SELECT item_id, item_key, layer, kind, name, path, line_start, line_end, framework, signature, summary, summary_source, details_json FROM analysis_catalog "
        f"WHERE {' AND '.join(where)} ORDER BY kind, name LIMIT ? OFFSET ?",
        params + [max(1, min(limit, 2000)), max(0, offset)],
    )
    out = []
    for row in rows:
        details = _loads(row.pop("details_json"), {})
        row["badge"] = _badge(row, details)
        row["summary"] = (row["summary"] or "")[:300]
        out.append(row)
    return {"total": total, "items": out}


def _by_keys(analysis_id: str, keys: list[str]) -> dict[str, dict]:
    out = {}
    for start in range(0, len(keys), 300):
        batch = keys[start : start + 300]
        if not batch:
            continue
        marks = ", ".join("?" for _ in batch)
        for row in db.fetchall(f"SELECT item_id, item_key, layer, kind, name, path, line_start FROM analysis_catalog WHERE analysis_id = ? AND item_key IN ({marks})", [analysis_id, *batch]):
            out[row["item_key"]] = row
    return out


def item(user: dict, analysis_id: str, item_id: str) -> dict:
    require(user, "repositories", "read")
    _completed(analysis_id)
    row = db.fetchone("SELECT * FROM analysis_catalog WHERE analysis_id = ? AND item_id = ?", [analysis_id, item_id])
    if row is None:
        raise AppError(404, "Catalogue item not found")
    row = dict(row)
    row["details"] = _loads(row.pop("details_json"), {})
    out_links = db.fetchall("SELECT target_key, kind, detail FROM analysis_catalog_links WHERE analysis_id = ? AND source_key = ?", [analysis_id, row["item_key"]])
    in_links = db.fetchall("SELECT source_key, kind, detail FROM analysis_catalog_links WHERE analysis_id = ? AND target_key = ?", [analysis_id, row["item_key"]])
    known = _by_keys(analysis_id, sorted({lk["target_key"] for lk in out_links} | {lk["source_key"] for lk in in_links}))
    row["links_out"] = [{**known[lk["target_key"]], "relation": lk["kind"], "detail": lk["detail"]} for lk in out_links if lk["target_key"] in known]
    row["links_in"] = [{**known[lk["source_key"]], "relation": lk["kind"], "detail": lk["detail"]} for lk in in_links if lk["source_key"] in known]
    return row


def resolve_key(user: dict, analysis_id: str, key: str) -> dict:
    require(user, "repositories", "read")
    row = db.fetchone("SELECT item_id FROM analysis_catalog WHERE analysis_id = ? AND item_key = ?", [analysis_id, key])
    if row is None:
        raise AppError(404, "Catalogue item not found")
    return row


def _graph(analysis_id: str) -> tuple[dict[str, dict], list[dict]]:
    rows = db.fetchall("SELECT item_id, item_key, layer, kind, name, path, line_start FROM analysis_catalog WHERE analysis_id = ?", [analysis_id])
    links = db.fetchall("SELECT source_key, target_key, kind, detail FROM analysis_catalog_links WHERE analysis_id = ?", [analysis_id])
    return {r["item_key"]: r for r in rows}, links


def _safe(text: str, limit: int = 60) -> str:
    text = re.sub(r"[\"`;#{}<>|\[\]]", " ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] or "?"


def trace(user: dict, analysis_id: str, item_id: str, depth: int = 5) -> dict:
    """Upstream callers and downstream dependencies of one item, as a flowchart and a sequence diagram."""
    require(user, "repositories", "read")
    _completed(analysis_id)
    nodes, links = _graph(analysis_id)
    start = next((k for k, v in nodes.items() if v["item_id"] == item_id), None)
    if start is None:
        raise AppError(404, "Catalogue item not found")
    out_adj, in_adj = defaultdict(list), defaultdict(list)
    for link in links:
        if link["kind"] in TRACE_KINDS and link["source_key"] in nodes and link["target_key"] in nodes:
            out_adj[link["source_key"]].append(link)
            in_adj[link["target_key"]].append(link)
    chosen, edges = {start}, []
    for adjacency, direction in ((out_adj, "down"), (in_adj, "up")):
        queue = deque([(start, 0)])
        seen = {start}
        while queue:
            node, level = queue.popleft()
            if level >= depth:
                continue
            for link in adjacency[node][:25]:
                nxt = link["target_key"] if direction == "down" else link["source_key"]
                if nxt in seen:
                    continue
                if direction == "up" and nodes[nxt]["kind"] in ("database",):
                    continue
                seen.add(nxt)
                chosen.add(nxt)
                edges.append(link)
                if len(chosen) > 60:
                    break
                queue.append((nxt, level + 1))
    ids = {key: f"n{i}" for i, key in enumerate(sorted(chosen, key=lambda k: (LAYERS.index(nodes[k]["layer"]) if nodes[k]["layer"] in LAYERS else 9, k)))}
    flow = ["flowchart LR"]
    for key, nid in ids.items():
        n = nodes[key]
        flow.append(f'  {nid}["{_safe(LAYER_LABELS.get(n["layer"], n["layer"]) + " · " + n["kind"])}<br/>{_safe(n["name"], 70)}"]:::layer_{n["layer"]}')
    unique = []
    seen_edges = set()
    for link in edges:
        key = (link["source_key"], link["target_key"], link["kind"])
        if key in seen_edges:
            continue
        seen_edges.add(key)
        unique.append(link)
        label = _safe(link["kind"] + (" " + link["detail"] if link["detail"] and len(link["detail"]) < 40 else ""), 50)
        flow.append(f'  {ids[link["source_key"]]} -->|{label}| {ids[link["target_key"]]}')
    seq = ["sequenceDiagram"]
    order = []
    for link in unique:
        for key in (link["source_key"], link["target_key"]):
            if key not in order:
                order.append(key)
    for key in order[:24]:
        seq.append(f"  participant {ids[key]} as {_safe(nodes[key]['name'], 40)}")
    shown = set(order[:24])
    for link in unique:
        if link["source_key"] in shown and link["target_key"] in shown and link["kind"] in ("calls", "submits", "accesses", "calls procedure", "publishes", "delivers to", "renders", "redirects to", "uses", "declares", "implemented by"):
            arrow = "-->>" if link["kind"] in ("renders", "delivers to") else "->>"
            seq.append(f"  {ids[link['source_key']]}{arrow}{ids[link['target_key']]}: {_safe(link['kind'] + ' ' + (link['detail'] or ''), 60)}")
    return {
        "nodes": [{"key": k, **nodes[k]} for k in ids], "edges": [{"source": lk["source_key"], "target": lk["target_key"], "kind": lk["kind"], "detail": lk["detail"]} for lk in unique],
        "flowchart": "\n".join(flow), "sequence": "\n".join(seq) if len(seq) > 1 + len(order[:24]) else "",
    }


def _er_type(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_()]", "_", (value or "col").replace(",", "_")).strip("_")
    return value[:30] or "col"


def erd(user: dict, analysis_id: str, keys: str = "", limit: int = 40) -> dict:
    require(user, "repositories", "read")
    _completed(analysis_id)
    rows = db.fetchall("SELECT item_id, item_key, name, details_json FROM analysis_catalog WHERE analysis_id = ? AND kind = 'table' ORDER BY name", [analysis_id])
    wanted = {k for k in keys.split(",") if k}
    tables = []
    for row in rows:
        if wanted and row["item_key"] not in wanted and row["item_id"] not in wanted:
            continue
        tables.append((row, _loads(row["details_json"], {})))
    if wanted and tables:
        names = {sqlname(d["table"]) for _, d in tables}
        for row in rows:
            details = _loads(row["details_json"], {})
            if any(sqlname(fk.get("ref_table", "")) in names for fk in details.get("foreign_keys", [])) or any(sqlname(fk.get("ref_table", "")) == sqlname(details["table"]) for _, d in tables for fk in d.get("foreign_keys", [])):
                if row not in [t[0] for t in tables]:
                    tables.append((row, details))
    tables = tables[:limit]
    present = {sqlname(d["table"]) for _, d in tables}
    lines = ["erDiagram"]
    for _row, details in tables:
        name = sqlname(details["table"])
        lines.append(f"  {name} {{")
        for column in details.get("columns", [])[:40]:
            marks = []
            if column.get("pk"):
                marks.append("PK")
            if column.get("references"):
                marks.append("FK")
            col_type = _er_type(column.get("type") or column.get("java_type") or "col")
            lines.append(f"    {col_type} {_er_type(column['name'])}{' ' + ','.join(marks) if marks else ''}")
        lines.append("  }")
    for _row, details in tables:
        name = sqlname(details["table"])
        seen = set()
        for fk in details.get("foreign_keys", []):
            target = sqlname(fk.get("ref_table", ""))
            if target in present and (target, tuple(fk.get("columns", []))) not in seen:
                seen.add((target, tuple(fk.get("columns", []))))
                lines.append(f'  {target} ||--o{{ {name} : "{_safe(", ".join(fk.get("columns", [])) or "fk", 30)}"')
    return {"mermaid": "\n".join(lines), "tables": len(tables), "total": len(rows)}


def sqlname(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", (name or "").split(".")[-1]).upper() or "TABLE"


CRUD_ACTORS = ["endpoint", "transaction", "service", "program", "job", "listener", "view_model", "screen", "procedure"]


def crud(user: dict, analysis_id: str) -> dict:
    require(user, "repositories", "read")
    _completed(analysis_id)
    rows = db.fetchall("SELECT item_id, name, details_json FROM analysis_catalog WHERE analysis_id = ? AND kind IN ('table', 'view', 'file') ORDER BY name", [analysis_id])
    actors: dict[str, dict] = {}
    matrix = []
    for row in rows:
        details = _loads(row["details_json"], {})
        cells: dict[str, str] = {}
        for op, names in (details.get("crud") or {}).items():
            for actor in names:
                kind, _, name = actor.partition(":")
                if kind not in CRUD_ACTORS:
                    continue
                actors.setdefault(actor, {"key": actor, "kind": kind, "name": name})
                cells[actor] = "".join(sorted(set(cells.get(actor, "") + op), key="CRUD".index))
        matrix.append({"table": row["name"], "item_id": row["item_id"], "cells": cells})
    ordered = sorted(actors.values(), key=lambda a: (CRUD_ACTORS.index(a["kind"]), a["name"]))
    return {"actors": ordered[:150], "rows": matrix}


# --- export ---------------------------------------------------------------------------------------------------------


def _all(analysis_id: str) -> list[dict]:
    rows = db.fetchall("SELECT * FROM analysis_catalog WHERE analysis_id = ? ORDER BY layer, kind, name", [analysis_id])
    for row in rows:
        row["details"] = _loads(row.pop("details_json"), {})
    return rows


def export_markdown(user: dict, analysis_id: str) -> str:
    require(user, "repositories", "read")
    analysis = _completed(analysis_id)
    repo = db.fetchone("SELECT name FROM repositories WHERE repo_id = ?", [analysis["repo_id"]]) or {"name": ""}
    rows = _all(analysis_id)
    out = [f"# Layer catalogue — {repo['name']} ({analysis['branch'] or 'default'} @ {(analysis.get('commit_sha') or '')[:10]})", ""]
    for layer in LAYERS:
        layer_rows = [r for r in rows if r["layer"] == layer]
        if not layer_rows:
            continue
        out += [f"## {LAYER_LABELS[layer]} layer", ""]
        for kind in [k for k in KIND_LABELS if any(r["kind"] == k for r in layer_rows)]:
            out += [f"### {KIND_LABELS[kind]}", ""]
            for r in [r for r in layer_rows if r["kind"] == kind][:400]:
                d = r["details"]
                out.append(f"#### {r['name']}")
                out.append(f"`{r['path']}:{r['line_start']}` · {r['framework']}")
                if r["summary"]:
                    out += ["", r["summary"]]
                if kind == "endpoint":
                    params = [p for p in d.get("params", []) if p.get("in") not in ("context", "body")]
                    if params:
                        out += ["", "| In | Name | Type | Required | Validation |", "|---|---|---|---|---|"]
                        out += [f"| {p.get('in')} | {p.get('name')} | {p.get('type', '')} | {'yes' if p.get('required') else ''} | {', '.join(p.get('validations', []))} |" for p in params]
                    request = d.get("request") or {}
                    if request.get("fields"):
                        out += ["", f"Request `{request.get('name') or request.get('type')}` ({request.get('content_type', '')}):", "", "| Field | Type | Required | Validation |", "|---|---|---|---|"]
                        out += [f"| {f['name']} | {f.get('type', '')} | {'yes' if f.get('required') else ''} | {', '.join(f.get('validations', []))} |" for f in request["fields"]]
                    response = d.get("response") or {}
                    out += ["", f"Response: `{response.get('type', '')}` · status {', '.join(str(c) for c in d.get('status_codes', []))}" + (f" · views {', '.join(response.get('views', []))}" if response.get("views") else "")]
                    if d.get("security"):
                        out.append(f"Security: {json.dumps(d['security'], default=str)}")
                if kind == "table":
                    out += ["", "| Column | Type | PK | Null | References | Field |", "|---|---|---|---|---|---|"]
                    out += [f"| {c['name']} | {c.get('type') or c.get('java_type', '')} | {'PK' if c.get('pk') else ''} | {'' if not c.get('nullable', True) else 'yes'} | {(c.get('references') or {}).get('table', '')} | {c.get('field', '')} |" for c in d.get("columns", [])]
                    if d.get("crud"):
                        out += ["", "CRUD: " + "; ".join(f"**{op}** {', '.join(names)}" for op, names in d["crud"].items())]
                if kind == "query" and d.get("sql"):
                    out += ["", "```sql", d["sql"][:3000], "```"]
                if kind == "screen":
                    if d.get("fields"):
                        out += ["", "| Field | Type | Label | Required | Validation |", "|---|---|---|---|---|"]
                        out += [f"| {f.get('name')} | {f.get('type', '')} | {f.get('label', '')} | {'yes' if f.get('required') else ''} | {', '.join((f.get('validations') or []) + (f.get('server_validations') or []))} |" for f in d["fields"]]
                    if d.get("backend_calls"):
                        out += ["", "Backend calls: " + ", ".join(f"`{c.get('method')} {c.get('url')}`" for c in d["backend_calls"])]
                if kind in ("service", "view_model", "repository") and d.get("methods"):
                    out += [""] + [f"- `{m.get('signature', m.get('name'))}`" + (f" — calls {', '.join(m.get('calls', [])[:6])}" if m.get("calls") else "") for m in d["methods"][:40]]
                rules = d.get("rules", [])
                if rules:
                    out += ["", "Rules:"] + [f"- If `{x.get('condition')}` → {x.get('outcome')} (line {x.get('line')})" for x in rules[:15]]
                if d.get("llm_rules"):
                    out += ["", "Business rules:"] + [f"- {x['rule']}" + (f" (line {x['line']})" if x.get("line") else "") for x in d["llm_rules"]]
                out.append("")
    return "\n".join(out)


def export_xlsx(user: dict, analysis_id: str) -> bytes:
    require(user, "repositories", "read")
    _completed(analysis_id)
    from app.services import xlsx

    rows = _all(analysis_id)
    sheets: list[tuple[str, list[str], list[list]]] = []

    def sheet(title: str, headers: list[str], data: list[list]):
        sheets.append((title, headers, [[json.dumps(v, default=str) if isinstance(v, (list, dict)) else v for v in record] for record in data]))

    sheet("Endpoints", ["Method", "Path", "Handler", "Style", "Params", "Request type", "Request fields", "Response", "Status", "Security", "Rules", "Purpose", "File", "Line"],
          [[r["details"].get("method"), r["details"].get("path"), r["signature"], r["details"].get("style"),
            "; ".join(f"{p.get('in')}:{p.get('name')}" for p in r["details"].get("params", []) if p.get("in") != "context"),
            (r["details"].get("request") or {}).get("name", ""), "; ".join(f"{f['name']}{'*' if f.get('required') else ''} {' '.join(f.get('validations', []))}" for f in (r["details"].get("request") or {}).get("fields", [])),
            (r["details"].get("response") or {}).get("type", ""), ", ".join(str(c) for c in r["details"].get("status_codes", [])), json.dumps(r["details"].get("security") or {}, default=str),
            " | ".join(f"{x.get('condition')} → {x.get('outcome')}" for x in r["details"].get("rules", [])) + " | ".join(x["rule"] for x in r["details"].get("llm_rules", [])),
            r["summary"], r["path"], r["line_start"]] for r in rows if r["kind"] == "endpoint"])
    sheet("Services", ["Kind", "Name", "Method", "Signature", "Transactional", "Calls", "Rules", "Purpose", "File", "Line"],
          [[r["kind"], r["name"], m.get("name"), m.get("signature"), "yes" if m.get("transactional") else "", ", ".join(m.get("calls", [])),
            " | ".join(f"{x.get('condition')} → {x.get('outcome')}" for x in m.get("rules", [])), r["summary"], r["path"], m.get("line")]
           for r in rows if r["kind"] in ("service", "view_model") for m in r["details"].get("methods", [])] +
          [[r["kind"], r["name"], "", r["signature"], "", ", ".join(r["details"].get("calls", [])), r["details"].get("human", ""), r["summary"], r["path"], r["line_start"]] for r in rows if r["kind"] in ("job", "listener")])
    sheet("Tables", ["Table", "Column", "Type", "Entity field", "PK", "Nullable", "References", "Comment", "Entity", "ORM", "CRUD by"],
          [[r["name"], c["name"], c.get("type", ""), f"{c.get('field', '')} {c.get('java_type', '')}".strip(), "PK" if c.get("pk") else "", "" if not c.get("nullable", True) else "yes",
            (c.get("references") or {}).get("table", ""), c.get("comment", ""), r["details"].get("entity", ""), r["details"].get("orm", ""),
            "; ".join(f"{op}: {', '.join(n)}" for op, n in (r["details"].get("crud") or {}).items())]
           for r in rows if r["kind"] == "table" for c in r["details"].get("columns", [])])
    sheet("Queries", ["Name", "Operation", "Dialect", "Tables", "SQL", "File", "Line"],
          [[r["name"], r["details"].get("operation"), r["details"].get("dialect"), ", ".join(f"{t}({''.join(o)})" for t, o in (r["details"].get("tables") or {}).items()), r["details"].get("sql", "")[:30000], r["path"], r["line_start"]]
           for r in rows if r["kind"] in ("query", "procedure", "view", "trigger")])
    sheet("Screens", ["Screen", "Platform / type", "Routes", "Field", "Field type", "Label", "Required", "Validation", "Backend calls", "File"],
          [[r["name"], f"{r['details'].get('platform', '')} {r['details'].get('type', '')}", ", ".join(r["details"].get("routes", [])), f.get("name"), f.get("type"), f.get("label"),
            "yes" if f.get("required") else "", ", ".join((f.get("validations") or []) + (f.get("server_validations") or [])),
            ", ".join(f"{c.get('method')} {c.get('url')}" for c in r["details"].get("backend_calls", [])), r["path"]]
           for r in rows if r["kind"] == "screen" for f in (r["details"].get("fields") or [{}])])
    sheet("Shared", ["Kind", "Name", "Detail", "File", "Line"],
          [[r["kind"], r["name"], (", ".join(f"{v['profile']}={v['value']}" for v in r["details"].get("values", [])) if r["kind"] == "config" else
                                   ", ".join(r["details"].get("values", [])) if r["kind"] == "enum" else
                                   "; ".join(f"{f['name']} {f.get('type', '')}" for f in r["details"].get("fields", [])) if r["kind"] in ("dto", "config_class") else r["signature"]),
            r["path"], r["line_start"]] for r in rows if r["layer"] == "shared"])
    return xlsx.workbook(sheets)


def source(user: dict, analysis_id: str, path: str) -> dict:
    """File content for the code pop-up (lighter than the inspector's file detail)."""
    require(user, "repositories", "read")
    _completed(analysis_id)
    row = db.fetchone("SELECT file_id, path, language, layer, role, loc FROM analysis_files WHERE analysis_id = ? AND path = ?", [analysis_id, path])
    if row is None:
        raise AppError(404, "File not found in this analysis")
    content = db.fetchone("SELECT content FROM analysis_file_contents WHERE file_id = ?", [row["file_id"]])
    return {"path": row["path"], "language": row["language"], "layer": row["layer"], "role": row["role"], "loc": row["loc"], "content": content["content"] if content else None}


def wireframe(user: dict, analysis_id: str, item_id: str, mode: str = "deterministic", regenerate: bool = False) -> dict:
    """Returns deterministic wireframe specification (mode='deterministic') or LLM-generated HTML mockup (mode='llm')."""
    require(user, "repositories", "read")
    _completed(analysis_id)
    row = db.fetchone("SELECT * FROM analysis_catalog WHERE analysis_id = ? AND item_id = ?", [analysis_id, item_id])
    if row is None:
        raise AppError(404, "Catalogue item not found")
    row = dict(row)
    details = _loads(row.get("details_json"), {})

    platform = details.get("platform") or ""
    if not platform:
        lang = (row.get("language") or "").lower()
        path = (row.get("path") or "").lower()
        if "kotlin" in lang or "android" in path or path.endswith((".kt", ".xml")):
            platform = "Android"
        elif "swift" in lang or "ios" in path or path.endswith((".swift", ".storyboard", ".xib")):
            platform = "iOS"
        else:
            platform = "Web"

    if mode != "llm":
        return {
            "mode": "deterministic",
            "item_id": item_id,
            "name": row["name"],
            "platform": platform,
            "type": details.get("type", ""),
            "title": details.get("title", "") or row["name"],
            "routes": details.get("routes", []),
            "fields": details.get("fields", []),
            "actions": details.get("actions", []),
            "forms": details.get("forms", []),
            "navigation": details.get("navigation", []),
            "components": details.get("components", []),
            "state": details.get("state", []),
            "layout": details.get("layout") or details.get("storyboard") or "",
        }

    # Mode: LLM
    cached_html = details.get("wireframe_llm")
    if cached_html and not regenerate:
        return {"mode": "llm", "html": cached_html, "cached": True}

    if not llm.configured():
        raise AppError(400, "LLM provider is not configured. Please configure an LLM in Settings → LLM.")

    # Retrieve source file content for context
    source_content = ""
    if row.get("path"):
        f_row = db.fetchone("SELECT file_id FROM analysis_files WHERE analysis_id = ? AND path = ?", [analysis_id, row["path"]])
        if f_row:
            c_row = db.fetchone("SELECT content FROM analysis_file_contents WHERE file_id = ?", [f_row["file_id"]])
            if c_row and c_row["content"]:
                lines = c_row["content"].splitlines()
                start = max(0, (row.get("line_start") or 1) - 10)
                end = min(len(lines), (row.get("line_end") or len(lines)) + 30)
                source_content = "\n".join(lines[start:end])[:6000]

    # If layout XML is referenced (e.g. in Android), also try loading it
    layout_name = details.get("layout")
    layout_content = ""
    if layout_name:
        l_row = db.fetchone(
            "SELECT fc.content FROM analysis_files af JOIN analysis_file_contents fc ON fc.file_id = af.file_id WHERE af.analysis_id = ? AND af.path LIKE ?",
            [analysis_id, f"%res/layout%/{layout_name}.xml"]
        )
        if l_row and l_row["content"]:
            layout_content = f"\n\nLayout XML ({layout_name}.xml):\n" + l_row["content"][:3000]

    system = (
        "You are an expert UI/UX designer and frontend engineer. "
        "Generate a clean, modern, realistic UI wireframe in standalone HTML and CSS for the given screen.\n"
        "Guidelines:\n"
        "- Aesthetic: Modern wireframe / prototype style with a clean neutral palette (slate/gray) and subtle accent color for primary actions.\n"
        "- Structure: Clean layout with top app header/navbar, proper spacing, form fields with labels & placeholders, buttons, icons (SVG or unicode), and realistic sample values.\n"
        "- Constraints: Must be a complete, self-contained HTML document (<!DOCTYPE html><html><head><style>...</style></head><body>...</body></html>) with inline CSS so it renders standalone inside an iframe.\n"
        "- Do NOT include markdown formatting or backticks around the HTML; return raw HTML only."
    )

    prompt = (
        f"Screen: {row['name']}\n"
        f"Platform: {platform} ({details.get('type', '')})\n"
        f"Title: {details.get('title', '')}\n"
        f"Routes: {details.get('routes', [])}\n"
        f"Extracted Fields: {json.dumps(details.get('fields', []), default=str)}\n"
        f"Extracted Actions: {json.dumps(details.get('actions', []), default=str)}\n"
        f"Extracted Navigation: {json.dumps(details.get('navigation', []), default=str)}\n"
        f"Code Excerpt:\n{source_content}{layout_content}\n\n"
        "Generate the standalone HTML/CSS wireframe for this screen."
    )

    try:
        raw_html = llm.complete(system, prompt, temperature=0.2)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(502, f"LLM wireframe generation failed: {exc}") from exc

    clean_html = raw_html.strip()
    if clean_html.startswith("```"):
        clean_html = re.sub(r"^```(?:html)?\s*", "", clean_html)
        clean_html = re.sub(r"\s*```$", "", clean_html).strip()

    # Cache into details_json
    details["wireframe_llm"] = clean_html
    db.exec_sql(
        "UPDATE analysis_catalog SET details_json = ? WHERE analysis_id = ? AND item_id = ?",
        [json.dumps(details), analysis_id, item_id]
    )

    return {"mode": "llm", "html": clean_html, "cached": False}


# --- comparison -----------------------------------------------------------------------------------------------------


def diff(left_id: str, right_id: str) -> dict:
    """API / schema / screen / config differences between two analyses, with breaking changes flagged."""
    def load(aid: str) -> dict[str, dict]:
        out = {}
        for row in db.fetchall("SELECT item_key, kind, name, details_json FROM analysis_catalog WHERE analysis_id = ? AND kind IN ('endpoint', 'table', 'screen', 'config', 'job', 'api_client')", [aid]):
            out[row["item_key"]] = {"kind": row["kind"], "name": row["name"], "details": _loads(row["details_json"], {})}
        return out

    left, right = load(left_id), load(right_id)
    if not left and not right:
        return {"available": False}
    result: dict = {"available": True, "breaking": []}
    for kind in ("endpoint", "table", "screen", "config", "job", "api_client", "program", "transaction"):
        lk = {k for k, v in left.items() if v["kind"] == kind}
        rk = {k for k, v in right.items() if v["kind"] == kind}
        changed = []
        for key in sorted(lk & rk):
            changes = _item_changes(kind, left[key]["details"], right[key]["details"])
            if changes:
                changed.append({"name": right[key]["name"], "changes": changes})
                result["breaking"] += [f"{right[key]['name']}: {c['text']}" for c in changes if c.get("breaking")]
        result[kind] = {"added": sorted(right[k]["name"] for k in rk - lk), "removed": sorted(left[k]["name"] for k in lk - rk), "changed": changed[:200]}
        if kind in ("endpoint", "table"):
            result["breaking"] += [f"{kind} removed: {left[k]['name']}" for k in sorted(lk - rk)]
    return result


def _item_changes(kind: str, a: dict, b: dict) -> list[dict]:
    out = []
    if kind == "endpoint":
        fa = {f["name"]: f for f in (a.get("request") or {}).get("fields", [])}
        fb = {f["name"]: f for f in (b.get("request") or {}).get("fields", [])}
        for name in fb.keys() - fa.keys():
            out.append({"text": f"request field added: {name}" + (" (required)" if fb[name].get("required") else ""), "breaking": bool(fb[name].get("required"))})
        for name in fa.keys() - fb.keys():
            out.append({"text": f"request field removed: {name}", "breaking": False})
        for name in fa.keys() & fb.keys():
            if fa[name].get("type") != fb[name].get("type"):
                out.append({"text": f"request field {name}: {fa[name].get('type')} → {fb[name].get('type')}", "breaking": True})
            if sorted(fa[name].get("validations", [])) != sorted(fb[name].get("validations", [])):
                out.append({"text": f"validation of {name}: {', '.join(fa[name].get('validations', [])) or 'none'} → {', '.join(fb[name].get('validations', [])) or 'none'}", "breaking": False})
        ra = {f["name"] for f in (a.get("response") or {}).get("fields", [])}
        rb = {f["name"] for f in (b.get("response") or {}).get("fields", [])}
        for name in ra - rb:
            out.append({"text": f"response field removed: {name}", "breaking": True})
        for name in rb - ra:
            out.append({"text": f"response field added: {name}", "breaking": False})
        pa = {(p.get("in"), p.get("name")): p for p in a.get("params", []) if p.get("in") != "context"}
        pb = {(p.get("in"), p.get("name")): p for p in b.get("params", []) if p.get("in") != "context"}
        for key in pb.keys() - pa.keys():
            out.append({"text": f"parameter added: {key[0]} {key[1]}" + (" (required)" if pb[key].get("required") else ""), "breaking": bool(pb[key].get("required"))})
        for key in pa.keys() - pb.keys():
            out.append({"text": f"parameter removed: {key[0]} {key[1]}", "breaking": False})
        if json.dumps(a.get("security") or {}, sort_keys=True, default=str) != json.dumps(b.get("security") or {}, sort_keys=True, default=str):
            out.append({"text": "security changed", "breaking": False})
    elif kind == "table":
        ca = {c["name"].lower(): c for c in a.get("columns", [])}
        cb = {c["name"].lower(): c for c in b.get("columns", [])}
        for name in cb.keys() - ca.keys():
            out.append({"text": f"column added: {cb[name]['name']}" + (" NOT NULL" if not cb[name].get("nullable", True) else ""), "breaking": not cb[name].get("nullable", True) and not cb[name].get("default")})
        for name in ca.keys() - cb.keys():
            out.append({"text": f"column removed: {ca[name]['name']}", "breaking": True})
        for name in ca.keys() & cb.keys():
            ta, tb = ca[name].get("type") or ca[name].get("java_type"), cb[name].get("type") or cb[name].get("java_type")
            if ta != tb:
                out.append({"text": f"column {cb[name]['name']}: {ta} → {tb}", "breaking": True})
            if ca[name].get("nullable", True) and not cb[name].get("nullable", True):
                out.append({"text": f"column {cb[name]['name']} became NOT NULL", "breaking": True})
    elif kind == "screen":
        fa = {f.get("name") for f in a.get("fields", [])}
        fb = {f.get("name") for f in b.get("fields", [])}
        if fa != fb:
            out.append({"text": f"fields +{len(fb - fa)} −{len(fa - fb)}: " + ", ".join([f"+{x}" for x in sorted(fb - fa)] + [f"−{x}" for x in sorted(fa - fb)])[:300], "breaking": False})
        ca = {f"{c.get('method')} {c.get('url')}" for c in a.get("backend_calls", [])}
        cb = {f"{c.get('method')} {c.get('url')}" for c in b.get("backend_calls", [])}
        if ca != cb:
            out.append({"text": "backend calls: " + ", ".join([f"+{x}" for x in sorted(cb - ca)] + [f"−{x}" for x in sorted(ca - cb)])[:300], "breaking": False})
    elif kind == "config":
        va = {v["profile"]: v["value"] for v in a.get("values", [])}
        vb = {v["profile"]: v["value"] for v in b.get("values", [])}
        if va != vb:
            out.append({"text": "values: " + ", ".join(f"{p}: {va.get(p, '—')} → {vb.get(p, '—')}" for p in sorted(set(va) | set(vb)) if va.get(p) != vb.get(p))[:300], "breaking": False})
    elif kind == "job":
        if a.get("schedule") != b.get("schedule"):
            out.append({"text": f"schedule: {a.get('human') or a.get('schedule')} → {b.get('human') or b.get('schedule')}", "breaking": False})
    elif kind == "api_client":
        if a.get("endpoint") and not b.get("endpoint"):
            out.append({"text": "no longer matches a backend endpoint", "breaking": True})
    elif kind == "program":
        if a.get("entry") != b.get("entry"):
            out.append({"text": f"entry parameters: {', '.join(a.get('entry', [])) or 'none'} → {', '.join(b.get('entry', [])) or 'none'}", "breaking": True})
        ca = {c.get("target") or c.get("variable") for c in a.get("calls", [])}
        cb = {c.get("target") or c.get("variable") for c in b.get("calls", [])}
        if ca != cb:
            out.append({"text": "calls: " + ", ".join([f"+{x}" for x in sorted(cb - ca)] + [f"−{x}" for x in sorted(ca - cb)])[:300], "breaking": False})
        ta = {t for s in a.get("sql", []) for t in s.get("tables", {})} | {f.get("external", "") for f in a.get("files", [])}
        tb = {t for s in b.get("sql", []) for t in s.get("tables", {})} | {f.get("external", "") for f in b.get("files", [])}
        if ta != tb:
            out.append({"text": "data accessed: " + ", ".join([f"+{x}" for x in sorted(tb - ta)] + [f"−{x}" for x in sorted(ta - tb)])[:300], "breaking": False})
    elif kind == "transaction":
        if a.get("program") != b.get("program"):
            out.append({"text": f"program: {a.get('program')} → {b.get('program')}", "breaking": True})
    return out
