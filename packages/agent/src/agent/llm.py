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
            max_output_tokens=settings.gemini_max_tokens,
        )

    if settings.llm_provider == "vertex":
        # Deliberately still langchain_google_genai, not the separate
        # langchain-google-vertexai package - that package's dependency
        # chain caps pyarrow<24.0.0 across every published version, which
        # conflicts outright with this project's pyarrow==25.0.1 pin (kept
        # in sync with the sandbox image - see that dependency's own
        # comment). langchain-google-genai 4.0.0+ already speaks to Vertex
        # AI through the same unified google-genai SDK this project already
        # depends on for the "gemini" provider - vertexai=True switches the
        # backend, no second Google SDK needed.
        #
        # Auth is NOT an API key by default - Vertex AI uses Application
        # Default Credentials, discovered in order: an explicit service
        # account file (vertex_credentials_path) if set, else `gcloud auth
        # application-default login`'s local login, else the environment's
        # own service identity. vertex_api_key is a third, newer option
        # (a Vertex-AI-enabled API key, NOT the same kind of key as
        # GEMINI_API_KEY/AI Studio) - only used if explicitly set.
        credentials = None
        if settings.vertex_credentials_path:
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(
                settings.vertex_credentials_path,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        return ChatGoogleGenerativeAI(
            model=model or settings.vertex_model,
            vertexai=True,
            project=settings.vertex_project,
            location=settings.vertex_location,
            credentials=credentials,
            # Confirmed by reading langchain_google_genai's own source: the
            # Vertex-mode key is read from the `google_api_key` field (the
            # same field the "gemini"/AI-Studio branch above uses) - it
            # temporarily exports that as the GOOGLE_API_KEY env var for the
            # underlying google-genai Client to discover, since that Client
            # only reads Vertex API keys from the environment, never from a
            # constructor kwarg. There is no separate `api_key=` parameter
            # for this - passing one is silently ignored, not an error,
            # which is exactly what happened here the first time (verified
            # live: without this, the client fell all the way through to
            # Application Default Credentials instead of the key).
            google_api_key=settings.vertex_api_key or None,
            temperature=temperature,
            # Same rationale as gemini's retry count above - a transient
            # provider-side hiccup shouldn't surface as a 500 to the user.
            max_retries=4,
            timeout=settings.vertex_timeout_seconds,
            max_output_tokens=settings.vertex_max_tokens,
        )

    if settings.llm_provider != "openrouter":
        raise ValueError(
            f"Unknown LLM_PROVIDER: {settings.llm_provider!r} (expected 'openrouter', 'gemini', or 'vertex')"
        )

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
        # See the setting's own comment - without this, OpenRouter checks
        # affordability against the model's full context window, not what
        # the request actually needs, so a low account balance fails every
        # request outright instead of just the genuinely large ones.
        max_tokens=settings.openrouter_max_tokens,
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
