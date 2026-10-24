"""Sign-in and user administration (local password or Active Directory), as in SpecForge."""

import re
import uuid

import bcrypt

from app import db
from app.errors import AppError
from app.permissions import require
from app.services import ldap_auth

LOGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{3,64}$")
UNUSABLE_PASSWORD = "!"


def _password_matches(stored: str, password: str) -> bool:
    if not stored or stored == UNUSABLE_PASSWORD:
        return False
    try:
        return bcrypt.checkpw(password.encode(), stored.encode())
    except ValueError:
        return False


def _role_names() -> dict[str, str]:
    return {row["role_id"]: row["name"] for row in db.fetchall("SELECT role_id, name FROM roles")}


def public_user(row: dict, names: dict[str, str] | None = None) -> dict:
    names = names if names is not None else _role_names()
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "login_id": row["login_id"],
        "role_id": row["role_id"],
        "role_name": names.get(row["role_id"], row["role_id"]),
        "auth_provider": row.get("auth_provider") or "local",
        "ldap_domain": row.get("ldap_domain") or "",
        "created_at": row["created_at"],
        "last_login_at": row.get("last_login_at"),
    }


def authenticate(login_id: str, password: str) -> dict:
    row = db.fetchone("SELECT * FROM users WHERE lower(login_id) = ?", [login_id.strip().lower()])
    if row is None:
        raise AppError(401, "Invalid login ID or password")
    if (row.get("auth_provider") or "local") == "ldap":
        display_name = ldap_auth.authenticate(row["login_id"], password, row.get("ldap_domain") or "").strip()[:100]
        if display_name and display_name != row["username"]:
            db.exec_sql("UPDATE users SET username = ? WHERE user_id = ?", [display_name, row["user_id"]])
    elif not _password_matches(row["password_hash"], password):
        raise AppError(401, "Invalid login ID or password")
    db.exec_sql("UPDATE users SET last_login_at = ? WHERE user_id = ?", [db.now(), row["user_id"]])
    return public_user(db.fetchone("SELECT * FROM users WHERE user_id = ?", [row["user_id"]]))


def list_users(user: dict) -> list[dict]:
    require(user, "settings", "read")
    names = _role_names()
    return [public_user(row, names) for row in db.fetchall("SELECT * FROM users ORDER BY created_at")]


def _provider(value: str) -> str:
    provider = (value or "local").strip().lower()
    if provider in ("local", "ldap"):
        return provider
    raise AppError(400, "Sign-in must be local or Active Directory (LDAP).")


def _known_role(role_id: str) -> str:
    cleaned = (role_id or "").strip()
    if not db.fetchone("SELECT role_id FROM roles WHERE role_id = ?", [cleaned]):
        raise AppError(400, "Unknown role")
    return cleaned


def _manages_settings(role_id: str) -> bool:
    row = db.fetchone("SELECT settings FROM roles WHERE role_id = ?", [role_id])
    return bool(row and row["settings"] == "write")


def _admin_count() -> int:
    return int(
        db.scalar("SELECT count(*) AS c FROM users u JOIN roles r ON r.role_id = u.role_id WHERE r.settings = 'write'") or 0
    )


def _ldap_domain(value: str) -> str:
    allowed = ldap_auth.domains()
    cleaned = (value or "").strip()
    if not cleaned:
        return allowed[0] if allowed else ""
    if allowed and cleaned not in allowed:
        raise AppError(400, f"LDAP domain must be one of: {', '.join(allowed)}")
    return cleaned


def create_user(actor: dict, body: dict) -> dict:
    require(actor, "settings", "write")
    login_id = str(body.get("login_id") or "").strip()
    password = str(body.get("password") or "")
    username = str(body.get("username") or "").strip()
    role_id = _known_role(str(body.get("role_id") or ""))
    provider = _provider(str(body.get("auth_provider") or "local"))
    if not LOGIN_ID_PATTERN.fullmatch(login_id):
        raise AppError(400, "Login ID must be 3-64 letters or numbers, with no spaces or special characters")
    if db.fetchone("SELECT user_id FROM users WHERE lower(login_id) = ?", [login_id.lower()]):
        raise AppError(400, "A user with that Login ID already exists")
    if provider == "ldap":
        if password.strip():
            raise AppError(400, "Active Directory users sign in through LDAP. Do not set a password.")
        if not ldap_auth.configured():
            raise AppError(400, "LDAP is not configured. Set LDAP_HOST in the environment.")
        ldap_domain = _ldap_domain(str(body.get("ldap_domain") or ""))
        hashed = UNUSABLE_PASSWORD
    else:
        if len(password) < 8:
            raise AppError(400, "Password must be at least 8 characters")
        ldap_domain = ""
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    if len(username) > 100:
        raise AppError(400, "Name must be 100 characters or fewer")
    user_id = str(uuid.uuid4())
    db.insert(
        "users",
        {
            "user_id": user_id,
            "username": username or login_id,
            "login_id": login_id,
            "password_hash": hashed,
            "role_id": role_id,
            "auth_provider": provider,
            "ldap_domain": ldap_domain,
            "created_at": db.now(),
        },
    )
    return public_user(db.fetchone("SELECT * FROM users WHERE user_id = ?", [user_id]))


def update_user(actor: dict, user_id: str, body: dict) -> dict:
    """Display name, role, sign-in method and password can change. The Login ID is fixed."""
    require(actor, "settings", "write")
    row = db.fetchone("SELECT * FROM users WHERE user_id = ?", [user_id])
    if row is None:
        raise AppError(404, "User not found")
    role_id = _known_role(str(body.get("role_id") or row["role_id"]))
    username = str(body.get("username", row["username"]) or "").strip()
    if not username:
        raise AppError(400, "Name is required")
    if len(username) > 100:
        raise AppError(400, "Name must be 100 characters or fewer")
    if _manages_settings(row["role_id"]) and not _manages_settings(role_id) and _admin_count() <= 1:
        raise AppError(400, "The last user who can manage settings cannot lose that role")
    current_provider = (row.get("auth_provider") or "local").lower()
    provider = _provider(str(body["auth_provider"])) if body.get("auth_provider") else current_provider
    new_password = str(body.get("password") or "").strip()
    if provider == "ldap":
        if new_password:
            raise AppError(400, "Active Directory users sign in through LDAP. Do not set a password.")
        if not ldap_auth.configured():
            raise AppError(400, "LDAP is not configured. Set LDAP_HOST in the environment.")
        ldap_domain = _ldap_domain(str(body.get("ldap_domain") or row.get("ldap_domain") or ""))
        db.exec_sql(
            "UPDATE users SET username = ?, role_id = ?, auth_provider = ?, ldap_domain = ?, password_hash = ? WHERE user_id = ?",
            [username, role_id, provider, ldap_domain, UNUSABLE_PASSWORD, user_id],
        )
    elif current_provider == "ldap" and not new_password:
        raise AppError(400, "Set a password when changing this user to a local sign-in.")
    elif new_password:
        if len(new_password) < 8:
            raise AppError(400, "Password must be at least 8 characters")
        hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        db.exec_sql(
            "UPDATE users SET username = ?, role_id = ?, auth_provider = ?, ldap_domain = '', password_hash = ? WHERE user_id = ?",
            [username, role_id, provider, hashed, user_id],
        )
    else:
        db.exec_sql("UPDATE users SET username = ?, role_id = ?, ldap_domain = '' WHERE user_id = ?", [username, role_id, user_id])
    return public_user(db.fetchone("SELECT * FROM users WHERE user_id = ?", [user_id]))


def delete_user(actor: dict, user_id: str) -> None:
    require(actor, "settings", "write")
    if actor["user_id"] == user_id:
        raise AppError(400, "You cannot delete your own account")
    row = db.fetchone("SELECT * FROM users WHERE user_id = ?", [user_id])
    if row is None:
        raise AppError(404, "User not found")
    if _manages_settings(row["role_id"]) and _admin_count() <= 1:
        raise AppError(400, "The last user who can manage settings cannot be deleted")
    with db.transaction():
        db.exec_sql("DELETE FROM git_credentials WHERE user_id = ?", [user_id])
        db.exec_sql("DELETE FROM users WHERE user_id = ?", [user_id])


def change_own_password(actor: dict, current: str, new: str) -> None:
    row = db.fetchone("SELECT * FROM users WHERE user_id = ?", [actor["user_id"]])
    if row is None:
        raise AppError(404, "User not found")
    if (row.get("auth_provider") or "local") == "ldap":
        raise AppError(400, "Active Directory users change their password in the directory.")
    if not _password_matches(row["password_hash"], current):
        raise AppError(400, "The current password is not correct")
    if len(new) < 8:
        raise AppError(400, "Password must be at least 8 characters")
    db.exec_sql(
        "UPDATE users SET password_hash = ? WHERE user_id = ?",
        [bcrypt.hashpw(new.encode(), bcrypt.gensalt()).decode(), actor["user_id"]],
    )
