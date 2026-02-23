from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Subscriber(Base):
    __tablename__ = "subscribers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    endpoint = Column(Text, nullable=False)
    secret = Column(String(64), nullable=False)  # stored plain, shown once
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    delivery_attempts = relationship("DeliveryAttempt", back_populates="subscriber")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    message_id = Column(String(255), primary_key=True)
    event_type = Column(String(255), nullable=False)
    raw_payload = Column(JSONB, nullable=False)
    enriched_payload = Column(JSONB, nullable=True)
    status = Column(String(50), nullable=False, default="received")
    # status: received | enriched | dispatched
    received_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    enriched_at = Column(DateTime(timezone=True), nullable=True)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)

    delivery_attempts = relationship("DeliveryAttempt", back_populates="idempotency_key")


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(String(255), ForeignKey("idempotency_keys.message_id"), nullable=False)
    subscriber_id = Column(UUID(as_uuid=True), ForeignKey("subscribers.id"), nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    # status: pending | in_flight | delivered | failed | dead
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_http_status = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    idempotency_key = relationship("IdempotencyKey", back_populates="delivery_attempts")
    subscriber = relationship("Subscriber", back_populates="delivery_attempts")

    __table_args__ = (
        UniqueConstraint("message_id", "subscriber_id", name="uq_delivery_message_subscriber"),
        Index(
            "ix_delivery_attempts_pending",
            "next_attempt_at",
            postgresql_where="status IN ('pending', 'failed')",
        ),
        Index("ix_delivery_attempts_message_id", "message_id"),
        Index("ix_delivery_attempts_subscriber_id", "subscriber_id"),
        Index("ix_delivery_attempts_status", "status"),
    )
