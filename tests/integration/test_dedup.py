"""Дедупликация: объединяем только по объяснимым идентификаторам, при сомнении — две карточки."""
from __future__ import annotations


def products_for(catalog, *external_ids):
    rows = catalog.conn.execute(
        "SELECT external_id, product_id FROM offers WHERE external_id = ANY(%s)", (list(external_ids),)
    ).fetchall()
    return {r["external_id"]: r["product_id"] for r in rows}


def test_same_model_and_colorway_in_two_shops_is_one_product(catalog):
    catalog.demo()
    p = products_for(catalog, "A-LOF-01|BLK", "B-77001|BLK")
    assert p["A-LOF-01|BLK"] == p["B-77001|BLK"]
    link = catalog.conn.execute(
        """SELECT d.method, d.evidence_json FROM dedup_links d JOIN offers o ON o.id = d.offer_id
           WHERE o.external_id='B-77001|BLK'"""
    ).fetchone()
    assert link["method"] == "brand_model_colorway"
    assert link["evidence_json"] == {"model_code": "ND-L100", "colorway_code": "BLK"}


def test_same_model_different_colors_are_not_merged(catalog):
    catalog.demo()
    p = products_for(catalog, "B-77001|BLK", "B-77002|BRN", "A-TS-01|BLK", "A-TS-01|WHT")
    assert p["B-77001|BLK"] != p["B-77002|BRN"]
    assert p["A-TS-01|BLK"] != p["A-TS-01|WHT"]


def test_same_title_different_models_are_not_merged(catalog):
    catalog.demo()
    p = products_for(catalog, "A-JKT-01|BRN", "B-77003|BRN")
    assert p["A-JKT-01|BRN"] != p["B-77003|BRN"]


def test_conflicting_category_blocks_merge(catalog, feeds):
    catalog.import_("demo-shop-a")
    text = (feeds / "demo_shop_b_v1.yml").read_text(encoding="utf-8")
    # тот же бренд/код/колорвей, что у лоферов A, но категория «Кроссовки» — не объединять
    text = text.replace(
        '<offer id="B-77001-BLK-38"', '<offer id="B-77001-BLK-38"'
    ).replace("<categoryId>10</categoryId>\n        <picture>https://demo-shop-b.example/img/b-77001-blk",
              "<categoryId>11</categoryId>\n        <picture>https://demo-shop-b.example/img/b-77001-blk")
    (feeds / "b_conflict.yml").write_text(text, encoding="utf-8")
    run = catalog.import_("demo-shop-b", "b_conflict.yml")
    assert run["status"] == "published"
    assert run["counts"]["publish"]["dedup_conflicts"] >= 1
    p = products_for(catalog, "A-LOF-01|BLK", "B-77001|BLK")
    assert p["A-LOF-01|BLK"] != p["B-77001|BLK"]


def test_second_merchant_does_not_overwrite_origin_product_fields(catalog):
    catalog.demo()
    pid = products_for(catalog, "A-LOF-01|BLK")["A-LOF-01|BLK"]
    assert catalog.scalar("SELECT title FROM products WHERE id=%s", pid) == "Лоферы кожаные"
    sources = catalog.scalar(
        "SELECT count(DISTINCT source_id) FROM product_attributes WHERE product_id=%s AND key='materials'", pid
    )
    assert sources == 2  # значения обоих продавцов сохранены, конфликт не затирается
