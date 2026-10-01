"""Задание импорта источника: download → parse → staging → validate → audit → guards → publish.

Гарантии:
* блокировка источника (advisory lock) — два импорта одного источника не идут параллельно;
* ошибка скачивания/разбора или неполный импорт не меняют опубликованный каталог;
* публикация останавливается (status=held) при падении объёма или доле ошибок выше порога.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from fashion_domain.config import Settings, get_settings
from fashion_domain.normalization import load_dictionaries, map_category
from fashion_domain.storage import SnapshotStore, get_snapshot_store

from ..adapters.download import DownloadError, download, resolve_secret
from ..adapters.yml import FeedRejected, ParseLimits, YmlParseResult, category_path, iter_yml_offers
from .publish import PublishRefused, publish_run, source_lock_key
from .validate import MAPPING_VERSION, SourceContext, validate_record

log = logging.getLogger(__name__)

BATCH = 1000
NULL_FIELDS = ("description", "brand", "model_code", "colorway_code", "color", "gtin", "old_price_minor")


class ImportFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _load_source(conn: psycopg.Connection, source: str) -> dict:
    row = conn.execute(
        """SELECT s.*, m.allowed_domains, m.is_demo, m.slug AS merchant_slug, m.enabled AS merchant_enabled
           FROM feed_sources s JOIN merchants m ON m.id = s.merchant_id
           WHERE s.slug = %s OR s.id::text = %s""",
        (source, source),
    ).fetchone()
    if row is None:
        raise ImportFailed("source_not_found", f"source {source!r} not found")
    return row


NOW = object()


def _set_run(conn, run_id, **fields) -> None:
    cols = ", ".join(f"{k} = now()" if v is NOW else f"{k} = %({k})s" for k, v in fields.items())
    params = {k: Jsonb(v) if isinstance(v, (dict, list)) else v for k, v in fields.items() if v is not NOW}
    conn.execute(f"UPDATE import_runs SET {cols} WHERE id = %(run_id)s", params | {"run_id": run_id})


def create_run(conn: psycopg.Connection, source: str, triggered_by: str) -> dict:
    src = _load_source(conn, source)
    return conn.execute(
        """INSERT INTO import_runs (source_id, status, is_full_snapshot, mapping_version, triggered_by)
           VALUES (%s, 'queued', %s, %s, %s) RETURNING *""",
        (src["id"], src["is_full_snapshot"], src["mapping_version"], triggered_by),
    ).fetchone()


# ------------------------------------------------------------------- шаги
def _stage(conn, run_id, stream, settings: Settings) -> YmlParseResult:
    result = YmlParseResult()
    limits = ParseLimits(settings.max_snapshot_bytes, settings.max_records, settings.max_xml_depth)
    batch: list[tuple] = []

    def flush():
        if batch:
            with conn.transaction(), conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO staging_records (run_id, seq, external_id, payload_json, content_hash, status)
                       VALUES (%s, %s, %s, %s, %s, 'pending')""",
                    batch,
                )
            batch.clear()

    for seq, payload in enumerate(iter_yml_offers(stream, limits, result)):
        ext = (payload["attrs"].get("id") or "").strip() or None
        batch.append((run_id, seq, ext, Jsonb(payload), _payload_hash(payload)))
        if len(batch) >= BATCH:
            flush()
    flush()
    return result


def _category_context(conn, src, parsed: YmlParseResult) -> tuple[dict, dict, Counter]:
    d = load_dictionaries()
    manual = {
        r["source_category_id"]: r["category_code"]
        for r in conn.execute(
            "SELECT source_category_id, category_code FROM category_mappings WHERE source_id=%s AND method='manual'",
            (src["id"],),
        ).fetchall()
    }
    paths, codes, stats = {}, {}, Counter()
    with conn.transaction():
        for cid in parsed.categories:
            path = category_path(parsed.categories, cid) or ""
            paths[cid] = path
            if cid in manual:
                codes[cid] = manual[cid]
                stats["manual"] += 1
                continue
            code = map_category(path, d)
            codes[cid] = code
            stats["rule" if code else "unmapped"] += 1
            conn.execute(
                """INSERT INTO category_mappings (source_id, source_category_id, source_path, category_code, method)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (source_id, source_category_id) DO UPDATE SET
                       source_path = EXCLUDED.source_path, category_code = EXCLUDED.category_code,
                       method = EXCLUDED.method, updated_at = now()
                   WHERE category_mappings.method <> 'manual'""",
                (src["id"], cid, path, code, "rule" if code else "unmapped"),
            )
    return paths, codes, stats


def _validate(conn, run_id, src, parsed: YmlParseResult, settings: Settings) -> dict:
    d = load_dictionaries()
    paths, codes, cat_stats = _category_context(conn, src, parsed)
    ctx = SourceContext(
        allowed_domains=list(src["allowed_domains"]),
        allowed_currencies=settings.allowed_currencies,
        category_paths=paths,
        category_codes=codes,
        is_full_snapshot=src["is_full_snapshot"],
    )
    seen: set[str] = set()
    status_c, errors_c, warn_c = Counter(), Counter(), Counter()
    nulls, brands, cats, systems, avail = Counter(), Counter(), Counter(), Counter(), Counter()
    groups, models = set(), set()
    no_size = unknown_gender = unmapped = no_material = 0
    last_seq = -1
    while True:
        rows = conn.execute(
            "SELECT seq, payload_json FROM staging_records WHERE run_id=%s AND seq>%s ORDER BY seq LIMIT %s",
            (run_id, last_seq, BATCH),
        ).fetchall()
        if not rows:
            break
        updates = []
        for row in rows:
            last_seq = row["seq"]
            v = validate_record(row["payload_json"], ctx, d)
            if v.external_id and v.status != "deleted":
                if v.external_id in seen:
                    v.status, v.normalized = "quarantined", None
                    v.errors = v.errors + ["duplicate_external_id"]
                seen.add(v.external_id)
            status_c[v.status] += 1
            errors_c.update(v.errors)
            warn_c.update(v.warnings)
            if v.status == "valid":
                n = v.normalized
                groups.add(v.group_key)
                models.add((n["brand"], n["model_code"] or v.group_key.split("|")[0]))
                for f in NULL_FIELDS:
                    if not n.get(f):
                        nulls[f] += 1
                no_material += not n["materials"]
                no_size += n["size"] is None
                unknown_gender += n["gender"] == "unknown"
                unmapped += n["category_code"] is None
                brands[n["brand"] or "∅"] += 1
                cats[n["category_code"] or "unmapped"] += 1
                systems[(n["size"] or {}).get("size_system", "none")] += 1
                avail[n["availability"]] += 1
            updates.append((v.status, Jsonb(v.normalized) if v.normalized else None, v.group_key,
                            v.errors, run_id, row["seq"]))
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                """UPDATE staging_records SET status=%s, normalized=%s, group_key=%s, error_codes=%s
                   WHERE run_id=%s AND seq=%s""",
                updates,
            )
    total = sum(status_c.values())
    valid = status_c["valid"]

    def ratio(x):
        return round(x / valid, 4) if valid else None

    return {
        "records": total,
        "valid": valid,
        "quarantined": status_c["quarantined"],
        "deleted": status_c["deleted"],
        "offers": len(groups),
        "models": len(models),
        "variants": valid,
        "duplicate_ids": errors_c["duplicate_external_id"],
        "error_ratio": round(status_c["quarantined"] / total, 4) if total else 0.0,
        "quarantine_reasons": dict(errors_c),
        "warnings": dict(warn_c),
        "null_ratio": {f: ratio(nulls[f]) for f in NULL_FIELDS}
        | {"materials": ratio(no_material), "size": ratio(no_size), "gender_unknown": ratio(unknown_gender),
           "category_unmapped": ratio(unmapped)},
        "availability": dict(avail),
        "availability_unknown_ratio": ratio(avail["unknown"]),
        "unavailable_ratio": ratio(avail["out_of_stock"]),
        "size_systems": dict(systems),
        "brands": dict(brands.most_common(50)),
        "brand_count": len(brands),
        "categories": dict(cats),
        "source_categories": dict(cat_stats),
    }


def _guard(conn, src, run_id, audit: dict) -> str | None:
    if audit["records"] and audit["error_ratio"] > float(src["max_error_ratio"]):
        return f"error_ratio {audit['error_ratio']:.3f} > {float(src['max_error_ratio']):.3f}"
    if not src["is_full_snapshot"]:
        return None
    if audit["valid"] == 0:
        return "empty_snapshot"
    prev = conn.execute(
        """SELECT counts FROM import_runs WHERE source_id=%s AND status='published' AND is_full_snapshot
           AND id <> %s ORDER BY completed_at DESC LIMIT 1""",
        (src["id"], run_id),
    ).fetchone()
    if prev and (prev_valid := prev["counts"].get("valid")):
        drop = 1 - audit["valid"] / prev_valid
        if drop > float(src["max_drop_ratio"]):
            return f"volume_drop {drop:.3f} > {float(src['max_drop_ratio']):.3f} (prev valid={prev_valid})"
    return None


def _touch_freshness(conn, src) -> int:
    """Тот же снимок: продавец подтвердил актуальность — обновляем проверку свежести без enrichment."""
    with conn.transaction():
        conn.execute("UPDATE feed_sources SET last_checked_at=now() WHERE id=%s", (src["id"],))
        return conn.execute(
            "UPDATE offers SET last_seen_at=now() WHERE source_id=%s AND active", (src["id"],)
        ).rowcount


# -------------------------------------------------------------------- run
def run_import(
    conn: psycopg.Connection,
    source: str,
    *,
    run_id=None,
    triggered_by: str = "cli",
    force: bool = False,
    snapshot_uri: str | None = None,
    store: SnapshotStore | None = None,
    settings: Settings | None = None,
) -> dict:
    """Выполняет импорт и возвращает строку import_runs. conn должен быть в autocommit."""
    settings = settings or get_settings()
    store = store or get_snapshot_store(settings)
    src = _load_source(conn, source)
    if run_id is None:
        run_id = create_run(conn, src["slug"], triggered_by)["id"]
    lock_key = source_lock_key(src["id"])
    if not conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS ok", (lock_key,)).fetchone()["ok"]:
        _set_run(conn, run_id, status="failed", completed_at=NOW,
                 errors=[{"code": "source_locked", "message": "another import of this source is running"}])
        return conn.execute("SELECT * FROM import_runs WHERE id=%s", (run_id,)).fetchone()

    started = time.monotonic()
    tmpdir = Path(tempfile.mkdtemp(prefix="fashion-import-"))
    try:
        conn.execute("UPDATE import_runs SET status='running', started_at=now() WHERE id=%s", (run_id,))
        if not src["enabled"] or not src["merchant_enabled"]:
            raise ImportFailed("source_disabled", "source or merchant is disabled")

        # 1. Снимок: скачиваем (или берём сохранённый при восстановлении), сохраняем в закрытое хранилище
        local = tmpdir / "snapshot"
        if snapshot_uri:
            with store.open(snapshot_uri) as fh, local.open("wb") as out:
                shutil.copyfileobj(fh, out)
            digest = hashlib.sha256(local.read_bytes()).hexdigest()
            headers, size, uri = {"restored_from": snapshot_uri}, local.stat().st_size, snapshot_uri
        else:
            res = download(resolve_secret(src["secret_ref"]), local, settings)
            digest, size, headers, uri = res.checksum, res.size, res.headers, None
        _set_run(conn, run_id, checksum=digest, snapshot_bytes=size, counts={"headers": headers},
                 **({"snapshot_uri": uri} if uri else {}))

        last = conn.execute(
            """SELECT r.checksum FROM feed_sources s JOIN import_runs r ON r.id = s.last_published_run_id
               WHERE s.id=%s""",
            (src["id"],),
        ).fetchone()
        if last and last["checksum"] == digest and not force:
            touched = _touch_freshness(conn, src)
            _set_run(conn, run_id, status="unchanged", completed_at=NOW,
                     counts={"headers": headers, "offers_freshness_touched": touched,
                             "seconds": round(time.monotonic() - started, 3)})
            return conn.execute("SELECT * FROM import_runs WHERE id=%s", (run_id,)).fetchone()

        if uri is None:
            try:
                uri = store.put(f"{src['slug']}/{time.strftime('%Y-%m-%d')}/{run_id}.xml", local)
            except Exception as exc:  # noqa: BLE001 — без сохранённого снимка не публикуем
                raise ImportFailed("snapshot_store_unavailable", type(exc).__name__) from exc
            _set_run(conn, run_id, snapshot_uri=uri)

        # 2. Потоковый разбор в staging
        _set_run(conn, run_id, status="staging")
        with local.open("rb") as fh:
            parsed = _stage(conn, run_id, fh, settings)

        # 3–4. Проверка, нормализация, аудит
        audit = _validate(conn, run_id, src, parsed, settings)
        audit["seconds"] = round(time.monotonic() - started, 3)
        audit["mapping_version"] = MAPPING_VERSION
        counts = {"records": audit["records"], "valid": audit["valid"], "quarantined": audit["quarantined"],
                  "offers": audit["offers"], "models": audit["models"], "headers": headers}
        _set_run(conn, run_id, status="validated", audit=audit, counts=counts)

        # 5. Защитные пороги
        reason = _guard(conn, src, run_id, audit)
        if reason:
            _set_run(conn, run_id, status="held", held_reason=reason, completed_at=NOW)
            log.warning("publication held", extra={"run_id": str(run_id), "source_id": str(src["id"]), "reason": reason})
            return conn.execute("SELECT * FROM import_runs WHERE id=%s", (run_id,)).fetchone()

        # 6–8. Публикация (одна транзакция) + outbox
        publish_run(conn, run_id)
        conn.execute(
            "UPDATE import_runs SET audit = audit || %s WHERE id=%s",
            (Jsonb({"seconds_total": round(time.monotonic() - started, 3)}), run_id),
        )
    except (FeedRejected, DownloadError, ImportFailed, PublishRefused) as exc:
        _fail(conn, run_id, exc.code, str(exc))
    except Exception as exc:  # noqa: BLE001 — любая ошибка не должна трогать каталог
        log.exception("import crashed", extra={"run_id": str(run_id), "source_id": str(src["id"])})
        _fail(conn, run_id, "internal_error", type(exc).__name__)
    finally:
        conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (lock_key,))
        shutil.rmtree(tmpdir, ignore_errors=True)
    return conn.execute("SELECT * FROM import_runs WHERE id=%s", (run_id,)).fetchone()


def _fail(conn, run_id, code: str, message: str) -> None:
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        conn.rollback()
    conn.execute(
        """UPDATE import_runs SET status='failed', completed_at=now(),
               errors = errors || %s WHERE id=%s""",
        (Jsonb([{"code": code, "message": message}]), run_id),
    )
    conn.execute("DELETE FROM staging_records WHERE run_id=%s", (run_id,))
    log.error("import failed", extra={"run_id": str(run_id), "code": code})
