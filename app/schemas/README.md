# app/schemas/

Pydantic models for API request validation and response serialization. Kept separate from ORM models to avoid coupling the API contract to the database schema.

## Files

| File | Models | Description |
|------|--------|-------------|
| `subscriber.py` | `SubscriberCreate`, `SubscriberUpdate`, `SubscriberResponse`, `SubscriberCreatedResponse` | `SubscriberCreatedResponse` extends the base response with the `secret` field — only returned on creation. |
| `event.py` | `EventPublishRequest`, `EventPublishResponse`, `EnrichedEventResponse` | `EventPublishRequest` accepts an optional `message_id` so callers can supply their own for idempotent publishes. |
| `delivery.py` | `DeliveryAttemptResponse`, `PipelineStats` | `PipelineStats` includes queue depth (from RabbitMQ management API) and counts by status for both deliveries and idempotency keys. |
