"""OpenAI enrichment provider stub.

Select with ENRICHMENT_PROVIDER=openai and set OPENAI_API_KEY.
"""
from __future__ import annotations

from typing import Any, Dict

import structlog

from app.enricher.base import EnrichedEvent, EnrichmentProvider, TransientEnrichmentError

logger = structlog.get_logger(__name__)


class OpenAILLMProvider(EnrichmentProvider):
    """Calls the OpenAI Chat Completions API to enrich events."""

    def __init__(self) -> None:
        from app.config import settings

        try:
            from openai import AsyncOpenAI  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("Install 'openai' to use the OpenAI enrichment provider.") from exc

        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for the OpenAI enrichment provider.")

        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

    async def enrich(self, event_type: str, payload: Dict[str, Any]) -> EnrichedEvent:
        import json

        prompt = (
            f"You are an event enrichment assistant.\n"
            f"Event type: {event_type}\n"
            f"Payload: {json.dumps(payload, indent=2)}\n\n"
            "Return a JSON object with: summary, sentiment (positive/neutral/negative), "
            "confidence (0-1), tags (list of strings)."
        )
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.3,
            )
        except Exception as exc:
            raise TransientEnrichmentError(f"OpenAI API error: {exc}") from exc

        message = response.choices[0].message.content or "{}"
        enriched_data = json.loads(message)
        tokens = response.usage.total_tokens if response.usage else 0

        logger.info("openai_llm.enriched", event_type=event_type, tokens=tokens)
        return EnrichedEvent(
            event_type=event_type,
            original_payload=payload,
            enriched_payload={**payload, "_enrichment": enriched_data},
            model=self._model,
            tokens_used=tokens,
        )
