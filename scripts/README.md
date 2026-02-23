# scripts/

Developer utility scripts. Not part of any service — run locally against a running stack.

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
