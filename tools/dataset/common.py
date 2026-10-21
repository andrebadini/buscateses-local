"""Shared constants and helpers for the BuscaTeses dataset pipeline.

The pipeline turns the CAPES open-data CSVs into a versioned Parquet release
(metadata plus EmbeddingGemma vectors).  This module keeps the pieces that must
stay identical across the stages: package names, column mappings, cleaning
rules, Arrow schema, hashing and tokenizer loading.
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import logging
import os
import re
import sys
import unicodedata
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pyarrow as pa

# ---------------------------------------------------------------------------
# Source packages (CKAN)
# ---------------------------------------------------------------------------

CKAN_BASE_URL = "https://dadosabertos.capes.gov.br"
CKAN_PACKAGE_SHOW = CKAN_BASE_URL + "/api/3/action/package_show"

PACKAGES: tuple[str, ...] = (
    "1987-a-2012-catalogo-de-teses-e-dissertacoes-brasil",
    "catalogo-de-teses-e-dissertacoes-de-2013-a-2016",
    "2017-2020-catalogo-de-teses-e-dissertacoes-da-capes",
    "2021-a-2024-catalogo-de-teses-e-dissertacoes-brasil",
)

SOURCE_NAME = "CAPES — Catálogo de Teses e Dissertações"
SOURCE_URL = CKAN_BASE_URL
SOURCE_LICENSE = "CC-BY"

PACKAGE_URLS: dict[str, str] = {name: f"{CKAN_BASE_URL}/dataset/{name}" for name in PACKAGES}

# ---------------------------------------------------------------------------
# Embeddings / tokenizer
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = "google/embeddinggemma-300m"
ONNX_REPO = "onnx-community/embeddinggemma-300m-ONNX"
#: Pinned revision of the ONNX repository used by the app and the pipeline.
EMBEDDING_REVISION = "5090578d9565bb06545b4552f76e6bc2c93e4a66"
TOKENIZER_FILENAME = "tokenizer.json"
FULL_DIMS = 768
APP_DIMS = 256
MAX_DOCUMENT_TOKENS = 512
DOCUMENT_TEMPLATE = "title: {title} | text: {text}"
QUERY_TEMPLATE = "task: search result | query: {query}"
PROBE_TEXT = QUERY_TEMPLATE.format(query="educação inclusiva")

# ---------------------------------------------------------------------------
# Cleaning rules
# ---------------------------------------------------------------------------

# Values that mean "no information" after trimming.  Comparison is casefolded.
NULL_TOKENS: frozenset[str] = frozenset(
    {
        "",
        "-",
        "--",
        "nao informado",
        "não informado",
        "null",
    }
)

_WHITESPACE_RE = re.compile(r"\s+")
_LEGACY_KEYWORD_SEP_RE = re.compile(r"[ \t]{2,}")
_DIGITS_RE = re.compile(r"^\d+$")

MONTHS: dict[str, int] = {
    "jan": 1,
    "fev": 2,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "apr": 4,
    "mai": 5,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "aug": 8,
    "set": 9,
    "sep": 9,
    "out": 10,
    "oct": 10,
    "nov": 11,
    "dez": 12,
    "dec": 12,
}

DEGREE_MAP: dict[str, str] = {
    "mestrado": "Mestrado",
    "mestrado academico": "Mestrado",
    "doutorado": "Doutorado",
    "doutorado academico": "Doutorado",
    "mestrado profissional": "Mestrado Profissional",
    "doutorado profissional": "Doutorado Profissional",
    "profissionalizante": "Mestrado Profissional",
    "dissertacao": "Mestrado",
    "tese": "Doutorado",
}

# Columns that must never reach the public output (personal data/identifiers).
FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "documentodiscente",
        "documentoorientador_1",
        "documentoorientador_2",
        "documentoorientador_3",
        "documentoorientador_4",
        "documentocoorientador_1",
        "documentocoorientador_2",
        "documentocoorientador_3",
        "documentocoorientador_4",
        "id_pessoa_discente",
        "id_pessoa_orientador",
        "dt_matricula",
        "nm_tp_vinculo",
        "id_tp_expectativa_atuacao",
        "nm_expectativa_atuacao",
        "ds_categoria_orientador",
        "nm_categoria_docente",
    }
)
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = ("documento", "cpf", "pessoa", "pdf_path")

# ---------------------------------------------------------------------------
# Output schema (SPEC §3.1)
# ---------------------------------------------------------------------------

METADATA_FIELDS: tuple[tuple[str, pa.DataType], ...] = (
    ("id", pa.string()),
    ("year", pa.int16()),
    ("defense_date", pa.string()),
    ("title", pa.string()),
    ("author", pa.string()),
    ("advisors", pa.string()),
    ("degree", pa.string()),
    ("institution", pa.string()),
    ("institution_acronym", pa.string()),
    ("uf", pa.string()),
    ("region", pa.string()),
    ("program", pa.string()),
    ("program_code", pa.string()),
    ("great_area", pa.string()),
    ("knowledge_area", pa.string()),
    ("evaluation_area", pa.string()),
    ("concentration_area", pa.string()),
    ("research_line", pa.string()),
    ("keywords", pa.string()),
    ("keywords_en", pa.string()),
    ("abstract", pa.string()),
    ("abstract_en", pa.string()),
    ("language", pa.string()),
    ("pages", pa.int32()),
    ("library", pa.string()),
    ("url", pa.string()),
    ("source", pa.string()),
)

METADATA_SCHEMA = pa.schema([pa.field(name, dtype) for name, dtype in METADATA_FIELDS])
METADATA_COLUMNS: tuple[str, ...] = tuple(name for name, _ in METADATA_FIELDS)
STAGE_SCHEMA = METADATA_SCHEMA.append(pa.field("__row", pa.int64()))

EMBEDDING_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("embedding", pa.list_(pa.float16(), FULL_DIMS)),
    ]
)
EMBEDDING_256_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("embedding", pa.list_(pa.float16(), APP_DIMS)),
    ]
)

# ---------------------------------------------------------------------------
# Column mappings (SPEC §3.1) — map by header name, never by position.
# ---------------------------------------------------------------------------

LEGACY_SCHEMA_NAME = "legacy"
MODERN_SCHEMA_NAME = "modern"

LEGACY_COLUMNS: dict[str, str] = {
    "year": "AnoBase",
    "defense_date": "DataDefesa",
    "title": "TituloTese",
    "author": "Autor",
    "degree": "Nivel",
    "institution": "NomeIes",
    "institution_acronym": "SiglaIes",
    "uf": "Uf",
    "region": "Regiao",
    "program": "NomePrograma",
    "program_code": "CodigoPrograma",
    "great_area": "GrandeAreaDescricao",
    "knowledge_area": "AreaConhecimento",
    "evaluation_area": "AreaAvaliacao",
    "research_line": "LinhaPesquisa",
    "keywords": "PalavrasChave",
    "abstract": "ResumoTese",
    "language": "Idioma",
    "pages": "NumeroPaginas",
    "library": "BibliotecaDepositaria",
    "url": "URLTextoCompleto",
}

MODERN_COLUMNS: dict[str, str] = {
    "year": "AN_BASE",
    "defense_date": "DT_TITULACAO",
    "title": "NM_PRODUCAO",
    "author": "NM_DISCENTE",
    "advisors": "NM_ORIENTADOR",
    "degree": "NM_GRAU_ACADEMICO",
    "degree_fallback": "NM_SUBTIPO_PRODUCAO",
    "institution": "NM_ENTIDADE_ENSINO",
    "institution_acronym": "SG_ENTIDADE_ENSINO",
    "uf": "SG_UF_IES",
    "region": "NM_REGIAO",
    "program": "NM_PROGRAMA",
    "program_code": "CD_PROGRAMA",
    "great_area": "NM_GRANDE_AREA_CONHECIMENTO",
    "knowledge_area": "NM_AREA_CONHECIMENTO",
    "evaluation_area": "NM_AREA_AVALIACAO",
    "concentration_area": "NM_AREA_CONCENTRACAO",
    "research_line": "NM_LINHA_PESQUISA",
    "keywords": "DS_PALAVRA_CHAVE",
    "keywords_en": "DS_KEYWORD",
    "abstract": "DS_RESUMO",
    "abstract_en": "DS_ABSTRACT",
    "language": "NM_IDIOMA",
    "pages": "NR_PAGINAS",
    "library": "DS_BIBLIOTECA_DEPOSITARIA",
    "url": "DS_URL_TEXTO_COMPLETO",
    "id": "ID_ADD_PRODUCAO_INTELECTUAL",
}

# ---------------------------------------------------------------------------
# Logging and filesystem helpers
# ---------------------------------------------------------------------------


def setup_logging(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a stderr logger with a single handler."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
        )
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Return the hexadecimal SHA-256 digest of *path*."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def file_size(path: Path) -> int:
    """Return the size of *path* in bytes."""
    return path.stat().st_size


# ---------------------------------------------------------------------------
# Text/date/identifier normalisation
# ---------------------------------------------------------------------------


def strip_accents(text: str) -> str:
    """Return *text* without diacritics (NFKD, combining marks removed)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def clean_text(value: Any) -> str | None:
    """Trim, collapse whitespace and map empty/sentinel values to ``None``."""
    if value is None:
        return None
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if text.casefold() in NULL_TOKENS:
        return None
    return text or None


def normalize_keywords(value: Any, *, legacy: bool = False) -> str | None:
    """Return keywords joined with ``"; "``.

    ``;`` wins over ``,``; for the legacy schema a run of two or more spaces is
    also treated as a separator when neither ``;`` nor ``,`` is present.
    """
    if value is None:
        return None
    raw = str(value).replace("\r", " ").replace("\n", " ")
    if clean_text(raw) is None:
        return None
    if ";" in raw:
        parts: Iterable[str] = raw.split(";")
    elif "," in raw:
        parts = raw.split(",")
    elif legacy and _LEGACY_KEYWORD_SEP_RE.search(raw):
        parts = _LEGACY_KEYWORD_SEP_RE.split(raw)
    else:
        parts = (raw,)
    cleaned = [item for item in (clean_text(part) for part in parts) if item]
    return "; ".join(cleaned) or None


def normalize_degree(value: Any) -> str | None:
    """Map CAPES degree labels to the canonical set used by the app."""
    text = clean_text(value)
    if text is None:
        return None
    key = strip_accents(text).casefold()
    return DEGREE_MAP.get(key, text)


def _coerce_year(year: int) -> int:
    return 2000 + year if year <= 30 else 1900 + year


def normalize_date(value: Any) -> str | None:
    """Parse the date formats found in the CAPES files into ``YYYY-MM-DD``.

    Accepted shapes: ``dd/mm/yyyy``, ``dd/mm/yy``, ``ddMMMyyyy`` (English
    month) and the ``ddMMMyyyy:HH:MM:SS`` variant used from 2013 on.  Month
    abbreviations may be Portuguese or English.
    """
    text = clean_text(value)
    if text is None:
        return None

    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?", text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if match:
        day, month, year = (int(part) for part in match.groups())
    else:
        match = re.fullmatch(
            r"(\d{1,2})/?([A-Za-z]{3})/?(\d{2,4})(?::\d{2}:\d{2}:\d{2})?", text
        )
        if not match:
            return None
        day = int(match.group(1))
        month = MONTHS.get(match.group(2).casefold(), 0)
        year = int(match.group(3))
    if year < 100:
        year = _coerce_year(year)
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


INT32_MAX = 2_147_483_647


def normalize_pages(value: Any) -> int | None:
    """Return a page count that fits in ``int32``, else ``None``.

    CAPES uses sentinel values such as ``9999999999`` for unknown page counts;
    those must not reach the Arrow writer because ``pages`` is an ``int32``.
    """
    text = clean_text(value)
    if text is None or not _DIGITS_RE.fullmatch(text):
        return None
    pages = int(text)
    return pages if 0 <= pages <= INT32_MAX else None


def normalize_url(value: Any) -> str | None:
    """Keep only absolute http/https URLs with a non-empty host."""
    text = clean_text(value)
    if text is None or any(char.isspace() for char in text):
        return None
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return None
    return text


def normalize_year(value: Any) -> int | None:
    """Return the year as ``int`` when it is a four-digit number."""
    text = clean_text(value)
    if text is None:
        return None
    match = re.fullmatch(r"(\d{4})(?:\.0)?", text)
    return int(match.group(1)) if match else None


def legacy_id(year: int | None, program_code: str | None, author: str | None, title: str | None) -> str:
    """Build the deterministic ``capes87:`` identifier (SPEC §3.1)."""

    def normalise(part: Any) -> str:
        return _WHITESPACE_RE.sub(" ", strip_accents(str(part or "")).casefold()).strip()

    payload = "|".join(
        (
            normalise(year if year is not None else ""),
            normalise(program_code),
            normalise(author),
            normalise(title),
        )
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"capes87:{digest}"


def modern_id(value: Any) -> str | None:
    """Build the ``capes:`` identifier from ``ID_ADD_PRODUCAO_INTELECTUAL``."""
    identifier = clean_text(value)
    return f"capes:{identifier}" if identifier else None


# ---------------------------------------------------------------------------
# Schema detection / forbidden columns
# ---------------------------------------------------------------------------


def detect_schema(header: Iterable[str]) -> str:
    """Detect the source schema from the CSV header."""
    names = {name.lstrip("﻿").strip() for name in header}
    if "AnoBase" in names:
        return LEGACY_SCHEMA_NAME
    if "AN_BASE" in names:
        return MODERN_SCHEMA_NAME
    raise ValueError("Cabeçalho CSV não reconhecido: esperado AnoBase ou AN_BASE")


def is_forbidden_column(name: str) -> bool:
    """Return ``True`` when *name* is a personal-data/identifier column."""
    lowered = name.strip().casefold()
    if lowered in FORBIDDEN_COLUMNS:
        return True
    return any(part in lowered for part in FORBIDDEN_SUBSTRINGS)


# ---------------------------------------------------------------------------
# CSV encoding / tokenizer
# ---------------------------------------------------------------------------


def detect_encoding(path: Path, sample_size: int = 1 << 20) -> str:
    """Detect ``utf-8-sig``, ``utf-8`` or ``cp1252`` from a sample of *path*."""
    with path.open("rb") as handle:
        sample = handle.read(sample_size)
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    try:
        # final=False tolerates a multi-byte character cut at the sample edge.
        codecs.getincrementaldecoder("utf-8")(errors="strict").decode(sample, final=False)
    except UnicodeDecodeError:
        return "cp1252"
    return "utf-8"


def field_size_limit() -> None:
    """Allow very long CSV fields (abstracts, malformed quoting)."""
    csv.field_size_limit(sys.maxsize)


def load_tokenizer(model_dir: Path, token: str | None = None):
    """Load (and download if needed) ``tokenizer.json`` into *model_dir*.

    Downloads from the ONNX community repository so that the same tokenizer is
    used by the embedding step and by the app.
    """
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    tokenizer_path = model_dir / TOKENIZER_FILENAME
    if not tokenizer_path.is_file():
        token = token or os.environ.get("HF_TOKEN") or None
        hf_hub_download(
            repo_id=ONNX_REPO,
            filename=TOKENIZER_FILENAME,
            local_dir=str(model_dir),
            token=token,
        )
    return Tokenizer.from_file(str(tokenizer_path))
