"""RabbitMQ consumer — idempotency check → LLM enrichment → dispatch delivery_attempts."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import aio_pika
import structlog
from sqlalchemy import select, text
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.db.models import DeliveryAttempt, IdempotencyKey, Subscriber
from app.db.session import AsyncSessionLocal
from app.enricher import get_provider
from app.enricher.base import EnrichmentProvider, FatalEnrichmentError, TransientEnrichmentError
from app.metrics import (
    enrichment_duration,
    enrichment_errors,
    messages_duplicate,
    messages_received,
)

logger = structlog.get_logger(__name__)

# Module-level singleton — instantiated once at startup, reused for every message.
_provider: EnrichmentProvider | None = None


def _get_provider() -> EnrichmentProvider:
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider


async def _insert_idempotency_key(
    session,
    message_id: str,
    event_type: str,
    payload: dict,
) -> IdempotencyKey | None:
    """Atomic insert. Returns the new row, or None if duplicate."""
    result = await session.execute(
        text(
            """
            INSERT INTO idempotency_keys
                (message_id, event_type, raw_payload, status, received_at)
            VALUES
                (:message_id, :event_type, :payload::jsonb, 'received', NOW())
            ON CONFLICT (message_id) DO NOTHING
            RETURNING message_id
            """
        ),
        {
            "message_id": message_id,
            "event_type": event_type,
            "payload": json.dumps(payload),
        },
    )
    row = result.fetchone()
    if row is None:
        return None  # duplicate
    # Load the full ORM object
    ik_result = await session.execute(
        select(IdempotencyKey).where(IdempotencyKey.message_id == message_id)
    )
    return ik_result.scalar_one()


async def _create_delivery_attempts(session, message_id: str) -> int:
    """Create one DeliveryAttempt per active subscriber. Returns count created."""
    result = await session.execute(
        select(Subscriber).where(
            Subscriber.is_active == True,  # noqa: E712
            Subscriber.deleted_at.is_(None),
        )
    )
    subscribers = result.scalars().all()
    count = 0
    for sub in subscribers:
        attempt = DeliveryAttempt(
            message_id=message_id,
            subscriber_id=sub.id,
            status="pending",
            next_attempt_at=datetime.now(timezone.utc),
        )
        session.add(attempt)
        count += 1
    return count


@retry(
    retry=retry_if_exception_type(TransientEnrichmentError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def _enrich_with_retry(provider, event_type: str, payload: dict):
    return await provider.enrich(event_type, payload)


async def handle_message(message: aio_pika.IncomingMessage) -> None:
    async with message.process(ignore_processed=True):
        try:
            data = json.loads(message.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error("consumer.invalid_json", error=str(exc))
            await message.ack()
            return

        message_id: str = data.get("message_id") or str(uuid.uuid4())
        event_type: str = data.get("event_type", "unknown")
        payload: dict = data.get("payload", {})

        log = logger.bind(message_id=message_id, event_type=event_type)

        # Step 1: Idempotency — atomic insert
        async with AsyncSessionLocal() as session:
            ik = await _insert_idempotency_key(session, message_id, event_type, payload)
            if ik is None:
                log.info("consumer.duplicate_skipped")
                messages_duplicate.inc()
                await message.ack()
                return

            # Step 2: Create delivery attempts (so we don't lose them if we crash after ACK)
            sub_count = await _create_delivery_attempts(session, message_id)
            await session.commit()
            messages_received.inc()
            log.info("consumer.received", subscriber_count=sub_count)

        # Step 3: ACK early — idempotency row ensures safety
        await message.ack()

        # Step 4: Enrich (after ACK)
        provider = _get_provider()
        enriched_payload: dict | None = None
        try:
            t0 = time.perf_counter()
            result = await _enrich_with_retry(provider, event_type, payload)
            enrichment_duration.observe(time.perf_counter() - t0)
            enriched_payload = result.enriched_payload
            log.info(
                "consumer.enriched",
                model=result.model,
                tokens=result.tokens_used,
            )
        except TransientEnrichmentError as exc:
            enrichment_errors.labels(error_type="transient").inc()
            log.warning("consumer.transient_enrichment_failed", error=str(exc))
            # Reconciler will retry later (status stays 'received')
            return
        except FatalEnrichmentError as exc:
            enrichment_errors.labels(error_type="fatal").inc()
            log.error("consumer.fatal_enrichment_failed", error=str(exc))
            async with AsyncSessionLocal() as session:
                result2 = await session.execute(
                    select(IdempotencyKey).where(IdempotencyKey.message_id == message_id)
                )
                ik_row = result2.scalar_one_or_none()
                if ik_row:
                    ik_row.error = str(exc)
                    await session.commit()
            return

        # Step 5: Persist enrichment + update status
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            result3 = await session.execute(
                select(IdempotencyKey).where(IdempotencyKey.message_id == message_id)
            )
            ik_row = result3.scalar_one_or_none()
            if ik_row:
                ik_row.enriched_payload = enriched_payload
                ik_row.status = "enriched"
                ik_row.enriched_at = now
                await session.commit()

        log.info("consumer.processing_complete")


async def _reconciler(channel: aio_pika.Channel, exchange: aio_pika.Exchange) -> None:
    """Re-enqueue idempotency_keys stuck in 'received' status."""
    while True:
        await asyncio.sleep(settings.reconciler_interval_seconds)
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(
                minutes=settings.reconciler_stale_minutes
            )
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(IdempotencyKey).where(
                        IdempotencyKey.status == "received",
                        IdempotencyKey.received_at < cutoff,
                    )
                )
                stale = result.scalars().all()
                for ik in stale:
                    msg_body = json.dumps(
                        {
                            "message_id": ik.message_id,
                            "event_type": ik.event_type,
                            "payload": ik.raw_payload,
                        }
                    ).encode()
                    await exchange.publish(
                        aio_pika.Message(body=msg_body),
                        routing_key=settings.rabbitmq_queue,
                    )
                    logger.info("reconciler.re_enqueued", message_id=ik.message_id)
        except Exception:
            logger.exception("reconciler.error")


async def run() -> None:
    logger.info("consumer.starting")

    connection = await aio_pika.connect_robust(
        settings.rabbitmq_url,
        heartbeat=60,
    )

    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=10)

        exchange = await channel.declare_exchange(
            settings.rabbitmq_exchange,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        queue = await channel.declare_queue(settings.rabbitmq_queue, durable=True)
        await queue.bind(exchange, routing_key=settings.rabbitmq_queue)

        logger.info("consumer.ready", queue=settings.rabbitmq_queue)

        # Start reconciler background task
        asyncio.create_task(_reconciler(channel, exchange))

        await queue.consume(handle_message)
        await asyncio.Future()  # block forever


if __name__ == "__main__":
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ]
    )
    asyncio.run(run())
