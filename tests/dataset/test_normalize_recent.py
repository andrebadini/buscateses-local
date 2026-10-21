"""Offline tests for the recent-years normaliser (synthetic raw pages)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import common
import normalize_recent
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


def _metadata_row(identifier: str, year: int, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": identifier,
        "year": year,
        "defense_date": f"{year}-01-01",
        "title": f"Título {identifier}",
        "author": f"Autor {identifier}",
        "advisors": None,
        "degree": "Mestrado",
        "institution": "Universidade Teste",
        "institution_acronym": "UT",
        "uf": "DF",
        "region": "CENTRO-OESTE",
        "program": "Educação",
        "program_code": "P1",
        "great_area": "CIÊNCIAS HUMANAS",
        "knowledge_area": "EDUCAÇÃO",
        "evaluation_area": "EDUCAÇÃO",
        "concentration_area": None,
        "research_line": None,
        "keywords": None,
        "keywords_en": None,
        "abstract": None,
        "abstract_en": None,
        "language": "PORTUGUES",
        "pages": 100,
        "library": None,
        "url": None,
        "source": "dados_2024.csv",
    }
    row.update(overrides)
    return row


def _write_metadata(path: Path, rows: list[dict[str, object]]) -> None:
    arrays = [
        pa.array([row[name] for row in rows], type=dtype)
        for name, dtype in common.METADATA_FIELDS
    ]
    pq.write_table(
        pa.Table.from_arrays(arrays, schema=common.METADATA_SCHEMA), path
    )


def _write_page(
    root: Path,
    source: str,
    year: int,
    partition: str,
    records: list[dict[str, object]],
) -> None:
    path = root / source / str(year) / partition / "page-00001.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    if source == "bdtd":
        response = {"records": [{"rawData": record} for record in records]}
    else:
        response = {"tesesDissertacoes": records}
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"year": year, "response": response}, handle, ensure_ascii=False)


def _bdtd_raw(
    identifier: str,
    title: str,
    author: str,
    *,
    year: str = "2025",
    **extra: object,
) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": identifier,
        "title": title,
        "author": [author],
        "publishDate": [year],
        "format": ["masterThesis"],
        "description": "Resumo em português com ção e educação inclusiva.",
        "dc.subject.por.fl_str_mv": ["Educação inclusiva", "Políticas públicas"],
        "dc.language.iso.fl_str_mv": ["por"],
        "dc.publisher.none.fl_str_mv": ["Universidade Federal X (UFX)"],
        "instacron_str": "UFX",
        "dc.publisher.program.fl_str_mv": [
            "Programa de Pós-Graduação em Educação"
        ],
        "dc.identifier.uri.fl_str_mv": [f"https://example.org/tese/{identifier}"],
        "dc.subject.cnpq.fl_str_mv": ["CNPQ::CIENCIAS HUMANAS::EDUCACAO"],
    }
    raw.update(extra)
    return raw


def _capes_raw(
    identifier: str,
    title: str,
    author: str,
    *,
    link: str | None = None,
    **extra: object,
) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": identifier,
        "titulo": title,
        "autor": author,
        "instituicao": "Universidade Federal X",
        "nomePrograma": "Programa de Pós-Graduação em Educação",
        "dataDefesa": "2025-05-06T00:00:00.000Z",
        "grauAcademico": "MESTRADO",
        "paginas": "120",
        "biblioteca": "Biblioteca Central",
        "link": link
        or (
            "https://catalogodeteses.capes.gov.br/catalogo-teses/"
            f"#!detalhes/&id_trabalho={identifier}"
        ),
    }
    raw.update(extra)
    return raw


def _build_raw(root: Path) -> None:
    _write_page(
        root,
        "bdtd",
        2025,
        "instname-a",
        [
            _bdtd_raw(
                "A1",
                "Trabalho Já Publicado",
                "Silva, Maria",
            ),
            _bdtd_raw(
                "B1",
                "Estudo sobre Educação Inclusiva",
                "Souza, João",
            ),
            _bdtd_raw(
                "C1",
                "Pesquisa Antiga",
                "Lima, Ana",
                year="2024",
            ),
        ],
    )
    _write_page(
        root,
        "bdtd",
        2025,
        "instname-b",
        [
            # Same id from another BDTD network, with fewer fields.
            {
                "id": "B1",
                "title": "Estudo sobre Educação Inclusiva",
                "author": ["Souza, João"],
                "publishDate": ["2025"],
                "format": ["doctoralThesis"],
            },
            _bdtd_raw(
                "E1",
                "Gestão Escolar e Democracia",
                "Lima, Ana",
            ),
            {"id": "invalid", "publishDate": ["2025"]},
        ],
    )
    _write_page(
        root,
        "capes",
        2025,
        "all",
        [
            _capes_raw("77", "Gestão Escolar e Democracia", "Ana Lima"),
            _capes_raw("99", "Trabalho Já Publicado", "Maria Silva"),
        ],
    )


def test_parse_bdtd_record_maps_fields() -> None:
    raw = _bdtd_raw(
        "x1",
        "Título da Pesquisa",
        "Souza, João",
        **{
            "dc.date.issued.fl_str_mv": ["2025-03-04"],
            "dc.contributor.advisor1.fl_str_mv": ["Pereira, Carla"],
            "dc.contributor.advisor-co1.fl_str_mv": ["Alves, Bruno"],
            "dc.identifier.citation.fl_str_mv": ["Dissertação (Mestrado Profissional)"],
            "dc.language.iso.fl_str_mv": ["eng"],
        },
    )
    raw.pop("description")
    raw["description"] = "This is an abstract written in English about the topic."
    record = normalize_recent.parse_bdtd_record(raw)

    assert record is not None
    assert record["id"] == "bdtd:x1"
    assert record["year"] == 2025
    assert record["defense_date"] == "2025-03-04"
    assert record["title"] == "Título da Pesquisa"
    assert record["author"] == "João Souza"
    assert record["advisors"] == "Carla Pereira; Bruno Alves (coorientador)"
    assert record["degree"] == "Mestrado Profissional"
    assert record["institution"] == "Universidade Federal X"
    assert record["institution_acronym"] == "UFX"
    assert record["program"] == "Educação"
    assert record["keywords"] == "Educação inclusiva; Políticas públicas"
    assert record["abstract"] is None
    assert record["abstract_en"].startswith("This is an abstract")
    assert record["language"] == "INGLES"
    assert record["source"] == "bdtd"
    assert record["url"] == "https://example.org/tese/x1"


def test_parse_bdtd_record_falls_back_to_publish_date_and_none_advisor() -> None:
    raw = _bdtd_raw(
        "x2",
        "Outro Título",
        "Souza, João",
        **{"dc.contributor.none.fl_str_mv": ["PEREIRA, CARLA"]},
    )
    raw.pop("dc.identifier.uri.fl_str_mv")
    raw["url"] = ["https://example.org/x2"]
    record = normalize_recent.parse_bdtd_record(raw)

    assert record is not None
    assert record["year"] == 2025
    assert record["defense_date"] is None
    assert record["advisors"] == "CARLA PEREIRA"
    assert record["url"] == "https://example.org/x2"

    assert normalize_recent.parse_bdtd_record({"id": "x3"}) is None


def test_parse_capes_record_maps_fields() -> None:
    record = normalize_recent.parse_capes_record(
        _capes_raw("9", "Título", "Ana Lima", grauAcademico="DOUTORADO")
    )
    assert record is not None
    assert record["id"] == "capes-catalogo:9"
    assert record["year"] == 2025
    assert record["defense_date"] == "2025-05-06"
    assert record["author"] == "Ana Lima"
    assert record["degree"] == "Doutorado"
    assert record["institution"] == "Universidade Federal X"
    assert record["program"] == "Educação"
    assert record["pages"] == 120
    assert record["library"] == "Biblioteca Central"
    assert record["source"] == "capes-catalogo"
    assert record["url"].endswith("id_trabalho=9")


def test_text_helpers() -> None:
    assert normalize_recent.person_name("Souza, João") == "João Souza"
    assert normalize_recent.people(["Souza, João", "João Souza"]) == "João Souza"
    assert normalize_recent.clean_program(
        "Programa de Pós-Graduação em Educação"
    ) == "Educação"
    assert normalize_recent.clean_institution("Universidade X (UX)") == "Universidade X"
    assert normalize_recent.language_label("por") == "PORTUGUES"
    assert normalize_recent.language_label("eng") == "INGLES"
    assert normalize_recent.detect_language("This is an abstract about the topic") == "en"
    assert normalize_recent.detect_language("Este resumo trata da educação inclusiva") == "pt"
    assert normalize_recent.author_tokens("Maria da Silva") == {"silva"}
    assert normalize_recent.author_tokens("João Silva Filho") == {"silva", "filho"}
    assert normalize_recent.author_tokens("") == set()


def test_enrich_record_fills_only_missing_fields() -> None:
    tables = normalize_recent.ExistingTables(
        institution={
            "UNIVERSIDADE TESTE": {
                "institution_acronym": "UT",
                "uf": "DF",
                "region": "CENTRO-OESTE",
            }
        },
        program={
            ("UNIVERSIDADE TESTE", "EDUCACAO"): {
                "program_code": "P1",
                "great_area": "CIÊNCIAS HUMANAS",
                "knowledge_area": "EDUCAÇÃO",
                "evaluation_area": "EDUCAÇÃO",
            }
        },
        great_labels={"CIENCIAS HUMANAS": "CIÊNCIAS HUMANAS"},
        knowledge_labels={"EDUCACAO": "EDUCAÇÃO"},
    )
    record: dict[str, object] = {
        "institution": "Universidade Teste",
        "institution_acronym": None,
        "uf": "GO",
        "region": None,
        "program": "Programa de Pós-Graduação em Educação",
        "program_code": None,
        "great_area": None,
        "knowledge_area": None,
        "evaluation_area": None,
    }
    normalize_recent.enrich_record(record, tables)

    assert record["institution_acronym"] == "UT"
    assert record["uf"] == "GO"  # never overwritten
    assert record["region"] == "CENTRO-OESTE"
    assert record["program_code"] == "P1"
    assert record["great_area"] == "CIÊNCIAS HUMANAS"

    great, knowledge = normalize_recent._cnpq_areas_from_value(
        "CNPQ::CIENCIAS HUMANAS::EDUCACAO", tables
    )
    assert great == "CIÊNCIAS HUMANAS"
    assert knowledge == "EDUCAÇÃO"


def test_normalize_records_dedupes_enriches_and_filters(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    existing = tmp_path / "metadata"
    existing.mkdir()
    _write_metadata(
        existing / "part-006.parquet",
        [
            _metadata_row(
                "capes:1",
                2024,
                title="Trabalho Já Publicado",
                author="Maria Silva",
                institution="Universidade Federal X",
                institution_acronym="UFX",
                url=(
                    "https://catalogodeteses.capes.gov.br/catalogo-teses/"
                    "#!detalhes/&id_trabalho=99"
                ),
            )
        ],
    )
    _build_raw(raw)

    records, stats, tables = normalize_recent.normalize_records(
        raw, existing, first_year=2025
    )

    assert stats.raw_records == 8
    assert stats.invalid == 1
    assert stats.before_first_year == 1
    assert stats.already_published == 2  # BDTD A1 and CAPES 99
    assert stats.duplicate_ids == 1
    assert stats.cross_source_duplicates == 1
    assert stats.written == 2
    assert tables.records == 1

    by_id = {record["id"]: record for record in records}
    assert set(by_id) == {"bdtd:B1", "capes-catalogo:77"}

    b1 = by_id["bdtd:B1"]
    assert b1["degree"] == "Mestrado"
    assert b1["institution_acronym"] == "UFX"
    assert b1["uf"] == "DF"
    assert b1["program_code"] == "P1"
    assert b1["great_area"] == "CIÊNCIAS HUMANAS"
    assert b1["knowledge_area"] == "EDUCAÇÃO"
    assert b1["advisors"] is None

    merged = by_id["capes-catalogo:77"]
    assert merged["source"] == "capes-catalogo"
    assert merged["author"] == "Ana Lima"
    assert merged["abstract"].startswith("Resumo em português")
    assert merged["keywords"] == "Educação inclusiva; Políticas públicas"
    assert merged["institution_acronym"] == "UFX"
    assert merged["language"] == "PORTUGUES"

    assert [record["year"] for record in records] == [2025, 2025]
    assert [record["id"] for record in records] == sorted(by_id)


def test_normalize_recent_writes_next_part_and_report(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    existing = tmp_path / "metadata"
    existing.mkdir()
    _write_metadata(
        existing / "part-006.parquet",
        [
            _metadata_row(
                "capes:1",
                2024,
                title="Trabalho Já Publicado",
                author="Maria Silva",
                institution="Universidade Federal X",
                institution_acronym="UFX",
                url=(
                    "https://catalogodeteses.capes.gov.br/catalogo-teses/"
                    "#!detalhes/&id_trabalho=99"
                ),
            )
        ],
    )
    _build_raw(raw)
    out = tmp_path / "recent" / "metadata"
    report_path = tmp_path / "recent" / "normalize_recent_report.json"

    report = normalize_recent.normalize_recent(
        raw, existing, out, report_path=report_path, first_year=2025
    )

    path = out / "part-007.parquet"
    assert path.is_file()
    table = pq.read_table(path)
    assert table.schema.names == list(common.METADATA_COLUMNS)
    assert table.column("id").to_pylist() == ["bdtd:B1", "capes-catalogo:77"]
    assert table.column("year").to_pylist() == [2025, 2025]

    stored = json.loads(report_path.read_text(encoding="utf-8"))
    assert stored == report
    assert stored["output"]["records"] == 2
    assert stored["output"]["by_source_year"]["bdtd"]["2025"] == 1
    assert stored["output"]["by_source_year"]["capes-catalogo"]["2025"] == 1
    assert stored["mapped_by_source_year"]["bdtd"]["2025"] == 4
    assert stored["null_percent"]["advisors"] == 100.0
    assert stored["null_percent"]["uf"] == 0.0


def test_write_metadata_never_deletes_parts_silently(tmp_path: Path) -> None:
    existing = tmp_path / "metadata"
    existing.mkdir()
    _write_metadata(existing / "part-006.parquet", [_metadata_row("capes:1", 2024)])
    out = tmp_path / "out"
    out.mkdir()
    _write_metadata(out / "part-008.parquet", [_metadata_row("old", 2024)])

    with pytest.raises(ValueError, match="--overwrite"):
        normalize_recent.write_metadata([], out, existing)

    path = normalize_recent.write_metadata([], out, existing, overwrite=True)
    assert path == out / "part-007.parquet"
    assert pq.read_table(path).num_rows == 0
    assert not (out / "part-008.parquet").exists()
    assert not (out / "part-006.parquet").exists()


def test_write_metadata_refuses_output_equal_to_existing(tmp_path: Path) -> None:
    existing = tmp_path / "metadata"
    existing.mkdir()
    _write_metadata(existing / "part-006.parquet", [_metadata_row("capes:1", 2024)])
    with pytest.raises(ValueError, match="mesmo"):
        normalize_recent.write_metadata([], existing, existing)


def test_parse_capes_record_uses_sucupira_detail_and_drops_placeholders() -> None:
    raw = _capes_raw("10", "Título", "Ana Lima")
    raw["_sucupira"] = {
        "resumo": "Este trabalho investiga a educação inclusiva em escolas públicas brasileiras.",
        "palavras": "Educação inclusiva;Escola pública; ;xx",
        "abstract": "xx",
        "keyword": "Inclusive education;Public school",
        "idioma": "PORTUGUES",
        "area_concentracao": "EDUCAÇÃO",
        "linha_pesquisa": "POLÍTICAS EDUCACIONAIS",
        "codigo_programa": "12001015009P1",
        "orientadores": ["MARIA SOUZA", "JOÃO PEREIRA"],
    }
    record = normalize_recent.parse_capes_record(raw)
    assert record is not None
    assert record["abstract"].startswith("Este trabalho")
    assert record["abstract_en"] is None
    assert record["keywords"] == "Educação inclusiva; Escola pública"
    assert record["keywords_en"] == "Inclusive education; Public school"
    assert record["language"] == "PORTUGUES"
    assert record["advisors"] == "MARIA SOUZA; JOÃO PEREIRA"
    assert record["program_code"] == "12001015009P1"
    assert record["concentration_area"] == "EDUCAÇÃO"
    assert record["research_line"] == "POLÍTICAS EDUCACIONAIS"


def test_iter_raw_records_attaches_cached_sucupira_detail(tmp_path: Path) -> None:
    import enrich_sucupira

    _write_page(tmp_path, "capes", 2025, "all", [_capes_raw("11", "Título", "Ana Lima")])
    enrich_sucupira._write(
        enrich_sucupira.detail_path(tmp_path, 2025, "11"),
        "11",
        {"resumo": "Resumo detalhado obtido da página pública da Plataforma Sucupira."},
    )
    records = list(normalize_recent.iter_raw_records(tmp_path))
    assert records[0][1]["_sucupira"]["resumo"].startswith("Resumo detalhado")


def test_person_name_drops_repository_tags_and_life_dates() -> None:
    assert normalize_recent.person_name("Momesso, Maria Regina [UNESP]") == "Maria Regina Momesso"
    assert normalize_recent.person_name("Fernandes, Angelo José, 1973-") == "Angelo José Fernandes"


def test_bdtd_contributor_list_yields_advisor_and_program() -> None:
    raw = _bdtd_raw("c1", "Título", "Ana Lima")
    raw["dc.contributor.none.fl_str_mv"] = [
        "Fernandes, Angelo José, 1973-",
        "Santos, Mauro",
        "Universidade Estadual de Campinas (UNICAMP). Instituto de Artes",
        "Programa de Pós-Graduação em Música",
    ]
    for key in [k for k in raw if k.startswith("dc.contributor.advisor")]:
        del raw[key]
    raw.pop("dc.publisher.program.fl_str_mv", None)
    record = normalize_recent.parse_bdtd_record(raw)
    assert record is not None
    assert record["advisors"] == "Angelo José Fernandes"
    assert record["program"] == "Música"


def test_enrich_record_aligns_labels_by_acronym_and_advisor() -> None:
    from collections import Counter

    tables = normalize_recent.ExistingTables()
    label = "UNIVERSIDADE EST.PAULISTA JÚLIO DE MESQUITA FILHO"
    key = normalize_recent.name_key(label)
    tables.institution[key] = {
        "label": label,
        "institution_acronym": "UNESP",
        "uf": "SP",
        "region": "SUDESTE",
    }
    tables.acronym_labels["UNESP"] = Counter({label: 10})
    tables.advisor_program[(key, "MARIA REGINA MOMESSO")] = {
        "label": "LETRAS",
        "program_code": "33004056084P1",
        "great_area": "LINGÜÍSTICA, LETRAS E ARTES",
        "knowledge_area": "LETRAS",
        "evaluation_area": "LINGUÍSTICA E LITERATURA",
    }
    record = {
        "institution": "Universidade Estadual Paulista (Unesp)",
        "institution_acronym": "unesp",
        "advisors": "Maria Regina Momesso",
        "program": None,
    }
    normalize_recent.enrich_record(record, tables)
    assert record["institution"] == label
    assert record["uf"] == "SP"
    assert record["program"] == "LETRAS"
    assert record["great_area"] == "LINGÜÍSTICA, LETRAS E ARTES"

    other = {"institution": "Instituto Novo", "program": "Programa de Pós-Graduação em Química"}
    normalize_recent.enrich_record(other, tables)
    assert other["institution"] == "INSTITUTO NOVO"
    assert other["program"] == "QUÍMICA"


def test_bdtd_drops_thesis_notes_and_splits_joined_keywords() -> None:
    raw = _bdtd_raw("n1", "Título", "Ana Lima")
    raw["description"] = "Tese (Doutorado em Direito) - Universidade de São Paulo, 2025"
    raw.pop("dc.subject.por.fl_str_mv", None)
    raw["dc.subject.keyword.pt_BR.fl_str_mv"] = ["Inteligência artificial||Poder Judiciário"]
    record = normalize_recent.parse_bdtd_record(raw)
    assert record is not None
    assert record["abstract"] is None and record["abstract_en"] is None
    assert record["keywords"] == "Inteligência artificial; Poder Judiciário"
