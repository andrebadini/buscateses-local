"""End-to-end tests for tools/dataset/normalize.py with synthetic CSVs."""

from __future__ import annotations

import csv
from pathlib import Path

import common
import normalize
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

LEGACY_HEADER = [
    "AnoBase",
    "CodigoPrograma",
    "Regiao",
    "Uf",
    "SiglaIes",
    "NomeIes",
    "NomePrograma",
    "GrandeAreaCodigo",
    "GrandeAreaDescricao",
    "AreaConhecimentoCodigo",
    "AreaConhecimento",
    "AreaAvaliacao",
    "DocumentoDiscente",
    "Autor",
    "TituloTese",
    "Nivel",
    "DataDefesa",
    "PalavrasChave",
    "Volume",
    "NumeroPaginas",
    "BibliotecaDepositaria",
    "Idioma",
    "ResumoTese",
    "LinhaPesquisa",
    "URLTextoCompleto",
    "DocumentoOrientador_1",
    "Orientador_1",
    "DocumentoOrientador_2",
    "Orientador_2",
    "DocumentoOrientador_3",
    "Orientador_3",
    "DocumentoOrientador_4",
    "Orientador_4",
    "DocumentoCoOrientador_1",
    "CoOrientador_1",
    "DocumentoCoOrientador_2",
    "CoOrientador_2",
    "DocumentoCoOrientador_3",
    "CoOrientador_3",
    "DocumentoCoOrientador_4",
    "CoOrientador_4",
]

MODERN_HEADER = [
    "AN_BASE",
    "CD_PROGRAMA",
    "NM_PROGRAMA",
    "SG_ENTIDADE_ENSINO",
    "NM_ENTIDADE_ENSINO",
    "ID_ADD_PRODUCAO_INTELECTUAL",
    "ID_PRODUCAO_INTELECTUAL",
    "NM_PRODUCAO",
    "ID_SUBTIPO_PRODUCAO",
    "NM_SUBTIPO_PRODUCAO",
    "ID_AREA_CONCENTRACAO",
    "NM_AREA_CONCENTRACAO",
    "ID_LINHA_PESQUISA",
    "NM_LINHA_PESQUISA",
    "ID_PROJETO",
    "NM_PROJETO",
    "DH_INICIO_AREA_CONC",
    "DH_FIM_AREA_CONC",
    "DH_INICIO_LINHA",
    "DH_FIM_LINHA",
    "DT_TITULACAO",
    "DS_PALAVRA_CHAVE",
    "DS_ABSTRACT",
    "DS_KEYWORD",
    "IN_TRABALHO_MESMA_AREA",
    "NM_TP_VINCULO",
    "IN_ORIENT_PARTICIPOU_BANCA",
    "DS_BIBLIOTECA_DEPOSITARIA",
    "ID_TP_EXPECTATIVA_ATUACAO",
    "NM_EXPECTATIVA_ATUACAO",
    "ID_PESSOA_DISCENTE",
    "NM_DISCENTE",
    "DT_MATRICULA",
    "ID_GRAU_ACADEMICO",
    "NM_GRAU_ACADEMICO",
    "NM_ORIENTADOR",
    "DS_CATEGORIA_ORIENTADOR",
    "NM_CATEGORIA_DOCENTE",
    "NM_REGIAO",
    "SG_UF_IES",
    "NM_UF_IES",
    "CD_GRANDE_AREA_CONHECIMENTO",
    "NM_GRANDE_AREA_CONHECIMENTO",
    "CD_AREA_CONHECIMENTO",
    "NM_AREA_CONHECIMENTO",
    "CD_SUBAREA_CONHECIMENTO",
    "NM_SUBAREA_CONHECIMENTO",
    "CD_ESPECIALIDADE",
    "NM_ESPECIALIDADE",
    "NM_AREA_AVALIACAO",
    "NR_VOLUME",
    "NR_PAGINAS",
    "NM_IDIOMA",
    "DS_RESUMO",
    "DS_URL_TEXTO_COMPLETO",
    "ID_PESSOA_ORIENTADOR",
]

SENTINELS = {
    "SEGREDO-DOC-111",
    "SEGREDO-DOC-333",
    "SEGREDO-ORI-1",
    "SEGREDO-CO-1",
    "SEGREDO-VINCULO",
    "SEGREDO-PESSOA",
    "SEGREDO-MATRICULA",
    "SEGREDO-EXPECTATIVA",
    "SEGREDO-CATEGORIA",
    "SEGREDO-ORIENTADOR-ID",
}


def _write_csv(path: Path, header: list[str], rows: list[dict[str, str]], encoding: str) -> None:
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(
            handle,
            delimiter=";",
            quotechar='"',
            lineterminator="\r\n",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writerow(header)
        for row in rows:
            writer.writerow([row.get(column, "") for column in header])


def _legacy_row(**overrides: str) -> dict[str, str]:
    row = {
        "AnoBase": "2012",
        "CodigoPrograma": "PROG1",
        "Regiao": "NORTE",
        "Uf": "RO",
        "SiglaIes": "UNIR",
        "NomeIes": "UNIVERSIDADE FEDERAL DE RONDÔNIA",
        "NomePrograma": "Biologia Experimental",
        "GrandeAreaCodigo": "2",
        "GrandeAreaDescricao": "CIÊNCIAS BIOLÓGICAS",
        "AreaConhecimentoCodigo": "213",
        "AreaConhecimento": "PARASITOLOGIA",
        "AreaAvaliacao": "CIÊNCIAS BIOLÓGICAS III",
        "DocumentoDiscente": "SEGREDO-DOC-111",
        "Autor": "José da Silva",
        "TituloTese": 'Ação "especial"; com acentos',
        "Nivel": "Mestrado",
        "DataDefesa": "01FEB2012",
        "PalavrasChave": "Cultura  de  tecidos; estádio  de  desenvolvimento",
        "Volume": "2",
        "NumeroPaginas": "62",
        "BibliotecaDepositaria": "BC",
        "Idioma": "Português",
        "ResumoTese": "Resumo\r\ncom quebra",
        "LinhaPesquisa": "Linha A",
        "URLTextoCompleto": "http://exemplo.br/tese",
        "DocumentoOrientador_1": "SEGREDO-ORI-1",
        "Orientador_1": "Maria Souza",
        "DocumentoOrientador_2": "SEGREDO-ORI-2",
        "Orientador_2": "",
        "DocumentoOrientador_3": "",
        "Orientador_3": "",
        "DocumentoOrientador_4": "",
        "Orientador_4": "",
        "DocumentoCoOrientador_1": "SEGREDO-CO-1",
        "CoOrientador_1": "João Lima",
        "DocumentoCoOrientador_2": "",
        "CoOrientador_2": "",
        "DocumentoCoOrientador_3": "",
        "CoOrientador_3": "",
        "DocumentoCoOrientador_4": "",
        "CoOrientador_4": "",
    }
    row.update(overrides)
    return row


def _modern_row(**overrides: str) -> dict[str, str]:
    row = {
        "AN_BASE": "2024",
        "CD_PROGRAMA": "PROG9",
        "NM_PROGRAMA": "Educação",
        "SG_ENTIDADE_ENSINO": "UNB",
        "NM_ENTIDADE_ENSINO": "Universidade de Brasília",
        "ID_ADD_PRODUCAO_INTELECTUAL": "111",
        "ID_PRODUCAO_INTELECTUAL": "999",
        "NM_PRODUCAO": "Título Moderno 111",
        "ID_SUBTIPO_PRODUCAO": "1",
        "NM_SUBTIPO_PRODUCAO": "DISSERTAÇÃO",
        "ID_AREA_CONCENTRACAO": "1",
        "NM_AREA_CONCENTRACAO": "Área de concentração",
        "ID_LINHA_PESQUISA": "1",
        "NM_LINHA_PESQUISA": "Linha moderna",
        "ID_PROJETO": "",
        "NM_PROJETO": "",
        "DH_INICIO_AREA_CONC": "",
        "DH_FIM_AREA_CONC": "",
        "DH_INICIO_LINHA": "",
        "DH_FIM_LINHA": "",
        "DT_TITULACAO": "27MAR2024:00:00:00",
        "DS_PALAVRA_CHAVE": "INCLUSÃO;EDUCAÇÃO",
        "DS_ABSTRACT": "Abstract text",
        "DS_KEYWORD": "INCLUSION",
        "IN_TRABALHO_MESMA_AREA": "",
        "NM_TP_VINCULO": "SEGREDO-VINCULO",
        "IN_ORIENT_PARTICIPOU_BANCA": "",
        "DS_BIBLIOTECA_DEPOSITARIA": "BCE",
        "ID_TP_EXPECTATIVA_ATUACAO": "SEGREDO-EXPECTATIVA",
        "NM_EXPECTATIVA_ATUACAO": "SEGREDO-EXPECTATIVA",
        "ID_PESSOA_DISCENTE": "SEGREDO-PESSOA",
        "NM_DISCENTE": "Autora Nova",
        "DT_MATRICULA": "SEGREDO-MATRICULA",
        "ID_GRAU_ACADEMICO": "1",
        "NM_GRAU_ACADEMICO": "MESTRADO",
        "NM_ORIENTADOR": "Orientadora X",
        "DS_CATEGORIA_ORIENTADOR": "SEGREDO-CATEGORIA",
        "NM_CATEGORIA_DOCENTE": "SEGREDO-CATEGORIA",
        "NM_REGIAO": "CENTRO-OESTE",
        "SG_UF_IES": "DF",
        "NM_UF_IES": "DISTRITO FEDERAL",
        "CD_GRANDE_AREA_CONHECIMENTO": "1",
        "NM_GRANDE_AREA_CONHECIMENTO": "CIÊNCIAS HUMANAS",
        "CD_AREA_CONHECIMENTO": "1",
        "NM_AREA_CONHECIMENTO": "EDUCAÇÃO",
        "CD_SUBAREA_CONHECIMENTO": "",
        "NM_SUBAREA_CONHECIMENTO": "",
        "CD_ESPECIALIDADE": "",
        "NM_ESPECIALIDADE": "",
        "NM_AREA_AVALIACAO": "EDUCAÇÃO",
        "NR_VOLUME": "1",
        "NR_PAGINAS": "120",
        "NM_IDIOMA": "PORTUGUES",
        "DS_RESUMO": "Resumo moderno",
        "DS_URL_TEXTO_COMPLETO": "https://exemplo.br/111",
        "ID_PESSOA_ORIENTADOR": "SEGREDO-ORIENTADOR-ID",
    }
    row.update(overrides)
    return row


def _read_parts(out_dir: Path) -> list[Path]:
    return sorted(out_dir.glob("part-*.parquet"))


def test_normalize_end_to_end(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    out = tmp_path / "metadata"
    raw.mkdir()

    _write_csv(
        raw / "dados_2012.csv",
        LEGACY_HEADER,
        [
            _legacy_row(),
            _legacy_row(
                Autor="",
                TituloTese="Segundo trabalho",
                Nivel="Profissionalizante",
                DataDefesa="01/jun/12",
                PalavrasChave="alfa, beta",
                NumeroPaginas="0",
                URLTextoCompleto="ftp://invalido",
                Idioma="Inglês",
                ResumoTese="",
            ),
            _legacy_row(
                TituloTese="",
                DocumentoDiscente="SEGREDO-DOC-333",
            ),
        ],
        encoding="cp1252",
    )
    _write_csv(
        raw / "dados_1990.csv",
        LEGACY_HEADER,
        [
            _legacy_row(
                AnoBase="1990",
                CodigoPrograma="PROG0",
                Autor="Ana",
                TituloTese="Trabalho antigo com acentuação",
                Nivel="Doutorado",
                DataDefesa="01/09/1990",
                NumeroPaginas="100",
                URLTextoCompleto="https://exemplo.br/ok",
                DocumentoDiscente="",
                DocumentoOrientador_1="",
                Orientador_1="",
                DocumentoCoOrientador_1="",
                CoOrientador_1="",
            )
        ],
        encoding="utf-8",
    )
    _write_csv(
        raw / "br-capes-btd-2024-01-01.csv",
        MODERN_HEADER,
        [
            _modern_row(NM_DISCENTE=""),
            _modern_row(
                ID_ADD_PRODUCAO_INTELECTUAL="222",
                NM_PRODUCAO="Duplicado 222",
                NM_DISCENTE="Autor Antigo",
                NM_GRAU_ACADEMICO="DOUTORADO",
                DT_TITULACAO="01/01/2024",
                DS_PALAVRA_CHAVE="A, B",
                DS_RESUMO="Resumo 222",
                DS_URL_TEXTO_COMPLETO="https://exemplo.br/222",
            ),
        ],
        encoding="cp1252",
    )
    _write_csv(
        raw / "br-capes-btd-2025-01-01.csv",
        MODERN_HEADER,
        [
            _modern_row(NM_DISCENTE="Autora Nova"),
            _modern_row(
                ID_ADD_PRODUCAO_INTELECTUAL="222",
                NM_PRODUCAO="Duplicado 222",
                NM_DISCENTE="Autor Novo",
                NM_GRAU_ACADEMICO="DOUTORADO",
                DT_TITULACAO="01/01/2024",
                DS_PALAVRA_CHAVE="A, B",
                DS_RESUMO="Resumo 222",
                DS_URL_TEXTO_COMPLETO="https://exemplo.br/222-novo",
            ),
            _modern_row(
                ID_ADD_PRODUCAO_INTELECTUAL="333",
                NM_PRODUCAO="Terceiro",
                NM_DISCENTE="Pessoa 333",
                NM_GRAU_ACADEMICO="",
                NM_SUBTIPO_PRODUCAO="TESE",
                DT_TITULACAO="01JAN2024",
                DS_URL_TEXTO_COMPLETO="https://ok.br/333",
            ),
        ],
        encoding="cp1252",
    )

    report = normalize.normalize(raw, out, rows_per_part=2)

    parts = _read_parts(out)
    assert len(parts) == 3
    for part in parts:
        table = pq.read_table(part)
        assert table.schema.names == common.METADATA_SCHEMA.names
        assert table.schema.field("year").type == pa.int16()
        assert table.schema.field("pages").type == pa.int32()
        assert table.num_rows <= 2

    table = pa.concat_tables([pq.read_table(part) for part in parts])
    records = {row["id"]: row for row in table.to_pylist()}
    assert len(records) == 6
    years = table.column("year").to_pylist()
    assert years == sorted(years)

    legacy_id = common.legacy_id(
        2012, "PROG1", "José da Silva", 'Ação "especial"; com acentos'
    )
    legacy = records[legacy_id]
    assert legacy["year"] == 2012
    assert legacy["title"] == 'Ação "especial"; com acentos'
    assert legacy["author"] == "José da Silva"
    assert legacy["advisors"] == "Maria Souza; João Lima (coorientador)"
    assert legacy["degree"] == "Mestrado"
    assert legacy["institution"] == "UNIVERSIDADE FEDERAL DE RONDÔNIA"
    assert legacy["keywords"] == "Cultura de tecidos; estádio de desenvolvimento"
    assert legacy["abstract"] == "Resumo com quebra"
    assert legacy["defense_date"] == "2012-02-01"
    assert legacy["pages"] == 62
    assert legacy["url"] == "http://exemplo.br/tese"
    assert legacy["source"] == "dados_2012.csv"
    assert legacy["concentration_area"] is None
    assert legacy["keywords_en"] is None

    legacy_b_id = common.legacy_id(2012, "PROG1", None, "Segundo trabalho")
    legacy_b = records[legacy_b_id]
    assert legacy_b["degree"] == "Mestrado Profissional"
    assert legacy_b["defense_date"] == "2012-06-01"
    assert legacy_b["keywords"] == "alfa; beta"
    assert legacy_b["pages"] == 0
    assert legacy_b["url"] is None
    assert legacy_b["author"] is None

    old_id = common.legacy_id(1990, "PROG0", "Ana", "Trabalho antigo com acentuação")
    old = records[old_id]
    assert old["defense_date"] == "1990-09-01"
    assert old["degree"] == "Doutorado"
    assert old["source"] == "dados_1990.csv"

    modern = records["capes:111"]
    assert modern["author"] == "Autora Nova"
    assert modern["advisors"] == "Orientadora X"
    assert modern["degree"] == "Mestrado"
    assert modern["defense_date"] == "2024-03-27"
    assert modern["keywords"] == "INCLUSÃO; EDUCAÇÃO"
    assert modern["keywords_en"] == "INCLUSION"
    assert modern["abstract"] == "Resumo moderno"
    assert modern["abstract_en"] == "Abstract text"
    assert modern["concentration_area"] == "Área de concentração"
    assert modern["pages"] == 120
    assert modern["url"] == "https://exemplo.br/111"
    assert modern["source"] == "br-capes-btd-2025-01-01.csv"

    duplicate = records["capes:222"]
    assert duplicate["author"] == "Autor Novo"
    assert duplicate["url"] == "https://exemplo.br/222-novo"
    # Without ';' and with ',' modern keywords are split on commas.
    assert duplicate["keywords"] == "A; B"

    fallback = records["capes:333"]
    assert fallback["degree"] == "Doutorado"
    assert fallback["defense_date"] == "2024-01-01"

    # No forbidden column name or value may reach the output.
    for part in parts:
        part_table = pq.read_table(part)
        for name in part_table.schema.names:
            assert not common.is_forbidden_column(name)
        values: list[str] = []
        for name in part_table.schema.names:
            if pa.types.is_string(part_table.schema.field(name).type):
                values.extend(value for value in part_table.column(name).to_pylist() if value)
        for sentinel in SENTINELS:
            assert not any(sentinel in value for value in values)

    assert report["rows_read"] == 9
    assert report["discarded_no_title_or_id"] == 1
    assert report["rows_staged"] == 8
    assert report["duplicates_removed"] == 2
    assert report["total"] == 6
    assert report["by_year"] == {"1990": 1, "2012": 2, "2024": 3}
    assert report["files"]["dados_2012.csv"]["encoding"] == "cp1252"
    assert report["files"]["dados_1990.csv"]["encoding"] == "utf-8"
    assert report["null_percent"]["author"] > 0
    assert (tmp_path / "normalize_report.json").is_file()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("01FEB2012", "2012-02-01"),
        ("27MAR2024:00:00:00", "2024-03-27"),
        ("01/jun/12", "2012-06-01"),
        ("01/09/1987", "1987-09-01"),
        ("2024-03-27", "2024-03-27"),
        ("31/02/2020", None),
        ("", None),
        ("NAO INFORMADO", None),
    ],
)
def test_normalize_date(raw: str, expected: str | None) -> None:
    assert common.normalize_date(raw) == expected


def test_normalize_keywords_separators() -> None:
    assert (
        common.normalize_keywords("CICLOTOMIA     EXTENSOES", legacy=True)
        == "CICLOTOMIA; EXTENSOES"
    )
    assert (
        common.normalize_keywords("CICLOTOMIA     EXTENSOES", legacy=False)
        == "CICLOTOMIA EXTENSOES"
    )
    assert common.normalize_keywords("a; b, c", legacy=True) == "a; b, c"


def test_normalize_url_rejects_malformed_values() -> None:
    assert common.normalize_url("https://exemplo.br/tese") == "https://exemplo.br/tese"
    assert common.normalize_url("HTTP://EXEMPLO.BR") == "HTTP://EXEMPLO.BR"
    assert common.normalize_url("http://[invalido") is None
    assert common.normalize_url("www.exemplo.br") is None
    assert common.normalize_url("https://") is None
    assert common.normalize_url("https://exemplo.br/a b") is None
    assert common.normalize_url("NAO INFORMADO") is None


def test_normalize_pages_rejects_non_int32_values() -> None:
    assert common.normalize_pages("0") == 0
    assert common.normalize_pages(" 120 ") == 120
    assert common.normalize_pages("9999999999") is None
    assert common.normalize_pages("2147483648") is None
    assert common.normalize_pages("2147483647") == 2_147_483_647
    assert common.normalize_pages("-1") is None
    assert common.normalize_pages("doze") is None
