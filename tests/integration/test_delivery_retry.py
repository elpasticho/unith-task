"""Integration test: delivery retry logic — 503 twice then 200."""
from __future__ import annotations

import asyncio
import json
import secrets
import uuid

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import DeliveryAttempt, IdempotencyKey, Subscriber
from app.delivery.worker import _deliver_attempt, _backoff_seconds


@pytest.mark.asyncio
async def test_retry_503_twice_then_200(containers, db_session):
    """
    Delivery worker should:
    1. First attempt → 503 → status=failed, attempt_count=1
    2. Second attempt → 503 → status=failed, attempt_count=2
    3. Third attempt → 200 → status=delivered, attempt_count=3
    """
    import app.delivery.sender as sender_module

    # Setup subscriber + event
    sub = Subscriber(
        name="retry-test",
        endpoint="http://retry-mock/webhook",
        secret=secrets.token_hex(32),
        is_active=True,
    )
    db_session.add(sub)

    ik = IdempotencyKey(
        message_id=f"retry-{uuid.uuid4()}",
        event_type="test.retry",
        raw_payload={"x": 1},
        enriched_payload={"x": 1, "_enrichment": {}},
        status="enriched",
    )
    db_session.add(ik)
    await db_session.commit()
    await db_session.refresh(sub)
    await db_session.refresh(ik)

    attempt = DeliveryAttempt(
        message_id=ik.message_id,
        subscriber_id=sub.id,
        status="in_flight",
        attempt_count=0,
    )
    db_session.add(attempt)
    await db_session.commit()
    await db_session.refresh(attempt)

    async def _load_attempt():
        result = await db_session.execute(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.id == attempt.id)
            .options(
                selectinload(DeliveryAttempt.idempotency_key),
                selectinload(DeliveryAttempt.subscriber),
            )
        )
        return result.scalar_one()

    # Attempt 1: 503
    with respx.mock(assert_all_called=False):
        respx.post("http://retry-mock/webhook").mock(
            return_value=httpx.Response(503)
        )
        loaded = await _load_attempt()
        original_client = sender_module._CLIENT
        sender_module._CLIENT = httpx.AsyncClient(
            transport=respx.MockTransport(respx.current()),
            timeout=5.0,
        )
        try:
            await _deliver_attempt(loaded)
        finally:
            sender_module._CLIENT = original_client

    await db_session.refresh(attempt)
    result = await db_session.execute(
        select(DeliveryAttempt).where(DeliveryAttempt.id == attempt.id)
    )
    a1 = result.scalar_one()
    assert a1.status == "failed"
    assert a1.attempt_count == 1
    assert a1.last_http_status == 503

    # Attempt 2: 503 again
    a1.status = "in_flight"
    await db_session.commit()

    with respx.mock(assert_all_called=False):
        respx.post("http://retry-mock/webhook").mock(
            return_value=httpx.Response(503)
        )
        loaded = await _load_attempt()
        sender_module._CLIENT = httpx.AsyncClient(
            transport=respx.MockTransport(respx.current()),
            timeout=5.0,
        )
        try:
            await _deliver_attempt(loaded)
        finally:
            sender_module._CLIENT = original_client

    result = await db_session.execute(
        select(DeliveryAttempt).where(DeliveryAttempt.id == attempt.id)
    )
    a2 = result.scalar_one()
    assert a2.status == "failed"
    assert a2.attempt_count == 2

    # Attempt 3: 200 success
    a2.status = "in_flight"
    await db_session.commit()

    with respx.mock(assert_all_called=False):
        respx.post("http://retry-mock/webhook").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        loaded = await _load_attempt()
        sender_module._CLIENT = httpx.AsyncClient(
            transport=respx.MockTransport(respx.current()),
            timeout=5.0,
        )
        try:
            await _deliver_attempt(loaded)
        finally:
            sender_module._CLIENT = original_client

    result = await db_session.execute(
        select(DeliveryAttempt).where(DeliveryAttempt.id == attempt.id)
    )
    a3 = result.scalar_one()
    assert a3.status == "delivered"
    assert a3.attempt_count == 3
    assert a3.last_http_status == 200


@pytest.mark.asyncio
async def test_dead_after_max_attempts(containers, db_session):
    """After max_attempts consecutive failures, status becomes dead."""
    from app.config import settings
    import app.delivery.sender as sender_module

    sub = Subscriber(
        name="dead-test",
        endpoint="http://dead-mock/webhook",
        secret=secrets.token_hex(32),
        is_active=True,
    )
    db_session.add(sub)

    ik = IdempotencyKey(
        message_id=f"dead-{uuid.uuid4()}",
        event_type="test.dead",
        raw_payload={},
        enriched_payload={"_enrichment": {}},
        status="enriched",
    )
    db_session.add(ik)
    await db_session.commit()
    await db_session.refresh(sub)
    await db_session.refresh(ik)

    # Start just below max_attempts
    attempt = DeliveryAttempt(
        message_id=ik.message_id,
        subscriber_id=sub.id,
        status="in_flight",
        attempt_count=settings.delivery_max_attempts - 1,
    )
    db_session.add(attempt)
    await db_session.commit()
    await db_session.refresh(attempt)

    with respx.mock(assert_all_called=False):
        respx.post("http://dead-mock/webhook").mock(
            return_value=httpx.Response(500)
        )
        result = await db_session.execute(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.id == attempt.id)
            .options(
                selectinload(DeliveryAttempt.idempotency_key),
                selectinload(DeliveryAttempt.subscriber),
            )
        )
        loaded = result.scalar_one()
        original_client = sender_module._CLIENT
        sender_module._CLIENT = httpx.AsyncClient(
            transport=respx.MockTransport(respx.current()),
            timeout=5.0,
        )
        try:
            await _deliver_attempt(loaded)
        finally:
            sender_module._CLIENT = original_client

    result = await db_session.execute(
        select(DeliveryAttempt).where(DeliveryAttempt.id == attempt.id)
    )
    final = result.scalar_one()
    assert final.status == "dead"
    assert final.attempt_count == settings.delivery_max_attempts
