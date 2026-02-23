"""HTTP delivery client with HMAC signing."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Dict

import httpx
import structlog

from app.delivery.signing import sign

logger = structlog.get_logger(__name__)

_CLIENT: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _CLIENT
    if _CLIENT is None or _CLIENT.is_closed:
        _CLIENT = httpx.AsyncClient(timeout=10.0, follow_redirects=False)
    return _CLIENT


@dataclass
class DeliveryResult:
    success: bool
    status_code: int | None
    error: str | None


async def deliver(
    delivery_id: uuid.UUID,
    endpoint: str,
    secret: str,
    event_type: str,
    payload: Dict[str, Any],
) -> DeliveryResult:
    """POST the enriched payload to the subscriber endpoint with HMAC auth."""
    body_data = {
        "delivery_id": str(delivery_id),
        "event_type": event_type,
        "payload": payload,
    }
    body_bytes = json.dumps(body_data, separators=(",", ":")).encode()

    signature, timestamp_ms = sign(secret, body_bytes)

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Webhook-Timestamp": str(timestamp_ms),
        "X-Webhook-ID": str(delivery_id),
    }

    client = _get_client()
    try:
        resp = await client.post(endpoint, content=body_bytes, headers=headers)
        success = 200 <= resp.status_code < 300
        log = logger.info if success else logger.warning
        log(
            "delivery.sent",
            delivery_id=str(delivery_id),
            endpoint=endpoint,
            status_code=resp.status_code,
            success=success,
        )
        return DeliveryResult(success=success, status_code=resp.status_code, error=None)
    except httpx.TimeoutException as exc:
        logger.warning("delivery.timeout", delivery_id=str(delivery_id), endpoint=endpoint, error=str(exc))
        return DeliveryResult(success=False, status_code=None, error=f"timeout: {exc}")
    except httpx.RequestError as exc:
        logger.warning("delivery.request_error", delivery_id=str(delivery_id), endpoint=endpoint, error=str(exc))
        return DeliveryResult(success=False, status_code=None, error=str(exc))
