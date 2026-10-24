"""Remote repository access: check access, list branches, and each user's own GitHub / Bitbucket connections."""

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.deps import current_user
from app.errors import AppError
from app.services import git_credentials, git_hosts

router = APIRouter(prefix="/git", tags=["git"])
public = APIRouter(prefix="/git", tags=["git"])


@router.post("/check")
def check(body: dict, user: dict = Depends(current_user)):
    """Can this user read the repository? Tells the UI when a sign-in popup is needed."""
    ref = git_hosts.parse_url(str(body.get("url") or ""))
    credential = git_credentials.credential_for(user["user_id"], ref)
    result = git_hosts.check_access(ref, credential)
    result["oauth_available"] = git_credentials.oauth_providers().get(ref.provider, False)
    result["token_help"] = git_credentials.token_help(ref.provider, ref.host)
    return result


@router.post("/branches")
def branches(body: dict, user: dict = Depends(current_user)):
    ref = git_hosts.parse_url(str(body.get("url") or ""))
    return git_hosts.list_branches(ref, git_credentials.credential_for(user["user_id"], ref))


@router.get("/credentials")
def list_credentials(user: dict = Depends(current_user)):
    return {"credentials": git_credentials.list_credentials(user), "oauth": git_credentials.oauth_providers()}


@router.post("/credentials")
def save_credential(body: dict, user: dict = Depends(current_user)):
    return git_credentials.save_token(user, body)


@router.post("/credentials/{credential_id}/verify")
def verify_credential(credential_id: str, user: dict = Depends(current_user)):
    return git_credentials.verify_credential(user, credential_id)


@router.delete("/credentials/{credential_id}", status_code=204)
def delete_credential(credential_id: str, user: dict = Depends(current_user)):
    git_credentials.delete_credential(user, credential_id)


@router.post("/oauth/{provider}/start")
def oauth_start(provider: str, body: dict | None = None, user: dict = Depends(current_user)):
    return git_credentials.oauth_start(user, provider, str((body or {}).get("host") or ""))


_POPUP = """<!doctype html><html><head><meta charset="utf-8"><title>Code Analyzer</title>
<style>body{{font:14px Inter,system-ui,sans-serif;background:#0a0d12;color:#e1e2ea;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.box{{border:1px solid #253041;background:#12171f;padding:24px 28px;max-width:420px}} .ok{{color:#00e676}} .bad{{color:#ff1744}}</style></head>
<body><div class="box"><p class="{cls}">{message}</p><p>You can close this window.</p></div>
<script>try{{window.opener&&window.opener.postMessage({payload},window.location.origin)}}catch(e){{}};setTimeout(function(){{window.close()}},{delay});</script>
</body></html>"""


@public.get("/oauth/{provider}/callback", response_class=HTMLResponse)
def oauth_callback(provider: str, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    """The OAuth popup lands here; the result is posted back to the window that opened it."""
    import json

    if error or not code:
        payload = json.dumps({"type": "git-auth", "ok": False, "provider": provider, "message": error_description or error or "Sign-in was cancelled"})
        return HTMLResponse(_POPUP.format(cls="bad", message="Sign-in was not completed.", payload=payload, delay=2500))
    try:
        saved = git_credentials.oauth_finish(provider, code, state)
    except AppError as exc:
        payload = json.dumps({"type": "git-auth", "ok": False, "provider": provider, "message": exc.detail})
        return HTMLResponse(_POPUP.format(cls="bad", message=exc.detail, payload=payload, delay=4000))
    payload = json.dumps({"type": "git-auth", "ok": True, "provider": provider, "account": saved["account_name"]})
    return HTMLResponse(_POPUP.format(cls="ok", message=f"Connected as {saved['account_name']}.", payload=payload, delay=800))
