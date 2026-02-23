# app/schemas/

Pydantic models for API request validation and response serialization. Kept separate from ORM models to avoid coupling the API contract to the database schema.

## Files

| File | Models | Description |
|------|--------|-------------|
| `subscriber.py` | `SubscriberCreate`, `SubscriberUpdate`, `SubscriberResponse`, `SubscriberCreatedResponse` | `SubscriberCreatedResponse` extends the base response with the `secret` field — only returned on creation. `endpoint` is validated as a structurally valid `http://` or `https://` URL in both create and update. |
| `event.py` | `EventPublishRequest`, `EventPublishResponse`, `EnrichedEventResponse` | `EventPublishRequest` accepts an optional `message_id` (max 255 chars) for idempotent publishes. `event_type` is limited to 255 characters. `payload` is validated to be ≤ 512 KB when serialized (overall request body is capped at 1 MB by middleware). |
| `delivery.py` | `DeliveryAttemptResponse`, `PipelineStats` | `PipelineStats.queue_depth` is `Optional[int]` — `null` when the RabbitMQ management API is unreachable. `queue_depth_available` (bool) and `queue_depth_error` (string) indicate availability. |
