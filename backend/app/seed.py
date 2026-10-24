"""Rows every installation needs: roles, the first users, settings and the two LLM providers."""

import os

import bcrypt

from app import db
from app.config import DEFAULT_USERS, GLOBAL_PROMPT
from app.permissions import PREDEFINED_ROLES

PROVIDER_DEFAULTS = [
    # provider, label, chat model, chat url, embedding model, embedding url
    ("gemini", "Gemini", "gemini-3.6-flash", "", "gemini-embedding-2", ""),
    (
        "spirare",
        "Spirare",
        "Qwen3.5-122B-A10B",
        "https://s-egp.sg.uobnet.com/nlp/v1/chat/completions",
        "/data/genai/models/bge-m3",
        "https://s-egp.sg.uobnet.com/embedding/v1/embeddings",
    ),
]


def run() -> None:
    now = db.now()
    for role in PREDEFINED_ROLES:
        if db.fetchone("SELECT role_id FROM roles WHERE role_id = ?", [role["role_id"]]) is None:
            db.insert(
                "roles",
                {
                    **{key: role[key] for key in ("role_id", "name", "description", "repositories", "insights", "settings")},
                    "is_system": True,
                    "created_at": now,
                    "updated_at": now,
                },
            )
    if (db.scalar("SELECT count(*) AS c FROM users") or 0) == 0:
        for item in DEFAULT_USERS:
            login_id = os.environ.get(item["login_id_env"], "").strip() or item["login_id"]
            password = os.environ.get(item["password_env"], "").strip() or item["password"]
            db.insert(
                "users",
                {
                    "user_id": item["user_id"],
                    "username": item["username"],
                    "login_id": login_id,
                    "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
                    "role_id": item["role"],
                    "auth_provider": "local",
                    "created_at": now,
                },
            )
    if db.fetchone("SELECT settings_id FROM app_settings WHERE settings_id = 'default'") is None:
        db.insert(
            "app_settings",
            {
                "settings_id": "default",
                "active_provider": "spirare",
                "global_system_prompt": GLOBAL_PROMPT,
                "llm_file_limit": 40,
                "embedding_chunk_limit": 3000,
                "updated_at": now,
            },
        )
    # Databases created with the earlier Spirare defaults move to the s-egp endpoints. Custom URLs are left alone.
    db.exec_sql(
        "UPDATE llm_providers SET chat_base_url = ? WHERE provider = 'spirare' AND chat_base_url = ?",
        ["https://s-egp.sg.uobnet.com/nlp/v1/chat/completions", "https://is-egp.sg.uobnet.com/wip/v1/chat/completions"],
    )
    db.exec_sql(
        "UPDATE llm_providers SET embedding_base_url = ? WHERE provider = 'spirare' AND embedding_base_url = ?",
        ["https://s-egp.sg.uobnet.com/embedding/v1/embeddings", "https://is-egp.sg.uobnet.com/embedding/v1/embeddings"],
    )
    # Gemini installs still on the previous default chat/embedding models move to the new ones. A model an admin chose is left alone.
    db.exec_sql(
        "UPDATE llm_providers SET chat_model = ? WHERE provider = 'gemini' AND chat_model = ?",
        ["gemini-3.6-flash", "gemini-2.5-flash"],
    )
    db.exec_sql(
        "UPDATE llm_providers SET embedding_model = ? WHERE provider = 'gemini' AND embedding_model = ?",
        ["gemini-embedding-2", "gemini-embedding-001"],
    )
    for provider, label, chat_model, chat_url, embedding_model, embedding_url in PROVIDER_DEFAULTS:
        if db.fetchone("SELECT provider FROM llm_providers WHERE provider = ?", [provider]) is None:
            db.insert(
                "llm_providers",
                {
                    "provider": provider,
                    "label": label,
                    "chat_model": chat_model,
                    "chat_base_url": chat_url,
                    "embedding_model": embedding_model,
                    "embedding_base_url": embedding_url,
                    "updated_at": now,
                },
            )
