"""Public LLM facade. Everything else calls these functions; the active provider's adapter does the work."""

import json
import logging
import re

from app.errors import AppError, LLMNotConfigured

logger = logging.getLogger(__name__)


def _summary(exc: Exception) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    return text if len(text) <= 400 else text[:397] + "..."


def _adapter():
    from app.services.llm_settings import active_adapter

    return active_adapter()


def configured() -> bool:
    try:
        return _adapter().chat_configured()
    except Exception:
        return False


def embedding_configured() -> bool:
    try:
        return _adapter().embedding_configured()
    except Exception:
        return False


def provider_name() -> str:
    try:
        return _adapter().provider
    except Exception:
        return ""


def complete(system: str, user: str, temperature: float = 0.2) -> str:
    adapter = _adapter()
    if not adapter.chat_configured():
        raise LLMNotConfigured()
    try:
        return adapter.complete(system, user, temperature)
    except AppError:
        raise
    except Exception as exc:
        logger.exception("LLM request failed")
        raise AppError(502, f"The {adapter.provider or 'LLM'} provider rejected the request: {_summary(exc)}") from exc


def complete_json(system: str, user: str, temperature: float = 0.1) -> dict:
    """Ask for JSON and parse the first object in the reply (models sometimes wrap it in fences)."""
    raw = complete(system, user, temperature)
    return parse_json(raw)


def parse_json(raw: str) -> dict:
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def embed(text: str) -> list[float] | None:
    adapter = _adapter()
    if not adapter.embedding_configured():
        return None
    try:
        return adapter.embed(text)
    except Exception:
        logger.exception("Embedding request failed")
        return None


def embedding_signature() -> str:
    """Identifies the embedding space; vectors from another provider/model are not comparable."""
    try:
        adapter = _adapter()
    except Exception:
        return ""
    if not adapter.embedding_configured():
        return ""
    return f"{adapter.provider}:{adapter.config.embedding_model}"
