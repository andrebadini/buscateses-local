#!/usr/bin/env python3
"""Compute EmbeddingGemma vectors for the normalised metadata.

Work is split into blocks of 8192 rows in the global metadata order.  Each
block is claimed atomically through a ``unit-XXXXX.lock`` file, embedded and
written to ``unit-XXXXX.parquet``; finished units are skipped, which makes the
step resumable and safe to run from several processes at once.

Two backends are supported: an OpenAI-compatible HTTP endpoint and a local
SentenceTransformer model.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from common import (
    DOCUMENT_TEMPLATE,
    EMBEDDING_MODEL,
    EMBEDDING_SCHEMA,
    FULL_DIMS,
    MAX_DOCUMENT_TOKENS,
    load_tokenizer,
    setup_logging,
)

try:
    from buscateses.textcase import truecase
except ModuleNotFoundError:  # running from a source checkout without installation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from buscateses.textcase import truecase

LOGGER = setup_logging("embed")
UNIT_ROWS = 8_192
LOCK_STALE_SECONDS = 2 * 60 * 60
HEARTBEAT_SECONDS = 5 * 60
MAX_BACKOFF_SECONDS = 60.0
MIN_BATCH_SIZE = 4
MEMORY_FAILURES_BEFORE_SHRINK = 3
_MEMORY_MARKERS = (
    "out of memory",
    "out_of_memory",
    "cuda_error_out_of_memory",
    "cuda oom",
    "hip out of memory",
    "backend error",
)
METADATA_READ_COLUMNS = ("id", "title", "abstract", "abstract_en", "keywords")


def build_document_text(
    title: str | None,
    abstract: str | None,
    keywords: str | None,
    tokenizer: Any,
    max_tokens: int = MAX_DOCUMENT_TOKENS,
) -> str:
    """Build and tokenizer-truncate the document text used for embeddings.

    The text follows SPEC §3.2: ``title: {title} | text: {text}``, where text
    is the abstract when present (Portuguese, else English) and the keywords
    otherwise.  Title, abstract
    and keywords are truecased first, so the model sees readable text instead
    of the uppercase catalogue records.  If the encoded sequence exceeds
    *max_tokens* (special tokens included), the original string is cut at the
    offset of the last kept token — ids are never decoded back to text.
    """
    title_text = truecase((title or "").strip()) or "none"
    body_text = truecase((abstract or "").strip()) or truecase((keywords or "").strip())
    text = DOCUMENT_TEMPLATE.format(title=title_text, text=body_text)
    if max_tokens <= 0:
        return ""
    encoding = tokenizer.encode(text, add_special_tokens=True)
    if len(encoding.ids) <= max_tokens:
        return text
    offsets = list(encoding.offsets)
    mask = encoding.special_tokens_mask
    if mask is None:
        mask = [1 if end == 0 else 0 for _, end in offsets]
    keep = max_tokens - sum(mask)
    if keep <= 0:
        return ""
    kept = 0
    cut = 0
    for (_, end), is_special in zip(offsets, mask, strict=True):
        if is_special:
            continue
        kept += 1
        cut = max(cut, end)
        if kept >= keep:
            break
    return text[:cut].rstrip()


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """L2-normalise each row of *matrix* in float32."""
    values = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    return values / np.maximum(norms, 1e-12)


class EmbeddingBackend(Protocol):
    """Minimal interface shared by the HTTP and local backends."""

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        """Return an ``[n, 768]`` float32, L2-normalised matrix."""


class _RetryableError(RuntimeError):
    """An HTTP error worth retrying (429, 424, 5xx, timeout, short response)."""


def _looks_like_memory_error(status_code: int, body: str) -> bool:
    """Return whether an HTTP error reports an exhausted inference backend.

    TEI answers ``424 Failed Dependency`` with ``{"error": "Backend error"}``
    when its worker crashed, usually after a CUDA out-of-memory error.
    """
    if status_code == 424:
        return True
    lowered = (body or "").lower()
    return any(marker in lowered for marker in _MEMORY_MARKERS)


class OpenAIBackend:
    """OpenAI-compatible embeddings endpoint (TEI, LM Studio, Ollama, ...)."""

    def __init__(
        self,
        url: str,
        *,
        model: str = "embeddinggemma",
        api_key: str | None = None,
        concurrency: int = 3,
        batch_size: int = 32,
        timeout: float = 120.0,
    ) -> None:
        self.endpoint = url.rstrip("/") + "/embeddings"
        self.model = model
        self.api_key = api_key
        self.concurrency = max(1, concurrency)
        self.batch_size = max(1, batch_size)
        self.timeout = timeout
        self._local = threading.local()
        self._batch_lock = threading.Lock()
        self._memory_failures = 0

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            if self.api_key:
                session.headers["Authorization"] = f"Bearer {self.api_key}"
            self._local.session = session
        return session

    def _record_memory_failure(self) -> bool:
        """Count a memory failure and shrink the batch when the threshold is hit.

        Returns ``True`` when the caller should split the failing batch; the
        configured batch size is halved (never below :data:`MIN_BATCH_SIZE`)
        so later calls also request smaller chunks.
        """
        with self._batch_lock:
            self._memory_failures += 1
            if self._memory_failures < MEMORY_FAILURES_BEFORE_SHRINK:
                return False
            self._memory_failures = 0
            if self.batch_size > MIN_BATCH_SIZE:
                self.batch_size = max(MIN_BATCH_SIZE, self.batch_size // 2)
                LOGGER.warning(
                    "Falhas seguidas de memória; reduzindo o lote para %d.",
                    self.batch_size,
                )
            return True

    def _reset_memory_failures(self) -> None:
        """Clear the consecutive memory-failure counter after a success."""
        with self._batch_lock:
            self._memory_failures = 0

    def _embed_batch(self, texts: Sequence[str]) -> np.ndarray:
        attempt = 0
        while True:
            memory_failure = False
            try:
                response = self._session().post(
                    self.endpoint,
                    json={"model": self.model, "input": list(texts)},
                    timeout=self.timeout,
                )
                status = response.status_code
                if status >= 400 and (
                    status == 424 or _looks_like_memory_error(status, response.text)
                ):
                    # TEI reports a crashed/OOM worker as 424 "Backend error".
                    memory_failure = True
                if status == 424 or status == 429 or status >= 500:
                    raise _RetryableError(f"HTTP {status}")
                if status >= 400:
                    raise RuntimeError(
                        "O servidor de embeddings recusou a requisição "
                        f"(HTTP {status}): {response.text[:300]}"
                    )
                payload = response.json()
                data = payload.get("data", payload) if isinstance(payload, dict) else payload
                if not isinstance(data, list):
                    raise _RetryableError("Resposta de embeddings sem lista 'data'")
                ordered = sorted(data, key=lambda item: item.get("index", 0))
                vectors = [item["embedding"] for item in ordered]
                if len(vectors) != len(texts):
                    raise _RetryableError(
                        f"Esperados {len(texts)} vetores, recebidos {len(vectors)}"
                    )
                matrix = np.asarray(vectors, dtype=np.float32)
                if matrix.ndim != 2 or matrix.shape[1] != FULL_DIMS:
                    raise RuntimeError(
                        f"Embedding com dimensão inesperada: {matrix.shape}; esperado 768"
                    )
                self._reset_memory_failures()
                return _l2_normalize(matrix)
            except (
                requests.RequestException,
                ConnectionError,
                ValueError,
                KeyError,
                _RetryableError,
            ) as exc:
                attempt += 1
                delay = min(MAX_BACKOFF_SECONDS, 2.0 ** min(attempt, 6))
                delay *= random.uniform(0.5, 1.5)
                if attempt == 1 or attempt % 10 == 0:
                    LOGGER.warning(
                        "Falha no backend OpenAI (tentativa %d): %s; nova tentativa em %.1fs",
                        attempt,
                        exc,
                        delay,
                    )
                time.sleep(delay)
                if (
                    memory_failure
                    and len(texts) > MIN_BATCH_SIZE
                    and self._record_memory_failure()
                ):
                    midpoint = len(texts) // 2
                    LOGGER.warning(
                        "Dividindo o lote de %d em %d + %d após erro de memória.",
                        len(texts),
                        midpoint,
                        len(texts) - midpoint,
                    )
                    left = self._embed_batch(texts[:midpoint])
                    right = self._embed_batch(texts[midpoint:])
                    return np.vstack([left, right])

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, FULL_DIMS), dtype=np.float32)
        batches = [list(texts[index : index + self.batch_size]) for index in range(0, len(texts), self.batch_size)]
        results: list[np.ndarray | None] = [None] * len(batches)
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = {
                executor.submit(self._embed_batch, batch): index
                for index, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        return np.vstack([result for result in results if result is not None])


class SentenceTransformerBackend:
    """Local ``google/embeddinggemma-300m`` backend (MPS/CUDA/CPU)."""

    def __init__(
        self,
        *,
        model_name: str = EMBEDDING_MODEL,
        device: str = "cpu",
        batch_size: int = 32,
    ) -> None:
        from sentence_transformers import SentenceTransformer

        token = os.environ.get("HF_TOKEN") or None
        try:
            self.model = SentenceTransformer(model_name, device=device, token=token)
        except TypeError:  # pragma: no cover - older sentence-transformers
            self.model = SentenceTransformer(model_name, device=device)
        self.model.max_seq_length = MAX_DOCUMENT_TOKENS
        self.batch_size = max(1, batch_size)

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, FULL_DIMS), dtype=np.float32)
        vectors = self.model.encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[1] != FULL_DIMS:
            raise RuntimeError(
                f"Embedding com dimensão inesperada: {matrix.shape}; esperado 768"
            )
        return _l2_normalize(matrix)


def create_backend(
    name: str,
    *,
    url: str | None = None,
    api_key: str | None = None,
    model: str = "embeddinggemma",
    concurrency: int = 3,
    batch_size: int = 32,
    device: str = "cpu",
    model_name: str = EMBEDDING_MODEL,
    timeout: float = 120.0,
) -> EmbeddingBackend:
    """Instantiate a backend by name."""
    if name == "openai":
        if not url:
            raise ValueError("--url é obrigatório para o backend openai")
        return OpenAIBackend(
            url,
            model=model,
            api_key=api_key,
            concurrency=concurrency,
            batch_size=batch_size,
            timeout=timeout,
        )
    if name == "st":
        return SentenceTransformerBackend(
            model_name=model_name,
            device=device,
            batch_size=batch_size,
        )
    raise ValueError(f"Backend desconhecido: {name}")


# ---------------------------------------------------------------------------
# Metadata layout and unit claiming
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetadataLayout:
    """Ordered metadata parts and their row counts."""

    parts: tuple[Path, ...]
    rows: tuple[int, ...]

    @property
    def total_rows(self) -> int:
        return sum(self.rows)

    @property
    def total_units(self) -> int:
        return math.ceil(self.total_rows / UNIT_ROWS)


def metadata_layout(metadata_dir: Path) -> MetadataLayout:
    """Read part sizes from the Parquet footer (no data loaded)."""
    parts = tuple(sorted(Path(metadata_dir).glob("*.parquet")))
    if not parts:
        raise FileNotFoundError(f"Nenhuma parte Parquet em {metadata_dir}")
    rows = tuple(pq.ParquetFile(path).metadata.num_rows for path in parts)
    if not any(rows):
        raise ValueError("Metadados vazios")
    return MetadataLayout(parts=parts, rows=rows)


class MetadataReader:
    """Slice metadata rows by global position, caching a couple of parts."""

    def __init__(self, layout: MetadataLayout, cache_size: int = 2) -> None:
        self.layout = layout
        self.cache_size = cache_size
        self._cache: OrderedDict[Path, pa.Table] = OrderedDict()
        self._starts: list[int] = []
        total = 0
        for rows in layout.rows:
            self._starts.append(total)
            total += rows

    def _table(self, path: Path) -> pa.Table:
        table = self._cache.get(path)
        if table is not None:
            self._cache.move_to_end(path)
            return table
        table = pq.read_table(path, columns=list(METADATA_READ_COLUMNS))
        self._cache[path] = table
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return table

    def read(self, start: int, end: int) -> pa.Table:
        """Return rows ``[start, end)`` in global metadata order."""
        slices: list[pa.Table] = []
        for index, path in enumerate(self.layout.parts):
            part_start = self._starts[index]
            part_end = part_start + self.layout.rows[index]
            overlap_start = max(start, part_start)
            overlap_end = min(end, part_end)
            if overlap_start >= overlap_end:
                continue
            table = self._table(path)
            slices.append(table.slice(overlap_start - part_start, overlap_end - overlap_start))
        if not slices:
            return pa.table({name: pa.array([], type=pa.string()) for name in METADATA_READ_COLUMNS})
        return pa.concat_tables(slices)


def _unit_path(out_dir: Path, unit: int) -> Path:
    return out_dir / f"unit-{unit:05d}.parquet"


def _lock_path(out_dir: Path, unit: int) -> Path:
    return out_dir / f"unit-{unit:05d}.lock"


def _try_claim(lock_path: Path, unit: int) -> bool:
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"pid": os.getpid(), "unit": unit, "started_at": time.time()}, handle)
    return True


def claim_unit(out_dir: Path, unit: int) -> bool:
    """Atomically claim *unit*, recovering locks abandoned for over two hours."""
    lock_path = _lock_path(out_dir, unit)
    if _try_claim(lock_path, unit):
        return True
    output = _unit_path(out_dir, unit)
    try:
        age = time.time() - lock_path.stat().st_mtime
    except FileNotFoundError:
        return _try_claim(lock_path, unit)
    if age > LOCK_STALE_SECONDS and not output.exists():
        LOGGER.warning("Retomando unidade %05d com lock abandonado (%.0f min)", unit, age / 60)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        return _try_claim(lock_path, unit)
    return False


def _unit_complete(out_dir: Path, unit: int, expected_rows: int) -> bool:
    path = _unit_path(out_dir, unit)
    if not path.is_file():
        return False
    try:
        rows = pq.ParquetFile(path).metadata.num_rows
    except (OSError, pa.ArrowException):
        LOGGER.warning("Unidade %05d corrompida; será recalculada", unit)
        path.unlink(missing_ok=True)
        return False
    if rows != expected_rows:
        LOGGER.warning(
            "Unidade %05d com %d/%d linhas; será recalculada", unit, rows, expected_rows
        )
        path.unlink(missing_ok=True)
        return False
    return True


def _write_unit(path: Path, ids: Sequence[str], vectors: np.ndarray) -> None:
    """Write one unit atomically as id + float16[768]."""
    matrix = np.ascontiguousarray(vectors, dtype=np.float16)
    if matrix.shape != (len(ids), FULL_DIMS):
        raise ValueError(f"Formato de embeddings inválido: {matrix.shape}")
    embedding = pa.FixedSizeListArray.from_arrays(
        pa.array(matrix.reshape(-1), type=pa.float16()), FULL_DIMS
    )
    table = pa.Table.from_arrays(
        [pa.array(ids, type=pa.string()), embedding], schema=EMBEDDING_SCHEMA
    )
    temporary = path.with_suffix(".parquet.tmp")
    pq.write_table(table, temporary, compression="zstd", row_group_size=UNIT_ROWS)
    os.replace(temporary, path)


def _heartbeat(lock_path: Path, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            os.utime(lock_path, None)
        except OSError:
            return


def process_unit(
    unit: int,
    start: int,
    end: int,
    reader: MetadataReader,
    backend: EmbeddingBackend,
    tokenizer: Any,
    out_dir: Path,
) -> None:
    """Embed and persist a single unit."""
    lock_path = _lock_path(out_dir, unit)
    stop = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat, args=(lock_path, stop), daemon=True)
    heartbeat.start()
    try:
        table = reader.read(start, end)
        ids = table.column("id").to_pylist()
        if len(ids) != end - start:
            raise RuntimeError(f"Unidade {unit}: esperadas {end - start} linhas, obtidas {len(ids)}")
        titles = table.column("title").to_pylist()
        # Recent BDTD records sometimes carry only the English abstract; the
        # model is multilingual, so it is a better body than the keywords.
        abstracts = [
            abstract or abstract_en
            for abstract, abstract_en in zip(
                table.column("abstract").to_pylist(),
                table.column("abstract_en").to_pylist(),
                strict=True,
            )
        ]
        keywords = table.column("keywords").to_pylist()
        texts = [
            build_document_text(title, abstract, keyword, tokenizer)
            for title, abstract, keyword in zip(titles, abstracts, keywords, strict=True)
        ]
        vectors = backend.embed_texts(texts)
        if len(vectors) != len(ids):
            raise RuntimeError(
                f"Unidade {unit}: {len(vectors)} vetores para {len(ids)} documentos"
            )
        _write_unit(_unit_path(out_dir, unit), ids, vectors)
    finally:
        stop.set()
        lock_path.unlink(missing_ok=True)


def embed_metadata(
    metadata_dir: Path,
    out_dir: Path,
    backend: EmbeddingBackend,
    tokenizer: Any,
    *,
    max_units: int | None = None,
) -> int:
    """Run units until done (or *max_units* processed by this process)."""
    layout = metadata_layout(metadata_dir)
    reader = MetadataReader(layout)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total_units = layout.total_units
    processed = 0
    started = time.monotonic()
    docs_processed = 0

    LOGGER.info(
        "Metadados: %d linha(s) em %d unidade(s) de %d linhas",
        layout.total_rows,
        total_units,
        UNIT_ROWS,
    )
    for unit in range(total_units):
        if max_units is not None and processed >= max_units:
            break
        start = unit * UNIT_ROWS
        end = min(start + UNIT_ROWS, layout.total_rows)
        expected = end - start
        if _unit_complete(out_dir, unit, expected):
            _lock_path(out_dir, unit).unlink(missing_ok=True)
            continue
        if not claim_unit(out_dir, unit):
            continue
        process_unit(unit, start, end, reader, backend, tokenizer, out_dir)
        processed += 1
        docs_processed += expected
        elapsed = max(time.monotonic() - started, 1e-6)
        done = sum(
            1
            for index in range(total_units)
            if _unit_path(out_dir, index).is_file()
        )
        rate = docs_processed / elapsed
        remaining = max(total_units - done, 0)
        eta = (remaining * UNIT_ROWS) / rate if rate > 0 else float("inf")
        LOGGER.info(
            "Unidade %05d concluída — %d/%d unidades, %.1f docs/s, ETA %.1f min",
            unit,
            done,
            total_units,
            rate,
            eta / 60,
        )

    done = sum(
        1 for index in range(total_units) if _unit_path(out_dir, index).is_file()
    )
    if done < total_units:
        LOGGER.info(
            "Parando com %d/%d unidades (as restantes podem ser retomadas)", done, total_units
        )
    else:
        LOGGER.info("Todas as %d unidades estão concluídas", total_units)
    return processed


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    default_model_dir = Path(__file__).resolve().parent / "work" / "model"
    parser = argparse.ArgumentParser(description="Gera embeddings do catálogo CAPES")
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--backend", choices=("openai", "st"), required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--api-key-env", default="TEI_API_KEY")
    parser.add_argument("--model", default="embeddinggemma")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--device", choices=("mps", "cuda", "cpu"), default="cpu")
    parser.add_argument("--model-name", default=EMBEDDING_MODEL)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--tokenizer-dir", type=Path, default=default_model_dir)
    parser.add_argument("--max-units", type=int, default=None)
    arguments = parser.parse_args(argv)

    api_key = os.environ.get(arguments.api_key_env) if arguments.backend == "openai" else None
    backend = create_backend(
        arguments.backend,
        url=arguments.url,
        api_key=api_key,
        model=arguments.model,
        concurrency=arguments.concurrency,
        batch_size=arguments.batch,
        device=arguments.device,
        model_name=arguments.model_name,
        timeout=arguments.timeout,
    )
    tokenizer = load_tokenizer(arguments.tokenizer_dir)
    embed_metadata(
        arguments.metadata,
        arguments.out,
        backend,
        tokenizer,
        max_units=arguments.max_units,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
