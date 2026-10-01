"""CLI worker: миграции, seed, импорт, публикация, восстановление, индексация, цикл очереди."""
from __future__ import annotations

import argparse
import json
import logging
import signal
import time

from fashion_domain.config import get_settings
from fashion_domain.db import connect, migrate
from fashion_domain.logs import setup_logging
from fashion_domain.storage import get_snapshot_store

from .indexing.outbox import drain_outbox, rebuild_index, reconcile
from .jobs.import_job import run_import
from .jobs.publish import PublishRefused, publish_run
from .seed import seed_demo

log = logging.getLogger("fashion_worker")


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _run_summary(run: dict) -> dict:
    keys = ("id", "status", "held_reason", "checksum", "snapshot_uri", "counts", "errors", "audit")
    return {k: run[k] for k in keys if k in run}


def _after_import(conn, run) -> dict:
    out = {"run": _run_summary(run)}
    if run["status"] == "published":
        out["index"] = drain_outbox(conn)
        out["reconcile"] = reconcile(conn)
    return out


def cmd_import(conn, args):
    run = run_import(conn, args.source, force=args.force)
    return _after_import(conn, run)


def cmd_publish(conn, args):
    try:
        stats = publish_run(conn, args.run_id, force=args.approve)
    except PublishRefused as exc:
        return {"error": exc.code, "message": str(exc)}
    return {"publish": stats, "index": drain_outbox(conn), "reconcile": reconcile(conn)}


def cmd_restore(conn, args):
    """Полное восстановление каталога источника из последнего принятого снимка."""
    row = conn.execute(
        """SELECT r.snapshot_uri FROM feed_sources s JOIN import_runs r ON r.id = s.last_published_run_id
           WHERE s.slug=%s OR s.id::text=%s""",
        (args.source, args.source),
    ).fetchone()
    if not row or not row["snapshot_uri"]:
        return {"error": "no_published_snapshot"}
    run = run_import(conn, args.source, force=True, snapshot_uri=row["snapshot_uri"], triggered_by="restore")
    return _after_import(conn, run)


def cmd_cleanup(conn, args):
    """Сроки хранения POC: снимки 14 дней, import_runs 90 дней, запросы/события 30 дней."""
    store = get_snapshot_store()
    # Последний принятый снимок каждого источника нужен для restore — его не удаляем
    keep = {
        r["snapshot_uri"]
        for r in conn.execute(
            """SELECT r.snapshot_uri FROM feed_sources s JOIN import_runs r ON r.id = s.last_published_run_id
               WHERE r.snapshot_uri IS NOT NULL"""
        ).fetchall()
    }
    removed = store.delete_older_than("", 14, keep)
    with conn.transaction():
        runs = conn.execute(
            """DELETE FROM import_runs r WHERE r.created_at < now() - interval '90 days'
               AND NOT EXISTS (SELECT 1 FROM feed_sources s WHERE s.last_published_run_id = r.id)
               AND NOT EXISTS (SELECT 1 FROM offers o WHERE o.last_run_id = r.id)
               AND NOT EXISTS (SELECT 1 FROM raw_records w WHERE w.run_id = r.id)"""
        ).rowcount
        sessions = conn.execute("DELETE FROM search_sessions WHERE created_at < now() - interval '30 days'").rowcount
        events = conn.execute("DELETE FROM events WHERE created_at < now() - interval '30 days'").rowcount
    return {"snapshots_removed": removed, "runs_removed": runs, "sessions_removed": sessions, "events_removed": events}


def cmd_run(conn, args):
    """Цикл worker: задания из Redis + периодическая обработка outbox."""
    import redis

    from fashion_domain.queue import dequeue

    client = redis.Redis.from_url(get_settings().redis_url)
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    log.info("worker started")
    last_outbox = 0.0
    while not stop["flag"]:
        job = dequeue(client, timeout=2)
        if job:
            log.info("job received", extra={"job_type": job.get("type"), "run_id": job.get("run_id")})
            try:
                if job["type"] == "import":
                    run = run_import(conn, job["source_id"], run_id=job["run_id"], force=job.get("force", False),
                                     triggered_by="api")
                    log.info("import finished", extra={"run_id": str(run["id"]), "status": run["status"]})
                elif job["type"] == "publish":
                    publish_run(conn, job["run_id"], force=job.get("approve", False))
            except Exception:  # noqa: BLE001
                log.exception("job failed", extra={"run_id": job.get("run_id")})
        if time.monotonic() - last_outbox > 2:
            res = drain_outbox(conn)
            if res["processed"] or res["failed"]:
                log.info("outbox processed", extra=res)
            last_outbox = time.monotonic()
    return {"stopped": True}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="fashion-worker")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sub.add_parser("seed-demo")
    p = sub.add_parser("import"); p.add_argument("source"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("publish"); p.add_argument("run_id"); p.add_argument("--approve", action="store_true",
                                                                           help="publish a held run after review")
    p = sub.add_parser("restore"); p.add_argument("source")
    p = sub.add_parser("audit"); p.add_argument("run_id")
    sub.add_parser("outbox")
    sub.add_parser("reconcile")
    sub.add_parser("rebuild-index")
    sub.add_parser("cleanup")
    sub.add_parser("run")
    args = parser.parse_args(argv)
    setup_logging()

    with connect(autocommit=True) as conn:
        if args.cmd == "migrate":
            out = {"applied": migrate(conn)}
        elif args.cmd == "seed-demo":
            out = seed_demo(conn)
        elif args.cmd == "import":
            out = cmd_import(conn, args)
        elif args.cmd == "publish":
            out = cmd_publish(conn, args)
        elif args.cmd == "restore":
            out = cmd_restore(conn, args)
        elif args.cmd == "audit":
            run = conn.execute("SELECT * FROM import_runs WHERE id=%s", (args.run_id,)).fetchone()
            out = _run_summary(run) if run else {"error": "run_not_found"}
        elif args.cmd == "outbox":
            out = drain_outbox(conn)
        elif args.cmd == "reconcile":
            out = reconcile(conn)
        elif args.cmd == "rebuild-index":
            out = rebuild_index(conn) | {"reconcile": reconcile(conn)}
        elif args.cmd == "cleanup":
            out = cmd_cleanup(conn, args)
        else:
            out = cmd_run(conn, args)
    _print(out)


if __name__ == "__main__":
    main()
