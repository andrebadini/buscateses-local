"""PyInstaller entry point for the desktop application (SPEC §11)."""

from __future__ import annotations

from buscateses.cli import main

if __name__ == "__main__":
    raise SystemExit(main([]))
