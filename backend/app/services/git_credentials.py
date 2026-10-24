"""Each user's own connections to GitHub / Bitbucket: personal access tokens or OAuth sign-ins (popup).

Tokens are encrypted at rest and never returned by the API. One connection per user, provider and host.
"""

from __future__ import annotations

import json
import time
import uuid
from urllib.parse import urlencode

import httpx
import jwt

from app import config, db
from app.crypto import decrypt, encrypt
from app.errors import AppError
from app.services import git_hosts
from app.services.git_hosts import BITBUCKET, BITBUCKET_SERVER, GIT, GITHUB, Credential, RepoRef

PROVIDERS = (GITHUB, BITBUCKET, BITBUCKET_SERVER, GIT)


def _public(row: dict) -> dict:
    return {
        "credential_id": row["credential_id"],
        "provider": row["provider"],
        "provider_label": git_hosts.PROVIDER_LABELS.get(row["provider"], row["provider"]),
        "host": row["host"],
        "username": row["username"],
        "account_name": row["account_name"],
        "auth_method": row["auth_method"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_verified_at": row["last_verified_at"],
    }


def list_credentials(user: dict) -> list[dict]:
    rows = db.fetchall("SELECT * FROM git_credentials WHERE user_id = ? ORDER BY provider, host", [user["user_id"]])
    return [_public(row) for row in rows]


def _row_for(user_id: str, provider: str, host: str) -> dict | None:
    return db.fetchone(
        "SELECT * FROM git_credentials WHERE user_id = ? AND provider = ? AND host = ?", [user_id, provider, host.lower()]
    )


def _secret(row: dict) -> dict:
    raw = decrypt(row["token_enc"])
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"token": raw}
    except ValueError:
        return {"token": raw}


def credential_for(user_id: str, ref: RepoRef) -> Credential | None:
    """The user's saved credential for the repository's host, refreshed when an OAuth token has expired."""
    row = _row_for(user_id, ref.provider, ref.host)
    if row is None:
        return None
    secret = _secret(row)
    if not secret.get("token"):
        return None
    if row["auth_method"] == "oauth" and secret.get("refresh_token") and secret.get("expires_at", 0) < time.time() + 60:
        secret = _refresh(row, secret) or secret
    return Credential(token=secret["token"], username=row["username"], auth_method=row["auth_method"])


def _save(user_id: str, provider: str, host: str, username: str, account: str, secret: dict, method: str) -> dict:
    now = db.now()
    existing = _row_for(user_id, provider, host)
    token_enc = encrypt(json.dumps(secret))
    if existing:
        db.exec_sql(
            """
            UPDATE git_credentials SET username = ?, account_name = ?, token_enc = ?, auth_method = ?, updated_at = ?,
                   last_verified_at = ?
            WHERE credential_id = ?
            """,
            [username, account, token_enc, method, now, now, existing["credential_id"]],
        )
        credential_id = existing["credential_id"]
    else:
        credential_id = str(uuid.uuid4())
        db.insert(
            "git_credentials",
            {
                "credential_id": credential_id,
                "user_id": user_id,
                "provider": provider,
                "host": host.lower(),
                "username": username,
                "account_name": account,
                "token_enc": token_enc,
                "auth_method": method,
                "created_at": now,
                "updated_at": now,
                "last_verified_at": now,
            },
        )
    return _public(db.fetchone("SELECT * FROM git_credentials WHERE credential_id = ?", [credential_id]))


def save_token(user: dict, body: dict) -> dict:
    """Sign in with a personal access token / app password / HTTP access token. Verified before it is stored."""
    url = str(body.get("url") or "").strip()
    username = str(body.get("username") or "").strip()
    token = str(body.get("token") or "").strip()
    if not token:
        raise AppError(400, "Enter an access token or app password")
    if url:
        ref = git_hosts.parse_url(url)
        provider, host, base_url = ref.provider, ref.host, ref.base_url
    else:
        provider = str(body.get("provider") or "").strip()
        host = str(body.get("host") or "").strip().lower()
        base_url = str(body.get("base_url") or "").strip().rstrip("/")
        if provider not in PROVIDERS:
            raise AppError(400, "Unknown Git provider")
        if not host:
            host = {GITHUB: "github.com", BITBUCKET: "bitbucket.org"}.get(provider, "")
        if not host:
            raise AppError(400, "Enter the Git host, e.g. bitbucket.mycompany.com")
        ref = None
    credential = Credential(token=token, username=username, auth_method="token")
    if ref is not None:
        # The repository decides: a token scoped only to repositories may not be allowed to read the account.
        access = git_hosts.check_access(ref, credential)
        if access["status"] != "ok" or not access.get("used_credential"):
            hint = ""
            if provider == BITBUCKET:
                hint = (" For a Bitbucket API token, enter your Atlassian account email (not your Bitbucket username) and give the"
                        " token the read:repository:bitbucket scope. For a repository or workspace access token, leave the email blank.")
            raise AppError(400, f"{git_hosts.PROVIDER_LABELS.get(provider, provider)} did not accept these credentials for {ref.name}.{hint}")
        try:
            account = git_hosts.whoami(provider, host, base_url, credential)
        except AppError:
            account = username or "access token"
    else:
        account = git_hosts.whoami(provider, host, base_url, credential)
    return _save(user["user_id"], provider, host, username, account, {"token": token}, "token")


def delete_credential(user: dict, credential_id: str) -> None:
    row = db.fetchone("SELECT * FROM git_credentials WHERE credential_id = ?", [credential_id])
    if row is None or row["user_id"] != user["user_id"]:
        raise AppError(404, "Connection not found")
    db.exec_sql("DELETE FROM git_credentials WHERE credential_id = ?", [credential_id])


def verify_credential(user: dict, credential_id: str) -> dict:
    row = db.fetchone("SELECT * FROM git_credentials WHERE credential_id = ?", [credential_id])
    if row is None or row["user_id"] != user["user_id"]:
        raise AppError(404, "Connection not found")
    secret = _secret(row)
    credential = Credential(token=secret.get("token", ""), username=row["username"], auth_method=row["auth_method"])
    base = f"https://{row['host']}"
    account = git_hosts.whoami(row["provider"], row["host"], base, credential)
    db.exec_sql(
        "UPDATE git_credentials SET account_name = ?, last_verified_at = ? WHERE credential_id = ?",
        [account, db.now(), credential_id],
    )
    return _public(db.fetchone("SELECT * FROM git_credentials WHERE credential_id = ?", [credential_id]))


# --- OAuth popup sign-in ------------------------------------------------------------------------------------------


def oauth_providers() -> dict:
    """Which providers can use the OAuth popup (needs an OAuth app configured by the administrator)."""
    github_id, github_secret = config.github_oauth()
    bb_id, bb_secret = config.bitbucket_oauth()
    return {GITHUB: bool(github_id and github_secret), BITBUCKET: bool(bb_id and bb_secret), BITBUCKET_SERVER: False, GIT: False}


def _callback_url(provider: str) -> str:
    return f"{config.public_base_url()}/api/git/oauth/{provider}/callback"


def oauth_start(user: dict, provider: str, host: str = "") -> dict:
    if not oauth_providers().get(provider):
        raise AppError(400, "OAuth sign-in is not configured for this provider. Use an access token instead.")
    host = (host or {GITHUB: "github.com", BITBUCKET: "bitbucket.org"}[provider]).lower()
    state = jwt.encode(
        {"sub": user["user_id"], "provider": provider, "host": host, "purpose": "git-oauth", "exp": int(time.time()) + 600},
        config.jwt_secret(),
        algorithm="HS256",
    )
    if provider == GITHUB:
        client_id, _ = config.github_oauth()
        origin = "https://github.com" if host == "github.com" else f"https://{host}"
        query = urlencode({"client_id": client_id, "redirect_uri": _callback_url(provider), "scope": "repo read:user", "state": state})
        return {"authorize_url": f"{origin}/login/oauth/authorize?{query}"}
    client_id, _ = config.bitbucket_oauth()
    query = urlencode({"client_id": client_id, "response_type": "code", "state": state})
    return {"authorize_url": f"https://bitbucket.org/site/oauth2/authorize?{query}"}


def oauth_finish(provider: str, code: str, state: str) -> dict:
    try:
        claims = jwt.decode(state, config.jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AppError(400, "The sign-in link expired. Start again.") from exc
    if claims.get("purpose") != "git-oauth" or claims.get("provider") != provider:
        raise AppError(400, "Invalid sign-in state")
    host = claims["host"]
    if provider == GITHUB:
        client_id, client_secret = config.github_oauth()
        origin = "https://github.com" if host == "github.com" else f"https://{host}"
        response = httpx.post(
            f"{origin}/login/oauth/access_token",
            data={"client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": _callback_url(provider)},
            headers={"Accept": "application/json"},
            timeout=20,
            verify=config.git_verify_ssl(),
        )
        body = response.json() if response.content else {}
        token = body.get("access_token")
        if not token:
            raise AppError(400, body.get("error_description") or "GitHub did not return a token")
        secret = {"token": token}
    else:
        client_id, client_secret = config.bitbucket_oauth()
        response = httpx.post(
            "https://bitbucket.org/site/oauth2/access_token",
            data={"grant_type": "authorization_code", "code": code},
            auth=(client_id, client_secret),
            timeout=20,
        )
        body = response.json() if response.content else {}
        if not body.get("access_token"):
            raise AppError(400, body.get("error_description") or "Bitbucket did not return a token")
        secret = {
            "token": body["access_token"],
            "refresh_token": body.get("refresh_token", ""),
            "expires_at": time.time() + int(body.get("expires_in") or 3600),
        }
    credential = Credential(token=secret["token"], auth_method="oauth")
    account = git_hosts.whoami(provider, host, f"https://{host}", credential)
    return _save(claims["sub"], provider, host, "", account, secret, "oauth")


def _refresh(row: dict, secret: dict) -> dict | None:
    if row["provider"] != BITBUCKET:
        return None
    client_id, client_secret = config.bitbucket_oauth()
    if not client_id:
        return None
    try:
        response = httpx.post(
            "https://bitbucket.org/site/oauth2/access_token",
            data={"grant_type": "refresh_token", "refresh_token": secret["refresh_token"]},
            auth=(client_id, client_secret),
            timeout=20,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not body.get("access_token"):
        return None
    fresh = {
        "token": body["access_token"],
        "refresh_token": body.get("refresh_token") or secret["refresh_token"],
        "expires_at": time.time() + int(body.get("expires_in") or 3600),
    }
    db.exec_sql(
        "UPDATE git_credentials SET token_enc = ?, updated_at = ? WHERE credential_id = ?",
        [encrypt(json.dumps(fresh)), db.now(), row["credential_id"]],
    )
    return fresh


def token_help(provider: str, host: str) -> dict:
    """Where the user creates a token, shown in the sign-in popup."""
    if provider == GITHUB:
        origin = "https://github.com" if host in ("", "github.com") else f"https://{host}"
        return {
            "url": f"{origin}/settings/tokens/new?scopes=repo&description=Code%20Analyzer",
            "hint": "Create a personal access token with the `repo` scope (classic) or read-only Contents access (fine-grained).",
            "username_required": False,
        }
    if provider == BITBUCKET:
        return {
            "url": "https://id.atlassian.com/manage-profile/security/api-tokens",
            "hint": "Create an API token with scopes (read:repository:bitbucket; add read:user:bitbucket to show your account name) "
                    "and enter your Atlassian account email. A repository or workspace access token also works: leave the email blank.",
            "username_required": False,
            "username_label": "Atlassian account email",
        }
    if provider == BITBUCKET_SERVER:
        return {
            "url": f"https://{host}/plugins/servlet/access-tokens/manage" if host else "",
            "hint": "Create an HTTP access token with Repository read permission. Your username is optional.",
            "username_required": False,
        }
    return {"url": "", "hint": "Enter the username and access token (or password) for this Git host.", "username_required": True}
