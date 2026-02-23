# app/db/

SQLAlchemy ORM models and async session management.

## Files

### `models.py`
Defines the three database tables as SQLAlchemy ORM classes:

| Model | Table | Description |
|-------|-------|-------------|
| `Subscriber` | `subscribers` | Webhook consumer registry. Stores endpoint (validated as http/https URL), HMAC secret, active flag, soft-delete timestamp. |
| `IdempotencyKey` | `idempotency_keys` | One row per unique `message_id`. Tracks enrichment status, payload, and reconciler retry count. PRIMARY KEY is the conflict target for deduplication. |
| `DeliveryAttempt` | `delivery_attempts` | One row per `(message_id, subscriber_id)`. Tracks retry state, attempt count, next scheduled attempt, last HTTP status, and last error (including response body preview). |

**Critical indexes:**
- `idempotency_keys.message_id` — PRIMARY KEY (used in `ON CONFLICT DO NOTHING`)
- `delivery_attempts(message_id, subscriber_id)` — UNIQUE constraint
- Partial index on `delivery_attempts(next_attempt_at) WHERE status IN ('pending', 'failed')` — keeps the delivery worker poll query fast

**Status flows:**
```
idempotency_keys:  received → enriched → dispatched
                           → failed  (abandoned after RECONCILER_MAX_ATTEMPTS cycles)
delivery_attempts: pending → in_flight → delivered
                                       → failed → (retry) → dead
```

**`idempotency_keys` notable columns:**
- `reconcile_count` — incremented each time the reconciler re-enqueues this message; compared against `RECONCILER_MAX_ATTEMPTS`

### `session.py`
- Creates the async SQLAlchemy engine bound to `DATABASE_URL`; pool size, overflow, and timeout are configurable via `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`
- `AsyncSessionLocal` — session factory used throughout the app
- `get_session()` — async context manager (used in non-FastAPI contexts)
- `get_db()` — FastAPI dependency that yields a session
- `create_all()` — creates all tables then runs `_apply_migrations()` to add any new columns with `ALTER TABLE … ADD COLUMN IF NOT EXISTS`; called by `python -m app.db.session` (the `migrate` service)

**Adding schema changes:** append a new `ALTER TABLE … ADD COLUMN IF NOT EXISTS` statement to `_COLUMN_MIGRATIONS` in `session.py`. The migrate service runs it idempotently on every startup.
