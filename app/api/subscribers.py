"""Subscriber CRUD endpoints."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Subscriber
from app.db.session import get_db
from app.schemas.subscriber import (
    SubscriberCreate,
    SubscriberCreatedResponse,
    SubscriberResponse,
    SubscriberUpdate,
)

router = APIRouter(prefix="/subscribers", tags=["subscribers"])


@router.post("", response_model=SubscriberCreatedResponse, status_code=201)
async def create_subscriber(
    body: SubscriberCreate, db: AsyncSession = Depends(get_db)
) -> SubscriberCreatedResponse:
    secret = secrets.token_hex(settings.webhook_hmac_secret_length)
    sub = Subscriber(name=body.name, endpoint=body.endpoint, secret=secret)
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return SubscriberCreatedResponse.model_validate(sub)


@router.get("", response_model=List[SubscriberResponse])
async def list_subscribers(db: AsyncSession = Depends(get_db)) -> List[SubscriberResponse]:
    result = await db.execute(
        select(Subscriber).where(Subscriber.deleted_at.is_(None)).order_by(Subscriber.created_at)
    )
    return [SubscriberResponse.model_validate(s) for s in result.scalars().all()]


@router.get("/{subscriber_id}", response_model=SubscriberResponse)
async def get_subscriber(
    subscriber_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> SubscriberResponse:
    sub = await _get_or_404(db, subscriber_id)
    return SubscriberResponse.model_validate(sub)


@router.patch("/{subscriber_id}", response_model=SubscriberResponse)
async def update_subscriber(
    subscriber_id: uuid.UUID, body: SubscriberUpdate, db: AsyncSession = Depends(get_db)
) -> SubscriberResponse:
    sub = await _get_or_404(db, subscriber_id)
    if body.endpoint is not None:
        sub.endpoint = body.endpoint
    if body.is_active is not None:
        sub.is_active = body.is_active
    sub.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(sub)
    return SubscriberResponse.model_validate(sub)


@router.delete("/{subscriber_id}", status_code=204)
async def delete_subscriber(
    subscriber_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> None:
    sub = await _get_or_404(db, subscriber_id)
    sub.deleted_at = datetime.now(timezone.utc)
    sub.is_active = False
    await db.commit()


async def _get_or_404(db: AsyncSession, subscriber_id: uuid.UUID) -> Subscriber:
    result = await db.execute(
        select(Subscriber).where(
            Subscriber.id == subscriber_id,
            Subscriber.deleted_at.is_(None),
        )
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    return sub
