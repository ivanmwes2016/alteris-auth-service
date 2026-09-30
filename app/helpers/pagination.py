"""
Opt-in pagination for list endpoints.

With `?page=` the endpoint returns one page as a `Page` envelope; without it, the
endpoint keeps returning the plain list, so callers that need everything (pickers,
dashboard counts) keep working unchanged.
"""

import math
from typing import Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class PageParams(BaseModel):
    page: int | None
    page_size: int
    search: str | None


def page_params(
    page: int | None = Query(default=None, ge=1, description="1-based; omit for the full list"),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    search: str | None = Query(default=None, max_length=100),
) -> PageParams:
    return PageParams(page=page, page_size=page_size, search=(search or "").strip() or None)


def search_filter(
    term: str | None, *columns: QueryableAttribute[Any] | ColumnElement[Any]
) -> ColumnElement[bool] | None:
    """Case-insensitive "contains" over any of the columns, with LIKE wildcards escaped."""
    if not term:
        return None
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return or_(*(column.ilike(pattern, escape="\\") for column in columns))


async def fetch_page(
    db: AsyncSession, query: Select[Any], params: PageParams
) -> tuple[list[Any], int]:
    """
    Rows for the requested page plus the total across all pages. `query` should carry
    the filters and a stable order_by; loader options on it are fine.
    """
    assert params.page is not None
    total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    result = await db.execute(
        query.limit(params.page_size).offset((params.page - 1) * params.page_size)
    )
    return list(result.scalars().unique().all()), total or 0


def make_page(items: list[T], total: int, params: PageParams) -> Page[T]:
    assert params.page is not None
    return Page[T](
        items=items,
        total=total,
        page=params.page,
        page_size=params.page_size,
        pages=max(1, math.ceil(total / params.page_size)),
    )
