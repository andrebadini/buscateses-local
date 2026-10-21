"""Dependency helpers for API route modules."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from buscateses.api.state import AppState


def get_state(request: Request) -> AppState:
    """Return the application state attached by :func:`create_app`."""
    return request.app.state.ctx


StateDep = Annotated[AppState, Depends(get_state)]

__all__ = ["StateDep", "get_state"]
