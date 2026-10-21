from __future__ import annotations

from pathlib import Path

import enrich_sucupira

PAGE = """
<span id="ies">UNIVERSIDADE FEDERAL X</span>
<span id="programa">EDUCAÇÃO (12001015009P1)</span>
<span id="autor">ANA LIMA</span>
<p><span id="resumo">Os dist&uacute;rbios
  estudados aqui.</span></p>
<span id="palavras">Metais;Exposi&ccedil;&atilde;o</span>
<span id="abstract">An abstract.</span>
<span id="keyword">Metals</span>
<span id="idioma">PORTUGUES</span>
<span id="biblioteca">BIBLIOTECA CENTRAL</span>
<span id="area">SAÚDE</span>
<span id="linha">AMBIENTE</span>
<span id="projeto">Descrição longa do projeto que não deve ser guardada.</span>
<span id="orientador">MARIA SOUZA</span>
<span id="tp_vinc">Colaborador</span>
"""


def test_parse_detail_keeps_only_public_schema_fields() -> None:
    detail = enrich_sucupira.parse_detail(PAGE)
    assert detail == {
        "resumo": "Os distúrbios estudados aqui.",
        "palavras": "Metais;Exposição",
        "abstract": "An abstract.",
        "keyword": "Metals",
        "idioma": "PORTUGUES",
        "biblioteca": "BIBLIOTECA CENTRAL",
        "area_concentracao": "SAÚDE",
        "linha_pesquisa": "AMBIENTE",
        "codigo_programa": "12001015009P1",
        "orientadores": ["MARIA SOUZA"],
    }


def test_detail_cache_round_trip(tmp_path: Path) -> None:
    path = enrich_sucupira.detail_path(tmp_path, 2025, "42")
    enrich_sucupira._write(path, "42", {"resumo": "Texto"})
    assert enrich_sucupira.load_detail(tmp_path, 2025, "42") == {"resumo": "Texto"}
    assert enrich_sucupira.load_detail(tmp_path, 2025, "43") is None
    assert enrich_sucupira.load_detail(tmp_path, None, "42") is None
    enrich_sucupira._write(enrich_sucupira.detail_path(tmp_path, 2025, "44"), "44", None)
    assert enrich_sucupira.load_detail(tmp_path, 2025, "44") == {}
