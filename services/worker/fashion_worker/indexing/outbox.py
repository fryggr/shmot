"""Обработка index_outbox: идемпотентное обновление поискового индекса по entity_version.

Этап 1: индекс — таблица search_documents (PostgreSQL FTS). OpenSearch-индексатор этапа 3
подключается сюда же, тем же контрактом (entity_id, entity_version, action).
"""
from __future__ import annotations

import logging

import psycopg

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5

# Документ: заголовок и бренд — вес A, категория/цвет/материал (русские названия) — B,
# описание — C. Коды словарей подменяются русскими алиасами из product_attributes.
UPSERT_SQL = """
INSERT INTO search_documents (product_id, entity_version, tsv, indexed_at)
SELECT p.id, %(version)s,
       setweight(to_tsvector('russian', coalesce(p.title, '')), 'A')
    || setweight(to_tsvector('russian', coalesce(b.name, '')), 'A')
    || setweight(to_tsvector('russian', coalesce(c.title, '') || ' ' || coalesce(pc.title, '')), 'B')
    || setweight(to_tsvector('russian', coalesce((
           SELECT string_agg(coalesce(a.value_json->>'raw', ''), ' ')
           FROM product_attributes a
           WHERE a.product_id = p.id AND a.key IN ('color', 'materials', 'fit', 'composition')), '')), 'B')
    || setweight(to_tsvector('russian', coalesce(p.description, '')), 'C'),
       now()
FROM products p
LEFT JOIN brands b ON b.id = p.brand_id
LEFT JOIN categories c ON c.id = p.category_id
LEFT JOIN categories pc ON pc.id = c.parent_id
WHERE p.id = %(id)s
ON CONFLICT (product_id) DO UPDATE SET
    entity_version = EXCLUDED.entity_version, tsv = EXCLUDED.tsv, indexed_at = EXCLUDED.indexed_at
WHERE search_documents.entity_version <= EXCLUDED.entity_version
"""


def process_outbox(conn: psycopg.Connection, batch_size: int = 500) -> dict:
    """Обрабатывает пачку событий. Возвращает счётчики. conn — autocommit."""
    done = failed = skipped = 0
    with conn.transaction():
        rows = conn.execute(
            """SELECT id, entity_id, entity_version, action, attempts FROM index_outbox
               WHERE status='pending' ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""",
            (batch_size,),
        ).fetchall()
        for row in rows:
            try:
                with conn.transaction():
                    current = conn.execute(
                        "SELECT version FROM products WHERE id=%s", (row["entity_id"],)
                    ).fetchone()
                    if current is None or current["version"] > row["entity_version"]:
                        # Есть более новое событие — оно и обновит документ
                        skipped += 1
                    else:
                        active = conn.execute(
                            "SELECT EXISTS (SELECT 1 FROM offers WHERE product_id=%s AND active) AS a",
                            (row["entity_id"],),
                        ).fetchone()["a"]
                        if active:
                            conn.execute(UPSERT_SQL, {"id": row["entity_id"], "version": row["entity_version"]})
                        else:
                            conn.execute(
                                "DELETE FROM search_documents WHERE product_id=%s AND entity_version <= %s",
                                (row["entity_id"], row["entity_version"]),
                            )
                        done += 1
                    conn.execute(
                        "UPDATE index_outbox SET status='done', processed_at=now(), attempts=attempts+1 WHERE id=%s",
                        (row["id"],),
                    )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                conn.execute(
                    """UPDATE index_outbox SET attempts=attempts+1, last_error=%s,
                           status = CASE WHEN attempts + 1 >= %s THEN 'failed' ELSE 'pending' END
                       WHERE id=%s""",
                    (type(exc).__name__, MAX_ATTEMPTS, row["id"]),
                )
                log.exception("outbox event failed", extra={"outbox_id": row["id"]})
    return {"processed": done, "skipped": skipped, "failed": failed, "batch": len(rows)}


def drain_outbox(conn: psycopg.Connection) -> dict:
    total = {"processed": 0, "skipped": 0, "failed": 0}
    while True:
        res = process_outbox(conn)
        for k in total:
            total[k] += res[k]
        if res["batch"] == 0 or res["processed"] + res["skipped"] == 0:
            return total


def reconcile(conn: psycopg.Connection) -> dict:
    """Сверка: опубликованные (есть активный offer) товары против проиндексированных."""
    row = conn.execute(
        """SELECT
             (SELECT count(*) FROM products p WHERE EXISTS
                (SELECT 1 FROM offers o WHERE o.product_id = p.id AND o.active)) AS published,
             (SELECT count(*) FROM search_documents) AS indexed,
             (SELECT count(*) FROM products p WHERE EXISTS
                (SELECT 1 FROM offers o WHERE o.product_id = p.id AND o.active)
                AND NOT EXISTS (SELECT 1 FROM search_documents s WHERE s.product_id = p.id)) AS missing,
             (SELECT count(*) FROM search_documents s WHERE NOT EXISTS
                (SELECT 1 FROM offers o WHERE o.product_id = s.product_id AND o.active)) AS orphaned,
             (SELECT count(*) FROM index_outbox WHERE status='pending') AS pending,
             (SELECT count(*) FROM index_outbox WHERE status='failed') AS failed,
             (SELECT extract(epoch FROM now() - min(created_at)) FROM index_outbox WHERE status='pending')
                AS oldest_pending_seconds"""
    ).fetchone()
    return dict(row) | {"in_sync": row["missing"] == 0 and row["orphaned"] == 0}


def rebuild_index(conn: psycopg.Connection) -> dict:
    """Полная перестройка: ставит в outbox все товары (восстановление индекса)."""
    with conn.transaction():
        n = conn.execute(
            """INSERT INTO index_outbox (entity_id, entity_version, action)
               SELECT p.id, p.version,
                      CASE WHEN EXISTS (SELECT 1 FROM offers o WHERE o.product_id=p.id AND o.active)
                           THEN 'upsert' ELSE 'delete' END
               FROM products p"""
        ).rowcount
        conn.execute("DELETE FROM search_documents s WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id=s.product_id)")
    return {"enqueued": n} | drain_outbox(conn)
