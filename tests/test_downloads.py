from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from buscateses.downloads import (
    ChecksumMismatchError,
    DownloadError,
    SizeMismatchError,
    copy_local_file,
    dataset_file_source,
    download_file,
    fetch_dataset_file,
    hf_dataset_url,
    hf_model_url,
)


class _RangeHandler(BaseHTTPRequestHandler):
    payload = b""
    ranges: list[int] = []
    fail_first = 0
    requests = 0

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        type(self).requests += 1
        range_header = self.headers.get("Range", "")
        start = 0
        if range_header.startswith("bytes="):
            start = int(range_header.removeprefix("bytes=").split("-", 1)[0])
        type(self).ranges.append(start)
        if type(self).fail_first > 0:
            type(self).fail_first -= 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.payload)))
            self.end_headers()
            self.wfile.write(self.payload[: len(self.payload) // 2])
            self.wfile.flush()
            self.close_connection = True
            return
        body = self.payload[start:]
        if start:
            self.send_response(206)
            self.send_header(
                "Content-Range",
                f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}",
            )
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        del args


@pytest.fixture
def http_payload():
    payload = bytes(range(256)) * 1024  # 256 KiB, deterministic
    _RangeHandler.payload = payload
    _RangeHandler.ranges = []
    _RangeHandler.requests = 0
    _RangeHandler.fail_first = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield payload, f"http://127.0.0.1:{server.server_port}/arquivo.bin"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_download_resumes_after_interruption(http_payload, tmp_path: Path) -> None:
    payload, url = http_payload
    _RangeHandler.fail_first = 1
    destination = tmp_path / "arquivo.bin"
    progress: list[tuple[int, int | None]] = []
    download_file(
        url,
        destination,
        expected_bytes=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        progress=lambda done, total: progress.append((done, total)),
        retries=3,
    )
    assert destination.read_bytes() == payload
    assert any(start > 0 for start in _RangeHandler.ranges)
    assert progress[-1][0] == len(payload)


def test_wrong_hash_fails_and_cleans_partial(http_payload, tmp_path: Path) -> None:
    payload, url = http_payload
    destination = tmp_path / "arquivo.bin"
    with pytest.raises(ChecksumMismatchError, match="sha256"):
        download_file(
            url,
            destination,
            expected_bytes=len(payload),
            expected_sha256="0" * 64,
            retries=2,
        )
    assert not destination.exists()
    assert not destination.with_name("arquivo.bin.part").exists()
    assert _RangeHandler.requests == 2


def test_existing_valid_file_is_not_downloaded(http_payload, tmp_path: Path) -> None:
    payload, url = http_payload
    destination = tmp_path / "arquivo.bin"
    destination.write_bytes(payload)
    download_file(
        url,
        destination,
        expected_bytes=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert _RangeHandler.requests == 0


def test_wrong_size_is_rejected(http_payload, tmp_path: Path) -> None:
    payload, url = http_payload
    with pytest.raises(SizeMismatchError, match="tamanho"):
        download_file(url, tmp_path / "x.bin", expected_bytes=len(payload) + 1, retries=1)


def test_local_copy_and_dataset_source(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    (root / "metadata").mkdir(parents=True)
    source = root / "metadata" / "part-000.parquet"
    content = b"conteudo" * 100
    source.write_bytes(content)
    destination = tmp_path / "download" / "part-000.parquet"
    copy_local_file(
        source,
        destination,
        expected_bytes=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )
    assert destination.read_bytes() == content
    resolved = dataset_file_source(str(root), "repo/x", "main", "metadata/part-000.parquet")
    assert Path(resolved) == source
    fetched = tmp_path / "fetch.bin"
    fetch_dataset_file(
        str(root),
        "repo/x",
        "main",
        "metadata/part-000.parquet",
        fetched,
        expected_bytes=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )
    assert fetched.read_bytes() == content


def test_urls() -> None:
    assert (
        hf_model_url("org/model", "rev", "onnx/model.onnx")
        == "https://huggingface.co/org/model/resolve/rev/onnx/model.onnx"
    )
    assert (
        hf_dataset_url("org/data", "rev", "manifest.json")
        == "https://huggingface.co/datasets/org/data/resolve/rev/manifest.json"
    )


def test_unsafe_dataset_path_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    root.mkdir()
    with pytest.raises(DownloadError):
        dataset_file_source(str(root), "repo", "main", "../fora.parquet")
