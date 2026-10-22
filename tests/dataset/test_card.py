"""Tests for the Hugging Face dataset card generator (stats, SVG charts, README)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import card
import common
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


def _row(identifier: str, year: int, source: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {name: None for name, _ in common.METADATA_FIELDS}
    row.update(
        {
            "id": identifier,
            "year": year,
            "defense_date": f"{year}-03-01",
            "title": f"Título {identifier}",
            "author": "Autor",
            "advisors": "Orientador",
            "degree": "Mestrado",
            "institution": "UNIVERSIDADE TESTE",
            "institution_acronym": "UT",
            "uf": "DF",
            "region": "CENTRO-OESTE",
            "program": "Programa",
            "program_code": "P1",
            "great_area": "CIÊNCIAS HUMANAS",
            "keywords": "a; b",
            "abstract": "Um resumo suficientemente longo para contar como preenchido.",
            "language": "PORTUGUES",
            "pages": 120,
            "url": "https://example.org/t",
            "source": source,
        }
    )
    row.update(overrides)
    return row


def _write_release(tmp_path: Path) -> Path:
    release = tmp_path / "release"
    (release / "metadata").mkdir(parents=True)
    rows = [
        _row("capes:1", 2023, "dados.csv", degree="Doutorado"),
        _row("capes:2", 2024, "dados.csv", region="SUDESTE", language="Português"),
        _row("capes-catalogo:3", 2025, "capes-catalogo", abstract="xx"),
        _row(
            "bdtd:R_4",
            2026,
            "bdtd",
            great_area=None,
            pages=0,
            abstract=None,
            institution_acronym="UNB",
        ),
    ]
    arrays = [
        pa.array([row[name] for row in rows], type=dtype) for name, dtype in common.METADATA_FIELDS
    ]
    path = release / "metadata" / "part-000.parquet"
    pq.write_table(pa.Table.from_arrays(arrays, schema=common.METADATA_SCHEMA), path)
    manifest = {
        "version": "2026.10.21.3",
        "records": len(rows),
        "files": [
            {"path": "metadata/part-000.parquet", "rows": 4, "bytes": 2_100_000_000},
            {"path": "embeddings-256/part-000.parquet", "rows": 4, "bytes": 800_000_000},
            {"path": "embeddings-768/part-000.parquet", "rows": 4, "bytes": 2_400_000_000},
        ],
        "sources": [],
    }
    (release / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return release


@pytest.fixture()
def release(tmp_path: Path) -> Path:
    return _write_release(tmp_path)


def test_collect_stats_counts_sources_years_and_coverage(release: Path) -> None:
    stats = card.collect_stats(release)

    assert stats["records"] == 4
    assert stats["years"] == [2023, 2026]
    assert stats["source_years"] == {
        "capes": [2023, 2024, 2],
        "catalogo": [2025, 2025, 1],
        "bdtd": [2026, 2026, 1],
    }
    by_year = {row["year"]: row for row in stats["by_year"]}
    assert by_year[2026] == {"year": 2026, "capes": 0, "catalogo": 0, "bdtd": 1}
    # "xx" is a placeholder, not an abstract; the BDTD row has none at all.
    assert stats["coverage"]["catalogo"]["abstract"] == 0
    assert stats["coverage"]["bdtd"]["great_area"] == 0
    assert stats["coverage"]["bdtd"]["pages"] == 0
    assert stats["coverage"]["capes"]["abstract"] == 2
    # Language labels are folded across sources (PORTUGUES == Português).
    assert dict(map(tuple, stats["language"]))["PORTUGUES"] == 4
    assert stats["distinct"]["institution_acronyms"] == 2
    assert stats["files"]["metadata"] == {"files": 1, "rows": 4, "bytes": 2_100_000_000}
    assert card.recent_start(stats) == 2025


def test_build_writes_readme_svgs_and_cache(release: Path) -> None:
    stats = card.build(release, png=False)

    assert (release / card.STATS_FILENAME).is_file()
    readme = (release / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("---\nlicense: cc-by-4.0\n")
    for name in card.asset_names():
        svg = release / "assets" / name.replace(".png", ".svg")
        root = ET.fromstring(svg.read_text(encoding="utf-8"))
        assert root.tag.endswith("svg")
        assert f"{card.ASSET_BASE_URL}/{name}" in readme
    # A second build reuses the cache while the manifest is unchanged.
    assert (
        card.load_or_collect_stats(release, json.loads((release / "manifest.json").read_text()))[
            "records"
        ]
        == stats["records"]
    )


def test_card_content_portuguese_first_then_english(release: Path) -> None:
    stats = card.collect_stats(release)
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    text = card.render_card(stats, manifest)

    # Front matter: viewer opens the metadata config; richer Hub metadata.
    assert "  default: true" in text
    assert "task_ids:\n- document-retrieval" in text
    assert "- feature-extraction" in text
    assert "- bdtd" in text

    # Banner, badges and the language switch at the top.
    head = text.split("# Catálogo de Teses e Dissertações (CAPES e BDTD)", 1)[0]
    assert "banner.pt.png" in head
    assert "img.shields.io/badge/" in head
    assert 'href="#english"' in head

    portuguese, english = text.split("## English", 1)
    for section in (
        "## Em números",
        "## Comece aqui",
        "## Panorama",
        "## Estrutura",
        "## Embeddings",
        "## Fontes e cobertura",
        "## Como foi gerado",
        "## Usos",
        "## Limitações e vieses",
        "## Privacidade",
        "## Licença e atribuição",
        "## Versões",
        "## Citação",
    ):
        assert section in portuguese
    for section in ("### At a glance", "### Quickstart", "### Sources", "### License and citation"):
        assert section in english

    # Truecasing, model and citation details survive from the previous card.
    assert "Normalização de caixa (truecasing) antes do embedding" in portuguese
    assert "preservam o texto original da fonte" in portuguese
    assert "truecased" in english
    assert "lexicon derived from the catalogue itself" in english
    assert common.EMBEDDING_REVISION[:12] in portuguese
    assert "@misc{buscateses_capes_2026_10_21_3," in portuguese
    assert "author       = {Badini, André}" in portuguese
    assert "capes_catalogo_teses_dissertacoes" in portuguese
    assert "bdtd_ibict" in portuguese
    assert "BADINI, André." in portuguese

    # Recent years are flagged as incomplete in both languages.
    assert "anos recentes incompletos" in portuguese
    assert "recent years are incomplete" in english
    assert "2025–2026" in portuguese

    # Pipes inside table cells are escaped so the Markdown table survives.
    assert "`title: {title} \\| text: {text}`" in portuguese
    assert "None" not in text


def test_card_without_recent_sources(tmp_path: Path) -> None:
    release = _write_release(tmp_path)
    stats = card.collect_stats(release)
    stats["by_year"] = [
        {**row, "capes": row["capes"] + row["catalogo"] + row["bdtd"], "catalogo": 0, "bdtd": 0}
        for row in stats["by_year"]
    ]
    stats["source_years"] = {"capes": [2023, 2026, 4]}
    stats["coverage"] = {"capes": stats["coverage"]["capes"]}
    text = card.render_card(stats, {"version": "2026.1.1"})
    assert card.recent_start(stats) is None
    assert "anos recentes incompletos" not in text
    assert "parcial" not in card.chart_by_year(stats, "pt")


def test_charts_render_numbers_in_each_locale(release: Path) -> None:
    stats = card.collect_stats(release)
    pt = card.chart_by_year(stats, "pt")
    en = card.chart_by_year(stats, "en")
    assert "parcial" in pt and "partial" in en
    assert "Teses e dissertações por ano de defesa" in pt
    assert "per defence year" in en
    areas = card.chart_great_areas(stats, "pt")
    assert "Ciências Humanas" in areas and "Não informado" in areas


def test_formatting_helpers() -> None:
    assert card.fmt_int(1728035) == "1.728.035"
    assert card.fmt_int(1728035, "en") == "1,728,035"
    assert card.fmt_pct(1, 3) == "33,3%"
    assert card.fmt_pct(711, 1_728_035) == "<0,1%"
    assert card.fmt_pct(0, 10) == "0,0%"
    assert card.fmt_millions(1_728_035) == "1,73 milhão"
    assert card.fmt_millions(2_500_000, "en") == "2.50 million"
    assert card.fmt_bytes(2_160_000_000) == "2,2 GB"
    assert card.fmt_bytes(844_000_000, "en") == "844 MB"
    assert card.fold("Lingüística") == "LINGUISTICA"


def test_badge_url_escaping() -> None:
    url = card.badge("anos", "1987–2026", "#0E4F4A")
    assert url == "https://img.shields.io/badge/anos-1987%E2%80%932026-0E4F4A"
    assert "a--b" in card.badge("a-b", "x_y", "000")
    assert "x__y" in card.badge("a-b", "x_y", "000")
    assert card.badge("app", "BuscaTeses", "000", logo="github").endswith(
        "?logo=github&logoColor=white"
    )


def test_nice_ceiling_handles_small_and_large_values() -> None:
    assert card._nice_ceiling(94_503) == (100_000, 20_000)
    assert card._nice_ceiling(2) == (2, 1)
    assert card._nice_ceiling(0) == (10, 2)
