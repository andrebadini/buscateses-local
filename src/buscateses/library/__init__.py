"""Personal library: extraction, chunking, storage and ingestion (SPEC §7)."""

from __future__ import annotations

from buscateses.library.chunking import (
    CHUNK_OVERLAP,
    CHUNK_TARGET,
    TextChunk,
    chunk_pages,
)
from buscateses.library.extract import (
    SUPPORTED_EXTENSIONS,
    ExtractedPage,
    ExtractionError,
    ExtractionResult,
    extract_document,
)
from buscateses.library.ingest import IngestOutcome, LibraryIngestor, reindex_missing
from buscateses.library.store import (
    LibraryError,
    LibraryStore,
    media_type_for,
    safe_filename,
    sha256_file,
)

__all__ = [
    "CHUNK_OVERLAP",
    "CHUNK_TARGET",
    "SUPPORTED_EXTENSIONS",
    "ExtractedPage",
    "ExtractionError",
    "ExtractionResult",
    "IngestOutcome",
    "LibraryError",
    "LibraryIngestor",
    "LibraryStore",
    "TextChunk",
    "chunk_pages",
    "extract_document",
    "media_type_for",
    "reindex_missing",
    "safe_filename",
    "sha256_file",
]
