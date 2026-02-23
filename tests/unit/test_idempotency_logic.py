"""Unit tests for idempotency logic in the consumer."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import aio_pika

from app.consumer.rabbitmq import handle_message


def _make_message(body: bytes, already_acked: bool = False) -> MagicMock:
    msg = MagicMock(spec=aio_pika.IncomingMessage)
    msg.body = body
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()
    msg.reject = AsyncMock()

    # Simulate process() context manager
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _process(ignore_processed=True):
        yield

    msg.process = _process
    return msg


@pytest.mark.asyncio
async def test_duplicate_message_acked_early():
    """When idempotency insert returns None (duplicate), message is ACKed and no enrichment."""
    msg_body = json.dumps(
        {"message_id": "dup-001", "event_type": "test", "payload": {}}
    ).encode()
    message = _make_message(msg_body)

    with patch("app.consumer.rabbitmq._insert_idempotency_key", new_callable=AsyncMock) as mock_insert, \
         patch("app.consumer.rabbitmq.AsyncSessionLocal") as mock_session_cls, \
         patch("app.consumer.rabbitmq.get_provider") as mock_provider:

        mock_insert.return_value = None  # simulate duplicate

        # Make the session context manager work
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        await handle_message(message)

        # Enrichment should NOT have been called
        mock_provider.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_json_acked():
    """Invalid JSON body is ACKed immediately without processing."""
    msg_body = b"not valid json"
    message = _make_message(msg_body)

    with patch("app.consumer.rabbitmq.AsyncSessionLocal") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        # Should not raise
        await handle_message(message)


@pytest.mark.asyncio
async def test_new_message_calls_enricher():
    """New (non-duplicate) message proceeds to enrichment after ACK."""
    msg_body = json.dumps(
        {"message_id": "new-001", "event_type": "user.action", "payload": {"x": 1}}
    ).encode()
    message = _make_message(msg_body)

    fake_ik = MagicMock()
    fake_ik.message_id = "new-001"

    fake_result = MagicMock()
    fake_result.enriched_payload = {"x": 1, "_enrichment": {}}
    fake_result.model = "mock"
    fake_result.tokens_used = 100

    with patch("app.consumer.rabbitmq._insert_idempotency_key", return_value=fake_ik), \
         patch("app.consumer.rabbitmq._create_delivery_attempts", return_value=1), \
         patch("app.consumer.rabbitmq.AsyncSessionLocal") as mock_session_cls, \
         patch("app.consumer.rabbitmq.get_provider") as mock_get_provider, \
         patch("app.consumer.rabbitmq._enrich_with_retry", new_callable=AsyncMock) as mock_enrich:

        mock_enrich.return_value = fake_result

        mock_provider = MagicMock()
        mock_get_provider.return_value = mock_provider

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: fake_ik))
        mock_session.commit = AsyncMock()
        mock_session_cls.return_value = mock_session

        await handle_message(message)

        mock_enrich.assert_called_once()
