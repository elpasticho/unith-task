# app/consumer/

RabbitMQ message consumer. Runs as a standalone process (`python -m app.consumer.rabbitmq`).

## Files

### `rabbitmq.py`

The main consumer loop and all message-handling logic.

**Processing flow for each message:**

```
1. Parse JSON body → extract message_id, event_type, payload
2. INSERT INTO idempotency_keys ON CONFLICT DO NOTHING
   └─ zero rows returned → duplicate → ACK + return
3. INSERT delivery_attempts for every active subscriber
4. COMMIT → ACK the RabbitMQ message (early ACK)
5. Enrich via provider singleton (tenacity: 3 retries on TransientEnrichmentError)
   ├─ TransientEnrichmentError (after retries) → log warning, return (reconciler retries)
   └─ FatalEnrichmentError → log error, record on idempotency_key.error, return
6. UPDATE idempotency_key: enriched_payload, status='enriched'
```

**Key functions:**

| Function | Description |
|----------|-------------|
| `_insert_idempotency_key()` | Atomic `ON CONFLICT DO NOTHING` insert. Returns the ORM row or `None` on duplicate. |
| `_create_delivery_attempts()` | Creates one `DeliveryAttempt` row per active subscriber within the same transaction as the idempotency insert. |
| `_enrich_with_retry()` | Wraps the provider call with tenacity: up to 3 attempts, exponential wait, reraises on exhaustion. |
| `_get_provider()` | Returns the module-level provider singleton (instantiated once, reused for every message). |
| `_reconciler()` | Background task: every 60 s, finds `idempotency_keys` stuck in `received` for > 5 min and re-publishes them to the queue. Handles the case where the consumer crashed post-ACK before enrichment completed. |
| `handle_message()` | aio-pika message handler — orchestrates the full flow above. |
| `run()` | Entry point: connects to RabbitMQ, declares exchange/queue, starts the reconciler task, begins consuming. |

**Idempotency guarantee:**
The `ON CONFLICT (message_id) DO NOTHING RETURNING *` pattern means duplicate detection is a single atomic DB operation with no SELECT-then-INSERT race condition. If the consumer crashes between the COMMIT and the ACK, RabbitMQ redelivers — the idempotency check catches it.

**asyncpg / JSONB note:**
The raw payload is inserted using `CAST(:payload AS jsonb)` rather than the PostgreSQL `::jsonb` shorthand. asyncpg's parameter parser misreads `:payload::jsonb` — it sees the `::` immediately after a named parameter and raises a syntax error. Standard SQL `CAST()` avoids this.
