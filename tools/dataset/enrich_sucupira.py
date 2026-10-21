"""Fetch the public Sucupira detail page of recent CAPES catalogue records.

The CAPES catalogue search API (``harvest_recent.py --sources capes``) returns
title, author, programme and defence date, but no abstract, keywords or
advisor. Those fields are published on the public Sucupira page of each work
(``viewTrabalhoConclusao.jsf?id_trabalho=<id>``). This script visits each page
once, keeps **only** the fields that exist in the public metadata schema and
stores them in ``raw/sucupira/<year>/<id>.json.gz``. Examination-board names,
employment data and research-project descriptions on the same page are
ignored and never written to disk.

The run is resumable: details already cached are skipped. Pages that do not
exist (HTTP 404) are cached as empty so they are not requested again.

Usage::

    .venv/bin/python tools/dataset/enrich_sucupira.py \\
        --raw tools/dataset/work/recent/raw --rate 1
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import random
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from common import setup_logging
from harvest_recent import USER_AGENT, RateLimiter, prefer_ipv4

LOGGER = setup_logging("enrich_sucupira")

SUCUPIRA_URL = (
    "https://sucupira-legado.capes.gov.br/sucupira/public/consultas/coleta/"
    "trabalhoConclusao/viewTrabalhoConclusao.jsf"
)
TIMEOUT = 60
MAX_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 120.0

# span id on the page -> key stored in the cache (only public-schema fields)
FIELDS = {
    "programa": "programa",
    "resumo": "resumo",
    "palavras": "palavras",
    "abstract": "abstract",
    "keyword": "keyword",
    "idioma": "idioma",
    "biblioteca": "biblioteca",
    "area": "area_concentracao",
    "linha": "linha_pesquisa",
}
_SPAN = re.compile(r'<span id="([A-Za-z_]+)">(.*?)</span>', re.S)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_PROGRAM_CODE = re.compile(r"\((\d{11}[A-Z]\d)\)\s*$")


class EnrichError(RuntimeError):
    """Raised when a detail page cannot be fetched."""


def _text(fragment: str) -> str | None:
    value = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()
    return value or None


def parse_detail(page: str) -> dict[str, Any]:
    """Extract the public-schema fields from one Sucupira detail page."""
    detail: dict[str, Any] = {}
    advisors: list[str] = []
    for span_id, fragment in _SPAN.findall(page):
        value = _text(fragment)
        if not value:
            continue
        if span_id.startswith("orientador") or span_id.startswith("coorientador"):
            if value not in advisors:
                advisors.append(value)
            continue
        key = FIELDS.get(span_id)
        if key and key not in detail:
            detail[key] = value
    program = detail.get("programa")
    if program:
        match = _PROGRAM_CODE.search(program)
        if match:
            detail["codigo_programa"] = match.group(1)
    detail.pop("programa", None)
    if advisors:
        detail["orientadores"] = advisors
    return detail


def detail_path(raw_dir: Path, year: int, identifier: str) -> Path:
    return Path(raw_dir) / "sucupira" / str(year) / f"{identifier}.json.gz"


def load_detail(raw_dir: Path, year: int | None, identifier: str) -> dict[str, Any] | None:
    """Return the cached detail fields of one record, if any."""
    if year is None:
        return None
    path = detail_path(raw_dir, year, identifier)
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    fields = payload.get("fields") if isinstance(payload, dict) else None
    return fields if isinstance(fields, dict) else None


def iter_capes_ids(raw_dir: Path) -> Iterator[tuple[int, str]]:
    """Yield ``(year, id)`` for every cached CAPES catalogue record."""
    seen: set[str] = set()
    for path in sorted((Path(raw_dir) / "capes").glob("*/*/page-*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        year = int(payload.get("year"))
        for record in payload.get("response", {}).get("tesesDissertacoes", []):
            identifier = str(record.get("id") or "").strip()
            if identifier.isdigit() and identifier not in seen:
                seen.add(identifier)
                yield year, identifier


def fetch_detail(
    session: requests.Session, identifier: str, limiter: RateLimiter
) -> dict[str, Any] | None:
    """Download and parse one page; ``None`` when the page does not exist."""
    params = {"popup": "true", "id_trabalho": identifier}
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        limiter.wait()
        try:
            response = session.get(SUCUPIRA_URL, params=params, timeout=TIMEOUT)
            if response.status_code == 404:
                return None
            if response.status_code == 429 or response.status_code >= 500:
                raise EnrichError(f"HTTP {response.status_code}")
            if response.status_code >= 400:
                raise EnrichError(f"HTTP {response.status_code}")
            response.encoding = response.encoding or "utf-8"
            page = response.text
            if 'id="autor"' not in page:
                return None
            return parse_detail(page)
        except (requests.RequestException, EnrichError) as exc:
            last_error = exc
            if attempt == MAX_ATTEMPTS:
                break
            delay = min(MAX_BACKOFF_SECONDS, 4.0 * 2.0 ** (attempt - 1))
            delay += random.uniform(0, 1)
            LOGGER.warning(
                "Falha em %s (tentativa %d/%d): %s; nova tentativa em %.1fs",
                identifier, attempt, MAX_ATTEMPTS, exc, delay,
            )
            time.sleep(delay)
    raise EnrichError(f"Não foi possível consultar {identifier}: {last_error}")


def _write(path: Path, identifier: str, fields: dict[str, Any] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": identifier,
        "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "found": fields is not None,
        "fields": fields or {},
    }
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    tmp.replace(path)


def enrich(raw_dir: Path, rate: float, limit: int | None = None) -> dict[str, int]:
    """Fetch every missing detail page and return simple counters."""
    prefer_ipv4()
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    limiter = RateLimiter(rate)
    todo = [
        (year, identifier)
        for year, identifier in iter_capes_ids(raw_dir)
        if not detail_path(raw_dir, year, identifier).exists()
    ]
    if limit is not None:
        todo = todo[:limit]
    LOGGER.info("Sucupira: %d página(s) a consultar", len(todo))
    counts = {"fetched": 0, "missing": 0, "with_abstract": 0}
    for index, (year, identifier) in enumerate(todo, start=1):
        fields = fetch_detail(session, identifier, limiter)
        _write(detail_path(raw_dir, year, identifier), identifier, fields)
        if fields is None:
            counts["missing"] += 1
        else:
            counts["fetched"] += 1
            if fields.get("resumo"):
                counts["with_abstract"] += 1
        if index % 200 == 0 or index == len(todo):
            LOGGER.info("Sucupira: %d/%d (%s)", index, len(todo), counts)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, default=Path("tools/dataset/work/recent/raw"))
    parser.add_argument("--rate", type=float, default=1.0, help="requisições por segundo")
    parser.add_argument("--limit", type=int, default=None, help="máximo de páginas (teste)")
    args = parser.parse_args(argv)
    counts = enrich(args.raw, args.rate, args.limit)
    LOGGER.info("Concluído: %s", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
