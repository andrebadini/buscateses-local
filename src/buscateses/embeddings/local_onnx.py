"""Local EmbeddingGemma inference with ONNX Runtime."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from buscateses.downloads import download_file, hf_model_url
from buscateses.embeddings.base import (
    EmbeddingUnavailable,
    format_document,
    format_query,
    truncate_normalize,
)

if TYPE_CHECKING:
    from buscateses.config import Paths

MODEL_REPO = "onnx-community/embeddinggemma-300m-ONNX"
MODEL_REVISION = "5090578d9565bb06545b4552f76e6bc2c93e4a66"

DOCUMENT_MAX_TOKENS = 512
QUERY_MAX_TOKENS = 128
BATCH_SIZE = 16
DIMS_FULL = 768


@dataclass(frozen=True)
class ModelFile:
    """A pinned model file."""

    path: str
    bytes: int
    sha256: str


TOKENIZER_FILE = ModelFile(
    "tokenizer.json",
    20_323_312,
    "4dda02faaf32bc91031dc8c88457ac272b00c1016cc679757d1c441b248b9c47",
)

MODEL_FILES: dict[str, tuple[ModelFile, ...]] = {
    "quantized": (
        TOKENIZER_FILE,
        ModelFile(
            "onnx/model_quantized.onnx",
            567_874,
            "172efde319fe1542dc41f31be6154910b05b78f7a861c265c4600eec906bd6d8",
        ),
        ModelFile(
            "onnx/model_quantized.onnx_data",
            308_890_624,
            "705626e28e4c23c82ade34566b4197d97f534c12275fa406dfb71e9937d388c0",
        ),
    ),
    "fp32": (
        TOKENIZER_FILE,
        ModelFile(
            "onnx/model.onnx",
            479_932,
            "ea91fd315a7c152d427d231746f0f811a1ac93beaba656abfdf2b24e091265e4",
        ),
        ModelFile(
            "onnx/model.onnx_data",
            1_234_521_088,
            "ef835ae565d8695236652475903078e8ed794c7c35faf1164d78ec3238e8a88d",
        ),
    ),
}


class ModelDownloadError(RuntimeError):
    """Raised when model files cannot be downloaded or validated."""


def model_directory(paths: Paths | str | Path) -> Path:
    """Return the model directory for a :class:`Paths` object or path."""
    if hasattr(paths, "model_dir"):
        return Path(paths.model_dir)  # type: ignore[union-attr]
    return Path(paths)


def model_file_path(paths: Paths | str | Path, file: ModelFile) -> Path:
    """Resolve a model file inside the local model directory."""
    return model_directory(paths) / file.path


def download_model(
    paths: Paths | str | Path,
    variant: str = "quantized",
    ctx: Any | None = None,
) -> Path:
    """Download and verify all files for ``variant``, then write ``READY``.

    ``ctx`` is an optional JobContext-like object with ``update`` and
    ``check_cancelled`` methods.
    """
    if variant not in MODEL_FILES:
        raise ModelDownloadError("Variante de modelo inválida. Use 'quantized' ou 'fp32'.")
    directory = model_directory(paths)
    directory.mkdir(parents=True, exist_ok=True)
    files = MODEL_FILES[variant]
    total_bytes = sum(item.bytes for item in files)
    completed = 0
    if ctx is not None:
        ctx.update(
            stage="download",
            message="Baixando o modelo de embedding…",
            bytes_done=0,
            bytes_total=total_bytes,
            progress=0.0,
        )
    for item in files:
        if ctx is not None:
            ctx.check_cancelled()
        destination = directory / item.path
        base = completed

        def progress(
            done: int,
            total: int | None,
            *,
            base: int = base,
            item_bytes: int = item.bytes,
        ) -> None:
            if ctx is not None:
                overall = base + min(done, item_bytes)
                fraction = overall / total_bytes if total_bytes else 1.0
                ctx.update(
                    bytes_done=overall,
                    bytes_total=total_bytes,
                    progress=min(fraction, 1.0),
                    message="Baixando o modelo de embedding…",
                )

        try:
            download_file(
                hf_model_url(MODEL_REPO, MODEL_REVISION, item.path),
                destination,
                expected_bytes=item.bytes,
                expected_sha256=item.sha256,
                progress=progress,
            )
        except Exception as exc:
            raise ModelDownloadError(
                "Não foi possível baixar o modelo de embedding. "
                "Verifique a conexão e tente novamente."
            ) from exc
        completed += item.bytes

    if ctx is not None:
        ctx.update(
            stage="verify",
            message="Verificando o modelo…",
            bytes_done=total_bytes,
            bytes_total=total_bytes,
            progress=1.0,
        )
        ctx.check_cancelled()
    _write_ready(directory, variant)
    if ctx is not None:
        ctx.update(
            stage="done",
            message="Modelo de embedding pronto.",
            bytes_done=total_bytes,
            bytes_total=total_bytes,
            progress=1.0,
        )
    return directory


def _write_ready(directory: Path, variant: str) -> None:
    destination = directory / "READY"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"variant": variant, "model": MODEL_REPO, "revision": MODEL_REVISION}),
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def _ready_variant(directory: Path) -> str | None:
    marker = directory / "READY"
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(payload, dict):
        value = payload.get("variant")
        return str(value) if value else ""
    return ""


class LocalOnnxEmbedder:
    """Lazy, thread-safe local EmbeddingGemma ONNX embedder."""

    def __init__(self, model_dir: Paths | str | Path, variant: str = "quantized") -> None:
        if variant not in MODEL_FILES:
            raise ValueError("Variante de modelo inválida. Use 'quantized' ou 'fp32'.")
        self.model_dir = model_directory(model_dir)
        self.variant = variant
        self.name = "embeddinggemma-300m-onnx"
        self.dims_full = DIMS_FULL
        self._lock = threading.RLock()
        self._session: ort.InferenceSession | None = None
        self._tokenizer: Tokenizer | None = None

    def ready(self) -> bool:
        """Return whether pinned files and the READY marker are present."""
        marker_variant = _ready_variant(self.model_dir)
        if marker_variant is None:
            return False
        if marker_variant and marker_variant != self.variant:
            return False
        for item in MODEL_FILES[self.variant]:
            path = self.model_dir / item.path
            if not path.is_file() or path.stat().st_size != item.bytes:
                return False
        return True

    def warm_up(self) -> None:
        """Load the tokenizer and ONNX session ahead of the first query."""
        with self._lock:
            self._ensure_loaded()
            # A tiny inference also warms the allocator and CPU kernels.
            self._embed(["aquecimento"], max_tokens=QUERY_MAX_TOKENS)

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        """Embed query strings with the query prompt and 128-token limit."""
        prompts = [format_query(text) for text in texts]
        return self._embed(prompts, max_tokens=QUERY_MAX_TOKENS)

    def embed_documents(self, documents: Sequence[tuple[str, str]]) -> np.ndarray:
        """Embed documents with the document prompt and 512-token limit."""
        prompts = [format_document(title, text) for title, text in documents]
        return self._embed(prompts, max_tokens=DOCUMENT_MAX_TOKENS)

    def _embed(self, texts: Sequence[str], *, max_tokens: int) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dims_full), dtype=np.float32)
        with self._lock:
            session, tokenizer = self._ensure_loaded()
            tokenizer.enable_truncation(max_length=max_tokens)
            tokenizer.enable_padding(pad_id=0, pad_token="<pad>")

            order = sorted(range(len(texts)), key=lambda index: len(texts[index]))
            pieces: list[np.ndarray] = []
            for start in range(0, len(order), BATCH_SIZE):
                indexes = order[start : start + BATCH_SIZE]
                batch = [texts[index] for index in indexes]
                encoded = tokenizer.encode_batch(batch)
                input_ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
                attention_mask = (input_ids != 0).astype(np.int64)
                feeds = _build_feeds(session, input_ids, attention_mask)
                output_name = _sentence_embedding_output(session)
                try:
                    values = session.run([output_name], feeds)[0]
                except Exception as exc:
                    raise EmbeddingUnavailable(
                        "Não foi possível executar o modelo de embedding local."
                    ) from exc
                matrix = np.asarray(values, dtype=np.float32)
                if matrix.ndim == 1:
                    matrix = matrix.reshape(1, -1)
                if matrix.ndim != 2 or matrix.shape[1] != self.dims_full:
                    raise EmbeddingUnavailable(
                        "O modelo de embedding local retornou vetores em formato inesperado."
                    )
                pieces.append(matrix)
            combined = np.concatenate(pieces, axis=0)
            restored = np.empty_like(combined)
            for position, original_index in enumerate(order):
                restored[original_index] = combined[position]
            return truncate_normalize(restored, self.dims_full)

    def _ensure_loaded(self) -> tuple[ort.InferenceSession, Tokenizer]:
        if not self.ready():
            raise EmbeddingUnavailable(
                "O modelo de embedding local ainda não foi baixado. "
                "Execute a configuração inicial para baixá-lo."
            )
        if self._session is None or self._tokenizer is None:
            try:
                tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
                options = ort.SessionOptions()
                options.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
                options.inter_op_num_threads = 1
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                options.log_severity_level = 3
                model_path = self.model_dir / next(
                    item.path
                    for item in MODEL_FILES[self.variant]
                    if item.path.endswith(".onnx")
                )
                session = ort.InferenceSession(
                    str(model_path),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
            except Exception as exc:
                raise EmbeddingUnavailable(
                    "Não foi possível carregar o modelo de embedding local."
                ) from exc
            self._session = session
            self._tokenizer = tokenizer
        return self._session, self._tokenizer


def _build_feeds(
    session: ort.InferenceSession,
    input_ids: np.ndarray,
    attention_mask: np.ndarray,
) -> dict[str, np.ndarray]:
    names = {item.name for item in session.get_inputs()}
    feeds: dict[str, np.ndarray] = {}
    if "input_ids" in names:
        feeds["input_ids"] = input_ids
    if "attention_mask" in names:
        feeds["attention_mask"] = attention_mask
    if not feeds:
        raise EmbeddingUnavailable(
            "O modelo de embedding local não aceita as entradas esperadas."
        )
    return feeds


def _sentence_embedding_output(session: ort.InferenceSession) -> str:
    names = [item.name for item in session.get_outputs()]
    if "sentence_embedding" in names:
        return "sentence_embedding"
    if not names:
        raise EmbeddingUnavailable("O modelo de embedding local não produziu resultados.")
    return names[0]


__all__ = [
    "BATCH_SIZE",
    "DIMS_FULL",
    "DOCUMENT_MAX_TOKENS",
    "MODEL_FILES",
    "MODEL_REPO",
    "MODEL_REVISION",
    "ModelDownloadError",
    "ModelFile",
    "QUERY_MAX_TOKENS",
    "TOKENIZER_FILE",
    "LocalOnnxEmbedder",
    "download_model",
    "model_directory",
]
