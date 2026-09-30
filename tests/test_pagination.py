import pytest
from sqlalchemy import Column, MetaData, String, Table
from sqlalchemy.dialects import postgresql

from app.helpers.pagination import PageParams, make_page, page_params, search_filter

people = Table("people", MetaData(), Column("name", String), Column("email", String))


def _sql(expr: object) -> str:
    return str(expr.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))  # type: ignore[attr-defined]


def test_no_search_term_means_no_filter() -> None:
    assert search_filter(None, people.c.name) is None
    assert search_filter("", people.c.name) is None


def test_search_is_case_insensitive_contains_over_every_column() -> None:
    sql = _sql(search_filter("ada", people.c.name, people.c.email))
    assert sql.count("ILIKE") == 2
    assert "'%%ada%%'" in sql or "'%ada%'" in sql


def test_like_wildcards_in_the_term_are_matched_literally() -> None:
    compiled = search_filter("50%_off", people.c.name).compile(  # type: ignore[union-attr]
        dialect=postgresql.dialect()
    )
    assert list(compiled.params.values()) == ["%50\\%\\_off%"]
    assert "ESCAPE" in str(compiled)


def test_blank_search_is_dropped_and_full_list_is_the_default() -> None:
    params = page_params(page=None, page_size=25, search="   ")
    assert params.page is None
    assert params.search is None


@pytest.mark.parametrize(
    ("total", "page_size", "pages"), [(0, 25, 1), (25, 25, 1), (26, 25, 2), (101, 10, 11)]
)
def test_page_count(total: int, page_size: int, pages: int) -> None:
    page = make_page([], total, PageParams(page=1, page_size=page_size, search=None))
    assert page.pages == pages
    assert page.total == total
