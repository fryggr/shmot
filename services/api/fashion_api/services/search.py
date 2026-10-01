"""Поиск этапа 1 (режим A, baseline): лексический поиск PostgreSQL FTS + жёсткие фильтры.

Ключевое правило: товар подходит, только если существует ОДИН вариант одного предложения,
который одновременно удовлетворяет размеру, наличию, цене, валюте и магазину. Цена в карточке
выдачи — цена именно этого подходящего варианта (matched_offer_id).

Разбор запроса (parser) и semantic retrieval — этапы 3–4; здесь q ищется как набор слов.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from fashion_domain.config import get_settings
from fashion_domain.normalization import load_dictionaries

from ..errors import ApiError
from ..schemas import Filters, SearchRequest

EXPERIMENT_ARM = "A"
RANKING_VERSION = "lexical-pg-v0"
PARSER_VERSION = "none"
WINDOW_LIMIT = 500
CURSOR_TTL_S = 15 * 60

ORDER_BY = {
    "relevance": "score DESC, product_id",
    "price_asc": "price ASC, product_id",
    "price_desc": "price DESC, product_id",
}

# Фасет считается по подходящим товарам; снимается только фильтр самого фасета
FACETS = {"colors": "color", "categories": "category", "brands": "brand_slug", "merchants": "merchant_slug"}


# ---------------------------------------------------------------- tsquery
def build_tsquery(conn: psycopg.Connection, q: str) -> str | None:
    """Лексемы запроса (русский стеммер), объединённые через OR; ранжирование ts_rank_cd
    поднимает документы с большим числом совпавших слов."""
    q = q.strip()
    if not q:
        return None
    row = conn.execute(
        "SELECT array_agg(DISTINCT lexeme ORDER BY lexeme) AS lx FROM unnest(to_tsvector('russian', %s))",
        (q,),
    ).fetchone()
    lexemes = [lx.replace("\\", "").replace("'", "''") for lx in (row["lx"] or []) if lx]
    return " | ".join(f"'{lx}'" for lx in lexemes if lx) or None


def category_codes_with_descendants(codes: list[str]) -> list[str]:
    d = load_dictionaries()
    out = set(codes)
    changed = True
    while changed:
        changed = False
        for node in d.categories:
            if node.parent_code in out and node.code not in out:
                out.add(node.code)
                changed = True
    return sorted(out)


# ------------------------------------------------------------------- SQL
@dataclass
class QueryParams:
    tsq: str | None
    filters: Filters

    def as_dict(self, drop: str | None = None) -> dict:
        f = self.filters
        g = lambda name, value: None if drop == name else value  # noqa: E731
        size = g("size", f.size)
        return {
            "tsq": self.tsq or "",
            "currency": f.currency,
            "size_system": size.system if size else None,
            "size_label": size.label if size else None,
            "in_stock": g("availability", f.availability) == "in_stock",
            "price_min": g("price", f.price_min_minor),
            "price_max": g("price", f.price_max_minor),
            "merchants": g("merchants", f.merchants or None),
            "categories": g("categories", category_codes_with_descendants(f.categories) if f.categories else None),
            "colors": g("colors", f.colors or None),
            "materials": g("materials", f.materials or None),
            "brands": g("brands", f.brands or None),
            "gender": g("gender", f.gender or None),
        }


BASE_SQL = """
WITH q AS (
    SELECT CASE WHEN %(tsq)s = '' THEN NULL ELSE to_tsquery('simple', %(tsq)s) END AS tsq
),
elig AS (
    SELECT o.product_id, o.id AS offer_id, m.name AS merchant, m.slug AS merchant_slug,
           coalesce(v.price_minor, o.price_minor) AS price,
           CASE WHEN v.id IS NULL THEN o.old_price_minor ELSE v.old_price_minor END AS old_price,
           coalesce(v.availability, o.availability) AS availability,
           (o.last_seen_at >= now() - s.stale_after) AS fresh
    FROM offers o
    JOIN feed_sources s ON s.id = o.source_id AND s.enabled
    JOIN merchants m ON m.id = o.merchant_id AND m.enabled
    LEFT JOIN offer_variants v ON v.offer_id = o.id
    WHERE o.active
      AND o.currency = %(currency)s
      AND (%(size_system)s::text IS NULL
           OR (v.size_system = %(size_system)s AND v.size_label = %(size_label)s))
      AND (NOT %(in_stock)s
           OR (coalesce(v.availability, o.availability) = 'in_stock'
               AND o.last_seen_at >= now() - s.stale_after))
      AND (%(price_min)s::bigint IS NULL OR coalesce(v.price_minor, o.price_minor) >= %(price_min)s)
      AND (%(price_max)s::bigint IS NULL OR coalesce(v.price_minor, o.price_minor) <= %(price_max)s)
      AND (%(merchants)s::text[] IS NULL OR m.slug = ANY(%(merchants)s))
),
best AS (
    -- Один подходящий вариант на товар: сначала свежий и в наличии, затем дешевле
    SELECT DISTINCT ON (product_id) *
    FROM elig
    ORDER BY product_id, (availability = 'in_stock' AND fresh) DESC, price, offer_id
),
matched AS (
    SELECT b.*, p.title, p.color, p.is_demo, br.name AS brand, br.slug AS brand_slug, c.code AS category,
           CASE WHEN q.tsq IS NULL THEN 0 ELSE ts_rank_cd(sd.tsv, q.tsq) END AS score
    FROM best b
    JOIN products p ON p.id = b.product_id
    JOIN search_documents sd ON sd.product_id = p.id
    CROSS JOIN q
    LEFT JOIN brands br ON br.id = p.brand_id
    LEFT JOIN categories c ON c.id = p.category_id
    WHERE (q.tsq IS NULL OR sd.tsv @@ q.tsq)
      AND (%(categories)s::text[] IS NULL OR c.code = ANY(%(categories)s))
      AND (%(colors)s::text[] IS NULL OR p.color = ANY(%(colors)s))
      AND (%(brands)s::text[] IS NULL OR br.slug = ANY(%(brands)s))
      AND (%(gender)s::text[] IS NULL OR p.gender = ANY(%(gender)s))
      -- Материал — жёсткий фильтр только по подтверждённому атрибуту; неизвестный состав не проходит
      AND (%(materials)s::text[] IS NULL OR EXISTS (
            SELECT 1 FROM product_attributes a
            WHERE a.product_id = p.id AND a.key = 'materials'
              AND a.provenance IN ('merchant', 'manual', 'rule')
              AND a.value_json -> 'codes' ?| %(materials)s))
)
"""


def _facet_sql(column: str) -> str:
    return BASE_SQL + (
        f"SELECT {column} AS value, count(*) AS count FROM matched "
        f"WHERE {column} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 50"
    )


# ----------------------------------------------------------------- cursor
def _fingerprint(req: SearchRequest) -> str:
    doc = {"q": req.q.strip(), "f": req.filters.model_dump(mode="json"), "s": req.sort,
           "d": sorted(req.disabled_inferred_filters), "arm": EXPERIMENT_ARM, "rv": RANKING_VERSION}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


def _sign(payload: bytes) -> str:
    return hmac.new(get_settings().cursor_secret.encode(), payload, hashlib.sha256).hexdigest()[:32]


def encode_cursor(offset: int, fingerprint: str, index_version: int) -> str:
    payload = json.dumps({"o": offset, "f": fingerprint, "iv": index_version, "t": int(time.time())},
                         separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + _sign(payload)


def decode_cursor(cursor: str, fingerprint: str, index_version: int) -> int:
    try:
        body, sig = cursor.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        data = json.loads(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ApiError(422, "invalid_cursor", "cursor is malformed") from exc
    if not hmac.compare_digest(sig, _sign(payload)):
        raise ApiError(422, "invalid_cursor", "cursor signature mismatch")
    if data.get("f") != fingerprint:
        raise ApiError(422, "cursor_mismatch", "cursor belongs to a different query, filters or sort")
    if time.time() - data.get("t", 0) > CURSOR_TTL_S or data.get("iv") != index_version:
        raise ApiError(410, "cursor_expired", "results changed or cursor expired; restart the search")
    return int(data["o"])


def index_version(conn) -> int:
    return conn.execute("SELECT coalesce(max(id), 0) AS v FROM index_outbox").fetchone()["v"]


# ----------------------------------------------------------------- search
def search(conn: psycopg.Connection, req: SearchRequest) -> dict:
    fp = _fingerprint(req)
    iv = index_version(conn)
    offset = decode_cursor(req.cursor, fp, iv) if req.cursor else 0
    params = QueryParams(build_tsquery(conn, req.q), req.filters)
    base = params.as_dict()

    rows = conn.execute(
        BASE_SQL + f"SELECT * FROM matched ORDER BY {ORDER_BY[req.sort]} LIMIT %(window)s",
        base | {"window": WINDOW_LIMIT + 1},
    ).fetchall()
    total = len(rows)
    window = rows[:WINDOW_LIMIT]
    page = window[offset: offset + req.limit]
    next_offset = offset + req.limit
    next_cursor = encode_cursor(next_offset, fp, iv) if next_offset < len(window) else None

    facets = {}
    for name, column in FACETS.items():
        facet_rows = conn.execute(_facet_sql(column), params.as_dict(drop=name)).fetchall()
        facets[name] = [{"value": r["value"], "count": r["count"]} for r in facet_rows]

    relaxations = []
    if total == 0:
        active = {
            "size": req.filters.size is not None,
            "availability": req.filters.availability == "in_stock",
            "price": req.filters.price_min_minor is not None or req.filters.price_max_minor is not None,
            "categories": bool(req.filters.categories), "colors": bool(req.filters.colors),
            "materials": bool(req.filters.materials), "brands": bool(req.filters.brands),
            "merchants": bool(req.filters.merchants), "gender": bool(req.filters.gender),
        }
        for name, on in active.items():
            if on:
                n = conn.execute(BASE_SQL + "SELECT count(*) AS n FROM matched", params.as_dict(drop=name)).fetchone()["n"]
                if n:
                    relaxations.append({"filter": name, "count": n})

    items = _hydrate(conn, page, req.filters)
    search_id = uuid.uuid4()
    parsed = {
        "hard_filters": req.filters.model_dump(mode="json", exclude_defaults=True),
        "soft_preferences": {}, "excluded": {}, "uncertain": [], "parser_version": PARSER_VERSION,
    }
    conn.execute(
        """INSERT INTO search_sessions (id, anonymous_session_id, experiment_arm, query, parsed_json, ranking_version)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (search_id, req.anonymous_session_id, EXPERIMENT_ARM, req.q, Jsonb(parsed), RANKING_VERSION),
    )
    return {
        "search_id": str(search_id),
        "experiment_arm": EXPERIMENT_ARM,
        "parsed": parsed,
        "items": items,
        "total": {"value": min(total, WINDOW_LIMIT), "relation": "gte" if total > WINDOW_LIMIT else "eq"},
        "window_limit": WINDOW_LIMIT,
        "facets": facets,
        "relaxations": relaxations,
        "next_cursor": next_cursor,
        "degraded_mode": False,
        "ranking_version": RANKING_VERSION,
        "demo_data": any(i["is_demo"] for i in items),
    }


def _hydrate(conn, page: list[dict], filters: Filters) -> list[dict]:
    if not page:
        return []
    offer_ids = [r["offer_id"] for r in page]
    in_stock_only = filters.availability == "in_stock"
    sizes: dict = {}
    for r in conn.execute(
        """SELECT offer_id, size_system, size_label FROM offer_variants
           WHERE offer_id = ANY(%s) AND size_label IS NOT NULL AND (NOT %s OR availability = 'in_stock')
           ORDER BY offer_id, size_system, size_label""",
        (offer_ids, in_stock_only),
    ).fetchall():
        sizes.setdefault(r["offer_id"], []).append({"system": r["size_system"], "label": r["size_label"]})
    images = {
        r["offer_id"]: r["url"]
        for r in conn.execute(
            "SELECT DISTINCT ON (offer_id) offer_id, url FROM product_images WHERE offer_id = ANY(%s) ORDER BY offer_id, position",
            (offer_ids,),
        ).fetchall()
    }
    out = []
    for r in page:
        old = r["old_price"] if r["old_price"] and r["old_price"] > r["price"] else None
        out.append({
            "product_id": str(r["product_id"]),
            "title": r["title"],
            "brand": r["brand"],
            "brand_slug": r["brand_slug"],
            "category": r["category"],
            "color": r["color"],
            "image_url": images.get(r["offer_id"]),
            "matched_offer_id": str(r["offer_id"]),
            "price_minor": r["price"],
            "old_price_minor": old,
            "currency": filters.currency,
            "merchant": r["merchant"],
            "merchant_slug": r["merchant_slug"],
            "availability": r["availability"],
            "available_sizes": sorted(sizes.get(r["offer_id"], []), key=_size_order),
            "freshness": "fresh" if r["fresh"] else "stale",
            "outbound_url": f"/api/v1/out/{r['offer_id']}",
            "is_demo": r["is_demo"],
            "score": float(r["score"]),
        })
    return out


_INT_ORDER = {s: i for i, s in enumerate(("XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL"))}


def _size_order(s: dict):
    label = s["label"] or ""
    num = label.replace(",", ".")
    try:
        return (s["system"] or "", 0, float(num), label)
    except ValueError:
        return (s["system"] or "", 1, _INT_ORDER.get(label, 99), label)
