import httpx

from app.services.llm_adapters.base import LLMAdapter

# Qwen3.5-122B-A10B accepts this many tokens. Settings can replace it per deployment.
DEFAULT_CONTEXT_LIMIT = 128000
OUTPUT_TOKENS = 4096
MIN_OUTPUT_TOKENS = 256
# Over-estimate tokens (about 3 characters each) so a long prompt is cut before the server returns 400.
CHARS_PER_TOKEN = 3
_FRAMING_TOKENS = 32
_OMITTED = "\n[Earlier context was omitted because it exceeded the model limit.]\n"


def _endpoint(url: str, suffix: str) -> str:
    """Use a full endpoint as given, or append the OpenAI-style path to a base URL."""
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if cleaned.endswith(suffix):
        return cleaned
    return f"{cleaned}/{suffix}"


def _estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= len(_OMITTED):
        return text[:max_chars]
    return text[: max_chars - len(_OMITTED)] + _OMITTED


def _clip_user(user: str, max_chars: int) -> str:
    """Keep the question at the end of a chat prompt and drop earlier context first."""
    if len(user) <= max_chars:
        return user
    marker = "\n\nQUESTION:\n"
    if marker in user and max_chars > len(_OMITTED) + 80:
        head, question = user.rsplit(marker, 1)
        tail = marker + question
        room = max_chars - len(_OMITTED) - len(tail)
        if room > 0:
            return head[:room] + _OMITTED + tail
        if len(tail) + len(_OMITTED) <= max_chars:
            return _OMITTED + tail
        return _clip(tail, max_chars)
    return _clip(user, max_chars)


def fit_chat(system: str, user: str, context_limit: int = DEFAULT_CONTEXT_LIMIT) -> tuple[str, str, int]:
    """Return a system prompt, user prompt and max_tokens that fit in the configured context window."""
    limit = context_limit if context_limit and context_limit > 0 else DEFAULT_CONTEXT_LIMIT
    output = OUTPUT_TOKENS
    if _estimate_tokens(system) + _estimate_tokens(user) + output + _FRAMING_TOKENS <= limit:
        return system, user, output
    room = limit - _FRAMING_TOKENS - _estimate_tokens(system) - _estimate_tokens(user)
    if room >= MIN_OUTPUT_TOKENS:
        return system, user, min(output, room)
    output = MIN_OUTPUT_TOKENS
    user_budget = limit - _FRAMING_TOKENS - output - _estimate_tokens(system)
    if user_budget < 64:
        system_tokens = max(128, limit - _FRAMING_TOKENS - output - 512)
        system = _clip(system, system_tokens * CHARS_PER_TOKEN)
        user_budget = limit - _FRAMING_TOKENS - output - _estimate_tokens(system)
    user = _clip_user(user, max(0, user_budget) * CHARS_PER_TOKEN)
    return system, user, output


def _context_overflow(detail: str) -> bool:
    text = detail.lower()
    return "context length" in text or "maximum context" in text or "prompt token" in text


class SpirareAdapter(LLMAdapter):
    """Spirare chat completions and embeddings, including the extra embedding fields it requires."""

    provider = "spirare"

    def _post(self, url: str, payload: dict, api_key: str) -> dict:
        if not url:
            raise RuntimeError("Spirare URL is not configured.")
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=180,
            verify=self.config.verify_ssl,
            trust_env=False,
        )
        if response.status_code >= 400:
            detail = " ".join(response.text.split())
            if len(detail) > 300:
                detail = detail[:297] + "..."
            raise RuntimeError(f"Spirare returned HTTP {response.status_code}: {detail or 'request failed'}")
        return response.json()

    def complete(self, system: str, user: str, temperature: float) -> str:
        url = _endpoint(self.config.chat_base_url, "chat/completions")
        system, user, max_tokens = fit_chat(system, user, self.config.max_model_length)
        try:
            body = self._post(url, self._chat_payload(system, user, temperature, max_tokens), self.config.api_key)
        except RuntimeError as exc:
            if not _context_overflow(str(exc)):
                raise
            # The character estimate can still be short of the server's tokenizer. Shrink once and retry.
            system, user, max_tokens = fit_chat(system[: len(system) // 2], user[: len(user) // 2], self.config.max_model_length)
            body = self._post(url, self._chat_payload(system, user, temperature, min(max_tokens, MIN_OUTPUT_TOKENS)), self.config.api_key)
        message = (body.get("choices") or [{}])[0].get("message") or {}
        return str(message.get("content") or "")

    def _chat_payload(self, system: str, user: str, temperature: float, max_tokens: int) -> dict:
        return {
            "model": self.config.chat_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }

    def embed(self, text: str) -> list[float] | None:
        if not self.embedding_configured():
            return None
        url = _endpoint(self.config.embedding_base_url or self.config.chat_base_url, "embeddings")
        body = self._post(
            url,
            {"input": text[:8000], "model": self.config.embedding_model, "encoding_format": "float", "input_type": "passage"},
            self.embedding_key,
        )
        data = body.get("data") or []
        if not data:
            return None
        return [float(value) for value in data[0].get("embedding") or []]
