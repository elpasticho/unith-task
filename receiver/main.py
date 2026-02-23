"""Test webhook receiver — logs and stores all incoming deliveries."""
from __future__ import annotations

import hashlib
import hmac
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import structlog
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)

app = FastAPI(title="Webhook Test Receiver", version="1.0.0")

# In-memory store (last 1000 deliveries)
_received: Deque[Dict[str, Any]] = deque(maxlen=1000)


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    x_webhook_signature: Optional[str] = Header(None),
    x_webhook_timestamp: Optional[str] = Header(None),
    x_webhook_id: Optional[str] = Header(None),
) -> JSONResponse:
    body = await request.body()

    try:
        import json
        data = json.loads(body)
    except Exception:
        data = {}

    entry = {
        "webhook_id": x_webhook_id,
        "timestamp_ms": x_webhook_timestamp,
        "signature": x_webhook_signature,
        "body": data,
        "raw_body": body.decode("utf-8", errors="replace"),  # preserved for HMAC verification
        "received_at": int(time.time() * 1000),
    }
    _received.append(entry)

    logger.info(
        "receiver.webhook_received",
        webhook_id=x_webhook_id,
        event_type=data.get("event_type"),
        delivery_id=data.get("delivery_id"),
    )

    return JSONResponse({"status": "ok", "webhook_id": x_webhook_id})


@app.get("/received")
async def list_received(limit: int = 50) -> JSONResponse:
    items = list(_received)[-limit:]
    return JSONResponse({"count": len(items), "items": items})


@app.delete("/received")
async def clear_received() -> JSONResponse:
    _received.clear()
    return JSONResponse({"status": "cleared"})


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "received_count": len(_received)})
