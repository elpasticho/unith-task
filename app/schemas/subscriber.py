from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


def _validate_http_url(v: str) -> str:
    """Require a structurally valid http:// or https:// URL with a non-empty host."""
    if not v.startswith(("http://", "https://")):
        raise ValueError("endpoint must start with http:// or https://")
    parsed = urlparse(v)
    if not parsed.netloc:
        raise ValueError("endpoint must include a valid host")
    return v


class SubscriberCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    endpoint: str

    @field_validator("endpoint")
    @classmethod
    def endpoint_must_be_http(cls, v: str) -> str:
        return _validate_http_url(v)


class SubscriberUpdate(BaseModel):
    endpoint: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("endpoint")
    @classmethod
    def endpoint_must_be_http(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _validate_http_url(v)
        return v


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
