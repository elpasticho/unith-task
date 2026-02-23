"""FastAPI application factory."""
from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.router import api_router

logger = structlog.get_logger(__name__)


def create_app() -> FastAPI:
    application = FastAPI(
        title="Event Processing & Distribution Service",
        version="1.0.0",
        description="RabbitMQ → LLM enrichment → webhook delivery pipeline",
    )

    application.include_router(api_router)

    @application.get("/health/live", tags=["health"])
    async def liveness() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @application.get("/health/ready", tags=["health"])
    async def readiness() -> JSONResponse:
        from sqlalchemy import text
        from app.db.session import engine
        import aio_pika
        from app.config import settings

        errors: list[str] = []

        # Check DB
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            errors.append(f"db: {exc}")

        # Check RabbitMQ
        try:
            conn = await aio_pika.connect_robust(settings.rabbitmq_url)
            await conn.close()
        except Exception as exc:
            errors.append(f"rabbitmq: {exc}")

        if errors:
            return JSONResponse({"status": "degraded", "errors": errors}, status_code=503)
        return JSONResponse({"status": "ok"})

    return application


app = create_app()
