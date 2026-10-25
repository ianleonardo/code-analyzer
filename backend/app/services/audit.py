"""Audit log: who changed what, and when. Written by AuditMiddleware and the login route."""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta

from app import db
from app.errors import AppError
from app.permissions import require

logger = logging.getLogger(__name__)

SENSITIVE = re.compile(r"password|secret|api_?key|token|authorization|credential", re.I)
LABEL_KEYS = ("name", "title", "username", "login_id", "url", "branch", "provider", "active_provider")


def sanitize(value, depth: int = 0):
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {
            key: ("***" if SENSITIVE.search(str(key)) else sanitize(item, depth + 1))
            for key, item in list(value.items())[:40]
        }
    if isinstance(value, list):
        return [sanitize(item, depth + 1) for item in value[:20]]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "…"
    return value


def label_of(body: dict | None) -> str:
    for key in LABEL_KEYS:
        if body and isinstance(body.get(key), str) and body[key].strip():
            return body[key].strip()[:120]
    return ""


def record(
    *,
    action: str,
    summary: str = "",
    user: dict | None = None,
    login_id: str | None = None,
    method: str = "",
    path: str = "",
    entity_type: str = "",
    entity_id: str = "",
    status: int = 200,
    ip_address: str | None = None,
    detail: dict | None = None,
) -> None:
    try:
        db.insert(
            "audit_log",
            {
                "audit_id": str(uuid.uuid4()),
                "ts": db.now(),
                "user_id": (user or {}).get("user_id"),
                "login_id": (user or {}).get("login_id") or login_id,
                "action": action[:80],
                "method": method,
                "path": path[:1000],
                "entity_type": entity_type[:60],
                "entity_id": str(entity_id or "")[:200],
                "summary": summary[:2000],
                "status": status,
                "ip_address": ip_address,
                "detail_json": json.dumps(detail or {}, default=str)[:20000],
            },
        )
    except Exception:  # noqa: BLE001 - auditing must never break a request
        logger.exception("Could not write the audit log")


def _parse_date(value: str | None, end: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        day = datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError as exc:
        raise AppError(400, "Dates must be YYYY-MM-DD") from exc
    return day + timedelta(days=1) if end else day


def list_entries(user: dict, *, q: str = "", action: str = "", date_from: str = "", date_to: str = "", limit: int = 200, offset: int = 0) -> dict:
    require(user, "settings", "read")
    where, params = ["1=1"], []
    if q.strip():
        where.append("(lower(summary) LIKE ? OR lower(coalesce(login_id, '')) LIKE ? OR lower(path) LIKE ?)")
        needle = f"%{q.strip().lower()}%"
        params += [needle, needle, needle]
    if action:
        where.append("action = ?")
        params.append(action)
    start, end = _parse_date(date_from), _parse_date(date_to, end=True)
    if start:
        where.append("ts >= ?")
        params.append(start)
    if end:
        where.append("ts < ?")
        params.append(end)
    clause = " AND ".join(where)
    total = int(db.scalar(f"SELECT count(*) AS c FROM audit_log WHERE {clause}", params) or 0)
    limit = max(1, min(int(limit), 1000))
    rows = db.fetchall(
        f"SELECT * FROM audit_log WHERE {clause} ORDER BY ts DESC LIMIT {limit} OFFSET {max(0, int(offset))}", params
    )
    for row in rows:
        try:
            row["detail"] = json.loads(row.pop("detail_json") or "{}")
        except ValueError:
            row["detail"] = {}
    return {"total": total, "items": rows}


def actions(user: dict) -> list[str]:
    require(user, "settings", "read")
    return [row["action"] for row in db.fetchall("SELECT DISTINCT action FROM audit_log ORDER BY action")]


def purge(user: dict, date_from: str, date_to: str, dry_run: bool = False) -> dict:
    require(user, "settings", "write")
    start, end = _parse_date(date_from), _parse_date(date_to, end=True)
    if not start and not end:
        raise AppError(400, "Choose a date range to delete")
    where, params = ["1=1"], []
    if start:
        where.append("ts >= ?")
        params.append(start)
    if end:
        where.append("ts < ?")
        params.append(end)
    clause = " AND ".join(where)
    count = int(db.scalar(f"SELECT count(*) AS c FROM audit_log WHERE {clause}", params) or 0)
    if not dry_run and count > 0:
        db.exec_sql(f"DELETE FROM audit_log WHERE {clause}", params)
    return {"matched": count, "deleted": 0 if dry_run else count, "skipped_active": 0, "dry_run": dry_run}
