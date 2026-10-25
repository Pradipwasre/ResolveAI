"""Application settings, loaded from environment variables (or a .env file)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ResolveAI"
    client_name: str = "UrbanCart"
    environment: str = "local"
    log_level: str = "INFO"

    # Infrastructure
    database_url: str = "postgresql+asyncpg://resolveai:resolveai@localhost:5432/resolveai"
    redis_url: str = "redis://localhost:6379/0"

    # LLM provider: "openai" for real calls, "fake" for offline demo and tests
    llm_provider: str = "fake"
    openai_api_key: str = ""
    llm_model_fast: str = "gpt-5.4-mini"
    llm_model_smart: str = "gpt-5.4"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    transcribe_model: str = "gpt-4o-mini-transcribe"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    llm_timeout_seconds: float = 45.0
    use_moderation_api: bool = True

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = "change-me-webhook-secret"
    public_base_url: str = ""

    # Dashboard auth
    jwt_secret: str = "change-me-jwt-secret-at-least-32-characters"
    jwt_expire_minutes: int = 720
    admin_email: str = "admin@resolveai.dev"
    admin_password: str = "ChangeMe123!"

    # Cost management (USD)
    daily_budget_usd: float = 5.0
    per_customer_daily_budget_usd: float = 0.25

    # Rate limiting
    rate_limit_messages: int = 10
    rate_limit_window_seconds: int = 60

    # Memory management
    memory_window_messages: int = 10
    memory_ttl_seconds: int = 60 * 60 * 24
    max_context_tokens: int = 3000

    # RAG
    rag_top_k: int = 4
    rag_candidates: int = 20
    rag_min_similarity: float = 0.30  # best match below this means "not in the knowledge base": escalate
    rag_min_similarity_fake: float = 0.08  # the offline fake embeddings score lower

    @property
    def retrieval_threshold(self) -> float:
        return self.rag_min_similarity if self.llm_provider == "openai" else self.rag_min_similarity_fake

    rag_rerank: str = "rrf"  # "rrf" or "llm"
    answer_cache_ttl_seconds: int = 60 * 60 * 24

    # Behaviour
    auto_resolve_minutes: int = 30  # AI-handled tickets idle this long are closed as resolved by AI
    confidence_threshold: float = 0.55
    refund_auto_approve_limit_inr: float = 0.0  # 0 means every refund needs a human
    knowledge_dir: str = "data/knowledge"
    run_startup_tasks: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    static_dir: str = "static"


@lru_cache
def get_settings() -> Settings:
    return Settings()
