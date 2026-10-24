from tests.conftest import login


def test_login_and_me(client, admin):
    me = client.get("/api/auth/me", headers=admin).json()
    assert me["login_id"] == "admin"
    assert me["permissions"] == {"repositories": "write", "insights": "write", "settings": "write"}


def test_bad_password_is_rejected_and_audited(client, admin):
    assert client.post("/api/auth/login", json={"login_id": "admin", "password": "nope"}).status_code == 401
    entries = client.get("/api/audit?action=login_failed", headers=admin).json()["items"]
    assert entries and entries[0]["login_id"] == "admin"


def test_requests_need_a_token(client):
    assert client.get("/api/repositories").status_code == 401


def test_viewer_cannot_change_anything(client, sample_repo):
    viewer = login(client, "viewer", "ViewerPass123!")
    assert client.get("/api/repositories", headers=viewer).status_code == 200
    assert client.post("/api/repositories", json={"source_type": "local", "path": str(sample_repo)}, headers=viewer).status_code == 403
    assert client.get("/api/settings", headers=viewer).status_code == 403
    assert client.post("/api/comparisons", json={}, headers=viewer).status_code == 403


def test_analyst_runs_analyses_but_not_settings(client, sample_repo):
    analyst = login(client, "analyst", "AnalystPass123!")
    response = client.post("/api/repositories", json={"source_type": "local", "path": str(sample_repo), "name": "orders"}, headers=analyst)
    assert response.status_code == 200, response.text
    assert client.get("/api/users", headers=analyst).status_code == 403
    assert client.put("/api/settings", json={"active_provider": "gemini"}, headers=analyst).status_code == 403


def test_git_connections_are_personal(client):
    viewer = login(client, "viewer", "ViewerPass123!")
    assert client.get("/api/git/credentials", headers=viewer).status_code == 200


def test_change_own_password(client):
    analyst = login(client, "analyst", "AnalystPass123!")
    assert client.post("/api/auth/password", json={"current_password": "AnalystPass123!", "new_password": "NewPass12345"}, headers=analyst).status_code == 200
    login(client, "analyst", "NewPass12345")
