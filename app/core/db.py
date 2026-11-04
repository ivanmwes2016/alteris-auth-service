import os
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

# Supabase's transaction pooler; its session pooler and direct connections use 5432.
TRANSACTION_POOLER_PORT = 6543


def engine_options(url: str, *, on_lambda: bool) -> dict[str, Any]:
    """
    How to connect, from where the database is and where we run.

    On Render the server runs all the time, so it keeps a few connections open and
    caches prepared statements: no new connection per request, one round trip per
    query. That needs Supabase's session pooler (port 5432) or a direct connection.

    Through the transaction pooler (port 6543), or on Lambda, where nothing outlives
    a request, it opens a connection per request and prepares every statement afresh.
    PgBouncer in transaction mode can swap the Postgres backend between calls, so a
    cached prepared statement may be gone ("prepared statement ... does not exist"),
    and two connections counting statement names from 0 can collide on one backend
    ("... already exists"): hence no caches, UUID statement names and NullPool, as the
    asyncpg dialect's "Prepared Statement Name with PGBouncer" docs recommend.
    """
    echo = os.getenv("SQL_ECHO", "").strip().lower() in {"1", "true", "yes", "on"}
    if on_lambda or urlparse(url).port == TRANSACTION_POOLER_PORT:
        return {
            "echo": echo,
            "poolclass": NullPool,
            "connect_args": {
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
                "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
            },
        }
    return {
        "echo": echo,
        # Small: the session pooler gives each client connection its own backend,
        # and the free plan allows only a few dozen.
        "pool_size": 5,
        "max_overflow": 5,
        # Supabase closes idle connections; check before use and renew every few minutes.
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }


config = get_settings()

engine = create_async_engine(
    config.DATABASE_URL,
    **engine_options(config.DATABASE_URL, on_lambda=bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))),
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
