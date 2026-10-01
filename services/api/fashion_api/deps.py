"""Зависимости API: пул соединений PostgreSQL, Redis, настройки."""
from __future__ import annotations

from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from fashion_domain.config import get_settings

from .errors import ApiError

_pool: ConnectionPool | None = None
_redis = None


def open_pool(database_url: str | None = None) -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            database_url or get_settings().database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row, "autocommit": True},
            open=False,
        )
        _pool.open(wait=False)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_conn() -> Iterator[psycopg.Connection]:
    pool = open_pool()
    try:
        with pool.connection(timeout=5) as conn:
            yield conn
    except psycopg.OperationalError as exc:
        raise ApiError(503, "database_unavailable", "database is unavailable") from exc
    except TimeoutError as exc:  # psycopg_pool.PoolTimeout наследует TimeoutError
        raise ApiError(503, "database_unavailable", "database is unavailable") from exc


def get_redis():
    global _redis
    if _redis is None:
        import redis

        _redis = redis.Redis.from_url(get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2)
    return _redis


def set_redis(client) -> None:
    """Для тестов: подменить клиент Redis."""
    global _redis
    _redis = client
