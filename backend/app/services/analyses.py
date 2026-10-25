"""Reading stored analyses: overview, files, symbols, tree, graph, concerns, blast radius and search (all offline)."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import PurePosixPath

from app import db
from app.errors import AppError
from app.permissions import require
from app.services import jobs

JSON_FIELDS = ("stats_json", "macro_json", "c4_json")
FILE_COLUMNS = (
    "file_id, path, directory, language, container, layer, role, role_confidence, size_bytes, loc, code_lines, comment_lines, "
    "blank_lines, complexity, max_function_complexity, symbol_count, fan_in, fan_out, pagerank, scc_id, is_entry, is_test, "
    "is_generated, is_source, sha256, summary_source, concepts_json, evidence_json, summary"
)
SORTS = {"path": "path", "loc": "code_lines", "complexity": "complexity", "fan_in": "fan_in", "fan_out": "fan_out", "pagerank": "pagerank", "symbols": "symbol_count", "role": "role", "layer": "layer"}


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _analysis_row(analysis_id: str) -> dict:
    row = db.fetchone("SELECT * FROM analyses WHERE analysis_id = ?", [analysis_id])
    if row is None:
        raise AppError(404, "Analysis not found")
    return row


def _completed(analysis_id: str) -> dict:
    row = _analysis_row(analysis_id)
    if row["status"] != "completed":
        raise AppError(409, f"The analysis is {row['status']}; results are available once it completes")
    return row


def _file_payload(row: dict) -> dict:
    row = dict(row)
    row["concepts"] = _loads(row.pop("concepts_json", "[]"), [])
    row["evidence"] = _loads(row.pop("evidence_json", "[]"), [])
    row["pagerank"] = round(float(row.get("pagerank") or 0), 6)
    return row


def list_analyses(user: dict, repo_id: str = "", status: str = "", limit: int = 100, include_pr: bool = False) -> list[dict]:
    require(user, "repositories", "read")
    where, params = ["1=1"], []
    if not include_pr:
        where.append("coalesce(a.purpose, 'branch') = 'branch'")
    if repo_id:
        where.append("a.repo_id = ?")
        params.append(repo_id)
    if status:
        where.append("a.status = ?")
        params.append(status)
    rows = db.fetchall(
        f"""
        SELECT a.analysis_id, a.repo_id, r.name AS repo_name, r.source_type, a.branch, a.commit_sha, a.status, a.stage, a.progress, a.message,
               a.health_score, a.file_count, a.loc_total, a.architecture_style, a.llm_used, a.created_at, a.started_at, a.finished_at, a.started_by
        FROM analyses a JOIN repositories r ON r.repo_id = a.repo_id
        WHERE {' AND '.join(where)} ORDER BY a.created_at DESC LIMIT {max(1, min(int(limit), 500))}
        """,
        params,
    )
    return rows


def get_analysis(user: dict, analysis_id: str) -> dict:
    require(user, "repositories", "read")
    row = _analysis_row(analysis_id)
    repo = db.fetchone("SELECT * FROM repositories WHERE repo_id = ?", [row["repo_id"]]) or {}
    payload = {key: value for key, value in row.items() if key not in JSON_FIELDS}
    payload["stats"] = _loads(row["stats_json"], {})
    payload["macro"] = _loads(row["macro_json"], {})
    payload["c4"] = _loads(row["c4_json"], {})
    payload["repo"] = {key: repo.get(key) for key in ("repo_id", "name", "source_type", "url", "host", "owner", "slug", "default_branch", "description")}
    payload["containers"] = [
        {**c, "frameworks": _loads(c.pop("frameworks_json"), [])}
        for c in db.fetchall("SELECT * FROM analysis_containers WHERE analysis_id = ? ORDER BY path", [analysis_id])
    ]
    starter = db.fetchone("SELECT username FROM users WHERE user_id = ?", [row["started_by"]])
    payload["started_by_name"] = starter["username"] if starter else row["started_by"]
    return payload


def events(user: dict, analysis_id: str, after: str = "") -> list[dict]:
    require(user, "repositories", "read")
    _analysis_row(analysis_id)
    if after:
        return db.fetchall("SELECT * FROM analysis_events WHERE analysis_id = ? AND ts > ? ORDER BY ts", [analysis_id, after])
    return db.fetchall("SELECT * FROM analysis_events WHERE analysis_id = ? ORDER BY ts", [analysis_id])


def cancel(user: dict, analysis_id: str) -> dict:
    require(user, "repositories", "write")
    row = _analysis_row(analysis_id)
    if row["status"] == "queued":
        db.exec_sql("UPDATE analyses SET status = 'cancelled', message = 'Cancelled before start', finished_at = ? WHERE analysis_id = ?", [db.now(), analysis_id])
    elif row["status"] == "running":
        jobs.cancel(analysis_id)
    return {"analysis_id": analysis_id, "status": "cancelling" if row["status"] == "running" else "cancelled"}


def delete_analysis(user: dict, analysis_id: str) -> None:
    require(user, "repositories", "write")
    row = _analysis_row(analysis_id)
    if row["status"] in ("queued", "running"):
        jobs.cancel(analysis_id)
    for table in db.ANALYSIS_TABLES:
        db.exec_sql(f"DELETE FROM {table} WHERE analysis_id = ?", [analysis_id])
    for session in db.fetchall("SELECT session_id FROM chat_sessions WHERE scope_type = 'analysis' AND scope_id = ?", [analysis_id]):
        db.exec_sql("DELETE FROM chat_messages WHERE session_id = ?", [session["session_id"]])
    db.exec_sql("DELETE FROM chat_sessions WHERE scope_type = 'analysis' AND scope_id = ?", [analysis_id])
    for comparison in db.fetchall("SELECT comparison_id FROM comparisons WHERE left_analysis_id = ? OR right_analysis_id = ?", [analysis_id, analysis_id]):
        from app.services import compare

        compare.delete_comparison(user, comparison["comparison_id"], require_write=False)
    db.exec_sql("DELETE FROM analyses WHERE analysis_id = ?", [analysis_id])
    if (row.get("purpose") or "branch") == "branch":
        from app.services import landscape

        landscape.on_analysis_removed(row["repo_id"], analysis_id)


def list_files(user: dict, analysis_id: str, *, q: str = "", layer: str = "", role: str = "", language: str = "", container: str = "",
               sort: str = "pagerank", order: str = "desc", include_tests: bool = True, only_source: bool = True, limit: int = 200, offset: int = 0) -> dict:
    require(user, "repositories", "read")
    _completed(analysis_id)
    where, params = ["analysis_id = ?"], [analysis_id]
    if q:
        where.append("lower(path) LIKE ?")
        params.append(f"%{q.lower()}%")
    for column, value in (("layer", layer), ("role", role), ("language", language), ("container", container)):
        if value == "__none__":
            return {"total": 0, "items": []}
        if value:
            values = [item for item in value.split(",") if item]
            where.append(f"{column} IN ({', '.join('?' for _ in values)})")
            params += values
    if not include_tests:
        where.append("is_test = FALSE")
    if only_source:
        where.append("is_source = TRUE")
    clause = " AND ".join(where)
    column = SORTS.get(sort, "pagerank")
    direction = "ASC" if order == "asc" else "DESC"
    total = int(db.scalar(f"SELECT count(*) AS c FROM analysis_files WHERE {clause}", params) or 0)
    rows = db.fetchall(
        f"SELECT {FILE_COLUMNS} FROM analysis_files WHERE {clause} ORDER BY {column} {direction}, path LIMIT {max(1, min(int(limit), 2000))} OFFSET {max(0, int(offset))}",
        params,
    )
    return {"total": total, "items": [_file_payload(row) for row in rows]}


def file_detail(user: dict, analysis_id: str, path: str) -> dict:
    require(user, "repositories", "read")
    _completed(analysis_id)
    row = db.fetchone(f"SELECT {FILE_COLUMNS} FROM analysis_files WHERE analysis_id = ? AND path = ?", [analysis_id, path])
    if row is None:
        raise AppError(404, "File not found in this analysis")
    payload = _file_payload(row)
    content = db.fetchone("SELECT content FROM analysis_file_contents WHERE file_id = ?", [row["file_id"]])
    payload["content"] = content["content"] if content else None
    payload["symbols"] = db.fetchall(
        "SELECT name, qualified_name, kind, parent, line_start, line_end, complexity, signature, exported FROM analysis_symbols WHERE analysis_id = ? AND path = ? ORDER BY line_start",
        [analysis_id, path],
    )
    payload["outgoing"] = db.fetchall("SELECT target_path AS path, kind, weight, detail FROM analysis_edges WHERE analysis_id = ? AND source_path = ? ORDER BY kind, target_path", [analysis_id, path])
    payload["incoming"] = db.fetchall("SELECT source_path AS path, kind, weight, detail FROM analysis_edges WHERE analysis_id = ? AND target_path = ? ORDER BY kind, source_path", [analysis_id, path])
    layer_of = {r["path"]: (r["layer"], r["role"]) for r in db.fetchall("SELECT path, layer, role FROM analysis_files WHERE analysis_id = ?", [analysis_id])}
    for item in payload["outgoing"] + payload["incoming"]:
        item["layer"], item["role"] = layer_of.get(item["path"], ("shared", ""))
    payload["concerns"] = db.fetchall("SELECT severity, category, title, detail, evidence FROM analysis_concerns WHERE analysis_id = ? AND (path = ? OR evidence LIKE ?)", [analysis_id, path, f"%{path}%"])[:30]
    payload["blast_radius"] = blast_radius(user, analysis_id, path)
    return payload


def _reverse(analysis_id: str) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = defaultdict(set)
    for edge in db.fetchall("SELECT source_path, target_path FROM analysis_edges WHERE analysis_id = ?", [analysis_id]):
        reverse[edge["target_path"]].add(edge["source_path"])
    return reverse


def blast_radius(user: dict, analysis_id: str, path: str, max_depth: int = 6) -> dict:
    require(user, "repositories", "read")
    reverse = _reverse(analysis_id)
    seen = {path: 0}
    queue = deque([path])
    while queue:
        node = queue.popleft()
        if seen[node] >= max_depth:
            continue
        for dependant in reverse.get(node, ()):
            if dependant not in seen:
                seen[dependant] = seen[node] + 1
                queue.append(dependant)
    seen.pop(path, None)
    files = {r["path"]: r for r in db.fetchall("SELECT path, layer, role, pagerank, is_test, is_entry FROM analysis_files WHERE analysis_id = ? AND is_source = TRUE", [analysis_id])}
    total = max(1, sum(1 for f in files.values() if not f["is_test"]))
    impacted = [{"path": p, "depth": d, "layer": files.get(p, {}).get("layer", ""), "role": files.get(p, {}).get("role", ""), "is_test": bool(files.get(p, {}).get("is_test"))} for p, d in sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))]
    non_test = [item for item in impacted if not item["is_test"]]
    rank_share = sum(float(files.get(item["path"], {}).get("pagerank") or 0) for item in non_test)
    entries = sum(1 for item in non_test if files.get(item["path"], {}).get("is_entry"))
    risk = min(1.0, 0.45 * min(1.0, len(non_test) / (0.15 * total)) + 0.35 * min(1.0, rank_share * 4) + 0.2 * min(1.0, entries / 3))
    label = "LOW" if risk < 0.25 else "MODERATE" if risk < 0.5 else "HIGH" if risk < 0.75 else "CRITICAL"
    layers: dict[str, int] = defaultdict(int)
    for item in non_test:
        layers[item["layer"]] += 1
    return {
        "path": path, "impacted_count": len(non_test), "test_count": len(impacted) - len(non_test), "direct": sum(1 for i in non_test if i["depth"] == 1),
        "risk": round(risk, 2), "risk_label": label, "layers": dict(layers), "impacted": impacted[:300],
    }


def tree(user: dict, analysis_id: str) -> dict:
    require(user, "repositories", "read")
    _completed(analysis_id)
    rows = db.fetchall("SELECT path, layer, role, language, code_lines, complexity, is_test, is_source, fan_in, scc_id FROM analysis_files WHERE analysis_id = ? ORDER BY path", [analysis_id])
    root: dict = {"name": "", "path": "", "type": "dir", "children": {}, "files": 0, "loc": 0, "layers": defaultdict(int)}
    for row in rows:
        parts = row["path"].split("/")
        node = root
        node["files"] += 1
        node["loc"] += row["code_lines"]
        node["layers"][row["layer"]] += 1
        for depth, part in enumerate(parts[:-1]):
            child = node["children"].get(part)
            if child is None:
                child = {"name": part, "path": "/".join(parts[: depth + 1]), "type": "dir", "children": {}, "files": 0, "loc": 0, "layers": defaultdict(int)}
                node["children"][part] = child
            child["files"] += 1
            child["loc"] += row["code_lines"]
            child["layers"][row["layer"]] += 1
            node = child
        node["children"][parts[-1]] = {
            "name": parts[-1], "path": row["path"], "type": "file", "layer": row["layer"], "role": row["role"], "language": row["language"],
            "loc": row["code_lines"], "complexity": row["complexity"], "is_test": row["is_test"], "is_source": row["is_source"], "fan_in": row["fan_in"], "scc_id": row["scc_id"],
        }

    def finish(node: dict) -> dict:
        if node["type"] == "file":
            return node
        children = [finish(child) for child in node["children"].values()]
        children.sort(key=lambda c: (c["type"] != "dir", c["name"].lower()))
        layers = dict(node["layers"])
        dominant = max(layers, key=layers.get) if layers else "shared"
        return {"name": node["name"], "path": node["path"], "type": "dir", "files": node["files"], "loc": node["loc"], "layers": layers, "layer": dominant, "children": children}

    return finish(root)


def graph(user: dict, analysis_id: str, *, limit: int = 150, layers: str = "", kinds: str = "", include_tests: bool = False, focus: str = "", container: str = "") -> dict:
    require(user, "repositories", "read")
    _completed(analysis_id)
    where, params = ["analysis_id = ?", "is_source = TRUE"], [analysis_id]
    if not include_tests:
        where.append("is_test = FALSE")
    if layers == "__none__":
        total_nodes = int(db.scalar("SELECT count(*) AS c FROM analysis_files WHERE analysis_id = ? AND is_source = TRUE", [analysis_id]) or 0)
        return {"nodes": [], "edges": [], "total_nodes": total_nodes}
    if layers:
        values = [v for v in layers.split(",") if v]
        where.append(f"layer IN ({', '.join('?' for _ in values)})")
        params += values
    if container:
        where.append("container = ?")
        params.append(container)
    nodes = db.fetchall(
        f"SELECT path, layer, role, language, code_lines, complexity, fan_in, fan_out, pagerank, scc_id, is_entry FROM analysis_files WHERE {' AND '.join(where)} ORDER BY pagerank DESC",
        params,
    )
    edge_rows = db.fetchall("SELECT source_path, target_path, kind, weight FROM analysis_edges WHERE analysis_id = ?", [analysis_id])
    kind_filter = {k for k in kinds.split(",") if k}
    if kind_filter:
        edge_rows = [e for e in edge_rows if e["kind"] in kind_filter]
    if focus:
        neighbours = {focus}
        for edge in edge_rows:
            if edge["source_path"] == focus:
                neighbours.add(edge["target_path"])
            if edge["target_path"] == focus:
                neighbours.add(edge["source_path"])
        second = set(neighbours)
        for edge in edge_rows:
            if edge["source_path"] in neighbours or edge["target_path"] in neighbours:
                second.update((edge["source_path"], edge["target_path"]))
        keep_paths = neighbours if len(second) > limit else second
        nodes = [n for n in nodes if n["path"] in keep_paths]
    else:
        nodes = nodes[: max(10, min(int(limit), 600))]
    keep = {n["path"] for n in nodes}
    edges = [e for e in edge_rows if e["source_path"] in keep and e["target_path"] in keep]
    for node in nodes:
        node["pagerank"] = round(float(node["pagerank"] or 0), 6)
    return {"nodes": nodes, "edges": edges, "total_nodes": int(db.scalar("SELECT count(*) AS c FROM analysis_files WHERE analysis_id = ? AND is_source = TRUE", [analysis_id]) or 0)}


def concerns(user: dict, analysis_id: str, severity: str = "", category: str = "", layers: str = "") -> list[dict]:
    require(user, "repositories", "read")
    _completed(analysis_id)
    if layers == "__none__":
        return []
    where, params = ["analysis_id = ?"], [analysis_id]
    if severity:
        where.append("severity = ?")
        params.append(severity)
    if category:
        where.append("category = ?")
        params.append(category)
    rows = db.fetchall(
        f"SELECT concern_id, severity, category, title, path, detail, evidence FROM analysis_concerns WHERE {' AND '.join(where)} "
        "ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, category, title",
        params,
    )
    layer_of = {
        r["path"]: r["layer"]
        for r in db.fetchall("SELECT path, layer FROM analysis_files WHERE analysis_id = ?", [analysis_id])
    }
    for row in rows:
        if row.get("path") and row["path"] in layer_of:
            row["layer"] = layer_of[row["path"]]
    if layers:
        allowed = {v for v in layers.split(",") if v}
        if len(allowed) < 5:
            rows = [
                r for r in rows
                if (r.get("layer") in allowed) or (not r.get("layer") and any(p in (r.get("evidence") or "") for p, lyr in layer_of.items() if lyr in allowed))
            ]
    return rows


def concerns_markdown(user: dict, analysis_id: str) -> str:
    analysis = get_analysis(user, analysis_id)
    items = concerns(user, analysis_id)
    lines = [
        f"# CONCERNS.md — {analysis['repo']['name']}",
        "",
        f"Branch `{analysis['branch'] or 'n/a'}` · commit `{(analysis['commit_sha'] or '')[:10]}` · health **{analysis['health_score']}/100** · generated by Code Analyzer",
        "",
    ]
    for severity in ("critical", "warning", "info"):
        group = [c for c in items if c["severity"] == severity]
        if not group:
            continue
        lines += [f"## {severity.title()} ({len(group)})", ""]
        for concern in group:
            lines.append(f"### [{concern['category']}] {concern['title']}")
            if concern["path"]:
                lines.append(f"- **Location:** `{concern['path']}`")
            if concern["detail"]:
                lines.append(f"- **Why it matters:** {concern['detail']}")
            if concern["evidence"]:
                lines += ["- **Evidence:**", "", "```", concern["evidence"], "```"]
            lines.append("")
    return "\n".join(lines)


def symbols(user: dict, analysis_id: str, q: str = "", kind: str = "", limit: int = 100) -> list[dict]:
    require(user, "repositories", "read")
    _completed(analysis_id)
    where, params = ["analysis_id = ?"], [analysis_id]
    if q:
        where.append("(lower(name) LIKE ? OR lower(qualified_name) LIKE ?)")
        params += [f"%{q.lower()}%", f"%{q.lower()}%"]
    if kind:
        where.append("kind = ?")
        params.append(kind)
    return db.fetchall(
        f"SELECT path, name, qualified_name, kind, parent, line_start, line_end, complexity, signature FROM analysis_symbols WHERE {' AND '.join(where)} ORDER BY complexity DESC, name LIMIT {max(1, min(int(limit), 1000))}",
        params,
    )


def search(user: dict, analysis_id: str, q: str) -> dict:
    """Ctrl+K search: files by path, symbols by name, and concerns by title."""
    require(user, "repositories", "read")
    _completed(analysis_id)
    needle = f"%{q.lower().strip()}%"
    if not q.strip():
        return {"files": [], "symbols": [], "concerns": []}
    files = db.fetchall(
        "SELECT path, layer, role, language FROM analysis_files WHERE analysis_id = ? AND lower(path) LIKE ? ORDER BY is_source DESC, pagerank DESC LIMIT 15",
        [analysis_id, needle],
    )
    syms = db.fetchall(
        "SELECT path, qualified_name, kind, line_start FROM analysis_symbols WHERE analysis_id = ? AND lower(qualified_name) LIKE ? ORDER BY length(qualified_name) LIMIT 15",
        [analysis_id, needle],
    )
    found = db.fetchall("SELECT severity, category, title, path FROM analysis_concerns WHERE analysis_id = ? AND lower(title) LIKE ? LIMIT 8", [analysis_id, needle])
    return {"files": files, "symbols": syms, "concerns": found}


def dependencies(user: dict, analysis_id: str) -> list[dict]:
    require(user, "repositories", "read")
    _completed(analysis_id)
    return db.fetchall("SELECT container, ecosystem, name, version, scope, manifest FROM analysis_dependencies WHERE analysis_id = ? ORDER BY container, scope, name", [analysis_id])


def module_of(path: str) -> str:
    parts = PurePosixPath(path).parts
    return parts[0] if len(parts) > 1 else "(root)"
