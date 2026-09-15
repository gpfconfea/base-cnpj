"""Filtros da busca por CNAE que servem a preparacao de evento no FTNF.

Capital social, inicio de atividade, CNAEs secundarios e a lista de CNPJs a
excluir entram no mesmo WHERE que a consulta por CNAE ja montava; o que se
garante aqui e a forma do SQL, que precisa continuar indexavel.
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api import Busca, _codigo_cnae, filtrar_cnaes, montar_filtro

CNAES = [
    ("7112000", "Serviços de engenharia"),
    ("4120400", "Construção de edifícios"),
    ("7111100", "Serviços de arquitetura"),
]


def test_filtro_minimo_e_so_o_cnae_principal():
    filtro, parametros = montar_filtro("7112000")

    assert filtro == "cnae_principal = ANY(%(cnaes)s)"
    assert parametros == {"cnaes": ["7112000"]}


def test_varios_cnaes_principais_entram_na_mesma_lista():
    filtro, parametros = montar_filtro(["1011201", "1012101"], inclui_secundario=True)

    assert "cnae_principal = ANY(%(cnaes)s)" in filtro
    assert "cnaes_secundarios && %(cnaes)s" in filtro
    assert parametros["cnaes"] == ["1011201", "1012101"]


def test_busca_aceita_cnae_unico_ou_lista():
    assert Busca(cnae="7112000").cnaes == []
    assert Busca(cnaes=["7112000", "4120400"]).cnae is None


def test_capital_e_inicio_de_atividade_viram_intervalos():
    filtro, parametros = montar_filtro(
        "7112000",
        capital_min=Decimal("1000"),
        capital_max=Decimal("50000"),
        inicio_de=date(2015, 1, 1),
        inicio_ate=date(2020, 12, 31),
    )

    assert "capital_social >= %(capital_min)s" in filtro
    assert "capital_social <= %(capital_max)s" in filtro
    assert "data_inicio_atividade >= %(inicio_de)s" in filtro
    assert "data_inicio_atividade <= %(inicio_ate)s" in filtro
    assert parametros["inicio_de"] == date(2015, 1, 1)


def test_cnaes_secundarios_usam_sobreposicao_que_o_gin_atende():
    filtro, parametros = montar_filtro("7112000", cnaes_secundarios="4120-4/00, 7111100")

    assert "cnaes_secundarios && %(secundarios)s" in filtro
    assert parametros["secundarios"] == ["4120400", "7111100"]


def test_cnaes_secundarios_aceitam_lista_json():
    _, parametros = montar_filtro("7112000", cnaes_secundarios=["4120400"])

    assert parametros["secundarios"] == ["4120400"]


def test_cnpjs_excluidos_saem_limpos_e_invalidos_sao_ignorados():
    filtro, parametros = montar_filtro(
        "7112000", excluir_cnpjs=["00.081.395/0001-02", "abc", "00083334000176"]
    )

    assert "cnpj <> ALL(%(cnpjs_fora)s)" in filtro
    assert parametros["cnpjs_fora"] == ["00081395000102", "00083334000176"]


def test_lista_vazia_de_cnpjs_nao_entra_no_filtro():
    filtro, _ = montar_filtro("7112000", excluir_cnpjs=[])

    assert "cnpjs_fora" not in filtro


def test_excluir_municipios_aceita_texto_e_lista():
    _, por_texto = montar_filtro("7112000", excluir_municipios="9373, 9227")
    _, por_lista = montar_filtro("7112000", excluir_municipios=["9373", "9227"])

    assert por_texto["excluidos"] == por_lista["excluidos"] == ["9373", "9227"]


def test_busca_por_cnae_com_digitos_olha_o_inicio_do_codigo():
    encontrados, total = filtrar_cnaes(CNAES, "711", 50)

    assert [c["codigo"] for c in encontrados] == ["7111100", "7112000"]
    assert total == 2


def test_busca_por_cnae_com_texto_ignora_acento_e_caixa():
    encontrados, _ = filtrar_cnaes(CNAES, "CONSTRUCAO", 50)

    assert [c["codigo"] for c in encontrados] == ["4120400"]


def test_busca_por_cnae_pagina_e_conta_o_total():
    primeira, total = filtrar_cnaes(CNAES, "", 2)
    segunda, _ = filtrar_cnaes(CNAES, "", 2, offset=2)

    assert [c["codigo"] for c in primeira] == ["4120400", "7111100"]
    assert [c["codigo"] for c in segunda] == ["7112000"]
    assert total == 3


def test_cnae_sem_digito_e_rejeitado():
    with pytest.raises(HTTPException) as erro:
        _codigo_cnae("abc")

    assert erro.value.status_code == 400


def test_cnae_mascarado_vira_so_digitos():
    assert _codigo_cnae("7112-0/00") == "7112000"


def test_busca_rejeita_lista_de_exclusao_acima_do_teto():
    with pytest.raises(ValueError):
        Busca(cnae="7112000", excluir_cnpjs=["1"] * 20001)
