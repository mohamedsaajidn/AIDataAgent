from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from core.settings import get_settings


def get_chat_model(model: str | None = None, temperature: float = 0.0) -> BaseChatModel:
    """Chat model routed through whichever provider LLM_PROVIDER selects.

    Swapping LLM_PROVIDER (and the matching *_MODEL) in .env changes the
    underlying provider/model with no code change - this is the only place a
    chat model is instantiated anywhere in this project.
    """
    settings = get_settings()

    if settings.llm_provider == "gemini":
        return ChatGoogleGenerativeAI(
            google_api_key=settings.gemini_api_key,
            model=model or settings.gemini_model,
            temperature=temperature,
            # Same rationale as the OpenRouter timeout below - don't let a
            # slow generation hang past what a caller would reasonably wait.
            timeout=settings.gemini_timeout_seconds,
            # AI Studio's free tier returns transient 503/504 ("high demand")
            # often enough in practice (confirmed live - 2 of 3 back-to-back
            # calls failed this way) that max_retries=1 surfaces most of them
            # as a 500 to the user. A few retries lets a brief spike resolve
            # within one call instead.
            max_retries=4,
        )

    if settings.llm_provider != "openrouter":
        raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r} (expected 'openrouter' or 'gemini')")

    return ChatOpenAI(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key,
        model=model or settings.openrouter_model,
        temperature=temperature,
        # A large/slow generation (e.g. reviewing many columns at once) has no
        # server-side cap on a free-tier model - without a client timeout this
        # call can hang far longer than any caller would reasonably wait.
        timeout=settings.openrouter_timeout_seconds,
        max_retries=1,
    )


def get_response_text(response: BaseMessage) -> str:
    """Normalize a chat response's `.content` into a plain string.

    OpenRouter (ChatOpenAI) always returns plain `str` content. Gemini
    (ChatGoogleGenerativeAI) instead returns a list of content blocks (e.g.
    `[{"type": "text", "text": "...", "extras": {...}}]`) - every prompt-
    parsing call site in this codebase (JSON/code extraction, schema review)
    assumes a plain string, so the provider content-shape difference is
    normalized once here rather than at each call site.
    """
    content = response.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)
