"""Central configuration for the ACE Studio v2 backend.

Everything is local-first: paths live under <repo>/data, which is created on first run
and holds the SQLite database, caches, saved prompts and licences. No settings require
the network to start the app.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# <repo>/backend/app/core/config.py  ->  <repo>
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime settings. Override any field with an ACE_ env var (e.g. ACE_PORT)."""

    model_config = SettingsConfigDict(env_prefix="ACE_", env_file=".env", extra="ignore")

    app_name: str = "ACE Studio"
    version: str = "2.7.0-final-hardening"

    host: str = "127.0.0.1"
    # v2 runs on 8766 so it never collides with the legacy studio/ app on 8765.
    port: int = 8766

    repo_root: Path = _REPO_ROOT
    data_dir: Path = _REPO_ROOT / "data"

    # Vite dev server origin, allowed through CORS while developing. In production the
    # built frontend is served by FastAPI itself, so CORS is a non-issue.
    frontend_dev_origin: str = "http://localhost:5173"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ace_studio_v2.db"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
