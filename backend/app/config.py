"""Runtime settings, read from environment variables prefixed PATHGUARD_."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PATHGUARD_", env_file=".env", extra="ignore")

    env: Literal["local", "test", "production"] = "local"
    database_url: str = "postgresql+psycopg://pathguard:pathguard@127.0.0.1:26432/pathguard"
    cors_origins: list[str] = ["http://localhost:5600", "http://localhost:3600"]
    # The seeded demo identity is a local convenience only; production rejects it.
    demo_identity_enabled: bool = True
    # Public demo hosting: each browser gets its own anonymous visitor account in a signed
    # cookie, so visitors only ever see their own projects. Not a login system.
    visitor_identity_enabled: bool = False
    secret_key: str = ""  # signs visitor cookies; required when visitor identity is on
    visitor_retention_days: int = 7  # visitor projects untouched this long are deleted
    visitor_project_limit: int = 20
    visitor_seed_examples: bool = True  # give each new visitor the two checkout examples
    # Built web app to serve from the API process (single-container hosting).
    static_dir: str | None = None
    # No project data leaves the machine unless this is switched on explicitly.
    external_processing_enabled: bool = False
    # Optional AI suggestions: off by default. Runs only when a person asks for it.
    ai_enabled: bool = False
    ai_provider: Literal["ollama"] = "ollama"
    ai_base_url: str = "http://127.0.0.1:11434"  # local Ollama; any other host counts as remote
    ai_model: str = "qwen2.5:1.5b"
    ai_timeout_seconds: float = 120  # a small local model on a CPU can take ~30–60 s
    ai_max_input_chars: int = 6000
    ai_max_output_tokens: int = 700
    # Learn from your past reviewed risks (no training): how many accepted examples and
    # rejected ideas from your other projects go into each AI request. 0 turns it off.
    ai_examples: int = 4
    ai_avoid: int = 3
    ai_use_library: bool = True  # also learn from the built-in 100-case risk library

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, url: str) -> str:
        """Hosted Postgres providers hand out postgres:// URLs; use the psycopg 3 driver."""
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    @model_validator(mode="after")
    def _guards(self) -> "Settings":
        if self.env == "production" and self.demo_identity_enabled:
            raise ValueError("PATHGUARD_DEMO_IDENTITY_ENABLED must be false in production")
        if self.demo_identity_enabled and self.visitor_identity_enabled:
            raise ValueError("Enable either demo identity or visitor identity, not both")
        if self.visitor_identity_enabled and len(self.secret_key) < 32:
            raise ValueError("PATHGUARD_SECRET_KEY must be at least 32 characters "
                             "when visitor identity is enabled")
        return self

    @property
    def identity_mode(self) -> Literal["demo", "visitor", "none"]:
        if self.demo_identity_enabled:
            return "demo"
        return "visitor" if self.visitor_identity_enabled else "none"


@lru_cache
def get_settings() -> Settings:
    return Settings()
