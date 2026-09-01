"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = two levels up from this file (…/socialleaker/config.py -> …/).
ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Strongly-typed settings backed by environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "Social Leaker"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_debug: bool = False

    secret_key: str = "change-me-please-use-a-random-64-char-hex-string"
    access_token_expire_minutes: int = 720

    # --- Database ---
    database_url: str = "sqlite:///./data/socialleaker.db"

    # --- First admin user ---
    admin_username: str = "admin"
    admin_password: str = "admin"

    # --- Collection engine safeguards & pacing ---
    scrape_min_delay: float = 2.5
    scrape_max_delay: float = 6.0
    scrape_max_items: int = 2000
    # Adaptive backoff when the platform rate-limits us (seconds).
    scrape_backoff_base: float = 20.0
    scrape_backoff_max: float = 600.0
    scrape_retries: int = 5

    # --- Instagram credentials ---
    ig_username: str = ""
    ig_password: str = ""
    ig_session_file: str = "./data/ig_session.json"

    # --- Claude Code ACP bridge ---
    acp_agent_cmd: str = "claude-code-acp"
    acp_cwd: str = "."
    acp_auto_approve: bool = True

    @property
    def resolved_db_path(self) -> Path | None:
        """Absolute path to the SQLite file, or None for non-sqlite URLs."""
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            raw = self.database_url[len(prefix):]
            p = Path(raw)
            return p if p.is_absolute() else (ROOT_DIR / p)
        return None


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()
