#!/usr/bin/env python3
"""Harvest the CAPES open-data CSVs via the CKAN API.

Downloads every CSV resource from the four packages listed in SPEC §3, with
resumable downloads (HTTP Range), five attempts per resource and a SHA-256
manifest written to ``_sources.json``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import socket
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from common import (
    CKAN_BASE_URL,
    CKAN_PACKAGE_SHOW,
    PACKAGES,
    SOURCE_NAME,
    SOURCE_URL,
    setup_logging,
    sha256_file,
)
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = setup_logging("harvest")
CAPES_HOST = "dadosabertos.capes.gov.br"
MAX_ATTEMPTS = 5
CHUNK_SIZE = 1 << 20


class HarvestError(RuntimeError):
    """A resource or the CKAN API could not be fetched."""


@contextlib.contextmanager
def force_capes_ip(ip: str | None) -> Iterator[None]:
    """Temporarily resolve the CAPES host to *ip* (needed on some networks)."""
    if not ip:
        yield
        return
    original = socket.getaddrinfo

    def patched(host: str, *args: Any, **kwargs: Any):
        if host == CAPES_HOST:
            host = ip
        return original(host, *args, **kwargs)

    socket.getaddrinfo = patched  # type: ignore[assignment]
    LOGGER.info("Forçando %s -> %s", CAPES_HOST, ip)
    try:
        yield
    finally:
        socket.getaddrinfo = original  # type: ignore[assignment]


def build_session() -> requests.Session:
    """Create a session with connection pooling and transport-level retries."""
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = "buscateses-dataset-harvester/1.0"
    return session


def fetch_package(session: requests.Session, package: str) -> dict[str, Any]:
    """Return the CKAN ``result`` object for *package* (with retries)."""
    params = {"id": package}
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(CKAN_PACKAGE_SHOW, params=params, timeout=(15, 60))
            response.raise_for_status()
            payload = response.json()
            if not payload.get("success"):
                raise HarvestError(f"CKAN recusou o pacote {package}")
            return payload["result"]
        except (requests.RequestException, ValueError, KeyError, HarvestError) as exc:
            last_error = exc
            delay = min(30.0, 2.0**attempt) + random.uniform(0, 1)
            LOGGER.warning(
                "Falha ao consultar %s (tentativa %d/%d): %s", package, attempt, MAX_ATTEMPTS, exc
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(delay)
    raise HarvestError(f"Não foi possível consultar o pacote {package}: {last_error}")


def csv_resources(package: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the CSV resources of a CKAN package."""
    resources: list[dict[str, Any]] = []
    for resource in package.get("resources", []):
        url = str(resource.get("url") or "")
        fmt = str(resource.get("format") or "").strip().upper()
        if fmt == "CSV" or urlparse(url).path.casefold().endswith(".csv"):
            resources.append(resource)
    return resources


def _content_range_total(response: requests.Response) -> int | None:
    """Parse ``Content-Range: bytes a-b/total`` and return *total*."""
    value = response.headers.get("Content-Range", "")
    _, _, total = value.rpartition("/")
    return int(total) if total.isdigit() else None


def download_resource(
    session: requests.Session,
    url: str,
    destination: Path,
    expected_size: int | None = None,
) -> None:
    """Download *url* to *destination*, resuming a partial file when possible."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if expected_size and destination.is_file() and destination.stat().st_size == expected_size:
        LOGGER.info("Já completo: %s", destination.name)
        return

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        local_size = destination.stat().st_size if destination.is_file() else 0
        if expected_size is not None and local_size > expected_size:
            LOGGER.warning("Arquivo maior que o esperado; recomeçando %s", destination.name)
            destination.unlink(missing_ok=True)
            local_size = 0
        headers = {"Range": f"bytes={local_size}-"} if local_size else {}
        try:
            with session.get(url, headers=headers, stream=True, timeout=(30, 300)) as response:
                if response.status_code == 416:
                    LOGGER.info("Servidor indica que %s já está completo", destination.name)
                    return
                if response.status_code in {429} or response.status_code >= 500:
                    raise HarvestError(f"HTTP {response.status_code}")
                response.raise_for_status()
                append = local_size > 0 and response.status_code == 206
                if not append:
                    local_size = 0
                mode = "ab" if append else "wb"
                total = expected_size
                if total is None:
                    content_length = response.headers.get("Content-Length")
                    range_total = _content_range_total(response)
                    if range_total is not None:
                        total = range_total
                    elif content_length and content_length.isdigit() and not append:
                        total = int(content_length)
                written = local_size
                with destination.open(mode) as handle, _progress(
                    destination.name, total, local_size
                ) as progress:
                    for chunk in response.iter_content(CHUNK_SIZE):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        written += len(chunk)
                        progress(written)
                if expected_size is not None and destination.stat().st_size != expected_size:
                    raise HarvestError(
                        f"Tamanho inesperado para {destination.name}: "
                        f"{destination.stat().st_size} != {expected_size}"
                    )
                if total is not None and written < total:
                    raise HarvestError(
                        f"Download incompleto de {destination.name}: {written}/{total}"
                    )
                return
        except (requests.RequestException, OSError, HarvestError) as exc:
            last_error = exc
            delay = min(30.0, 2.0**attempt) + random.uniform(0, 1)
            LOGGER.warning(
                "Falha ao baixar %s (tentativa %d/%d): %s",
                destination.name,
                attempt,
                MAX_ATTEMPTS,
                exc,
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(delay)
    raise HarvestError(f"Não foi possível baixar {url}: {last_error}")


@contextlib.contextmanager
def _progress(label: str, total: int | None, initial: int = 0):
    """Small tqdm wrapper that yields a ``update(written)`` callback."""
    try:
        from tqdm import tqdm

        bar = tqdm(
            total=total,
            initial=initial,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=label,
            leave=False,
        )

        def update(written: int) -> None:
            bar.n = written
            bar.refresh()

        try:
            yield update
        finally:
            bar.close()
    except ImportError:  # pragma: no cover - tqdm is a declared dependency
        last = initial

        def update(written: int) -> None:
            nonlocal last
            if written - last >= 50 * CHUNK_SIZE:
                LOGGER.info("%s: %d bytes", label, written)
                last = written

        yield update


def harvest(out_dir: Path, *, capes_ip: str | None = None) -> dict[str, Any]:
    """Download all CSV resources and write ``_sources.json``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    packages_report: list[dict[str, Any]] = []
    files_report: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    with force_capes_ip(capes_ip):
        session = build_session()
        try:
            for package_name in PACKAGES:
                package = fetch_package(session, package_name)
                resources = csv_resources(package)
                package_files: list[str] = []
                LOGGER.info("Pacote %s: %d recurso(s) CSV", package_name, len(resources))
                for resource in resources:
                    url = str(resource["url"])
                    filename = Path(urlparse(url).path).name
                    if not filename:
                        filename = f"{resource.get('id', 'resource')}.csv"
                    if filename in seen_names:
                        raise HarvestError(f"Nome de arquivo duplicado entre pacotes: {filename}")
                    seen_names.add(filename)
                    expected_size = resource.get("size")
                    expected_size = int(expected_size) if expected_size else None
                    destination = out_dir / filename
                    LOGGER.info("Baixando %s", filename)
                    download_resource(session, url, destination, expected_size)
                    actual_size = destination.stat().st_size
                    if expected_size is not None and actual_size != expected_size:
                        raise HarvestError(
                            f"Tamanho de {filename} difere do catálogo: "
                            f"{actual_size} != {expected_size}"
                        )
                    files_report.append(
                        {
                            "package": package_name,
                            "resource_name": resource.get("name"),
                            "url": url,
                            "path": filename,
                            "bytes": actual_size,
                            "sha256": sha256_file(destination),
                            "last_modified": resource.get("last_modified"),
                        }
                    )
                    package_files.append(filename)
                packages_report.append(
                    {
                        "name": package_name,
                        "title": package.get("title"),
                        "url": f"{CKAN_BASE_URL}/dataset/{package_name}",
                        "resources": len(resources),
                        "files": package_files,
                    }
                )
        finally:
            session.close()

    report = {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": {"name": SOURCE_NAME, "url": SOURCE_URL, "license": "CC-BY"},
        "packages": packages_report,
        "files": files_report,
        "total_bytes": sum(item["bytes"] for item in files_report),
    }
    report_path = out_dir / "_sources.json"
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, report_path)
    LOGGER.info(
        "Concluído: %d arquivo(s), %.1f GB; manifesto em %s",
        len(files_report),
        report["total_bytes"] / 1e9,
        report_path,
    )
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Baixa os CSVs abertos da CAPES via CKAN")
    parser.add_argument("--out", type=Path, required=True, help="diretório de saída (work/raw)")
    parser.add_argument(
        "--capes-ip",
        default=None,
        help=f"força a resolução DNS de {CAPES_HOST} para este IP (ex.: 200.130.18.200)",
    )
    arguments = parser.parse_args(argv)
    harvest(arguments.out, capes_ip=arguments.capes_ip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
