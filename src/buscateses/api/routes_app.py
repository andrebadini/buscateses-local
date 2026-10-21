"""Desktop lifecycle routes."""

from __future__ import annotations

import inspect

from fastapi import APIRouter, HTTPException

from buscateses.api.deps import StateDep
from buscateses.desktop import open_folder

router = APIRouter(prefix="/api", tags=["app"])


@router.post("/app/quit")
async def quit_app(state: StateDep) -> dict:
    """Signal the injected desktop shutdown callback, if any."""
    callback = state.quit_callback
    if callback is None:
        return {"ok": False, "message": "Encerramento não configurado neste modo."}
    result = callback()
    if inspect.isawaitable(result):
        await result
    return {"ok": True, "message": "Encerrando o BuscaTeses…"}


@router.post("/app/open-data-dir")
def open_data_dir(state: StateDep) -> dict:
    """Open the user data directory in the system file manager."""
    state.paths.ensure()
    try:
        open_folder(state.paths.home)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Não foi possível abrir a pasta de dados.",
        ) from exc
    return {"ok": True, "path": str(state.paths.home)}


__all__ = ["router"]
