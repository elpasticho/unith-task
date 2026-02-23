# Event Processing & Distribution Service

A production-grade event pipeline: **RabbitMQ → LLM enrichment → HTTP webhook delivery**.

Built as a take-home assignment for a Senior Backend Engineer (LLM/AI) role at UNITH.

---

## Architecture

```mermaid
flowchart TB
    classDef infra  fill:#F59E0B,stroke:#B45309,color:#000
    classDef svc    fill:#3B82F6,stroke:#1D4ED8,color:#fff
    classDef ext    fill:#10B981,stroke:#047857,color:#fff
    classDef decision fill:#8B5CF6,stroke:#6D28D9,color:#fff

    %% ── External actors ──────────────────────────────────────────
    PUB([" Publisher\n API · scripts/publish_events.py "]):::ext
    SUB([" Subscriber Endpoints "]):::ext
    RCV([" Receiver :9001\n test webhook sink "]):::ext

    %% ── Infrastructure ───────────────────────────────────────────
    RMQ[("RabbitMQ\nevents queue")]:::infra
    PG[("PostgreSQL\nsubscribers\nidempotency_keys\ndelivery_attempts")]:::infra

    %% ── API service ──────────────────────────────────────────────
    subgraph API["API Service  :8000"]
        direction TB
        A1["Subscriber CRUD"]
        A2["POST /events/publish"]
        A3["Delivery observation"]
        A4["GET /metrics  Prometheus"]
    end

    %% ── Consumer service ─────────────────────────────────────────
    subgraph CONS["Consumer Service"]
        direction TB
        C1["Receive message"]
        C2{{"Idempotency check\nINSERT … ON CONFLICT\nDO NOTHING RETURNING *"}}
        C3["Write idempotency_key\n+ delivery_attempts\nthen COMMIT"]
        C4(["ACK ✓  early"])
        C5["LLM Enrichment\nMockLLMProvider · OpenAI stub\ntenacity 3 retries"]
        C6["Persist enriched_payload\nstatus → enriched"]
        RECON["Reconciler  every 60 s\nre-enqueue status=received\nrows older than 5 min"]
    end

    %% ── Delivery worker ──────────────────────────────────────────
    subgraph WORKER["Delivery Worker"]
        direction TB
        W1["SELECT FOR UPDATE SKIP LOCKED\nclaim batch → status in_flight"]
        W2["HTTP POST\nX-Webhook-Signature  HMAC-SHA256\nX-Webhook-Timestamp  replay guard\nX-Webhook-ID  subscriber idempotency"]
        W3{{"2xx?"}}
        W4["status → delivered"]
        W5["status → failed\nfull-jitter exponential backoff"]
        W6["status → dead\nafter max_attempts  default 10"]
    end

    %% ── Ingestion flow ───────────────────────────────────────────
    PUB               -->|"AMQP publish\npersistent message"| RMQ
    API               -->|"shared app.state\nRobustConnection"| RMQ
    RMQ               --> C1
    C1                --> C2
    C2                -->|"duplicate → skip"| C4
    C2                -->|"new"| C3
    C3                --> PG
    C3                --> C4
    C4                --> C5
    C5                -->|"success"| C6
    C6                --> PG
    C5                -->|"transient / fatal error"| PG
    RECON             -->|"re-enqueue"| RMQ
    RECON             <-->|"query stale rows"| PG

    %% ── Delivery flow ────────────────────────────────────────────
    W1                <-->|"SELECT / UPDATE"| PG
    W1                --> W2
    W2                -->|"POST + sig headers"| SUB
    W2                -->|"POST + sig headers"| RCV
    W2                --> W3
    W3                -->|"yes"| W4
    W3                -->|"no, attempts < max"| W5
    W3                -->|"no, attempts == max"| W6
    W4 & W5 & W6      --> PG
    W5                -->|"next_attempt_at scheduled"| W1

    %% ── API ↔ DB ─────────────────────────────────────────────────
    API               <-->|"CRUD + stats queries"| PG
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| postgres | 5432 | Primary data store |
| rabbitmq | 5672 / 15672 | Message broker / management UI |
| api | 8000 | REST API + Prometheus metrics |
| consumer | — | RabbitMQ message consumer + reconciler |
| delivery_worker | — | Webhook delivery loop |
| receiver | 9001 | Test webhook receiver |

---

## Key Design Decisions

### 1. Idempotency — atomic `ON CONFLICT DO NOTHING`
```sql
INSERT INTO idempotency_keys (message_id, ...) ON CONFLICT (message_id) DO NOTHING RETURNING *
```
Zero rows returned → duplicate → ACK immediately, return. No SELECT-then-INSERT race condition. Single DB round-trip.

### 2. Early ACK strategy
The RabbitMQ message is ACKed right after the idempotency row and delivery attempts are committed — before enrichment begins. This means the consumer is never blocked by LLM latency or HTTP calls, and a broker restart cannot cause duplicate processing.

A background **reconciler** runs every 60 seconds and re-enqueues any `idempotency_keys` rows stuck in `status='received'` for longer than 5 minutes, recovering from consumer crashes that occurred post-ACK.

### 3. Delivery retries — `SELECT FOR UPDATE SKIP LOCKED`
The delivery worker claims rows atomically by setting `status='in_flight'` inside a transaction, then releases the lock before making the HTTP call. Multiple worker instances coordinate without deadlock. On worker startup, any `in_flight` rows older than 10 minutes are reset to `failed` (crash recovery).

Backoff: full-jitter exponential — `delay = random(0, min(base × 2^attempt, 300s))`. After `max_attempts` (default 10) → `status='dead'`. Dead deliveries can be manually reset via `POST /api/v1/deliveries/{id}/retry`.

### 4. Webhook authentication — HMAC-SHA256 (Stripe model)
```
canonical = f"{timestamp_ms}.{body_bytes}"
X-Webhook-Signature: sha256=HMAC-SHA256(secret, canonical)
X-Webhook-Timestamp: <unix_ms>    ← replay attack prevention (5 min tolerance)
X-Webhook-ID: <delivery_attempt_id>  ← subscriber-side idempotency key
```
Secret is generated with `secrets.token_hex(32)` at registration, shown **once**, never returned again. Signature comparison uses `hmac.compare_digest` (constant-time).

### 5. LLM integration — ABC with mock default
- `EnrichmentProvider` ABC: `async def enrich(event_type, payload) → EnrichedEvent`
- `MockLLMProvider`: log-normal latency (~0.8 s median), 8% transient errors, 2% fatal errors
- `OpenAILLMProvider`: fully wired stub — calls Chat Completions with JSON-mode prompt; enabled via `ENRICHMENT_PROVIDER=openai`
- Provider is instantiated once as a module-level singleton in the consumer (not per-message)

### 6. Shared RabbitMQ connection (API)
The API uses a FastAPI lifespan-managed connection (`app/broker.py`) opened at startup and stored on `app.state`. All publish requests reuse the same connection instead of opening one per request.

### 7. Observability
- **Structured logging** via `structlog` — every key event carries named fields (`message_id`, `event_type`, `delivery_id`, etc.)
- **Prometheus metrics** at `GET /metrics`:
  - `consumer_messages_received_total`
  - `consumer_messages_duplicate_total`
  - `consumer_enrichment_errors_total{error_type}` — `transient` | `fatal`
  - `consumer_enrichment_duration_seconds` — histogram
  - `delivery_attempts_total`, `delivery_successes_total`, `delivery_dead_total`
  - `delivery_failures_total{http_status}` — labeled by HTTP status code or `timeout`
  - `delivery_http_duration_seconds` — histogram
- **`GET /api/v1/pipeline/stats`** — delivery counts by status + idempotency key counts + live queue depth from the RabbitMQ management API
- **`GET /api/v1/events/{message_id}`** — trace a single event end-to-end: raw payload, enriched payload, status, all delivery attempts

---

## Failure Modes

| Failure | How it's handled |
|---------|-----------------|
| RabbitMQ redelivers a message | `ON CONFLICT DO NOTHING` on `message_id` — duplicate is ACKed and discarded in one DB round-trip |
| Consumer crashes after ACK, before enrichment | Reconciler detects `status='received'` rows > 5 min old and re-enqueues them |
| Subscriber endpoint returns 5xx / times out | Delivery worker retries with full-jitter exponential backoff up to `max_attempts` |
| Delivery worker crashes mid-delivery | On restart, stale `in_flight` rows > 10 min are reset to `failed` and retried |
| All retries exhausted | `status='dead'`; operator can inspect via API and reset with `POST /deliveries/{id}/retry` |
| Transient LLM error | tenacity retries up to 3 times; on final failure, reconciler picks it up later |
| Fatal LLM error | Error recorded on the idempotency key row; reconciler retries after the stale window |
| Spoofed webhook | HMAC-SHA256 + timestamp tolerance rejects requests with invalid signatures or replayed timestamps |

---

## Quick Start

```bash
# Copy env vars
cp .env.example .env

# Start all services (builds, starts, waits for healthy)
bash scripts/start.sh

# Stop (keeps data volumes)
bash scripts/stop.sh

# Stop and wipe all data
bash scripts/stop.sh --volumes

# Register a subscriber pointing at the test receiver
curl -X POST http://localhost:8000/api/v1/subscribers \
  -H "Content-Type: application/json" \
  -d '{"name":"test","endpoint":"http://receiver:9001/webhook"}'

# Publish 10 events (with ~30% duplicates to test idempotency)
python scripts/publish_events.py --count 10 --duplicates

# Watch logs
docker compose logs -f consumer delivery_worker

# Check pipeline stats
curl http://localhost:8000/api/v1/pipeline/stats

# View received webhooks at the test receiver
curl http://localhost:9001/received

# Prometheus metrics
curl http://localhost:8000/metrics

# RabbitMQ Management UI
open http://localhost:15672   # guest / guest

# Interactive API docs
open http://localhost:8000/docs

# Run the full E2E smoke test suite (9 scenarios)
python scripts/e2e_test.py
```

---

## Debugging a Failed Event

```bash
# 1. Find the event by message_id
curl http://localhost:8000/api/v1/events/<message_id>
# → shows status, enriched payload, error field if enrichment failed

# 2. Find its delivery attempts
curl "http://localhost:8000/api/v1/deliveries?message_id=<message_id>"
# → shows attempt_count, last_http_status, last_error, next_attempt_at per subscriber

# 3. Manually retry a dead delivery
curl -X POST http://localhost:8000/api/v1/deliveries/<delivery_id>/retry

# 4. Grep structured logs by message_id
docker compose logs consumer | grep <message_id>
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
GET    /api/v1/events/{message_id}   Event detail + enrichment + all delivery attempts

GET    /api/v1/deliveries            List deliveries (filter: status, subscriber_id, message_id)
POST   /api/v1/deliveries/{id}/retry Reset dead/failed delivery to pending

GET    /api/v1/pipeline/stats        Queue depth + delivery/event counts by status

GET    /health/live                  Liveness probe
GET    /health/ready                 Readiness probe (checks DB + RabbitMQ connection)
GET    /metrics                      Prometheus metrics
```

---

## Database Schema

**`subscribers`** — Webhook consumer registry

**`idempotency_keys`** — One row per unique `message_id`
- `status`: `received` → `enriched` → `dispatched`
- `enriched_payload`: stored after LLM processing; used by delivery worker

**`delivery_attempts`** — One row per `(message_id, subscriber_id)`
- `status`: `pending` → `in_flight` → `delivered` | `failed` → `dead`
- `UNIQUE (message_id, subscriber_id)` — prevents duplicate rows on redelivery

Critical indexes:
- `idempotency_keys.message_id` — PRIMARY KEY (conflict target)
- `delivery_attempts(message_id, subscriber_id)` — UNIQUE constraint
- Partial index on `delivery_attempts(next_attempt_at) WHERE status IN ('pending', 'failed')` — delivery worker poll query

---

## Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Unit tests — no external services, run in < 1 s
pytest tests/unit -v

# Integration tests — testcontainers spins up real Postgres + RabbitMQ
pytest tests/integration -v --timeout=120

# With coverage
pytest tests/unit --cov=app --cov-report=term-missing
```

### What the tests protect

| Test file | What it protects |
|-----------|-----------------|
| `test_signing.py` | Valid sig passes; tampered body/sig fails; stale timestamp rejected; constant-time compare |
| `test_backoff.py` | Delay math per attempt; jitter varies; always capped at max_delay |
| `test_enricher.py` | EnrichedEvent fields populated; error rates at correct proportions (N=1000) |
| `test_idempotency_logic.py` | Handler short-circuits on duplicate; invalid JSON ACKed; new message calls enricher |
| `test_pipeline_e2e.py` | Full pipeline with real enrichment code path; duplicate message skipped; reconciler re-enqueues stale rows |
| `test_delivery_retry.py` | 503 × 2 → 200 produces `delivered` with `attempt_count=3`; exhausted retries produce `dead` |

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://unith:unith@localhost:5432/unith` | PostgreSQL connection |
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | RabbitMQ connection |
| `ENRICHMENT_PROVIDER` | `mock` | `mock` or `openai` |
| `OPENAI_API_KEY` | — | Required if using OpenAI provider |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model name |
| `DELIVERY_MAX_ATTEMPTS` | `10` | Max delivery attempts before dead |
| `DELIVERY_POLL_INTERVAL` | `5` | Worker poll interval (seconds) |
| `DELIVERY_STALE_IN_FLIGHT_MINUTES` | `10` | Age threshold for in-flight crash recovery |
| `DELIVERY_BASE_DELAY_SECONDS` | `1.0` | Backoff base delay |
| `DELIVERY_MAX_DELAY_SECONDS` | `300.0` | Backoff cap (5 min) |
| `WEBHOOK_HMAC_SECRET_LENGTH` | `32` | Bytes of entropy in generated secrets |
| `WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS` | `300` | Replay attack window |
| `RECONCILER_STALE_MINUTES` | `5` | Age before a `received` key is re-enqueued |
| `RECONCILER_INTERVAL_SECONDS` | `60` | How often the reconciler runs |
| `RABBITMQ_MANAGEMENT_URL` | `http://rabbitmq:15672` | Management API for queue depth in stats |
| `RABBITMQ_MANAGEMENT_USER` | `guest` | Management API username |
| `RABBITMQ_MANAGEMENT_PASSWORD` | `guest` | Management API password |

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
│   ├── config.py           # pydantic-settings — all env vars
│   ├── main.py             # FastAPI app factory + lifespan
│   ├── broker.py           # lifespan-managed RabbitMQ connection
│   ├── metrics.py          # Prometheus counters + histograms
│   ├── db/
│   │   ├── models.py       # SQLAlchemy ORM (Subscriber, IdempotencyKey, DeliveryAttempt)
│   │   └── session.py      # async engine + session factory + schema bootstrap
│   ├── api/
│   │   ├── router.py
│   │   ├── subscribers.py  # CRUD
│   │   ├── events.py       # publish + event detail
│   │   └── deliveries.py   # list, retry, pipeline stats
│   ├── schemas/
│   │   ├── subscriber.py
│   │   ├── event.py
│   │   └── delivery.py
│   ├── consumer/
│   │   └── rabbitmq.py     # aio-pika consumer, idempotency, reconciler, provider singleton
│   ├── enricher/
│   │   ├── base.py         # EnrichmentProvider ABC + error types
│   │   ├── mock_llm.py     # log-normal latency + error injection
│   │   └── openai_llm.py   # Chat Completions stub
│   └── delivery/
│       ├── worker.py       # SELECT FOR UPDATE SKIP LOCKED loop
│       ├── sender.py       # httpx HTTP client
│       └── signing.py      # HMAC-SHA256 sign + verify
├── receiver/
│   └── main.py             # test webhook receiver (in-memory log)
├── tests/
│   ├── unit/
│   │   ├── test_signing.py
│   │   ├── test_backoff.py
│   │   ├── test_enricher.py
│   │   └── test_idempotency_logic.py
│   └── integration/
│       ├── conftest.py     # testcontainers session fixtures
│       ├── test_pipeline_e2e.py
│       └── test_delivery_retry.py
└── scripts/
    └── publish_events.py   # CLI: publish N events, optional duplicates
```
