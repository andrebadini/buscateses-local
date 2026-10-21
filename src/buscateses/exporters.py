"""Export search results as CSV, BibTeX, RIS or an approximate ABNT reference."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from buscateses.textcase import display_record, name_case

EXPORT_FORMATS = {"csv", "bibtex", "ris", "abnt"}

_CSV_COLUMNS = (
    ("id", "ID"),
    ("title", "Título"),
    ("author", "Autor"),
    ("advisors", "Orientadores"),
    ("year", "Ano"),
    ("degree", "Grau"),
    ("institution", "Instituição"),
    ("institution_acronym", "Sigla"),
    ("uf", "UF"),
    ("program", "Programa"),
    ("great_area", "Grande área"),
    ("knowledge_area", "Área do conhecimento"),
    ("keywords", "Palavras-chave"),
    ("pages", "Páginas"),
    ("language", "Idioma"),
    ("url", "URL"),
    ("abstract", "Resumo"),
)

_BIBTEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def export_csv(records: Sequence[Mapping[str, Any]]) -> str:
    """Return UTF-8 CSV text with BOM and semicolon separators for pt-BR Excel."""
    output = io.StringIO(newline="")
    output.write("﻿")
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([label for _key, label in _CSV_COLUMNS])
    for raw_record in records:
        record = display_record(raw_record)
        row: list[Any] = []
        for key, _label in _CSV_COLUMNS:
            value = record.get(key)
            row.append("" if value is None else _csv_safe(value))
        writer.writerow(row)
    return output.getvalue()


def export_bibtex(records: Sequence[Mapping[str, Any]]) -> str:
    """Return a BibTeX bibliography with unique citation keys."""
    entries: list[str] = []
    used: set[str] = set()
    for raw_record in records:
        record = display_record(raw_record)
        key = _unique_bibtex_key(record, used)
        entry_type = _bibtex_type(record.get("degree"))
        fields: list[tuple[str, str]] = []
        _append_bibtex(fields, "author", record.get("author"))
        _append_bibtex(fields, "title", record.get("title"))
        _append_bibtex(fields, "school", record.get("institution"))
        _append_bibtex(fields, "address", record.get("uf"))
        _append_bibtex(fields, "year", record.get("year"))
        _append_bibtex(fields, "type", record.get("degree"))
        _append_bibtex(fields, "keywords", _keywords_for_bibtex(record.get("keywords")))
        _append_bibtex(fields, "url", record.get("url"))
        if record.get("advisors"):
            _append_bibtex(fields, "note", f"Orientadores: {record['advisors']}")
        if record.get("abstract"):
            _append_bibtex(fields, "abstract", record.get("abstract"))
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
        entries.append(f"@{entry_type}{{{key},\n{body}\n}}")
    return "\n\n".join(entries) + ("\n" if entries else "")


def export_ris(records: Sequence[Mapping[str, Any]]) -> str:
    """Return a RIS bibliography using ``TY  - THES``."""
    blocks: list[str] = []
    for raw_record in records:
        record = display_record(raw_record)
        lines = ["TY  - THES"]
        _append_ris(lines, "TI", record.get("title"))
        _append_ris(lines, "AU", record.get("author"))
        advisors = _split_people(record.get("advisors"))
        for advisor in advisors:
            _append_ris(lines, "A2", advisor)
        _append_ris(lines, "PY", record.get("year"))
        _append_ris(lines, "PB", record.get("institution"))
        _append_ris(lines, "CY", record.get("uf"))
        _append_ris(lines, "M3", record.get("degree"))
        _append_ris(lines, "AB", record.get("abstract"))
        _append_ris(lines, "UR", record.get("url"))
        for keyword in _split_keywords(record.get("keywords")):
            _append_ris(lines, "KW", keyword)
        lines.append("ER  -")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def abnt_reference(record: Mapping[str, Any]) -> str:
    """Return an approximate ABNT reference in plain text (no markdown)."""
    record = display_record(record)
    pieces: list[str] = []
    author = _abnt_author(record.get("author"))
    title = _clean_text(record.get("title"))
    year = _clean_text(record.get("year"))
    pages = _as_positive_int(record.get("pages"))
    degree = _clean_text(record.get("degree"))
    program = _clean_text(record.get("program"))
    institution = _clean_text(record.get("institution"))

    if author:
        pieces.append(f"{author}.")
    if title:
        pieces.append(f"{title.rstrip('.')}.")
    if year:
        pieces.append(f"{year}.")
    if pages:
        pieces.append(f"{pages} f.")
    work = _work_description(degree, program)
    if work:
        pieces.append(work)
    tail = institution or ""
    if institution and year:
        tail = f"{institution}, {year}"
    if tail:
        pieces.append(f"– {tail}.")
    return " ".join(pieces).replace("..", ".").strip()


def export_abnt(records: Sequence[Mapping[str, Any]]) -> str:
    """Return one plain-text ABNT reference per record."""
    references = [abnt_reference(record) for record in records]
    return "\n\n".join(item for item in references if item) + ("\n" if references else "")


def export_records(records: Sequence[Mapping[str, Any]], fmt: str) -> str:
    """Dispatch to the requested exporter."""
    clean_format = str(fmt).strip().lower()
    if clean_format == "csv":
        return export_csv(records)
    if clean_format == "bibtex":
        return export_bibtex(records)
    if clean_format == "ris":
        return export_ris(records)
    if clean_format == "abnt":
        return export_abnt(records)
    raise ValueError("Formato de exportação inválido.")


def _csv_safe(value: Any) -> Any:
    """Neutralise spreadsheet formulas (CSV injection) in exported text."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _bibtex_type(degree: Any) -> str:
    text = str(degree or "").casefold()
    if text.startswith("doutorado"):
        return "phdthesis"
    if text.startswith("mestrado"):
        return "mastersthesis"
    return "phdthesis"


def _unique_bibtex_key(record: Mapping[str, Any], used: set[str]) -> str:
    surname = _ascii_slug(_surname(str(record.get("author") or ""))) or "semautor"
    year = re.sub(r"[^0-9]", "", str(record.get("year") or "")) or "sd"
    title = str(record.get("title") or "")
    words = re.findall(r"[^\W_]+", title, re.UNICODE)
    word = _ascii_slug(words[0]) if words else "obra"
    base = f"{surname}{year}{word}" or "buscateses"
    candidate = base
    suffix = 1
    while candidate in used:
        candidate = f"{base}{chr(ord('a') + suffix - 1)}"
        suffix += 1
    used.add(candidate)
    return candidate


def _ascii_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_text.casefold())


def _surname(author: str) -> str:
    clean = author.strip()
    if not clean:
        return ""
    if "," in clean:
        return clean.split(",", 1)[0].strip()
    parts = clean.split()
    return parts[-1] if parts else ""


def _append_bibtex(fields: list[tuple[str, str]], name: str, value: Any) -> None:
    clean = _clean_text(value)
    if clean:
        fields.append((name, _escape_bibtex(clean)))


def _escape_bibtex(value: str) -> str:
    return "".join(_BIBTEX_SPECIAL.get(character, character) for character in value)


def _keywords_for_bibtex(value: Any) -> str:
    return "; ".join(_split_keywords(value))


def _append_ris(lines: list[str], tag: str, value: Any) -> None:
    clean = _clean_text(value)
    if clean:
        lines.append(f"{tag}  - {clean.replace(chr(10), ' ')}")


def _split_keywords(value: Any) -> list[str]:
    if value is None:
        return []
    return [item.strip() for item in re.split(r"[;,]", str(value)) if item.strip()]


def _split_people(value: Any) -> list[str]:
    if value is None:
        return []
    return [item.strip() for item in str(value).split(";") if item.strip()]


def _abnt_author(value: Any) -> str:
    clean = _clean_text(value)
    if not clean:
        return ""
    if "," in clean:
        surname, given = clean.split(",", 1)
        return f"{surname.strip().upper()}, {name_case(given.strip())}".strip(", ")
    parts = clean.split()
    if len(parts) == 1:
        return parts[0].upper()
    surname = parts[-1]
    given = " ".join(parts[:-1])
    return f"{surname.upper()}, {name_case(given)}"


def _work_description(degree: str | None, program: str | None) -> str:
    if not degree:
        return ""
    lowered = degree.casefold()
    if lowered.startswith("doutorado"):
        kind = "Tese"
    elif lowered.startswith("mestrado"):
        kind = "Dissertação"
    else:
        kind = "Trabalho"
    detail = degree
    if program and program.casefold() not in degree.casefold():
        detail = f"{degree} em {program}"
    return f"{kind} ({detail})"


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    clean = re.sub(r"\s+", " ", str(value)).strip()
    return clean or None


def _as_positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


__all__ = [
    "EXPORT_FORMATS",
    "abnt_reference",
    "export_abnt",
    "export_bibtex",
    "export_csv",
    "export_records",
    "export_ris",
]
