"""Unit tests for the mock LLM enrichment provider."""
from __future__ import annotations

import asyncio

import pytest

from app.enricher.base import EnrichedEvent, FatalEnrichmentError, TransientEnrichmentError
from app.enricher.mock_llm import MockLLMProvider, _FATAL_RATE, _TRANSIENT_RATE


async def _noop_sleep(_):
    pass


@pytest.fixture
def provider():
    return MockLLMProvider()


@pytest.mark.asyncio
async def test_enriched_event_fields_populated(provider, monkeypatch):
    """Successful enrichment returns all required fields."""
    import random
    monkeypatch.setattr(random, "random", lambda: 0.5)  # no errors
    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    result = await provider.enrich("user.action", {"key": "value"})

    assert isinstance(result, EnrichedEvent)
    assert result.event_type == "user.action"
    assert result.original_payload == {"key": "value"}
    assert "_enrichment" in result.enriched_payload
    enrichment = result.enriched_payload["_enrichment"]
    assert "summary" in enrichment
    assert "sentiment" in enrichment
    assert "confidence" in enrichment
    assert "tags" in enrichment
    assert result.model == "mock-gpt"
    assert result.tokens_used > 0


@pytest.mark.asyncio
async def test_error_rates_at_correct_proportions(monkeypatch):
    """Over 1000 runs, fatal≈2%, transient≈8%, success≈90%."""
    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    provider = MockLLMProvider()
    fatal = transient = success = 0
    N = 1000

    for _ in range(N):
        try:
            await provider.enrich("test", {})
            success += 1
        except FatalEnrichmentError:
            fatal += 1
        except TransientEnrichmentError:
            transient += 1

    # Allow ±3% tolerance
    assert abs(fatal / N - _FATAL_RATE) < 0.03, f"Fatal rate {fatal/N:.3f} != {_FATAL_RATE}"
    assert abs(transient / N - _TRANSIENT_RATE) < 0.03, f"Transient rate {transient/N:.3f} != {_TRANSIENT_RATE}"
    assert success + fatal + transient == N


@pytest.mark.asyncio
async def test_fatal_error_raised(monkeypatch):
    import random
    monkeypatch.setattr(random, "random", lambda: 0.005)  # < _FATAL_RATE
    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    provider = MockLLMProvider()
    with pytest.raises(FatalEnrichmentError):
        await provider.enrich("test", {})


@pytest.mark.asyncio
async def test_transient_error_raised(monkeypatch):
    import random
    monkeypatch.setattr(random, "random", lambda: 0.05)  # > fatal, < fatal+transient
    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    provider = MockLLMProvider()
    with pytest.raises(TransientEnrichmentError):
        await provider.enrich("test", {})
