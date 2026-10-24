"""User roles: none / read / write on Repositories, Insights and Settings."""

import re

from app import db
from app.errors import AppError
from app.permissions import ADMIN_ROLE_ID, AREAS, LEVELS, require


def _payload(row: dict, counts: dict[str, int]) -> dict:
    return {
        "role_id": row["role_id"],
        "name": row["name"],
        "description": row["description"],
        **{area: row[area] for area in AREAS},
        "is_system": bool(row["is_system"]),
        "locked": row["role_id"] == ADMIN_ROLE_ID,
        "user_count": counts.get(row["role_id"], 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _counts() -> dict[str, int]:
    return {row["role_id"]: int(row["c"]) for row in db.fetchall("SELECT role_id, count(*) AS c FROM users GROUP BY role_id")}


def list_roles(user: dict) -> list[dict]:
    require(user, "settings", "read")
    counts = _counts()
    order = "CASE role_id WHEN 'viewer' THEN 1 WHEN 'analyst' THEN 2 WHEN 'administrator' THEN 3 ELSE 4 END"
    return [_payload(row, counts) for row in db.fetchall(f"SELECT * FROM roles ORDER BY {order}, created_at")]


def _row(role_id: str) -> dict:
    row = db.fetchone("SELECT * FROM roles WHERE role_id = ?", [role_id])
    if row is None:
        raise AppError(404, "Role not found")
    return row


def _clean_name(name, role_id: str | None = None) -> str:
    cleaned = " ".join(str(name or "").split())
    if not cleaned:
        raise AppError(400, "Role name is required")
    if len(cleaned) > 50:
        raise AppError(400, "Role name must be 50 characters or fewer")
    clash = db.fetchone("SELECT role_id FROM roles WHERE lower(name) = lower(?)", [cleaned])
    if clash and clash["role_id"] != role_id:
        raise AppError(400, "A role with that name already exists")
    return cleaned


def _clean_description(value) -> str:
    cleaned = " ".join(str(value or "").split())
    if len(cleaned) > 200:
        raise AppError(400, "Description must be 200 characters or fewer")
    return cleaned


def _levels(body: dict, current: dict | None = None) -> dict[str, str]:
    result = {}
    for area in AREAS:
        value = str(body.get(area, (current or {}).get(area, "none")) or "none").strip().lower()
        if value not in LEVELS:
            raise AppError(400, f"{area.title()} access must be none, read or write")
        result[area] = value
    return result


def _new_id(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "role"
    candidate, counter = base, 2
    while db.fetchone("SELECT role_id FROM roles WHERE role_id = ?", [candidate]):
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def get_role(user: dict, role_id: str) -> dict:
    require(user, "settings", "read")
    return _payload(_row(role_id), _counts())


def create_role(user: dict, body: dict) -> dict:
    require(user, "settings", "write")
    name = _clean_name(body.get("name"))
    levels = _levels(body)
    role_id = _new_id(name)
    now = db.now()
    db.insert(
        "roles",
        {
            "role_id": role_id,
            "name": name,
            "description": _clean_description(body.get("description")),
            **levels,
            "is_system": False,
            "created_at": now,
            "updated_at": now,
        },
    )
    return get_role(user, role_id)


def _settings_managers_outside(role_id: str) -> int:
    return int(
        db.scalar(
            "SELECT count(*) AS c FROM users u JOIN roles r ON r.role_id = u.role_id WHERE r.settings = 'write' AND r.role_id <> ?",
            [role_id],
        )
        or 0
    )


def update_role(user: dict, role_id: str, body: dict) -> dict:
    require(user, "settings", "write")
    row = _row(role_id)
    if role_id == ADMIN_ROLE_ID:
        raise AppError(400, "The Administrator role cannot be changed")
    name = _clean_name(body["name"], role_id) if "name" in body else row["name"]
    description = _clean_description(body["description"]) if "description" in body else row["description"]
    levels = _levels(body, row)
    in_use = _counts().get(role_id, 0)
    if row["settings"] == "write" and levels["settings"] != "write" and in_use and _settings_managers_outside(role_id) == 0:
        raise AppError(400, "At least one user must keep a role that can manage settings")
    db.exec_sql(
        "UPDATE roles SET name = ?, description = ?, repositories = ?, insights = ?, settings = ?, updated_at = ? WHERE role_id = ?",
        [name, description, levels["repositories"], levels["insights"], levels["settings"], db.now(), role_id],
    )
    return get_role(user, role_id)


def delete_role(user: dict, role_id: str) -> None:
    require(user, "settings", "write")
    row = _row(role_id)
    if row["is_system"]:
        raise AppError(400, "Predefined roles cannot be deleted")
    in_use = _counts().get(role_id, 0)
    if in_use:
        raise AppError(400, f"{in_use} user(s) have this role. Give them another role first")
    db.exec_sql("DELETE FROM roles WHERE role_id = ?", [role_id])
