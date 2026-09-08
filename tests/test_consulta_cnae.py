"""Sorteio e exclusao de municipio na consulta por CNAE.

O consumidor desta rota monta grupo de controle: pede N empresas de um CNAE numa
UF, tirando os municipios onde a fiscalizacao passou. Paginar por CNPJ nao serve
para isso, porque CNPJ cresce com o tempo de registro e cortar no limite entrega
sempre as empresas mais antigas.
"""
from app.api import clausula_de_ordem, municipios_excluidos


def test_sem_amostra_a_ordem_e_o_cnpj_e_pagina():
    clausula = clausula_de_ordem(None)

    assert "ORDER BY cnpj" in clausula
    assert "OFFSET" in clausula


def test_com_amostra_a_ordem_e_sorteada_pela_semente():
    clausula = clausula_de_ordem(50)

    assert "md5(cnpj || %(semente)s)" in clausula
    assert "OFFSET" not in clausula


def test_lista_de_municipios_vem_separada_por_virgula():
    assert municipios_excluidos("9373,9227") == ["9373", "9227"]


def test_espaco_e_campo_vazio_nao_viram_municipio():
    assert municipios_excluidos(" 9373 , ,9227,") == ["9373", "9227"]


def test_sem_parametro_nao_ha_exclusao():
    assert municipios_excluidos(None) == []
    assert municipios_excluidos("") == []
