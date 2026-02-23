# app/db/

SQLAlchemy ORM models and async session management.

## Files

### `models.py`
Defines the three database tables as SQLAlchemy ORM classes:

| Model | Table | Description |
|-------|-------|-------------|
| `Subscriber` | `subscribers` | Webhook consumer registry. Stores endpoint, HMAC secret, active flag, soft-delete timestamp. |
| `IdempotencyKey` | `idempotency_keys` | One row per unique `message_id`. Tracks enrichment status and payload. PRIMARY KEY is the conflict target for deduplication. |
| `DeliveryAttempt` | `delivery_attempts` | One row per `(message_id, subscriber_id)`. Tracks retry state, attempt count, next scheduled attempt, last HTTP status. |

**Critical indexes:**
- `idempotency_keys.message_id` — PRIMARY KEY (used in `ON CONFLICT DO NOTHING`)
- `delivery_attempts(message_id, subscriber_id)` — UNIQUE constraint
- Partial index on `delivery_attempts(next_attempt_at) WHERE status IN ('pending', 'failed')` — keeps the delivery worker poll query fast

**Status flows:**
```
idempotency_keys:  received → enriched → dispatched
delivery_attempts: pending → in_flight → delivered
                                       → failed → (retry) → dead
```

### `session.py`
- Creates the async SQLAlchemy engine bound to `DATABASE_URL`
- `AsyncSessionLocal` — session factory used throughout the app
- `get_session()` — async context manager (used in non-FastAPI contexts)
- `get_db()` — FastAPI dependency that yields a session
- `create_all()` — creates all tables; called by `python -m app.db.session` (the `migrate` service)
