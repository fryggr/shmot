"""Публичные эндпоинты /api/v1."""
from __future__ import annotations

import logging
import uuid

import psycopg
from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import RedirectResponse

from fashion_domain.normalization import SIZE_SYSTEMS, load_dictionaries
from fashion_domain.urls import is_allowed_merchant_url

from ..deps import get_conn, open_pool
from ..errors import ApiError
from ..schemas import SearchRequest, SearchResponse
from ..services import products as product_service
from ..services import search as search_service

router = APIRouter(prefix="/api/v1")
log = logging.getLogger("fashion_api")


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ApiError(404, "not_found", "not found") from exc


@router.post("/search", response_model=SearchResponse)
def search(req: SearchRequest, conn: psycopg.Connection = Depends(get_conn)):
    try:
        return search_service.search(conn, req)
    except psycopg.OperationalError as exc:
        raise ApiError(503, "search_unavailable", "search is temporarily unavailable") from exc


@router.get("/products/{product_id}")
def product(product_id: str, conn: psycopg.Connection = Depends(get_conn)):
    data = product_service.get_product(conn, _uuid(product_id))
    if data is None:
        raise ApiError(404, "not_found", "product not found")
    return data


@router.get("/brands/{slug}")
def brand(slug: str, conn: psycopg.Connection = Depends(get_conn)):
    row = conn.execute(
        """SELECT b.name, b.slug,
                  count(DISTINCT p.id) FILTER (WHERE o.active) AS product_count,
                  bool_or(p.is_demo) AS is_demo
           FROM brands b LEFT JOIN products p ON p.brand_id = b.id LEFT JOIN offers o ON o.product_id = p.id
           WHERE b.slug = %s GROUP BY b.id""",
        (slug,),
    ).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "brand not found")
    # Индексация страниц брендов включается только после проверки ассортимента (не на этапе 1)
    return dict(row) | {"indexable": False}


@router.get("/dictionaries")
def dictionaries():
    d = load_dictionaries()
    return {
        "versions": d.versions,
        "categories": [
            {"code": c.code, "title": c.title, "parent": c.parent_code, "size_kind": c.size_kind}
            for c in d.categories
        ],
        "colors": [{"code": k, "title": v[0]} for k, v in d.colors.items()],
        "materials": [{"code": k, "title": v[0]} for k, v in d.materials.items()],
        "size_systems": list(SIZE_SYSTEMS),
        "availability": ["in_stock", "any"],
        "sorts": list(search_service.ORDER_BY),
    }


def _log_outbound(offer_id, product_id) -> None:
    """Серверная запись перехода; не блокирует редирект. Пользовательский успех считается
    только по клиентскому событию merchant_outbound_clicked (этап 5)."""
    try:
        with open_pool().connection(timeout=2) as conn:
            conn.execute(
                """INSERT INTO events (event_type, product_id, offer_id, dedup_key)
                   VALUES ('outbound_redirect_served', %s, %s, %s)""",
                (product_id, offer_id, f"redirect:{uuid.uuid4()}"),
            )
    except Exception:  # noqa: BLE001
        log.warning("outbound event not logged", extra={"offer_id": str(offer_id)})


@router.get("/out/{offer_id}")
def outbound(offer_id: str, background: BackgroundTasks, conn: psycopg.Connection = Depends(get_conn)):
    """302 только на URL из базы, прошедший проверку доменов продавца. Произвольный URL не принимается."""
    row = conn.execute(
        """SELECT o.id, o.product_id, o.product_url, o.affiliate_url, o.active,
                  m.allowed_domains, m.affiliate_domains, m.enabled
           FROM offers o JOIN merchants m ON m.id = o.merchant_id WHERE o.id = %s""",
        (_uuid(offer_id),),
    ).fetchone()
    if row is None or not row["enabled"]:
        raise ApiError(404, "not_found", "offer not found")
    if not row["active"]:
        raise ApiError(410, "offer_inactive", "offer is no longer available")
    target = None
    if row["affiliate_url"] and is_allowed_merchant_url(row["affiliate_url"], row["affiliate_domains"]):
        target = row["affiliate_url"]
    elif is_allowed_merchant_url(row["product_url"], row["allowed_domains"]):
        target = row["product_url"]
    if target is None:
        log.warning("outbound url rejected", extra={"offer_id": str(row["id"])})
        raise ApiError(404, "outbound_not_allowed", "merchant url is not allowed")
    background.add_task(_log_outbound, row["id"], row["product_id"])
    return RedirectResponse(
        target, status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "origin", "X-Robots-Tag": "noindex"},
    )
