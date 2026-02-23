"""Shared RabbitMQ connection managed by the FastAPI lifespan.

Usage in route handlers:
    from app.broker import get_exchange
    exchange = await get_exchange(request.app)
    await exchange.publish(...)
"""
from __future__ import annotations

import aio_pika
import structlog
from fastapi import FastAPI

from app.config import settings

logger = structlog.get_logger(__name__)


async def connect(app: FastAPI) -> None:
    """Open a robust connection and store exchange on app.state."""
    connection = await aio_pika.connect_robust(
        settings.rabbitmq_url,
        heartbeat=60,
    )
    channel = await connection.channel()
    exchange = await channel.declare_exchange(
        settings.rabbitmq_exchange,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )
    # Ensure queue exists so publishes don't get dropped
    await channel.declare_queue(settings.rabbitmq_queue, durable=True)

    app.state.rmq_connection = connection
    app.state.rmq_exchange = exchange
    logger.info("broker.connected")


async def disconnect(app: FastAPI) -> None:
    """Close the connection gracefully."""
    conn: aio_pika.RobustConnection = getattr(app.state, "rmq_connection", None)
    if conn and not conn.is_closed:
        await conn.close()
    logger.info("broker.disconnected")


async def get_exchange(app: FastAPI) -> aio_pika.Exchange:
    return app.state.rmq_exchange
