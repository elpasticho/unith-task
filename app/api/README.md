# app/api/

FastAPI route handlers. All routers are aggregated in `router.py` and mounted under `/api/v1`.

## Files

| File | Prefix | Description |
|------|--------|-------------|
| `router.py` | `/api/v1` | Aggregates all sub-routers into one `APIRouter`. |
| `subscribers.py` | `/api/v1/subscribers` | Subscriber CRUD — register, list, get, update, soft-delete. Secret is generated on create and returned once only. |
| `events.py` | `/api/v1/events` | Publish a test event to RabbitMQ (uses shared `app.state` exchange). Get event detail + enrichment status by `message_id`. |
| `deliveries.py` | `/api/v1/deliveries` | List delivery attempts (filterable by status, subscriber, message). Reset a dead/failed delivery to pending. Pipeline stats (DB counts + live RabbitMQ queue depth). |

## Endpoints

```
POST   /api/v1/subscribers           Create subscriber (returns secret once)
GET    /api/v1/subscribers           List active subscribers
GET    /api/v1/subscribers/{id}      Get subscriber
PATCH  /api/v1/subscribers/{id}      Update endpoint / is_active
DELETE /api/v1/subscribers/{id}      Soft-delete

POST   /api/v1/events/publish        Publish event to RabbitMQ
GET    /api/v1/events/{message_id}   Event detail + enrichment + delivery status

GET    /api/v1/deliveries            List delivery attempts
POST   /api/v1/deliveries/{id}/retry Reset dead/failed → pending

GET    /api/v1/pipeline/stats        Queue depth + counts by status
```
