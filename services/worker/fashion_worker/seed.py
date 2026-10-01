"""Демонстрационные продавцы и источники. Все имена и домены вымышлены (.example)."""
from __future__ import annotations

import psycopg

from fashion_domain.taxonomy_sync import sync_categories

DEMO_MERCHANTS = [
    {"name": "Demo Shop A", "slug": "demo-shop-a", "domains": ["demo-shop-a.example"],
     "source": "demo-shop-a", "secret_ref": "env:FEED_URL_DEMO_SHOP_A"},
    {"name": "Demo Shop B", "slug": "demo-shop-b", "domains": ["demo-shop-b.example"],
     "source": "demo-shop-b", "secret_ref": "env:FEED_URL_DEMO_SHOP_B"},
]


def seed_demo(conn: psycopg.Connection) -> dict:
    sync_categories(conn)
    with conn.transaction():
        for m in DEMO_MERCHANTS:
            mid = conn.execute(
                """INSERT INTO merchants (name, slug, allowed_domains, is_demo) VALUES (%s, %s, %s, true)
                   ON CONFLICT (slug) DO UPDATE SET name=EXCLUDED.name, allowed_domains=EXCLUDED.allowed_domains
                   RETURNING id""",
                (m["name"], m["slug"], m["domains"]),
            ).fetchone()["id"]
            conn.execute(
                """INSERT INTO feed_sources (merchant_id, slug, format, secret_ref, refresh_interval, stale_after)
                   VALUES (%s, %s, 'yml', %s, interval '1 day', interval '2 days')
                   ON CONFLICT (slug) DO UPDATE SET secret_ref=EXCLUDED.secret_ref""",
                (mid, m["source"], m["secret_ref"]),
            )
    return {"merchants": len(DEMO_MERCHANTS)}
