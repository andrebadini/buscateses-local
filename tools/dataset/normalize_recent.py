#!/usr/bin/env python3
"""Normalise recent BDTD and CAPES-catalogue records into the public schema.

The harvester stores one gzip-compressed JSON page per request.  This module
maps those records to the same Parquet schema used by the CAPES open-data
files, enriches them with tables derived from the existing metadata and removes
duplicates (against the existing catalogue, between sources and among BDTD
networks).
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from common import (
    METADATA_COLUMNS,
    METADATA_SCHEMA,
    clean_text,
    normalize_degree,
    normalize_pages,
    normalize_url,
    setup_logging,
    strip_accents,
)
from enrich_sucupira import load_detail as load_sucupira_detail

LOGGER = setup_logging("normalize_recent")

BDTD_SOURCE = "bdtd"
CAPES_SOURCE = "capes-catalogo"
BDTD_DIR = "bdtd"
CAPES_DIR = "capes"
PART_BATCH_ROWS = 50_000

_PROGRAM_PREFIX = re.compile(
    r"^\s*programa\s+de\s+p[óo]s[-\s]?gradua[çc][ãa]o\s+(?:em\s+|de\s+)?",
    flags=re.IGNORECASE,
)
_INSTITUTION_PARENS = re.compile(r"\s*\([^()]*\)\s*$")
_NOT_INSTITUTION = re.compile(
    r"^\[?s\.\s?n\.?\]?$|^biblioteca|^reposit[óo]rio|^maxwell$|^brasil$|^programa\s+de\s+p",
    re.IGNORECASE,
)
_NAME_TAG = re.compile(r"\s*\[[^\]]*\]")
_LIFE_DATES = re.compile(r",?\s*\d{4}\s*-\s*(?:\d{4})?\.?\s*$")
_ORGANIZATION = re.compile(
    r"universidade|faculdade|instituto|escola|centro|departamento|programa|funda[çc][ãa]o",
    re.IGNORECASE,
)
_THESIS_NOTE = re.compile(
    r"^(disserta[çc][ãa]o|tese|trabalho de conclus[ãa]o( de curso)?)\s*"
    r"(\(|de\s+(mestrado|doutorado)|apresentad[ao]\s|[-–—,.]|$)",
    re.IGNORECASE,
)
_ID_TRABALHO = re.compile(r"id_trabalho=(\d+)")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_YEAR_RE = re.compile(r"(\d{4})")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

_PERSON_PARTICLES = {"de", "da", "do", "das", "dos", "e", "del", "la", "le"}

_PT_WORDS = {
    "não",
    "nao",
    "uma",
    "dos",
    "das",
    "pelo",
    "pela",
    "pelos",
    "pelas",
    "foi",
    "são",
    "sao",
    "também",
    "tambem",
    "então",
    "porém",
    "pois",
    "já",
    "além",
    "qual",
    "quais",
    "seus",
    "suas",
    "nosso",
    "nossa",
    "esse",
    "essa",
    "isso",
    "isto",
    "entre",
    "durante",
    "sobre",
    "como",
    "mais",
}
_EN_WORDS = {
    "the",
    "of",
    "and",
    "to",
    "in",
    "is",
    "that",
    "for",
    "with",
    "this",
    "are",
    "was",
    "were",
    "from",
    "by",
    "as",
    "an",
    "be",
    "has",
    "have",
    "its",
    "their",
    "which",
    "not",
    "can",
    "we",
    "it",
    "at",
    "on",
    "or",
    "these",
    "those",
    "been",
}
_ES_WORDS = {
    "el",
    "los",
    "las",
    "del",
    "y",
    "es",
    "son",
    "una",
    "un",
    "por",
    "con",
    "para",
    "se",
    "al",
    "su",
    "sus",
    "fue",
    "esta",
    "este",
    "como",
    "más",
    "mas",
    "pero",
    "entre",
    "durante",
    "desde",
    "hasta",
    "según",
    "sobre",
    "también",
    "lo",
    "le",
    "les",
}


# ---------------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------------


def _values(raw: dict[str, Any], key: str) -> list[str]:
    """Return the non-empty string values stored under *key*."""
    value = raw.get(key)
    if value is None:
        return []
    if isinstance(value, str):
        items: Iterable[Any] = [value]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        items = [value]
    cleaned: list[str] = []
    for item in items:
        # Some repositories join several values with "||" in one string.
        for part in str(item).split("||") if item is not None else []:
            text = clean_text(part)
            if text:
                cleaned.append(text)
    return cleaned


def _first(raw: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty value among *keys*."""
    for key in keys:
        values = _values(raw, key)
        if values:
            return values[0]
    return None


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def name_key(value: Any) -> str:
    """Normalise an institution/program name for lookups."""
    text = strip_accents(str(value or "")).upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", text)).strip()


def title_key(value: Any) -> str:
    """Normalise a title: lowercase, no accents, alphanumerics only."""
    return re.sub(r"[^a-z0-9]+", "", strip_accents(str(value or "")).casefold())


def author_tokens(value: Any) -> set[str]:
    """Return comparable surname tokens.

    Authors are stored as ``Nome Sobrenome``, so every token after the first
    given name is treated as a surname.  Particles (``de``, ``da``, ...) are
    removed.  A single-token name is kept as-is.
    """
    tokens: set[str] = set()
    for name in str(value or "").split(";"):
        words = [
            token
            for token in _WORD_RE.findall(strip_accents(name).casefold())
            if token not in _PERSON_PARTICLES
        ]
        surnames = words[1:] if len(words) > 1 else words
        for token in surnames:
            if len(token) >= 2:
                tokens.add(token)
    return tokens


def person_name(value: Any) -> str | None:
    """Convert ``Sobrenome, Nome`` to ``Nome Sobrenome``.

    Repository tags such as ``[UNESP]`` and catalogue birth dates such as
    ``, 1973-`` are removed first.
    """
    text = clean_text(value)
    if text is None:
        return None
    text = clean_text(_LIFE_DATES.sub("", _NAME_TAG.sub("", text)))
    if text is None:
        return None
    if "," in text:
        surname, given = (part.strip() for part in text.split(",", 1))
        if surname and given:
            return f"{given} {surname}"
    return text


def people(values: Iterable[Any]) -> str | None:
    """Convert a list of names and join them with ``"; "``."""
    converted = [person_name(value) for value in values]
    names = _dedupe(item for item in converted if item)
    return "; ".join(names) or None


def clean_program(value: Any) -> str | None:
    """Strip the ``Programa de Pós-Graduação em`` prefix from a program name."""
    text = clean_text(value)
    if text is None:
        return None
    return clean_text(_PROGRAM_PREFIX.sub("", text))


def clean_institution(value: Any) -> str | None:
    """Remove a trailing acronym in parentheses from an institution name."""
    text = clean_text(value)
    if text is None:
        return None
    return clean_text(_INSTITUTION_PARENS.sub("", text)) or text


def detect_language(text: str | None) -> str:
    """Return ``pt``, ``en`` or ``es`` for an abstract (default ``pt``)."""
    if not text:
        return "pt"
    lowered = text.casefold()
    scores = {"pt": 0, "en": 0, "es": 0}
    for word in _WORD_RE.findall(lowered):
        if word in _PT_WORDS:
            scores["pt"] += 1
        if word in _EN_WORDS:
            scores["en"] += 1
        if word in _ES_WORDS:
            scores["es"] += 1
    if re.search(r"ç(?:ão|ões)\b", lowered):
        scores["pt"] += 3
    if re.search(r"ción(?:es)?\b", lowered):
        scores["es"] += 3
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "pt"


def language_label(value: Any) -> str | None:
    """Map an ISO 639 code or name to the label used by the CAPES open data."""
    text = clean_text(value)
    if text is None:
        return None
    code = strip_accents(text).casefold()
    mapping = {
        "por": "PORTUGUES",
        "pt": "PORTUGUES",
        "pt_br": "PORTUGUES",
        "pt-br": "PORTUGUES",
        "portugues": "PORTUGUES",
        "eng": "INGLES",
        "en": "INGLES",
        "ingles": "INGLES",
        "spa": "ESPANHOL",
        "es": "ESPANHOL",
        "espanol": "ESPANHOL",
        "espanhol": "ESPANHOL",
        "fra": "FRANCES",
        "fre": "FRANCES",
        "fr": "FRANCES",
        "frances": "FRANCES",
        "ita": "ITALIANO",
        "it": "ITALIANO",
        "italiano": "ITALIANO",
        "deu": "ALEMAO",
        "ger": "ALEMAO",
        "de": "ALEMAO",
        "alemao": "ALEMAO",
        "lat": "LATIM",
    }
    return mapping.get(code)


def _iso_datetime(value: str | None) -> tuple[int | None, str | None]:
    """Return ``(year, ISO date)`` from an ISO timestamp, date when complete."""
    text = clean_text(value)
    if text is None:
        return None, None
    match = _ISO_DATE_RE.match(text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            return year, date(year, month, day).isoformat()
        except ValueError:
            return year, None
    year_match = _YEAR_RE.search(text)
    return (int(year_match.group(1)) if year_match else None), None


def _year_from(value: str | None) -> int | None:
    text = clean_text(value)
    if text is None:
        return None
    match = _YEAR_RE.search(text)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Source mappers
# ---------------------------------------------------------------------------


def parse_bdtd_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map one BDTD ``rawData`` object to the public metadata shape."""
    identifier = clean_text(raw.get("id"))
    title = _first(raw, "title", "dc.title.none.fl_str_mv", "dc.title.pt_BR.fl_str_mv")
    if not identifier or not title:
        return None

    issued = _first(raw, "dc.date.issued.fl_str_mv")
    year, defense_date = _iso_datetime(issued)
    if year is None:
        year = _year_from(_first(raw, "publishDate"))

    author_values = _values(raw, "author") or _values(raw, "dc.contributor.author.fl_str_mv")
    advisor_values: list[str] = []
    for index in range(1, 5):
        advisor_values.extend(_values(raw, f"dc.contributor.advisor{index}.fl_str_mv"))
    for index in range(1, 4):
        for advisor in _values(raw, f"dc.contributor.advisor-co{index}.fl_str_mv"):
            converted = person_name(advisor)
            if converted:
                advisor_values.append(f"{converted} (coorientador)")
    contributor_program = None
    if not advisor_values:
        # Some repositories only fill dc.contributor.none: the advisor first,
        # then committee members, departments and the programme.
        for value in _values(raw, "dc.contributor.none.fl_str_mv"):
            if _PROGRAM_PREFIX.match(value):
                contributor_program = contributor_program or clean_program(value)
            elif not advisor_values and not _ORGANIZATION.search(value):
                advisor_values.append(value)

    degree = _bdtd_degree(raw)
    publisher = _first(raw, "dc.publisher.none.fl_str_mv")
    publisher_program = (
        clean_program(publisher) if publisher and _PROGRAM_PREFIX.match(publisher) else None
    )
    institution = next(
        (
            name
            for name in (
                _first(raw, "dc.publisher.none.fl_str_mv"),
                _first(raw, "publisher.none.fl_str_mv"),
                clean_institution(raw.get("instname_str")),
            )
            if name and not _NOT_INSTITUTION.search(name)
        ),
        None,
    )
    acronym = _first(raw, "dc.publisher.initials.fl_str_mv") or clean_text(
        raw.get("instacron_str")
    )
    program = (
        clean_program(_first(raw, "dc.publisher.program.fl_str_mv"))
        or publisher_program
        or contributor_program
    )

    keywords = (
        _values(raw, "dc.subject.por.fl_str_mv")
        or _values(raw, "dc.subject.keyword.pt_BR.fl_str_mv")
        or _values(raw, "dc.subject.none.fl_str_mv")
    )
    keywords_en = (
        _values(raw, "dc.subject.eng.fl_str_mv")
        or _values(raw, "dc.subject.en.none.fl_str_mv")
        or _values(raw, "dc.subject.en.fl_str_mv")
    )

    description = _first(raw, "description")
    if description and _THESIS_NOTE.match(description) and len(description) < 500:
        # "Tese (Doutorado em ...) - Universidade X, 2025": a catalogue note,
        # not an abstract.
        description = None
    abstract = None
    abstract_en = None
    if description:
        if detect_language(description) == "en":
            abstract_en = description
        else:
            abstract = description

    url = normalize_url(
        _first(raw, "dc.identifier.uri.fl_str_mv", "url")
    )
    return {
        "id": f"bdtd:{identifier}",
        "year": year,
        "defense_date": defense_date,
        "title": title,
        "author": people(author_values),
        "advisors": people(advisor_values),
        "degree": degree,
        "institution": clean_institution(institution),
        "institution_acronym": clean_text(acronym),
        "uf": None,
        "region": None,
        "program": program,
        "program_code": None,
        "great_area": None,
        "knowledge_area": None,
        "evaluation_area": None,
        "concentration_area": None,
        "research_line": None,
        "keywords": "; ".join(_dedupe(keywords)) or None,
        "keywords_en": "; ".join(_dedupe(keywords_en)) or None,
        "abstract": abstract,
        "abstract_en": abstract_en,
        "language": language_label(
            _first(raw, "dc.language.iso.fl_str_mv", "language")
        ),
        "pages": None,
        "library": clean_text(raw.get("reponame_str")),
        "url": url,
        "source": BDTD_SOURCE,
    }


def _bdtd_degree(raw: dict[str, Any]) -> str | None:
    """Map the BDTD format plus the citation to a canonical degree."""
    format_value = (
        _first(raw, "format", "dc.type.driver.fl_str_mv") or ""
    ).casefold()
    if "doctoral" in format_value or "tese" in format_value:
        degree = "Doutorado"
    elif "master" in format_value or "disserta" in format_value:
        degree = "Mestrado"
    else:
        degree = clean_text(_first(raw, "format"))
    citation = " ".join(_values(raw, "dc.identifier.citation.fl_str_mv")).casefold()
    if "mestrado profissional" in citation:
        return "Mestrado Profissional"
    if "doutorado profissional" in citation:
        return "Doutorado Profissional"
    return degree


_PLACEHOLDER_RE = re.compile(r"^[\W_xX0-9]*$")


def informative(value: Any, minimum: int = 1) -> str | None:
    """Return the cleaned text unless it is a placeholder such as ``xx``."""
    text = clean_text(value)
    if text is None or _PLACEHOLDER_RE.match(text) or len(text) < minimum:
        return None
    return text


def keyword_list(value: Any) -> str | None:
    """Split ``a;b;c`` keywords and join them with ``"; "``."""
    text = clean_text(value)
    if text is None:
        return None
    parts = (informative(part, 2) for part in re.split(r"[;\n]", text))
    return "; ".join(_dedupe(part for part in parts if part)) or None


def parse_capes_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map one CAPES catalogue record to the public metadata shape."""
    identifier = clean_text(raw.get("id"))
    title = clean_text(raw.get("titulo"))
    if not identifier or not title:
        return None
    year, defense_date = _iso_datetime(raw.get("dataDefesa"))
    detail = raw.get("_sucupira") if isinstance(raw.get("_sucupira"), dict) else {}
    advisors = detail.get("orientadores") if isinstance(detail.get("orientadores"), list) else []
    return {
        "id": f"capes-catalogo:{identifier}",
        "year": year,
        "defense_date": defense_date,
        "title": title,
        "author": clean_text(raw.get("autor")),
        "advisors": people(advisors),
        "degree": normalize_degree(raw.get("grauAcademico")),
        "institution": clean_institution(raw.get("instituicao")),
        "institution_acronym": None,
        "uf": None,
        "region": None,
        "program": clean_program(raw.get("nomePrograma")),
        "program_code": clean_text(detail.get("codigo_programa")),
        "great_area": None,
        "knowledge_area": None,
        "evaluation_area": None,
        "concentration_area": informative(detail.get("area_concentracao"), 3),
        "research_line": informative(detail.get("linha_pesquisa"), 3),
        "keywords": keyword_list(detail.get("palavras")),
        "keywords_en": keyword_list(detail.get("keyword")),
        "abstract": informative(detail.get("resumo"), 40),
        "abstract_en": informative(detail.get("abstract"), 40),
        "language": language_label(detail.get("idioma")),
        "pages": normalize_pages(raw.get("paginas")),
        "library": clean_text(raw.get("biblioteca")) or clean_text(detail.get("biblioteca")),
        "url": normalize_url(raw.get("link")),
        "source": CAPES_SOURCE,
    }


# ---------------------------------------------------------------------------
# Raw page discovery
# ---------------------------------------------------------------------------


def _iter_page_files(raw_dir: Path, source_dir: str) -> Iterator[Path]:
    yield from sorted((Path(raw_dir) / source_dir).glob("*/*/page-*.json.gz"))


def _read_page(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
        raise ValueError(f"Página inválida: {path}")
    return payload


def iter_raw_records(raw_dir: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(source, raw_record)`` from every cached page."""
    for path in _iter_page_files(raw_dir, BDTD_DIR):
        payload = _read_page(path)
        for record in payload["response"].get("records", []):
            raw = record.get("rawData") if isinstance(record, dict) else None
            if isinstance(raw, dict):
                yield BDTD_SOURCE, raw
    for path in _iter_page_files(raw_dir, CAPES_DIR):
        payload = _read_page(path)
        year = payload.get("year")
        for record in payload["response"].get("tesesDissertacoes", []):
            if isinstance(record, dict):
                detail = load_sucupira_detail(
                    raw_dir, int(year) if year else None, str(record.get("id") or "")
                )
                if detail:
                    record = {**record, "_sucupira": detail}
                yield CAPES_SOURCE, record


# ---------------------------------------------------------------------------
# Existing metadata tables
# ---------------------------------------------------------------------------


@dataclass
class ExistingTables:
    """Lookup tables derived from the published metadata."""

    institution: dict[str, dict[str, str | None]] = field(default_factory=dict)
    acronym_labels: dict[str, Counter[str]] = field(default_factory=dict)
    advisor_program: dict[tuple[str, str], dict[str, str | None]] = field(default_factory=dict)
    advisor_area: dict[str, dict[str, str | None] | None] = field(default_factory=dict)
    program: dict[tuple[str, str], dict[str, str | None]] = field(default_factory=dict)
    great_labels: dict[str, str] = field(default_factory=dict)
    knowledge_labels: dict[str, str] = field(default_factory=dict)
    matching_authors: dict[str, set[str]] = field(default_factory=dict)
    capes_ids: set[str] = field(default_factory=set)
    records: int = 0


def _metadata_parts(metadata_dir: Path) -> list[Path]:
    return sorted(Path(metadata_dir).glob("*.parquet"))


def load_existing_tables(
    metadata_dir: Path,
    *,
    candidate_titles: set[str],
    candidate_capes_ids: set[str],
) -> ExistingTables:
    """Scan the existing metadata once, newest parts first.

    Institution and program lookups keep the first value seen (the most recent
    year wins).  Author tokens are only stored for titles that could collide
    with a new record, which keeps memory bounded.
    """
    tables = ExistingTables()
    parts = _metadata_parts(metadata_dir)
    columns = [
        "institution",
        "institution_acronym",
        "uf",
        "region",
        "program",
        "program_code",
        "great_area",
        "knowledge_area",
        "evaluation_area",
        "title",
        "author",
        "advisors",
        "url",
    ]
    for path in reversed(parts):
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(batch_size=PART_BATCH_ROWS, columns=columns):
            data = batch.to_pydict()
            count = batch.num_rows
            for index in range(count):
                institution = data["institution"][index]
                program = data["program"][index]
                inst_key = name_key(institution)
                if inst_key:
                    acronym = acronym_key(data["institution_acronym"][index])
                    if acronym:
                        tables.acronym_labels.setdefault(acronym, Counter())[
                            str(institution)
                        ] += 1
                    tables.institution.setdefault(
                        inst_key,
                        {
                            "label": institution,
                            "institution_acronym": data["institution_acronym"][index],
                            "uf": data["uf"][index],
                            "region": data["region"][index],
                        },
                    )
                program_key = name_key(program)
                if inst_key and program_key:
                    tables.program.setdefault(
                        (inst_key, program_key),
                        {
                            "label": program,
                            "program_code": data["program_code"][index],
                            "great_area": data["great_area"][index],
                            "knowledge_area": data["knowledge_area"][index],
                            "evaluation_area": data["evaluation_area"][index],
                        },
                    )
                if inst_key and data["great_area"][index]:
                    for advisor in str(data["advisors"][index] or "").split(";"):
                        advisor_key = name_key(advisor)
                        if advisor_key:
                            area = {
                                "great_area": data["great_area"][index],
                                "knowledge_area": data["knowledge_area"][index],
                                "evaluation_area": data["evaluation_area"][index],
                            }
                            if advisor_key not in tables.advisor_area:
                                tables.advisor_area[advisor_key] = area
                            elif tables.advisor_area[advisor_key] not in (None, area):
                                # Same name in two areas: too ambiguous to use.
                                tables.advisor_area[advisor_key] = None
                            tables.advisor_program.setdefault(
                                (inst_key, advisor_key),
                                {
                                    "label": program,
                                    "program_code": data["program_code"][index],
                                    "great_area": data["great_area"][index],
                                    "knowledge_area": data["knowledge_area"][index],
                                    "evaluation_area": data["evaluation_area"][index],
                                },
                            )
                great = data["great_area"][index]
                knowledge = data["knowledge_area"][index]
                if great:
                    tables.great_labels.setdefault(name_key(great), str(great))
                if knowledge:
                    tables.knowledge_labels.setdefault(
                        name_key(knowledge), str(knowledge)
                    )
                title = data["title"][index]
                key = title_key(title)
                if key and key in candidate_titles:
                    tables.matching_authors.setdefault(key, set()).update(
                        author_tokens(data["author"][index])
                    )
                url = data["url"][index]
                if url and candidate_capes_ids:
                    match = _ID_TRABALHO.search(str(url))
                    if match and match.group(1) in candidate_capes_ids:
                        tables.capes_ids.add(match.group(1))
                tables.records += 1
    return tables


def _cnpq_areas_from_value(
    value: str, tables: ExistingTables
) -> tuple[str | None, str | None]:
    """Map one CNPq subject tree to existing great/knowledge labels."""
    parts = [part.strip() for part in value.split("::") if part.strip()]
    if parts and parts[0].casefold() == "cnpq":
        parts = parts[1:]
    if not parts:
        return None, None
    # Only labels that already exist in the catalogue are accepted, so the
    # area facets never gain free-text variants.
    great = tables.great_labels.get(name_key(parts[0]))
    knowledge = tables.knowledge_labels.get(name_key(parts[1])) if len(parts) > 1 else None
    if great is None:
        knowledge = None
    return great, knowledge


def acronym_key(value: Any) -> str:
    """``PUC_RIO``/``PUC-RIO`` -> ``PUCRIO`` (letters and digits, uppercase)."""
    text = clean_text(value)
    if not text:
        return ""
    return re.sub(r"[^0-9A-Z]", "", strip_accents(text).upper())


def _institution_from_acronym(acronym: Any, tables: ExistingTables) -> str | None:
    """Return the dominant existing institution label for an acronym."""
    labels = tables.acronym_labels.get(acronym_key(acronym))
    if not labels:
        return None
    label, count = labels.most_common(1)[0]
    if count < 0.8 * sum(labels.values()):
        return None
    return label


def enrich_record(record: dict[str, Any], tables: ExistingTables) -> None:
    """Align institution/program labels with the existing metadata and fill
    location and area fields from it.

    The institution is matched by name (ignoring case and accents) and, when
    the name does not match, by acronym. Matched records take the existing
    labels, so facets do not split between ``Universidade X`` and
    ``UNIVERSIDADE X``. Unmatched names are upper-cased for the same reason.
    """
    inst_key = name_key(record.get("institution"))
    inst = tables.institution.get(inst_key) if inst_key else None
    if inst is None:
        label = _institution_from_acronym(record.get("institution_acronym"), tables)
        if label:
            inst_key = name_key(label)
            inst = tables.institution.get(inst_key)
    if inst is not None:
        record["institution"] = inst.get("label") or record.get("institution")
        if inst.get("institution_acronym"):
            record["institution_acronym"] = inst["institution_acronym"]
        for column in ("uf", "region"):
            if not record.get(column) and inst.get(column):
                record[column] = inst[column]
    elif record.get("institution"):
        record["institution"] = str(record["institution"]).upper()
    if not inst_key:
        return
    program_key = name_key(clean_program(record.get("program")))
    program = tables.program.get((inst_key, program_key)) if program_key else None
    if program is None and not program_key:
        # Many repositories omit the programme. The main advisor usually
        # supervises in one programme of the same institution, so the most
        # recent programme where they advised is a good stand-in.
        first_advisor = str(record.get("advisors") or "").split(";")[0]
        first_advisor = re.sub(r"\s*\(coorientador\)\s*$", "", first_advisor)
        advisor_key = name_key(first_advisor)
        if advisor_key:
            program = tables.advisor_program.get((inst_key, advisor_key))
            if program is None and len(advisor_key.split()) >= 3:
                # Campus-level institution labels vary (e.g. UNESP); a full
                # advisor name seen in a single area is still a safe hint.
                area = tables.advisor_area.get(advisor_key)
                if area:
                    for column, value in area.items():
                        if not record.get(column) and value:
                            record[column] = value
    if program:
        record["program"] = program.get("label") or record.get("program")
        for column in (
            "program_code",
            "great_area",
            "knowledge_area",
            "evaluation_area",
        ):
            if not record.get(column) and program.get(column):
                record[column] = program[column]
    elif record.get("program"):
        record["program"] = str(clean_program(record["program"]) or record["program"]).upper()


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _filled(record: dict[str, Any]) -> int:
    return sum(
        1 for column in METADATA_COLUMNS if record.get(column) not in (None, "")
    )


def _merge_into(base: dict[str, Any], donor: dict[str, Any]) -> None:
    """Copy missing values from *donor* into *base* (never overwrite)."""
    for column in METADATA_COLUMNS:
        if column in {"id", "source"}:
            continue
        if base.get(column) in (None, "") and donor.get(column) not in (None, ""):
            base[column] = donor[column]


def _components(group: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group records that share at least one author token."""
    parent = list(range(len(group)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    tokens = [author_tokens(record.get("author")) for record in group]
    for left in range(len(group)):
        if not tokens[left]:
            continue
        for right in range(left + 1, len(group)):
            if tokens[left] & tokens[right]:
                union(left, right)
    components: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, record in enumerate(group):
        components[find(index)].append(record)
    return list(components.values())


def _merge_component(component: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Pick a base record and complete it with the other duplicates."""
    capes = [record for record in component if record["source"] == CAPES_SOURCE]
    pool = capes or list(component)
    base = max(pool, key=lambda record: (_filled(record), record["id"]))
    for donor in sorted(component, key=_filled, reverse=True):
        if donor is not base:
            _merge_into(base, donor)
    return dict(base)


def _is_already_published(
    record: dict[str, Any], tables: ExistingTables
) -> bool:
    """Return whether the record is already in the existing metadata."""
    if record["source"] == CAPES_SOURCE:
        identifier = record["id"].split(":", 1)[1]
        if identifier in tables.capes_ids:
            return True
    key = title_key(record.get("title"))
    tokens = author_tokens(record.get("author"))
    existing = tables.matching_authors.get(key)
    if existing is None:
        return False
    if not tokens:
        return False
    return bool(tokens & existing)


@dataclass
class NormalizeStats:
    """Counters reported by the normalisation step."""

    raw_records: int = 0
    invalid: int = 0
    before_first_year: int = 0
    already_published: int = 0
    duplicate_ids: int = 0
    cross_source_duplicates: int = 0
    bdtd_duplicates: int = 0
    capes_duplicates: int = 0
    by_source_year: dict[str, Counter[int]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    merged_by_source_year: dict[str, Counter[int]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    existing_records: int = 0
    written: int = 0


def normalize_records(
    raw_dir: Path,
    existing_dir: Path,
    *,
    first_year: int,
) -> tuple[list[dict[str, Any]], NormalizeStats, ExistingTables]:
    """Map, enrich and deduplicate the raw records."""
    stats = NormalizeStats()
    mapped: list[dict[str, Any]] = []
    cnpq_by_id: dict[str, str] = {}
    for source, raw in iter_raw_records(raw_dir):
        stats.raw_records += 1
        record = (
            parse_bdtd_record(raw)
            if source == BDTD_SOURCE
            else parse_capes_record(raw)
        )
        if record is None:
            stats.invalid += 1
            continue
        if record.get("year") is not None and record["year"] > date.today().year:
            # Typo in the source (e.g. 2925): the defence cannot be in the future.
            stats.invalid += 1
            continue
        if record.get("year") is None or record["year"] < first_year:
            stats.before_first_year += 1
            continue
        stats.by_source_year[source][int(record["year"])] += 1
        if source == BDTD_SOURCE:
            cnpq = _first(raw, "dc.subject.cnpq.fl_str_mv")
            if cnpq:
                cnpq_by_id[record["id"]] = cnpq
        mapped.append(record)

    candidate_titles = {
        key for record in mapped if (key := title_key(record.get("title")))
    }
    candidate_capes_ids = {
        record["id"].split(":", 1)[1]
        for record in mapped
        if record["source"] == CAPES_SOURCE
    }
    tables = load_existing_tables(
        existing_dir,
        candidate_titles=candidate_titles,
        candidate_capes_ids=candidate_capes_ids,
    )
    stats.existing_records = tables.records

    for record in mapped:
        enrich_record(record, tables)
        cnpq = cnpq_by_id.get(record["id"])
        if cnpq and not record.get("great_area"):
            great, knowledge = _cnpq_areas_from_value(cnpq, tables)
            record["great_area"] = great
            record["knowledge_area"] = record.get("knowledge_area") or knowledge

    kept: list[dict[str, Any]] = []
    for record in mapped:
        if _is_already_published(record, tables):
            stats.already_published += 1
            continue
        kept.append(record)

    deduped: dict[str, dict[str, Any]] = {}
    for record in kept:
        existing = deduped.get(record["id"])
        if existing is None:
            deduped[record["id"]] = record
            continue
        stats.duplicate_ids += 1
        deduped[record["id"]] = _merge_component([existing, record])

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in deduped.values():
        key = title_key(record.get("title")) or f"__id__:{record['id']}"
        groups[key].append(record)

    merged: list[dict[str, Any]] = []
    for group in groups.values():
        for component in _components(group):
            if len(component) > 1:
                sources = {record["source"] for record in component}
                if len(sources) > 1:
                    stats.cross_source_duplicates += 1
                elif CAPES_SOURCE in sources:
                    stats.capes_duplicates += len(component) - 1
                else:
                    stats.bdtd_duplicates += len(component) - 1
            merged.append(_merge_component(component))

    merged.sort(key=lambda record: (record["year"], record["id"]))
    for record in merged:
        stats.merged_by_source_year[record["source"]][int(record["year"])] += 1
    stats.written = len(merged)
    return merged, stats, tables


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _validate_existing_schema(metadata_dir: Path) -> pa.Schema:
    """Return the schema of the last existing part (or the shared schema)."""
    parts = _metadata_parts(metadata_dir)
    if not parts:
        return METADATA_SCHEMA
    schema = pq.ParquetFile(parts[-1]).schema_arrow
    if schema.names != list(METADATA_COLUMNS) or any(
        schema.field(name).type != METADATA_SCHEMA.field(name).type
        for name in METADATA_COLUMNS
    ):
        raise ValueError(
            f"O esquema de {parts[-1].name} difere do esperado pela pipeline"
        )
    return schema


def _next_part_number(metadata_dir: Path) -> int:
    numbers = [
        int(match.group(1))
        for path in _metadata_parts(metadata_dir)
        if (match := re.search(r"part-(\d+)\.parquet$", path.name))
    ]
    return max(numbers) + 1 if numbers else 0


def write_metadata(
    records: Sequence[dict[str, Any]],
    out_dir: Path,
    existing_dir: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write one zstd Parquet part with exactly the existing schema.

    Existing parts are never deleted silently: a stale part from an earlier
    run is only removed when *overwrite* is true.
    """
    schema = _validate_existing_schema(existing_dir)
    out_dir = Path(out_dir)
    if out_dir.resolve() == Path(existing_dir).resolve():
        raise ValueError(
            "O diretório de saída não pode ser o mesmo dos metadados existentes"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    part_number = _next_part_number(existing_dir)
    path = out_dir / f"part-{part_number:03d}.parquet"
    stale = sorted(
        item
        for item in out_dir.glob("part-*.parquet")
        if item.resolve() != path.resolve()
    )
    if stale:
        if not overwrite:
            names = ", ".join(item.name for item in stale)
            raise ValueError(
                f"Já existem partes de outra execução em {out_dir} ({names}); "
                "use --overwrite para substituí-las"
            )
        for item in stale:
            item.unlink()
    columns = [
        [record.get(column) for record in records] for column in METADATA_COLUMNS
    ]
    table = pa.Table.from_arrays(
        [
            pa.array(values, type=schema.field(column).type)
            for column, values in zip(METADATA_COLUMNS, columns, strict=True)
        ],
        schema=schema,
    )
    pq.write_table(
        table,
        path,
        compression="zstd",
        row_group_size=PART_BATCH_ROWS,
    )
    return path


def _null_percent(records: Sequence[dict[str, Any]]) -> dict[str, float]:
    if not records:
        return {column: 100.0 for column in METADATA_COLUMNS}
    total = len(records)
    return {
        column: round(
            100.0
            * sum(
                1 for record in records if record.get(column) in (None, "")
            )
            / total,
            2,
        )
        for column in METADATA_COLUMNS
    }


def normalize_recent(
    raw_dir: Path,
    existing_dir: Path,
    out_dir: Path,
    *,
    report_path: Path | None = None,
    first_year: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run the whole normalisation and write the report."""
    raw_dir = Path(raw_dir)
    existing_dir = Path(existing_dir)
    out_dir = Path(out_dir)
    has_pages = any(_iter_page_files(raw_dir, BDTD_DIR)) or any(
        _iter_page_files(raw_dir, CAPES_DIR)
    )
    if not has_pages:
        raise FileNotFoundError(f"Nenhuma página bruta encontrada em {raw_dir}")

    if first_year is None:
        last = _last_existing_year(existing_dir)
        first_year = (last + 1) if last is not None else 2025

    records, stats, _tables = normalize_records(
        raw_dir, existing_dir, first_year=first_year
    )

    path = write_metadata(records, out_dir, existing_dir, overwrite=overwrite)
    report_path = (
        Path(report_path)
        if report_path
        else out_dir.parent / "normalize_recent_report.json"
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "first_year": first_year,
        "raw_records": stats.raw_records,
        "invalid": stats.invalid,
        "before_first_year": stats.before_first_year,
        "already_published": stats.already_published,
        "duplicate_ids": stats.duplicate_ids,
        "cross_source_duplicates": stats.cross_source_duplicates,
        "bdtd_duplicates": stats.bdtd_duplicates,
        "capes_duplicates": stats.capes_duplicates,
        "existing_records_scanned": stats.existing_records,
        "mapped_by_source_year": {
            source: {str(year): count for year, count in sorted(counter.items())}
            for source, counter in stats.by_source_year.items()
        },
        "output": {
            "path": str(path),
            "records": stats.written,
            "by_source_year": {
                source: {str(year): count for year, count in sorted(counter.items())}
                for source, counter in stats.merged_by_source_year.items()
            },
            "by_year": {
                str(year): count
                for year, count in sorted(
                    Counter(int(record["year"]) for record in records).items()
                )
            },
        },
        "null_percent": _null_percent(records),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    LOGGER.info(
        "Concluído: %d registro(s) em %s (%d já publicados, %d entre fontes)",
        stats.written,
        path,
        stats.already_published,
        stats.cross_source_duplicates,
    )
    return report


def _last_existing_year(metadata_dir: Path) -> int | None:
    parts = _metadata_parts(metadata_dir)
    if not parts:
        return None
    years = pq.read_table(parts[-1], columns=["year"]).column("year")
    values = [value for value in years.to_pylist() if value is not None]
    return max(values) if values else None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Normaliza os trabalhos recentes da BDTD e do catálogo da CAPES"
    )
    parser.add_argument(
        "--raw", type=Path, default=Path("tools/dataset/work/recent/raw")
    )
    parser.add_argument(
        "--existing", type=Path, default=Path("tools/dataset/work/metadata")
    )
    parser.add_argument(
        "--out", type=Path, default=Path("tools/dataset/work/recent/metadata")
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("tools/dataset/work/recent/normalize_recent_report.json"),
    )
    parser.add_argument(
        "--first-year",
        type=int,
        default=None,
        help="primeiro ano a manter (padrão: seguinte ao último dos metadados)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="remove partes de uma execução anterior no diretório de saída",
    )
    arguments = parser.parse_args(argv)
    normalize_recent(
        arguments.raw,
        arguments.existing,
        arguments.out,
        report_path=arguments.report,
        first_year=arguments.first_year,
        overwrite=arguments.overwrite,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
