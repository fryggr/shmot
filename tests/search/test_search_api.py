"""Поиск и API: условия выполняются в ОДНОМ варианте одного предложения, цена «от» после фильтра,
неизвестное наличие, устаревший источник, курсоры, outbound allowlist, internal auth."""
from __future__ import annotations

import json
import uuid

import pytest


def search(client, q="", **filters):
    body = {"q": q, "filters": filters, "limit": 48}
    r = client.post("/api/v1/search", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def by_title(data, title):
    return [i for i in data["items"] if i["title"] == title]


@pytest.fixture
def demo(catalog, client):
    catalog.demo()
    return client


# --------------------------------------------------------------- eligibility
def test_size_and_price_must_match_in_the_same_variant(demo):
    # Косуха: S=25 000 (в наличии), M=35 000 (в наличии), L=24 000 (нет в наличии)
    m = search(demo, "косуха", size={"system": "INT", "label": "M"}, price_max_minor=3_000_000)
    assert by_title(m, "Кожаная куртка косуха") == []

    s = search(demo, "косуха", size={"system": "INT", "label": "S"}, price_max_minor=3_000_000)
    [item] = by_title(s, "Кожаная куртка косуха")
    assert item["price_minor"] == 2_500_000

    l_stock = search(demo, "косуха", size={"system": "INT", "label": "L"}, availability="in_stock")
    assert by_title(l_stock, "Кожаная куртка косуха") == []
    l_any = search(demo, "косуха", size={"system": "INT", "label": "L"})
    [item] = by_title(l_any, "Кожаная куртка косуха")
    assert item["price_minor"] == 2_400_000 and item["availability"] == "out_of_stock"


def test_size_from_one_shop_and_price_from_another_are_not_combined(demo):
    # Чёрные лоферы ND-L100: магазин A — 38 EU за 25 000; магазин B — 38 EU нет в наличии, 39 EU за 15 000
    cheap = search(demo, "лоферы", size={"system": "EU", "label": "38"}, price_max_minor=2_000_000,
                   availability="in_stock")
    assert by_title(cheap, "Лоферы кожаные") == []

    ok = search(demo, "лоферы", size={"system": "EU", "label": "38"}, price_max_minor=3_000_000,
                availability="in_stock")
    [item] = by_title(ok, "Лоферы кожаные")
    assert item["merchant"] == "Demo Shop A" and item["price_minor"] == 2_500_000

    product = demo.get(f"/api/v1/products/{item['product_id']}").json()
    assert item["matched_offer_id"] in {o["offer_id"] for o in product["offers"] if o["merchant"] == "Demo Shop A"}


def test_price_from_is_recomputed_after_filters(demo):
    no_size = search(demo, "лоферы кожаные", availability="in_stock")
    [item] = by_title(no_size, "Лоферы кожаные")
    assert item["price_minor"] == 1_500_000 and item["merchant"] == "Demo Shop B"

    eu37 = search(demo, "лоферы кожаные", size={"system": "EU", "label": "37"}, availability="in_stock")
    [item] = by_title(eu37, "Лоферы кожаные")
    assert item["price_minor"] == 1_899_000 and item["merchant"] == "Demo Shop A"
    assert {"system": "EU", "label": "37"} in item["available_sizes"]


def test_size_systems_are_not_mixed(demo):
    ru38 = search(demo, "лоферы", size={"system": "RU", "label": "38"})
    assert ru38["items"] == []
    ru = search(demo, "куртка замша", size={"system": "RU", "label": "46"})
    assert {i["merchant"] for i in ru["items"]} == {"Demo Shop B"}


def test_unknown_availability_is_not_in_stock(demo):
    in_stock = search(demo, "кардиган", availability="in_stock")
    assert by_title(in_stock, "Кардиган крупной вязки") == []
    anyw = search(demo, "кардиган")
    [item] = by_title(anyw, "Кардиган крупной вязки")
    assert item["availability"] == "unknown"


def test_stale_source_is_hidden_from_in_stock_and_flagged(demo, catalog):
    catalog.conn.execute(
        """UPDATE offers SET last_seen_at = now() - interval '3 days'
           WHERE source_id = (SELECT id FROM feed_sources WHERE slug='demo-shop-b')"""
    )
    in_stock = search(demo, "", availability="in_stock")
    assert all(i["merchant"] != "Demo Shop B" for i in in_stock["items"])
    anyw = search(demo, "пуховая")
    [item] = by_title(anyw, "Куртка пуховая")
    assert item["freshness"] == "stale"
    product = demo.get(f"/api/v1/products/{item['product_id']}").json()
    assert product["offers"][0]["freshness"] == "stale"


def test_discontinued_product_card(demo, catalog):
    pid = catalog.scalar("SELECT product_id FROM offers WHERE external_id='A-SH-01|BLU'")
    catalog.import_("demo-shop-a", "demo_shop_a_v2.yml")
    data = demo.get(f"/api/v1/products/{pid}").json()
    assert data["status"] == "discontinued"
    assert data["offers"][0]["outbound_url"] is None
    assert by_title(search(demo, "рубашка"), "Рубашка оверсайз в полоску") == []


def test_material_filter_uses_confirmed_values_only(demo):
    data = search(demo, "", materials=["suede"])
    titles = {i["title"] for i in data["items"]}
    assert "Куртка из натуральной замши оверсайз" in titles
    assert "Куртка под замшу укороченная" not in titles   # искусственная замша
    assert "Кардиган крупной вязки" not in titles         # состав неизвестен — не «подтверждённая» замша
    wool = search(demo, "", materials=["wool"])
    assert "Пальто прямого кроя без шерсти" not in {i["title"] for i in wool["items"]}


def test_category_filter_includes_descendants(demo):
    data = search(demo, "", categories=["outerwear"])
    cats = {i["category"] for i in data["items"]}
    assert cats <= {"jacket", "coat", "trench", "bomber", "puffer"} and "jacket" in cats


# --------------------------------------------------------------- facets/zero
def test_facets_are_computed_on_matching_products_without_own_filter(demo):
    data = search(demo, "куртка", colors=["brown"])
    assert {i["color"] for i in data["items"]} == {"brown"}
    colors = {f["value"] for f in data["facets"]["colors"]}
    assert {"brown", "black"} <= colors
    # остальные фасеты считаются с учётом фильтра цвета
    merchant_total = sum(f["count"] for f in data["facets"]["merchants"])
    assert merchant_total == data["total"]["value"]


def test_zero_results_suggest_which_filter_to_drop(demo):
    data = search(demo, "лоферы", size={"system": "EU", "label": "38"}, price_max_minor=10_000,
                  availability="in_stock")
    assert data["total"] == {"value": 0, "relation": "eq"}
    assert {"filter": "price", "count": 2} in data["relaxations"]
    assert all(r["filter"] != "size" or r["count"] > 0 for r in data["relaxations"])


# --------------------------------------------------------------- validation
@pytest.mark.parametrize(
    "body",
    [
        {"q": "x" * 501},
        {"q": "a", "limit": 49},
        {"q": "a", "limit": 0},
        {"q": "a", "filters": {"price_max_minor": -1}},
        {"q": "a", "filters": {"currency": "USD"}},
        {"q": "a", "filters": {"size": {"label": "38"}}},
        {"q": "a", "filters": {"size": {"system": "XX", "label": "38"}}},
        {"q": "a", "filters": {"colors": ["розовый"]}},
        {"q": "a", "filters": {"price_min_minor": 5, "price_max_minor": 1}},
        {"q": "a", "unknown": 1},
    ],
)
def test_invalid_parameters_return_422_with_error_shape(demo, body):
    r = demo.post("/api/v1/search", json=body)
    assert r.status_code == 422
    data = r.json()
    assert data["code"] == "invalid_parameters" and data["request_id"] and data["message"]


# --------------------------------------------------------------- cursor
def test_cursor_pagination_mismatch_and_expiry(demo, catalog):
    first = demo.post("/api/v1/search", json={"q": "", "limit": 10}).json()
    assert first["total"]["value"] == 32 and first["next_cursor"]
    second = demo.post("/api/v1/search", json={"q": "", "limit": 10, "cursor": first["next_cursor"]}).json()
    ids1 = {i["product_id"] for i in first["items"]}
    ids2 = {i["product_id"] for i in second["items"]}
    assert len(ids2) == 10 and not ids1 & ids2

    mismatch = demo.post("/api/v1/search", json={"q": "куртка", "limit": 10, "cursor": first["next_cursor"]})
    assert mismatch.status_code == 422 and mismatch.json()["code"] == "cursor_mismatch"

    body, sig = first["next_cursor"].rsplit(".", 1)
    tampered = demo.post("/api/v1/search", json={"q": "", "limit": 10, "cursor": body + ".deadbeef"})
    assert tampered.json()["code"] == "invalid_cursor"

    catalog.import_("demo-shop-a", "demo_shop_a_v2.yml")  # индекс изменился
    expired = demo.post("/api/v1/search", json={"q": "", "limit": 10, "cursor": first["next_cursor"]})
    assert expired.status_code == 410 and expired.json()["code"] == "cursor_expired"


def test_search_is_logged_and_marked_as_demo(demo, catalog):
    data = search(demo, "платье")
    assert data["demo_data"] is True and data["experiment_arm"] == "A"
    assert data["degraded_mode"] is False
    row = catalog.conn.execute("SELECT * FROM search_sessions WHERE id=%s", (data["search_id"],)).fetchone()
    assert row["query"] == "платье" and row["ranking_version"] == data["ranking_version"]


# --------------------------------------------------------------- outbound
def test_outbound_redirects_only_to_allowed_merchant_urls(demo, catalog):
    item = search(demo, "тренч")["items"][0]
    r = demo.get(item["outbound_url"], follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://demo-shop-a.example/")
    assert r.headers["cache-control"] == "no-store"

    oid = item["matched_offer_id"]
    catalog.conn.execute("UPDATE offers SET product_url='https://evil.example/x' WHERE id=%s", (oid,))
    assert demo.get(f"/api/v1/out/{oid}", follow_redirects=False).status_code == 404
    catalog.conn.execute("UPDATE offers SET product_url='javascript:alert(1)' WHERE id=%s", (oid,))
    assert demo.get(f"/api/v1/out/{oid}", follow_redirects=False).status_code == 404
    catalog.conn.execute(
        "UPDATE offers SET product_url='https://demo-shop-a.example.evil.example/x' WHERE id=%s", (oid,)
    )
    assert demo.get(f"/api/v1/out/{oid}", follow_redirects=False).status_code == 404

    # affiliate URL используется только при явно разрешённом tracking-домене
    catalog.conn.execute(
        "UPDATE offers SET product_url='https://demo-shop-a.example/p', affiliate_url='https://track.example/c?x=1' WHERE id=%s",
        (oid,),
    )
    assert demo.get(f"/api/v1/out/{oid}", follow_redirects=False).headers["location"] == "https://demo-shop-a.example/p"
    catalog.conn.execute("UPDATE merchants SET affiliate_domains='{track.example}' WHERE slug='demo-shop-a'")
    assert demo.get(f"/api/v1/out/{oid}", follow_redirects=False).headers["location"] == "https://track.example/c?x=1"

    catalog.conn.execute("UPDATE offers SET active=false WHERE id=%s", (oid,))
    assert demo.get(f"/api/v1/out/{oid}", follow_redirects=False).status_code == 410
    assert demo.get(f"/api/v1/out/{uuid.uuid4()}", follow_redirects=False).status_code == 404
    assert demo.get("/api/v1/out/not-a-uuid", follow_redirects=False).status_code == 404


# --------------------------------------------------------------- internal
def test_internal_endpoints_require_token(demo, catalog):
    assert demo.post("/internal/imports", json={"source": "demo-shop-a"}).status_code == 401
    bad = demo.post("/internal/imports", json={"source": "demo-shop-a"}, headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401 and bad.json()["code"] == "unauthorized"

    auth = {"Authorization": "Bearer test-internal-token"}
    r = demo.post("/internal/imports", json={"source": "demo-shop-a"}, headers=auth)
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    _, payload = demo.fake_redis.items[-1]
    assert json.loads(payload)["run_id"] == run_id

    run = demo.get(f"/internal/imports/{run_id}", headers=auth).json()
    assert run["status"] == "queued"
    assert "snapshot_uri" not in run and "secret_ref" not in run
    assert demo.post("/internal/imports", json={"source": "nope"}, headers=auth).status_code == 404

    published = catalog.scalar("SELECT id FROM import_runs WHERE status='published' LIMIT 1")
    r = demo.post(f"/internal/imports/{published}/publish", json={"approve": True}, headers=auth)
    assert r.status_code == 409


def test_public_responses_do_not_leak_feed_secrets(demo):
    item = search(demo, "куртка")["items"][0]
    text = demo.get(f"/api/v1/products/{item['product_id']}").text
    assert "FEED_URL" not in text and "fixture://" not in text and "secret" not in text


def test_health_and_dictionaries(demo):
    assert demo.get("/health/live").json() == {"status": "ok"}
    ready = demo.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["checks"] == {"postgres": "ok", "redis": "ok"}
    d = demo.get("/api/v1/dictionaries").json()
    assert "jacket" in {c["code"] for c in d["categories"]} and "UNKNOWN" in d["size_systems"]
    brand = demo.get("/api/v1/brands/nord-demo").json()
    assert brand["product_count"] >= 3 and brand["indexable"] is False


def test_rate_limit_returns_429(demo):
    demo.app.state.limiter.per_minute = 2
    codes = [demo.post("/api/v1/search", json={"q": "a"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
