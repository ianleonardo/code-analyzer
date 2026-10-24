"""Feature catalogue per repository: user-facing capabilities mapped to entry points by patterns.

The LLM (or a deterministic grouping by route segment / folder) proposes features from the entry points of a stored
analysis. This happens automatically when an analysis of the default branch completes (and the first time the catalogue of
an older repository is opened); users confirm, rename or edit the proposals, and confirmed features are never replaced, so
the mapping stays stable across pull requests. Patterns:
  {"type": "route", "value": "/payments"}     endpoint URL prefix (or glob)
  {"type": "path",  "value": "src/pay/**"}    file glob of the entry point
  {"type": "item",  "value": "endpoint:..."}  one catalogue item key
  {"type": "name",  "value": "regex"}         entry point name
"""

from __future__ import annotations

import json
import uuid

from app import db
from app.errors import AppError
from app.permissions import require
from app.services import pr_logic
from app.services.analyzer.impact import ENTRY_KINDS, all_entries, match_features

PATTERN_TYPES = ("route", "path", "item", "name")


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _payload(row: dict) -> dict:
    return {**{k: row[k] for k in ("feature_id", "repo_id", "name", "description", "source", "confirmed", "created_at", "updated_at")},
            "patterns": _loads(row["patterns_json"], [])}


def all_for(repo_id: str) -> list[dict]:
    return [_payload(r) for r in db.fetchall("SELECT * FROM features WHERE repo_id = ? ORDER BY name", [repo_id])]


def _repo(repo_id: str) -> dict:
    row = db.fetchone("SELECT * FROM repositories WHERE repo_id = ?", [repo_id])
    if row is None:
        raise AppError(404, "Repository not found")
    return row


def _latest_analysis(repo_id: str) -> str:
    row = db.fetchone(
        "SELECT analysis_id FROM analyses WHERE repo_id = ? AND status = 'completed' ORDER BY coalesce(purpose, 'branch') = 'branch' DESC, finished_at DESC LIMIT 1",
        [repo_id],
    )
    return row["analysis_id"] if row else ""


def entry_items(analysis_id: str) -> list[dict]:
    """Endpoints, jobs, listeners and screens of an analysis: catalogue items plus those found directly in code."""
    from app.services import pr_impact

    side = pr_impact.load_side(analysis_id)
    rows = [i for i in all_entries(side) if i["kind"] in ENTRY_KINDS]
    rows.sort(key=lambda r: (r["kind"], r["name"]))
    return [{**{k: r.get(k, "") for k in ("item_key", "kind", "name", "path", "line_start", "line_end", "layer", "framework", "summary")},
             "line": r.get("line_start", 0), "reach": "", "depth": 0, "symbols": []} for r in rows]


def list_features(user: dict, repo_id: str, analysis_id: str = "") -> dict:
    """Features with the entry points each one selects in the given (or latest) analysis, and the unassigned rest."""
    require(user, "repositories", "read")
    _repo(repo_id)
    features = all_for(repo_id)
    analysis_id = analysis_id or _latest_analysis(repo_id)
    if not features and analysis_id:
        # Repositories analysed before automatic building: build the catalogue the first time it is opened.
        rebuild(repo_id, analysis_id, user["user_id"])
        features = all_for(repo_id)
    entries = entry_items(analysis_id) if analysis_id else []
    match_features(entries, features)
    counts: dict[str, int] = {}
    for entry in entries:
        for fid in entry["feature_ids"]:
            counts[fid] = counts.get(fid, 0) + 1
    for feature in features:
        feature["entry_count"] = counts.get(feature["feature_id"], 0)
        feature["entries"] = [{"item_key": e["item_key"], "kind": e["kind"], "name": e["name"], "path": e["path"]} for e in entries if feature["feature_id"] in e["feature_ids"]][:200]
    unassigned = [{"item_key": e["item_key"], "kind": e["kind"], "name": e["name"], "path": e["path"]} for e in entries if not e["feature_ids"]]
    return {"analysis_id": analysis_id, "features": features, "unassigned": unassigned[:500], "entry_total": len(entries)}


def _clean(body: dict, current: dict | None = None) -> dict:
    name = str(body.get("name", (current or {}).get("name", "")) or "").strip()[:300]
    if not name:
        raise AppError(400, "Give the feature a name")
    patterns = body.get("patterns", (current or {}).get("patterns", []))
    if not isinstance(patterns, list):
        raise AppError(400, "Patterns must be a list")
    clean = []
    for pattern in patterns[:100]:
        if not isinstance(pattern, dict):
            continue
        ptype, value = str(pattern.get("type") or "path"), str(pattern.get("value") or "").strip()[:500]
        if ptype not in PATTERN_TYPES:
            raise AppError(400, f"Unknown pattern type {ptype}; use route, path, item or name")
        if value:
            clean.append({"type": ptype, "value": value})
    return {"name": name, "description": str(body.get("description", (current or {}).get("description", "")) or "")[:2000], "patterns": clean,
            "confirmed": bool(body.get("confirmed", (current or {}).get("confirmed", True)))}


def create_feature(user: dict, repo_id: str, body: dict) -> dict:
    require(user, "repositories", "write")
    _repo(repo_id)
    data = _clean(body)
    feature_id = str(uuid.uuid4())
    now = db.now()
    db.insert("features", {"feature_id": feature_id, "repo_id": repo_id, "name": data["name"], "description": data["description"],
                           "patterns_json": json.dumps(data["patterns"]), "source": str(body.get("source") or "user")[:10], "confirmed": data["confirmed"],
                           "created_by": user["user_id"], "created_at": now, "updated_at": now})
    return _payload(db.fetchone("SELECT * FROM features WHERE feature_id = ?", [feature_id]))


def _row(feature_id: str) -> dict:
    row = db.fetchone("SELECT * FROM features WHERE feature_id = ?", [feature_id])
    if row is None:
        raise AppError(404, "Feature not found")
    return row


def update_feature(user: dict, feature_id: str, body: dict) -> dict:
    require(user, "repositories", "write")
    current = _payload(_row(feature_id))
    data = _clean(body, current)
    db.exec_sql("UPDATE features SET name = ?, description = ?, patterns_json = ?, confirmed = ?, updated_at = ? WHERE feature_id = ?",
                [data["name"], data["description"], json.dumps(data["patterns"]), data["confirmed"], db.now(), feature_id])
    return _payload(_row(feature_id))


def delete_feature(user: dict, feature_id: str) -> None:
    require(user, "repositories", "write")
    _row(feature_id)
    db.exec_sql("DELETE FROM features WHERE feature_id = ?", [feature_id])


def _insert(repo_id: str, proposal: dict, created_by: str) -> None:
    data = _clean({**proposal, "confirmed": False})
    now = db.now()
    db.insert("features", {"feature_id": str(uuid.uuid4()), "repo_id": repo_id, "name": data["name"], "description": data["description"],
                           "patterns_json": json.dumps(data["patterns"]), "source": str(proposal.get("source") or "rules")[:10], "confirmed": False,
                           "created_by": created_by, "created_at": now, "updated_at": now})


def rebuild(repo_id: str, analysis_id: str, created_by: str) -> dict:
    """Re-proposes the catalogue from an analysis' entry points. Confirmed features (and their names) are kept untouched;
    earlier unconfirmed proposals are replaced. Returns {"created", "kept", "entries"}."""
    entries = entry_items(analysis_id)
    if not entries:
        return {"created": 0, "kept": len(all_for(repo_id)), "entries": 0}
    proposals = pr_logic.suggest_features(entries)
    db.exec_sql("DELETE FROM features WHERE repo_id = ? AND confirmed = FALSE", [repo_id])
    confirmed = all_for(repo_id)
    existing = {f["name"].lower() for f in confirmed}
    # Entry points a confirmed feature already selects: a proposal that only re-selects those (e.g. "Orders" after the
    # user renamed it to "Order lookup") is skipped instead of duplicating the confirmed feature.
    match_features(entries, confirmed)
    covered = {e["item_key"] for e in entries if e["feature_ids"]}
    created = 0
    for index, proposal in enumerate(proposals):
        if proposal["name"].lower() in existing:
            continue
        probe = [dict(e) for e in entries]
        match_features(probe, [{"feature_id": f"p{index}", "patterns": proposal.get("patterns", [])}])
        selects = {e["item_key"] for e in probe if e["feature_ids"]}
        if not selects or selects <= covered:
            continue
        try:
            _insert(repo_id, proposal, created_by)
        except AppError:
            continue
        existing.add(proposal["name"].lower())
        created += 1
    return {"created": created, "kept": len(confirmed), "entries": len(entries)}


def auto_build(repo_id: str, analysis_id: str, created_by: str) -> dict | None:
    """Called when a branch analysis completes. Rebuilds the proposals when the analysis is of the default branch (or the
    repository has no default branch, or no features yet) and it is the repository's latest completed branch analysis.
    Returns None when nothing was done."""
    repo = db.fetchone("SELECT default_branch FROM repositories WHERE repo_id = ?", [repo_id])
    analysis = db.fetchone("SELECT branch, coalesce(purpose, 'branch') AS purpose FROM analyses WHERE analysis_id = ?", [analysis_id])
    if not repo or not analysis or analysis["purpose"] != "branch":
        return None
    has_features = db.fetchone("SELECT feature_id FROM features WHERE repo_id = ? LIMIT 1", [repo_id]) is not None
    default = (repo.get("default_branch") or "").strip()
    on_default = not default or not analysis["branch"] or analysis["branch"] == default
    if has_features and (not on_default or _latest_analysis(repo_id) != analysis_id):
        return None
    return rebuild(repo_id, analysis_id, created_by)


def suggest(user: dict, repo_id: str, body: dict) -> dict:
    """Proposes features (unconfirmed) from the entry points of an analysis. Existing confirmed features are kept."""
    require(user, "repositories", "write")
    _repo(repo_id)
    analysis_id = str(body.get("analysis_id") or "") or _latest_analysis(repo_id)
    if not analysis_id:
        raise AppError(409, "Analyse the repository first: features are proposed from its endpoints, jobs, listeners and screens.")
    entries = entry_items(analysis_id)
    if not entries:
        raise AppError(409, "The analysis has no endpoints, jobs, listeners or screens to group into features.")
    created = rebuild(repo_id, analysis_id, user["user_id"])["created"]
    result = list_features(user, repo_id, analysis_id)
    result["created"] = created
    return result


def delete_for_repo(repo_id: str) -> None:
    db.exec_sql("DELETE FROM features WHERE repo_id = ?", [repo_id])
