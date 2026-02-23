"""Abstract base for enrichment providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class EnrichedEvent:
    event_type: str
    original_payload: Dict[str, Any]
    enriched_payload: Dict[str, Any]
    model: str
    tokens_used: int


class EnrichmentProvider(ABC):
    @abstractmethod
    async def enrich(self, event_type: str, payload: Dict[str, Any]) -> EnrichedEvent:
        """Enrich an event payload with LLM-generated metadata.

        Raises:
            TransientEnrichmentError: Retry-able failure (network, rate limit, …)
            FatalEnrichmentError: Non-retry-able failure (invalid payload, …)
        """


class TransientEnrichmentError(Exception):
    """Transient failure — consumer should retry."""


class FatalEnrichmentError(Exception):
    """Fatal failure — should not retry; log and move on."""
