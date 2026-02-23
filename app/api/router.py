"""API router aggregator."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.subscribers import router as subscribers_router
from app.api.events import router as events_router
from app.api.deliveries import router as deliveries_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(subscribers_router)
api_router.include_router(events_router)
api_router.include_router(deliveries_router)
