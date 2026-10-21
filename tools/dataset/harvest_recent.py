#!/usr/bin/env python3
"""Harvest recent theses (2025+) from the BDTD and CAPES catalogue APIs.

Two public sources are supported:

* ``bdtd`` — IBICT's VuFind endpoint.  The API serves at most 1,000 records
  per query (any deeper page repeats the last window), so years with more
  records are recursively split with lexicographic range filters (format,
  institution, repository network, ...; ``id`` as the last resort) until every
  partition fits.  A split is accepted only when both sides add up to the
  parent count.
* ``capes`` — the CAPES catalogue search endpoint.  It always returns 20
  records per page, regardless of ``registrosPorPagina``.

Every page is cached as ``raw/<source>/<year>/<partition>/page-NNNNN.json.gz``
so an interrupted run resumes where it stopped.  A machine-readable summary is
written to ``raw/_sources_recent.json``.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import requests
from common import setup_logging

LOGGER = setup_logging("harvest_recent")

USER_AGENT = "BuscaTeses-dataset (+https://github.com/andrebadini/buscateses-local)"
BDTD_URL = "https://bdtd.ibict.br/vufind/api/v1/search"
CAPES_URL = "https://catalogodeteses.capes.gov.br/catalogo-teses/rest/busca"
CAPES_HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://catalogodeteses.capes.gov.br",
    "Referer": "https://catalogodeteses.capes.gov.br/catalogo-teses/",
}
BDTD_LIMIT = 100
# VuFind only reaches offsets below 1,000: page 11 with limit=100 repeats the
# window starting at offset 1,000.  Every partition must stay at or below it.
BDTD_OFFSET_LIMIT = 1_000
CAPES_REQUESTED_PAGE_SIZE = 100
MAX_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 60.0
TIMEOUT = (15, 120)


class HarvestError(RuntimeError):
    """The remote API returned an unexpected or unusable response."""


@dataclass
class Partition:
    """One query partition and its expected number of records."""

    name: str
    filters: list[str]
    expected: int
    page_size: int


class RateLimiter:
    """Simple request throttle (at most *rate* requests per second)."""

    def __init__(self, rate: float) -> None:
        if rate <= 0:
            raise ValueError("--rate deve ser positivo")
        self.interval = 1.0 / rate
        self._last = 0.0

    def wait(self) -> None:
        delay = self.interval - (time.monotonic() - self._last)
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


def prefer_ipv4() -> None:
    """Skip IPv6 resolution.

    Some networks announce an IPv6 route that never answers for the CAPES
    host; falling back to IPv4 keeps the harvest fast and deterministic.
    """
    try:
        import urllib3.util.connection as connection

        connection.HAS_IPV6 = False  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - urllib3 always ships with requests
        pass


def build_session() -> requests.Session:
    """Create an HTTP session with the project User-Agent."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def _request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    params: Sequence[tuple[str, Any]] | None = None,
    body: dict[str, Any] | None = None,
    limiter: RateLimiter,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Perform one request with five exponential-backoff attempts."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        limiter.wait()
        try:
            response = session.request(
                method,
                url,
                params=list(params) if params else None,
                json=body,
                headers=headers,
                timeout=TIMEOUT,
            )
            if response.status_code == 429 or response.status_code >= 500:
                raise HarvestError(f"HTTP {response.status_code}")
            if response.status_code >= 400:
                raise HarvestError(
                    f"HTTP {response.status_code}: {response.text[:300]}"
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise HarvestError("Resposta JSON inesperada")
            return payload
        except (
            requests.RequestException,
            ValueError,
            HarvestError,
        ) as exc:
            last_error = exc
            if attempt == MAX_ATTEMPTS:
                break
            delay = min(
                MAX_BACKOFF_SECONDS, 4.0 * 2.0 ** (attempt - 1)
            ) + random.uniform(0, 1)
            LOGGER.warning(
                "Falha em %s (tentativa %d/%d): %s; nova tentativa em %.1fs",
                url,
                attempt,
                MAX_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)
    raise HarvestError(f"Não foi possível consultar {url}: {last_error}")


def _page_path(root: Path, source: str, year: int, partition: str, page: int) -> Path:
    return root / source / str(year) / partition / f"page-{page:05d}.json.gz"


def _save_page(path: Path, payload: dict[str, Any]) -> None:
    """Write one page atomically as gzip-compressed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.gz.tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(temporary, path)


def _load_page(path: Path) -> dict[str, Any] | None:
    """Return a cached page, or ``None`` when it is absent or unreadable."""
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        LOGGER.warning("Página corrompida; será baixada de novo: %s", path)
        path.unlink(missing_ok=True)
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
        LOGGER.warning("Página sem resposta válida; será baixada de novo: %s", path)
        path.unlink(missing_ok=True)
        return None
    return payload


# ---------------------------------------------------------------------------
# BDTD (VuFind)
# ---------------------------------------------------------------------------


def _bdtd_params(
    year: int,
    *,
    filters: Sequence[str] = (),
    limit: int = BDTD_LIMIT,
    page: int = 1,
) -> list[tuple[str, Any]]:
    params: list[tuple[str, Any]] = [
        ("lookfor", "*"),
        ("type", "AllFields"),
        ("filter[]", f'publishDate:"{year}"'),
    ]
    combined = _combined_filter(filters)
    if combined is not None:
        params.append(("filter[]", combined))
    params.extend(
        [
            ("limit", limit),
            ("page", page),
            ("field[]", "rawData"),
        ]
    )
    return params


def _combined_filter(filters: Sequence[str]) -> str | None:
    """Pack every constraint into one Solr ``filter[]`` value.

    The API answers 429 when a request carries more than one constraint
    besides ``publishDate``.  A parenthesized boolean expression keeps the
    query to a single filter while preserving all field ranges.
    """
    if not filters:
        return None
    if len(filters) == 1:
        return filters[0]
    return "(" + " AND ".join(filters) + ")"


def _bdtd_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise HarvestError("BDTD: campo 'records' inválido")
    return records


def bdtd_result_count(
    session: requests.Session,
    year: int,
    limiter: RateLimiter,
    *,
    filters: Sequence[str] = (),
) -> int:
    """Return the number of BDTD records matching *filters* in *year*."""
    payload = _request(
        session,
        "GET",
        BDTD_URL,
        params=_bdtd_params(year, filters=filters, limit=1),
        limiter=limiter,
    )
    try:
        return int(payload["resultCount"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HarvestError("BDTD: resultCount ausente") from exc


#: Characters that can follow a prefix in a BDTD record id (network acronym,
#: separators and the hexadecimal hash).  Splitting by id prefix produces
#: disjoint partitions that always sum to the parent, using a single
#: ``filter[]`` constraint besides ``publishDate`` (the API answers 429 when a
#: request carries more than one).
# Probe order: hex digits first (ids end in hashes), then the rest.
BDTD_ID_CHARSET = (
    "0123456789abcdef"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "_-."
    "ghijklmnopqrstuvwxyz"
)
_SOLR_SPECIAL = set('+-&|!(){}[]^"~*?:\\/ ')


def _escape_prefix(prefix: str) -> str:
    """Escape Solr query syntax characters in an id prefix."""
    return "".join(f"\\{char}" if char in _SOLR_SPECIAL else char for char in prefix)


def _id_prefix_filter(prefix: str) -> str:
    return f"id:{_escape_prefix(prefix)}*"


def _prefix_slug(prefix: str) -> str:
    """Filesystem-safe, case-preserving partition name for an id prefix."""
    encoded = "".join(
        char if char.isalnum() else f"~{ord(char):02x}" for char in prefix
    )
    # Case-insensitive file systems (macOS) must not merge "Ab" and "AB".
    digest = hashlib.sha1(prefix.encode("utf-8")).hexdigest()[:6]
    return f"id-{encoded}-{digest}"


def _split_partition(
    session: requests.Session,
    year: int,
    partition: Partition,
    limiter: RateLimiter,
) -> list[Partition]:
    """Split *partition* by record-id prefix until every leaf fits the cap."""
    leaves: list[Partition] = []
    pending: list[tuple[str, int]] = [("", partition.expected)]
    while pending:
        prefix, expected = pending.pop()
        if prefix and expected <= BDTD_OFFSET_LIMIT:
            leaves.append(
                Partition(_prefix_slug(prefix), [_id_prefix_filter(prefix)], expected, BDTD_LIMIT)
            )
            continue
        if len(prefix) > 40:
            raise HarvestError(f"BDTD {year}: prefixo de id longo demais ({prefix!r})")
        children: list[tuple[str, int]] = []
        for char in BDTD_ID_CHARSET:
            child = prefix + char
            count = bdtd_result_count(
                session, year, limiter, filters=[_id_prefix_filter(child)]
            )
            if count:
                children.append((child, count))
                if sum(item for _, item in children) >= expected:
                    # Prefixes are disjoint: once the children add up to the
                    # parent, every other prefix is empty.
                    break
        covered = sum(count for _, count in children)
        if covered != expected:
            raise HarvestError(
                f"BDTD {year}: prefixo {prefix!r} cobre {covered} de {expected} registro(s)"
            )
        LOGGER.info(
            "BDTD %d: prefixo %r com %d registro(s) dividido em %d parte(s)",
            year,
            prefix,
            expected,
            len(children),
        )
        pending.extend(children)
    return sorted(leaves, key=lambda item: item.name)


def bdtd_partitions(
    session: requests.Session, year: int, total: int, limiter: RateLimiter
) -> list[Partition]:
    """Return disjoint partitions covering every BDTD record of *year*."""
    if total <= 0:
        return [Partition("all", [], total, BDTD_LIMIT)]
    if total <= BDTD_OFFSET_LIMIT:
        return [Partition("all", [], total, BDTD_LIMIT)]
    partitions = _split_partition(
        session, year, Partition("all", [], total, BDTD_LIMIT), limiter
    )
    covered = sum(partition.expected for partition in partitions)
    if covered != total:
        raise HarvestError(
            f"BDTD {year}: partições somam {covered}, mas o total é {total}"
        )
    if len({partition.name for partition in partitions}) != len(partitions):
        raise HarvestError(f"BDTD {year}: nomes de partição repetidos")
    LOGGER.info(
        "BDTD %d: %d registro(s) em %d partição(ões)",
        year,
        total,
        len(partitions),
    )
    return partitions


BDTD_PLAN_VERSION = 2


def _plan_path(root: Path, source: str, year: int) -> Path:
    return Path(root) / source / str(year) / "_plan.json"


def _load_plan(path: Path, total: int) -> list[Partition] | None:
    """Return a saved partition plan when it still matches *total*."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != BDTD_PLAN_VERSION:
            return None
        if int(payload["expected_total"]) != total:
            return None
        partitions = [
            Partition(
                name=str(item["name"]),
                filters=[str(value) for value in item["filters"]],
                expected=int(item["expected"]),
                page_size=int(item["page_size"]),
            )
            for item in payload["partitions"]
        ]
    except (KeyError, TypeError, ValueError, OSError):
        LOGGER.warning("Plano de partições inválido; será recalculado: %s", path)
        return None
    if sum(partition.expected for partition in partitions) != total:
        return None
    return partitions


def _save_plan(path: Path, total: int, partitions: Sequence[Partition]) -> None:
    """Persist the partition plan so an interrupted run resumes the same tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": BDTD_PLAN_VERSION,
        "expected_total": total,
        "partitions": [
            {
                "name": partition.name,
                "filters": partition.filters,
                "expected": partition.expected,
                "page_size": partition.page_size,
            }
            for partition in partitions
        ],
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _bdtd_record_id(record: dict[str, Any]) -> str | None:
    raw = record.get("rawData") if isinstance(record, dict) else None
    if not isinstance(raw, dict):
        return None
    identifier = raw.get("id")
    return str(identifier) if identifier else None


def _capes_record_id(record: dict[str, Any]) -> str | None:
    identifier = record.get("id") if isinstance(record, dict) else None
    return str(identifier) if identifier else None


# ---------------------------------------------------------------------------
# CAPES catalogue
# ---------------------------------------------------------------------------


def _capes_body(year: int, page: int) -> dict[str, Any]:
    return {
        "termo": "",
        "filtros": [{"campo": "Ano", "valor": str(year)}],
        "pagina": page,
        "registrosPorPagina": CAPES_REQUESTED_PAGE_SIZE,
    }


def capes_partition(
    session: requests.Session, year: int, limiter: RateLimiter
) -> Partition:
    """Probe the CAPES API and return the single ``all`` partition."""
    payload = _request(
        session,
        "POST",
        CAPES_URL,
        body=_capes_body(year, 1),
        limiter=limiter,
        headers=CAPES_HEADERS,
    )
    total = int(payload.get("total") or 0)
    # The server ignores registrosPorPagina and answers with 20.
    page_size = int(payload.get("registrosPorPagina") or 20)
    return Partition("all", [], total, max(1, page_size))


# ---------------------------------------------------------------------------
# Collection and reporting
# ---------------------------------------------------------------------------


def _collect_partition(
    session: requests.Session,
    *,
    source: str,
    year: int,
    partition: Partition,
    root: Path,
    limiter: RateLimiter,
    max_pages: int | None,
    seen: set[str] | None = None,
) -> dict[str, Any]:
    """Fetch all pages of *partition*, skipping pages already on disk."""
    total_pages = math.ceil(partition.expected / partition.page_size)
    if max_pages is not None:
        total_pages = min(total_pages, max_pages)
    fetched = 0
    cached = 0
    ids: set[str] = set()
    duplicates = 0
    for page in range(1, total_pages + 1):
        path = _page_path(root, source, year, partition.name, page)
        cached_payload = _load_page(path)
        if cached_payload is not None:
            payload = cached_payload
            cached += 1
        else:
            if source == "bdtd":
                response = _request(
                    session,
                    "GET",
                    BDTD_URL,
                    params=_bdtd_params(
                        year,
                        filters=partition.filters,
                        limit=partition.page_size,
                        page=page,
                    ),
                    limiter=limiter,
                )
            else:
                response = _request(
                    session,
                    "POST",
                    CAPES_URL,
                    body=_capes_body(year, page),
                    limiter=limiter,
                    headers=CAPES_HEADERS,
                )
            payload = {
                "source": source,
                "year": year,
                "partition": partition.name,
                "page": page,
                "expected": partition.expected,
                "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "response": response,
            }
            _save_page(path, payload)
            fetched += 1
            if fetched % 25 == 0:
                LOGGER.info(
                    "%s %d/%s: %d página(s) baixada(s)", source, year, partition.name, fetched
                )
        records = (
            _bdtd_records(payload["response"])
            if source == "bdtd"
            else payload["response"].get("tesesDissertacoes", [])
        )
        for record in records:
            identifier = (
                _bdtd_record_id(record) if source == "bdtd" else _capes_record_id(record)
            )
            if identifier is None:
                continue
            if identifier in ids:
                duplicates += 1
            else:
                ids.add(identifier)
                if seen is not None:
                    seen.add(identifier)
    return {
        "filters": list(partition.filters),
        "query_filter": _combined_filter(partition.filters),
        "expected": partition.expected,
        "page_size": partition.page_size,
        "pages_total": total_pages,
        "pages_fetched_now": fetched,
        "pages_cached": cached,
        "records_collected": len(ids),
        "duplicates_on_pages": duplicates,
        "complete": len(ids) >= partition.expected and (
            max_pages is None or total_pages == math.ceil(partition.expected / partition.page_size)
        ),
    }


def _prune_partitions(
    root: Path, source: str, year: int, keep: Sequence[str]
) -> None:
    """Remove cached partitions that are no longer part of the query tree."""
    year_dir = Path(root) / source / str(year)
    if not year_dir.is_dir():
        return
    keep_set = set(keep)
    for directory in year_dir.iterdir():
        if directory.is_dir() and directory.name not in keep_set:
            LOGGER.info(
                "Removendo partição antiga sem uso: %s/%s/%s",
                source,
                year,
                directory.name,
            )
            for page in directory.glob("page-*.json.gz"):
                page.unlink()
            directory.rmdir()


def _year_from_metadata(metadata_dir: Path) -> int | None:
    parts = sorted(Path(metadata_dir).glob("*.parquet"))
    if not parts:
        return None
    years = pq.read_table(parts[-1], columns=["year"]).column("year")
    values = [value for value in years.to_pylist() if value is not None]
    return max(values) if values else None


def default_years(metadata_dir: Path, current_year: int) -> list[int]:
    """Return the years missing from the published metadata."""
    last = _year_from_metadata(metadata_dir)
    if last is None:
        raise FileNotFoundError(
            f"Nenhuma parte de metadados em {metadata_dir}; informe --years"
        )
    if last >= current_year:
        return [current_year]
    return list(range(last + 1, current_year + 1))


def harvest(
    out_dir: Path,
    years: Sequence[int],
    *,
    sources: Sequence[str] = ("bdtd", "capes"),
    max_pages: int | None = None,
    rate: float = 0.5,
) -> dict[str, Any]:
    """Collect every requested year/source and write the summary report."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(rate)
    session = build_session()
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "user_agent": USER_AGENT,
        "rate_limit_per_second": rate,
        "max_pages": max_pages,
        "sources": {},
    }
    try:
        for source in sources:
            source_report: dict[str, Any] = {
                "url": BDTD_URL if source == "bdtd" else CAPES_URL,
                "method": "GET" if source == "bdtd" else "POST",
                "parameters": (
                    {"lookfor": "*", "type": "AllFields", "limit": BDTD_LIMIT}
                    if source == "bdtd"
                    else {"termo": "", "filtros": "Ano", "registrosPorPagina": 20}
                ),
                "years": {},
            }
            for year in years:
                if source == "bdtd":
                    total = bdtd_result_count(session, year, limiter)
                    plan_path = _plan_path(out_dir, source, year)
                    partitions = _load_plan(plan_path, total)
                    if partitions is None:
                        partitions = bdtd_partitions(session, year, total, limiter)
                        _save_plan(plan_path, total, partitions)
                    else:
                        LOGGER.info(
                            "BDTD %d: reutilizando plano com %d partição(ões)",
                            year,
                            len(partitions),
                        )
                else:
                    partition = capes_partition(session, year, limiter)
                    total = partition.expected
                    partitions = [partition]
                _prune_partitions(
                    out_dir,
                    source,
                    year,
                    [partition.name for partition in partitions],
                )
                year_report: dict[str, Any] = {
                    "expected_total": total,
                    "partitions": {},
                }
                year_ids: set[str] = set()
                for partition in partitions:
                    LOGGER.info(
                        "%s %d/%s: %d registro(s) esperado(s)",
                        source,
                        year,
                        partition.name,
                        partition.expected,
                    )
                    year_report["partitions"][partition.name] = _collect_partition(
                        session,
                        source=source,
                        year=year,
                        partition=partition,
                        root=out_dir,
                        limiter=limiter,
                        max_pages=max_pages,
                        seen=year_ids,
                    )
                collected = len(year_ids)
                year_report["records_seen"] = sum(
                    item["records_collected"]
                    for item in year_report["partitions"].values()
                )
                year_report["records_duplicated_across_partitions"] = (
                    year_report["records_seen"] - collected
                )
                year_report["records_collected_unique"] = collected
                year_report["complete"] = (
                    collected == total
                    and all(
                        item["complete"] for item in year_report["partitions"].values()
                    )
                )
                if not year_report["complete"]:
                    LOGGER.warning(
                        "%s %d: coletados %d de %d registro(s)",
                        source,
                        year,
                        collected,
                        total,
                    )
                expected_sum = sum(
                    item["expected"] for item in year_report["partitions"].values()
                )
                if expected_sum != total:
                    raise HarvestError(
                        f"{source} {year}: soma das partições ({expected_sum}) "
                        f"difere do total ({total})"
                    )
                source_report["years"][str(year)] = year_report
            report["sources"][source] = source_report
    finally:
        session.close()

    report_path = out_dir / "_sources_recent.json"
    # A run limited to some sources keeps what earlier runs recorded for the
    # others, so the summary always describes every cached source.
    if report_path.exists():
        try:
            previous = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        for name, entry in (previous.get("sources") or {}).items():
            report.setdefault("sources", {}).setdefault(name, entry)
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, report_path)
    LOGGER.info("Resumo da coleta em %s", report_path)
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Coleta trabalhos recentes da BDTD e do Catálogo de Teses da CAPES"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("tools/dataset/work/recent/raw"),
        help="diretório de saída das páginas brutas",
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=None,
        help="anos a coletar (padrão: do ano seguinte ao último dos metadados até o ano corrente)",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=Path("tools/dataset/work/metadata"),
        help="metadados existentes usados para descobrir o último ano",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=("bdtd", "capes"),
        default=("bdtd", "capes"),
    )
    parser.add_argument(
        "--max-pages", type=int, default=None, help="limite de páginas por partição (teste)"
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=0.5,
        help="requisições por segundo (0,5 é o valor seguro para a BDTD)",
    )
    arguments = parser.parse_args(argv)

    years = arguments.years or default_years(arguments.metadata, date.today().year)
    prefer_ipv4()
    report = harvest(
        arguments.out,
        years,
        sources=arguments.sources,
        max_pages=arguments.max_pages,
        rate=arguments.rate,
    )
    for source, source_report in report["sources"].items():
        for year, year_report in source_report["years"].items():
            LOGGER.info(
                "%s %s: %d/%d registro(s) — %s",
                source,
                year,
                year_report["records_collected_unique"],
                year_report["expected_total"],
                "completo" if year_report["complete"] else "incompleto",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
