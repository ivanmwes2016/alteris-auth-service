from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

config = get_settings()

engine = create_async_engine(
    config.DATABASE_URL,
    echo=True,
    # Supabase's pooler runs PgBouncer in transaction mode: it can swap the underlying
    # Postgres backend between calls, so a prepared statement name SQLAlchemy reused
    # from its own cache may no longer exist there ("prepared statement ... does not
    # exist"). prepared_statement_cache_size=0 is SQLAlchemy's asyncpg-dialect cache
    # (distinct from asyncpg's own statement_cache_size) — disabling it forces a fresh
    # PREPARE immediately before every EXECUTE instead of reusing a stale name.
    connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
)


class Base(DeclarativeBase):
    pass


SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
