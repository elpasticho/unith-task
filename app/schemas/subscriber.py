from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, HttpUrl


class SubscriberCreate(BaseModel):
    name: str
    endpoint: str


class SubscriberUpdate(BaseModel):
    endpoint: Optional[str] = None
    is_active: Optional[bool] = None


class SubscriberResponse(BaseModel):
    id: uuid.UUID
    name: str
    endpoint: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class SubscriberCreatedResponse(SubscriberResponse):
    """Returned once at registration — includes the webhook secret."""
    secret: str
