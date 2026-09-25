from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    postgres_user: str = "dataagent"
    postgres_password: str = "dataagent_dev_password"
    postgres_db: str = "dataagent"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "dataagent-datasets"
    s3_region: str = "us-east-1"

    redis_url: str = "redis://localhost:6379/0"

    chroma_host: str = "localhost"
    chroma_port: int = 8001

    # Which provider agent.llm.get_chat_model() routes to - "openrouter",
    # "gemini" (Google AI Studio, API-key auth), or "vertex" (Google Cloud
    # Vertex AI, project+ADC auth - see the vertex_* settings below, this is
    # a different auth model from "gemini" despite both calling the same
    # underlying Gemini models). Swapping this (plus the matching *_MODEL)
    # is the one-line env change described in CLAUDE.md's "LLM provider"
    # section.
    llm_provider: str = "openrouter"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "deepseek/deepseek-v4-flash-0731:free"
    # Free-tier OpenRouter models are shared/queued infrastructure - even a
    # single small column can legitimately take 20-30s, so this needs to be
    # generous enough to not misfire on normal (if slow) responses.
    openrouter_timeout_seconds: int = 45
    # Without this, ChatOpenAI sends no max_tokens at all, and OpenRouter
    # defaults the request's affordability check to the model's full context
    # window (observed live: 116892) - with a low/near-zero account balance
    # that makes EVERY request fail outright with 402, even a tiny
    # classification prompt that only ever needed a few hundred tokens back.
    # Every prompt in this codebase is short-answer/JSON/code-snippet, never
    # long-form generation, so this comfortably covers the largest legitimate
    # case (a freehand chart/transform script) while keeping the requested
    # ceiling realistic regardless of account balance.
    openrouter_max_tokens: int = 4096

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_timeout_seconds: int = 45
    gemini_max_tokens: int = 4096

    # Vertex AI - a paid, enterprise Google Cloud product, not the same
    # account/billing as AI Studio's "gemini" provider above even though it
    # serves the same model family, and reached through the same
    # langchain_google_genai client as "gemini" (vertexai=True switches the
    # backend) - see agent.llm's comment for why, not the separate
    # langchain-google-vertexai package. Auth defaults to Application
    # Default Credentials (ADC), discovered from (in order)
    # vertex_credentials_path below if set, `gcloud auth
    # application-default login`'s local credential file, or the
    # environment's own service identity (Cloud Run/GCE/etc, irrelevant on
    # a dev machine) - or set vertex_api_key for the newer API-key-based
    # auth option instead (a Vertex-AI-enabled key, NOT interchangeable
    # with GEMINI_API_KEY/AI Studio's key). vertex_project MUST be the
    # project ID (the short string, e.g. "my-app-123"), not the numeric
    # project number - Vertex AI's client rejects the number.
    vertex_project: str = ""
    vertex_location: str = "us-central1"
    vertex_api_key: str = ""
    vertex_model: str = "gemini-2.5-flash"
    vertex_timeout_seconds: int = 45
    vertex_max_tokens: int = 4096
    # Optional - path to a downloaded service-account JSON key. Leave empty
    # to fall back to gcloud's local ADC login instead; set this when that's
    # not set up (e.g. gcloud CLI isn't installed) or for a deployed
    # environment where a user-login credential isn't available at all.
    vertex_credentials_path: str = ""

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
