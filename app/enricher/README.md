# app/enricher/

LLM enrichment provider abstraction. Follows the Strategy pattern — swap providers via env var, no code changes.

## Files

### `base.py`
Defines the contract:

```python
class EnrichmentProvider(ABC):
    async def enrich(self, event_type: str, payload: dict) -> EnrichedEvent: ...

class TransientEnrichmentError(Exception): ...  # retry-able (network, rate limit)
class FatalEnrichmentError(Exception):     ...  # not retry-able (invalid payload)
```

`EnrichedEvent` is a dataclass with: `event_type`, `original_payload`, `enriched_payload`, `model`, `tokens_used`.

### `mock_llm.py` — `MockLLMProvider`
Used by default (`ENRICHMENT_PROVIDER=mock`). Simulates realistic LLM behavior:
- **Latency**: sampled from a log-normal distribution (μ=log(0.8), σ=0.4) — median ~0.8 s
- **Transient errors**: 8% of calls raise `TransientEnrichmentError`
- **Fatal errors**: 2% of calls raise `FatalEnrichmentError`
- **Success**: adds `_enrichment` key with `summary`, `sentiment`, `confidence`, `tags`

### `openai_llm.py` — `OpenAILLMProvider`
Calls the OpenAI Chat Completions API with a JSON-mode prompt. Enabled via:
```bash
ENRICHMENT_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini   # optional, this is the default
```
Requires the `openai` Python package (not installed by default — add to `pyproject.toml` if needed).

### `__init__.py` — provider factory
```python
def get_provider() -> EnrichmentProvider
```
Returns the correct provider based on `ENRICHMENT_PROVIDER`. The result is cached as a module-level singleton (`_provider`) in `consumer/rabbitmq.py` and reused for every message. The singleton is reset to `None` on `FatalEnrichmentError` so a rotated API key or stale client is recovered automatically on the next message.

## Adding a new provider

1. Create `app/enricher/my_provider.py` implementing `EnrichmentProvider`
2. Add a branch in `__init__.py:get_provider()`
3. Set `ENRICHMENT_PROVIDER=my_provider`
