from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DeliveryAttemptResponse(BaseModel):
    id: uuid.UUID
    message_id: str
    subscriber_id: uuid.UUID
    status: str
    attempt_count: int
    next_attempt_at: datetime
    last_attempt_at: Optional[datetime]
    last_http_status: Optional[int]
    last_error: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class PipelineStats(BaseModel):
    # C5: queue_depth is None when the RabbitMQ management API is unreachable
    queue_depth: Optional[int] = None
    queue_depth_available: bool = True
    queue_depth_error: Optional[str] = None
    deliveries_by_status: dict[str, int]
    idempotency_keys_by_status: dict[str, int]
