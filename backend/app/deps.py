from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import db
from app.config import jwt_secret
from app.errors import AppError
from app.permissions import check_request, permissions_of

bearer = HTTPBearer(auto_error=False)


def create_token(user: dict, hours: int = 12, **extra) -> str:
    payload = {
        "sub": user["user_id"],
        "role": user.get("role_id"),
        "login_id": user["login_id"],
        "exp": datetime.now(timezone.utc) + timedelta(hours=hours),
        **extra,
    }
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AppError(401, "Not authenticated") from exc


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(401, "Not authenticated")
    payload = decode_token(credentials.credentials)
    if payload.get("purpose"):
        raise AppError(401, "Not authenticated")
    return load_user(payload.get("sub"))


def load_user(user_id) -> dict:
    """The signed-in user with their role's name and permissions."""
    row = db.fetchone(
        """
        SELECT u.user_id, u.username, u.login_id, u.role_id, u.auth_provider, u.created_at, u.last_login_at,
               r.name AS role_name, r.repositories, r.insights, r.settings
        FROM users u LEFT JOIN roles r ON r.role_id = u.role_id
        WHERE u.user_id = ?
        """,
        [user_id],
    )
    if row is None:
        raise AppError(401, "Not authenticated")
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "login_id": row["login_id"],
        "role_id": row["role_id"],
        "role_name": row["role_name"] or row["role_id"],
        "auth_provider": row["auth_provider"],
        "permissions": permissions_of(row),
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


def enforce_access(request: Request, user: dict = Depends(current_user)) -> dict:
    """Router-wide check: the request's area and level (from its path and method) against the user's role."""
    check_request(user, request.method, request.url.path)
    return user
