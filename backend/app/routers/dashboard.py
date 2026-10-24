import json

from fastapi import APIRouter, Depends

from app import db
from app.deps import current_user
from app.permissions import can
from app.services import llm

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard(user: dict = Depends(current_user)):
    payload = {"llm_configured": llm.configured(), "llm_provider": llm.provider_name(), "embedding_configured": llm.embedding_configured(), "database": db.describe()}
    if not can(user, "repositories", "read"):
        return payload
    counts = {row["status"]: int(row["c"]) for row in db.fetchall("SELECT status, count(*) AS c FROM analyses GROUP BY status")}
    recent = db.fetchall(
        """
        SELECT a.analysis_id, a.repo_id, r.name AS repo_name, r.source_type, a.branch, a.commit_sha, a.status, a.stage, a.progress, a.message,
               a.health_score, a.file_count, a.loc_total, a.architecture_style, a.created_at, a.finished_at, a.stats_json
        FROM analyses a JOIN repositories r ON r.repo_id = a.repo_id ORDER BY a.created_at DESC LIMIT 12
        """
    )
    for row in recent:
        stats = json.loads(row.pop("stats_json") or "{}")
        row["languages"] = list((stats.get("languages") or {}).keys())[:3]
        row["concerns"] = stats.get("concerns_by_severity", {})
    completed = db.fetchall("SELECT health_score, loc_total, file_count FROM analyses WHERE status = 'completed'")
    payload.update(
        repositories=int(db.scalar("SELECT count(*) AS c FROM repositories") or 0),
        analyses=counts,
        running=db.fetchall(
            "SELECT a.analysis_id, r.name AS repo_name, a.branch, a.status, a.stage, a.progress, a.message FROM analyses a JOIN repositories r ON r.repo_id = a.repo_id WHERE a.status IN ('queued', 'running') ORDER BY a.created_at"
        ),
        recent=recent,
        total_loc=sum(int(row["loc_total"] or 0) for row in completed),
        avg_health=round(sum(float(row["health_score"] or 0) for row in completed) / len(completed), 1) if completed else None,
        comparisons=int(db.scalar("SELECT count(*) AS c FROM comparisons") or 0),
        chats=int(db.scalar("SELECT count(*) AS c FROM chat_sessions WHERE user_id = ?", [user["user_id"]]) or 0),
    )
    return payload
