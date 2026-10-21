"""Slow integration test against the real pinned ONNX model (~330 MB download)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

from buscateses.config import get_paths
from buscateses.embeddings.local_onnx import (
    DIMS_FULL,
    LocalOnnxEmbedder,
    download_model,
)


def _home() -> Path:
    configured = os.getenv("BUSCATESES_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path(tempfile.mkdtemp(prefix="buscateses-slow-"))


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right))


@pytest.mark.slow
def test_real_model_download_and_embedding(real_model_available: bool) -> None:
    if not real_model_available:
        pytest.skip("defina BUSCATESES_RUN_SLOW=1 para executar")
    paths = get_paths(_home()).ensure()
    directory = download_model(paths, "quantized")
    assert (directory / "READY").is_file()

    embedder = LocalOnnxEmbedder(paths, "quantized")
    assert embedder.ready() is True

    query = embedder.embed_queries(["educação inclusiva no Brasil"])
    assert query.shape == (1, DIMS_FULL)
    assert query.dtype == np.float32
    assert np.isclose(np.linalg.norm(query[0]), 1.0, atol=1e-4)

    documents = embedder.embed_documents(
        [
            (
                "Educação inclusiva",
                "Políticas públicas e práticas pedagógicas para a inclusão "
                "de estudantes com deficiência na escola regular.",
            ),
            (
                "Computação quântica",
                "Algoritmos quânticos, correção de erros e arquiteturas de "
                "computadores quânticos.",
            ),
        ]
    )
    assert documents.shape == (2, DIMS_FULL)
    related = _cosine(query[0], documents[0])
    unrelated = _cosine(query[0], documents[1])
    assert related > unrelated, (related, unrelated)
    assert related > 0.1

    texts = [
        (f"Título {index}", f"Um texto sintético de comprimento variável {index} " * (index + 1))
        for index in range(18)
    ]
    forward = embedder.embed_documents(texts)
    backward = embedder.embed_documents(list(reversed(texts)))
    assert forward.shape == (18, DIMS_FULL)
    assert np.allclose(forward, backward[::-1], atol=2e-4)

    empty = embedder.embed_queries([])
    assert empty.shape == (0, DIMS_FULL)
