# receiver/

Standalone test webhook receiver service. Runs on port 9001 (`uvicorn receiver.main:app --port 9001`). Built with its own minimal Dockerfile (`Dockerfile.receiver`) — no dependency on the main app package.

## Purpose

Provides a real HTTP endpoint for the delivery worker to POST to during local development and integration testing, so you can observe what the webhook payload actually looks like.

## Files

### `main.py`

| Endpoint | Description |
|----------|-------------|
| `POST /webhook` | Accepts incoming webhook deliveries. Logs the request and stores it in an in-memory deque (last 1000 entries). Always returns `{"status": "ok"}`. |
| `GET /received` | Returns the stored deliveries as JSON. Accepts a `?limit=N` query parameter (default 50). |
| `DELETE /received` | Clears the in-memory store. Useful between test runs. |
| `GET /health` | Liveness check. Returns current `received_count`. |

## Inspecting deliveries

```bash
# See what the receiver got
curl http://localhost:9001/received

# Each entry contains:
# {
#   "webhook_id": "<delivery_attempt_id>",
#   "timestamp_ms": "<unix_ms>",
#   "signature": "sha256=...",
#   "body": { "delivery_id": "...", "event_type": "...", "payload": {...} },
#   "received_at": <unix_ms>
# }

# Clear between tests
curl -X DELETE http://localhost:9001/received
```

## Note on signature verification

The receiver logs the `X-Webhook-Signature` header but does not verify it — its job is observation, not enforcement. To test verification, use `app/delivery/signing.verify()` directly or write a custom receiver with the subscriber's secret.
