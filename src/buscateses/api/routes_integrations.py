"""AI assistant integration routes (SPEC §8/§10)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from buscateses.api.deps import StateDep
from buscateses.integrations import (
    IntegrationError,
    connect,
    disconnect,
    list_integrations,
)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("")
def integrations(state: StateDep) -> list[dict[str, Any]]:
    """List supported clients with detection and connection state."""
    return list_integrations(data_home=state.paths.home)


@router.post("/{client_id}/connect")
def connect_client(client_id: str, state: StateDep) -> dict[str, Any]:
    """Configure one client to launch the BuscaTeses MCP server."""
    try:
        return connect(client_id, data_home=state.paths.home).to_dict()
    except IntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{client_id}/disconnect")
def disconnect_client(client_id: str, state: StateDep) -> dict[str, Any]:
    """Remove the BuscaTeses entry from one client configuration."""
    del state
    try:
        return disconnect(client_id).to_dict()
    except IntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = ["router"]
