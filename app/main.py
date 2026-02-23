"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.router import api_router
from app.broker import connect as broker_connect, disconnect as broker_disconnect
from app.config import settings
from app.delivery.sender import close_client
from app.metrics import router as metrics_router

logger = structlog.get_logger(__name__)


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds the configured limit."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > settings.api_max_request_body_bytes:
            return JSONResponse(
                {"detail": "Request body too large"},
                status_code=413,
            )
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: open shared RabbitMQ connection
    await broker_connect(app)
    yield
    # Shutdown: close RabbitMQ connection and shared HTTP client
    await broker_disconnect(app)
    await close_client()


def create_app() -> FastAPI:
    application = FastAPI(
        title="Event Processing & Distribution Service",
        version="1.0.0",
        description="RabbitMQ → LLM enrichment → webhook delivery pipeline",
        lifespan=lifespan,
    )

    application.add_middleware(_BodySizeLimitMiddleware)
    application.include_router(api_router)
    application.include_router(metrics_router)

    @application.get("/health/live", tags=["health"])
    async def liveness() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @application.get("/health/ready", tags=["health"])
    async def readiness() -> JSONResponse:
        from sqlalchemy import text
        from app.db.session import engine

        errors: list[str] = []

        # Check DB
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            errors.append(f"db: {exc}")

        # Check RabbitMQ via app.state connection
        try:
            conn = getattr(application.state, "rmq_connection", None)
            if conn is None or conn.is_closed:
                errors.append("rabbitmq: connection not established")
        except Exception as exc:
            errors.append(f"rabbitmq: {exc}")

        if errors:
            return JSONResponse({"status": "degraded", "errors": errors}, status_code=503)
        return JSONResponse({"status": "ok"})

    return application


app = create_app()
