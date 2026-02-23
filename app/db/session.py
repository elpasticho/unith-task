"""Database engine, session factory, and schema bootstrap."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency."""
    async with AsyncSessionLocal() as session:
        yield session


async def create_all() -> None:
    """Create all tables and apply incremental column migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_migrations(conn)


# ---------------------------------------------------------------------------
# Incremental column migrations
# ---------------------------------------------------------------------------
# create_all() is idempotent for whole tables but won't add columns to
# existing tables.  Each ALTER statement below uses ADD COLUMN IF NOT EXISTS
# so it is safe to run on every startup — it's a no-op when the column
# already exists.
# ---------------------------------------------------------------------------
_COLUMN_MIGRATIONS = [
    # 2024-Q1: reconcile cycle counter on idempotency_keys (M1 fix)
    """
    ALTER TABLE idempotency_keys
        ADD COLUMN IF NOT EXISTS reconcile_count INTEGER NOT NULL DEFAULT 0
    """,
]


async def _apply_migrations(conn) -> None:
    for sql in _COLUMN_MIGRATIONS:
        await conn.execute(text(sql.strip()))


if __name__ == "__main__":
    asyncio.run(create_all())
    print("Database schema created/migrated.")
