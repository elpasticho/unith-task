"""Delivery worker — polls delivery_attempts with SELECT FOR UPDATE SKIP LOCKED."""
from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import DeliveryAttempt, IdempotencyKey, Subscriber
from app.db.session import AsyncSessionLocal
from app.delivery.sender import deliver

logger = structlog.get_logger(__name__)


def _backoff_seconds(attempt: int) -> float:
    """Full-jitter exponential backoff."""
    base = settings.delivery_base_delay_seconds
    cap = min(base * (2 ** attempt), settings.delivery_max_delay_seconds)
    return random.uniform(0, cap)


async def _reset_stale_in_flight() -> None:
    """On startup, reset in_flight rows older than threshold to failed."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=settings.delivery_stale_in_flight_minutes
    )
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(DeliveryAttempt)
            .where(
                DeliveryAttempt.status == "in_flight",
                DeliveryAttempt.updated_at < cutoff,
            )
            .values(
                status="failed",
                last_error="reset by worker startup (stale in_flight)",
                next_attempt_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    logger.info("worker.stale_reset_complete")


async def _process_batch() -> int:
    """Claim and deliver a batch of pending delivery attempts. Returns count processed."""
    now = datetime.now(timezone.utc)
    processed = 0

    async with AsyncSessionLocal() as session:
        # Claim rows: SET status='in_flight' atomically
        stmt = (
            select(DeliveryAttempt)
            .where(
                DeliveryAttempt.status.in_(["pending", "failed"]),
                DeliveryAttempt.next_attempt_at <= now,
            )
            .with_for_update(skip_locked=True)
            .limit(50)
            .options(
                selectinload(DeliveryAttempt.idempotency_key),
                selectinload(DeliveryAttempt.subscriber),
            )
        )
        result = await session.execute(stmt)
        attempts = result.scalars().all()

        if not attempts:
            return 0

        # Mark all as in_flight within the same transaction
        ids = [a.id for a in attempts]
        await session.execute(
            update(DeliveryAttempt)
            .where(DeliveryAttempt.id.in_(ids))
            .values(status="in_flight", updated_at=now)
        )
        await session.commit()

    # Deliver outside the lock (don't hold DB lock during HTTP call)
    for attempt in attempts:
        await _deliver_attempt(attempt)
        processed += 1

    return processed


async def _deliver_attempt(attempt: DeliveryAttempt) -> None:
    sub: Subscriber = attempt.subscriber
    ik: IdempotencyKey = attempt.idempotency_key

    if not sub.is_active or sub.deleted_at is not None:
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(DeliveryAttempt)
                .where(DeliveryAttempt.id == attempt.id)
                .values(status="dead", last_error="subscriber inactive or deleted")
            )
            await session.commit()
        return

    payload = ik.enriched_payload or ik.raw_payload
    result = await deliver(attempt.id, sub.endpoint, sub.secret, ik.event_type, payload)

    now = datetime.now(timezone.utc)
    new_attempt_count = attempt.attempt_count + 1

    if result.success:
        new_status = "delivered"
        next_attempt_at = now  # irrelevant
        last_error = None
    elif new_attempt_count >= settings.delivery_max_attempts:
        new_status = "dead"
        next_attempt_at = now
        last_error = result.error or f"HTTP {result.status_code}"
        logger.error(
            "delivery.dead",
            delivery_id=str(attempt.id),
            message_id=attempt.message_id,
            subscriber_id=str(attempt.subscriber_id),
            attempts=new_attempt_count,
        )
    else:
        new_status = "failed"
        delay = _backoff_seconds(new_attempt_count)
        next_attempt_at = now + timedelta(seconds=delay)
        last_error = result.error or f"HTTP {result.status_code}"
        logger.warning(
            "delivery.retry_scheduled",
            delivery_id=str(attempt.id),
            attempt=new_attempt_count,
            next_in_s=round(delay, 1),
        )

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(DeliveryAttempt)
            .where(DeliveryAttempt.id == attempt.id)
            .values(
                status=new_status,
                attempt_count=new_attempt_count,
                last_attempt_at=now,
                last_http_status=result.status_code,
                last_error=last_error,
                next_attempt_at=next_attempt_at,
                updated_at=now,
            )
        )
        await session.commit()


async def run() -> None:
    logger.info("delivery_worker.starting")
    await _reset_stale_in_flight()
    logger.info("delivery_worker.running", poll_interval=settings.delivery_poll_interval)

    while True:
        try:
            count = await _process_batch()
            if count == 0:
                await asyncio.sleep(settings.delivery_poll_interval)
        except Exception:
            logger.exception("delivery_worker.unhandled_error")
            await asyncio.sleep(settings.delivery_poll_interval)


if __name__ == "__main__":
    import structlog

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ]
    )
    asyncio.run(run())
