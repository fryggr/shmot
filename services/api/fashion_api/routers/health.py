from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import get_redis, open_pool

router = APIRouter(prefix="/health")


@router.get("/live")
def live():
    return {"status": "ok"}


@router.get("/ready")
def ready():
    checks = {}
    try:
        with open_pool().connection(timeout=2) as conn:
            conn.execute("SELECT 1 FROM schema_migrations LIMIT 1")
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"error: {type(exc).__name__}"
    try:
        get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)
