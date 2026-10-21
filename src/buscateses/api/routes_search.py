"""Search, facets and thesis detail routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from buscateses.api.deps import StateDep
from buscateses.api.params import search_query_params
from buscateses.catalog.search import SearchError, SearchNotFoundError, SearchQuery

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search")
def search(
    query: Annotated[SearchQuery, Depends(search_query_params)],
    state: StateDep,
) -> dict:
    """Run a text, semantic or hybrid search."""
    catalog = state.catalog
    if catalog is None:
        raise HTTPException(status_code=503, detail="O catálogo ainda não foi instalado.")
    try:
        result = catalog.search(query)
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.to_dict() if hasattr(result, "to_dict") else result


@router.get("/facets")
def facets(state: StateDep) -> dict:
    """Return global facets for the filter sidebar."""
    catalog = state.catalog
    if catalog is None:
        return {
            "degree": [],
            "uf": [],
            "great_area": [],
            "knowledge_area": [],
            "institution_acronym": [],
            "year": [],
        }
    try:
        return catalog.global_facets()
    except SearchError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/theses/{thesis_id}")
def get_thesis(thesis_id: str, state: StateDep) -> dict:
    """Return the full record of one thesis."""
    catalog = state.catalog
    if catalog is None:
        raise HTTPException(status_code=503, detail="O catálogo ainda não foi instalado.")
    try:
        return catalog.get(thesis_id)
    except SearchNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tese não encontrada.") from exc
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/theses/{thesis_id}/similar")
def similar_theses(
    thesis_id: str,
    state: StateDep,
    k: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[dict]:
    """Return semantically similar theses."""
    catalog = state.catalog
    if catalog is None:
        raise HTTPException(status_code=503, detail="O catálogo ainda não foi instalado.")
    try:
        return catalog.similar(thesis_id, k)
    except SearchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = ["router"]
