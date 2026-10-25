"""Settings: LLM providers (Gemini, Spirare), users, user roles, audit log, database information."""

from fastapi import APIRouter, Depends

from app import db
from app.deps import current_user
from app.permissions import require
from app.services import audit, ldap_auth, llm_settings, roles, users

router = APIRouter(tags=["settings"])


@router.get("/settings")
def get_settings(user: dict = Depends(current_user)):
    require(user, "settings", "read")
    return llm_settings.settings_payload()


@router.put("/settings")
def put_settings(body: dict, user: dict = Depends(current_user)):
    return llm_settings.update_settings(user, body)


@router.put("/settings/providers/{provider}")
def put_provider(provider: str, body: dict, user: dict = Depends(current_user)):
    return llm_settings.update_provider(user, provider, body)


@router.post("/settings/providers/{provider}/test")
def test_provider(provider: str, user: dict = Depends(current_user)):
    return llm_settings.test_provider(user, provider)


@router.get("/users")
def list_users(user: dict = Depends(current_user)):
    return users.list_users(user)


@router.get("/users/ldap-domains")
def list_ldap_domains(user: dict = Depends(current_user)):
    require(user, "settings", "read")
    return {"domains": ldap_auth.domains(), "configured": ldap_auth.configured()}


@router.post("/users")
def create_user(body: dict, user: dict = Depends(current_user)):
    return users.create_user(user, body)


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: dict, user: dict = Depends(current_user)):
    return users.update_user(user, user_id, body)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str, user: dict = Depends(current_user)):
    users.delete_user(user, user_id)


@router.get("/roles")
def list_roles(user: dict = Depends(current_user)):
    return roles.list_roles(user)


@router.post("/roles")
def create_role(body: dict, user: dict = Depends(current_user)):
    return roles.create_role(user, body)


@router.get("/roles/{role_id}")
def get_role(role_id: str, user: dict = Depends(current_user)):
    return roles.get_role(user, role_id)


@router.patch("/roles/{role_id}")
def update_role(role_id: str, body: dict, user: dict = Depends(current_user)):
    return roles.update_role(user, role_id, body)


@router.delete("/roles/{role_id}", status_code=204)
def delete_role(role_id: str, user: dict = Depends(current_user)):
    roles.delete_role(user, role_id)


@router.get("/audit")
def audit_log(q: str = "", action: str = "", date_from: str = "", date_to: str = "", limit: int = 100, offset: int = 0, user: dict = Depends(current_user)):
    return audit.list_entries(user, q=q, action=action, date_from=date_from, date_to=date_to, limit=limit, offset=offset)


@router.get("/audit/actions")
def audit_actions(user: dict = Depends(current_user)):
    return audit.actions(user)


@router.post("/audit/purge")
def purge_audit(body: dict, user: dict = Depends(current_user)):
    return audit.purge(
        user,
        str(body.get("date_from") or ""),
        str(body.get("date_to") or ""),
        bool(body.get("dry_run", False)),
    )


@router.get("/database")
def database_info(user: dict = Depends(current_user)):
    require(user, "settings", "read")
    return {**db.describe(), "tables": db.table_counts()}
