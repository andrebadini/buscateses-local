"""OpenAI-compatible embedding endpoint (LM Studio, Ollama, TEI, etc.)."""

from __future__ import annotations

import threading
from collections.abc import Sequence

import numpy as np
import requests

from buscateses.downloads import DEFAULT_TIMEOUT
from buscateses.embeddings.base import (
    EmbeddingUnavailable,
    format_document,
    format_query,
    truncate_normalize,
)

COMPATIBILITY_THRESHOLD = 0.97
DIMS_FULL = 768


class OpenAICompatibleEmbedder:
    """Embedder backed by a local OpenAI-compatible HTTP server."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = str(base_url).strip().rstrip("/")
        self.model = str(model).strip()
        self.api_key = str(api_key)
        self.timeout = timeout
        self.name = f"openai-compatible:{self.model or 'unknown'}"
        self.dims_full = DIMS_FULL
        self._lock = threading.RLock()
        self._session = requests.Session()

    @property
    def endpoint(self) -> str:
        """Return the embeddings endpoint."""
        if self.base_url.endswith("/embeddings"):
            return self.base_url
        return f"{self.base_url}/embeddings"

    def ready(self) -> bool:
        """Return whether an endpoint and model are configured."""
        return bool(self.base_url and self.model)

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        """Embed queries using the shared query prompt."""
        return self._embed([format_query(text) for text in texts])

    def embed_documents(self, documents: Sequence[tuple[str, str]]) -> np.ndarray:
        """Embed documents using the shared document prompt."""
        return self._embed([format_document(title, text) for title, text in documents])

    def check_compatibility(
        self,
        probe_text: str,
        probe_head: Sequence[float],
    ) -> float:
        """Return cosine similarity between the first 16 values and a probe.

        The comparison is an approximation: the app accepts the endpoint when
        this value is at least :data:`COMPATIBILITY_THRESHOLD`.
        """
        reference = np.asarray(list(probe_head)[:16], dtype=np.float32)
        if reference.size != 16:
            raise EmbeddingUnavailable("O vetor de referência do modelo é inválido.")
        # The manifest probe already contains the full query prompt.
        vector = self._embed([str(probe_text)])
        head = np.asarray(vector[0, :16], dtype=np.float32)
        denominator = float(np.linalg.norm(head) * np.linalg.norm(reference))
        if denominator <= 1e-12:
            return 0.0
        return float(np.dot(head, reference) / denominator)

    def is_compatible(self, probe_text: str, probe_head: Sequence[float]) -> bool:
        """Return whether compatibility cosine reaches the documented threshold."""
        return self.check_compatibility(probe_text, probe_head) >= COMPATIBILITY_THRESHOLD

    def _embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dims_full), dtype=np.float32)
        if not self.ready():
            raise EmbeddingUnavailable(
                "Configure a URL e o nome do modelo de embedding do servidor."
            )
        payload: dict[str, object] = {"model": self.model, "input": list(texts)}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with self._lock:
                response = self._session.post(
                    self.endpoint,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
            response.raise_for_status()
            body = response.json()
        except requests.RequestException as exc:
            raise EmbeddingUnavailable(
                "Não foi possível falar com o servidor de embeddings. "
                "Confira se ele está aberto e se a URL está correta."
            ) from exc
        except ValueError as exc:
            raise EmbeddingUnavailable(
                "O servidor de embeddings retornou uma resposta inválida."
            ) from exc

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingUnavailable(
                "O servidor de embeddings retornou uma quantidade inesperada de vetores."
            )
        ordered = data
        if all(isinstance(item, dict) and "index" in item for item in data):
            ordered = sorted(data, key=lambda item: int(item["index"]))
        rows: list[list[float]] = []
        for item in ordered:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list) or len(embedding) != self.dims_full:
                raise EmbeddingUnavailable(
                    "O servidor de embeddings não usa o modelo esperado "
                    "(são necessárias 768 dimensões)."
                )
            rows.append(embedding)
        matrix = np.asarray(rows, dtype=np.float32)
        return truncate_normalize(matrix, self.dims_full)


__all__ = [
    "COMPATIBILITY_THRESHOLD",
    "DIMS_FULL",
    "OpenAICompatibleEmbedder",
]
