# app/api/

FastAPI route handlers. All routers are aggregated in `router.py` and mounted under `/api/v1`.

## Files

| File | Prefix | Description |
|------|--------|-------------|
| `router.py` | `/api/v1` | Aggregates all sub-routers into one `APIRouter`. |
| `subscribers.py` | `/api/v1/subscribers` | Subscriber CRUD — register, list, get, update, soft-delete. Secret is generated on create and returned once only. PATCH accepts an optional `X-Idempotency-Key` header; duplicate requests within 60 s return the cached response without a DB write. |
| `events.py` | `/api/v1/events` | Publish a test event to RabbitMQ (uses shared `app.state` exchange). Get event detail + enrichment status by `message_id`. |
| `deliveries.py` | `/api/v1/deliveries` | List delivery attempts (filterable by `DeliveryStatus` enum, subscriber, message — invalid `status` values return 422). Reset a dead/failed delivery to pending. Pipeline stats expose `queue_depth_available` and `queue_depth_error` when the RabbitMQ management API is unreachable. |

## Endpoints

```
POST   /api/v1/subscribers           Create subscriber (returns secret once)
GET    /api/v1/subscribers           List active subscribers
GET    /api/v1/subscribers/{id}      Get subscriber
PATCH  /api/v1/subscribers/{id}      Update endpoint / is_active
                                     Header: X-Idempotency-Key (optional, 60 s TTL cache)
DELETE /api/v1/subscribers/{id}      Soft-delete

POST   /api/v1/events/publish        Publish event to RabbitMQ
GET    /api/v1/events/{message_id}   Event detail + enrichment + delivery status

GET    /api/v1/deliveries            List delivery attempts
                                     ?status=  pending|in_flight|delivered|failed|dead
POST   /api/v1/deliveries/{id}/retry Reset dead/failed → pending

GET    /api/v1/pipeline/stats        Queue depth + counts by status
```
