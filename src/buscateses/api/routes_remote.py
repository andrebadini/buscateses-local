"""Public-address (Cloudflare Quick Tunnel) control endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from buscateses.api.deps import StateDep
from buscateses.remote import RemoteError

router = APIRouter(prefix="/api/remote", tags=["remote"])


class StartRemoteRequest(BaseModel):
    """Options for creating a public address."""

    include_library: bool = False


@router.get("")
def remote_status(state: StateDep) -> dict[str, Any]:
    """Return the current state of the public MCP address."""
    return state.remote.status()


@router.post("/start")
def start_remote(
    payload: StartRemoteRequest | None = None, *, state: StateDep
) -> dict[str, Any]:
    """Create a public address; progress is reported through ``state``."""
    include_library = bool(payload.include_library) if payload else False
    try:
        return state.remote.start(include_library)
    except RemoteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def stop_remote(*, state: StateDep) -> dict[str, Any]:
    """Close the public address and the local MCP endpoint."""
    return state.remote.stop()


@router.post("/rotate")
def rotate_remote(*, state: StateDep) -> dict[str, Any]:
    """Generate a new secret path, restarting the tunnel when it is online."""
    try:
        return state.remote.rotate()
    except RemoteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


__all__ = ["router"]
