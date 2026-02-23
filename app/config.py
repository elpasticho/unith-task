from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://unith:unith@localhost:5432/unith"

    # RabbitMQ
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    rabbitmq_exchange: str = "events"
    rabbitmq_queue: str = "events.process"

    # Enrichment
    enrichment_provider: str = "mock"  # "mock" | "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Delivery worker
    delivery_max_attempts: int = 10
    delivery_poll_interval: int = 5  # seconds
    delivery_stale_in_flight_minutes: int = 10
    delivery_base_delay_seconds: float = 1.0
    delivery_max_delay_seconds: float = 300.0

    # Webhook signing
    webhook_hmac_secret_length: int = 32
    webhook_timestamp_tolerance_seconds: int = 300

    # Reconciler
    reconciler_stale_minutes: int = 5
    reconciler_interval_seconds: int = 60
    reconciler_max_attempts: int = 10   # max re-enqueue cycles per message before abandoning
    reconciler_batch_size: int = 100    # max stale rows loaded per reconciler run

    # RabbitMQ management API (for queue depth in /pipeline/stats)
    rabbitmq_management_url: str = "http://rabbitmq:15672"
    rabbitmq_management_user: str = "guest"
    rabbitmq_management_password: str = "guest"

    # Database connection pool (per process)
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: float = 30.0

    # Consumer input guard
    consumer_max_message_bytes: int = 1_048_576  # 1 MB

    # API request body size guard
    api_max_request_body_bytes: int = 1_048_576  # 1 MB


settings = Settings()
