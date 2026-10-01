"""Транзакционная публикация проверенного снимка в каталог.

Всё — в одной транзакции: upsert offers/variants/images/attributes, деактивация пропавших
offers (только для принятого полного снимка), raw provenance, версии товаров и outbox.
Ошибка на любом шаге откатывает транзакцию — опубликованный каталог не меняется.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from itertools import groupby

import psycopg
from psycopg.types.json import Jsonb

from fashion_domain.normalization import slugify

log = logging.getLogger(__name__)


class PublishRefused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def source_lock_key(source_id) -> str:
    return f"fashion-import:{source_id}"


def _hash(doc) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def aggregate_offer(variants: list[dict]) -> dict:
    """Цена «от» на уровне offer — минимальная среди вариантов в наличии (иначе среди всех).
    На выдачу эта цена не влияет: поиск всегда считает цену по подходящему варианту."""
    pool = [v for v in variants if v["availability"] == "in_stock"] or variants
    cheapest = min(pool, key=lambda v: (v["price_minor"], v["external_variant_id"]))
    states = {v["availability"] for v in variants}
    availability = "in_stock" if "in_stock" in states else ("unknown" if "unknown" in states else "out_of_stock")
    return {
        "price_minor": cheapest["price_minor"],
        "old_price_minor": cheapest.get("old_price_minor"),
        "availability": availability,
    }


def _variant_from(n: dict) -> dict:
    size = n.get("size") or {}
    return {
        "external_variant_id": n["external_variant_id"],
        "size_raw": size.get("size_raw"),
        "size_system": size.get("size_system"),
        "size_label": size.get("size_label"),
        "size_kind": size.get("size_kind"),
        "price_minor": n["price_minor"],
        "old_price_minor": n.get("old_price_minor"),
        "availability": n["availability"],
        "gtin": n.get("gtin"),
    }


class Publisher:
    def __init__(self, conn: psycopg.Connection, run: dict, source: dict):
        self.conn = conn
        self.run = run
        self.source = source
        self.is_full = run["is_full_snapshot"]
        self.changed: set = set()
        self.stats: Counter = Counter()
        self._brands: dict[str, object] = {}
        self._categories = {
            r["code"]: r["id"] for r in conn.execute("SELECT code, id FROM categories").fetchall()
        }
        self.now = conn.execute("SELECT now() AS now").fetchone()["now"]

    # ------------------------------------------------------------------ helpers
    def brand_id(self, name: str | None):
        if not name:
            return None
        alias = " ".join(name.lower().split())
        if alias in self._brands:
            return self._brands[alias]
        row = self.conn.execute("SELECT brand_id FROM brand_aliases WHERE alias=%s", (alias,)).fetchone()
        if row:
            bid = row["brand_id"]
        else:
            base = slugify(name)
            slug, n = base, 1
            while self.conn.execute("SELECT 1 FROM brands WHERE slug=%s", (slug,)).fetchone():
                n += 1
                slug = f"{base}-{n}"
            bid = self.conn.execute(
                "INSERT INTO brands (name, slug) VALUES (%s, %s) RETURNING id", (name.strip(), slug)
            ).fetchone()["id"]
            self.conn.execute("INSERT INTO brand_aliases (alias, brand_id) VALUES (%s, %s)", (alias, bid))
        self._brands[alias] = bid
        return bid

    def match_product(self, brand_id, category_id, doc: dict, gtins: list[str]):
        """Объяснимое сопоставление с существующей карточкой. Похожие названия и embeddings
        здесь не используются: при сомнении создаётся отдельная карточка."""

        def compatible(row) -> bool:
            if row["category_id"] and category_id and row["category_id"] != category_id:
                return False
            if row["color"] and doc["color"] and row["color"] != doc["color"]:
                return False
            return True

        if gtins:
            rows = self.conn.execute(
                """SELECT DISTINCT p.id, p.category_id, p.color FROM offer_variants v
                   JOIN offers o ON o.id = v.offer_id JOIN products p ON p.id = o.product_id
                   WHERE v.gtin = ANY(%s)""",
                (gtins,),
            ).fetchall()
            ok = [r for r in rows if compatible(r)]
            if len(rows) == 1 and ok:
                return ok[0]["id"], "gtin", {"gtins": gtins}
        if brand_id and doc["model_code"] and doc["colorway_code"]:
            rows = self.conn.execute(
                """SELECT id, category_id, color FROM products
                   WHERE brand_id=%s AND model_code=%s AND colorway_code=%s""",
                (brand_id, doc["model_code"], doc["colorway_code"]),
            ).fetchall()
            ok = [r for r in rows if compatible(r)]
            if len(ok) == 1:
                return ok[0]["id"], "brand_model_colorway", {
                    "model_code": doc["model_code"], "colorway_code": doc["colorway_code"],
                }
            if rows and not ok:
                self.stats["dedup_conflicts"] += 1
        return None

    def write_attributes(self, product_id, doc: dict) -> None:
        values = {
            "color": {"code": doc["color"], "raw": doc["color_raw"]} if doc["color_raw"] else None,
            "materials": {"codes": doc["materials"], "raw": doc["material_raw"]} if doc["material_raw"] or doc["materials"] else None,
            "composition": {"items": doc["composition"], "raw": doc["composition_raw"]} if doc["composition_raw"] else None,
            "fit": {"raw": doc["fit_raw"]} if doc["fit_raw"] else None,
            "gender": {"code": doc["gender"]},
            "category": {"code": doc["category_code"], "source_path": doc["source_category_path"]},
        }
        for key, value in values.items():
            if value is None:
                self.conn.execute(
                    "DELETE FROM product_attributes WHERE product_id=%s AND key=%s AND provenance='merchant' AND source_id=%s",
                    (product_id, key, self.source["id"]),
                )
                continue
            self.conn.execute(
                """INSERT INTO product_attributes (product_id, key, value_json, provenance, confidence, source_id)
                   VALUES (%s, %s, %s, 'merchant', 1.0, %s)
                   ON CONFLICT (product_id, key, provenance,
                                coalesce(source_id, '00000000-0000-0000-0000-000000000000'::uuid))
                   DO UPDATE SET value_json = EXCLUDED.value_json,
                                 version = product_attributes.version + 1
                   WHERE product_attributes.value_json IS DISTINCT FROM EXCLUDED.value_json""",
                (product_id, key, Jsonb(value), self.source["id"]),
            )

    def write_variants(self, offer_id, variants: list[dict], replace: bool) -> list[dict]:
        for v in variants:
            self.conn.execute(
                """INSERT INTO offer_variants (offer_id, external_variant_id, size_raw, size_system, size_label,
                                              size_kind, price_minor, old_price_minor, availability, gtin)
                   VALUES (%(offer_id)s, %(external_variant_id)s, %(size_raw)s, %(size_system)s, %(size_label)s,
                           %(size_kind)s, %(price_minor)s, %(old_price_minor)s, %(availability)s, %(gtin)s)
                   ON CONFLICT (offer_id, external_variant_id) DO UPDATE SET
                       size_raw=EXCLUDED.size_raw, size_system=EXCLUDED.size_system, size_label=EXCLUDED.size_label,
                       size_kind=EXCLUDED.size_kind, price_minor=EXCLUDED.price_minor,
                       old_price_minor=EXCLUDED.old_price_minor, availability=EXCLUDED.availability,
                       gtin=EXCLUDED.gtin""",
                v | {"offer_id": offer_id},
            )
        if replace:
            self.conn.execute(
                "DELETE FROM offer_variants WHERE offer_id=%s AND NOT (external_variant_id = ANY(%s))",
                (offer_id, [v["external_variant_id"] for v in variants]),
            )
        return self.conn.execute(
            "SELECT external_variant_id, price_minor, old_price_minor, availability FROM offer_variants WHERE offer_id=%s",
            (offer_id,),
        ).fetchall()

    def write_images(self, product_id, offer_id, pictures: list[str]) -> None:
        self.conn.execute("DELETE FROM product_images WHERE offer_id=%s", (offer_id,))
        for pos, url in enumerate(pictures):
            self.conn.execute(
                "INSERT INTO product_images (product_id, offer_id, url, position, source_id) VALUES (%s,%s,%s,%s,%s)",
                (product_id, offer_id, url, pos, self.source["id"]),
            )

    # ---------------------------------------------------------------- publish
    def publish_group(self, group_key: str, records: list[dict]) -> None:
        normalized = [r["normalized"] for r in sorted(records, key=lambda r: r["seq"])]
        first = normalized[0]
        variants = sorted((_variant_from(n) for n in normalized), key=lambda v: v["external_variant_id"])
        pictures: list[str] = []
        for n in normalized:
            pictures += [p for p in n["pictures"] if p not in pictures]
        doc = {
            k: first.get(k)
            for k in ("title", "description", "brand", "model_code", "colorway_code", "color", "color_raw",
                      "materials", "material_raw", "composition", "composition_raw", "gender", "fit_raw",
                      "category_code", "source_category_path", "url", "currency")
        }
        doc["pictures"] = pictures
        content_hash = _hash({"doc": doc, "variants": variants})

        existing = self.conn.execute(
            "SELECT id, product_id, content_hash, active FROM offers WHERE source_id=%s AND external_id=%s FOR UPDATE",
            (self.source["id"], group_key),
        ).fetchone()
        base = {"now": self.now, "run_id": self.run["id"]}

        if existing and existing["content_hash"] == content_hash and self.is_full:
            self.conn.execute(
                "UPDATE offers SET last_seen_at=%(now)s, imported_at=%(now)s, active=true, last_run_id=%(run_id)s WHERE id=%(id)s",
                base | {"id": existing["id"]},
            )
            if not existing["active"]:
                self.changed.add(existing["product_id"])
                self.stats["offers_reactivated"] += 1
            else:
                self.stats["offers_unchanged"] += 1
            return

        brand_id = self.brand_id(doc["brand"])
        category_id = self._categories.get(doc["category_code"]) if doc["category_code"] else None

        if existing:
            offer_id, product_id = existing["id"], existing["product_id"]
            stored = self.write_variants(offer_id, variants, replace=self.is_full)
            agg = aggregate_offer(stored)
            self.conn.execute(
                """UPDATE offers SET title=%(title)s, product_url=%(url)s, currency=%(currency)s,
                       price_minor=%(price_minor)s, old_price_minor=%(old_price_minor)s, availability=%(availability)s,
                       content_hash=%(hash)s, last_seen_at=%(now)s, imported_at=%(now)s, active=true,
                       last_run_id=%(run_id)s
                   WHERE id=%(id)s""",
                base | agg | {"title": doc["title"], "url": doc["url"], "currency": doc["currency"],
                              "hash": content_hash, "id": offer_id},
            )
            # Основные поля карточки меняет только источник, который её создал
            self.conn.execute(
                """UPDATE products SET title=%(title)s, description=%(description)s, gender=%(gender)s,
                       color=%(color)s, category_id=%(category_id)s, brand_id=%(brand_id)s
                   WHERE id=%(id)s AND origin_source_id=%(source_id)s AND origin_external_id=%(key)s""",
                {"title": doc["title"], "description": doc["description"], "gender": doc["gender"],
                 "color": doc["color"], "category_id": category_id, "brand_id": brand_id, "id": product_id,
                 "source_id": self.source["id"], "key": group_key},
            )
            self.stats["offers_updated"] += 1
        else:
            gtins = sorted({v["gtin"] for v in variants if v["gtin"]})
            match = self.match_product(brand_id, category_id, doc, gtins)
            if match:
                product_id, method, evidence = match
                self.stats["offers_matched_existing_product"] += 1
            else:
                product_id = self.conn.execute(
                    """INSERT INTO products (brand_id, category_id, title, description, gender, color, model_code,
                                             colorway_code, is_demo, origin_source_id, origin_external_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (brand_id, category_id, doc["title"], doc["description"], doc["gender"], doc["color"],
                     doc["model_code"], doc["colorway_code"], self.source["is_demo"], self.source["id"], group_key),
                ).fetchone()["id"]
                method, evidence = "source_group", {"group_key": group_key}
                self.stats["products_created"] += 1
            agg = aggregate_offer(variants)
            offer_id = self.conn.execute(
                """INSERT INTO offers (product_id, merchant_id, source_id, external_id, title, product_url, currency,
                                       price_minor, old_price_minor, availability, content_hash, last_seen_at,
                                       imported_at, active, last_run_id)
                   VALUES (%(product_id)s, %(merchant_id)s, %(source_id)s, %(key)s, %(title)s, %(url)s, %(currency)s,
                           %(price_minor)s, %(old_price_minor)s, %(availability)s, %(hash)s, %(now)s, %(now)s,
                           true, %(run_id)s)
                   RETURNING id""",
                base | agg | {"product_id": product_id, "merchant_id": self.source["merchant_id"],
                              "source_id": self.source["id"], "key": group_key, "title": doc["title"],
                              "url": doc["url"], "currency": doc["currency"], "hash": content_hash},
            ).fetchone()["id"]
            self.write_variants(offer_id, variants, replace=True)
            self.conn.execute(
                """INSERT INTO dedup_links (offer_id, product_id, method, evidence_json, confidence)
                   VALUES (%s, %s, %s, %s, 1.0)""",
                (offer_id, product_id, method, Jsonb(evidence)),
            )
            self.stats["offers_created"] += 1

        self.write_images(product_id, offer_id, pictures)
        self.write_attributes(product_id, doc)
        self.changed.add(product_id)

    def apply_deletions(self) -> None:
        ids = [r["external_id"] for r in self.conn.execute(
            "SELECT external_id FROM staging_records WHERE run_id=%s AND status='deleted'", (self.run["id"],)
        ).fetchall()]
        if not ids:
            return
        touched = self.conn.execute(
            """DELETE FROM offer_variants v USING offers o
               WHERE v.offer_id = o.id AND o.source_id = %s AND v.external_variant_id = ANY(%s)
               RETURNING o.id AS offer_id, o.product_id""",
            (self.source["id"], ids),
        ).fetchall()
        self.stats["variants_deleted"] += len(touched)
        for offer_id in {r["offer_id"] for r in touched}:
            rows = self.conn.execute(
                "SELECT external_variant_id, price_minor, old_price_minor, availability FROM offer_variants WHERE offer_id=%s",
                (offer_id,),
            ).fetchall()
            if rows:
                self.conn.execute(
                    """UPDATE offers SET price_minor=%(price_minor)s, old_price_minor=%(old_price_minor)s,
                           availability=%(availability)s, content_hash='' WHERE id=%(id)s""",
                    aggregate_offer(rows) | {"id": offer_id},
                )
            else:
                self.conn.execute("UPDATE offers SET active=false WHERE id=%s", (offer_id,))
                self.stats["offers_deactivated"] += 1
        self.changed.update(r["product_id"] for r in touched)

    def deactivate_missing(self) -> None:
        rows = self.conn.execute(
            """UPDATE offers SET active=false
               WHERE source_id=%s AND active AND last_run_id IS DISTINCT FROM %s
               RETURNING product_id""",
            (self.source["id"], self.run["id"]),
        ).fetchall()
        self.stats["offers_deactivated"] += len(rows)
        self.changed.update(r["product_id"] for r in rows)

    def write_raw(self) -> None:
        cur = self.conn.execute(
            """INSERT INTO raw_records (source_id, external_id, run_id, payload_json, content_hash, updated_at)
               SELECT DISTINCT ON (external_id) %(source_id)s, external_id, run_id, payload_json, content_hash, now()
               FROM staging_records WHERE run_id=%(run_id)s AND external_id IS NOT NULL
               ORDER BY external_id, seq
               ON CONFLICT (source_id, external_id) DO UPDATE SET
                   run_id = EXCLUDED.run_id,
                   payload_json = CASE WHEN raw_records.content_hash = EXCLUDED.content_hash
                                       THEN raw_records.payload_json ELSE EXCLUDED.payload_json END,
                   content_hash = EXCLUDED.content_hash,
                   updated_at = CASE WHEN raw_records.content_hash = EXCLUDED.content_hash
                                     THEN raw_records.updated_at ELSE EXCLUDED.updated_at END""",
            {"source_id": self.source["id"], "run_id": self.run["id"]},
        )
        self.stats["raw_records_written"] = cur.rowcount

    def enqueue_index(self) -> None:
        if not self.changed:
            return
        ids = list(self.changed)
        self.conn.execute(
            "UPDATE products SET version = version + 1, updated_at = now() WHERE id = ANY(%s)", (ids,)
        )
        cur = self.conn.execute(
            """INSERT INTO index_outbox (entity_id, entity_version, action)
               SELECT p.id, p.version,
                      CASE WHEN EXISTS (SELECT 1 FROM offers o WHERE o.product_id = p.id AND o.active)
                           THEN 'upsert' ELSE 'delete' END
               FROM products p WHERE p.id = ANY(%s)""",
            (ids,),
        )
        self.stats["outbox_events"] = cur.rowcount

    def publish(self) -> dict:
        cur = self.conn.cursor(name=f"publish_{self.run['id'].hex}")
        cur.itersize = 2000
        cur.execute(
            """SELECT seq, group_key, normalized FROM staging_records
               WHERE run_id=%s AND status='valid' ORDER BY group_key, seq""",
            (self.run["id"],),
        )
        # Отдельное соединение не нужно: серверный курсор и остальные запросы живут в одной транзакции
        for group_key, rows in groupby(cur, key=lambda r: r["group_key"]):
            self.publish_group(group_key, list(rows))
        cur.close()
        if self.is_full:
            self.deactivate_missing()
        else:
            self.apply_deletions()
        self.write_raw()
        self.enqueue_index()
        self.stats["products_changed"] = len(self.changed)
        return dict(self.stats)


def publish_run(conn: psycopg.Connection, run_id, *, force: bool = False) -> dict:
    """Публикует run в статусе validated (или held при force=True — ручное подтверждение)."""
    run = conn.execute("SELECT * FROM import_runs WHERE id=%s", (run_id,)).fetchone()
    if run is None:
        raise PublishRefused("run_not_found", "import run not found")
    if run["status"] == "held" and not force:
        raise PublishRefused("run_held", f"run is held for review: {run['held_reason']}")
    if run["status"] not in ("validated", "held"):
        raise PublishRefused("bad_status", f"run status is {run['status']}")
    source = conn.execute(
        """SELECT s.id, s.slug, s.merchant_id, m.is_demo FROM feed_sources s
           JOIN merchants m ON m.id = s.merchant_id WHERE s.id=%s""",
        (run["source_id"],),
    ).fetchone()
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (source_lock_key(source["id"]) + ":publish",))
        newer = conn.execute(
            """SELECT 1 FROM import_runs WHERE source_id=%s AND status IN ('published','unchanged')
               AND created_at > %s""",
            (run["source_id"], run["created_at"]),
        ).fetchone()
        if newer:
            raise PublishRefused("newer_run_published", "a newer snapshot is already published")
        stats = Publisher(conn, run, source).publish()
        conn.execute(
            """UPDATE import_runs SET status='published', completed_at=now(), counts = counts || %s,
                   held_reason = CASE WHEN %s THEN held_reason || ' (approved manually)' ELSE held_reason END
               WHERE id=%s""",
            (Jsonb({"publish": stats}), run["status"] == "held", run_id),
        )
        conn.execute(
            "UPDATE feed_sources SET last_published_run_id=%s, last_checked_at=now() WHERE id=%s",
            (run_id, run["source_id"]),
        )
    conn.execute("DELETE FROM staging_records WHERE run_id=%s", (run_id,))
    log.info("run published", extra={"run_id": str(run_id), "source_id": str(source["id"]), **stats})
    return stats
