"""Personal library HTTP routes (SPEC §8)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from buscateses.api.deps import StateDep
from buscateses.library.extract import SUPPORTED_EXTENSIONS
from buscateses.library.ingest import reindex_missing
from buscateses.library.store import (
    ATTACHMENT_KINDS,
    MAX_FILE_BYTES,
    LibraryError,
    LibraryNotFoundError,
    media_type_for,
    safe_filename,
    sha256_file,
)

router = APIRouter(prefix="/api/library", tags=["library"])
_UPLOAD_BLOCK = 1024 * 1024
#: Reject oversized multipart bodies before reading a single byte.
MAX_REQUEST_BYTES = 520 * 1024 * 1024
_CSP_SANDBOX = "sandbox; default-src 'none'"


class DocumentUpdate(BaseModel):
    """Editable metadata for one library document."""

    title: str | None = None
    author: str | None = None
    year: int | None = None


def _stream_upload(upload: UploadFile, destination: Path) -> int:
    size = 0
    with destination.open("wb") as output:
        while block := upload.file.read(_UPLOAD_BLOCK):
            size += len(block)
            if size > MAX_FILE_BYTES:
                raise LibraryError("O arquivo é maior que o limite de 500 MB.")
            output.write(block)
        output.flush()
    return size


@router.post("/upload")
def upload(
    files: Annotated[list[UploadFile], File(description="Arquivos da biblioteca")],
    state: StateDep,
    request: Request,
) -> dict[str, Any]:
    """Receive multipart files, streaming each one to a temporary file."""
    length = request.headers.get("content-length")
    if length:
        try:
            too_large = int(length) > MAX_REQUEST_BYTES
        except ValueError:
            too_large = False
        if too_large:
            raise HTTPException(
                status_code=413,
                detail="O envio é maior que o limite de 520 MB.",
            )
    if not files:
        raise HTTPException(status_code=400, detail="Selecione ao menos um arquivo.")
    jobs: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    accepted: set[str] = set()
    for upload_file in files:
        filename = safe_filename(upload_file.filename)
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            skipped.append(
                {
                    "filename": filename,
                    "reason": "Formato não suportado. Envie PDF, EPUB, DOCX, TXT, Markdown ou HTML.",
                }
            )
            continue
        descriptor, temporary = tempfile.mkstemp(
            prefix=".upload-", suffix=".part", dir=state.paths.library_dir
        )
        temporary_path = Path(temporary)
        try:
            os.close(descriptor)
            size = _stream_upload(upload_file, temporary_path)
            if size == 0:
                skipped.append({"filename": filename, "reason": "O arquivo está vazio."})
                temporary_path.unlink(missing_ok=True)
                continue
            digest = sha256_file(temporary_path)
            existing = state.library.find_by_sha256(digest)
            if existing is not None or digest in accepted:
                skipped.append(
                    {"filename": filename, "reason": "Este arquivo já está na biblioteca."}
                )
                temporary_path.unlink(missing_ok=True)
                continue
            job_id = state.ingestor.submit(
                state.jobs,
                temporary_path,
                filename=filename,
                remove_source=True,
            )
            accepted.add(digest)
            jobs.append({"job_id": job_id, "filename": filename})
        except LibraryError as exc:
            temporary_path.unlink(missing_ok=True)
            skipped.append({"filename": filename, "reason": str(exc)})
        except OSError as exc:
            temporary_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=500,
                detail="Não foi possível salvar o arquivo enviado.",
            ) from exc
    return {"jobs": jobs, "skipped": skipped}


@router.get("/documents")
def list_documents(state: StateDep) -> list[dict[str, Any]]:
    """List documents in the personal library."""
    return state.library.list_documents()


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, state: StateDep) -> dict[str, Any]:
    """Return one library document."""
    document = state.library.get_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    return document


@router.patch("/documents/{doc_id}")
def update_document(doc_id: str, payload: DocumentUpdate, state: StateDep) -> dict[str, Any]:
    """Update title, author or year of a document."""
    try:
        return state.library.update_document(
            doc_id,
            title=payload.title,
            author=payload.author,
            year=payload.year,
        )
    except LibraryError as exc:
        status = 404 if _not_found(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, state: StateDep) -> dict[str, Any]:
    """Delete a document, its chunks, vectors and stored file."""
    if not state.library.delete_document(doc_id):
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    return {"ok": True, "id": doc_id}


@router.get("/documents/{doc_id}/file")
def document_file(doc_id: str, state: StateDep) -> FileResponse:
    """Serve a stored file with a policy that can never execute scripts.

    Only PDFs stay inline in the browser viewer. Text-like formats are sent
    as ``text/plain`` and non-PDF responses are sandboxed, so an uploaded HTML
    file cannot run same-origin script against the local API. PDF responses get
    no CSP on purpose: Chromium and WebView2 refuse to render PDFs delivered
    with ``Content-Security-Policy: sandbox``.
    """
    try:
        path = state.library.document_file(doc_id)
        document = state.library.get_document(doc_id) or {}
    except LibraryError as exc:
        status = 404 if _not_found(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    kind = str(document.get("kind") or "")
    filename = str(document.get("filename") or path.name)
    safe_name = filename.replace("\r", " ").replace("\n", " ")
    ascii_name = safe_name.encode("ascii", "replace").decode("ascii").replace('"', "_")
    attachment = kind in ATTACHMENT_KINDS or kind not in {
        "pdf",
        "txt",
        "md",
        "html",
    }
    disposition = "attachment" if attachment else "inline"
    headers = {
        "Content-Disposition": (
            f'{disposition}; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(safe_name, safe='')}"
        ),
        "X-Content-Type-Options": "nosniff",
    }
    if kind != "pdf":
        headers["Content-Security-Policy"] = _CSP_SANDBOX
    return FileResponse(
        path,
        media_type=media_type_for(kind),
        headers=headers,
    )


def _not_found(exc: LibraryError) -> bool:
    return isinstance(exc, LibraryNotFoundError)


@router.get("/search")
def search_library(
    state: StateDep,
    q: Annotated[str, Query(min_length=1)],
    doc_id: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    """Search the library using FTS5 and, when available, vectors."""
    try:
        return state.library.search(
            q,
            embedder=state.embedder,
            doc_id=doc_id,
            page=page,
            page_size=page_size,
        )
    except LibraryError as exc:
        status = 404 if _not_found(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get("/jobs")
def library_jobs(state: StateDep) -> list[dict[str, Any]]:
    """List recent library ingestion and reindex jobs."""
    return [
        job
        for job in state.jobs.list()
        if job.get("kind") in {"library", "library_reindex"}
    ]


@router.post("/reindex")
def reindex(state: StateDep) -> dict[str, str]:
    """Start a job that embeds chunks ingested before the model was ready."""
    def run(ctx: Any) -> None:
        count = reindex_missing(state.library, state.embedder, ctx=ctx)
        message = (
            "Todos os trechos já têm vetores."
            if count == 0
            else f"{count} trecho(s) receberam vetores."
        )
        ctx.update(stage="done", message=message, progress=1.0)

    try:
        job_id = state.jobs.submit("library_reindex", run, exclusive=True)
    except Exception as exc:  # handled by the global API error layer
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id}


__all__ = ["router"]
