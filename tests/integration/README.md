# tests/integration/

Integration tests that run against real Postgres and RabbitMQ instances, spun up automatically via [testcontainers](https://testcontainers-python.readthedocs.io/).

No Docker Compose required — containers are managed per test session.

## `conftest.py`

Session-scoped fixtures:

| Fixture | Scope | Description |
|---------|-------|-------------|
| `containers` | session | Starts Postgres + RabbitMQ containers, sets `DATABASE_URL` and `RABBITMQ_URL` env vars, yields connection strings, stops containers after all tests. |
| `db_engine` | session | Creates the async SQLAlchemy engine and runs `create_all()` once per session. |
| `db_session` | function | Opens a session per test; rolls back after each test so tests are isolated. |

## Files

### `test_pipeline_e2e.py`

End-to-end tests for the full consumer → delivery pipeline.

| Test | What it verifies |
|------|-----------------|
| `test_full_pipeline` | `handle_message()` runs the **real enrichment code path** (asyncio.sleep and random() are patched for speed). Asserts `idempotency_key.status='enriched'`, `_enrichment` key present in payload, `delivery_attempt` created, then delivers via worker with a mocked HTTP endpoint and asserts `status='delivered'`. |
| `test_duplicate_message_skipped` | Sending the same `message_id` twice results in exactly one `idempotency_keys` row. |
| `test_reconciler_re_enqueues_stale_received` | Inserting a `status='received'` row older than `RECONCILER_STALE_MINUTES` and running one reconciler cycle causes it to be re-published to the queue. |

### `test_delivery_retry.py`

Tests the delivery worker's retry and dead-letter logic.

| Test | What it verifies |
|------|-----------------|
| `test_retry_503_twice_then_200` | Three sequential `_deliver_attempt()` calls (503, 503, 200) produce `attempt_count=3, status='delivered'`. Verifies intermediate `failed` states and correct `last_http_status` at each step. |
| `test_dead_after_max_attempts` | Starting an attempt at `max_attempts - 1` and receiving a 500 tips it to `status='dead'` with `attempt_count == max_attempts`. |

## Shared helper

`_mock_http(pattern, responses)` — an async context manager defined in `test_delivery_retry.py` that wires a list of `httpx.Response` objects to a URL pattern via respx, installs a mock transport on the sender's client, and restores the original client on exit. Eliminates boilerplate in each test.
