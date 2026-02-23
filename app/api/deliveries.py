"""Delivery observation and management endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

import aio_pika
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import DeliveryAttempt, IdempotencyKey
from app.db.session import get_db
from app.schemas.delivery import DeliveryAttemptResponse, PipelineStats

router = APIRouter(tags=["deliveries"])


class DeliveryStatus(str, Enum):
    """M5: Enumerated delivery statuses — prevents silent no-match on typos."""
    pending = "pending"
    in_flight = "in_flight"
    delivered = "delivered"
    failed = "failed"
    dead = "dead"


@router.get("/deliveries", response_model=List[DeliveryAttemptResponse])
async def list_deliveries(
    status: Optional[DeliveryStatus] = Query(None),
    subscriber_id: Optional[uuid.UUID] = Query(None),
    message_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> List[DeliveryAttemptResponse]:
    stmt = select(DeliveryAttempt).order_by(DeliveryAttempt.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(DeliveryAttempt.status == status.value)
    if subscriber_id:
        stmt = stmt.where(DeliveryAttempt.subscriber_id == subscriber_id)
    if message_id:
        stmt = stmt.where(DeliveryAttempt.message_id == message_id)
    result = await db.execute(stmt)
    return [DeliveryAttemptResponse.model_validate(d) for d in result.scalars().all()]


@router.post("/deliveries/{delivery_id}/retry", response_model=DeliveryAttemptResponse)
async def retry_delivery(
    delivery_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> DeliveryAttemptResponse:
    result = await db.execute(
        select(DeliveryAttempt).where(DeliveryAttempt.id == delivery_id)
    )
    attempt = result.scalar_one_or_none()
    if attempt is None:
        raise HTTPException(status_code=404, detail="Delivery attempt not found")
    if attempt.status not in ("dead", "failed"):
        raise HTTPException(
            status_code=409,
            detail=f"Can only retry dead/failed attempts; current status={attempt.status!r}",
        )
    attempt.status = "pending"
    attempt.next_attempt_at = datetime.now(timezone.utc)
    attempt.last_error = None
    attempt.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(attempt)
    return DeliveryAttemptResponse.model_validate(attempt)


@router.get("/pipeline/stats", response_model=PipelineStats)
async def pipeline_stats(db: AsyncSession = Depends(get_db)) -> PipelineStats:
    # Delivery status counts
    delivery_counts = await db.execute(
        select(DeliveryAttempt.status, func.count().label("cnt"))
        .group_by(DeliveryAttempt.status)
    )
    deliveries_by_status = {row.status: row.cnt for row in delivery_counts}

    # Idempotency key status counts
    ik_counts = await db.execute(
        select(IdempotencyKey.status, func.count().label("cnt"))
        .group_by(IdempotencyKey.status)
    )
    ik_by_status = {row.status: row.cnt for row in ik_counts}

    # RabbitMQ queue depth via management API (best-effort)
    # C5: surface unavailability explicitly instead of silently returning 0
    import httpx
    queue_depth: int | None = None
    queue_depth_available = True
    queue_depth_error: str | None = None
    try:
        url = (
            f"{settings.rabbitmq_management_url}/api/queues/%2F/{settings.rabbitmq_queue}"
        )
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                url,
                auth=(settings.rabbitmq_management_user, settings.rabbitmq_management_password),
            )
            if resp.status_code == 200:
                queue_depth = resp.json().get("messages", 0)
            else:
                queue_depth_available = False
                queue_depth_error = f"management API returned HTTP {resp.status_code}"
    except Exception as exc:
        queue_depth_available = False
        queue_depth_error = str(exc)

    if not queue_depth_available:
        import structlog
        structlog.get_logger(__name__).warning(
            "pipeline_stats.queue_depth_unavailable", error=queue_depth_error
        )

    return PipelineStats(
        queue_depth=queue_depth,
        queue_depth_available=queue_depth_available,
        queue_depth_error=queue_depth_error,
        deliveries_by_status=deliveries_by_status,
        idempotency_keys_by_status=ik_by_status,
    )
