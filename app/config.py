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


settings = Settings()
