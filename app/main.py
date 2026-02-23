"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.broker import connect as broker_connect, disconnect as broker_disconnect
from app.metrics import router as metrics_router

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: open shared RabbitMQ connection
    await broker_connect(app)
    yield
    # Shutdown: close gracefully
    await broker_disconnect(app)


def create_app() -> FastAPI:
    application = FastAPI(
        title="Event Processing & Distribution Service",
        version="1.0.0",
        description="RabbitMQ → LLM enrichment → webhook delivery pipeline",
        lifespan=lifespan,
    )

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
