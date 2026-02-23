# Event Processing & Distribution Service

A production-grade event pipeline: **RabbitMQ → LLM enrichment → HTTP webhook delivery**.

Built as a take-home assignment for a Senior Backend Engineer (LLM/AI) role at UNITH.

---

## Architecture

```
[RabbitMQ] ──► [consumer]
                    │
          idempotency check (Postgres)
                    │
             LLM enrichment (mock/OpenAI)
                    │
          write idempotency_key + delivery_attempts
                    │
                 ACK msg
                    │
[delivery_worker] ──► SELECT FOR UPDATE SKIP LOCKED
                      │
               HTTP POST + HMAC sig ──► [subscriber endpoint]
                      │
               update delivery_attempts (retry/dead/success)

[api]      ── subscriber CRUD, event publishing, delivery observation
[receiver] ── test webhook endpoint (logs + stores deliveries)
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| postgres | 5432 | Primary data store |
| rabbitmq | 5672 / 15672 | Message broker / management UI |
| api | 8000 | REST API |
| consumer | — | RabbitMQ message consumer |
| delivery_worker | — | Webhook delivery loop |
| receiver | 9001 | Test webhook receiver |

---

## Key Design Decisions

### 1. Idempotency — atomic `ON CONFLICT DO NOTHING`
```sql
INSERT INTO idempotency_keys (message_id, ...) ON CONFLICT (message_id) DO NOTHING RETURNING *
```
Zero rows returned → duplicate → ACK immediately. No SELECT-then-INSERT race.

### 2. Early ACK strategy
ACK the RabbitMQ message immediately after the idempotency row is committed.
Enrichment and delivery happen post-ACK. A reconciler re-enqueues any `status='received'` rows older than 5 minutes.

### 3. Delivery retries — `SELECT FOR UPDATE SKIP LOCKED`
Multiple worker instances coordinate without deadlock. Full-jitter exponential backoff. After `max_attempts` (default 10) → `status='dead'`.

### 4. Webhook authentication — HMAC-SHA256 (Stripe model)
```
canonical = f"{timestamp_ms}.{body_bytes}"
X-Webhook-Signature: sha256=HMAC-SHA256(secret, canonical)
X-Webhook-Timestamp: <unix_ms>
X-Webhook-ID: <delivery_attempt_id>
```
Secret shown once at registration. Replay attack prevention (5-min tolerance).

### 5. LLM integration — ABC with mock default
- `EnrichmentProvider` ABC with `enrich(event_type, payload) → EnrichedEvent`
- `MockLLMProvider`: log-normal latency, 8% transient errors, 2% fatal errors
- `OpenAILLMProvider`: stub, enabled via `ENRICHMENT_PROVIDER=openai`

---

## Quick Start

```bash
# Copy env vars
cp .env.example .env

# Start all services
docker compose up --build

# Register a subscriber
curl -X POST http://localhost:8000/api/v1/subscribers \
  -H "Content-Type: application/json" \
  -d '{"name":"test","endpoint":"http://receiver:9001/webhook"}'

# Publish 10 events (with duplicates to test idempotency)
python scripts/publish_events.py --count 10 --duplicates

# Watch logs
docker compose logs -f consumer delivery_worker

# Check pipeline stats
curl http://localhost:8000/api/v1/pipeline/stats

# View received webhooks
curl http://localhost:9001/received

# RabbitMQ Management UI
open http://localhost:15672   # guest / guest
```

---

## API Reference

```
POST   /api/v1/subscribers           Register subscriber (returns secret once)
GET    /api/v1/subscribers           List all active subscribers
GET    /api/v1/subscribers/{id}      Get subscriber detail
PATCH  /api/v1/subscribers/{id}      Update endpoint or is_active
DELETE /api/v1/subscribers/{id}      Soft-delete

POST   /api/v1/events/publish        Publish a test event to RabbitMQ
GET    /api/v1/events/{message_id}   Event detail + enrichment + delivery status

GET    /api/v1/deliveries            List deliveries (filter: status, subscriber_id, message_id)
POST   /api/v1/deliveries/{id}/retry Reset dead/failed delivery to pending

GET    /api/v1/pipeline/stats        Queue depth + delivery counts by status

GET    /health/live                  Liveness probe
GET    /health/ready                 Readiness probe (DB + RabbitMQ)
```

Interactive docs: http://localhost:8000/docs

---

## Database Schema

**`subscribers`** — Webhook consumer registry
**`idempotency_keys`** — One row per unique `message_id`; `status: received|enriched|dispatched`
**`delivery_attempts`** — One row per `(message_id, subscriber_id)`; `status: pending|in_flight|delivered|failed|dead`

Critical indexes:
- `idempotency_keys.message_id` — PRIMARY KEY (conflict target)
- `delivery_attempts(message_id, subscriber_id)` — UNIQUE constraint
- Partial index on `delivery_attempts(next_attempt_at) WHERE status IN ('pending','failed')`

---

## Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Unit tests (no external services required)
pytest tests/unit -v

# Integration tests (spins up real Postgres + RabbitMQ via testcontainers)
pytest tests/integration -v --timeout=120

# With coverage
pytest tests/unit --cov=app --cov-report=term-missing
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://unith:unith@localhost:5432/unith` | PostgreSQL connection |
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | RabbitMQ connection |
| `ENRICHMENT_PROVIDER` | `mock` | `mock` or `openai` |
| `OPENAI_API_KEY` | — | Required if using OpenAI provider |
| `DELIVERY_MAX_ATTEMPTS` | `10` | Max delivery attempts before dead |
| `DELIVERY_POLL_INTERVAL` | `5` | Worker poll interval (seconds) |
| `DELIVERY_BASE_DELAY_SECONDS` | `1.0` | Backoff base delay |
| `DELIVERY_MAX_DELAY_SECONDS` | `300.0` | Backoff max delay (5 min) |
| `WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS` | `300` | Replay attack window |
| `RECONCILER_STALE_MINUTES` | `5` | Age threshold for reconciler |

---

## Project Structure

```
tarea-unith/
├── docker-compose.yml
├── Dockerfile              # api, consumer, delivery_worker
├── Dockerfile.receiver     # test receiver
├── pyproject.toml
├── README.md
├── .env.example
├── app/
│   ├── config.py           # pydantic-settings
│   ├── main.py             # FastAPI app
│   ├── db/
│   │   ├── models.py       # SQLAlchemy ORM
│   │   └── session.py      # async engine + session factory
│   ├── api/
│   │   ├── router.py
│   │   ├── subscribers.py
│   │   ├── events.py
│   │   └── deliveries.py
│   ├── schemas/
│   │   ├── subscriber.py
│   │   ├── event.py
│   │   └── delivery.py
│   ├── consumer/
│   │   └── rabbitmq.py     # aio-pika consumer
│   ├── enricher/
│   │   ├── base.py         # EnrichmentProvider ABC
│   │   ├── mock_llm.py
│   │   └── openai_llm.py
│   └── delivery/
│       ├── worker.py       # SKIP LOCKED delivery loop
│       ├── sender.py       # httpx HTTP client
│       └── signing.py      # HMAC-SHA256
├── receiver/
│   └── main.py             # test webhook receiver
├── tests/
│   ├── unit/
│   │   ├── test_signing.py
│   │   ├── test_backoff.py
│   │   ├── test_enricher.py
│   │   └── test_idempotency_logic.py
│   └── integration/
│       ├── conftest.py     # testcontainers fixtures
│       ├── test_pipeline_e2e.py
│       └── test_delivery_retry.py
└── scripts/
    └── publish_events.py
```
