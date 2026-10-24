from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    chat_model: str = ""
    chat_base_url: str = ""
    embedding_model: str = ""
    embedding_base_url: str = ""
    api_key: str = ""
    embedding_api_key: str = ""
    verify_ssl: bool = True
    max_model_length: int = 128000


def message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content)


class LLMAdapter(ABC):
    """One adapter per provider. Each exposes chat completion and text embeddings."""

    provider: str = ""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def chat_configured(self) -> bool:
        return bool(self.config.api_key and self.config.chat_model)

    def embedding_configured(self) -> bool:
        return bool((self.config.embedding_api_key or self.config.api_key) and self.config.embedding_model)

    @property
    def embedding_key(self) -> str:
        return self.config.embedding_api_key or self.config.api_key

    @abstractmethod
    def complete(self, system: str, user: str, temperature: float) -> str:
        """Return the model's text reply."""

    @abstractmethod
    def embed(self, text: str) -> list[float] | None:
        """Return an embedding vector, or None when embeddings are unavailable."""
