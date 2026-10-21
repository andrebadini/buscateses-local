"""Persistent "Minha seleção" favorites in ``user.sqlite``."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from buscateses.config import Paths


class FavoritesError(ValueError):
    """Raised when a favorite cannot be stored."""


class Favorites:
    """Small thread-safe CRUD store for favorite theses."""

    def __init__(self, paths: Paths | str | Path) -> None:
        if isinstance(paths, Paths):
            self.path = paths.user_db
        else:
            candidate = Path(paths)
            self.path = candidate / "user.sqlite" if candidate.is_dir() else candidate
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS favorites(
                    id TEXT PRIMARY KEY,
                    added_at TEXT NOT NULL,
                    note TEXT
                )
                """
            )

    def add(self, thesis_id: str, note: str | None = None) -> bool:
        """Add a thesis id, returning whether a new row was inserted."""
        identifier = _identifier(thesis_id)
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO favorites(id, added_at, note) VALUES(?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                [identifier, timestamp, note],
            )
            inserted = bool(cursor.rowcount)
            if not inserted and note is not None:
                connection.execute(
                    "UPDATE favorites SET note = ? WHERE id = ?",
                    [note, identifier],
                )
            return inserted

    def remove(self, thesis_id: str) -> bool:
        """Remove a favorite, returning whether it existed."""
        identifier = _identifier(thesis_id)
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM favorites WHERE id = ?", [identifier])
            return bool(cursor.rowcount)

    def contains(self, thesis_id: str) -> bool:
        """Return whether ``thesis_id`` is a favorite."""
        identifier = _identifier(thesis_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM favorites WHERE id = ?", [identifier]
            ).fetchone()
        return row is not None

    def list(self) -> list[dict[str, Any]]:
        """List favorites, most recently added first."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT id, added_at, note FROM favorites ORDER BY added_at DESC, id ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        """Return the number of favorites."""
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT count(*) FROM favorites").fetchone()
        return int(row[0]) if row else 0


def _identifier(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise FavoritesError("Informe o identificador da tese.")
    return clean


__all__ = ["Favorites", "FavoritesError"]
