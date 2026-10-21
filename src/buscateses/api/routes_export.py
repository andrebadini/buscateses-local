"""Export routes for CSV, BibTeX, RIS and ABNT."""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from buscateses.api.deps import StateDep
from buscateses.api.params import search_query_params, split_ids
from buscateses.catalog.search import SearchError, SearchQuery
from buscateses.exporters import EXPORT_FORMATS, export_records

router = APIRouter(prefix="/api", tags=["export"])

_MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "bibtex": "application/x-bibtex; charset=utf-8",
    "ris": "application/x-research-info-systems; charset=utf-8",
    "abnt": "text/plain; charset=utf-8",
}
_FILENAMES = {
    "csv": "buscateses.csv",
    "bibtex": "buscateses.bib",
    "ris": "buscateses.ris",
    "abnt": "buscateses-referencias.txt",
}
_MAX_EXPORT = 1000


@router.get("/export")
def export(
    query: Annotated[SearchQuery, Depends(search_query_params)],
    state: StateDep,
    format: Annotated[str, Query()] = "csv",
    ids: Annotated[list[str] | None, Query()] = None,
) -> Response:
    """Export selected ids or the current search results (up to 1000)."""
    clean_format = str(format).strip().lower()
    if clean_format not in EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail="Formato de exportação inválido.")
    catalog = state.catalog
    if catalog is None:
        raise HTTPException(status_code=503, detail="O catálogo ainda não foi instalado.")
    selected_ids = split_ids(ids)
    if len(selected_ids) > _MAX_EXPORT:
        raise HTTPException(
            status_code=400,
            detail="Selecione no máximo 1000 trabalhos para exportar.",
        )
    try:
        if selected_ids:
            records = []
            for identifier in selected_ids:
                try:
                    records.append(catalog.get(identifier))
                except SearchError as exc:
                    raise HTTPException(
                        status_code=404, detail="Tese não encontrada."
                    ) from exc
        else:
            records = _collect_search_records(catalog, query)
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    body = export_records(records, clean_format)
    return Response(
        content=body.encode("utf-8"),
        media_type=_MEDIA_TYPES[clean_format],
        headers={
            "Content-Disposition": f'attachment; filename="{_FILENAMES[clean_format]}"'
        },
    )


def _collect_search_records(catalog: object, query: SearchQuery) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    page = 1
    while len(records) < _MAX_EXPORT:
        page_query = replace(query, page=page, page_size=100)
        result = catalog.search(page_query)  # type: ignore[attr-defined]
        payload = result.to_dict() if hasattr(result, "to_dict") else result
        results = payload.get("results", []) if isinstance(payload, dict) else []
        if not results:
            break
        for item in results:
            identifier = str(item.get("id") or "")
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            getter = getattr(catalog, "get", None)
            if callable(getter):
                try:
                    records.append(getter(identifier))
                except SearchError:
                    records.append(item)
            else:
                records.append(item)
            if len(records) >= _MAX_EXPORT:
                break
        pages = int(payload.get("pages") or 0) if isinstance(payload, dict) else 0
        if page >= pages:
            break
        page += 1
    return records


__all__ = ["router"]
