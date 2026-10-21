"""Favorites ("Minha seleção") routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from buscateses.api.deps import StateDep
from buscateses.favorites import Favorites, FavoritesError

router = APIRouter(prefix="/api", tags=["favorites"])


class FavoriteRequest(BaseModel):
    """Favorite creation payload."""

    id: str
    note: str | None = None


@router.get("/favorites")
def list_favorites(state: StateDep) -> list[dict]:
    """List saved theses, most recent first."""
    return Favorites(state.paths).list()


@router.post("/favorites")
def add_favorite(payload: FavoriteRequest, state: StateDep) -> dict:
    """Add a thesis to the selection."""
    try:
        added = Favorites(state.paths).add(payload.id, payload.note)
    except FavoritesError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "added": added, "id": payload.id}


@router.delete("/favorites/{thesis_id}")
def remove_favorite(thesis_id: str, state: StateDep) -> dict:
    """Remove a thesis from the selection."""
    try:
        removed = Favorites(state.paths).remove(thesis_id)
    except FavoritesError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "removed": removed, "id": thesis_id}


__all__ = ["router"]
