"""
Create the schema in an empty local Postgres, for development (`make db-local`).

The migration history can't be replayed from nothing (it was started against an
existing database), so the tables come from the models, the roles are seeded, and the
database is stamped as up to date. New migrations then apply with `make migrate-local`.
Refuses to touch a database that already has tables, or one that isn't on this machine.
"""

import asyncio
import os
import sys
import uuid
from urllib.parse import urlparse

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.core.db import Base, engine
from app.db import models  # noqa: F401  (registers every table on Base.metadata)

ROLES = {
    "owner": "Full ownership and administrative access",
    "admin": "Administrative access to school management features",
    "head_teacher": "Manages school operations and academic activities",
    "teacher": "Manages teaching activities, students, and classes",
    "bursar": "Manages school finances, fees, and payments",
    "admissions_officer": "Manages student admissions and enrollment",
    "nurse": "Manages student health and medical information",
}


def _drop_duplicate_indexes() -> None:
    """Some models declare the same index twice (index=True and an explicit Index)."""
    for table in Base.metadata.tables.values():
        seen: set[str | None] = set()
        for index in sorted(table.indexes, key=lambda i: str(i.name)):
            if index.name in seen:
                table.indexes.discard(index)
            seen.add(index.name)


def _is_local(url: str | None) -> bool:
    return urlparse(url or "").hostname in {"localhost", "127.0.0.1"}


async def create() -> bool:
    """True if the tables were created; False if the database already had some."""
    engine.sync_engine.echo = False
    async with engine.begin() as conn:
        existing = await conn.run_sync(lambda sync: inspect(sync).get_table_names())
        if existing:
            return False
        _drop_duplicate_indexes()
        await conn.run_sync(Base.metadata.create_all)
        for name, description in ROLES.items():
            await conn.execute(
                text("INSERT INTO roles (id, name, description) VALUES (:id, :name, :description)"),
                {"id": uuid.uuid4(), "name": name, "description": description},
            )
    await engine.dispose()
    return True


def main() -> None:
    urls = os.environ.get("DATABASE_URL"), os.environ.get("DATABASE_URL_SYNC")
    if not all(_is_local(url) for url in urls):
        sys.exit("DATABASE_URL and DATABASE_URL_SYNC must both point at localhost; refusing.")
    if not asyncio.run(create()):
        print("The database already has tables; nothing done. `make migrate-local` updates it.")
        return
    command.stamp(Config("alembic.ini"), "head")
    print(f"Created {len(Base.metadata.tables)} tables and {len(ROLES)} roles.")


if __name__ == "__main__":
    main()
