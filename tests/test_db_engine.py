from sqlalchemy.pool import NullPool

from app.core.db import engine_options

POOLER = "postgresql+asyncpg://u:p@aws-1-eu-central-1.pooler.supabase.com"


def test_the_session_pooler_keeps_connections_and_caches_statements() -> None:
    options = engine_options(f"{POOLER}:5432/postgres", on_lambda=False)
    assert options["pool_size"] == 5
    assert options["pool_pre_ping"] is True
    assert "poolclass" not in options
    assert "connect_args" not in options


def test_the_transaction_pooler_opens_a_connection_per_request() -> None:
    options = engine_options(f"{POOLER}:6543/postgres", on_lambda=False)
    assert options["poolclass"] is NullPool
    assert options["connect_args"]["statement_cache_size"] == 0
    name = options["connect_args"]["prepared_statement_name_func"]
    assert name() != name()


def test_lambda_never_keeps_connections() -> None:
    options = engine_options(f"{POOLER}:5432/postgres", on_lambda=True)
    assert options["poolclass"] is NullPool


def test_sql_is_only_logged_when_asked(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    url = "postgresql+asyncpg://postgres@localhost:54330/skolarly"
    monkeypatch.delenv("SQL_ECHO", raising=False)
    assert engine_options(url, on_lambda=False)["echo"] is False
    monkeypatch.setenv("SQL_ECHO", "true")
    assert engine_options(url, on_lambda=False)["echo"] is True
