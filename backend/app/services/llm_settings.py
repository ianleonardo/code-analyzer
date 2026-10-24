"""LLM provider configuration: storage, encrypted API keys and the active-provider choice (Gemini or Spirare)."""

import os

from app import db
from app.crypto import decrypt, encrypt
from app.errors import AppError
from app.permissions import require
from app.services.llm_adapters import ADAPTERS, LLMAdapter, ProviderConfig, build_adapter

PROVIDERS = tuple(ADAPTERS)
_URL_FIELDS = ("chat_base_url", "embedding_base_url")
_TEXT_FIELDS = ("chat_model", "chat_base_url", "embedding_model", "embedding_base_url")


def _stored_length(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 128000
    return number if number > 0 else 128000


def _whole(value, label: str, low: int, high: int) -> int:
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text.isdigit():
        raise AppError(400, f"{label} must be a whole number.")
    number = int(text)
    if number < low or number > high:
        raise AppError(400, f"{label} must be between {low} and {high}.")
    return number


def _row(provider: str) -> dict:
    row = db.fetchone("SELECT * FROM llm_providers WHERE provider = ?", [provider])
    if row is None:
        raise AppError(404, "Unknown LLM provider")
    return row


def settings_row() -> dict:
    return db.fetchone("SELECT * FROM app_settings WHERE settings_id = 'default'") or {}


def runtime_config(provider: str) -> ProviderConfig:
    """Decrypted settings for one provider. Never returned by the API."""
    row = _row(provider)
    api_key = decrypt(row["api_key_enc"])
    embedding_key = decrypt(row["embedding_api_key_enc"])
    if provider == "spirare":
        api_key = api_key or os.environ.get("LLM_API_KEY", "")
        embedding_key = embedding_key or os.environ.get("EMBEDDING_API_KEY", "")
    if provider == "gemini":
        api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
    verify_raw = row.get("verify_ssl")
    return ProviderConfig(
        provider=provider,
        chat_model=row["chat_model"],
        chat_base_url=row["chat_base_url"],
        embedding_model=row["embedding_model"],
        embedding_base_url=row["embedding_base_url"],
        api_key=api_key,
        embedding_api_key=embedding_key,
        verify_ssl=True if verify_raw is None else bool(verify_raw),
        max_model_length=_stored_length(row.get("max_model_length")),
    )


def active_provider() -> str:
    value = settings_row().get("active_provider") or "spirare"
    return value if value in PROVIDERS else "spirare"


def active_adapter() -> LLMAdapter:
    return build_adapter(runtime_config(active_provider()))


def provider_payload(row: dict) -> dict:
    config = runtime_config(row["provider"])
    return {
        "provider": row["provider"],
        "label": row["label"],
        "chat_model": row["chat_model"],
        "chat_base_url": row["chat_base_url"],
        "embedding_model": row["embedding_model"],
        "embedding_base_url": row["embedding_base_url"],
        # The key itself is write-only; the API only says whether one exists.
        "api_key_configured": bool(config.api_key),
        "embedding_api_key_configured": bool(config.embedding_api_key),
        "verify_ssl": config.verify_ssl,
        "max_model_length": config.max_model_length,
        "updated_at": row["updated_at"],
    }


def settings_payload() -> dict:
    settings = settings_row()
    rows = db.fetchall("SELECT * FROM llm_providers")
    order = {name: index for index, name in enumerate(PROVIDERS)}
    rows = [row for row in rows if row["provider"] in order]
    rows.sort(key=lambda item: order.get(item["provider"], 99))
    return {
        "active_provider": active_provider(),
        "global_system_prompt": settings.get("global_system_prompt", ""),
        "llm_file_limit": settings.get("llm_file_limit", 40),
        "embedding_chunk_limit": settings.get("embedding_chunk_limit", 3000),
        "llm_catalog_limit": settings.get("llm_catalog_limit") if settings.get("llm_catalog_limit") is not None else 60,
        "updated_at": settings.get("updated_at"),
        "providers": [provider_payload(row) for row in rows],
    }


def update_settings(user: dict, body: dict) -> dict:
    require(user, "settings", "write")
    current = settings_payload()
    active = str(body.get("active_provider") or current["active_provider"])
    if active not in PROVIDERS:
        raise AppError(400, "Unknown LLM provider")
    prompt = str(body.get("global_system_prompt", current["global_system_prompt"]) or "")
    if not prompt.strip():
        raise AppError(400, "Global prompt is required")
    file_limit = _whole(body.get("llm_file_limit", current["llm_file_limit"]), "Files summarised by the LLM", 0, 2000)
    chunk_limit = _whole(body.get("embedding_chunk_limit", current["embedding_chunk_limit"]), "Embedded chunks", 0, 100000)
    catalog_limit = _whole(body.get("llm_catalog_limit", current["llm_catalog_limit"]), "Catalogue items explained by the LLM", 0, 5000)
    db.exec_sql(
        """
        UPDATE app_settings
        SET active_provider = ?, global_system_prompt = ?, llm_file_limit = ?, embedding_chunk_limit = ?, llm_catalog_limit = ?, updated_at = ?
        WHERE settings_id = 'default'
        """,
        [active, prompt, file_limit, chunk_limit, catalog_limit, db.now()],
    )
    return settings_payload()


def update_provider(user: dict, provider: str, body: dict) -> dict:
    require(user, "settings", "write")
    _row(provider)
    assignments: list[str] = []
    params: list = []
    for field in _TEXT_FIELDS:
        if field not in body:
            continue
        value = str(body.get(field) or "").strip()
        if field in _URL_FIELDS and value and not value.startswith(("http://", "https://")):
            raise AppError(400, "URLs must start with http:// or https://")
        assignments.append(f"{field} = ?")
        params.append(value)
    # A blank key keeps the stored one; clear_* removes it.
    for field, column in (("api_key", "api_key_enc"), ("embedding_api_key", "embedding_api_key_enc")):
        incoming = str(body.get(field) or "").strip()
        if body.get(f"clear_{field}"):
            assignments.append(f"{column} = ?")
            params.append("")
        elif incoming:
            assignments.append(f"{column} = ?")
            params.append(encrypt(incoming))
    if provider == "spirare" and "verify_ssl" in body:
        assignments.append("verify_ssl = ?")
        params.append(bool(body.get("verify_ssl")))
    if provider == "spirare" and "max_model_length" in body and body.get("max_model_length") not in (None, ""):
        assignments.append("max_model_length = ?")
        params.append(_whole(body.get("max_model_length"), "Max model length", 1024, 2_000_000))
    if assignments:
        assignments.append("updated_at = ?")
        params.append(db.now())
        params.append(provider)
        db.exec_sql(f"UPDATE llm_providers SET {', '.join(assignments)} WHERE provider = ?", params)
    return provider_payload(_row(provider))


def test_provider(user: dict, provider: str) -> dict:
    """Sends a tiny prompt (and embedding) to check the saved settings work."""
    require(user, "settings", "read")
    adapter = build_adapter(runtime_config(provider))
    result = {"provider": provider, "chat": "not configured", "embedding": "not configured"}
    if adapter.chat_configured():
        try:
            reply = adapter.complete("Reply with the single word OK.", "Ping", 0)
            result["chat"] = "ok" if reply.strip() else "empty reply"
        except Exception as exc:  # noqa: BLE001
            result["chat"] = f"failed: {' '.join(str(exc).split())[:300]}"
    if adapter.embedding_configured():
        try:
            vector = adapter.embed("ping")
            result["embedding"] = f"ok ({len(vector or [])} dimensions)" if vector else "empty vector"
        except Exception as exc:  # noqa: BLE001
            result["embedding"] = f"failed: {' '.join(str(exc).split())[:300]}"
    return result
