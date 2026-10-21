"""Shared FastAPI query parameter parsing."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from buscateses.catalog.search import SearchQuery


def search_query_params(
    q: Annotated[str, Query()] = "",
    mode: Annotated[str, Query()] = "hybrid",
    scope: Annotated[str, Query()] = "all",
    year_min: Annotated[int | None, Query()] = None,
    year_max: Annotated[int | None, Query()] = None,
    degree: Annotated[list[str] | None, Query()] = None,
    uf: Annotated[list[str] | None, Query()] = None,
    institution: Annotated[list[str] | None, Query()] = None,
    great_area: Annotated[list[str] | None, Query()] = None,
    knowledge_area: Annotated[list[str] | None, Query()] = None,
    sort: Annotated[str, Query()] = "relevance",
    page: Annotated[int, Query()] = 1,
    page_size: Annotated[int, Query()] = 20,
) -> SearchQuery:
    """Build a :class:`SearchQuery` from repeated query parameters."""
    return SearchQuery(
        q=q,
        mode=mode,
        scope=scope,
        year_min=year_min,
        year_max=year_max,
        degree=tuple(degree or ()),
        uf=tuple(uf or ()),
        institution=tuple(institution or ()),
        great_area=tuple(great_area or ()),
        knowledge_area=tuple(knowledge_area or ()),
        sort=sort,
        page=page,
        page_size=page_size,
    )


def split_ids(values: list[str] | None) -> list[str]:
    """Flatten comma-separated and repeated ``ids`` values."""
    if not values:
        return []
    result: list[str] = []
    for value in values:
        result.extend(part.strip() for part in str(value).split(",") if part.strip())
    return list(dict.fromkeys(result))


__all__ = ["search_query_params", "split_ids"]
