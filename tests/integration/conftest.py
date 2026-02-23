"""Integration test fixtures using testcontainers."""
from __future__ import annotations

import asyncio
import os
import time

import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer
from testcontainers.rabbitmq import RabbitMqContainer

# Override settings before any app imports
@pytest.fixture(scope="session", autouse=True)
def containers():
    pg = PostgresContainer("postgres:16-alpine")
    rmq = RabbitMqContainer("rabbitmq:3.13-management-alpine")

    pg.start()
    rmq.start()

    db_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
    rmq_url = f"amqp://guest:guest@{rmq.get_container_host_ip()}:{rmq.get_exposed_port(5672)}/"

    os.environ["DATABASE_URL"] = db_url
    os.environ["RABBITMQ_URL"] = rmq_url
    os.environ["ENRICHMENT_PROVIDER"] = "mock"

    yield {"db_url": db_url, "rmq_url": rmq_url}

    rmq.stop()
    pg.stop()


@pytest_asyncio.fixture(scope="session")
async def db_engine(containers):
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.db.models import Base

    # Need to reload settings after env vars are set
    import importlib
    import app.config
    importlib.reload(app.config)
    import app.db.session
    importlib.reload(app.db.session)

    engine = create_async_engine(containers["db_url"], echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    Session = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with Session() as session:
        yield session
        await session.rollback()
