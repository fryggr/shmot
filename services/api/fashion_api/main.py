"""FastAPI-приложение Fashion Search POC."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from fashion_domain.logs import setup_logging

from . import errors
from .deps import close_pool, open_pool
from .ratelimit import RateLimiter
from .routers import health, internal, public

log = logging.getLogger("fashion_api")
RATE_LIMITED_PREFIXES = ("/api/v1/search", "/api/v1/out/")


def create_app() -> FastAPI:
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        open_pool()
        yield
        close_pool()

    app = FastAPI(title="Fashion Search POC API", version="0.1.0", lifespan=lifespan)
    limiter = RateLimiter()
    app.state.limiter = limiter

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        if request.url.path.startswith(RATE_LIMITED_PREFIXES):
            client = request.client.host if request.client else "unknown"
            if not app.state.limiter.allow(client):
                return JSONResponse(
                    errors.error_body(request, "rate_limited", "too many requests"), status_code=429,
                    headers={"Retry-After": "60", "X-Request-ID": request.state.request_id},
                )
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith(("/api/v1/search", "/internal")):
            response.headers["X-Robots-Tag"] = "noindex"
        # Путь без query string: в запросах могут быть персональные сведения
        log.info("request", extra={"request_id": request.state.request_id, "method": request.method,
                                   "path": request.url.path, "status": response.status_code,
                                   "ms": round((time.perf_counter() - started) * 1000, 1)})
        return response

    app.add_middleware(
        CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )
    errors.install(app)
    app.include_router(health.router)
    app.include_router(public.router)
    app.include_router(internal.router)
    return app


app = create_app()
