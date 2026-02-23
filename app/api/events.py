"""Event publishing and observation endpoints."""
from __future__ import annotations

import json
import uuid

import aio_pika
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.broker import get_exchange
from app.config import settings
from app.db.models import IdempotencyKey
from app.db.session import get_db
from app.schemas.event import EnrichedEventResponse, EventPublishRequest, EventPublishResponse

router = APIRouter(tags=["events"])
logger = structlog.get_logger(__name__)


@router.post("/events/publish", response_model=EventPublishResponse, status_code=202)
async def publish_event(body: EventPublishRequest, request: Request) -> EventPublishResponse:
    message_id = body.message_id or str(uuid.uuid4())

    msg_body = json.dumps(
        {
            "message_id": message_id,
            "event_type": body.event_type,
            "payload": body.payload,
        }
    ).encode()

    exchange = await get_exchange(request.app)
    await exchange.publish(
        aio_pika.Message(
            body=msg_body,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=message_id,
        ),
        routing_key=settings.rabbitmq_queue,
    )

    logger.info("api.event_published", message_id=message_id, event_type=body.event_type)
    return EventPublishResponse(message_id=message_id)


@router.get("/events/{message_id}", response_model=EnrichedEventResponse)
async def get_event(
    message_id: str, db: AsyncSession = Depends(get_db)
) -> EnrichedEventResponse:
    result = await db.execute(
        select(IdempotencyKey).where(IdempotencyKey.message_id == message_id)
    )
    ik = result.scalar_one_or_none()
    if ik is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return EnrichedEventResponse.model_validate(ik)
