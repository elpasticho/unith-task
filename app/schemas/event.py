from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


class EventPublishRequest(BaseModel):
    event_type: str
    payload: Dict[str, Any]
    message_id: Optional[str] = None  # caller can supply; auto-generated if omitted


class EventPublishResponse(BaseModel):
    message_id: str
    status: str = "queued"


class EnrichedEventResponse(BaseModel):
    message_id: str
    event_type: str
    raw_payload: Dict[str, Any]
    enriched_payload: Optional[Dict[str, Any]]
    status: str
    received_at: datetime
    enriched_at: Optional[datetime]
    dispatched_at: Optional[datetime]
    error: Optional[str]

    model_config = {"from_attributes": True}
