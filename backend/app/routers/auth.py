from fastapi import APIRouter, Depends, Request

from app.deps import create_token, current_user, load_user
from app.errors import AppError
from app.services import audit, users

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(body: dict, request: Request):
    login_id = str(body.get("login_id") or "")
    address = request.client.host if request.client else None
    try:
        user = users.authenticate(login_id, str(body.get("password") or ""))
    except AppError:
        audit.record(action="login_failed", summary=f"Failed sign-in for '{login_id.strip()[:100]}'", login_id=login_id.strip()[:100] or None,
                     method="POST", path="/api/auth/login", entity_type="user", status=401, ip_address=address)
        raise
    user = load_user(user["user_id"])
    audit.record(action="login", summary="Signed in", user=user, method="POST", path="/api/auth/login", entity_type="user", entity_id=user["user_id"], ip_address=address)
    return {"access_token": create_token(user), "user": user}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return user


@router.post("/password")
def change_password(body: dict, user: dict = Depends(current_user)):
    users.change_own_password(user, str(body.get("current_password") or ""), str(body.get("new_password") or ""))
    return {"ok": True}
