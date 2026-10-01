"""Сквозной сценарий: fixture → import → индекс → запрос с размером → товар → разрешённый outbound."""
from __future__ import annotations

from fashion_worker.indexing.outbox import reconcile


def test_fixture_to_outbound(catalog, client):
    a, b = catalog.demo()
    assert reconcile(catalog.conn)["in_sync"] is True

    r = client.post("/api/v1/search", json={
        "q": "куртка замша",
        "filters": {"size": {"system": "INT", "label": "M"}, "availability": "in_stock",
                    "price_max_minor": 3_000_000},
        "limit": 24,
    })
    assert r.status_code == 200
    data = r.json()
    titles = [i["title"] for i in data["items"]]
    assert "Куртка из натуральной замши оверсайз" in titles
    item = next(i for i in data["items"] if i["title"] == "Куртка из натуральной замши оверсайз")
    assert item["merchant"] == "Demo Shop A"           # у B размеры RU, INT:M нет
    assert item["price_minor"] == 2_799_000 and item["old_price_minor"] == 3_499_000
    assert {"system": "INT", "label": "M"} in item["available_sizes"]

    product = client.get(f"/api/v1/products/{item['product_id']}").json()
    assert product["status"] == "active" and product["is_demo"] is True
    offer = next(o for o in product["offers"] if o["offer_id"] == item["matched_offer_id"])
    assert any(s["system"] == "INT" and s["label"] == "M" and s["availability"] == "in_stock" for s in offer["sizes"])

    out = client.get(offer["outbound_url"], follow_redirects=False)
    assert out.status_code == 302
    assert out.headers["location"].startswith("https://demo-shop-a.example/p/a-jkt-01")
