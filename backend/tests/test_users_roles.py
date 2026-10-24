def test_create_role_and_user(client, admin):
    role = client.post("/api/roles", json={"name": "Auditor", "repositories": "read", "insights": "none", "settings": "read"}, headers=admin).json()
    assert role["role_id"] == "auditor"
    user = client.post("/api/users", json={"login_id": "aud1", "username": "Audrey", "password": "Password123", "role_id": "auditor"}, headers=admin).json()
    assert user["role_name"] == "Auditor"
    token = client.post("/api/auth/login", json={"login_id": "AUD1", "password": "Password123"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/users", headers=headers).status_code == 200  # settings read
    assert client.post("/api/users", json={}, headers=headers).status_code == 403
    assert client.get("/api/chat/sessions", headers=headers).status_code == 403  # insights none


def test_login_id_rules(client, admin):
    bad = client.post("/api/users", json={"login_id": "a b", "username": "x", "password": "Password123", "role_id": "viewer"}, headers=admin)
    assert bad.status_code == 400
    short = client.post("/api/users", json={"login_id": "abc", "username": "x", "password": "short", "role_id": "viewer"}, headers=admin)
    assert short.status_code == 400


def test_last_admin_is_protected(client, admin):
    me = client.get("/api/auth/me", headers=admin).json()
    assert client.delete(f"/api/users/{me['user_id']}", headers=admin).status_code == 400
    response = client.patch(f"/api/users/{me['user_id']}", json={"role_id": "viewer"}, headers=admin)
    assert response.status_code == 400


def test_predefined_roles_cannot_be_deleted_and_admin_is_locked(client, admin):
    assert client.delete("/api/roles/viewer", headers=admin).status_code == 400
    assert client.patch("/api/roles/administrator", json={"settings": "none"}, headers=admin).status_code == 400
    roles = {r["role_id"]: r for r in client.get("/api/roles", headers=admin).json()}
    assert set(roles) >= {"viewer", "analyst", "administrator"}


def test_ldap_domains_and_ldap_user_creation(client, admin, monkeypatch):
    monkeypatch.setenv("LDAP_HOST", "ldap.example.internal")
    monkeypatch.setenv("LDAP_DOMAINS", "NTSGPDOM,NTMYPDOM")
    domains_res = client.get("/api/users/ldap-domains", headers=admin).json()
    assert domains_res["domains"] == ["NTSGPDOM", "NTMYPDOM"]

    created = client.post(
        "/api/users",
        json={"login_id": "ldapuser1", "username": "LDAP User", "role_id": "analyst", "auth_provider": "ldap", "ldap_domain": "NTMYPDOM"},
        headers=admin,
    )
    assert created.status_code == 200, created.text
    assert created.json()["auth_provider"] == "ldap"
    assert created.json()["ldap_domain"] == "NTMYPDOM"

    bad = client.post(
        "/api/users",
        json={"login_id": "ldapuser2", "username": "Bad Domain", "role_id": "analyst", "auth_provider": "ldap", "ldap_domain": "UNKNOWNDOM"},
        headers=admin,
    )
    assert bad.status_code == 400
