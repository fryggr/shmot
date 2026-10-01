"""Импорт: staging → проверка → публикация, идемпотентность и защита каталога."""
from __future__ import annotations

import psycopg
import pytest

from fashion_worker.jobs import publish as publish_mod
from fashion_worker.jobs.publish import PublishRefused, publish_run


def test_full_import_publishes_catalog_with_audit_and_provenance(catalog):
    run = catalog.import_("demo-shop-a")
    assert run["status"] == "published"
    c, audit = run["counts"], run["audit"]
    assert (c["records"], c["valid"], c["quarantined"]) == (94, 90, 4)
    assert audit["quarantine_reasons"] == {
        "invalid_price": 1, "url_not_allowed": 1, "missing_image": 1, "duplicate_external_id": 1,
    }
    assert audit["duplicate_ids"] == 1
    assert audit["offers"] == 20 and audit["models"] == 19
    assert audit["size_systems"] == {"INT": 83, "EU": 6, "none": 1}
    assert audit["availability"]["unknown"] > 0
    assert set(audit["null_ratio"]) >= {"description", "gtin", "materials", "size", "category_unmapped"}

    assert catalog.scalar("SELECT count(*) FROM offers WHERE active") == 20
    assert catalog.scalar("SELECT count(*) FROM offer_variants") == 90
    # Карточка — модель в цвете: одна group_id с двумя цветами даёт две карточки
    assert catalog.scalar("SELECT count(*) FROM offers WHERE external_id LIKE 'A-TS-01|%%'") == 2
    # raw provenance: последняя версия каждой записи (и карантинной тоже) со ссылкой на run и снимок
    assert catalog.scalar("SELECT count(*) FROM raw_records") == 93
    assert catalog.scalar("SELECT count(DISTINCT run_id) FROM raw_records") == 1
    bad = catalog.conn.execute(
        "SELECT payload_json FROM raw_records WHERE external_id='A-BROKEN-PRICE'"
    ).fetchone()["payload_json"]
    assert bad["fields"]["price"] == "-100"
    assert run["snapshot_uri"].startswith("file://") and run["checksum"]
    # staging очищается после публикации
    assert catalog.scalar("SELECT count(*) FROM staging_records") == 0
    # индексы и outbox сходятся
    assert catalog.scalar("SELECT count(*) FROM search_documents") == 20
    assert catalog.scalar("SELECT count(*) FROM index_outbox WHERE status <> 'done'") == 0


def test_repeat_import_is_idempotent(catalog):
    catalog.demo()
    before = catalog.snapshot()

    same = catalog.import_("demo-shop-a")  # тот же снимок: только проверка свежести
    assert same["status"] == "unchanged"
    assert same["counts"]["offers_freshness_touched"] == 20

    forced = catalog.import_("demo-shop-a", force=True)  # полный повтор обработки
    assert forced["status"] == "published"
    stats = forced["counts"]["publish"]
    assert stats["offers_unchanged"] == 20
    assert stats.get("offers_created", 0) == 0 and stats.get("products_created", 0) == 0
    assert stats.get("outbox_events", 0) == 0
    assert catalog.snapshot() == before


def test_full_snapshot_deactivates_missing_and_updates_changed(catalog):
    catalog.demo()
    shirt_product = catalog.scalar("SELECT product_id FROM offers WHERE external_id='A-SH-01|BLU'")
    sneaker_variant_id = catalog.scalar("SELECT id FROM offer_variants WHERE external_variant_id='A-SNK-01-WHT-38'")

    run = catalog.import_("demo-shop-a", "demo_shop_a_v2.yml")
    assert run["status"] == "published"
    stats = run["counts"]["publish"]
    assert stats["offers_deactivated"] == 1 and stats["offers_updated"] == 2

    assert catalog.scalar("SELECT active FROM offers WHERE external_id='A-SH-01|BLU'") is False
    assert catalog.scalar("SELECT price_minor FROM offers WHERE external_id='A-TR-01|BEI'") == 1799000
    # вариант обновлён на месте (id стабилен), наличие изменилось
    row = catalog.conn.execute(
        "SELECT id, availability FROM offer_variants WHERE external_variant_id='A-SNK-01-WHT-38'"
    ).fetchone()
    assert row["id"] == sneaker_variant_id and row["availability"] == "out_of_stock"
    # снятый товар уходит из индекса, данные о нём сохраняются
    assert catalog.scalar("SELECT count(*) FROM search_documents WHERE product_id=%s", shirt_product) == 0
    assert catalog.scalar("SELECT count(*) FROM products WHERE id=%s", shirt_product) == 1


def test_variants_removed_from_full_snapshot_are_deleted_atomically(catalog, feeds):
    catalog.demo()
    text = (feeds / "demo_shop_a_v1.yml").read_text(encoding="utf-8")
    start = text.index('<offer id="A-JKT-04-BLK-L"')
    end = text.index("</offer>", start) + len("</offer>")
    (feeds / "a_no_l.yml").write_text(text[:start] + text[end:], encoding="utf-8")

    run = catalog.import_("demo-shop-a", "a_no_l.yml")
    assert run["status"] == "published"
    labels = [r["size_label"] for r in catalog.conn.execute(
        """SELECT v.size_label FROM offer_variants v JOIN offers o ON o.id=v.offer_id
           WHERE o.external_id='A-JKT-04|BLK' ORDER BY 1"""
    ).fetchall()]
    assert labels == ["M", "S"]


def test_unchanged_snapshot_keeps_catalog_fresh_without_reprocessing(catalog):
    catalog.demo()
    catalog.conn.execute("UPDATE offers SET last_seen_at = now() - interval '10 days'")
    run = catalog.import_("demo-shop-a")
    assert run["status"] == "unchanged"
    assert catalog.scalar(
        """SELECT count(*) FROM offers o JOIN feed_sources s ON s.id=o.source_id
           WHERE s.slug='demo-shop-a' AND o.active AND o.last_seen_at > now() - interval '1 minute'"""
    ) == 20


@pytest.mark.parametrize(
    "fixture, code",
    [
        ("broken_truncated.yml", "xml_malformed"),
        ("broken_xxe.yml", "xml_forbidden_construct"),
        ("broken_billion_laughs.yml", "xml_forbidden_construct"),
        ("does_not_exist.yml", "fixture_not_found"),
        ("../../../etc/passwd", "fixture_not_found"),
    ],
)
def test_broken_or_unsafe_snapshot_is_rejected_and_catalog_kept(catalog, fixture, code):
    catalog.demo()
    before = catalog.snapshot()
    run = catalog.import_("demo-shop-a", fixture)
    assert run["status"] == "failed"
    assert run["errors"][-1]["code"] == code
    assert catalog.snapshot() == before
    assert catalog.scalar("SELECT count(*) FROM staging_records") == 0


def test_xxe_entity_is_never_resolved(catalog):
    run = catalog.import_("demo-shop-a", "broken_xxe.yml")
    assert run["status"] == "failed"
    assert catalog.scalar("SELECT count(*) FROM raw_records") == 0
    assert "root:" not in str(run["errors"])


def test_missing_secret_fails_without_touching_catalog(catalog, monkeypatch):
    catalog.demo()
    before = catalog.snapshot()
    monkeypatch.delenv("FEED_URL_DEMO_SHOP_A")
    run = catalog.import_("demo-shop-a")
    assert run["status"] == "failed" and run["errors"][-1]["code"] == "secret_missing"
    assert catalog.snapshot() == before


def test_empty_feed_is_held_and_catalog_kept(catalog):
    catalog.demo()
    before = catalog.snapshot()
    run = catalog.import_("demo-shop-a", "empty_offers.yml")
    assert run["status"] == "held" and run["held_reason"] == "empty_snapshot"
    assert catalog.snapshot() == before


def test_volume_drop_is_held_until_manual_approval(catalog, feeds):
    catalog.demo()
    before = catalog.snapshot()
    text = (feeds / "demo_shop_a_v1.yml").read_text(encoding="utf-8")
    head, rest = text.split("<offers>", 1)
    offers = rest.split("</offer>")
    kept = "</offer>".join(offers[:40]) + "</offer>\n    </offers>\n  </shop>\n</yml_catalog>\n"
    (feeds / "a_half.yml").write_text(head + "<offers>" + kept, encoding="utf-8")

    run = catalog.import_("demo-shop-a", "a_half.yml")
    assert run["status"] == "held"
    assert run["held_reason"].startswith("volume_drop")
    assert catalog.snapshot() == before
    with pytest.raises(PublishRefused):
        publish_run(catalog.conn, run["id"])  # без подтверждения нельзя

    stats = publish_run(catalog.conn, run["id"], force=True)
    assert stats["offers_deactivated"] > 0
    status = catalog.scalar("SELECT status FROM import_runs WHERE id=%s", run["id"])
    assert status == "published"


def test_held_run_cannot_override_newer_published_snapshot(catalog):
    catalog.demo()
    held = catalog.import_("demo-shop-a", "empty_offers.yml")
    assert held["status"] == "held"
    newer = catalog.import_("demo-shop-a", "demo_shop_a_v2.yml")
    assert newer["status"] == "published"
    with pytest.raises(PublishRefused) as exc:
        publish_run(catalog.conn, held["id"], force=True)
    assert exc.value.code == "newer_run_published"


def test_error_ratio_above_threshold_is_held(catalog, feeds):
    text = (feeds / "demo_shop_b_v1.yml").read_text(encoding="utf-8")
    broken = text.replace("<currencyId>RUR</currencyId>", "<currencyId>EUR</currencyId>", 5)
    (feeds / "b_errors.yml").write_text(broken, encoding="utf-8")
    run = catalog.import_("demo-shop-b", "b_errors.yml")
    assert run["status"] == "held"
    assert run["held_reason"].startswith("error_ratio")
    assert catalog.scalar("SELECT count(*) FROM offers") == 0


def test_crash_during_publish_rolls_back_everything(catalog, monkeypatch):
    catalog.demo()
    before = catalog.snapshot()

    def boom(self):
        raise RuntimeError("simulated crash after offers were written")

    monkeypatch.setattr(publish_mod.Publisher, "enqueue_index", boom)
    run = catalog.import_("demo-shop-a", "demo_shop_a_v2.yml")
    assert run["status"] == "failed"
    assert run["errors"][-1]["code"] == "internal_error"
    assert catalog.snapshot() == before
    assert catalog.scalar("SELECT active FROM offers WHERE external_id='A-SH-01|BLU'") is True


def test_concurrent_import_of_same_source_is_refused(catalog, database):
    from fashion_worker.jobs.publish import source_lock_key

    source_id = catalog.scalar("SELECT id FROM feed_sources WHERE slug='demo-shop-a'")
    with psycopg.connect(database, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (source_lock_key(source_id),))
        run = catalog.import_("demo-shop-a")
        assert run["status"] == "failed" and run["errors"][-1]["code"] == "source_locked"
    assert catalog.import_("demo-shop-a")["status"] == "published"


def test_invalid_old_price_is_not_published_as_discount(catalog):
    catalog.demo()
    row = catalog.conn.execute(
        "SELECT price_minor, old_price_minor FROM offers WHERE external_id='B-77012|BEI'"
    ).fetchone()
    assert row == {"price_minor": 3999000, "old_price_minor": None}
    raw = catalog.conn.execute(
        "SELECT payload_json FROM raw_records WHERE external_id='B-77012-BEI-42'"
    ).fetchone()["payload_json"]
    assert raw["fields"]["oldprice"] == "39990.00"
    assert catalog.scalar("SELECT old_price_minor FROM offers WHERE external_id='A-JKT-01|BRN'") == 3499000


def test_sizes_keep_their_system_and_are_never_guessed(catalog):
    catalog.demo()
    rows = {
        r["external_variant_id"]: (r["size_system"], r["size_label"], r["size_kind"])
        for r in catalog.conn.execute("SELECT * FROM offer_variants").fetchall()
    }
    assert rows["A-JKT-01-BRN-M"] == ("INT", "M", "apparel")
    assert rows["A-LOF-01-BLK-38"] == ("EU", "38", "shoes")
    assert rows["B-77003-BRN-46"] == ("RU", "46", "apparel")
    assert rows["B-77011-PNK-44"] == ("UNKNOWN", "44", "apparel")  # unit не передан
    assert rows["A-BAG-01-BRD-OS"] == (None, None, None)  # у сумки размера нет — не выдумываем


def test_delta_feed_only_deletes_on_explicit_event(catalog, feeds):
    catalog.demo()
    catalog.conn.execute("UPDATE feed_sources SET is_full_snapshot=false WHERE slug='demo-shop-b'")
    delta = """<?xml version="1.0" encoding="UTF-8"?>
<yml_catalog date="2026-09-30 12:00"><shop><name>Demo Shop B</name>
<categories><category id="10">Лоферы</category></categories>
<offers>
  <offer id="B-77001-BLK-40" group_id="B-77001" deleted="true"><param name="Код цвета">BLK</param></offer>
  <offer id="B-77001-BLK-41" group_id="B-77001" available="true">
    <url>https://demo-shop-b.example/p/b-77001?sku=B-77001-BLK-41</url><price>14000.00</price>
    <currencyId>RUR</currencyId><categoryId>10</categoryId>
    <picture>https://demo-shop-b.example/img/b-77001-blk-1.jpg</picture>
    <name>Лоферы из кожи Nord Demo</name><vendor>Nord Demo</vendor><vendorCode>ND-L100</vendorCode>
    <param name="Размер" unit="EU">41</param><param name="Цвет">чёрный</param><param name="Код цвета">BLK</param>
  </offer>
</offers></shop></yml_catalog>
"""
    (feeds / "b_delta.yml").write_text(delta, encoding="utf-8")
    run = catalog.import_("demo-shop-b", "b_delta.yml")
    assert run["status"] == "published", run["errors"]
    # остальные предложения источника не тронуты (delta не деактивирует отсутствующие)
    assert catalog.scalar(
        "SELECT count(*) FROM offers o JOIN feed_sources s ON s.id=o.source_id WHERE s.slug='demo-shop-b' AND o.active"
    ) == 13
    labels = [r["size_label"] for r in catalog.conn.execute(
        """SELECT v.size_label FROM offer_variants v JOIN offers o ON o.id=v.offer_id
           WHERE o.external_id='B-77001|BLK' ORDER BY v.size_label"""
    ).fetchall()]
    assert labels == ["38", "39", "41"]
    assert catalog.scalar("SELECT price_minor FROM offers WHERE external_id='B-77001|BLK'") == 1400000


def test_category_mapping_and_materials_respect_imitations(catalog):
    catalog.demo()
    attrs = {
        r["external_id"]: r["value_json"]["codes"]
        for r in catalog.conn.execute(
            """SELECT o.external_id, a.value_json FROM product_attributes a
               JOIN offers o ON o.product_id = a.product_id AND o.source_id = a.source_id
               WHERE a.key = 'materials'"""
        ).fetchall()
    }
    assert attrs["A-JKT-01|BRN"] == ["suede"]
    assert attrs["A-JKT-02|BRN"] == ["faux_suede"]       # «искусственная замша» ≠ замша
    assert attrs["A-JKT-03|BLK"] == ["faux_leather"]     # «экокожа» ≠ кожа
    cats = {
        r["k"]: r["v"]
        for r in catalog.conn.execute(
            """SELECT o.external_id AS k, c.code AS v FROM offers o JOIN products p ON p.id=o.product_id
               JOIN categories c ON c.id=p.category_id"""
        ).fetchall()
    }
    assert cats["A-JKT-01|BRN"] == "jacket"
    assert cats["A-LOF-01|BLK"] == "loafers"
    assert cats["B-77013|BLK"] == "top"
