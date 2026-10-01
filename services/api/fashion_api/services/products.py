"""Карточка товара: подтверждённые характеристики, продавцы, размеры с системой, свежесть."""
from __future__ import annotations

import psycopg

from .search import _size_order


def get_product(conn: psycopg.Connection, product_id) -> dict | None:
    p = conn.execute(
        """SELECT p.*, b.name AS brand_name, b.slug AS brand_slug, c.code AS category_code, c.title AS category_title
           FROM products p LEFT JOIN brands b ON b.id = p.brand_id LEFT JOIN categories c ON c.id = p.category_id
           WHERE p.id = %s""",
        (product_id,),
    ).fetchone()
    if p is None:
        return None
    offers = conn.execute(
        """SELECT o.*, m.name AS merchant_name, m.slug AS merchant_slug,
                  (o.last_seen_at >= now() - s.stale_after) AS fresh
           FROM offers o JOIN merchants m ON m.id = o.merchant_id JOIN feed_sources s ON s.id = o.source_id
           WHERE o.product_id = %s AND m.enabled
           ORDER BY o.active DESC, o.price_minor, o.id""",
        (product_id,),
    ).fetchall()
    offer_ids = [o["id"] for o in offers]
    variants: dict = {}
    for v in conn.execute(
        "SELECT * FROM offer_variants WHERE offer_id = ANY(%s)", (offer_ids,)
    ).fetchall():
        variants.setdefault(v["offer_id"], []).append(v)
    images = conn.execute(
        """SELECT i.url FROM product_images i JOIN offers o ON o.id = i.offer_id
           WHERE i.product_id = %s ORDER BY o.active DESC, i.offer_id, i.position""",
        (product_id,),
    ).fetchall()
    attrs = conn.execute(
        """SELECT a.key, a.value_json, a.provenance, a.confidence, m.name AS merchant
           FROM product_attributes a
           LEFT JOIN feed_sources s ON s.id = a.source_id LEFT JOIN merchants m ON m.id = s.merchant_id
           WHERE a.product_id = %s ORDER BY a.key, a.provenance""",
        (product_id,),
    ).fetchall()

    seen, image_urls = set(), []
    for row in images:
        if row["url"] not in seen:
            seen.add(row["url"])
            image_urls.append(row["url"])

    def offer_out(o):
        sizes = sorted(
            (
                {"system": v["size_system"], "label": v["size_label"], "raw": v["size_raw"],
                 "availability": v["availability"], "price_minor": v["price_minor"] or o["price_minor"]}
                for v in variants.get(o["id"], []) if v["size_label"] is not None
            ),
            key=_size_order,
        )
        return {
            "offer_id": str(o["id"]),
            "merchant": o["merchant_name"],
            "merchant_slug": o["merchant_slug"],
            "title": o["title"],
            "price_minor": o["price_minor"],
            "old_price_minor": o["old_price_minor"] if o["old_price_minor"] and o["old_price_minor"] > o["price_minor"] else None,
            "currency": o["currency"],
            "availability": o["availability"],
            "active": o["active"],
            "freshness": "fresh" if o["fresh"] else "stale",
            "last_seen_at": o["last_seen_at"],
            "imported_at": o["imported_at"],
            "sizes": sizes,
            "outbound_url": f"/api/v1/out/{o['id']}" if o["active"] else None,
        }

    out_offers = [offer_out(o) for o in offers]
    active = [o for o in out_offers if o["active"]]
    return {
        "product_id": str(p["id"]),
        "title": p["title"],
        "description": p["description"],
        "brand": {"name": p["brand_name"], "slug": p["brand_slug"]} if p["brand_name"] else None,
        "category": {"code": p["category_code"], "title": p["category_title"]} if p["category_code"] else None,
        "gender": p["gender"],
        "color": p["color"],
        "model_code": p["model_code"],
        "is_demo": p["is_demo"],
        "status": "active" if active else "discontinued",
        "images": image_urls,
        "attributes": [
            {"key": a["key"], "value": a["value_json"], "provenance": a["provenance"],
             "confidence": float(a["confidence"]), "merchant": a["merchant"]}
            for a in attrs
        ],
        "offers": out_offers,
    }
