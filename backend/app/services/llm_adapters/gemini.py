from app.services.llm_adapters.base import LLMAdapter, message_text


class GeminiAdapter(LLMAdapter):
    """Google Gemini through langchain-google-genai."""

    provider = "gemini"

    def complete(self, system: str, user: str, temperature: float) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = ChatGoogleGenerativeAI(
            model=self.config.chat_model,
            temperature=temperature,
            google_api_key=self.config.api_key,
            base_url=self.config.chat_base_url or None,
        )
        result = model.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        return message_text(result.content)

    def embed(self, text: str) -> list[float] | None:
        if not self.embedding_configured():
            return None
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        client = GoogleGenerativeAIEmbeddings(
            model=self.config.embedding_model,
            google_api_key=self.embedding_key,
            base_url=self.config.embedding_base_url or None,
        )
        return client.embed_query(text[:8000])
