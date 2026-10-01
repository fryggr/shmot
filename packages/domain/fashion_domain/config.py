"""Настройки из переменных окружения. Значения секретов никогда не логируются."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    config_dir: Path
    migrations_dir: Path
    fixtures_dir: Path
    snapshot_dir: Path
    object_storage_endpoint: str | None
    object_storage_bucket: str | None
    object_storage_access_key: str | None
    object_storage_secret_key: str | None
    internal_api_token: str | None
    cursor_secret: str
    allowed_currencies: tuple[str, ...] = ("RUB",)
    # Защитные лимиты разбора фида
    max_snapshot_bytes: int = 512 * 1024 * 1024
    max_records: int = 2_000_000
    max_xml_depth: int = 32
    download_timeout_s: float = 60.0
    feed_allowed_hosts: tuple[str, ...] = field(default_factory=tuple)


@lru_cache
def get_settings() -> Settings:
    config_dir = Path(_env("FASHION_CONFIG_DIR", str(REPO_ROOT / "config")))
    return Settings(
        database_url=_env("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fashion"),
        redis_url=_env("REDIS_URL", "redis://localhost:6379/0"),
        config_dir=config_dir,
        migrations_dir=Path(_env("FASHION_MIGRATIONS_DIR", str(REPO_ROOT / "db" / "migrations"))),
        fixtures_dir=Path(_env("FASHION_FIXTURES_DIR", str(REPO_ROOT / "tests" / "fixtures" / "feeds"))),
        snapshot_dir=Path(_env("FASHION_SNAPSHOT_DIR", str(REPO_ROOT / ".data" / "snapshots"))),
        object_storage_endpoint=_env("OBJECT_STORAGE_ENDPOINT"),
        object_storage_bucket=_env("OBJECT_STORAGE_BUCKET"),
        object_storage_access_key=_env("OBJECT_STORAGE_ACCESS_KEY"),
        object_storage_secret_key=_env("OBJECT_STORAGE_SECRET_KEY"),
        internal_api_token=_env("INTERNAL_API_TOKEN"),
        cursor_secret=_env("CURSOR_SECRET", "dev-only-cursor-secret"),
        feed_allowed_hosts=tuple(
            h.strip().lower() for h in (_env("FEED_ALLOWED_HOSTS", "") or "").split(",") if h.strip()
        ),
    )
