"""Enrichment provider factory."""
from __future__ import annotations

from app.config import settings
from app.enricher.base import EnrichmentProvider


def get_provider() -> EnrichmentProvider:
    if settings.enrichment_provider == "openai":
        from app.enricher.openai_llm import OpenAILLMProvider
        return OpenAILLMProvider()
    from app.enricher.mock_llm import MockLLMProvider
    return MockLLMProvider()
