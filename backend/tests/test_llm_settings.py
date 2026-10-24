def test_only_gemini_and_spirare(client, admin):
    settings = client.get("/api/settings", headers=admin).json()
    assert [p["provider"] for p in settings["providers"]] == ["gemini", "spirare"]
    assert settings["active_provider"] == "spirare"


def test_keys_are_write_only_and_encrypted(client, admin):
    from app import db

    saved = client.put("/api/settings/providers/gemini", json={"api_key": "AIza-secret", "chat_model": "gemini-2.5-pro"}, headers=admin).json()
    assert saved["api_key_configured"] is True
    assert "AIza-secret" not in str(saved)
    stored = db.fetchone("SELECT api_key_enc FROM llm_providers WHERE provider = 'gemini'")
    assert stored["api_key_enc"] and "AIza-secret" not in stored["api_key_enc"]
    cleared = client.put("/api/settings/providers/gemini", json={"clear_api_key": True}, headers=admin).json()
    assert cleared["api_key_configured"] is False


def test_switch_active_provider_and_limits(client, admin):
    updated = client.put("/api/settings", json={"active_provider": "gemini", "llm_file_limit": 10, "embedding_chunk_limit": 0}, headers=admin).json()
    assert updated["active_provider"] == "gemini" and updated["llm_file_limit"] == 10
    assert client.put("/api/settings", json={"active_provider": "anthropic"}, headers=admin).status_code == 400
    assert client.put("/api/settings/providers/spirare", json={"chat_base_url": "ftp://x"}, headers=admin).status_code == 400


def test_spirare_fit_chat_keeps_question():
    from app.services.llm_adapters.spirare import fit_chat

    system, user, tokens = fit_chat("s" * 100, "context " * 50000 + "\n\nQUESTION:\nwhat?", 4096)
    assert user.endswith("what?") and tokens >= 256
