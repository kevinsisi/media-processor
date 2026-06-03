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
    llm_model: str = Field(default="gemini-2.5-flash")
    llm_timeout_s: float = Field(default=30.0)

    # OpenCode provider — primary text AI route. Falls back to Gemini key pool
    # when OpenCode is unavailable. Password stays env-only (HomeProject canonical
    # deployment at provider-amd.sisihome.org is no-auth).
    opencode_servers: str = Field(default="")  # comma or newline separated base URLs
    opencode_model: str = Field(default="openai/gpt-5.5")
    opencode_variant: str = Field(default="medium")
    opencode_server_password: str = Field(default="")

    profiles_dir: str = Field(default="profiles")

    # Storage roots — bind-mounted by docker-compose.
    # MEDIA_STORAGE_DIR on the host -> /app/media in the container.
    media_root: str = Field(default="/app/media")
    assets_dir: str = Field(default="/app/media/assets")
    drafts_dir: str = Field(default="/app/media/drafts")
    uploads_dir: str = Field(default="/app/media/uploads")
    analysis_dir: str = Field(default="/app/media/analysis")
    thumbnails_dir: str = Field(default="/app/media/thumbnails")
    # M6.4 — uploaded BGM tracks (one per project). Lives under
    # ${MEDIA_STORAGE_DIR}/bgm on the host via the existing media bind mount.
    bgm_dir: str = Field(default="/app/media/bgm")
    # v0.18 — uploaded brand watermarks (one PNG per project). Lives under
    # ${MEDIA_STORAGE_DIR}/watermarks; piggybacks on the same media mount
    # so no docker-compose change is required.
    watermark_dir: str = Field(default="/app/media/watermarks")

    # Story/Narrato TTS narration. Disabled by default; story mode keeps
    # subtitle-only rendering unless the caller explicitly enables generated
    # narration and a provider is configured.
    story_tts_provider: str = Field(default="")
    story_tts_voice: str = Field(default="zh-TW-HsiaoChenNeural")
    story_tts_model: str = Field(default="edge-tts")
    story_tts_timeout_s: float = Field(default=45.0)
    story_narration_dir: str = Field(default="/app/media/story_narration")

    # Frame analysis — keyframe cache for documentary/narration Vision LLM pipeline.
    frame_cache_dir: str = Field(default="/app/media/frame_cache")

    # M4 — Whisper local STT in the worker container.
    # WHISPER_FAKE=1 swaps the engine for a deterministic canned zh-Hant
    # transcript so CI / non-GPU dev boxes can drive the rest of the pipeline.
    whisper_model: str = Field(default="medium")
    whisper_compute_type: str = Field(default="int8_float16")
    whisper_device: str = Field(default="cuda")
    whisper_fake: int = Field(default=0)

    # M4 — Vision frame sampling interval. Capped to 60 frames per asset
    # inside the service so very long clips don't blow the Vision quota.
    scene_sample_interval_ms: int = Field(default=5000)

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
