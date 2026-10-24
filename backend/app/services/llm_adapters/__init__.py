from app.services.llm_adapters.base import LLMAdapter, ProviderConfig
from app.services.llm_adapters.gemini import GeminiAdapter
from app.services.llm_adapters.spirare import SpirareAdapter

# Exactly two providers, as in the Admin > LLM page.
ADAPTERS: dict[str, type[LLMAdapter]] = {
    "gemini": GeminiAdapter,
    "spirare": SpirareAdapter,
}


def build_adapter(config: ProviderConfig) -> LLMAdapter:
    try:
        return ADAPTERS[config.provider](config)
    except KeyError as exc:
        raise ValueError(f"Unknown LLM provider: {config.provider}") from exc


__all__ = ["ADAPTERS", "GeminiAdapter", "LLMAdapter", "ProviderConfig", "SpirareAdapter", "build_adapter"]
