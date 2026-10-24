"""Writes an audit entry for every request that changes data and succeeded."""

import json

import jwt
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import jwt_secret
from app.services import audit

CHANGING = {"POST", "PUT", "PATCH", "DELETE"}
# Changing requests that change nothing worth tracing.
IGNORED_SUFFIXES = ("/auth/login", "/git/check", "/git/branches", "/messages")
BODY_LIMIT = 64_000


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return ""


def _claims(scope: Scope) -> dict:
    scheme, _, token = _header(scope, b"authorization").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return {}
    try:
        return jwt.decode(token, jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return {}


class AuditMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in CHANGING or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        if scope["path"].endswith(IGNORED_SUFFIXES):
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0

        async def receive_wrapper() -> Message:
            nonlocal size
            message = await receive()
            if message["type"] == "http.request" and size < BODY_LIMIT:
                body = message.get("body", b"")
                chunks.append(body[: BODY_LIMIT - size])
                size += len(body)
            return message

        status = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        await self.app(scope, receive_wrapper, send_wrapper)
        if status["code"] >= 400:
            return
        body = None
        if "json" in _header(scope, b"content-type"):
            try:
                parsed = json.loads(b"".join(chunks) or b"{}")
                body = parsed if isinstance(parsed, dict) else None
            except ValueError:
                body = None
        claims = _claims(scope)
        route = scope.get("route")
        name = getattr(route, "name", None) or scope["method"].lower()
        parts = scope["path"].split("/api/", 1)[-1].strip("/").split("/")
        entity_type = parts[0] if parts else ""
        entity_id = parts[1] if len(parts) > 1 else ""
        label = audit.label_of(body)
        summary = f"{name.replace('_', ' ').capitalize()}{': ' + label if label else ''}"
        client = scope.get("client")
        await run_in_threadpool(
            audit.record,
            action=name,
            summary=summary,
            user={"user_id": claims.get("sub"), "login_id": claims.get("login_id")} if claims else None,
            method=scope["method"],
            path=scope["path"],
            entity_type=entity_type,
            entity_id=entity_id,
            status=status["code"],
            ip_address=client[0] if client else None,
            detail=audit.sanitize(body) if body else None,
        )
