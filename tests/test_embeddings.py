from __future__ import annotations

import numpy as np
import pytest
import requests

from buscateses.config import EmbeddingSettings, Settings, get_paths
from buscateses.embeddings import (
    DOCUMENT_TEMPLATE,
    QUERY_TEMPLATE,
    LocalOnnxEmbedder,
    OpenAICompatibleEmbedder,
    build_embedder,
    format_document,
    format_query,
    get_embedder,
)
from buscateses.embeddings.base import (
    Embedder,
    EmbeddingUnavailable,
    truncate_normalize,
)


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class _Session:
    def __init__(self, vectors: list[list[float]] | None = None, error=None) -> None:
        self.vectors = vectors
        self.error = error
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs) -> _Response:
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        texts = kwargs["json"]["input"]
        vectors = self.vectors
        if vectors is None:
            vectors = [self._vector(index) for index in range(len(texts))]
        data = [
            {"index": index, "embedding": vector}
            for index, vector in enumerate(vectors)
        ]
        return _Response({"data": data})

    @staticmethod
    def _vector(index: int) -> list[float]:
        vector = [0.0] * 768
        vector[index % 768] = 1.0
        vector[(index + 1) % 768] = float(index + 1)
        return vector


def test_prompt_templates() -> None:
    assert QUERY_TEMPLATE == "task: search result | query: {query}"
    assert DOCUMENT_TEMPLATE == "title: {title} | text: {text}"
    assert format_query("teste") == "task: search result | query: teste"
    assert (
        format_document(None, None) == "title: none | text: "
    )
    assert format_document("Título", "Resumo") == "title: Título | text: Resumo"


def test_truncate_normalize() -> None:
    matrix = np.arange(1, 769, dtype=np.float32).reshape(1, 768)
    truncated = truncate_normalize(matrix, 256)
    assert truncated.shape == (1, 256)
    assert truncated.dtype == np.float32
    assert np.isclose(np.linalg.norm(truncated[0]), 1.0)
    assert np.isclose(truncated[0, 0], matrix[0, 0] / np.linalg.norm(matrix[0, :256]))
    one_dimensional = truncate_normalize(matrix[0], 256)
    assert one_dimensional.shape == (256,)
    assert np.isclose(np.linalg.norm(one_dimensional), 1.0)
    zero = np.zeros((1, 768), dtype=np.float32)
    assert not np.any(truncate_normalize(zero, 256))
    with pytest.raises(ValueError):
        truncate_normalize(matrix, 0)


def test_openai_compatible_request_shape() -> None:
    embedder = OpenAICompatibleEmbedder("http://127.0.0.1:1234/v1/", "modelo")
    session = _Session()
    embedder._session = session
    assert embedder.endpoint == "http://127.0.0.1:1234/v1/embeddings"
    result = embedder.embed_queries(["educação inclusiva"])
    assert result.shape == (1, 768)
    assert np.isclose(np.linalg.norm(result[0]), 1.0)
    call = session.calls[0]
    assert call["json"]["input"] == [format_query("educação inclusiva")]
    assert call["json"]["model"] == "modelo"
    assert call["url"].endswith("/embeddings")

    documents = embedder.embed_documents([("Título", "Resumo")])
    assert documents.shape == (1, 768)
    assert session.calls[1]["json"]["input"] == [format_document("Título", "Resumo")]


def test_openai_compatible_api_key_and_endpoint_override() -> None:
    embedder = OpenAICompatibleEmbedder(
        "http://localhost:8080/v1/embeddings", "m", "segredo"
    )
    session = _Session()
    embedder._session = session
    embedder.embed_queries(["x"])
    call = session.calls[0]
    assert call["url"] == "http://localhost:8080/v1/embeddings"
    assert call["headers"]["Authorization"] == "Bearer segredo"


def test_openai_compatible_errors() -> None:
    embedder = OpenAICompatibleEmbedder("http://host/v1", "m")
    embedder._session = _Session(error=requests.ConnectionError("fora do ar"))
    with pytest.raises(EmbeddingUnavailable, match="servidor de embeddings"):
        embedder.embed_queries(["x"])

    bad_dimensions = [[0.0] * 767]
    embedder._session = _Session(vectors=bad_dimensions)
    with pytest.raises(EmbeddingUnavailable, match="768"):
        embedder.embed_queries(["x"])

    embedder._session = _Session(vectors=[])
    with pytest.raises(EmbeddingUnavailable, match="quantidade"):
        embedder.embed_queries(["a", "b"])

    empty = embedder.embed_queries([])
    assert empty.shape == (0, 768)


def test_openai_compatibility_probe_uses_manifest_text_verbatim() -> None:
    reference = [0.0] * 768
    reference[0] = 1.0
    embedder = OpenAICompatibleEmbedder("http://host/v1", "m")
    session = _Session(vectors=[reference])
    embedder._session = session
    probe_text = "task: search result | query: educação inclusiva"
    score = embedder.check_compatibility(probe_text, reference[:16])
    assert score == pytest.approx(1.0)
    assert session.calls[0]["json"]["input"] == [probe_text]
    assert embedder.is_compatible(probe_text, reference[:16]) is True
    assert embedder.is_compatible(probe_text, [0.0, 1.0] + [0.0] * 14) is False


def test_build_embedder_factory(tmp_path) -> None:
    paths = get_paths(tmp_path / "home")
    disabled = Settings(embedding=EmbeddingSettings(engine="none"))
    assert build_embedder(disabled, paths) is None

    local_settings = Settings(
        embedding=EmbeddingSettings(engine="local", local_variant="quantized")
    )
    local = build_embedder(local_settings, paths)
    assert isinstance(local, LocalOnnxEmbedder)
    assert local.variant == "quantized"
    assert local.ready() is False
    assert isinstance(local, Embedder)
    assert get_embedder(local_settings, paths) is not None

    missing = Settings(
        embedding=EmbeddingSettings(engine="openai", base_url="", model="")
    )
    assert build_embedder(missing, paths) is None

    remote_settings = Settings(
        embedding=EmbeddingSettings(
            engine="openai", base_url="http://127.0.0.1:1234/v1", model="embed"
        )
    )
    remote = build_embedder(remote_settings, paths)
    assert isinstance(remote, OpenAICompatibleEmbedder)
    assert remote.name == "openai-compatible:embed"
    assert remote.ready() is True
