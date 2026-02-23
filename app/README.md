# app/

The core application package. All services (API, consumer, delivery worker) import from here.

## Entry points

| File | Runs as |
|------|---------|
| `main.py` | `uvicorn app.main:app` — FastAPI REST API |
| `consumer/rabbitmq.py` | `python -m app.consumer.rabbitmq` — RabbitMQ consumer |
| `delivery/worker.py` | `python -m app.delivery.worker` — delivery worker |
| `db/session.py` | `python -m app.db.session` — schema bootstrap (migrate service) |

## Files

| File | Purpose |
|------|---------|
| `config.py` | All configuration via pydantic-settings. Reads env vars / `.env` file. Single `settings` instance imported everywhere. |
| `main.py` | FastAPI app factory. Registers routers, lifespan (opens/closes RabbitMQ connection), health endpoints. |
| `broker.py` | Lifespan-managed RabbitMQ connection stored on `app.state`. API routes call `get_exchange(request.app)` to publish without opening a new connection per request. |
| `metrics.py` | Prometheus counters and histograms for the consumer and delivery worker. Exposed at `GET /metrics`. |

## Sub-packages

- [`api/`](api/README.md) — FastAPI route handlers
- [`db/`](db/README.md) — SQLAlchemy models and async session factory
- [`schemas/`](schemas/README.md) — Pydantic request/response models
- [`consumer/`](consumer/README.md) — RabbitMQ consumer, idempotency, reconciler
- [`enricher/`](enricher/README.md) — LLM enrichment provider abstraction and implementations
- [`delivery/`](delivery/README.md) — Webhook delivery worker, HTTP sender, HMAC signing
