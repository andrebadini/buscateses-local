from __future__ import annotations

import csv
import io

import pytest

from buscateses.exporters import (
    abnt_reference,
    export_abnt,
    export_bibtex,
    export_csv,
    export_records,
    export_ris,
)


@pytest.fixture
def records() -> list[dict]:
    return [
        {
            "id": "capes:0001",
            "title": "Educação inclusiva: desafios & possibilidades",
            "author": "SILVA, Maria",
            "advisors": "SOUZA, João; LIMA, Ana (coorientadora)",
            "year": 2020,
            "degree": "Doutorado",
            "institution": "Universidade Federal de Minas Gerais",
            "institution_acronym": "UFMG",
            "uf": "MG",
            "program": "Educação",
            "great_area": "CIENCIAS HUMANAS",
            "knowledge_area": "Educação",
            "keywords": "educação inclusiva; políticas públicas",
            "pages": 150,
            "language": "Português",
            "url": "https://example.org/tese/1",
            "abstract": 'Resumo com "aspas", ponto-e-vírgula; e\nquebra de linha.',
        },
        {
            "id": "capes:0002",
            "title": "Educação inclusiva na prática",
            "author": "Souza, João",
            "advisors": "Lima, Ana",
            "year": 2021,
            "degree": "Mestrado",
            "institution": "Universidade de São Paulo",
            "institution_acronym": "USP",
            "uf": "SP",
            "program": "Educação",
            "great_area": "CIENCIAS HUMANAS",
            "knowledge_area": "Educação",
            "keywords": "escola; inclusão",
            "pages": 90,
            "language": "Português",
            "url": "https://example.org/tese/2",
            "abstract": "Outro resumo.",
        },
    ]


def test_csv_has_bom_semicolon_and_crlf(records: list[dict]) -> None:
    body = export_csv(records)
    assert body.startswith("﻿")
    assert body.splitlines()[0].lstrip("﻿").startswith("ID;Título;Autor;")
    rows = list(csv.reader(io.StringIO(body.lstrip("﻿")), delimiter=";"))
    assert rows[0][0] == "ID"
    assert rows[1][0] == "capes:0001"
    assert rows[1][-1] == records[0]["abstract"]
    assert rows[2][-1] == records[1]["abstract"]
    raw = body.encode("utf-8")
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b";" in raw
    assert b"\r\n" in raw


def test_csv_neutralises_spreadsheet_formulas() -> None:
    records = [
        {
            "id": "capes:0001",
            "title": "=1+1",
            "author": "+SUM(A1:A2)",
            "institution": "-2+3",
            "program": "@cmd",
            "url": "\t=HYPERLINK(1)",
            "language": "\r@cmd",
            "year": 2020,
        }
    ]
    text = export_csv(records)
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=";"))
    header, row = rows[0], rows[1]
    values = dict(zip(header, row, strict=True))
    assert values["Título"] == "'=1+1"
    assert values["Autor"].startswith("'+")
    assert values["Autor"].endswith("(A1:A2)")
    assert values["Instituição"] == "'-2+3"
    assert values["Programa"].casefold() == "'@cmd"
    assert values["URL"] == "'\t=HYPERLINK(1)"
    assert values["Idioma"] == "'\r@cmd"
    assert values["Ano"] == "2020"


def test_csv_handles_empty_records() -> None:
    lines = export_csv([]).splitlines()
    assert len(lines) == 1
    assert lines[0].lstrip("﻿").startswith("ID;")


def test_bibtex_types_keys_and_escaping(records: list[dict]) -> None:
    body = export_bibtex(records)
    assert "@phdthesis{" in body
    assert "@mastersthesis{" in body
    assert "silva2020educacao" in body
    assert "souza2021educacao" in body
    assert "author = {SILVA, Maria}" in body
    assert r"Educação inclusiva: desafios \& possibilidades" in body
    assert body.count("@") == 2
    assert body.endswith("\n")


def test_bibtex_keys_are_unique() -> None:
    record = {
        "author": "Silva, Maria",
        "year": 2020,
        "title": "Educação inclusiva",
        "degree": "Doutorado",
    }
    body = export_bibtex([record, dict(record), dict(record)])
    keys = [line.split("{", 1)[1].rstrip(",") for line in body.splitlines() if line.startswith("@")]
    assert len(keys) == len(set(keys)) == 3


def test_ris_structure(records: list[dict]) -> None:
    body = export_ris(records)
    blocks = body.strip().split("\n\n")
    assert len(blocks) == 2
    assert blocks[0].startswith("TY  - THES\n")
    assert "TI  - Educação inclusiva: desafios & possibilidades" in blocks[0]
    assert "AU  - SILVA, Maria" in blocks[0]
    assert "A2  - SOUZA, João" in blocks[0]
    assert "A2  - LIMA, Ana (coorientadora)" in blocks[0]
    assert "PY  - 2020" in blocks[0]
    assert "KW  - educação inclusiva" in blocks[0]
    assert "ER  -" in blocks[0]


def test_abnt_reference(records: list[dict]) -> None:
    text = abnt_reference(records[0])
    assert text.startswith("SILVA, Maria.")
    assert "Educação inclusiva: desafios & possibilidades." in text
    assert "150 f." in text
    assert "Tese (Doutorado em Educação)" in text
    assert "Universidade Federal de Minas Gerais, 2020" in text
    assert "\n" not in text
    body = export_abnt(records)
    assert body.count("\n\n") == 1
    assert body.endswith("\n")


def test_abnt_without_institution(records: list[dict]) -> None:
    record = dict(records[0])
    record["institution"] = ""
    text = abnt_reference(record)
    assert "2020" in text
    assert text.count("2020") >= 1
    assert "  " not in text


def test_dispatch_and_invalid_format(records: list[dict]) -> None:
    assert export_records(records, "CSV") == export_csv(records)
    assert export_records(records, "ris") == export_ris(records)
    with pytest.raises(ValueError, match="Formato"):
        export_records(records, "json")


def test_abnt_truecases_uppercase_records() -> None:
    record = {
        "author": "NATALI GONCALVES GOMES",
        "title": "A ALFABETIZACAO NO CONTEXTO PANDEMICO: UM OLHAR",
        "year": 2024,
        "degree": "Mestrado",
        "program": "ENSINO",
        "institution": "UNIVERSIDADE FEDERAL DO PAMPA",
    }
    text = abnt_reference(record)
    assert text.startswith("GOMES, Natali Gonçalves.")
    assert "A alfabetização no contexto pandêmico: um olhar." in text
    assert "Universidade Federal do Pampa" in text


def test_abnt_truecases_short_uppercase_program() -> None:
    record = {
        "author": "SILVA, MARIA",
        "title": "EDUCACAO INCLUSIVA NA ESCOLA PUBLICAAAA",
        "year": 2024,
        "degree": "Mestrado",
        "program": "DIREITO",
        "institution": "UNIVERSIDADE FEDERAL DE MINAS GERAIS",
    }
    text = abnt_reference(record)
    assert "Dissertação (Mestrado em Direito)" in text
    assert "DIREITO" not in text


def test_csv_uses_truecased_text() -> None:
    body = export_csv(
        [
            {
                "id": "capes:9",
                "author": "NATALI GONCALVES GOMES",
                "title": "A ALFABETIZACAO NO CONTEXTO PANDEMICO",
                "abstract": "ESTE TRABALHO ANALISA A EDUCACAO.",
                "great_area": "CIENCIAS HUMANAS",
            }
        ]
    )
    assert "Natali Gonçalves Gomes" in body
    assert "A alfabetização no contexto pandêmico" in body
    assert "Este trabalho analisa a educação." in body
    assert "Ciências Humanas" in body
