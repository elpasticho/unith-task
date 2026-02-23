"""Mock LLM enrichment provider with realistic latency and error simulation."""
from __future__ import annotations

import asyncio
import math
import random
from typing import Any, Dict

import structlog

from app.enricher.base import (
    EnrichedEvent,
    EnrichmentProvider,
    FatalEnrichmentError,
    TransientEnrichmentError,
)

logger = structlog.get_logger(__name__)

# Log-normal latency params (mimics real LLM response times)
_LATENCY_MU = math.log(0.8)   # ~0.8 s median
_LATENCY_SIGMA = 0.4

# Error rates
_TRANSIENT_RATE = 0.08   # 8%
_FATAL_RATE = 0.02       # 2%


class MockLLMProvider(EnrichmentProvider):
    """Simulates LLM enrichment with realistic latency and error injection."""

    async def enrich(self, event_type: str, payload: Dict[str, Any]) -> EnrichedEvent:
        # Simulate network / model latency
        latency = random.lognormvariate(_LATENCY_MU, _LATENCY_SIGMA)
        await asyncio.sleep(min(latency, 5.0))  # cap at 5 s in tests

        roll = random.random()
        if roll < _FATAL_RATE:
            raise FatalEnrichmentError(
                f"Mock fatal error for event_type={event_type!r} (roll={roll:.4f})"
            )
        if roll < _FATAL_RATE + _TRANSIENT_RATE:
            raise TransientEnrichmentError(
                f"Mock transient error for event_type={event_type!r} (roll={roll:.4f})"
            )

        enriched = {
            "summary": f"Mock enrichment of '{event_type}' event",
            "sentiment": random.choice(["positive", "neutral", "negative"]),
            "confidence": round(random.uniform(0.7, 1.0), 3),
            "tags": [event_type, "mock", "llm"],
            "original_field_count": len(payload),
        }

        logger.debug("mock_llm.enriched", event_type=event_type, latency_s=round(latency, 3))
        return EnrichedEvent(
            event_type=event_type,
            original_payload=payload,
            enriched_payload={**payload, "_enrichment": enriched},
            model="mock-gpt",
            tokens_used=random.randint(50, 300),
        )
