"""Интеграционные тесты против настоящего PostgreSQL (+pgvector).

Нужна переменная TEST_DATABASE_URL с правами на создание базы, например
postgresql://postgres:postgres@localhost:5432/postgres. Для каждой сессии тестов создаётся
отдельная база, перед каждым тестом таблицы очищаются и заново заводятся демо-источники.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get("TEST_DATABASE_URL")
if not ADMIN_URL:
    pytest.exit("TEST_DATABASE_URL is not set (see README: Тесты)", returncode=2)

import psycopg  # noqa: E402
from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: E402

DB_NAME = f"fashion_test_{uuid.uuid4().hex[:8]}"
TEST_URL = make_conninfo(ADMIN_URL, dbname=DB_NAME)

os.environ["DATABASE_URL"] = TEST_URL
os.environ["INTERNAL_API_TOKEN"] = "test-internal-token"
os.environ["CURSOR_SECRET"] = "test-cursor-secret"
os.environ["RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ.pop("OBJECT_STORAGE_ENDPOINT", None)

TABLES = [
    "events", "search_sessions", "index_outbox", "search_documents", "dedup_links", "product_embeddings",
    "product_images", "offer_variants", "offers", "product_attributes", "products", "category_mappings",
    "brand_aliases", "brands", "raw_records", "staging_records", "import_runs", "feed_sources", "merchants",
]


@pytest.fixture(scope="session", autouse=True)
def database(tmp_path_factory):
    admin = conninfo_to_dict(ADMIN_URL)
    with psycopg.connect(make_conninfo(**admin), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{DB_NAME}"')
    from fashion_domain.config import get_settings

    get_settings.cache_clear()
    from fashion_domain.db import connect, migrate

    with connect(TEST_URL, autocommit=True) as conn:
        migrate(conn)
    yield TEST_URL
    from fashion_api import deps

    deps.close_pool()
    with psycopg.connect(make_conninfo(**admin), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Изолированные снимки и копия фикстур (тесты могут добавлять свои фиды)."""
    fixtures = tmp_path / "feeds"
    shutil.copytree(REPO / "tests" / "fixtures" / "feeds", fixtures)
    monkeypatch.setenv("FASHION_FIXTURES_DIR", str(fixtures))
    monkeypatch.setenv("FASHION_SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setenv("FEED_URL_DEMO_SHOP_A", "fixture://demo_shop_a_v1.yml")
    monkeypatch.setenv("FEED_URL_DEMO_SHOP_B", "fixture://demo_shop_b_v1.yml")
    from fashion_domain.config import get_settings

    get_settings.cache_clear()
    yield fixtures
    get_settings.cache_clear()


@pytest.fixture
def conn(database, env):
    from fashion_domain.db import connect
    from fashion_worker.seed import seed_demo

    with connect(database, autocommit=True) as c:
        c.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        seed_demo(c)
        yield c


@pytest.fixture
def feeds(env):
    return env


class Catalog:
    """Помощник: импорт + индексация, как это делает worker."""

    def __init__(self, conn, monkeypatch):
        self.conn = conn
        self.monkeypatch = monkeypatch

    def import_(self, source: str, fixture: str | None = None, **kw):
        from fashion_domain.config import get_settings
        from fashion_worker.indexing.outbox import drain_outbox
        from fashion_worker.jobs.import_job import run_import

        if fixture:
            env_name = "FEED_URL_" + source.upper().replace("-", "_")
            self.monkeypatch.setenv(env_name, f"fixture://{fixture}")
        run = run_import(self.conn, source, settings=get_settings(), **kw)
        drain_outbox(self.conn)
        return run

    def demo(self):
        a = self.import_("demo-shop-a")
        b = self.import_("demo-shop-b")
        assert a["status"] == "published", a["errors"]
        assert b["status"] == "published", b["errors"]
        return a, b

    def scalar(self, sql: str, *params):
        row = self.conn.execute(sql, params).fetchone()
        return next(iter(row.values()))

    def snapshot(self) -> dict:
        """Состояние опубликованного каталога для сравнения «до/после»."""
        return {
            "offers": self.conn.execute(
                "SELECT external_id, active, price_minor, availability, content_hash FROM offers ORDER BY external_id"
            ).fetchall(),
            "variants": self.conn.execute(
                "SELECT id, external_variant_id, price_minor, availability FROM offer_variants ORDER BY external_variant_id"
            ).fetchall(),
            "products": self.scalar("SELECT count(*) FROM products"),
            "docs": self.scalar("SELECT count(*) FROM search_documents"),
        }


@pytest.fixture
def catalog(conn, monkeypatch):
    return Catalog(conn, monkeypatch)


class FakeRedis:
    def __init__(self):
        self.items = []

    def rpush(self, key, value):
        self.items.append((key, value))

    def ping(self):
        return True


@pytest.fixture
def client(conn):
    from fastapi.testclient import TestClient

    from fashion_api import deps
    from fashion_api.main import create_app

    fake = FakeRedis()
    deps.set_redis(fake)
    with TestClient(create_app()) as c:
        c.fake_redis = fake
        yield c
    deps.set_redis(None)
