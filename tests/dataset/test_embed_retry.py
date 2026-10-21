"""Retry and adaptive-batch tests for the dataset embedding backend."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import embed
import numpy as np
import pytest


@dataclass
class FakeState:
    """Mutable behaviour of the fake embeddings server."""

    memory_status: int = 424
    max_batch: int = 4
    abort_connections: int = 0
    requests: list[int] = field(default_factory=list)
    aborted: int = 0


class _Server(ThreadingHTTPServer):
    """Threaded HTTP server carrying the mutable :class:`FakeState`."""

    daemon_threads = True
    state: FakeState


class _Handler(BaseHTTPRequestHandler):
    """Answer like a TEI server that runs out of GPU memory on big batches."""

    server: _Server

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        state = self.server.state
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        texts = payload.get("input") or []
        state.requests.append(len(texts))
        if state.abort_connections:
            state.abort_connections -= 1
            state.aborted += 1
            # Return without a response so the client sees an aborted connection.
            self.close_connection = True
            return
        if len(texts) > state.max_batch:
            self._send(
                state.memory_status,
                {
                    "error": "Backend error",
                    "detail": "CUDA_ERROR_OUT_OF_MEMORY",
                },
            )
            return
        data = [
            {
                "index": index,
                "embedding": [float(index + 1)] + [0.0] * (embed.FULL_DIMS - 1),
            }
            for index in range(len(texts))
        ]
        self._send(200, {"data": data})

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Keep the test output quiet."""


@contextmanager
def fake_server(state: FakeState) -> Iterator[str]:
    """Run the fake server on a loopback port and yield its base URL."""
    server = _Server(("127.0.0.1", 0), _Handler)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the exponential backoff so the tests stay fast."""
    monkeypatch.setattr(embed.time, "sleep", lambda _seconds: None)


def test_http_424_is_retried_and_batch_shrinks_after_three_failures() -> None:
    state = FakeState(max_batch=4)
    with fake_server(state) as url:
        backend = embed.OpenAIBackend(url, batch_size=32, concurrency=1)
        matrix = backend.embed_texts([f"documento {index}" for index in range(8)])

    assert matrix.shape == (8, embed.FULL_DIMS)
    assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0)
    assert backend.batch_size == 16
    # Three 424s for the original batch, then two smaller successful calls.
    assert state.requests[:3] == [8, 8, 8]
    assert state.requests[3:] == [4, 4]


def test_aborted_connection_is_retried() -> None:
    state = FakeState(abort_connections=2)
    with fake_server(state) as url:
        backend = embed.OpenAIBackend(url, batch_size=4, concurrency=1)
        matrix = backend.embed_texts(["primeiro", "segundo"])

    assert matrix.shape == (2, embed.FULL_DIMS)
    assert state.aborted == 2
    assert state.requests == [2, 2, 2]
