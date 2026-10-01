"""Синхронизация справочника категорий из config/taxonomy.yaml в таблицу categories."""
from __future__ import annotations

import psycopg

from .normalization import load_dictionaries


def sync_categories(conn: psycopg.Connection) -> int:
    d = load_dictionaries()
    with conn.transaction():
        for node in sorted(d.categories, key=lambda c: c.depth):
            conn.execute(
                """
                INSERT INTO categories (code, title, parent_id)
                VALUES (%(code)s, %(title)s, (SELECT id FROM categories WHERE code = %(parent)s))
                ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title, parent_id = EXCLUDED.parent_id
                """,
                {"code": node.code, "title": node.title, "parent": node.parent_code},
            )
    return len(d.categories)
