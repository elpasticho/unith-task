from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator

# Overall request body size is enforced by _BodySizeLimitMiddleware (1 MB default).
# The validators below add a second layer of defence at the schema level.
_MAX_PAYLOAD_BYTES = 512_000  # 512 KB serialized
_MAX_EVENT_TYPE_LEN = 255


class EventPublishRequest(BaseModel):
    event_type: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_EVENT_TYPE_LEN,
        description="Dot-separated event category, e.g. 'order.placed'",
    )
    payload: Dict[str, Any] = Field(
        ...,
        description="Arbitrary event payload. Max serialized size: 512 KB.",
    )
    message_id: Optional[str] = Field(
        None,
        max_length=255,
        description="Idempotency key; auto-generated UUID if omitted.",
    )

    @field_validator("payload")
    @classmethod
    def payload_size_limit(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        size = len(json.dumps(v, separators=(",", ":")))
        if size > _MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"payload exceeds maximum serialized size of {_MAX_PAYLOAD_BYTES} bytes "
                f"(got {size} bytes)"
            )
        return v


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
