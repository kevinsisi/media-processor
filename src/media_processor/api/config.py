"""Application settings loaded from environment via pydantic-settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = Field(...)
    postgres_password: str = Field(...)
    postgres_db: str = Field(...)
    postgres_host: str = Field(default="postgres")
    postgres_port: int = Field(default=5432)

    redis_host: str = Field(default="redis")
    redis_port: int = Field(default=6379)

    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)

    # Stage 4.5 LLM Patcher — reads a comma-separated key pool (ai-core style)
    # so callers can rotate across multiple Gemini API keys on quota errors.
    # Empty pool disables the /drafts/{id}/patch endpoint at request time.
    llm_api_keys: str = Field(default="")
    llm_model: str = Field(default="gemini-2.0-flash")
    llm_timeout_s: float = Field(default=30.0)

    profiles_dir: str = Field(default="profiles")

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"


settings = Settings()  # type: ignore[call-arg]
