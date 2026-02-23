# app/delivery/

Webhook delivery worker, HTTP sender, and HMAC signing. Runs as a standalone process (`python -m app.delivery.worker`).

## Files

### `worker.py`

Polls `delivery_attempts` continuously and sends webhooks to subscriber endpoints.

**Delivery loop:**
```
1. SELECT ... WHERE status IN ('pending','failed') AND next_attempt_at <= now
   FOR UPDATE SKIP LOCKED LIMIT 50
2. SET status='in_flight' → COMMIT  (lock released here)
3. For each attempt: call _deliver_attempt() outside the transaction
```

**`_deliver_attempt()` outcome logic:**

| Result | New status | Next action |
|--------|-----------|-------------|
| HTTP 2xx | `delivered` | Done |
| Non-2xx / timeout, attempts < max | `failed` | Schedule retry with backoff |
| Non-2xx / timeout, attempts == max | `dead` | Log structured error; operator retries manually |
| Subscriber inactive/deleted | `dead` | Skip immediately |

**Backoff:** full-jitter exponential — `delay = random(0, min(base × 2^attempt, max_delay))`. Default: base=1 s, cap=300 s.

**Startup recovery:** resets any `in_flight` rows older than 10 minutes to `failed`, so deliveries are not stuck if the worker crashed mid-attempt.

**`SELECT FOR UPDATE SKIP LOCKED`:** multiple worker replicas coordinate without deadlock — each instance claims a disjoint set of rows. The lock is released before the HTTP call, so a slow subscriber does not block other deliveries.

---

### `sender.py`

Thin async HTTP client wrapping httpx. The `AsyncClient` is a module-level singleton (`_CLIENT`) to reuse connections across deliveries.

```python
async def deliver(delivery_id, endpoint, secret, event_type, payload) -> DeliveryResult
```

Signs the request body with `signing.sign()` and sets the three webhook headers before sending.

---

### `signing.py`

HMAC-SHA256 webhook authentication (Stripe model).

```python
def sign(secret, body, timestamp_ms=None) -> (signature_header, timestamp_ms)
def verify(secret, body, signature_header, timestamp_ms, tolerance_seconds=300) -> bool
```

**Canonical message:** `f"{timestamp_ms}.{body_bytes}"`

**Headers set on every delivery:**
- `X-Webhook-Signature: sha256=<hex>` — HMAC of the canonical message
- `X-Webhook-Timestamp: <unix_ms>` — replay attack prevention (subscriber rejects requests outside the 5-min window)
- `X-Webhook-ID: <delivery_attempt_id>` — subscriber-side idempotency key

Signature comparison uses `hmac.compare_digest` (constant-time, no timing oracle).
