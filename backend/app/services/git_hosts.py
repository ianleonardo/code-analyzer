"""GitHub, Bitbucket Cloud, Bitbucket Server/Data Center and plain Git remotes.

parse_url()      turns any clone or browse URL into a RepoRef (provider, host, owner, slug, clone URL, API base).
check_access()   tells whether the signed-in user can read the repository, anonymously or with a saved connection,
                 and when they cannot, whether signing in to the host would help (`auth_required`).
list_branches()  lists branches (and the default branch) so an analysis can target master, release/* and so on.
clone()          shallow-clones one branch with the user's credentials passed per command, never written to disk.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from app import config
from app.errors import AppError

GITHUB = "github"
BITBUCKET = "bitbucket"  # Bitbucket Cloud (bitbucket.org)
BITBUCKET_SERVER = "bitbucket_server"  # Bitbucket Server / Data Center
GIT = "git"

PROVIDER_LABELS = {GITHUB: "GitHub", BITBUCKET: "Bitbucket Cloud", BITBUCKET_SERVER: "Bitbucket Server", GIT: "Git"}


@dataclass
class RepoRef:
    provider: str
    host: str
    owner: str
    slug: str
    clone_url: str
    web_url: str
    api_base: str
    base_url: str = ""  # Bitbucket Server: scheme://host[/context]

    @property
    def name(self) -> str:
        return f"{self.owner}/{self.slug}" if self.owner else self.slug

    def to_dict(self) -> dict:
        data = asdict(self)
        data["name"] = self.name
        data["provider_label"] = PROVIDER_LABELS.get(self.provider, self.provider)
        return data


@dataclass
class Credential:
    token: str
    username: str = ""
    auth_method: str = "token"  # token (basic user:token or bearer), oauth

    def http_headers(self, provider: str) -> dict:
        # Bitbucket Cloud API tokens: REST basic auth is Atlassian account email + token.
        # Repository / workspace access tokens (no username) and OAuth tokens are Bearer tokens.
        if self.auth_method == "oauth" or not self.username:
            return {"Authorization": f"Bearer {self.token}"}
        raw = base64.b64encode(f"{self.username}:{self.token}".encode()).decode()
        return {"Authorization": f"Basic {raw}"}

    def git_header(self, provider: str) -> str:
        """The Authorization header git sends over HTTPS."""
        if provider == GITHUB:
            user = self.username if (self.username and self.auth_method != "oauth") else "x-access-token"
            raw = base64.b64encode(f"{user}:{self.token}".encode()).decode()
            return f"Authorization: Basic {raw}"
        if provider == BITBUCKET:
            if self.auth_method == "oauth" or not self.username:
                user = "x-token-auth"  # OAuth and repository / workspace access tokens
            elif "@" in self.username:
                user = "x-bitbucket-api-token-auth"  # Atlassian API token (git does not take the email)
            else:
                user = self.username  # legacy app password
            raw = base64.b64encode(f"{user}:{self.token}".encode()).decode()
            return f"Authorization: Basic {raw}"
        if not self.username:
            return f"Authorization: Bearer {self.token}"
        raw = base64.b64encode(f"{self.username}:{self.token}".encode()).decode()
        return f"Authorization: Basic {raw}"


# --- URL parsing --------------------------------------------------------------------------------------------------

_SSH = re.compile(r"^(?:ssh://)?(?:[\w.-]+@)?([\w.-]+)(?::\d+)?[:/](.+?)$")


def _normalise(url: str) -> str:
    text = (url or "").strip()
    if not text:
        raise AppError(400, "Enter a repository URL")
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("ssh://") or re.match(r"^[\w.-]+@[\w.-]+:", text):
        match = _SSH.match(text)
        if match:
            host, path = match.group(1), match.group(2)
            if path.startswith("scm/"):
                return f"https://{host}/{path}"
            return f"https://{host}/{path}"
    if re.match(r"^[\w.-]+\.[a-z]{2,}/", text):
        return f"https://{text}"
    raise AppError(400, "Enter an https:// or git@ repository URL")


def _strip_git(value: str) -> str:
    return value[:-4] if value.endswith(".git") else value


def parse_url(url: str) -> RepoRef:
    normalised = _normalise(url)
    parsed = urlparse(normalised)
    host = (parsed.hostname or "").lower()
    if not host:
        raise AppError(400, "The repository URL has no host")
    port = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
    origin = f"{parsed.scheme}://{host}{port}"
    parts = [part for part in parsed.path.split("/") if part]

    if host == "github.com" or host in config.github_enterprise_hosts():
        if len(parts) < 2:
            raise AppError(400, "A GitHub URL needs an owner and a repository, e.g. https://github.com/org/repo")
        owner, slug = parts[0], _strip_git(parts[1])
        api = "https://api.github.com" if host == "github.com" else f"{origin}/api/v3"
        return RepoRef(GITHUB, host, owner, slug, f"{origin}/{owner}/{slug}.git", f"{origin}/{owner}/{slug}", api, origin)

    if host == "bitbucket.org":
        if len(parts) < 2:
            raise AppError(400, "A Bitbucket URL needs a workspace and a repository, e.g. https://bitbucket.org/team/repo")
        owner, slug = parts[0], _strip_git(parts[1])
        return RepoRef(
            BITBUCKET, host, owner, slug, f"https://bitbucket.org/{owner}/{slug}.git", f"https://bitbucket.org/{owner}/{slug}",
            "https://api.bitbucket.org/2.0", "https://bitbucket.org",
        )

    lowered = [part.lower() for part in parts]
    server_hint = host in config.bitbucket_server_hosts()
    if "scm" in lowered:
        index = lowered.index("scm")
        if len(parts) >= index + 3:
            context = "/".join(parts[:index])
            base = origin + (f"/{context}" if context else "")
            owner, slug = parts[index + 1], _strip_git(parts[index + 2])
            return _server_ref(host, base, owner, slug)
    if "projects" in lowered and "repos" in lowered:
        p_index, r_index = lowered.index("projects"), lowered.index("repos")
        if r_index == p_index + 2 and len(parts) > r_index + 1:
            context = "/".join(parts[:p_index])
            base = origin + (f"/{context}" if context else "")
            return _server_ref(host, base, parts[p_index + 1], _strip_git(parts[r_index + 1]))
    if "users" in lowered and "repos" in lowered:
        u_index, r_index = lowered.index("users"), lowered.index("repos")
        if r_index == u_index + 2 and len(parts) > r_index + 1:
            context = "/".join(parts[:u_index])
            base = origin + (f"/{context}" if context else "")
            return _server_ref(host, base, f"~{parts[u_index + 1]}", _strip_git(parts[r_index + 1]))
    if server_hint and len(parts) >= 2:
        return _server_ref(host, origin, parts[-2], _strip_git(parts[-1]))

    slug = _strip_git(parts[-1]) if parts else host
    owner = parts[-2] if len(parts) >= 2 else ""
    clone_url = normalised if normalised.endswith(".git") else normalised.rstrip("/")
    return RepoRef(GIT, host, owner, slug, clone_url, normalised.removesuffix(".git"), "", origin)


def _server_ref(host: str, base: str, project: str, slug: str) -> RepoRef:
    if project.startswith("~"):
        web = f"{base}/users/{project[1:]}/repos/{slug}/browse"
    else:
        web = f"{base}/projects/{project}/repos/{slug}/browse"
    return RepoRef(
        BITBUCKET_SERVER, host, project, slug, f"{base}/scm/{project.lower()}/{slug}.git", web, f"{base}/rest/api/1.0", base,
    )


# --- HTTP helpers -------------------------------------------------------------------------------------------------


def _client() -> httpx.Client:
    return httpx.Client(timeout=20, verify=config.git_verify_ssl(), follow_redirects=True)


def _get(url: str, credential: Credential | None, provider: str, params: dict | None = None) -> httpx.Response:
    headers = {"Accept": "application/json", "User-Agent": "CodeAnalyzer"}
    if provider == GITHUB:
        headers["Accept"] = "application/vnd.github+json"
    if credential:
        headers.update(credential.http_headers(provider))
    try:
        with _client() as client:
            return client.get(url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        raise AppError(502, f"Could not reach {urlparse(url).hostname}: {exc.__class__.__name__}") from exc


def _repo_api_url(ref: RepoRef) -> str:
    if ref.provider == GITHUB:
        return f"{ref.api_base}/repos/{quote(ref.owner)}/{quote(ref.slug)}"
    if ref.provider == BITBUCKET:
        return f"{ref.api_base}/repositories/{quote(ref.owner)}/{quote(ref.slug)}"
    if ref.provider == BITBUCKET_SERVER:
        if ref.owner.startswith("~"):
            return f"{ref.api_base}/users/{quote(ref.owner[1:])}/repos/{quote(ref.slug)}"
        return f"{ref.api_base}/projects/{quote(ref.owner)}/repos/{quote(ref.slug)}"
    return ""


def _repo_facts(ref: RepoRef, body: dict) -> dict:
    if ref.provider == GITHUB:
        return {
            "default_branch": body.get("default_branch") or "",
            "is_private": bool(body.get("private")),
            "description": body.get("description") or "",
        }
    if ref.provider == BITBUCKET:
        return {
            "default_branch": ((body.get("mainbranch") or {}).get("name")) or "",
            "is_private": bool(body.get("is_private")),
            "description": body.get("description") or "",
        }
    if ref.provider == BITBUCKET_SERVER:
        return {"default_branch": "", "is_private": not bool(body.get("public")), "description": body.get("description") or ""}
    return {"default_branch": "", "is_private": False, "description": ""}


def _ls_remote(ref: RepoRef, credential: Credential | None, args: list[str], patterns: list[str] | None = None) -> subprocess.CompletedProcess:
    if not shutil.which("git"):
        raise AppError(500, "git is not installed on the server")
    command = ["git", *(_git_config(ref, credential)), "ls-remote", *args, ref.clone_url, *(patterns or [])]
    return subprocess.run(command, capture_output=True, text=True, timeout=60, env=_git_env())


def _git_env() -> dict:
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo", "SSH_ASKPASS": "echo", "GCM_INTERACTIVE": "never"})
    return env


def _git_config(ref: RepoRef, credential: Credential | None) -> list[str]:
    options = ["-c", "credential.helper=", "-c", "core.askPass="]
    if not config.git_verify_ssl():
        options += ["-c", "http.sslVerify=false"]
    if credential:
        options += ["-c", f"http.extraHeader={credential.git_header(ref.provider)}"]
    return options


# --- access check -------------------------------------------------------------------------------------------------


def check_access(ref: RepoRef, credential: Credential | None) -> dict:
    """status: ok | auth_required | no_access | not_found | error."""
    result = {"repo": ref.to_dict(), "has_credential": credential is not None, "status": "error", "message": "", "used_credential": False}
    if ref.provider == GIT:
        return _check_plain_git(ref, credential, result)
    url = _repo_api_url(ref)
    attempts: list[Credential | None] = [None]
    if credential:
        attempts.insert(0, credential)
    last = None
    for attempt in attempts:
        response = _get(url, attempt, ref.provider)
        last = response
        if response.status_code == 200:
            body = response.json() if response.content else {}
            result.update(_repo_facts(ref, body))
            if ref.provider == BITBUCKET_SERVER and not result["default_branch"]:
                result["default_branch"] = _server_default_branch(ref, attempt)
            result.update(status="ok", used_credential=attempt is not None, message="Repository is reachable.")
            if attempt is None:
                result["message"] = "Public repository; reachable without signing in."
            return result
        if response.status_code == 403 and ref.provider == GITHUB and "rate limit" in response.text.lower():
            result.update(status="error", message="GitHub rate limit reached. Sign in to GitHub to continue.")
            result["status"] = "auth_required" if attempt is None and not credential else "error"
            return result
    code = last.status_code if last is not None else 0
    label = PROVIDER_LABELS[ref.provider]
    # The REST API can be blocked by a proxy while git over HTTPS still works: confirm with git ls-remote.
    fallback = _check_plain_git(ref, credential, dict(result))
    if fallback["status"] == "ok":
        fallback["message"] = "Repository is reachable (verified with git ls-remote; the host API was not available)."
        fallback.setdefault("description", "")
        return fallback
    if code in (401, 403, 404):
        if credential:
            result.update(
                status="no_access",
                message=f"Your {label} connection cannot read {ref.name}. Check the repository name, your permissions, "
                f"or sign in with a different account.",
            )
        else:
            result.update(
                status="auth_required",
                message=f"{ref.name} is private or does not exist. Sign in to {label} ({ref.host}) to check your access.",
            )
        return result
    result.update(status="error", message=f"{label} returned HTTP {code}.")
    return result


def _check_plain_git(ref: RepoRef, credential: Credential | None, result: dict) -> dict:
    completed = _ls_remote(ref, credential, ["--symref"], ["HEAD"])
    if completed.returncode == 0:
        match = re.search(r"ref: refs/heads/(\S+)\s+HEAD", completed.stdout)
        result.update(status="ok", default_branch=match.group(1) if match else "", used_credential=credential is not None,
                      message="Repository is reachable.", is_private=credential is not None, description="")
        return result
    error = (completed.stderr or "").lower()
    if any(word in error for word in ("authentication", "could not read username", "403", "401", "terminal prompts disabled")):
        result.update(status="no_access" if credential else "auth_required",
                      message="Sign in to this Git host with a username and access token.")
    elif "not found" in error or "does not appear to be a git repository" in error:
        result.update(status="not_found", message="Repository not found.")
    else:
        result.update(status="error", message=(completed.stderr or "git ls-remote failed").strip()[:300])
    return result


def _server_default_branch(ref: RepoRef, credential: Credential | None) -> str:
    response = _get(f"{_repo_api_url(ref)}/branches/default", credential, ref.provider)
    if response.status_code == 200:
        return (response.json() or {}).get("displayId") or ""
    return ""


# --- branches -----------------------------------------------------------------------------------------------------


def list_branches(ref: RepoRef, credential: Credential | None) -> dict:
    """{"default_branch": str, "branches": [{"name", "sha"}]} — up to 1000 branches."""
    try:
        return _list_branches_api(ref, credential)
    except AppError:
        if ref.provider == GIT:
            raise
        result = _list_branches_git(ref, credential)
        if not result["branches"]:
            raise
        return result


def _list_branches_git(ref: RepoRef, credential: Credential | None) -> dict:
    return _list_branches_api(RepoRef(GIT, ref.host, ref.owner, ref.slug, ref.clone_url, ref.web_url, "", ref.base_url), credential)


def _list_branches_api(ref: RepoRef, credential: Credential | None) -> dict:
    branches: list[dict] = []
    default = ""
    if ref.provider == GITHUB:
        info = _get(_repo_api_url(ref), credential, ref.provider)
        if info.status_code == 200:
            default = info.json().get("default_branch") or ""
        page = 1
        while page <= 10:
            response = _get(f"{_repo_api_url(ref)}/branches", credential, ref.provider, {"per_page": 100, "page": page})
            _raise_for(response, ref)
            items = response.json() or []
            branches += [{"name": item["name"], "sha": (item.get("commit") or {}).get("sha", "")} for item in items]
            if len(items) < 100:
                break
            page += 1
    elif ref.provider == BITBUCKET:
        info = _get(_repo_api_url(ref), credential, ref.provider)
        if info.status_code == 200:
            default = ((info.json().get("mainbranch") or {}).get("name")) or ""
        url: str | None = f"{_repo_api_url(ref)}/refs/branches"
        params: dict | None = {"pagelen": 100}
        while url and len(branches) < 1000:
            response = _get(url, credential, ref.provider, params)
            _raise_for(response, ref)
            body = response.json() or {}
            branches += [{"name": item["name"], "sha": (item.get("target") or {}).get("hash", "")} for item in body.get("values", [])]
            url, params = body.get("next"), None
    elif ref.provider == BITBUCKET_SERVER:
        start = 0
        while len(branches) < 1000:
            response = _get(f"{_repo_api_url(ref)}/branches", credential, ref.provider, {"limit": 500, "start": start})
            _raise_for(response, ref)
            body = response.json() or {}
            for item in body.get("values", []):
                branches.append({"name": item.get("displayId"), "sha": item.get("latestCommit", "")})
                if item.get("isDefault"):
                    default = item.get("displayId")
            if body.get("isLastPage", True):
                break
            start = body.get("nextPageStart", start + 500)
    else:
        completed = _ls_remote(ref, credential, ["--symref"])
        if completed.returncode != 0:
            raise AppError(403, "Cannot list branches. Sign in to this Git host first.", extra={"status": "auth_required"})
        for line in completed.stdout.splitlines():
            if line.startswith("ref: refs/heads/") and line.endswith("HEAD"):
                default = line.split()[1].removeprefix("refs/heads/")
                continue
            sha, _, name = line.partition("\t")
            if name.startswith("refs/heads/"):
                branches.append({"name": name.removeprefix("refs/heads/"), "sha": sha})
    names = {item["name"] for item in branches}
    if default and default not in names:
        branches.insert(0, {"name": default, "sha": ""})
    branches.sort(key=lambda item: (item["name"] != default, _branch_rank(item["name"]), item["name"].lower()))
    return {"default_branch": default, "branches": branches}


def _branch_rank(name: str) -> int:
    lowered = name.lower()
    if lowered in ("master", "main", "develop", "development"):
        return 0
    if lowered.startswith(("release", "hotfix")):
        return 1
    return 2


def _raise_for(response: httpx.Response, ref: RepoRef) -> None:
    if response.status_code == 200:
        return
    if response.status_code in (401, 403, 404):
        raise AppError(
            403,
            f"Cannot list branches of {ref.name}. Sign in to {PROVIDER_LABELS[ref.provider]} first.",
            extra={"status": "auth_required"},
        )
    raise AppError(502, f"{PROVIDER_LABELS[ref.provider]} returned HTTP {response.status_code} while listing branches")


# --- clone --------------------------------------------------------------------------------------------------------


def clone(ref: RepoRef, credential: Credential | None, branch: str, target: Path) -> dict:
    """Shallow clone of one branch into `target`. Returns commit facts. Credentials live only on the command line."""
    if not shutil.which("git"):
        raise AppError(500, "git is not installed on the server")
    target.parent.mkdir(parents=True, exist_ok=True)
    command = ["git", *_git_config(ref, credential), "clone", "--depth", "1", "--single-branch", "--no-tags"]
    if branch:
        command += ["--branch", branch]
    command += [ref.clone_url, str(target)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=config.git_timeout_seconds(), env=_git_env())
    except subprocess.TimeoutExpired as exc:
        raise AppError(504, "Cloning took too long. Raise GIT_TIMEOUT_SECONDS for very large repositories.") from exc
    if completed.returncode != 0:
        error = _scrub(completed.stderr or completed.stdout)
        lowered = error.lower()
        if "remote branch" in lowered and "not found" in lowered:
            raise AppError(400, f"Branch '{branch}' was not found in {ref.name}.")
        if any(word in lowered for word in ("authentication", "403", "401", "could not read username", "terminal prompts")):
            raise AppError(403, f"Access to {ref.name} was refused. Sign in to {PROVIDER_LABELS[ref.provider]} again.")
        raise AppError(502, f"git clone failed: {error[:400]}")
    log = subprocess.run(
        ["git", "-C", str(target), "log", "-1", "--format=%H%n%an%n%aI%n%s"], capture_output=True, text=True, timeout=30
    )
    lines = (log.stdout or "").splitlines() + ["", "", "", ""]
    # Credential hygiene: nothing was persisted, but drop the .git folder to keep the workspace small.
    shutil.rmtree(target / ".git", ignore_errors=True)
    return {"commit_sha": lines[0], "commit_author": lines[1], "commit_date": lines[2], "commit_message": lines[3]}


def _scrub(text: str) -> str:
    return re.sub(r"Authorization: \S+ \S+", "Authorization: ***", text or "").strip()


# --- identity (verifying a token) ---------------------------------------------------------------------------------


def whoami(provider: str, host: str, base_url: str, credential: Credential) -> str:
    """Return the account name the credential belongs to, or raise when it is rejected."""
    if provider == GITHUB:
        api = "https://api.github.com" if host == "github.com" else f"{base_url or 'https://' + host}/api/v3"
        response = _get(f"{api}/user", credential, provider)
        if response.status_code == 200:
            return response.json().get("login") or ""
    elif provider == BITBUCKET:
        response = _get("https://api.bitbucket.org/2.0/user", credential, provider)
        if response.status_code == 200:
            body = response.json()
            return body.get("username") or body.get("display_name") or ""
    elif provider == BITBUCKET_SERVER:
        base = base_url or f"https://{host}"
        response = _get(f"{base}/plugins/servlet/applinks/whoami", credential, provider)
        if response.status_code == 200 and response.text.strip():
            return response.text.strip()[:200]
        response = _get(f"{base}/rest/api/1.0/projects", credential, provider, {"limit": 1})
        if response.status_code == 200:
            return credential.username or "token"
    else:
        return credential.username or "token"
    # 400, not 401: a 401 from this API means "your session expired" to the browser.
    raise AppError(400, f"{PROVIDER_LABELS.get(provider, provider)} rejected these credentials.")
