#!/usr/bin/env python3
"""Build the Hugging Face dataset card: statistics, charts, banner and README.

The card is generated from the release itself, so every number, chart and
table matches the published files:

1. :func:`collect_stats` reads ``metadata/*.parquet`` with DuckDB and the
   ``manifest.json`` and returns a JSON-serialisable summary (also cached as
   ``card_stats.json`` next to the release, outside the publish allowlist).
2. :func:`render_assets` draws the banner and the charts as SVG (pure Python,
   no plotting dependency) in ``assets/``.
3. :func:`rasterize_assets` converts the SVGs to 2x PNGs with Playwright,
   embedding the app fonts (Literata and Atkinson Hyperlegible Next).  The Hub
   card links to the PNGs, which render the same everywhere.
4. :func:`render_card` writes ``README.md`` (Portuguese first, with a complete
   English section).

Usage::

    python tools/dataset/card.py --release /caminho/release
    python tools/dataset/card.py --release /caminho/release --no-png

``finalize.py`` calls :func:`build` with ``png=False``; run this CLI afterwards
(on a machine with Playwright) to produce the PNGs before ``publish.py``.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

from common import (
    APP_DIMS,
    DOCUMENT_TEMPLATE,
    EMBEDDING_MODEL,
    EMBEDDING_REVISION,
    FULL_DIMS,
    MAX_DOCUMENT_TOKENS,
    ONNX_REPO,
    PACKAGE_URLS,
    QUERY_TEMPLATE,
    SOURCE_URL,
    setup_logging,
)

LOGGER = setup_logging("card")

DATASET_REPO = "andrebadini/catalogo-teses-capes"
DATASET_URL = f"https://huggingface.co/datasets/{DATASET_REPO}"
ASSET_BASE_URL = f"{DATASET_URL}/resolve/main/assets"
APP_URL = "https://github.com/andrebadini/buscateses-local"
APP_ISSUES_URL = f"{APP_URL}/issues"
PIPELINE_URL = f"{APP_URL}/tree/main/tools/dataset"
MODEL_URL = f"https://huggingface.co/{EMBEDDING_MODEL}"
CAPES_CATALOGUE_URL = "https://catalogodeteses.capes.gov.br/catalogo-teses/"
SUCUPIRA_URL = "https://sucupira-legado.capes.gov.br/"
BDTD_URL = "https://bdtd.ibict.br/"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/deed.pt_BR"
STATS_FILENAME = "card_stats.json"
ASSETS_DIR = "assets"

#: Release notes shown on the card, newest first.  Add one entry per release.
RELEASE_NOTES: tuple[tuple[str, str, str], ...] = (
    (
        "2026.10.21.3",
        "Inclui 2025 e 2026 (Catálogo de Teses da CAPES e BDTD), com resumos, "
        "palavras-chave e orientação completados pela Plataforma Sucupira.",
        "Adds 2025 and 2026 (CAPES catalogue and BDTD), with abstracts, keywords "
        "and advisors completed from the Sucupira platform.",
    ),
    (
        "2026.10.21.2",
        "Versão intermediária com 2025 e 2026, substituída pela 2026.10.21.3 "
        "(correção dos resumos).",
        "Intermediate release with 2025 and 2026, superseded by 2026.10.21.3 (abstract fixes).",
    ),
    (
        "2026.10.21",
        "Primeira versão: dados abertos da CAPES, 1987–2024.",
        "First release: CAPES open data, 1987–2024.",
    ),
)

# --------------------------------------------------------------------------
# Palette (BuscaTeses brand)
# --------------------------------------------------------------------------
INK = "#12302C"
MUTED = "#5B6B68"
GRID = "#E4E1D8"
PAPER = "#FBFAF7"
BORDER = "#E3DFD3"
TEAL_DARK = "#0A3B37"
TEAL = "#0E4F4A"
TEAL_MID = "#15665E"
SAGE = "#7FB5AD"
GOLD = "#C99A2E"
GOLD_BRIGHT = "#E2B547"
TRACK = "#ECE9E0"

#: Source groups, in stacking order (bottom to top).
SOURCE_GROUPS: tuple[tuple[str, str, str, str], ...] = (
    ("capes", "Dados abertos da CAPES", "CAPES open data", TEAL),
    ("catalogo", "Catálogo de Teses da CAPES", "CAPES theses catalogue", GOLD),
    ("bdtd", "BDTD (IBICT)", "BDTD (IBICT)", SAGE),
)

GREAT_AREA_LABELS: dict[str, tuple[str, str]] = {
    "CIENCIAS HUMANAS": ("Ciências Humanas", "Humanities"),
    "CIENCIAS DA SAUDE": ("Ciências da Saúde", "Health Sciences"),
    "CIENCIAS SOCIAIS APLICADAS": ("Ciências Sociais Aplicadas", "Applied Social Sciences"),
    "ENGENHARIAS": ("Engenharias", "Engineering"),
    "MULTIDISCIPLINAR": ("Multidisciplinar", "Multidisciplinary"),
    "CIENCIAS AGRARIAS": ("Ciências Agrárias", "Agricultural Sciences"),
    "CIENCIAS EXATAS E DA TERRA": ("Ciências Exatas e da Terra", "Exact and Earth Sciences"),
    "CIENCIAS BIOLOGICAS": ("Ciências Biológicas", "Biological Sciences"),
    "LINGUISTICA, LETRAS E ARTES": ("Linguística, Letras e Artes", "Linguistics, Letters and Arts"),
}
REGION_LABELS: dict[str, tuple[str, str]] = {
    "SUDESTE": ("Sudeste", "Southeast"),
    "SUL": ("Sul", "South"),
    "NORDESTE": ("Nordeste", "Northeast"),
    "CENTRO-OESTE": ("Centro-Oeste", "Central-West"),
    "NORTE": ("Norte", "North"),
}
DEGREE_LABELS: dict[str, tuple[str, str]] = {
    "MESTRADO": ("Mestrado", "Master's"),
    "DOUTORADO": ("Doutorado", "Doctorate"),
    "MESTRADO PROFISSIONAL": ("Mestrado profissional", "Professional master's"),
    "DOUTORADO PROFISSIONAL": ("Doutorado profissional", "Professional doctorate"),
    "MESTRADO E PROFISSIONALIZANTE": ("Mestrado profissional", "Professional master's"),
}
LANGUAGE_LABELS: dict[str, tuple[str, str]] = {
    "PORTUGUES": ("Português", "Portuguese"),
    "INGLES": ("Inglês", "English"),
    "ESPANHOL": ("Espanhol", "Spanish"),
    "FRANCES": ("Francês", "French"),
    "ITALIANO": ("Italiano", "Italian"),
    "ALEMAO": ("Alemão", "German"),
}
NOT_INFORMED = ("Não informado", "Not informed")
OTHERS = ("Outros", "Others")

#: (key, SQL predicate, pt label, en label) for the completeness chart/table.
COVERAGE_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("institution", "length(trim(institution)) >= 2", "Instituição", "Institution"),
    ("keywords", "length(trim(keywords)) >= 2", "Palavras-chave", "Keywords"),
    ("advisors", "length(trim(advisors)) >= 3", "Orientação", "Advisors"),
    ("abstract", "length(trim(abstract)) >= 40", "Resumo em português", "Abstract (Portuguese)"),
    ("program", "length(trim(program)) >= 2", "Programa", "Graduate programme"),
    ("great_area", "length(trim(great_area)) >= 2", "Grande área", "Broad field"),
    ("pages", "pages > 0", "Número de páginas", "Page count"),
    ("defense_date", "defense_date IS NOT NULL", "Data de defesa", "Defence date"),
    ("url", "url LIKE 'http%'", "Link para o texto completo", "Full-text link"),
    (
        "keywords_en",
        "length(trim(keywords_en)) >= 2",
        "Palavras-chave em inglês",
        "Keywords (English)",
    ),
    ("abstract_en", "length(trim(abstract_en)) >= 40", "Resumo em inglês", "Abstract (English)"),
)

_SOURCE_GROUP_SQL = (
    "CASE WHEN source = 'bdtd' THEN 'bdtd' "
    "WHEN source = 'capes-catalogo' THEN 'catalogo' ELSE 'capes' END"
)


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------
def fmt_int(value: int, lang: str = "pt") -> str:
    """Thousands separators: ``1.728.035`` (pt) or ``1,728,035`` (en)."""
    text = f"{int(value):,}"
    return text.replace(",", ".") if lang == "pt" else text


def fmt_pct(part: float, total: float, lang: str = "pt", digits: int = 1) -> str:
    """Percentage with locale decimal separator."""
    value = 0.0 if not total else 100.0 * part / total
    smallest = 10**-digits
    if 0 < value < smallest / 2:
        text = f"<{smallest:.{digits}f}"
        return (text.replace(".", ",") if lang == "pt" else text) + "%"
    text = f"{value:.{digits}f}"
    return (text.replace(".", ",") if lang == "pt" else text) + "%"


def fmt_decimal(value: float, lang: str = "pt", digits: int = 1) -> str:
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if lang == "pt" else text


def fmt_millions(value: int, lang: str = "pt") -> str:
    """``1,73 milhão`` / ``1.73 million``."""
    millions = value / 1_000_000
    if lang == "pt":
        unit = "milhão" if millions < 2 else "milhões"
        return f"{fmt_decimal(millions, 'pt', 2)} {unit}"
    return f"{millions:.2f} million"


def fmt_bytes(value: int, lang: str = "pt") -> str:
    """Decimal gigabytes/megabytes."""
    if value >= 1_000_000_000:
        return f"{fmt_decimal(value / 1e9, lang)} GB"
    return f"{fmt_decimal(value / 1e6, lang, 0)} MB"


def fmt_axis(value: int, lang: str = "pt") -> str:
    if value == 0:
        return "0"
    if value >= 1000:
        return f"{value // 1000} mil" if lang == "pt" else f"{value // 1000}k"
    return str(value)


def fold(text: str | None) -> str:
    """Upper-case and strip accents, for label lookups."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).upper().strip()


def label_for(raw: str | None, table: dict[str, tuple[str, str]], lang: str) -> str:
    index = 0 if lang == "pt" else 1
    if raw is None or not str(raw).strip():
        return NOT_INFORMED[index]
    key = fold(raw)
    if key in table:
        return table[key][index]
    return str(raw).strip().capitalize()


def badge(label: str, message: str, color: str, *, logo: str | None = None) -> str:
    """Return a shields.io static badge URL."""

    def part(text: str) -> str:
        return quote(text.replace("-", "--").replace("_", "__"), safe="")

    url = f"https://img.shields.io/badge/{part(label)}-{part(message)}-{color.lstrip('#')}"
    if logo:
        url += f"?logo={quote(logo, safe='')}&logoColor=white"
    return url


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------
def _manifest_file_summary(manifest: dict[str, Any]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for entry in manifest.get("files", []):
        path = str(entry.get("path", ""))
        kind = path.split("/", 1)[0] if "/" in path else str(entry.get("kind", "other"))
        item = summary.setdefault(kind, {"files": 0, "rows": 0, "bytes": 0})
        item["files"] += 1
        item["rows"] += int(entry.get("rows") or 0)
        item["bytes"] += int(entry.get("bytes") or entry.get("size") or 0)
    return summary


def collect_stats(release: Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compute the card statistics from ``release/metadata/*.parquet``."""
    import duckdb

    release = Path(release)
    if manifest is None:
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    pattern = (release / "metadata" / "*.parquet").as_posix().replace("'", "''")
    con = duckdb.connect()
    con.execute("SET enable_progress_bar = false")
    con.execute(
        f"CREATE VIEW m AS SELECT *, {_SOURCE_GROUP_SQL} AS grp FROM read_parquet('{pattern}')"
    )

    def rows(sql: str) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in con.execute(sql).fetchall()]

    records = rows("SELECT count(*) FROM m")[0][0]
    by_year: dict[int, dict[str, int]] = {}
    for year, group, count in rows("SELECT year, grp, count(*) FROM m GROUP BY 1, 2"):
        by_year.setdefault(int(year), {"capes": 0, "catalogo": 0, "bdtd": 0})[group] = int(count)
    group_years = {
        group: [int(low), int(high), int(count)]
        for group, low, high, count in rows(
            "SELECT grp, min(year), max(year), count(*) FROM m GROUP BY 1"
        )
    }

    def counts(column: str) -> list[list[Any]]:
        return [
            [value, int(count)]
            for value, count in rows(f"SELECT {column}, count(*) FROM m GROUP BY 1 ORDER BY 2 DESC")
        ]

    languages: dict[str, int] = {}
    for value, count in counts("language"):
        key = fold(value) or ""
        languages[key] = languages.get(key, 0) + int(count)

    institutions: dict[str, dict[str, Any]] = {}
    for acronym, name, count in rows(
        "SELECT institution_acronym, institution, count(*) FROM m "
        "WHERE institution_acronym IS NOT NULL AND trim(institution_acronym) <> '' GROUP BY 1, 2"
    ):
        item = institutions.setdefault(str(acronym), {"n": 0, "name": name, "name_n": 0})
        item["n"] += int(count)
        if int(count) > item["name_n"]:
            item["name"], item["name_n"] = name, int(count)
    top_institutions = sorted(institutions.items(), key=lambda kv: (-kv[1]["n"], kv[0]))[:12]

    coverage_sql = ", ".join(
        f"sum(CASE WHEN {predicate} THEN 1 ELSE 0 END)" for _, predicate, _, _ in COVERAGE_FIELDS
    )
    coverage: dict[str, dict[str, int]] = {}
    for row in rows(f"SELECT grp, count(*), {coverage_sql} FROM m GROUP BY 1"):
        group, total, *values = row
        coverage[str(group)] = {"_total": int(total)}
        for (key, _, _, _), value in zip(COVERAGE_FIELDS, values, strict=True):
            coverage[str(group)][key] = int(value or 0)

    distinct = rows(
        "SELECT count(DISTINCT institution_acronym), count(DISTINCT institution), "
        "count(DISTINCT program_code), count(DISTINCT uf) FROM m"
    )[0]
    median_words = rows(
        "SELECT median(len(string_split(trim(abstract), ' '))) FROM m WHERE length(trim(abstract)) >= 40"
    )[0][0]
    degree, great_area, region = counts("degree"), counts("great_area"), counts("region")
    con.close()

    years = sorted(by_year)
    return {
        "version": manifest.get("version"),
        "records": int(records),
        "years": [years[0], years[-1]] if years else [],
        "by_year": [{"year": year, **by_year[year]} for year in years],
        "source_years": group_years,
        "degree": degree,
        "great_area": great_area,
        "region": region,
        "language": sorted(([k, v] for k, v in languages.items()), key=lambda kv: -kv[1]),
        "top_institutions": [[acr, item["name"], item["n"]] for acr, item in top_institutions],
        "distinct": {
            "institution_acronyms": int(distinct[0]),
            "institution_names": int(distinct[1]),
            "programs": int(distinct[2]),
            "ufs": int(distinct[3]),
        },
        "coverage": coverage,
        "abstract_median_words": int(median_words or 0),
        "files": _manifest_file_summary(manifest),
        "sources": manifest.get("sources", []),
    }


def coverage_totals(stats: dict[str, Any]) -> dict[str, int]:
    """Sum the per-source coverage counts."""
    totals: dict[str, int] = {}
    for group in stats["coverage"].values():
        for key, value in group.items():
            totals[key] = totals.get(key, 0) + int(value)
    return totals


def recent_start(stats: dict[str, Any]) -> int | None:
    starts = [
        bounds[0]
        for group, bounds in stats.get("source_years", {}).items()
        if group in {"catalogo", "bdtd"} and bounds
    ]
    return min(starts) if starts else None


def _grouped(
    items: Iterable[Sequence[Any]],
    table: dict[str, tuple[str, str]],
    lang: str,
    *,
    keep: int | None = None,
    min_share: float = 0.0,
    total: int | None = None,
) -> list[tuple[str, int]]:
    """Map raw values to labels, merge duplicates and fold small ones into "Outros"."""
    merged: dict[str, int] = {}
    order: list[str] = []
    for raw, count in items:
        label = label_for(raw, table, lang)
        if label not in merged:
            order.append(label)
            merged[label] = 0
        merged[label] += int(count)
    ranked = sorted(order, key=lambda name: -merged[name])
    not_informed = NOT_INFORMED[0 if lang == "pt" else 1]
    others_label = OTHERS[0 if lang == "pt" else 1]
    result: list[tuple[str, int]] = []
    others = 0
    for name in ranked:
        share = merged[name] / total if total else 1.0
        if (keep is not None and len(result) >= keep) or share < min_share:
            others += merged[name]
        else:
            result.append((name, merged[name]))
    # "Not informed" always goes last.
    result = [item for item in result if item[0] != not_informed] + [
        item for item in result if item[0] == not_informed
    ]
    if others:
        result.insert(len([i for i in result if i[0] != not_informed]), (others_label, others))
    return result


# --------------------------------------------------------------------------
# SVG drawing
# --------------------------------------------------------------------------
SANS = "'Atkinson Hyperlegible Next', 'Helvetica Neue', Arial, sans-serif"
SERIF = "Literata, Georgia, 'Times New Roman', serif"


def _text(
    x: float,
    y: float,
    content: str,
    *,
    size: float = 16,
    fill: str = INK,
    weight: int = 400,
    anchor: str = "start",
    family: str = SANS,
    extra: str = "",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{extra}>{escape(content)}</text>'
    )


def _frame(width: int, height: int, title: str, subtitle: str, body: str, *, desc: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d">'
        f'<title id="t">{escape(title)}</title><desc id="d">{escape(desc)}</desc>'
        f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="22" fill="{PAPER}" stroke="{BORDER}" stroke-width="2"/>'
        + _text(48, 66, title, size=30, weight=650, family=SERIF)
        + _text(48, 100, subtitle, size=18, fill=MUTED)
        + body
        + "</svg>"
    )


def _nice_ceiling(value: float) -> tuple[int, int]:
    """Return (axis maximum, tick step) for *value*."""
    if value <= 0:
        return 10, 2
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0):
        tick = max(1, int(step * magnitude))
        if value / tick <= 5:
            return int(math.ceil(value / tick) * tick), tick
    tick = max(1, int(10 * magnitude))
    return int(math.ceil(value / tick) * tick), tick


def chart_by_year(stats: dict[str, Any], lang: str = "pt") -> str:
    """Stacked columns: records per defence year, by source."""
    width, height = 1200, 600
    left, right, top, bottom = 96, 1160, 176, 516
    rows = stats["by_year"]
    totals = [row["capes"] + row["catalogo"] + row["bdtd"] for row in rows]
    axis_max, tick = _nice_ceiling(max(totals))
    slot = (right - left) / max(len(rows), 1)
    bar = min(slot * 0.7, 30)
    start = recent_start(stats)
    current_year = stats["years"][1] if stats["years"] else None

    def y_of(value: float) -> float:
        return bottom - (bottom - top) * value / axis_max

    parts: list[str] = [
        '<defs><pattern id="hatch" width="7" height="7" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><rect width="7" height="7" fill="none"/>'
        '<line x1="0" y1="0" x2="0" y2="7" stroke="#FFFFFF" stroke-width="2.6" stroke-opacity="0.75"/>'
        "</pattern></defs>"
    ]
    value = 0
    while value <= axis_max:
        y = y_of(value)
        parts.append(
            f'<line x1="{left}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1.5"/>'
        )
        parts.append(
            _text(left - 14, y + 5, fmt_axis(value, lang), size=15, fill=MUTED, anchor="end")
        )
        value += tick
    for index, row in enumerate(rows):
        x = left + index * slot + (slot - bar) / 2
        base = 0
        partial = start is not None and row["year"] >= start
        for key, _, _, color in SOURCE_GROUPS:
            amount = row[key]
            if not amount:
                continue
            y0, y1 = y_of(base), y_of(base + amount)
            parts.append(
                f'<rect x="{x:.1f}" y="{y1:.1f}" width="{bar:.1f}" height="{y0 - y1:.1f}" fill="{color}"/>'
            )
            base += amount
        if partial:
            parts.append(
                f'<rect x="{x:.1f}" y="{y_of(base):.1f}" width="{bar:.1f}" height="{bottom - y_of(base):.1f}" fill="url(#hatch)"/>'
            )
        year = row["year"]
        if (
            index == 0
            or year == current_year
            or (year % 5 == 0 and (current_year or year) - year >= 3)
        ):
            parts.append(
                _text(x + bar / 2, bottom + 28, str(year), size=15, fill=MUTED, anchor="middle")
            )
    parts.append(
        f'<line x1="{left}" x2="{right}" y1="{bottom}" y2="{bottom}" stroke="{MUTED}" stroke-width="1.5"/>'
    )

    # Peak annotation.
    peak_index = max(range(len(totals)), key=lambda i: totals[i])
    peak_x = left + peak_index * slot + slot / 2
    peak_y = y_of(totals[peak_index])
    peak_text = (
        f"{fmt_int(totals[peak_index], lang)} em {rows[peak_index]['year']}"
        if lang == "pt"
        else f"{fmt_int(totals[peak_index], lang)} in {rows[peak_index]['year']}"
    )
    parts.append(
        _text(
            peak_x,
            peak_y - 12,
            peak_text,
            size=15,
            weight=700,
            anchor="middle",
            extra=f' stroke="{PAPER}" stroke-width="6" paint-order="stroke"',
        )
    )

    # Partial-years bracket.
    if start is not None:
        first = next((i for i, row in enumerate(rows) if row["year"] >= start), None)
        if first is not None:
            x0 = left + first * slot + (slot - bar) / 2
            x1 = left + (len(rows) - 1) * slot + (slot + bar) / 2
            partial_max = max(totals[first:])
            y = y_of(partial_max) - 16
            parts.append(
                f'<path d="M{x0:.1f} {y + 8:.1f} V{y:.1f} H{x1:.1f} V{y + 8:.1f}" fill="none" stroke="{MUTED}" stroke-width="1.5"/>'
            )
            parts.append(
                _text(
                    x1,
                    y - 10,
                    "parcial" if lang == "pt" else "partial",
                    size=15,
                    fill=MUTED,
                    anchor="end",
                    weight=700,
                )
            )

    # Legend.
    legend_x, legend_y = 48, 140
    for key, label_pt, label_en, color in SOURCE_GROUPS:
        if not any(row[key] for row in rows):
            continue
        label = label_pt if lang == "pt" else label_en
        parts.append(
            f'<rect x="{legend_x}" y="{legend_y - 13}" width="16" height="16" rx="3" fill="{color}"/>'
        )
        parts.append(_text(legend_x + 24, legend_y, label, size=16))
        legend_x += 24 + len(label) * 8.6 + 32
    if start is not None:
        parts.append(
            f'<rect x="{legend_x}" y="{legend_y - 13}" width="16" height="16" rx="3" fill="{SAGE}"/>'
            f'<rect x="{legend_x}" y="{legend_y - 13}" width="16" height="16" rx="3" fill="url(#hatch)"/>'
        )
        parts.append(
            _text(
                legend_x + 24,
                legend_y,
                "ano com cobertura parcial" if lang == "pt" else "year with partial coverage",
                size=16,
            )
        )

    first_year, last_year = stats["years"]
    if lang == "pt":
        title = "Teses e dissertações por ano de defesa"
        subtitle = f"{fmt_int(stats['records'])} registros de {first_year} a {last_year}, por fonte dos dados"
        desc = (
            f"Gráfico de colunas empilhadas com o número de trabalhos por ano, de {first_year} a {last_year}. "
            f"O máximo é {peak_text}."
        )
    else:
        title = "Theses and dissertations per defence year"
        subtitle = f"{fmt_int(stats['records'], 'en')} records from {first_year} to {last_year}, by data source"
        desc = f"Stacked column chart of records per year, {first_year}–{last_year}. The peak is {peak_text}."
    return _frame(width, height, title, subtitle, "".join(parts), desc=desc)


def _hbars(
    x: float,
    y: float,
    width: float,
    items: Sequence[tuple[str, int]],
    total: int,
    lang: str,
    *,
    label_width: float,
    color: str = TEAL,
    row: float = 46,
    percent_of: int | None = None,
    show_count: bool = True,
    track: bool = False,
    value_width: float = 190,
) -> str:
    """Horizontal bars with labels on the left and values on the right."""
    parts: list[str] = []
    bar_left = x + label_width + 16
    bar_span = width - label_width - 16 - value_width
    scale_max = percent_of or max((value for _, value in items), default=1) or 1
    for index, (label, value) in enumerate(items):
        cy = y + index * row
        length = bar_span * value / scale_max
        parts.append(_text(x + label_width, cy + 6, label, size=17, anchor="end"))
        if track:
            parts.append(
                f'<rect x="{bar_left:.1f}" y="{cy - 12:.1f}" width="{bar_span:.1f}" height="24" rx="6" fill="{TRACK}"/>'
            )
        parts.append(
            f'<rect x="{bar_left:.1f}" y="{cy - 12:.1f}" width="{max(length, 3):.1f}" height="24" rx="6" fill="{color}"/>'
        )
        share = fmt_pct(value, percent_of or total, lang)
        text = f"{fmt_int(value, lang)} · {share}" if show_count else share
        value_x = (bar_left + bar_span + 14) if track else (bar_left + length + 12)
        parts.append(_text(value_x, cy + 6, text, size=16, fill=MUTED, weight=600))
    return "".join(parts)


def chart_great_areas(stats: dict[str, Any], lang: str = "pt") -> str:
    items = _grouped(stats["great_area"], GREAT_AREA_LABELS, lang)
    total = stats["records"]
    height = 150 + len(items) * 46 + 20
    body = _hbars(48, 160, 1104, items, total, lang, label_width=330)
    if lang == "pt":
        title, subtitle = (
            "Grandes áreas do conhecimento",
            "Classificação da CAPES; registros por grande área",
        )
        desc = "Barras horizontais com o número de trabalhos em cada grande área do conhecimento."
    else:
        title, subtitle = (
            "Broad fields of knowledge",
            "CAPES classification; records per broad field",
        )
        desc = "Horizontal bars with the number of records in each broad field of knowledge."
    return _frame(1200, height, title, subtitle, body, desc=desc)


def chart_degree_region(stats: dict[str, Any], lang: str = "pt") -> str:
    total = stats["records"]
    degrees = _grouped(stats["degree"], DEGREE_LABELS, lang, min_share=0.0001, total=total)
    regions = _grouped(stats["region"], REGION_LABELS, lang)
    rows_count = max(len(degrees), len(regions))
    height = 196 + rows_count * 46 + 16
    head_pt = ("Nível", "Região")
    head_en = ("Degree", "Region")
    heads = head_pt if lang == "pt" else head_en
    body = (
        _text(48, 150, heads[0], size=19, weight=700)
        + _hbars(48, 196, 560, degrees, total, lang, label_width=210, color=TEAL, value_width=170)
        + _text(640, 150, heads[1], size=19, weight=700)
        + _hbars(640, 196, 512, regions, total, lang, label_width=140, color=GOLD, value_width=170)
    )
    if lang == "pt":
        title, subtitle = (
            "Nível e região",
            "Mestrados, doutorados e distribuição pelas regiões do Brasil",
        )
        desc = "Dois painéis de barras: trabalhos por nível do curso e por região do país."
    else:
        title, subtitle = (
            "Degree and region",
            "Master's, doctorates and distribution across Brazil's regions",
        )
        desc = "Two bar panels: records by degree level and by region of Brazil."
    return _frame(1200, height, title, subtitle, body, desc=desc)


def chart_institutions(stats: dict[str, Any], lang: str = "pt") -> str:
    items = [(str(acronym), int(count)) for acronym, _name, count in stats["top_institutions"]]
    height = 150 + len(items) * 46 + 20
    body = _hbars(48, 160, 1104, items, stats["records"], lang, label_width=150, color=SAGE)
    count = len(items)
    if lang == "pt":
        title = f"As {count} instituições com mais trabalhos"
        subtitle = (
            f"{fmt_int(stats['distinct']['institution_acronyms'])} siglas de instituição no catálogo; "
            "percentual sobre o total"
        )
        desc = f"Barras horizontais com as {count} instituições com mais teses e dissertações."
    else:
        title = f"Top {count} institutions by number of records"
        subtitle = (
            f"{fmt_int(stats['distinct']['institution_acronyms'], 'en')} institution acronyms in the catalogue; "
            "share of the total"
        )
        desc = (
            f"Horizontal bars with the {count} institutions with the most theses and dissertations."
        )
    return _frame(1200, height, title, subtitle, body, desc=desc)


def chart_coverage(stats: dict[str, Any], lang: str = "pt") -> str:
    totals = coverage_totals(stats)
    total = totals.get("_total", stats["records"])
    items = sorted(
        (
            (label_pt if lang == "pt" else label_en, totals.get(key, 0))
            for key, _, label_pt, label_en in COVERAGE_FIELDS
        ),
        key=lambda item: -item[1],
    )
    height = 150 + len(items) * 46 + 20
    body = _hbars(
        48,
        160,
        1104,
        items,
        total,
        lang,
        label_width=300,
        color=TEAL_MID,
        percent_of=total,
        show_count=False,
        track=True,
        value_width=90,
    )
    if lang == "pt":
        title, subtitle = "Completude dos campos", "Parcela dos registros com cada campo preenchido"
        desc = "Barras de 0 a 100% com a parcela de registros que tem cada campo preenchido."
    else:
        title, subtitle = "Field completeness", "Share of records with each field filled in"
        desc = "Bars from 0 to 100% with the share of records that have each field filled in."
    return _frame(1200, height, title, subtitle, body, desc=desc)


def _icon_markup(x: float, y: float, size: float) -> str:
    """The BuscaTeses app icon (same drawing as ``web/assets/brand/icon.svg``)."""
    scale = size / 512
    return (
        f'<g transform="translate({x:.1f} {y:.1f}) scale({scale:.4f})">'
        '<defs><linearGradient id="iconbg" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#1B7A70"/><stop offset="1" stop-color="#0A3B37"/></linearGradient></defs>'
        '<rect width="512" height="512" rx="112" fill="url(#iconbg)"/>'
        '<rect x="122" y="128" width="290" height="196" rx="18" fill="#7FB5AD" transform="rotate(-8 267 226)"/>'
        '<g transform="rotate(2 250 268)"><rect x="86" y="160" width="318" height="216" rx="18" fill="#F6F7F5"/>'
        '<rect x="120" y="196" width="138" height="18" rx="9" fill="#0E4F4A"/>'
        '<rect x="86" y="232" width="318" height="5" fill="#C99A2E"/>'
        '<rect x="120" y="258" width="200" height="11" rx="5.5" fill="#A3B1AD"/>'
        '<rect x="120" y="284" width="150" height="11" rx="5.5" fill="#A3B1AD"/>'
        '<circle cx="245" cy="344" r="14" fill="#0A3B37"/></g>'
        '<circle cx="352" cy="306" r="60" fill="#0E4F4A" fill-opacity="0.14" stroke="#E2B547" stroke-width="24"/>'
        '<line x1="396" y1="350" x2="438" y2="392" stroke="#E2B547" stroke-width="30" stroke-linecap="round"/>'
        "</g>"
    )


def banner_svg(
    title: str,
    lines: Sequence[str],
    *,
    eyebrow: str | None = None,
    chips: Sequence[str] = (),
    title_size: int = 96,
    width: int = 1520,
    height: int = 440,
) -> str:
    """Brand banner: icon, serif title, subtitle lines, optional chips."""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{escape(title)}">',
        f'<defs><clipPath id="clip"><rect width="{width}" height="{height}" rx="28"/></clipPath>'
        f'<linearGradient id="glow" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{TEAL_MID}"/>'
        f'<stop offset="1" stop-color="{TEAL_DARK}"/></linearGradient>'
        '<filter id="shadow" x="-30%" y="-30%" width="160%" height="160%">'
        '<feDropShadow dx="0" dy="14" stdDeviation="18" flood-color="#000" flood-opacity="0.28"/></filter></defs>',
        '<g clip-path="url(#clip)">',
        f'<rect width="{width}" height="{height}" fill="url(#glow)"/>',
        f'<circle cx="{width - 120}" cy="-40" r="260" fill="#FFFFFF" fill-opacity="0.04"/>',
        f'<circle cx="{width - 20}" cy="{height + 40}" r="200" fill="#FFFFFF" fill-opacity="0.03"/>',
        f'<rect y="{height - 12}" width="{width}" height="12" fill="{GOLD}"/>',
        "</g>",
        f'<g filter="url(#shadow)">{_icon_markup(96, (height - 12 - 220) / 2, 220)}</g>',
    ]
    text_x = 382
    block = (
        (eyebrow is not None) * 40 + title_size * 0.95 + 24 + len(lines) * 46 + (60 if chips else 0)
    )
    y = (height - 12 - block) / 2
    if eyebrow:
        y += 28
        parts.append(
            _text(
                text_x,
                y,
                eyebrow.upper(),
                size=21,
                fill=GOLD_BRIGHT,
                weight=700,
                extra=' letter-spacing="2.5"',
            )
        )
        y += 12
    y += title_size * 0.88
    parts.append(_text(text_x, y, title, size=title_size, fill="#F6F7F5", weight=700, family=SERIF))
    y += 22
    for line in lines:
        y += 44
        parts.append(_text(text_x, y, line, size=31, fill="#CFE3DF"))
    if chips:
        y += 30
        x = text_x
        for chip in chips:
            chip_width = 26 + len(chip) * 10.6
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{chip_width:.1f}" height="40" rx="20" '
                'fill="#FFFFFF" fill-opacity="0.10" stroke="#FFFFFF" stroke-opacity="0.22"/>'
            )
            parts.append(
                _text(
                    x + chip_width / 2,
                    y + 27,
                    chip,
                    size=19,
                    fill="#F6F7F5",
                    weight=600,
                    anchor="middle",
                )
            )
            x += chip_width + 12
    parts.append("</svg>")
    return "".join(parts)


def dataset_banner(stats: dict[str, Any], lang: str = "pt") -> str:
    first_year, last_year = stats["years"]
    if lang == "pt":
        return banner_svg(
            "Teses e Dissertações",
            [
                f"{fmt_int(stats['records'])} trabalhos defendidos no Brasil, de {first_year} a {last_year},",
                "com metadados e embeddings para busca por significado.",
            ],
            eyebrow="Dataset aberto · BuscaTeses",
            chips=(
                "Parquet",
                "CAPES + BDTD",
                f"EmbeddingGemma {FULL_DIMS}/{APP_DIMS}",
                "CC BY 4.0",
            ),
        )
    return banner_svg(
        "Theses & Dissertations",
        [
            f"{fmt_int(stats['records'], 'en')} works defended in Brazil, {first_year}–{last_year},",
            "with metadata and embeddings for semantic search.",
        ],
        eyebrow="Open dataset · BuscaTeses",
        chips=("Parquet", "CAPES + BDTD", f"EmbeddingGemma {FULL_DIMS}/{APP_DIMS}", "CC BY 4.0"),
    )


def app_banner(stats: dict[str, Any], lang: str = "pt") -> str:
    """Banner of the GitHub README (``docs/assets/banner*.png``)."""
    if lang == "pt":
        lines = [
            f"Busca inteligente em {fmt_millions(stats['records'])} de teses e dissertações",
            "brasileiras, no seu computador.",
        ]
    else:
        lines = [
            f"Intelligent search across {fmt_millions(stats['records'], 'en')} Brazilian theses",
            "and dissertations, on your own computer.",
        ]
    return banner_svg("BuscaTeses", lines, title_size=116)


#: (asset stem, builder) for every image referenced on the card.
CHARTS = (
    ("anos", chart_by_year),
    ("areas", chart_great_areas),
    ("nivel-regiao", chart_degree_region),
    ("instituicoes", chart_institutions),
    ("completude", chart_coverage),
)


def asset_names() -> list[str]:
    """Every PNG the card references (stems × languages)."""
    stems = ["banner"] + [stem for stem, _ in CHARTS]
    return [f"{stem}.{lang}.png" for stem in stems for lang in ("pt", "en")]


def render_assets(stats: dict[str, Any], out_dir: Path) -> list[Path]:
    """Write the banner and the charts as SVG files in *out_dir*."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for lang in ("pt", "en"):
        documents = [("banner", dataset_banner(stats, lang))] + [
            (stem, build(stats, lang)) for stem, build in CHARTS
        ]
        for stem, svg in documents:
            path = out_dir / f"{stem}.{lang}.svg"
            path.write_text(svg, encoding="utf-8")
            written.append(path)
    return written


def _font_face(family: str, path: Path) -> str:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        f"@font-face{{font-family:'{family}';src:url(data:font/woff2;base64,{data}) format('woff2');"
        "font-weight:100 900;font-style:normal;}"
    )


def default_font_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "buscateses" / "web" / "assets" / "fonts"


def rasterize(
    svg_paths: Sequence[Path], *, font_dir: Path | None = None, scale: int = 2
) -> list[Path]:
    """Render each SVG to a PNG next to it using Playwright (Chromium)."""
    from playwright.sync_api import sync_playwright

    font_dir = Path(font_dir or default_font_dir())
    style = _font_face("Literata", font_dir / "literata-latin-wght-normal.woff2") + _font_face(
        "Atkinson Hyperlegible Next",
        font_dir / "atkinson-hyperlegible-next-latin-wght-normal.woff2",
    )
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(device_scale_factor=scale)
        for svg_path in svg_paths:
            svg = Path(svg_path).read_text(encoding="utf-8")
            page.set_content(
                f"<!doctype html><html><head><meta charset='utf-8'><style>{style}"
                "html,body{margin:0;background:transparent}svg{display:block}</style></head>"
                f"<body>{svg}</body></html>"
            )
            page.evaluate("document.fonts.ready")
            png_path = Path(svg_path).with_suffix(".png")
            page.locator("svg").first.screenshot(path=str(png_path), omit_background=True)
            written.append(png_path)
        browser.close()
    return written


# --------------------------------------------------------------------------
# README
# --------------------------------------------------------------------------
def _cell(text: str) -> str:
    """Escape pipes for use inside a Markdown table cell."""
    return text.replace("|", "\\|")


def _img(stem: str, lang: str, alt: str, width: int = 900) -> str:
    return f'<p align="center"><img src="{ASSET_BASE_URL}/{stem}.{lang}.png" alt="{escape(alt)}" width="{width}"></p>'


def _front_matter(stats: dict[str, Any]) -> str:
    records = stats["records"]
    if records < 1_000_000:
        size = "100K<n<1M" if records >= 100_000 else "10K<n<100K"
    else:
        size = "1M<n<10M" if records < 10_000_000 else "n>10M"
    return f"""---
license: cc-by-4.0
language:
- pt
- en
pretty_name: Catálogo de Teses e Dissertações (CAPES e BDTD)
size_categories:
- {size}
language_creators:
- found
annotations_creators:
- no-annotation
multilinguality:
- multilingual
source_datasets:
- original
task_categories:
- text-retrieval
- sentence-similarity
- feature-extraction
task_ids:
- document-retrieval
tags:
- theses
- dissertations
- capes
- bdtd
- ibict
- brazil
- academic
- bibliometrics
- scientometrics
- semantic-search
- embeddings
- embeddinggemma
- matryoshka
- portuguese
configs:
- config_name: metadata
  default: true
  data_files:
  - split: train
    path: metadata/*.parquet
- config_name: embeddings-256
  data_files:
  - split: train
    path: embeddings-256/*.parquet
- config_name: embeddings-768
  data_files:
  - split: train
    path: embeddings-768/*.parquet
---
"""


def _badges(stats: dict[str, Any], version: str) -> str:
    first_year, last_year = stats["years"]
    items = [
        (
            "Registros",
            badge(
                "registros",
                fmt_millions(stats["records"]).replace(" milhão", " mi").replace(" milhões", " mi"),
                TEAL,
            ),
            f"{DATASET_URL}/viewer",
        ),
        ("Anos", badge("anos", f"{first_year}–{last_year}", TEAL_MID), "#fontes-e-cobertura"),
        ("Versão", badge("versão", version, "334155"), f"{DATASET_URL}/tree/{version}"),
        ("Licença", badge("licença", "CC BY 4.0", GOLD), LICENSE_URL),
        ("Formato", badge("formato", "Parquet", "50626B"), f"{DATASET_URL}/tree/main"),
        (
            "Embeddings",
            badge("embeddings", f"EmbeddingGemma {FULL_DIMS}·{APP_DIMS}", "5B4B8A"),
            MODEL_URL,
        ),
        ("Aplicativo", badge("app", "BuscaTeses", "0E4F4A", logo="github"), APP_URL),
    ]
    links = "\n  ".join(
        f'<a href="{href}"><img alt="{escape(alt)}" src="{src}"></a>' for alt, src, href in items
    )
    return f'<p align="center">\n  {links}\n</p>'


def _source_rows(stats: dict[str, Any], lang: str) -> list[str]:
    years = stats.get("source_years", {})
    names = {
        "capes": (
            f"[Dados abertos da CAPES]({SOURCE_URL}) — Catálogo de Teses e Dissertações",
            f"[CAPES open data]({SOURCE_URL}) — theses and dissertations catalogue",
            "CC BY",
        ),
        "catalogo": (
            f"[Catálogo de Teses da CAPES]({CAPES_CATALOGUE_URL}) + [Plataforma Sucupira]({SUCUPIRA_URL})",
            f"[CAPES theses catalogue]({CAPES_CATALOGUE_URL}) + [Sucupira platform]({SUCUPIRA_URL})",
            "metadados públicos" if lang == "pt" else "public metadata",
        ),
        "bdtd": (
            f"[BDTD (IBICT)]({BDTD_URL}) — Biblioteca Digital Brasileira de Teses e Dissertações",
            f"[BDTD (IBICT)]({BDTD_URL}) — Brazilian Digital Library of Theses and Dissertations",
            "metadados públicos" if lang == "pt" else "public metadata",
        ),
    }
    rows = []
    for key, _, _, _ in SOURCE_GROUPS:
        if key not in years:
            continue
        low, high, count = years[key]
        name_pt, name_en, terms = names[key]
        span = f"{low}–{high}" if low != high else str(low)
        rows.append(
            f"| {name_pt if lang == 'pt' else name_en} | {span} | {fmt_int(count, lang)} | "
            f"{fmt_pct(count, stats['records'], lang)} | {terms} |"
        )
    return rows


def _coverage_table(stats: dict[str, Any], lang: str) -> str:
    groups = [key for key, *_ in SOURCE_GROUPS if key in stats["coverage"]]
    heads = {key: (pt if lang == "pt" else en) for key, pt, en, _ in SOURCE_GROUPS}
    totals = coverage_totals(stats)
    header = "| Campo | " if lang == "pt" else "| Field | "
    header += " | ".join(heads[key] for key in groups) + (
        " | Total |" if lang == "pt" else " | All |"
    )
    lines = [header, "|---|" + "---:|" * (len(groups) + 1)]
    for key, _, label_pt, label_en in COVERAGE_FIELDS:
        cells = [
            fmt_pct(
                stats["coverage"][group].get(key, 0), stats["coverage"][group]["_total"], lang, 0
            )
            for group in groups
        ]
        cells.append(f"**{fmt_pct(totals.get(key, 0), totals['_total'], lang, 0)}**")
        lines.append(f"| {label_pt if lang == 'pt' else label_en} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _schema_table(lang: str) -> str:
    rows_pt = [
        (
            "id",
            "string",
            "Identificador estável: `capes:<id>` (CAPES 2013–2024), `capes87:<hash>` (CAPES 1987–2012), `capes-catalogo:<id>` (Catálogo de Teses) ou `bdtd:<rede>_<hash>` (BDTD).",
        ),
        ("year", "int16", "Ano de defesa ou titulação."),
        ("defense_date", "string", "Data ISO `AAAA-MM-DD`, quando publicada."),
        ("title", "string", "Título do trabalho."),
        ("author", "string", "Nome do(a) autor(a)."),
        (
            "advisors",
            "string",
            "Orientadores separados por `; `; coorientadores com o sufixo ` (coorientador)`.",
        ),
        (
            "degree",
            "string",
            "`Mestrado`, `Doutorado`, `Mestrado Profissional` ou `Doutorado Profissional`.",
        ),
        ("institution", "string", "Nome da instituição."),
        ("institution_acronym", "string", "Sigla da instituição."),
        ("uf", "string", "Unidade federativa (duas letras)."),
        ("region", "string", "Região do Brasil."),
        ("program", "string", "Programa de pós-graduação."),
        ("program_code", "string", "Código do programa na CAPES."),
        ("great_area", "string", "Grande área do conhecimento (CAPES)."),
        ("knowledge_area", "string", "Área do conhecimento."),
        ("evaluation_area", "string", "Área de avaliação."),
        ("concentration_area", "string", "Área de concentração, quando publicada."),
        ("research_line", "string", "Linha de pesquisa."),
        ("keywords", "string", "Palavras-chave separadas por `; `."),
        ("keywords_en", "string", "Palavras-chave em inglês."),
        ("abstract", "string", "Resumo em português."),
        ("abstract_en", "string", "Resumo em inglês."),
        ("language", "string", "Idioma do trabalho, como publicado na fonte."),
        ("pages", "int32", "Número de páginas (`0` quando não informado)."),
        ("library", "string", "Biblioteca depositária."),
        ("url", "string", "Link http/https para o texto completo, quando existe."),
        (
            "source",
            "string",
            "Origem: nome do arquivo CSV dos dados abertos da CAPES, `capes-catalogo` ou `bdtd`.",
        ),
    ]
    rows_en = [
        (
            "id",
            "string",
            "Stable id: `capes:<id>` (CAPES 2013–2024), `capes87:<hash>` (CAPES 1987–2012), `capes-catalogo:<id>` (CAPES catalogue) or `bdtd:<network>_<hash>` (BDTD).",
        ),
        ("year", "int16", "Defence year."),
        ("defense_date", "string", "ISO date `YYYY-MM-DD`, when published."),
        ("title", "string", "Title."),
        ("author", "string", "Author name."),
        (
            "advisors",
            "string",
            "Advisors separated by `; `; co-advisors carry the suffix ` (coorientador)`.",
        ),
        (
            "degree",
            "string",
            "`Mestrado` (master's), `Doutorado` (doctorate), `Mestrado Profissional` or `Doutorado Profissional`.",
        ),
        ("institution", "string", "Institution name."),
        ("institution_acronym", "string", "Institution acronym."),
        ("uf", "string", "Brazilian state (two letters)."),
        ("region", "string", "Brazilian region."),
        ("program", "string", "Graduate programme."),
        ("program_code", "string", "CAPES programme code."),
        ("great_area", "string", "Broad field of knowledge (CAPES)."),
        ("knowledge_area", "string", "Field of knowledge."),
        ("evaluation_area", "string", "CAPES evaluation area."),
        ("concentration_area", "string", "Concentration area, when published."),
        ("research_line", "string", "Research line."),
        ("keywords", "string", "Keywords separated by `; `."),
        ("keywords_en", "string", "English keywords."),
        ("abstract", "string", "Abstract in Portuguese."),
        ("abstract_en", "string", "Abstract in English."),
        ("language", "string", "Language of the work, as published by the source."),
        ("pages", "int32", "Page count (`0` when unknown)."),
        ("library", "string", "Holding library."),
        ("url", "string", "http/https link to the full text, when available."),
        ("source", "string", "Origin: CAPES open-data CSV file name, `capes-catalogo` or `bdtd`."),
    ]
    rows = rows_pt if lang == "pt" else rows_en
    head = "| Coluna | Tipo | Descrição |" if lang == "pt" else "| Column | Type | Description |"
    return "\n".join(
        [head, "|---|---|---|"] + [f"| `{name}` | {kind} | {text} |" for name, kind, text in rows]
    )


def _files_table(stats: dict[str, Any], lang: str) -> str:
    files = stats.get("files", {})
    descriptions = {
        "metadata": (
            "27 colunas de metadados (tabela abaixo)",
            "27 metadata columns (table below)",
        ),
        "embeddings-256": (
            f"`id` + `embedding`: float16[{APP_DIMS}], projeção Matryoshka",
            f"`id` + `embedding`: float16[{APP_DIMS}], Matryoshka projection",
        ),
        "embeddings-768": (
            f"`id` + `embedding`: float16[{FULL_DIMS}], vetor completo",
            f"`id` + `embedding`: float16[{FULL_DIMS}], full vector",
        ),
    }
    head = (
        "| Configuração | Arquivos | Linhas | Tamanho | Conteúdo |"
        if lang == "pt"
        else "| Config | Files | Rows | Size | Contents |"
    )
    lines = [head, "|---|---:|---:|---:|---|"]
    for kind in ("metadata", "embeddings-256", "embeddings-768"):
        if kind not in files:
            continue
        item = files[kind]
        text = descriptions[kind][0 if lang == "pt" else 1]
        lines.append(
            f"| `{kind}` | {item['files']} | {fmt_int(item['rows'], lang)} | {fmt_bytes(item['bytes'], lang)} | {text} |"
        )
    return "\n".join(lines)


def _quickstart(lang: str) -> str:
    pt = lang == "pt"
    c = lambda a, b: a if pt else b  # noqa: E731
    return f"""```python
# {c("SQL direto no Hugging Face, sem baixar tudo (DuckDB ≥ 1.1)", "SQL straight from the Hub, no full download (DuckDB ≥ 1.1)")}
import duckdb

duckdb.sql(\"\"\"
    SELECT year, count(*) AS {c("trabalhos", "works")}
    FROM 'hf://datasets/{DATASET_REPO}/metadata/*.parquet'
    WHERE lower(title) LIKE '%inteligência artificial%'
       OR lower(title) LIKE '%inteligencia artificial%'
    GROUP BY year ORDER BY year
\"\"\").show()
```

```python
# {c("Biblioteca datasets: em streaming (sem baixar os 2 GB) ou completa", "datasets library: streaming (no 2 GB download) or full")}
from datasets import load_dataset

{c("fluxo", "stream")} = load_dataset("{DATASET_REPO}", "metadata", split="train", streaming=True)
print(next(iter({c("fluxo", "stream")}))["title"])

{c("metadados", "metadata")} = load_dataset("{DATASET_REPO}", "metadata", split="train")
df = {c("metadados", "metadata")}.to_pandas()
```

```python
# {c("Busca semântica com os embeddings de 256 dimensões", "Semantic search with the 256-dim embeddings")}
# pip install -U sentence-transformers huggingface_hub pyarrow pandas numpy
# {c("O modelo google/embeddinggemma-300m exige aceitar a licença no Hub e `hf auth login`.", "google/embeddinggemma-300m is gated: accept its licence on the Hub and run `hf auth login`.")}
import numpy as np
import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download
from sentence_transformers import SentenceTransformer

REPO = "{DATASET_REPO}"
{c("parte", "part")} = "part-006"  # {c("partes ordenadas por ano: a 006 tem os trabalhos mais recentes", "parts are sorted by year: 006 holds the most recent works")}
meta = pq.read_table(
    hf_hub_download(REPO, f"metadata/{{{c("parte", "part")}}}.parquet", repo_type="dataset"),
    columns=["id", "year", "title", "institution_acronym"],
).to_pandas()
emb = pq.read_table(hf_hub_download(REPO, f"embeddings-256/{{{c("parte", "part")}}}.parquet", repo_type="dataset"))
assert meta["id"].tolist() == emb["id"].to_pylist()  # {c("mesma ordem, linha a linha", "same order, row by row")}
{c("vetores", "vectors")} = emb["embedding"].combine_chunks().flatten().to_numpy().reshape(-1, {APP_DIMS}).astype(np.float32)

{c("modelo", "model")} = SentenceTransformer("{EMBEDDING_MODEL}")
q = {c("modelo", "model")}.encode("{c("educação inclusiva no ensino superior", "inclusive education in higher education")}", prompt="{QUERY_TEMPLATE.split("{query}")[0]}")
q = q[:{APP_DIMS}] / np.linalg.norm(q[:{APP_DIMS}])  # {c("Matryoshka: 256 primeiros valores, re-normalizados", "Matryoshka: first 256 values, re-normalised")}

scores = {c("vetores", "vectors")} @ q
top = np.argsort(-scores)[:5]
print(meta.iloc[top].assign(score=scores[top])[["score", "year", "institution_acronym", "title"]])
```"""


def _citation(stats: dict[str, Any], version: str) -> str:
    key = "buscateses_capes_" + re.sub(r"[^0-9a-zA-Z]+", "_", version)
    year = version.split(".", 1)[0]
    return f"""```bibtex
@misc{{{key},
  title        = {{Catálogo de Teses e Dissertações (CAPES e BDTD) — dataset BuscaTeses}},
  author       = {{Badini, André}},
  year         = {{{year}}},
  version      = {{{version}}},
  publisher    = {{Hugging Face}},
  howpublished = {{\\url{{{DATASET_URL}}}}},
  note         = {{Dados originais: CAPES — Catálogo de Teses e Dissertações (CC-BY) e BDTD (IBICT). Versão {version}.}}
}}
```"""


def render_card(stats: dict[str, Any], manifest: dict[str, Any]) -> str:
    """Return the full dataset card (front matter + Markdown)."""
    version = str(manifest.get("version") or stats.get("version") or "")
    year = version.split(".", 1)[0]
    records = stats["records"]
    first_year, last_year = stats["years"]
    start = recent_start(stats)
    totals = coverage_totals(stats)
    files = stats.get("files", {})
    app_bytes = sum(files.get(kind, {}).get("bytes", 0) for kind in ("metadata", "embeddings-256"))
    all_bytes = sum(item.get("bytes", 0) for item in files.values())
    distinct = stats["distinct"]
    region_top = _grouped(stats["region"], REGION_LABELS, "pt")[0]
    region_top_en = _grouped(stats["region"], REGION_LABELS, "en")[0]
    degree_pt = dict(_grouped(stats["degree"], DEGREE_LABELS, "pt"))
    doctorates = degree_pt.get("Doutorado", 0) + degree_pt.get("Doutorado profissional", 0)
    masters = records - doctorates
    portuguese = next((count for key, count in stats["language"] if key == "PORTUGUES"), 0)
    recent_years = [row for row in stats["by_year"] if start is not None and row["year"] >= start]
    complete_years = [row for row in stats["by_year"] if start is None or row["year"] < start]
    recent_text_pt = ", ".join(
        f"{row['year']}: {fmt_int(row['capes'] + row['catalogo'] + row['bdtd'])}"
        for row in recent_years
    )
    recent_text_en = ", ".join(
        f"{row['year']}: {fmt_int(row['capes'] + row['catalogo'] + row['bdtd'], 'en')}"
        for row in recent_years
    )
    last_complete = complete_years[-1] if complete_years else None
    last_complete_total = (
        last_complete["capes"] + last_complete["catalogo"] + last_complete["bdtd"]
        if last_complete
        else 0
    )
    bdtd_cov = stats["coverage"].get("bdtd")
    bdtd_abstract_missing = (
        fmt_pct(bdtd_cov["_total"] - bdtd_cov.get("abstract", 0), bdtd_cov["_total"])
        if bdtd_cov
        else None
    )
    bdtd_area_missing = (
        fmt_pct(bdtd_cov["_total"] - bdtd_cov.get("great_area", 0), bdtd_cov["_total"])
        if bdtd_cov
        else None
    )
    catalogo_cov = stats["coverage"].get("catalogo")
    catalogo_abstract_missing = (
        fmt_pct(catalogo_cov["_total"] - catalogo_cov.get("abstract", 0), catalogo_cov["_total"])
        if catalogo_cov
        else None
    )

    sources_pt = "\n".join(_source_rows(stats, "pt"))
    sources_en = "\n".join(_source_rows(stats, "en"))
    packages = "\n".join(f"  - <{url}>" for url in PACKAGE_URLS.values())
    notes_pt = "\n".join(
        f"| `{tag}`{' (atual)' if index == 0 else ''} | {text_pt} |"
        for index, (tag, text_pt, _) in enumerate(RELEASE_NOTES)
    )
    notes_en = "\n".join(
        f"| `{tag}`{' (current)' if index == 0 else ''} | {text_en} |"
        for index, (tag, _, text_en) in enumerate(RELEASE_NOTES)
    )
    recent_warning_pt = (
        f"""
> **Atenção — anos recentes incompletos.** Os dados abertos da CAPES vão até {start - 1}. Os trabalhos de {start} em
> diante ({recent_text_pt}) vêm do Catálogo de Teses da CAPES e da BDTD e ainda crescem: a CAPES consolida a coleta de
> cada ano depois que ele termina, e a BDTD depende de cada universidade publicar. Para comparação, {last_complete["year"] if last_complete else ""} tem
> {fmt_int(last_complete_total)} trabalhos. Não trate {start}–{last_year} como anos completos em séries históricas.
"""
        if start is not None
        else ""
    )
    recent_warning_en = (
        f"""
> **Heads-up — recent years are incomplete.** CAPES open data ends in {start - 1}. Works from {start} onward
> ({recent_text_en}) come from the CAPES theses catalogue and the BDTD and are still growing: CAPES consolidates each
> year after it ends and the BDTD depends on each university publishing. For comparison, {last_complete["year"] if last_complete else ""} has
> {fmt_int(last_complete_total, "en")} works. Do not treat {start}–{last_year} as complete years in time series.
"""
        if start is not None
        else ""
    )

    credit_pt = ' e, para os trabalhos da BDTD, "BDTD — IBICT"' if start else ""
    credit_en = ' and "BDTD — IBICT"' if start else ""

    def cov(key: str, lang: str = "pt") -> str:
        return fmt_pct(totals.get(key, 0), totals.get("_total", records), lang)

    card = (
        _front_matter(stats)
        + f"""
<p align="center">
  <img src="{ASSET_BASE_URL}/banner.pt.png" alt="Teses e Dissertações — dataset aberto com {fmt_int(records)} trabalhos defendidos no Brasil, de {first_year} a {last_year}, com metadados e embeddings" width="900">
</p>

{_badges(stats, version)}

<p align="center"><b>Português</b> · <a href="#english">English</a></p>

# Catálogo de Teses e Dissertações (CAPES e BDTD)

Metadados de **{fmt_int(records)} teses e dissertações** defendidas no Brasil entre **{first_year} e {last_year}** — título,
autoria, orientação, instituição, programa, área, palavras-chave e resumo — em Parquet, acompanhados de
**embeddings semânticos** ([EmbeddingGemma]({MODEL_URL}), {FULL_DIMS} e {APP_DIMS} dimensões) para busca por significado.
Os dados vêm do Catálogo de Teses e Dissertações da CAPES e, nos anos mais recentes, também da BDTD (IBICT).

É a base do [**BuscaTeses**]({APP_URL}), aplicativo gratuito e de código aberto que pesquisa todo o catálogo no
computador do usuário, sem enviar consultas para a nuvem. Este dataset é aberto para qualquer outro uso:
bibliometria, revisões de literatura, sistemas de recomendação e avaliação de modelos de busca em português.

## Em números

| | |
|---|---:|
| Teses e dissertações | **{fmt_int(records)}** |
| Período | {first_year}–{last_year} |
| Doutorados · mestrados | {fmt_int(doctorates)} · {fmt_int(masters)} |
| Instituições (siglas) | {fmt_int(distinct["institution_acronyms"])} |
| Programas de pós-graduação | {fmt_int(distinct["programs"])} |
| Com resumo em português | {cov("abstract")} |
| Com resumo em inglês | {cov("abstract_en")} |
| Com link para o texto completo | {cov("url")} |
| Tamanho típico do resumo | {fmt_int(stats["abstract_median_words"])} palavras (mediana) |
| Download completo · usado pelo app | {fmt_bytes(all_bytes)} · {fmt_bytes(app_bytes)} |
| Versão | `{version}` |

## Comece aqui

{_quickstart("pt")}

O texto de cada documento foi codificado como `{DOCUMENT_TEMPLATE}` e as consultas como
`{QUERY_TEMPLATE}`. Use os mesmos modelos de texto para que os vetores sejam comparáveis. Detalhes em
[Embeddings](#embeddings).

## Panorama

{_img("anos", "pt", f"Colunas empilhadas com teses e dissertações por ano, de {first_year} a {last_year}, separadas por fonte; os anos a partir de {start} aparecem hachurados por estarem incompletos." if start else "Colunas com teses e dissertações por ano.")}
{recent_warning_pt}
{_img("areas", "pt", "Barras horizontais com o número de trabalhos por grande área do conhecimento.")}

{_img("nivel-regiao", "pt", "Dois painéis: trabalhos por nível (mestrado, doutorado e profissionais) e por região do Brasil.")}

{_img("instituicoes", "pt", "Barras horizontais com as instituições que têm mais teses e dissertações no catálogo.")}

## Estrutura

{_files_table(stats, "pt")}

```text
metadata/part-000.parquet … part-{files.get("metadata", {}).get("files", 1) - 1:03d}.parquet         ordenados por (year, id), ~250 mil linhas cada
embeddings-256/part-000.parquet …                     mesma ordem e mesmo particionamento dos metadados
embeddings-768/part-000.parquet …                     idem, vetor completo
manifest.json                                         contagens, fontes, SHA-256 de cada arquivo e vetor de sonda
```

A linha *i* de `embeddings-256/part-NNN.parquet` (ou de `embeddings-768/`) corresponde à linha *i* de
`metadata/part-NNN.parquet`; a coluna `id` repete o identificador para conferência. O `manifest.json` traz o tamanho e
o SHA-256 de cada arquivo, a versão mínima do aplicativo e um vetor de sonda para validar o modelo de embeddings.

### Campos

{_schema_table("pt")}

### Completude por fonte

{_img("completude", "pt", "Barras de 0 a 100% com a parcela dos registros que tem cada campo preenchido.")}

<details>
<summary>Tabela por fonte</summary>

{_coverage_table(stats, "pt")}

Critérios: resumo com pelo menos 40 caracteres; páginas maior que zero; link começando por `http`.

</details>

## Embeddings

| | |
|---|---|
| Modelo | [`{EMBEDDING_MODEL}`]({MODEL_URL}) via [`{ONNX_REPO}`](https://huggingface.co/{ONNX_REPO}) (revisão `{EMBEDDING_REVISION[:12]}`) |
| Dimensões | {FULL_DIMS} (`embeddings-768`) e {APP_DIMS} (`embeddings-256`, projeção Matryoshka) |
| Tipo e norma | `float16`, normalizados em L2 (o produto interno é o cosseno) |
| Texto do documento | `{_cell(DOCUMENT_TEMPLATE)}`, truncado em {MAX_DOCUMENT_TOKENS} tokens |
| Texto da consulta | `{_cell(QUERY_TEMPLATE)}` |

- **Texto usado:** o título e, como corpo, o resumo em português; na falta dele, o resumo em inglês ou, por último,
  as palavras-chave.
- **Normalização de caixa (truecasing) antes do embedding:** muitos registros da CAPES estão em MAIÚSCULAS e, nos anos
  antigos, sem acentos. Antes de gerar os vetores, título, resumo e palavras-chave passam pelo módulo
  `buscateses.textcase`, com um léxico de siglas, nomes próprios e acentos derivado do próprio catálogo. Os metadados
  publicados preservam o texto original da fonte.
- **Matryoshka:** os {APP_DIMS} primeiros valores de cada vetor de {FULL_DIMS} dimensões, re-normalizados em L2. Para
  consultas, faça o mesmo com o vetor da pergunta.
- **Validação:** `manifest.json` → `embedding.probe` guarda os 16 primeiros valores do vetor de
  `{QUERY_TEMPLATE.format(query="educação inclusiva")}`. Para conferir se o seu ambiente reproduz os vetores:

```python
import json
import numpy as np
from huggingface_hub import hf_hub_download
from sentence_transformers import SentenceTransformer

manifesto = json.load(open(hf_hub_download("{DATASET_REPO}", "manifest.json", repo_type="dataset")))
sonda = manifesto["embedding"]["probe"]
vetor = SentenceTransformer("{EMBEDDING_MODEL}").encode(sonda["text"])  # o texto já inclui o prompt
print(np.allclose(vetor[:16], sonda["vector_768_head"], atol=1e-3))  # True
```

## Fontes e cobertura

| Fonte | Anos | Registros | % | Termos |
|---|---|---:|---:|---|
{sources_pt}

- Os dados abertos da CAPES vêm de quatro pacotes do [Portal de Dados Abertos]({SOURCE_URL}):
{packages}
- O Catálogo de Teses da CAPES não traz resumo; resumo, palavras-chave, idioma e orientação vêm da página pública de
  cada trabalho na Plataforma Sucupira.
- A BDTD agrega os repositórios institucionais das universidades brasileiras.
- O mesmo trabalho pode aparecer em mais de uma fonte. Nesse caso prevalece o registro da CAPES, completado com os
  campos que faltam vindos da BDTD; duplicatas entre redes da BDTD são unificadas.

## Como foi gerado

1. Coleta dos CSVs dos dados abertos da CAPES (delimitador `;`, codificação cp1252/latin-1){f" para {first_year}–{start - 1}" if start else ""}.
{f"2. Coleta, por API, dos anos de {start} em diante no Catálogo de Teses da CAPES e na BDTD, com paginação retomável e ritmo limitado de requisições; os registros da CAPES são completados pela página pública de cada trabalho na Plataforma Sucupira." if start else "2. —"}
3. Normalização para um esquema único, com remoção de campos pessoais, alinhamento de nomes de instituição e de área
   aos rótulos da CAPES e deduplicação por `id` e entre fontes. Notas de catálogo e marcadores vazios ("xx", "-") não
   são aceitos como resumo.
4. Normalização de caixa (truecasing) do texto que alimenta o modelo, sem alterar os metadados publicados.
5. Embeddings com `{EMBEDDING_MODEL}` ({FULL_DIMS} dimensões) e projeção Matryoshka de {APP_DIMS} dimensões.
6. Particionamento alinhado, manifesto com SHA-256 e vetor de sonda, e este card gerado a partir dos próprios arquivos.

O código do pipeline é aberto: [`tools/dataset`]({PIPELINE_URL}).

## Usos

**Indicados:** busca textual, semântica e híbrida; bibliometria e cienciometria (produção por área, instituição, região
e período); revisões de literatura; recomendação de trabalhos relacionados; agrupamento temático; avaliação de
modelos de recuperação de informação em português.

**Fora do escopo:** avaliar pessoas, orientadores ou programas (os dados têm lacunas e não são a avaliação oficial da
CAPES); inferir atributos pessoais de autores; tratar os anos recentes como completos; usar como fonte do texto
integral (o dataset traz apenas metadados e o link, quando existe).

## Limitações e vieses

- **Anos recentes incompletos:** {f"{start}–{last_year} ainda estão sendo coletados pelas fontes (veja o aviso em [Panorama](#panorama))." if start else "a cobertura varia por ano."}
- **Concentração regional:** {fmt_pct(region_top[1], records)} dos trabalhos são do {region_top[0]}, reflexo da distribuição da
  pós-graduação no país. Normalize por região ou instituição em comparações.
- **Caixa e acentos:** os registros da CAPES de 2013 em diante estão integralmente em maiúsculas na fonte; boa parte
  dos registros de 1987–1999 também está em maiúsculas e sem acentos. Os metadados preservam o texto original da CAPES.
- **Rótulos de idioma** seguem cada fonte (`PORTUGUES` e `Português`, por exemplo); normalize antes de agrupar.
  {fmt_pct(portuguese, records)} dos trabalhos estão em português.
- **Campos ausentes:** {f"{catalogo_abstract_missing} dos registros do Catálogo de Teses da CAPES não têm resumo na fonte; " if catalogo_abstract_missing else ""}{f"nos registros da BDTD, {bdtd_abstract_missing} não têm resumo em português e {bdtd_area_missing} não têm grande área, porque o repositório de origem não os informa. " if bdtd_area_missing else ""}O número de
  páginas pode ser `0` e o link para o texto completo pode faltar ou ter deixado de responder.
- **Erros da fonte:** os dados são reproduzidos como publicados; erros, lacunas e duplicidades das fontes podem
  permanecer.

## Privacidade

Campos pessoais e identificadores do catálogo original **não** são publicados: documentos de discentes,
orientadores e coorientadores; identificadores de pessoa; data de matrícula; tipo de vínculo; expectativa de atuação;
e categoria docente. Nomes de autores e orientadores são mantidos por serem públicos nas fontes. Para pedir a correção
ou a remoção de um registro, abra uma [issue]({APP_ISSUES_URL}) — e corrija também na fonte original.

## Licença e atribuição

Os dados originais da CAPES são publicados sob a licença Creative Commons Atribuição (CC-BY), e este dataset derivado é
distribuído sob a mesma licença ([CC BY 4.0]({LICENSE_URL})). Ao reutilizar, atribua a fonte: "CAPES — Catálogo de Teses
e Dissertações, Portal de Dados Abertos"{credit_pt}. O texto completo e eventuais licenças
adicionais de cada trabalho permanecem nos repositórios de origem.

**Não afiliação.** Este é um projeto independente, sem afiliação, endosso ou vínculo oficial com a CAPES, o IBICT, o
Google (modelo EmbeddingGemma) ou o Hugging Face.

## Versões

| Versão | Mudanças |
|---|---|
{notes_pt}

Para fixar uma versão: `load_dataset("{DATASET_REPO}", "metadata", revision="{version}")`. O aplicativo BuscaTeses
avisa quando há uma versão nova e atualiza o catálogo local.

## Citação

{_citation(stats, version)}

**ABNT:** BADINI, André. **Catálogo de Teses e Dissertações (CAPES e BDTD)**: dataset BuscaTeses. Versão {version}.
[*S. l.*]: Hugging Face, {year}. Disponível em: {DATASET_URL}. Acesso em: dia mês ano.

<details>
<summary>Citação das fontes originais (CAPES e BDTD)</summary>

```bibtex
@misc{{capes_catalogo_teses_dissertacoes,
  author       = {{CAPES}},
  title        = {{Catálogo de Teses e Dissertações}},
  howpublished = {{\\url{{{SOURCE_URL}}}}},
  note         = {{Portal de Dados Abertos da CAPES. Dados sob licença CC-BY.}}
}}

@misc{{bdtd_ibict,
  author       = {{IBICT}},
  title        = {{Biblioteca Digital Brasileira de Teses e Dissertações}},
  howpublished = {{\\url{{{BDTD_URL}}}}},
  note         = {{Metadados públicos; consulte a licença de cada repositório.}}
}}
```

CAPES. **Catálogo de Teses e Dissertações**. Portal de Dados Abertos da CAPES. Disponível em: {SOURCE_URL}.

</details>

## Links

- Aplicativo BuscaTeses: <{APP_URL}>
- Código do pipeline do dataset: <{PIPELINE_URL}>
- Dúvidas, erros e sugestões: <{APP_ISSUES_URL}>
- Portal de Dados Abertos da CAPES: <{SOURCE_URL}>
- Catálogo de Teses e Dissertações da CAPES: <{CAPES_CATALOGUE_URL}>
- BDTD (IBICT): <{BDTD_URL}>

---

<a id="english"></a>

## English

<p align="center">
  <img src="{ASSET_BASE_URL}/banner.en.png" alt="Theses & Dissertations — open dataset with {fmt_int(records, "en")} works defended in Brazil, {first_year}–{last_year}, with metadata and embeddings" width="900">
</p>

Metadata for **{fmt_int(records, "en")} theses and dissertations** defended in Brazil from **{first_year} to {last_year}** —
title, authorship, advisors, institution, programme, field, keywords and abstract — as Parquet, together with
**semantic embeddings** ([EmbeddingGemma]({MODEL_URL}), {FULL_DIMS} and {APP_DIMS} dimensions). Records come from the CAPES
theses and dissertations catalogue and, for recent years, also from the BDTD (IBICT). The dataset powers
[**BuscaTeses**]({APP_URL}), a free, open-source app that searches the whole catalogue locally, and is open for any
other use: bibliometrics, literature reviews, recommendation and Portuguese retrieval benchmarks.

### At a glance

| | |
|---|---:|
| Theses and dissertations | **{fmt_int(records, "en")}** |
| Years | {first_year}–{last_year} |
| Doctorates · master's | {fmt_int(doctorates, "en")} · {fmt_int(masters, "en")} |
| Institutions (acronyms) | {fmt_int(distinct["institution_acronyms"], "en")} |
| Graduate programmes | {fmt_int(distinct["programs"], "en")} |
| With a Portuguese abstract | {cov("abstract", "en")} |
| With an English abstract | {cov("abstract_en", "en")} |
| Full download · used by the app | {fmt_bytes(all_bytes, "en")} · {fmt_bytes(app_bytes, "en")} |
| Release | `{version}` |

### Quickstart

{_quickstart("en")}

Documents were encoded as `{DOCUMENT_TEMPLATE}` (truncated at {MAX_DOCUMENT_TOKENS} tokens) and queries as
`{QUERY_TEMPLATE}`. `manifest.json` → `embedding.probe` holds the first 16 values of a reference query vector,
so you can check that your environment reproduces the embeddings. Vectors are `float16` and L2-normalised; the 256-dim config holds the first 256 values of each
768-dim vector, re-normalised (Matryoshka). Titles, abstracts and keywords were truecased (sentence case, with
acronyms and proper names restored, using a lexicon derived from the catalogue itself) before embedding; the published
metadata keeps the original source text unchanged.

### Overview

{_img("anos", "en", f"Stacked columns of theses and dissertations per year, {first_year}–{last_year}, by source; years from {start} onward are hatched because they are incomplete." if start else "Columns of theses and dissertations per year.")}
{recent_warning_en}
{_img("areas", "en", "Horizontal bars with the number of records per broad field of knowledge.")}

{_img("nivel-regiao", "en", "Two panels: records by degree level and by region of Brazil.")}

{_img("instituicoes", "en", "Horizontal bars with the institutions that have the most theses and dissertations.")}

{_img("completude", "en", "Bars from 0 to 100% with the share of records that have each field filled in.")}

### Structure and fields

{_files_table(stats, "en")}

Row *i* of every embeddings part matches row *i* of the metadata part with the same number; `id` is repeated for
verification. `manifest.json` lists every file with its size and SHA-256, plus a probe vector to validate the model.

<details>
<summary>Metadata columns</summary>

{_schema_table("en")}

</details>

<details>
<summary>Field completeness by source</summary>

{_coverage_table(stats, "en")}

</details>

### Sources

| Source | Years | Records | % | Terms |
|---|---|---:|---:|---|
{sources_en}

When a work appears in more than one source, the CAPES record wins and missing fields are filled from the BDTD.
Personal identifiers from the original catalogue (ID documents, person ids, enrolment data) are **not** published;
author and advisor names are kept because they are public in the sources. The pipeline is open source:
[`tools/dataset`]({PIPELINE_URL}).

### Intended uses and limitations

- **Good for:** lexical, semantic and hybrid search; bibliometrics; literature reviews; related-work recommendation;
  topic clustering; Portuguese retrieval evaluation.
- **Not for:** assessing people, advisors or programmes (not an official CAPES evaluation); inferring personal
  attributes; full-text access (only metadata and links are included).
- **Caveats:** {f"{start}–{last_year} are incomplete; " if start else ""}{fmt_pct(region_top_en[1], records, "en")} of the works come from the {region_top_en[0]} region;
  CAPES records from 2013 onward are all upper case at the source and many 1987–1999 records lack accents;
  language labels follow each source (`PORTUGUES` vs `Português`); page counts may be `0` and links may be missing or broken.

### License and citation

Original CAPES data is published under Creative Commons Attribution (CC-BY); this derived dataset is released under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Please credit "CAPES — Catálogo de Teses e Dissertações,
Portal de Dados Abertos"{credit_en}, and cite this dataset with the BibTeX entry in the Portuguese section above ("Citação"). This is an
independent project with no affiliation with CAPES, IBICT, Google or Hugging Face.

| Release | Changes |
|---|---|
{notes_en}

Questions, errors and suggestions: <{APP_ISSUES_URL}>.
"""
    )
    return re.sub(r"\n{3,}", "\n\n", card)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def load_or_collect_stats(
    release: Path, manifest: dict[str, Any], *, refresh: bool = False
) -> dict[str, Any]:
    """Read ``card_stats.json`` when it matches the manifest, else recompute it."""
    cache = Path(release) / STATS_FILENAME
    if cache.is_file() and not refresh:
        stats = json.loads(cache.read_text(encoding="utf-8"))
        if stats.get("version") == manifest.get("version") and stats.get("records") == manifest.get(
            "records"
        ):
            stats["files"] = _manifest_file_summary(manifest)
            stats["sources"] = manifest.get("sources", [])
            return stats
    stats = collect_stats(release, manifest)
    cache.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return stats


def build(
    release: Path, *, png: bool = True, refresh: bool = False, font_dir: Path | None = None
) -> dict[str, Any]:
    """Write ``README.md`` and ``assets/`` for *release*; return the stats."""
    release = Path(release)
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    stats = load_or_collect_stats(release, manifest, refresh=refresh)
    svgs = render_assets(stats, release / ASSETS_DIR)
    if png:
        rasterize(svgs, font_dir=font_dir)
        LOGGER.info("%d imagem(ns) PNG gerada(s) em %s", len(svgs), release / ASSETS_DIR)
    (release / "README.md").write_text(render_card(stats, manifest), encoding="utf-8")
    LOGGER.info("Card do dataset escrito em %s", release / "README.md")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gera o card do dataset (README, gráficos e banner)"
    )
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--no-png", action="store_true", help="só SVG; não usa o Playwright")
    parser.add_argument("--refresh", action="store_true", help="recalcula as estatísticas")
    parser.add_argument("--font-dir", type=Path, default=None)
    parser.add_argument(
        "--app-banner",
        type=Path,
        default=None,
        metavar="DIR",
        help="também gera banner.png e banner.en.png do README do GitHub em DIR",
    )
    arguments = parser.parse_args(argv)
    if arguments.app_banner:
        release = Path(arguments.release)
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        stats = load_or_collect_stats(release, manifest)
        target = Path(arguments.app_banner)
        target.mkdir(parents=True, exist_ok=True)
        svgs = []
        for lang, name in (("pt", "banner.svg"), ("en", "banner.en.svg")):
            svg_path = target / name
            svg_path.write_text(app_banner(stats, lang), encoding="utf-8")
            svgs.append(svg_path)
        rasterize(svgs, font_dir=arguments.font_dir)
        for svg_path in svgs:
            svg_path.unlink()
    build(
        arguments.release,
        png=not arguments.no_png,
        refresh=arguments.refresh,
        font_dir=arguments.font_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
