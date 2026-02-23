"""E2E integration test: publish → consume → enrich → deliver."""
from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from unittest.mock import AsyncMock, patch

import aio_pika
import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import selectinload


@pytest.mark.asyncio
async def test_full_pipeline(containers, db_session):
    """
    1. Register a subscriber
    2. Publish an event to RabbitMQ
    3. Run handle_message (real enrichment, sleep patched for speed)
    4. Assert idempotency_key created + enriched
    5. Deliver via worker with mocked HTTP endpoint
    6. Assert delivery status = delivered
    """
    from app.db.models import DeliveryAttempt, IdempotencyKey, Subscriber
    from app.consumer.rabbitmq import handle_message

    # 1. Register subscriber
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
    msg_body = json.dumps(
        {
            "message_id": message_id,
            "event_type": "user.purchase",
            "payload": {"amount": 42.0, "currency": "USD"},
        }
    ).encode()

    # Build a fake aio-pika message
    fake_msg = AsyncMock(spec=aio_pika.IncomingMessage)
    fake_msg.body = msg_body
    fake_msg.ack = AsyncMock()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _process(ignore_processed=True):
        yield

    fake_msg.process = _process

    # 2. Run handle_message with real enrichment code path.
    #    Patch asyncio.sleep so the mock LLM latency doesn't slow the test,
    #    and pin random() so we always get a success (no error injection).
    import random as _random
    with patch("asyncio.sleep", new=AsyncMock()), \
         patch.object(_random, "random", return_value=0.5):
        await handle_message(fake_msg)

    await asyncio.sleep(0.05)

    # 3. Assert idempotency_key row exists and was enriched
    result = await db_session.execute(
        select(IdempotencyKey).where(IdempotencyKey.message_id == message_id)
    )
    ik = result.scalar_one_or_none()
    assert ik is not None, "idempotency_key should exist"
    assert ik.event_type == "user.purchase"
    assert ik.enriched_payload is not None, "payload should be enriched"
    assert "_enrichment" in ik.enriched_payload
    assert ik.status == "enriched"

    # 4. Assert delivery_attempt was created
    result = await db_session.execute(
        select(DeliveryAttempt).where(DeliveryAttempt.message_id == message_id)
    )
    attempt = result.scalar_one_or_none()
    assert attempt is not None, "delivery_attempt should exist"
    assert attempt.subscriber_id == sub.id

    # 5. Deliver via worker with mocked HTTP
    import app.delivery.sender as sender_module

    with respx.mock(assert_all_called=False) as rmock:
        rmock.post("http://mock-receiver/webhook").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        sender_module._CLIENT = httpx.AsyncClient(
            transport=respx.MockTransport(rmock),
            timeout=5.0,
        )
        try:
            attempt.status = "in_flight"
            await db_session.commit()

            result = await db_session.execute(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.message_id == message_id)
                .options(
                    selectinload(DeliveryAttempt.idempotency_key),
                    selectinload(DeliveryAttempt.subscriber),
                )
            )
            loaded = result.scalar_one()

            from app.delivery.worker import _deliver_attempt
            await _deliver_attempt(loaded)
        finally:
            await sender_module._CLIENT.aclose()
            sender_module._CLIENT = None

    # 6. Assert delivered
    result = await db_session.execute(
        select(DeliveryAttempt).where(DeliveryAttempt.message_id == message_id)
    )
    final = result.scalar_one()
    assert final.status == "delivered", f"Expected delivered, got {final.status}"
    assert final.last_http_status == 200


@pytest.mark.asyncio
async def test_duplicate_message_skipped(containers, db_session):
    """Publishing the same message_id twice → second is a no-op."""
    from app.db.models import IdempotencyKey
    from app.consumer.rabbitmq import handle_message

    message_id = str(uuid.uuid4())
    msg_body = json.dumps(
        {"message_id": message_id, "event_type": "test.dedup", "payload": {}}
    ).encode()

    import random as _random

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

    with patch("asyncio.sleep", new=AsyncMock()), \
         patch.object(_random, "random", return_value=0.5):
        await handle_message(make_fake_msg())
        await handle_message(make_fake_msg())

    result = await db_session.execute(
        select(IdempotencyKey).where(IdempotencyKey.message_id == message_id)
    )
    keys = result.scalars().all()
    assert len(keys) == 1, f"Expected 1 idempotency key, got {len(keys)}"


@pytest.mark.asyncio
async def test_reconciler_re_enqueues_stale_received(containers, db_session):
    """Idempotency keys stuck in 'received' for > stale_minutes should be re-enqueued."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.db.models import IdempotencyKey
    from app.consumer.rabbitmq import _reconciler

    # Insert a stale 'received' key
    stale_id = f"stale-{uuid.uuid4()}"
    ik = IdempotencyKey(
        message_id=stale_id,
        event_type="test.stale",
        raw_payload={"x": 1},
        status="received",
        received_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db_session.add(ik)
    await db_session.commit()

    # Mock exchange.publish and make reconciler run one cycle then stop
    published: list[str] = []

    async def fake_publish(msg, routing_key):
        body = json.loads(msg.body)
        published.append(body["message_id"])

    mock_exchange = MagicMock()
    mock_exchange.publish = fake_publish

    # Patch sleep so the first sleep completes instantly, second call raises to break the loop
    call_count = 0

    async def controlled_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=controlled_sleep):
        try:
            await _reconciler(None, mock_exchange)
        except asyncio.CancelledError:
            pass

    assert stale_id in published, f"Stale key {stale_id} was not re-enqueued"
