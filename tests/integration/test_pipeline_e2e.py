"""E2E integration test: publish → consume → enrich → deliver."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from unittest.mock import AsyncMock, patch

import aio_pika
import pytest
import pytest_asyncio
import respx
import httpx

from sqlalchemy import select


@pytest.mark.asyncio
async def test_full_pipeline(containers, db_session):
    """
    1. Register a subscriber
    2. Publish an event to RabbitMQ
    3. Run the consumer handle_message once
    4. Assert idempotency_key created + delivery_attempt created
    5. Run one delivery_worker batch with mocked HTTP
    6. Assert delivery status = delivered
    """
    from app.db.models import DeliveryAttempt, IdempotencyKey, Subscriber
    from app.consumer.rabbitmq import handle_message

    # 1. Register subscriber
    import secrets
    sub = Subscriber(
        name="e2e-test",
        endpoint="http://mock-receiver/webhook",
        secret=secrets.token_hex(32),
        is_active=True,
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    message_id = str(uuid.uuid4())

    # 2. Create a fake RabbitMQ message
    msg_body = json.dumps(
        {
            "message_id": message_id,
            "event_type": "user.purchase",
            "payload": {"amount": 42.0, "currency": "USD"},
        }
    ).encode()

    fake_msg = AsyncMock(spec=aio_pika.IncomingMessage)
    fake_msg.body = msg_body
    fake_msg.ack = AsyncMock()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _process(ignore_processed=True):
        yield

    fake_msg.process = _process

    # Patch the enricher to avoid actual LLM calls and sleep
    from app.enricher.base import EnrichedEvent

    async def fast_enrich(provider, event_type, payload):
        return EnrichedEvent(
            event_type=event_type,
            original_payload=payload,
            enriched_payload={**payload, "_enrichment": {"summary": "test"}},
            model="mock",
            tokens_used=10,
        )

    with patch("app.consumer.rabbitmq._enrich_with_retry", side_effect=fast_enrich):
        await handle_message(fake_msg)

    # Give asyncio a moment
    await asyncio.sleep(0.1)

    # 3. Assert idempotency_key row created
    result = await db_session.execute(
        select(IdempotencyKey).where(IdempotencyKey.message_id == message_id)
    )
    ik = result.scalar_one_or_none()
    assert ik is not None, "idempotency_key should exist"
    assert ik.event_type == "user.purchase"

    # 4. Assert delivery_attempt created
    result = await db_session.execute(
        select(DeliveryAttempt).where(DeliveryAttempt.message_id == message_id)
    )
    attempt = result.scalar_one_or_none()
    assert attempt is not None, "delivery_attempt should exist"
    assert attempt.subscriber_id == sub.id

    # 5. Simulate delivery worker with mocked HTTP
    with respx.mock:
        respx.post("http://mock-receiver/webhook").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        # Patch delivery sender to use our mock
        import app.delivery.sender as sender_module
        original_client = sender_module._CLIENT
        sender_module._CLIENT = httpx.AsyncClient(
            transport=respx.MockTransport(respx.current()),
            timeout=5.0,
        )

        try:
            from app.delivery.worker import _deliver_attempt
            # Reload attempt with relationships
            result = await db_session.execute(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.message_id == message_id)
            )
            attempt = result.scalar_one()
            # manually set in_flight so worker picks it up
            attempt.status = "in_flight"
            await db_session.commit()

            # Load relationships
            from sqlalchemy.orm import selectinload
            result = await db_session.execute(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.message_id == message_id)
                .options(
                    selectinload(DeliveryAttempt.idempotency_key),
                    selectinload(DeliveryAttempt.subscriber),
                )
            )
            attempt_loaded = result.scalar_one()
            await _deliver_attempt(attempt_loaded)
        finally:
            sender_module._CLIENT = original_client

    # 6. Assert delivered
    await db_session.refresh(attempt)
    result = await db_session.execute(
        select(DeliveryAttempt).where(DeliveryAttempt.message_id == message_id)
    )
    final_attempt = result.scalar_one()
    assert final_attempt.status == "delivered", f"Expected delivered, got {final_attempt.status}"
    assert final_attempt.last_http_status == 200


@pytest.mark.asyncio
async def test_duplicate_message_skipped(containers, db_session):
    """Publishing the same message_id twice → second is a no-op."""
    from app.db.models import IdempotencyKey
    from app.consumer.rabbitmq import handle_message
    from app.enricher.base import EnrichedEvent

    message_id = str(uuid.uuid4())

    msg_body = json.dumps(
        {"message_id": message_id, "event_type": "test.dedup", "payload": {}}
    ).encode()

    async def _fast_enrich(provider, event_type, payload):
        return EnrichedEvent(
            event_type=event_type,
            original_payload=payload,
            enriched_payload={**payload, "_enrichment": {}},
            model="mock",
            tokens_used=0,
        )

    def make_fake_msg():
        fake_msg = AsyncMock(spec=aio_pika.IncomingMessage)
        fake_msg.body = msg_body
        fake_msg.ack = AsyncMock()
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _process(ignore_processed=True):
            yield

        fake_msg.process = _process
        return fake_msg

    with patch("app.consumer.rabbitmq._enrich_with_retry", side_effect=_fast_enrich):
        await handle_message(make_fake_msg())
        await handle_message(make_fake_msg())

    # Only one idempotency_key should exist
    result = await db_session.execute(
        select(IdempotencyKey).where(IdempotencyKey.message_id == message_id)
    )
    keys = result.scalars().all()
    assert len(keys) == 1, f"Expected 1 idempotency key, got {len(keys)}"
