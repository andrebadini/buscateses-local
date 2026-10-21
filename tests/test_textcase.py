"""Tests for truecasing CAPES catalogue text (synthetic and real-shaped)."""

from __future__ import annotations

from buscateses.textcase import display_record, is_shouting, name_case, truecase


def test_is_shouting_thresholds() -> None:
    assert is_shouting("DISSERTACAO DE MESTRADO")
    assert is_shouting("ABCDEFGH ij")  # 8/10 uppercase
    assert not is_shouting("ABCDEFG hij")  # 7/10 uppercase
    assert not is_shouting("CURTO")  # fewer than eight letters
    assert not is_shouting("Educação inclusiva")
    assert not is_shouting("")
    assert not is_shouting(None)


def test_truecase_keeps_mixed_case_untouched() -> None:
    text = "Educação inclusiva: desafios & possibilidades"
    assert truecase(text) == text
    assert truecase(None) == ""


def test_truecase_converts_shouting_segment_in_mixed_title() -> None:
    title = (
        "ESTUDO ETNOBOTÂNICO DAS PLANTAS MEDICINAIS NA CULTURA "
        "ÍTALOBRASILEIRA NO RIO GRANDE DO SUL. Um modelo para o cultivo"
    )
    assert truecase(title) == (
        "Estudo etnobotânico das plantas medicinais na cultura "
        "ítalobrasileira no Rio Grande do Sul. Um modelo para o cultivo"
    )


def test_truecase_converts_each_shouting_segment_and_preserves_normal_ones() -> None:
    text = "UM ESTUDO DE CASO - resultados parciais e novas perguntas"
    assert truecase(text) == "Um estudo de caso - resultados parciais e novas perguntas"
    text = "DADOS PRELIMINARES: um recorte da pesquisa"
    assert truecase(text) == "Dados preliminares: um recorte da pesquisa"


def test_truecase_ignores_short_shouting_segment_in_mixed_text() -> None:
    text = "Breve descrição. METODOLOGIA"
    assert truecase(text) == text


def test_truecase_2024_title() -> None:
    title = (
        "A ALFABETIZAÇÃO NO CONTEXTO PANDÊMICO E PÓS PANDÊMICO: "
        "UM OLHAR A LUZ DA INOVAÇÃO PEDAGÓGICA"
    )
    assert truecase(title) == (
        "A alfabetização no contexto pandêmico e pós pandêmico: "
        "um olhar a luz da inovação pedagógica"
    )


def test_truecase_restores_accents_in_legacy_text() -> None:
    title = "COMPRESSAO PARALELA AS FIBRAS EM PECAS DE MADEIRA"
    assert truecase(title) == "Compressão paralela as fibras em peças de madeira"
    abstract = (
        "ESTE TRABALHO FOI DESENVOLVIDO COM BASE EXPERIMENTAL. "
        "SAO INVESTIGADAS AS INFLUENCIAS DO TAMANHO."
    )
    assert truecase(abstract) == (
        "Este trabalho foi desenvolvido com base experimental. "
        "São investigadas as influências do tamanho."
    )


def test_truecase_does_not_restore_accents_when_text_already_has_them() -> None:
    title = "CONTRIBUIÇÃO AO ESTUDO DE CIMENTOS SUPERSULFATADOS: FORMULAÇÃO E MECANISMOS"
    assert truecase(title) == (
        "Contribuição ao estudo de cimentos supersulfatados: formulação e mecanismos"
    )


def test_truecase_keeps_acronyms_numbers_and_roman_numerals() -> None:
    text = "SUS UFMT BNCC COVID-19 H1N1 AG2MOO4 CAPITULO II DA LEI XXI"
    assert truecase(text) == (
        "SUS UFMT BNCC COVID-19 H1N1 AG2MOO4 capítulo II da lei XXI"
    )


def test_truecase_preserves_highlight_markers() -> None:
    text = "RESUMO [[EDUCACAO INCLUSIVA]] E [[SAUDE]]"
    assert truecase(text) == "Resumo [[educação inclusiva]] e [[saúde]]"


def test_truecase_capitalize_first_false() -> None:
    text = "SEGUNDO O AUTOR [[EDUCACAO]]"
    assert truecase(text, capitalize_first=False) == "segundo o autor [[educação]]"


def test_truecase_capitalizes_after_terminal_but_not_after_colon() -> None:
    text = "ESTUDO DE CASO: METODOLOGIA E RESULTADOS. OS DADOS SAO NOVOS"
    assert truecase(text) == (
        "Estudo de caso: metodologia e resultados. Os dados são novos"
    )


def test_truecase_does_not_capitalize_after_decimal_point() -> None:
    text = "O VALOR FOI DE 1.5 E O TOTAL DE 2.0. OS DADOS SAO NOVOS"
    assert truecase(text) == (
        "O valor foi de 1.5 e o total de 2.0. Os dados são novos"
    )


def test_truecase_does_not_capitalize_after_abbreviation_without_space() -> None:
    text = "A PARTIR DA LEI N.°10.436 DE 24 ABRIL DE 2002"
    assert truecase(text) == "A partir da lei n.°10.436 de 24 abril de 2002"
    text = "AMOSTRA (CONTROLE, L.) E OUTRA"
    assert truecase(text) == "Amostra (controle, l.) e outra"


def test_truecase_capitalizes_proper_names() -> None:
    assert truecase("O BRASIL E O MATO GROSSO") == "O Brasil e o Mato Grosso"


def test_truecase_capitalizes_compound_toponyms() -> None:
    cases = {
        "ESTADO DE SAO PAULO": "Estado de São Paulo",
        "RIO GRANDE DO SUL": "Rio Grande do Sul",
        "MATO GROSSO": "Mato Grosso",
        "SANTA CATARINA": "Santa Catarina",
        "ESPIRITO SANTO": "Espírito Santo",
        "MINAS GERAIS": "Minas Gerais",
        "BELO HORIZONTE": "Belo Horizonte",
        "PORTO ALEGRE": "Porto Alegre",
        "CAMPO GRANDE": "Campo Grande",
        "FOZ DO IGUACU": "Foz do Iguaçu",
        "JUIZ DE FORA": "Juiz de Fora",
        "NOVA IGUACU": "Nova Iguaçu",
        "PONTA GROSSA": "Ponta Grossa",
        "RIO DE JANEIRO": "Rio de Janeiro",
    }
    for source, expected in cases.items():
        assert truecase(source) == expected


def test_truecase_keeps_common_words_lowercase() -> None:
    assert truecase("OS DADOS SAO ANALISADOS") == "Os dados são analisados"
    assert truecase("O RIO PRINCIPAL DO ESTADO") == "O rio principal do estado"
    assert truecase("O CAMPO DE ESTUDO") == "O campo de estudo"
    assert truecase("A NOVA ANALISE DOS DADOS") == "A nova análise dos dados"


def test_name_case_particles_apostrophes_and_separators() -> None:
    assert name_case("MARIA DA SILVA E SOUZA; JOSE D'AVILA") == (
        "Maria da Silva e Souza; José d'Avila"
    )
    assert name_case("DE SOUZA, ANTONIO") == "De Souza, Antonio"


def test_name_case_institutions_and_acronyms() -> None:
    assert name_case("UNIVERSIDADE FEDERAL DE MATO GROSSO") == (
        "Universidade Federal de Mato Grosso"
    )
    assert name_case("PROGRAMA DE POS-GRADUACAO EM EDUCACAO DA UFMT") == (
        "Programa de Pós-Graduação em Educação da UFMT"
    )
    assert name_case("SUS") == "SUS"


def test_name_case_restores_accents_and_keeps_annotations() -> None:
    assert name_case("MARIA DA SILVA; JOSE D'AVILA (coorientador)") == (
        "Maria da Silva; José d'Avila (coorientador)"
    )


def test_name_case_leaves_non_shouting_text_untouched() -> None:
    text = "Universidade Federal de Minas Gerais"
    assert name_case(text) == text


def test_name_case_converts_short_uppercase_values() -> None:
    assert name_case("DIREITO") == "Direito"
    assert name_case("SUDESTE") == "Sudeste"
    assert name_case("EMPRESA") == "Empresa"
    assert name_case("SAUDE") == "Saúde"
    assert name_case("DIREITO; EMPRESA") == "Direito; Empresa"


def test_name_case_keeps_known_acronyms_and_state_codes() -> None:
    assert name_case("UFMG") == "UFMG"
    assert name_case("USP") == "USP"
    assert name_case("SUS") == "SUS"
    assert name_case("SP") == "SP"
    assert name_case("MG") == "MG"
    assert name_case("PUC/SP") == "PUC/SP"


def test_name_case_does_not_convert_values_with_digits() -> None:
    assert name_case("H1N1") == "H1N1"
    assert name_case("COVID-19") == "COVID-19"


def test_short_keywords_are_converted_item_by_item() -> None:
    displayed = display_record(
        {"keywords": "EMPRESA; DIREITO; UFMG; SP; SAUDE; COVID-19"}
    )
    assert displayed["keywords"] == "Empresa; Direito; UFMG; SP; Saúde; COVID-19"


def test_display_record_forces_short_fields_when_record_is_shouting() -> None:
    displayed = display_record(
        {
            "title": "ESTUDO SOBRE A EDUCACAO INCLUSIVA NO BRASIL",
            "abstract": "ESTE TRABALHO ANALISA A EDUCACAO INCLUSIVA.",
            "program": "DIREITO",
            "knowledge_area": "DIREITO",
            "region": "SUDESTE",
            "keywords": "EMPRESA",
        }
    )
    assert displayed["program"] == "Direito"
    assert displayed["knowledge_area"] == "Direito"
    assert displayed["region"] == "Sudeste"
    assert displayed["keywords"] == "Empresa"


def test_display_record_maps_only_display_fields() -> None:
    record = {
        "id": "CAPES:1",
        "url": "HTTPS://EXAMPLE.ORG/X",
        "title": "A ALFABETIZACAO NO CONTEXTO PANDEMICO: UM OLHAR",
        "abstract": "ESTE TRABALHO ANALISA A EDUCACAO. OS RESULTADOS SAO POSITIVOS.",
        "abstract_en": "THIS IS UNCHANGED",
        "keywords": "EDUCACAO INCLUSIVA; ALFABETIZACAO",
        "keywords_en": "education; literacy",
        "author": "NATALI GONCALVES GOMES",
        "advisors": "MARIA DA SILVA; JOSE D'AVILA (coorientador)",
        "institution": "UNIVERSIDADE FEDERAL DO PAMPA",
        "program": "POS-GRADUACAO EM EDUCACAO",
        "great_area": "CIENCIAS HUMANAS",
        "knowledge_area": "EDUCACAO",
        "evaluation_area": "EDUCACAO",
        "concentration_area": "EDUCACAO INCLUSIVA",
        "research_line": "POLITICAS PUBLICAS",
        "pages": 120,
    }
    displayed = display_record(record)

    assert displayed["title"] == "A alfabetização no contexto pandêmico: um olhar"
    assert displayed["abstract"] == (
        "Este trabalho analisa a educação. Os resultados são positivos."
    )
    assert displayed["keywords"] == "Educação inclusiva; Alfabetização"
    assert displayed["author"] == "Natali Gonçalves Gomes"
    assert displayed["advisors"] == "Maria da Silva; José d'Avila (coorientador)"
    assert displayed["institution"] == "Universidade Federal do Pampa"
    assert displayed["program"] == "Pós-Graduação em Educação"
    assert displayed["great_area"] == "Ciências Humanas"
    assert displayed["knowledge_area"] == "Educação"
    assert displayed["evaluation_area"] == "Educação"
    assert displayed["concentration_area"] == "Educação Inclusiva"
    assert displayed["research_line"] == "Políticas Públicas"

    assert displayed["abstract_en"] == "THIS IS UNCHANGED"
    assert displayed["keywords_en"] == "education; literacy"
    assert displayed["url"] == "HTTPS://EXAMPLE.ORG/X"
    assert displayed["id"] == "CAPES:1"
    assert displayed["pages"] == 120
    assert record["title"].isupper()  # the input mapping is not mutated


def test_truecase_does_not_capitalize_after_short_abbreviation() -> None:
    assert truecase("ESPECIES SPP. OCORRENTES NA REGIAO") == (
        "Espécies spp. ocorrentes na região"
    )
    assert truecase("ATIV. INFLAM.") == "Ativ. inflam."


def test_truecase_capitalizes_after_short_common_sentence_words() -> None:
    assert truecase("O FIM. OUTRO ESTUDO") == "O fim. Outro estudo"
    assert truecase("A LEI. OUTRO ESTUDO") == "A lei. Outro estudo"


def test_truecase_keeps_short_parenthetical_acronyms() -> None:
    assert truecase("AMOSTRA (LAC) E (TDAH) ANALISADA") == (
        "Amostra (LAC) e (TDAH) analisada"
    )


def test_pronoun_se_is_not_treated_as_sergipe() -> None:
    text = "A PESQUISA CONCENTRA-SE NA TEMÁTICA. SE A CRIANÇA BRINCA, ELA SE COMUNICA."
    assert truecase(text) == (
        "A pesquisa concentra-se na temática. Se a criança brinca, ela se comunica."
    )


def test_sergipe_code_kept_in_place_context() -> None:
    converted = truecase("ESTUDO REALIZADO NA UFS/SE, EM ARACAJU - SE E EM SERGIPE (SE).")
    assert "UFS/SE" in converted
    assert "Aracaju - SE" in converted
    assert "(SE)" in converted


def test_institution_acronyms_stay_uppercase() -> None:
    converted = truecase("PESQUISA DESENVOLVIDA NA UFF E NA UNISINOS SOBRE O RIO E O MAR.")
    assert "UFF" in converted and "UNISINOS" in converted
    assert " rio " in converted and " mar." in converted
