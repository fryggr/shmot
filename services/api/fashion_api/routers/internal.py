"""Защищённые внутренние эндпоинты: запуск импорта и аудит."""
from __future__ import annotations

import hmac
import uuid

import psycopg
from fastapi import APIRouter, Depends, Header

from fashion_domain.config import get_settings
from fashion_domain.queue import enqueue

from ..deps import get_conn, get_redis
from ..errors import ApiError
from ..schemas import ImportRequest, PublishRequest

router = APIRouter(prefix="/internal")


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise ApiError(503, "internal_api_disabled", "INTERNAL_API_TOKEN is not configured")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise ApiError(401, "unauthorized", "invalid or missing internal token")


RUN_FIELDS = ("id", "source_id", "status", "is_full_snapshot", "mapping_version", "checksum", "snapshot_bytes",
              "triggered_by", "held_reason", "counts", "audit", "errors", "created_at", "started_at", "completed_at")


@router.post("/imports", status_code=202, dependencies=[Depends(require_token)])
def start_import(req: ImportRequest, conn: psycopg.Connection = Depends(get_conn)):
    src = conn.execute(
        "SELECT id, slug, is_full_snapshot, mapping_version, enabled FROM feed_sources WHERE slug=%s",
        (req.source,),
    ).fetchone()
    if src is None:
        raise ApiError(404, "source_not_found", "source not found")
    run = conn.execute(
        """INSERT INTO import_runs (source_id, status, is_full_snapshot, mapping_version, triggered_by)
           VALUES (%s, 'queued', %s, %s, 'api') RETURNING id""",
        (src["id"], src["is_full_snapshot"], src["mapping_version"]),
    ).fetchone()
    try:
        enqueue(get_redis(), {"type": "import", "source_id": str(src["id"]), "run_id": str(run["id"]),
                              "force": req.force})
    except Exception as exc:  # noqa: BLE001
        conn.execute(
            """UPDATE import_runs SET status='failed', completed_at=now(),
                   errors='[{"code":"queue_unavailable","message":"job queue is unavailable"}]' WHERE id=%s""",
            (run["id"],),
        )
        raise ApiError(503, "queue_unavailable", "job queue is unavailable") from exc
    return {"run_id": str(run["id"]), "status": "queued"}


@router.get("/imports/{run_id}", dependencies=[Depends(require_token)])
def get_import(run_id: str, conn: psycopg.Connection = Depends(get_conn)):
    try:
        rid = uuid.UUID(run_id)
    except ValueError as exc:
        raise ApiError(404, "not_found", "run not found") from exc
    row = conn.execute(f"SELECT {', '.join(RUN_FIELDS)} FROM import_runs WHERE id=%s", (rid,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "run not found")
    return row  # snapshot_uri и secret_ref наружу не отдаются


@router.post("/imports/{run_id}/publish", status_code=202, dependencies=[Depends(require_token)])
def approve_import(run_id: str, req: PublishRequest, conn: psycopg.Connection = Depends(get_conn)):
    try:
        rid = uuid.UUID(run_id)
    except ValueError as exc:
        raise ApiError(404, "not_found", "run not found") from exc
    row = conn.execute("SELECT status FROM import_runs WHERE id=%s", (rid,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "run not found")
    if row["status"] not in ("held", "validated"):
        raise ApiError(409, "bad_status", f"run status is {row['status']}")
    if row["status"] == "held" and not req.approve:
        raise ApiError(409, "approval_required", "held run requires approve=true after manual review")
    enqueue(get_redis(), {"type": "publish", "run_id": str(rid), "approve": req.approve})
    return {"run_id": str(rid), "status": "publish_queued"}
