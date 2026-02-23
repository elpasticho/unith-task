"""Integration test: delivery retry logic — 503 twice then 200."""
from __future__ import annotations

import secrets
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import DeliveryAttempt, IdempotencyKey, Subscriber
from app.delivery.worker import _deliver_attempt, _backoff_seconds
import app.delivery.sender as sender_module


@asynccontextmanager
async def _mock_http(pattern: str, responses: list[httpx.Response]):
    """Context manager that serves responses in sequence for a given URL pattern."""
    iter_responses = iter(responses)

    with respx.mock(assert_all_called=False) as rmock:
        rmock.post(pattern).mock(side_effect=lambda _: next(iter_responses))
        client = httpx.AsyncClient(transport=respx.MockTransport(rmock), timeout=5.0)
        original = sender_module._CLIENT
        sender_module._CLIENT = client
        try:
            yield
        finally:
            await client.aclose()
            sender_module._CLIENT = original


async def _load(db_session, attempt_id) -> DeliveryAttempt:
    result = await db_session.execute(
        select(DeliveryAttempt)
        .where(DeliveryAttempt.id == attempt_id)
        .options(
            selectinload(DeliveryAttempt.idempotency_key),
            selectinload(DeliveryAttempt.subscriber),
        )
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_retry_503_twice_then_200(containers, db_session):
    """
    Attempt 1 → 503 → failed (count=1)
    Attempt 2 → 503 → failed (count=2)
    Attempt 3 → 200 → delivered (count=3)
    """
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
    attempt_id = attempt.id

    # Attempt 1: 503
    async with _mock_http("http://retry-mock/webhook", [httpx.Response(503)]):
        loaded = await _load(db_session, attempt_id)
        await _deliver_attempt(loaded)

    result = await db_session.execute(select(DeliveryAttempt).where(DeliveryAttempt.id == attempt_id))
    a1 = result.scalar_one()
    assert a1.status == "failed"
    assert a1.attempt_count == 1
    assert a1.last_http_status == 503

    # Attempt 2: 503
    a1.status = "in_flight"
    await db_session.commit()

    async with _mock_http("http://retry-mock/webhook", [httpx.Response(503)]):
        loaded = await _load(db_session, attempt_id)
        await _deliver_attempt(loaded)

    result = await db_session.execute(select(DeliveryAttempt).where(DeliveryAttempt.id == attempt_id))
    a2 = result.scalar_one()
    assert a2.status == "failed"
    assert a2.attempt_count == 2

    # Attempt 3: 200
    a2.status = "in_flight"
    await db_session.commit()

    async with _mock_http("http://retry-mock/webhook", [httpx.Response(200, json={"status": "ok"})]):
        loaded = await _load(db_session, attempt_id)
        await _deliver_attempt(loaded)

    result = await db_session.execute(select(DeliveryAttempt).where(DeliveryAttempt.id == attempt_id))
    a3 = result.scalar_one()
    assert a3.status == "delivered"
    assert a3.attempt_count == 3
    assert a3.last_http_status == 200


@pytest.mark.asyncio
async def test_dead_after_max_attempts(containers, db_session):
    """After max_attempts consecutive failures → status becomes dead."""
    from app.config import settings

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

    # Start one attempt below max so the next failure tips it to dead
    attempt = DeliveryAttempt(
        message_id=ik.message_id,
        subscriber_id=sub.id,
        status="in_flight",
        attempt_count=settings.delivery_max_attempts - 1,
    )
    db_session.add(attempt)
    await db_session.commit()
    await db_session.refresh(attempt)
    attempt_id = attempt.id

    async with _mock_http("http://dead-mock/webhook", [httpx.Response(500)]):
        loaded = await _load(db_session, attempt_id)
        await _deliver_attempt(loaded)

    result = await db_session.execute(select(DeliveryAttempt).where(DeliveryAttempt.id == attempt_id))
    final = result.scalar_one()
    assert final.status == "dead"
    assert final.attempt_count == settings.delivery_max_attempts
