class AppError(Exception):
    def __init__(self, status: int, detail: str, errors: list[dict] | None = None, extra: dict | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.errors = errors
        self.extra = extra or {}


class LLMNotConfigured(AppError):
    def __init__(self) -> None:
        super().__init__(
            503,
            "No LLM is configured. Add an API key under Settings > LLM (Gemini or Spirare). "
            "Deterministic analysis results are still available.",
        )
