from langchain_openai import ChatOpenAI

from core.settings import get_settings


def get_chat_model(model: str | None = None, temperature: float = 0.0) -> ChatOpenAI:
    """Chat model routed through OpenRouter's OpenAI-compatible API.

    Swapping OPENROUTER_MODEL in .env changes the underlying model with no code change.
    """
    settings = get_settings()
    return ChatOpenAI(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key,
        model=model or settings.openrouter_model,
        temperature=temperature,
    )
