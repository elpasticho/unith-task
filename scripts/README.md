# scripts/

Developer utility scripts. Not part of any service — run locally against a running stack.

## `start.sh`

Builds and starts the full docker-compose stack, then waits until the API is healthy before returning.

```bash
bash scripts/start.sh
```

Prints URLs for the API, docs, metrics, receiver, and RabbitMQ management UI when ready.

## `stop.sh`

Stops all services. Data volumes are preserved by default so the database survives restarts.

```bash
# Stop services, keep volumes (database + queue data intact)
bash scripts/stop.sh

# Stop services AND delete volumes (clean slate)
bash scripts/stop.sh --volumes
```

## `e2e_test.py`

End-to-end smoke test suite. Runs 10 scenarios against the live docker-compose stack via HTTP.
No pytest, no testcontainers — just plain `httpx` calls.

```bash
# Run against the default docker-compose stack
python scripts/e2e_test.py

# All flags with their defaults shown explicitly
python scripts/e2e_test.py \
  --api http://localhost:8000 \
  --receiver http://localhost:9001 \
  --subscriber-endpoint http://receiver:9001
```

**Exit codes:** `0` = all pass, `1` = failures, `2` = connection error (stack not running).

**Important — two receiver URLs:**

| Flag | Default | Used by |
|------|---------|---------|
| `--receiver` | `http://localhost:9001` | The test script itself, to query `GET /received` |
| `--subscriber-endpoint` | `http://receiver:9001` | Stored in the DB; the delivery worker (inside Docker) POSTs to this URL |

The delivery worker runs inside the Docker network where `localhost` resolves to its own container. Subscriber endpoints must use the Docker-internal service name (`receiver`), not `localhost`.

**Scenarios covered:**

| # | Scenario | What it proves |
|---|----------|----------------|
| 1 | Health checks | API, DB, and RabbitMQ are all reachable |
| 2 | Subscriber lifecycle | Create returns secret once; GET hides it; PATCH updates endpoint; appears in list |
| 3 | Happy path | publish → enriched → delivered → receiver confirms event_type + payload |
| 4 | Idempotency | Same `message_id` published 3× → exactly 1 `idempotency_keys` row, 1 `delivery_attempt` |
| 5 | Multiple subscribers | One event → independent delivery to each active subscriber |
| 6 | HMAC verification | Signature on received webhook is cryptographically valid; tampered body fails |
| 7 | Failed delivery → retry | Bad endpoint → `status=failed` → fix endpoint → `POST /deliveries/{id}/retry` → `delivered` |
| 8 | Pipeline stats | `/pipeline/stats` reflects delivered count and event totals |
| 9 | Prometheus metrics | `/metrics` exposes all named counters/histograms with non-zero values |
| 10 | API edge cases | DELETE soft-deletes and returns 404 after; all 404 paths; `subscriber_id`/`status`/`limit` filters on `GET /deliveries`; 409 on retry of a delivered attempt |

## `publish_events.py`

CLI tool to publish test events directly to RabbitMQ, bypassing the API. Useful for load testing, idempotency testing, and manual pipeline walkthroughs.

```bash
# Publish 10 events
python scripts/publish_events.py --count 10

# Publish 20 events with ~30% duplicates (tests idempotency)
python scripts/publish_events.py --count 20 --duplicates

# Custom event type and RabbitMQ URL
python scripts/publish_events.py \
  --url amqp://guest:guest@localhost:5672/ \
  --event-type user.purchase \
  --count 5
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--url` | `amqp://guest:guest@localhost:5672/` | RabbitMQ connection URL |
| `--exchange` | `events` | Exchange name |
| `--queue` | `events.process` | Queue / routing key |
| `--count` | `10` | Number of messages to publish |
| `--duplicates` | off | Re-use existing `message_id`s at ~30% rate |
| `--event-type` | `user.action` | Value of the `event_type` field |

Each message payload includes a random `user_id`, `action`, and `value` field to simulate realistic data. When `--duplicates` is used, the script prints `NEW` or `DUPLICATE` next to each message so you can verify idempotency in the consumer logs.
