import pytest
from app.errors import AppError
from app.services import git_hosts
from app.services.git_hosts import Credential


@pytest.mark.parametrize(
    "url,provider,owner,slug,clone",
    [
        ("https://github.com/org/repo", "github", "org", "repo", "https://github.com/org/repo.git"),
        ("https://github.com/org/repo.git", "github", "org", "repo", "https://github.com/org/repo.git"),
        ("git@github.com:org/repo.git", "github", "org", "repo", "https://github.com/org/repo.git"),
        ("https://github.com/org/repo/tree/release/1.0", "github", "org", "repo", "https://github.com/org/repo.git"),
        ("https://bitbucket.org/team/svc", "bitbucket", "team", "svc", "https://bitbucket.org/team/svc.git"),
        ("https://bitbucket.corp.com/scm/core/payments.git", "bitbucket_server", "core", "payments", "https://bitbucket.corp.com/scm/core/payments.git"),
        ("https://bitbucket.corp.com/projects/CORE/repos/payments/browse", "bitbucket_server", "CORE", "payments", "https://bitbucket.corp.com/scm/core/payments.git"),
        ("https://git.corp.com/bitbucket/scm/ntn/alerts.git", "bitbucket_server", "ntn", "alerts", "https://git.corp.com/bitbucket/scm/ntn/alerts.git"),
        ("https://gitlab.com/group/project.git", "git", "group", "project", "https://gitlab.com/group/project.git"),
    ],
)
def test_parse_url(url, provider, owner, slug, clone):
    ref = git_hosts.parse_url(url)
    assert (ref.provider, ref.owner, ref.slug, ref.clone_url) == (provider, owner, slug, clone)


def test_server_api_base_keeps_context_path():
    ref = git_hosts.parse_url("https://git.corp.com/bitbucket/projects/NTN/repos/alerts/browse")
    assert ref.api_base == "https://git.corp.com/bitbucket/rest/api/1.0"


def test_bad_url():
    with pytest.raises(AppError):
        git_hosts.parse_url("not a url")


def test_git_headers_never_put_tokens_in_urls():
    github = Credential(token="ghp_x", auth_method="oauth").git_header("github")
    assert github.startswith("Authorization: Basic ")
    server = Credential(token="bbs_tok").git_header("bitbucket_server")
    assert server == "Authorization: Bearer bbs_tok"
    assert "ghp_x" not in git_hosts._scrub("fatal: Authorization: Basic abc123 failed")


def test_check_access_reports_sign_in_needed(monkeypatch):
    class Response:
        status_code = 404
        text = "Not Found"
        content = b""

        def json(self):
            return {}

    monkeypatch.setattr(git_hosts, "_get", lambda *a, **k: Response())
    monkeypatch.setattr(git_hosts, "_check_plain_git", lambda ref, cred, result: {**result, "status": "auth_required"})
    ref = git_hosts.parse_url("https://github.com/org/private")
    assert git_hosts.check_access(ref, None)["status"] == "auth_required"
    assert git_hosts.check_access(ref, Credential(token="t"))["status"] == "no_access"


def test_branch_sorting_puts_default_and_release_first(monkeypatch):
    class Response:
        def __init__(self, status, body):
            self.status_code, self._body, self.text, self.content = status, body, "", b"x"

        def json(self):
            return self._body

    def fake_get(url, credential, provider, params=None):
        if url.endswith("/branches"):
            return Response(200, [{"name": n, "commit": {"sha": "1"}} for n in ("feature/x", "release/2.0", "master", "develop")])
        return Response(200, {"default_branch": "master"})

    monkeypatch.setattr(git_hosts, "_get", fake_get)
    result = git_hosts.list_branches(git_hosts.parse_url("https://github.com/o/r"), None)
    assert [b["name"] for b in result["branches"]][:3] == ["master", "develop", "release/2.0"]


def test_bitbucket_cloud_token_kinds():
    import base64

    def basic(header: str) -> str:
        return base64.b64decode(header.split()[-1]).decode()

    api_token = Credential(token="ATATT", username="ian@corp.com")
    assert basic(api_token.http_headers("bitbucket")["Authorization"]) == "ian@corp.com:ATATT"  # REST: email + token
    assert basic(api_token.git_header("bitbucket")) == "x-bitbucket-api-token-auth:ATATT"  # git: fixed username
    access_token = Credential(token="ATCTT")
    assert access_token.http_headers("bitbucket") == {"Authorization": "Bearer ATCTT"}
    assert basic(access_token.git_header("bitbucket")) == "x-token-auth:ATCTT"


def test_rejected_git_credentials_do_not_end_the_session(client, monkeypatch):
    """A Git host rejecting a token must be a 400, never a 401 (which signs the user out)."""
    from tests.conftest import login

    headers = login(client)

    class Response:
        status_code = 401
        text = "Unauthorized"
        content = b""

        def json(self):
            return {}

    monkeypatch.setattr(git_hosts, "_get", lambda *a, **k: Response())
    monkeypatch.setattr(git_hosts, "_check_plain_git", lambda ref, cred, result: {**result, "status": "no_access"})
    response = client.post("/api/git/credentials", json={"url": "https://bitbucket.org/team/private", "username": "ian", "token": "bad"}, headers=headers)
    assert response.status_code == 400
    assert "Atlassian account email" in response.json()["detail"]
    response = client.post("/api/git/credentials", json={"provider": "bitbucket", "host": "bitbucket.org", "token": "bad"}, headers=headers)
    assert response.status_code == 400
    assert client.get("/api/auth/me", headers=headers).status_code == 200


def test_bad_token_on_public_repo_is_not_saved(client, monkeypatch):
    from tests.conftest import login

    headers = login(client)

    class Response:
        def __init__(self, status):
            self.status_code, self.text, self.content = status, "", b"{}"

        def json(self):
            return {"is_private": False}

    # the token is rejected, but the repository itself is public
    monkeypatch.setattr(git_hosts, "_get", lambda url, credential, provider, params=None: Response(401 if credential else 200))
    response = client.post("/api/git/credentials", json={"url": "https://bitbucket.org/team/public", "token": "bad"}, headers=headers)
    assert response.status_code == 400
    assert client.get("/api/git/credentials", headers=headers).json()["credentials"] == []


def test_spirare_defaults_and_upgrade(client):
    from app import db, seed

    from tests.conftest import login

    settings = client.get("/api/settings", headers=login(client)).json()
    spirare = next(p for p in settings["providers"] if p["provider"] == "spirare")
    assert spirare["chat_base_url"] == "https://s-egp.sg.uobnet.com/nlp/v1/chat/completions"
    assert spirare["embedding_base_url"] == "https://s-egp.sg.uobnet.com/embedding/v1/embeddings"
    db.exec_sql("UPDATE llm_providers SET chat_base_url = 'https://is-egp.sg.uobnet.com/wip/v1/chat/completions' WHERE provider = 'spirare'")
    seed.run()
    row = db.fetchone("SELECT chat_base_url FROM llm_providers WHERE provider = 'spirare'")
    assert row["chat_base_url"] == "https://s-egp.sg.uobnet.com/nlp/v1/chat/completions"
