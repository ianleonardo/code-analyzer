"""Comparison of two stored analyses: two repositories, or two branches/commits of the same repository.

Deterministic diff of metrics, stack, dependencies, structure (layers, roles, containers), files (added / removed /
modified by content hash), public symbols and concerns (new / resolved), plus an optional LLM narrative.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter

from app import db
from app.config import SYNTHESIS_RULES
from app.errors import AppError
from app.permissions import require
from app.services import llm

FILE_LIST_CAP = 400


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _side(analysis_id: str) -> dict:
    row = db.fetchone(
        "SELECT a.*, r.name AS repo_name, r.source_type, r.url FROM analyses a JOIN repositories r ON r.repo_id = a.repo_id WHERE a.analysis_id = ?",
        [analysis_id],
    )
    if row is None:
        raise AppError(404, "Analysis not found")
    if row["status"] != "completed":
        raise AppError(409, f"Analysis {analysis_id[:8]} is {row['status']}; compare completed analyses only")
    return row


def _metrics(row: dict) -> dict:
    stats = _loads(row["stats_json"], {})
    concerns = stats.get("concerns_by_severity") or {}
    return {
        "health_score": row["health_score"],
        "files": stats.get("files_source", row["file_count"]),
        "loc": row["loc_total"],
        "symbols": stats.get("symbols", 0),
        "edges": stats.get("edges", 0),
        "avg_complexity": stats.get("avg_complexity", 0),
        "max_complexity": stats.get("max_complexity", 0),
        "cycles": stats.get("cycles", 0),
        "layer_violations": stats.get("layer_violations", 0),
        "god_modules": stats.get("god_modules", 0),
        "duplication_pct": stats.get("duplication_pct", 0),
        "test_files": stats.get("test_files", 0),
        "test_ratio": stats.get("test_ratio", 0),
        "critical": concerns.get("critical", 0),
        "warning": concerns.get("warning", 0),
        "info": concerns.get("info", 0),
    }


def _share(counter: dict) -> dict:
    total = sum(counter.values()) or 1
    return {key: round(100 * value / total, 1) for key, value in counter.items()}


def compute(left_id: str, right_id: str) -> dict:
    left, right = _side(left_id), _side(right_id)
    left_stats, right_stats = _loads(left["stats_json"], {}), _loads(right["stats_json"], {})
    left_macro, right_macro = _loads(left["macro_json"], {}), _loads(right["macro_json"], {})
    same_repo = left["repo_id"] == right["repo_id"]

    lm, rm = _metrics(left), _metrics(right)
    metrics = [
        {"metric": key, "left": lm[key], "right": rm[key], "delta": (round(rm[key] - lm[key], 3) if isinstance(lm[key], (int, float)) and isinstance(rm[key], (int, float)) else None)}
        for key in lm
    ]

    lf = {fw["name"]: fw for fw in left_macro.get("frameworks", [])}
    rf = {fw["name"]: fw for fw in right_macro.get("frameworks", [])}
    stack = {
        "only_left": sorted(set(lf) - set(rf)),
        "only_right": sorted(set(rf) - set(lf)),
        "common": sorted(set(lf) & set(rf)),
        "categories": {name: (lf.get(name) or rf.get(name))["category"] for name in set(lf) | set(rf)},
    }

    def deps(analysis_id: str) -> dict[tuple[str, str], str]:
        return {(r["ecosystem"], r["name"].lower()): r["version"] for r in db.fetchall("SELECT ecosystem, name, version FROM analysis_dependencies WHERE analysis_id = ?", [analysis_id])}

    ld, rd = deps(left_id), deps(right_id)
    dependency_diff = {
        "only_left": sorted(f"{k[1]} ({k[0]})" for k in set(ld) - set(rd))[:300],
        "only_right": sorted(f"{k[1]} ({k[0]})" for k in set(rd) - set(ld))[:300],
        "version_changes": sorted(
            ({"name": k[1], "ecosystem": k[0], "left": ld[k], "right": rd[k]} for k in set(ld) & set(rd) if ld[k] != rd[k]),
            key=lambda d: d["name"],
        )[:300],
        "common": len(set(ld) & set(rd)),
    }

    structure = {
        "architecture_style": {"left": left["architecture_style"], "right": right["architecture_style"]},
        "languages": {"left": _share(left_stats.get("languages", {})), "right": _share(right_stats.get("languages", {}))},
        "layers": {"left": left_stats.get("layers", {}), "right": right_stats.get("layers", {})},
        "roles": {"left": left_stats.get("roles", {}), "right": right_stats.get("roles", {})},
        "containers": {
            "left": [c["name"] for c in db.fetchall("SELECT name FROM analysis_containers WHERE analysis_id = ? ORDER BY path", [left_id])],
            "right": [c["name"] for c in db.fetchall("SELECT name FROM analysis_containers WHERE analysis_id = ? ORDER BY path", [right_id])],
        },
    }

    lfiles = {r["path"]: r for r in db.fetchall("SELECT path, sha256, code_lines, layer, role, complexity FROM analysis_files WHERE analysis_id = ? AND is_source = TRUE", [left_id])}
    rfiles = {r["path"]: r for r in db.fetchall("SELECT path, sha256, code_lines, layer, role, complexity FROM analysis_files WHERE analysis_id = ? AND is_source = TRUE", [right_id])}
    added = sorted(set(rfiles) - set(lfiles))
    removed = sorted(set(lfiles) - set(rfiles))
    modified = sorted(p for p in set(lfiles) & set(rfiles) if lfiles[p]["sha256"] != rfiles[p]["sha256"])
    unchanged = len(set(lfiles) & set(rfiles)) - len(modified)
    lhashes = Counter(r["sha256"] for r in lfiles.values())
    rhashes = Counter(r["sha256"] for r in rfiles.values())
    shared_content = sum((lhashes & rhashes).values())
    similarity = round(100 * shared_content / max(1, len(set(lfiles) | set(rfiles))), 1)
    dep_similarity = round(100 * len(set(ld) & set(rd)) / max(1, len(set(ld) | set(rd))), 1)
    files = {
        "added": [{"path": p, "loc": rfiles[p]["code_lines"], "layer": rfiles[p]["layer"], "role": rfiles[p]["role"]} for p in added[:FILE_LIST_CAP]],
        "removed": [{"path": p, "loc": lfiles[p]["code_lines"], "layer": lfiles[p]["layer"], "role": lfiles[p]["role"]} for p in removed[:FILE_LIST_CAP]],
        "modified": [
            {"path": p, "loc_left": lfiles[p]["code_lines"], "loc_right": rfiles[p]["code_lines"], "delta": rfiles[p]["code_lines"] - lfiles[p]["code_lines"],
             "complexity_delta": rfiles[p]["complexity"] - lfiles[p]["complexity"], "layer": rfiles[p]["layer"], "role": rfiles[p]["role"]}
            for p in sorted(modified, key=lambda p: -abs(rfiles[p]["code_lines"] - lfiles[p]["code_lines"]))[:FILE_LIST_CAP]
        ],
        "counts": {"added": len(added), "removed": len(removed), "modified": len(modified), "unchanged": unchanged},
        "content_similarity_pct": similarity,
    }

    def public_symbols(analysis_id: str) -> set[tuple[str, str]]:
        return {(r["path"], r["qualified_name"]) for r in db.fetchall("SELECT path, qualified_name FROM analysis_symbols WHERE analysis_id = ? AND exported = TRUE", [analysis_id])}

    symbols = {}
    if same_repo or similarity > 20:
        ls, rs = public_symbols(left_id), public_symbols(right_id)
        symbols = {
            "added": [f"{p} :: {q}" for p, q in sorted(rs - ls)][:300],
            "removed": [f"{p} :: {q}" for p, q in sorted(ls - rs)][:300],
            "added_count": len(rs - ls),
            "removed_count": len(ls - rs),
        }

    def concern_keys(analysis_id: str) -> dict[tuple[str, str, str], dict]:
        return {(r["category"], r["path"], r["title"]): r for r in db.fetchall("SELECT severity, category, title, path FROM analysis_concerns WHERE analysis_id = ?", [analysis_id])}

    lc, rc = concern_keys(left_id), concern_keys(right_id)
    concerns = {
        "new": [rc[k] for k in sorted(set(rc) - set(lc))][:200],
        "resolved": [lc[k] for k in sorted(set(lc) - set(rc))][:200],
        "persisting": len(set(lc) & set(rc)),
    }

    def side_info(row: dict) -> dict:
        return {
            "analysis_id": row["analysis_id"], "repo_id": row["repo_id"], "repo_name": row["repo_name"], "branch": row["branch"],
            "commit_sha": row["commit_sha"], "finished_at": row["finished_at"], "health_score": row["health_score"], "summary": (row["summary"] or "")[:1500],
        }

    return {
        "left": side_info(left),
        "right": side_info(right),
        "same_repository": same_repo,
        "metrics": metrics,
        "stack": stack,
        "dependencies": dependency_diff,
        "dependency_similarity_pct": dep_similarity,
        "structure": structure,
        "files": files,
        "symbols": symbols,
        "concerns": concerns,
        "catalog": _catalog_diff(left_id, right_id),
    }


def _catalog_diff(left_id: str, right_id: str) -> dict:
    from app.services import catalog

    try:
        return catalog.diff(left_id, right_id)
    except Exception:  # noqa: BLE001 - comparisons of analyses without a catalogue still work
        return {"available": False}


def create_comparison(user: dict, body: dict) -> dict:
    require(user, "insights", "write")
    left_id, right_id = str(body.get("left_analysis_id") or ""), str(body.get("right_analysis_id") or "")
    if not left_id or not right_id:
        raise AppError(400, "Choose two analyses to compare")
    if left_id == right_id:
        raise AppError(400, "Choose two different analyses")
    result = compute(left_id, right_id)
    comparison_id = str(uuid.uuid4())
    title = str(body.get("title") or "").strip() or (
        f"{result['left']['repo_name']}@{result['left']['branch'] or '-'} vs {result['right']['repo_name']}@{result['right']['branch'] or '-'}"
    )
    narrative = ""
    if body.get("narrative", True) and llm.configured():
        narrative = _narrative(result)
    db.insert(
        "comparisons",
        {
            "comparison_id": comparison_id, "left_analysis_id": left_id, "right_analysis_id": right_id, "title": title[:500],
            "result_json": json.dumps(result, default=str), "narrative": narrative, "created_by": user["user_id"], "created_at": db.now(),
        },
    )
    return get_comparison(user, comparison_id)


def _narrative(result: dict) -> str:
    compact = {key: result[key] for key in ("left", "right", "same_repository", "metrics", "stack", "dependency_similarity_pct", "structure")}
    compact["files"] = {"counts": result["files"]["counts"], "content_similarity_pct": result["files"]["content_similarity_pct"], "largest_changes": result["files"]["modified"][:20], "added": result["files"]["added"][:30], "removed": result["files"]["removed"][:30]}
    compact["dependencies"] = {k: (v[:40] if isinstance(v, list) else v) for k, v in result["dependencies"].items()}
    compact["concerns"] = {"new": result["concerns"]["new"][:20], "resolved": result["concerns"]["resolved"][:20]}
    cat = result.get("catalog") or {}
    if cat.get("available"):
        compact["layer_catalogue"] = {"breaking_changes": cat.get("breaking", [])[:30],
                                      **{k: {"added": v["added"][:25], "removed": v["removed"][:25], "changed": v["changed"][:15]} for k, v in cat.items() if isinstance(v, dict)}}
    prompt = (
        "COMPARISON FACTS:\n" + json.dumps(compact, default=str)[:60000] +
        '\n\nReturn JSON: {"narrative": "Markdown: a short executive summary, then sections Architecture & Stack, Code Changes, Quality & Risk, Recommendations"}'
    )
    try:
        return str(llm.complete_json(SYNTHESIS_RULES, prompt, 0.2).get("narrative") or "")[:12000]
    except Exception:  # noqa: BLE001
        return ""


def regenerate_narrative(user: dict, comparison_id: str) -> dict:
    require(user, "insights", "write")
    row = _row(comparison_id)
    if not llm.configured():
        raise AppError(503, "No LLM is configured. Add one under Settings > LLM.")
    narrative = _narrative(_loads(row["result_json"], {}))
    db.exec_sql("UPDATE comparisons SET narrative = ? WHERE comparison_id = ?", [narrative, comparison_id])
    return get_comparison(user, comparison_id)


def _row(comparison_id: str) -> dict:
    row = db.fetchone("SELECT * FROM comparisons WHERE comparison_id = ?", [comparison_id])
    if row is None:
        raise AppError(404, "Comparison not found")
    return row


def get_comparison(user: dict, comparison_id: str) -> dict:
    require(user, "insights", "read")
    row = _row(comparison_id)
    return {
        "comparison_id": row["comparison_id"], "title": row["title"], "left_analysis_id": row["left_analysis_id"],
        "right_analysis_id": row["right_analysis_id"], "result": _loads(row["result_json"], {}), "narrative": row["narrative"],
        "created_by": row["created_by"], "created_at": row["created_at"],
    }


def list_comparisons(user: dict) -> list[dict]:
    require(user, "insights", "read")
    rows = db.fetchall("SELECT comparison_id, title, left_analysis_id, right_analysis_id, created_by, created_at FROM comparisons ORDER BY created_at DESC")
    names = {u["user_id"]: u["username"] for u in db.fetchall("SELECT user_id, username FROM users")}
    for row in rows:
        row["created_by_name"] = names.get(row["created_by"], row["created_by"])
    return rows


def delete_comparison(user: dict, comparison_id: str, require_write: bool = True) -> None:
    if require_write:
        require(user, "insights", "write")
    _row(comparison_id)
    for session in db.fetchall("SELECT session_id FROM chat_sessions WHERE scope_type = 'comparison' AND scope_id = ?", [comparison_id]):
        db.exec_sql("DELETE FROM chat_messages WHERE session_id = ?", [session["session_id"]])
    db.exec_sql("DELETE FROM chat_sessions WHERE scope_type = 'comparison' AND scope_id = ?", [comparison_id])
    db.exec_sql("DELETE FROM comparisons WHERE comparison_id = ?", [comparison_id])


def comparison_markdown(user: dict, comparison_id: str) -> str:
    comp = get_comparison(user, comparison_id)
    r = comp.get("result") or {}
    left = r.get("left") or {}
    right = r.get("right") or {}
    files = r.get("files") or {}
    counts = files.get("counts") or {}
    stack = r.get("stack") or {}
    deps = r.get("dependencies") or {}
    structure = r.get("structure") or {}
    concerns = r.get("concerns") or {}
    symbols = r.get("symbols") or {}
    catalog = r.get("catalog") or {}

    def fmt_delta(val) -> str:
        if val is None or val == 0:
            return "0"
        sign = "+" if val > 0 else ""
        return f"{sign}{val if isinstance(val, int) or float(val).is_integer() else round(float(val), 2)}"

    lines = [
        f"# Comparison Report — {comp['title']}",
        "",
        f"- **Type:** {'Two snapshots of the same repository' if r.get('same_repository') else 'Two different repositories'}",
        f"- **Created:** `{comp.get('created_at')}`",
        f"- **Dependency similarity:** `{r.get('dependency_similarity_pct', 0)}%` · **Content similarity:** `{files.get('content_similarity_pct', 0)}%`",
        f"- **Left (Baseline):** `{left.get('repo_name', '-')}` · branch `{left.get('branch') or '-'}` · commit `{(left.get('commit_sha') or '')[:10]}` · health **{left.get('health_score', '—')}/100**",
        f"- **Right (Candidate):** `{right.get('repo_name', '-')}` · branch `{right.get('branch') or '-'}` · commit `{(right.get('commit_sha') or '')[:10]}` · health **{right.get('health_score', '—')}/100**",
        "",
    ]

    if comp.get("narrative"):
        lines += [
            "## Executive Narrative",
            "",
            comp["narrative"].strip(),
            "",
        ]

    lines += [
        "## Metrics",
        "",
        "| Metric | Left (Baseline) | Right (Candidate) | Δ |",
        "| :--- | ---: | ---: | ---: |",
    ]
    for m in r.get("metrics") or []:
        name = str(m.get("metric", "")).replace("_", " ")
        lines.append(f"| {name} | {m.get('left', '—')} | {m.get('right', '—')} | {fmt_delta(m.get('delta'))} |")
    lines.append("")

    arch = structure.get("architecture_style") or {}
    layers_l = (structure.get("layers") or {}).get("left") or {}
    layers_r = (structure.get("layers") or {}).get("right") or {}
    lines += [
        "## Structure & Layers",
        "",
        f"- **Architecture style (Left):** {arch.get('left') or '—'}",
        f"- **Architecture style (Right):** {arch.get('right') or '—'}",
        "",
        "| Layer | Left Files | Right Files |",
        "| :--- | ---: | ---: |",
    ]
    for layer in ("api", "service", "data", "ui", "shared"):
        lines.append(f"| {layer} | {layers_l.get(layer, 0)} | {layers_r.get(layer, 0)} |")
    lines.append("")

    lines += [
        "## Technology Stack & Dependencies",
        "",
        f"- **Stack — Only Left ({len(stack.get('only_left') or [])}):** {', '.join(stack.get('only_left') or []) or 'None'}",
        f"- **Stack — Common ({len(stack.get('common') or [])}):** {', '.join(stack.get('common') or []) or 'None'}",
        f"- **Stack — Only Right ({len(stack.get('only_right') or [])}):** {', '.join(stack.get('only_right') or []) or 'None'}",
        f"- **Dependencies — Only Left ({len(deps.get('only_left') or [])}):** {', '.join((deps.get('only_left') or [])[:50]) or 'None'}",
        f"- **Dependencies — Only Right ({len(deps.get('only_right') or [])}):** {', '.join((deps.get('only_right') or [])[:50]) or 'None'}",
        "",
    ]

    vchanges = deps.get("version_changes") or []
    if vchanges:
        lines += [
            f"### Dependency Version Changes ({len(vchanges)})",
            "",
            "| Package | Ecosystem | Left | Right |",
            "| :--- | :--- | :--- | :--- |",
        ]
        for v in vchanges[:100]:
            lines.append(f"| `{v.get('name')}` | {v.get('ecosystem')} | `{v.get('left')}` | `{v.get('right')}` |")
        lines.append("")

    new_concerns = concerns.get("new") or []
    resolved_concerns = concerns.get("resolved") or []
    lines += [
        "## Concerns & Quality Diff",
        "",
        f"Persisting concerns: **{concerns.get('persisting', 0)}** · New on Right: **{len(new_concerns)}** · Resolved from Left: **{len(resolved_concerns)}**",
        "",
    ]
    if new_concerns:
        lines += [f"### New Concerns on Right ({len(new_concerns)})", ""]
        for c in new_concerns[:100]:
            path_part = f" (`{c['path']}`)" if c.get("path") else ""
            lines.append(f"- **[{str(c.get('severity', '')).upper()}]** {c.get('category')}: {c.get('title')}{path_part}")
        lines.append("")
    if resolved_concerns:
        lines += [f"### Resolved Concerns ({len(resolved_concerns)})", ""]
        for c in resolved_concerns[:100]:
            path_part = f" (`{c['path']}`)" if c.get("path") else ""
            lines.append(f"- **[{str(c.get('severity', '')).upper()}]** {c.get('category')}: {c.get('title')}{path_part}")
        lines.append("")

    if catalog.get("available"):
        lines += ["## API & Data Contract Changes", ""]
        breaking = catalog.get("breaking") or []
        if breaking:
            lines += [f"### Potential Breaking Changes ({len(breaking)})", ""]
            for b in breaking:
                lines.append(f"- {b}")
            lines.append("")
        for kind, label in (
            ("endpoint", "API Endpoints"),
            ("table", "Tables & Columns"),
            ("screen", "Screens"),
            ("api_client", "Client Calls"),
            ("job", "Scheduled Jobs"),
            ("config", "Configuration Keys"),
        ):
            part = catalog.get(kind)
            if not isinstance(part, dict):
                continue
            added_c = part.get("added") or []
            removed_c = part.get("removed") or []
            changed_c = part.get("changed") or []
            if not (added_c or removed_c or changed_c):
                continue
            lines += [
                f"### {label} (+{len(added_c)} / −{len(removed_c)} / Δ{len(changed_c)})",
                "",
            ]
            for item in added_c[:50]:
                lines.append(f"- **Added:** `{item}`")
            for item in removed_c[:50]:
                lines.append(f"- **Removed:** `{item}`")
            for item in changed_c[:50]:
                changes_str = "; ".join(ch.get("text", "") for ch in (item.get("changes") or []))
                lines.append(f"- **Changed:** `{item.get('name')}` — {changes_str}")
            lines.append("")

    lines += [
        "## File & Symbol Changes",
        "",
        f"- **Modified:** {counts.get('modified', 0)} · **Added:** {counts.get('added', 0)} · **Removed:** {counts.get('removed', 0)} · **Unchanged:** {counts.get('unchanged', 0)}",
        "",
    ]
    mod_files = files.get("modified") or []
    if mod_files:
        lines += [
            f"### Modified Files (top {min(len(mod_files), 100)})",
            "",
            "| File | Layer | Role | LOC (L → R) | Δ LOC | Δ McCabe |",
            "| :--- | :--- | :--- | ---: | ---: | ---: |",
        ]
        for f in mod_files[:100]:
            lines.append(
                f"| `{f.get('path')}` | {f.get('layer')} | {f.get('role')} | {f.get('loc_left')} → {f.get('loc_right')} | {fmt_delta(f.get('delta'))} | {fmt_delta(f.get('complexity_delta'))} |"
            )
        lines.append("")
    add_files = files.get("added") or []
    if add_files:
        lines += [f"### Added Files ({len(add_files)})", ""]
        for f in add_files[:100]:
            lines.append(f"- + `{f.get('path')}` [{f.get('layer')}/{f.get('role')}] ({f.get('loc')} LOC)")
        lines.append("")
    rem_files = files.get("removed") or []
    if rem_files:
        lines += [f"### Removed Files ({len(rem_files)})", ""]
        for f in rem_files[:100]:
            lines.append(f"- − `{f.get('path')}` [{f.get('layer')}/{f.get('role')}] ({f.get('loc')} LOC)")
        lines.append("")

    if symbols.get("added") or symbols.get("removed"):
        lines += [
            "### Public Symbols Diff",
            "",
            f"- **Added ({symbols.get('added_count', 0)}):** {', '.join(f'`{s}`' for s in (symbols.get('added') or [])[:60]) or 'None'}",
            f"- **Removed ({symbols.get('removed_count', 0)}):** {', '.join(f'`{s}`' for s in (symbols.get('removed') or [])[:60]) or 'None'}",
            "",
        ]

    return "\n".join(lines) + "\n"


_PDF_TRANSLATE = str.maketrans({
    "—": "-",
    "–": "-",
    "−": "-",
    "·": " | ",
    "•": "-",
    "→": "->",
    "←": "<-",
    "Δ": "Delta ",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "…": "...",
})


def _pdf_escape(text: str) -> str:
    clean = str(text or "").translate(_PDF_TRANSLATE)
    clean = clean.encode("ascii", "replace").decode("ascii")
    return clean.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def comparison_pdf(user: dict, comparison_id: str) -> bytes:
    """Render the Markdown comparison report into a multi-page styled PDF 1.4 document with zero external dependencies."""
    import textwrap

    md_text = comparison_markdown(user, comparison_id)
    page_w, page_h = 595.28, 841.89
    margin_x = 42.0
    top_y = page_h - 48.0
    bottom_y = 48.0

    pages: list[list[str]] = [[]]
    y = top_y

    def cur_page() -> list[str]:
        return pages[-1]

    def new_page() -> None:
        nonlocal y
        pages.append([])
        y = top_y

    def ensure_space(height: float) -> None:
        if y - height < bottom_y:
            new_page()

    def draw_text(x: float, y_pos: float, text: str, font: str = "F1", size: float = 9.5, rgb: tuple[float, float, float] = (0.12, 0.16, 0.23)) -> None:
        r, g, b = rgb
        cur_page().append(f"BT /{font} {size:.1f} Tf {r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y_pos:.2f} Td ({_pdf_escape(text)}) Tj ET")

    def draw_rect(x: float, y_pos: float, w: float, h: float, rgb: tuple[float, float, float]) -> None:
        r, g, b = rgb
        cur_page().append(f"{r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y_pos:.2f} {w:.2f} {h:.2f} re f")

    def strip_md_inline(line: str) -> str:
        s = line.replace("**", "").replace("`", "")
        return s.strip()

    for raw_line in md_text.splitlines():
        line = raw_line.rstrip()
        if not line:
            y -= 6.0
            continue

        if line.startswith("# "):
            ensure_space(36.0)
            title_text = strip_md_inline(line[2:])
            wrapped = textwrap.wrap(title_text, width=62) or [title_text]
            box_h = 16.0 * len(wrapped) + 14.0
            draw_rect(margin_x, y - box_h + 6.0, page_w - 2 * margin_x, box_h, (0.06, 0.09, 0.16))
            for idx, part in enumerate(wrapped):
                draw_text(margin_x + 10.0, y - 12.0 - idx * 16.0, part, font="F2", size=13.5, rgb=(0.98, 0.98, 1.0))
            y -= box_h + 8.0
            continue

        if line.startswith("## "):
            ensure_space(28.0)
            y -= 6.0
            sec_text = strip_md_inline(line[3:])
            draw_rect(margin_x, y - 4.0, page_w - 2 * margin_x, 18.0, (0.94, 0.96, 0.98))
            draw_rect(margin_x, y - 4.0, 3.0, 18.0, (0.01, 0.52, 0.78))
            draw_text(margin_x + 8.0, y + 1.0, sec_text.upper(), font="F2", size=10.5, rgb=(0.06, 0.09, 0.16))
            y -= 20.0
            continue

        if line.startswith("### "):
            ensure_space(20.0)
            y -= 3.0
            sub_text = strip_md_inline(line[4:])
            draw_text(margin_x, y, sub_text, font="F2", size=9.8, rgb=(0.01, 0.41, 0.63))
            y -= 14.0
            continue

        if line.startswith("|"):
            if ":---" in line or "---:" in line:
                continue
            cells = [strip_md_inline(c) for c in line.strip("|").split("|")]
            ensure_space(15.0)
            col_w = (page_w - 2 * margin_x) / max(1, len(cells))
            is_header = any(h in cells[0].lower() for h in ("metric", "layer", "package", "file"))
            if is_header:
                draw_rect(margin_x, y - 3.5, page_w - 2 * margin_x, 13.5, (0.91, 0.94, 0.97))
            for idx, cell in enumerate(cells):
                max_chars = max(8, int(col_w / 5.2))
                clipped = (cell[: max_chars - 2] + "..") if len(cell) > max_chars else cell
                draw_text(
                    margin_x + idx * col_w + 4.0,
                    y,
                    clipped,
                    font="F2" if is_header else "F3",
                    size=8.2,
                    rgb=(0.06, 0.09, 0.16) if is_header else (0.18, 0.22, 0.30),
                )
            y -= 13.5
            continue

        is_bullet = line.startswith("- ") or line.startswith("* ")
        clean_line = strip_md_inline(line[2:] if is_bullet else line)
        prefix = "- " if is_bullet else ""
        wrapped = textwrap.wrap(clean_line, width=88) or [clean_line]
        for idx, part in enumerate(wrapped):
            ensure_space(13.0)
            draw_text(
                margin_x + (10.0 if is_bullet and idx > 0 else 0.0),
                y,
                (prefix if idx == 0 else "") + part,
                font="F1",
                size=9.2,
                rgb=(0.15, 0.19, 0.26),
            )
            y -= 12.5

    total_pages = len(pages)
    for idx, page_cmds in enumerate(pages, start=1):
        footer = f"Code Analyzer Comparison Report | Page {idx} of {total_pages}"
        page_cmds.append(
            f"BT /F1 8.0 Tf 0.450 0.500 0.580 rg {margin_x:.2f} 24.00 Td ({_pdf_escape(footer)}) Tj ET"
        )

    objects: list[bytes] = []

    def add_obj(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add_obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(b"")  # placeholder for Pages object (id 2)
    f1_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    f3_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")

    page_ids: list[int] = []
    for page_cmds in pages:
        stream_bytes = "\n".join(page_cmds).encode("ascii", "replace")
        content_id = add_obj(b"<< /Length " + str(len(stream_bytes)).encode("ascii") + b" >>\nstream\n" + stream_bytes + b"\nendstream")
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w:.2f} {page_h:.2f}] "
            f"/Resources << /Font << /F1 {f1_id} 0 R /F2 {f2_id} 0 R /F3 {f3_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")
        page_ids.append(add_obj(page_dict))

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = [0]
    for obj_id, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{obj_id} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)
