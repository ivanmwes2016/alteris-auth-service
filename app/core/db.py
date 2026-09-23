from collections.abc import AsyncGenerator
from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

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
    #
    # That alone isn't enough, though: the default statement-name generator is just a
    # per-connection counter (__asyncpg_stmt_0__, _1__, ...). PgBouncer can route two
    # different pooled connections to the same backend, and a shared backend has one
    # prepared-statement namespace — so two connections independently counting from 0
    # collide ("prepared statement ... already exists"). A UUID-based name avoids that,
    # and NullPool stops SQLAlchemy from holding its own idle pool on top of PgBouncer's
    # (recommended together — see the asyncpg dialect's "Prepared Statement Name with
    # PGBouncer" docs).
    poolclass=NullPool,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
    },
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
