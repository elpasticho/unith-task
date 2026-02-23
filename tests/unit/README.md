# tests/unit/

Unit tests. No database, no RabbitMQ, no network. All external calls are mocked or patched. Run in under 1 second.

## Files

### `test_signing.py`
Tests `app/delivery/signing.py`.

| Test | Protects against |
|------|-----------------|
| `test_valid_signature_passes` | Correctly signed request is accepted |
| `test_tampered_body_fails` | Payload modification invalidates the signature |
| `test_tampered_signature_fails` | Signature manipulation is detected |
| `test_wrong_secret_fails` | Attacker without the secret cannot forge a valid signature |
| `test_stale_timestamp_rejected` | Replayed requests older than the tolerance window are rejected |
| `test_within_tolerance_accepted` | Requests within the window are accepted |
| `test_signature_header_format` | Header is correctly prefixed with `sha256=` and is 71 chars total |
| `test_deterministic_for_same_inputs` | Same inputs always produce the same signature |
| `test_constant_time_comparison_used` | `verify()` returns `False` on bad input, does not raise |

### `test_backoff.py`
Tests `_backoff_seconds()` in `app/delivery/worker.py`.

| Test | Protects against |
|------|-----------------|
| `test_first_attempt_within_bounds` | Delay never exceeds the cap for attempt 1 |
| `test_delays_increase_with_attempts` | Backoff grows with attempt count (median check over 500 samples) |
| `test_capped_at_max_delay` | No delay ever exceeds `DELIVERY_MAX_DELAY_SECONDS` |
| `test_jitter_varies` | Full-jitter produces distinct values (not constant) |
| `test_non_negative` | Delay is never negative |

### `test_enricher.py`
Tests `app/enricher/mock_llm.py`. `asyncio.sleep` is patched to a no-op so tests run instantly.

| Test | Protects against |
|------|-----------------|
| `test_enriched_event_fields_populated` | `EnrichedEvent` has all required fields on success |
| `test_error_rates_at_correct_proportions` | Fatal ≈ 2%, transient ≈ 8% over N=1000 calls (±3% tolerance) |
| `test_fatal_error_raised` | `FatalEnrichmentError` raised when roll < fatal threshold |
| `test_transient_error_raised` | `TransientEnrichmentError` raised when roll in transient band |

### `test_idempotency_logic.py`
Tests `handle_message()` in `app/consumer/rabbitmq.py` with a mocked DB.

| Test | Protects against |
|------|-----------------|
| `test_duplicate_message_acked_early` | When idempotency insert returns `None`, enricher is never called |
| `test_invalid_json_acked` | Malformed message body is ACKed and discarded without crashing |
| `test_new_message_calls_enricher` | New message proceeds through the full enrichment path |
