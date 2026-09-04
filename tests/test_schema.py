from datetime import date
from decimal import Decimal

import pytest

from app.schema import COLUNAS, linha

REGISTRO = {
    "cnpj": "99017782000139",
    "razao_social": "SOCIEDADE UNIAO DE ARTISTAS",
    "nome_fantasia": "SOCIEDADE UNIAO DE ARTISTAS",
    "situacao_cadastral": "Baixada",
    "data_situacao_cadastral": "2015-02-09",
    "matriz_filial": "Matriz",
    "data_inicio_atividade": "1984-02-20",
    "cnae_principal": "9493600",
    "cnaes_secundarios": ["9499500"],
    "cnaes": [
        {"codigo": "9493600", "descricao": "Atividades de organizacoes ligadas a cultura", "is_principal": True},
        {"codigo": "9499500", "descricao": "Atividades associativas nao especificadas", "is_principal": False},
    ],
    "natureza_juridica": "Associacao Privada",
    "tipo_logradouro": "PRACA",
    "logradouro": "VIGARIO ANTONIO JOAQUIM",
    "numero": "06",
    "complemento": "",
    "bairro": "CENTRO",
    "cep": "59600520",
    "uf": "RN",
    "municipio": "MOSSORO",
    "codigo_municipio": "1759",
    "email": "",
    "telefones": [{"ddd": "84", "numero": "33212869", "is_fax": False}],
    "capital_social": "0,00",
    "qualificacao_responsavel": {"codigo": "16", "descricao": "Presidente"},
    "ente_federativo": "",
    "porte_empresa": "Demais",
    "opcao_simples": "",
    "data_opcao_simples": "",
    "data_exclusao_simples": "",
    "opcao_mei": "",
    "data_opcao_mei": "",
    "data_exclusao_mei": "",
    "motivo_situacao_cadastral": {"codigo": "73", "descricao": "COOMISSAO CONTUMAZ"},
    "nome_cidade_exterior": "",
    "codigo_pais": "",
    "pais": {"codigo": "", "descricao": ""},
    "situacao_especial": "",
    "data_situacao_especial": "",
    "QSA": [{"nome_socio": "FRANCISCO FREIRE", "cnpj_cpf_socio": "***217524**", "qualificacao_socio": "Presidente"}],
}


@pytest.fixture
def convertido():
    tupla, dominios = linha(REGISTRO)
    return dict(zip(COLUNAS, tupla)), dominios


def test_tupla_tem_uma_posicao_por_coluna():
    tupla, _ = linha(REGISTRO)
    assert len(tupla) == len(COLUNAS)


def test_campos_escalares(convertido):
    valores, _ = convertido
    assert valores["cnpj"] == "99017782000139"
    assert valores["razao_social"] == "SOCIEDADE UNIAO DE ARTISTAS"
    assert valores["uf"] == "RN"
    assert valores["cnae_principal"] == "9493600"
    assert valores["cnaes_secundarios"] == ["9499500"]


def test_datas_viram_date(convertido):
    valores, _ = convertido
    assert valores["data_inicio_atividade"] == date(1984, 2, 20)
    assert valores["data_situacao_cadastral"] == date(2015, 2, 9)


def test_string_vazia_vira_nulo(convertido):
    valores, _ = convertido
    assert valores["complemento"] is None
    assert valores["email"] is None
    assert valores["data_opcao_mei"] is None
    assert valores["codigo_pais"] is None


def test_capital_social_em_decimal(convertido):
    valores, _ = convertido
    assert valores["capital_social"] == Decimal("0.00")


def test_capital_social_com_separador_de_milhar():
    tupla, _ = linha({**REGISTRO, "capital_social": "1.234.567,89"})
    valores = dict(zip(COLUNAS, tupla))
    assert valores["capital_social"] == Decimal("1234567.89")


def test_data_invalida_nao_derruba_a_linha():
    tupla, _ = linha({**REGISTRO, "data_situacao_cadastral": "0000-00-00"})
    valores = dict(zip(COLUNAS, tupla))
    assert valores["data_situacao_cadastral"] is None
    assert valores["cnpj"] == "99017782000139"


def test_descricoes_saem_para_dominio(convertido):
    _, dominios = convertido
    assert ("cnae", "9493600", "Atividades de organizacoes ligadas a cultura") in dominios
    assert ("qualificacao", "16", "Presidente") in dominios
    assert ("motivo", "73", "COOMISSAO CONTUMAZ") in dominios


def test_pais_vazio_nao_gera_dominio(convertido):
    _, dominios = convertido
    assert not [d for d in dominios if d[0] == "pais"]


def test_codigos_ficam_na_empresa_sem_descricao(convertido):
    valores, _ = convertido
    assert valores["motivo_situacao_codigo"] == "73"
    assert valores["qualificacao_responsavel_codigo"] == "16"


def test_qsa_vem_inteiro(convertido):
    valores, _ = convertido
    assert valores["qsa"] == REGISTRO["QSA"]


def test_nul_e_removido_dos_textos_e_do_jsonb():
    tupla, _ = linha({
        **REGISTRO,
        "razao_social": "ACME\x00 LTDA",
        "cnaes_secundarios": ["94995\x0000"],
        "QSA": [{"nome_socio": "FRANCISCO\x00 FREIRE"}],
        "telefones": [{"numero": "3321\x002869"}],
    })
    valores = dict(zip(COLUNAS, tupla))
    assert valores["razao_social"] == "ACME LTDA"
    assert valores["cnaes_secundarios"] == ["9499500"]
    assert valores["qsa"] == [{"nome_socio": "FRANCISCO FREIRE"}]
    assert valores["telefones"] == [{"numero": "33212869"}]


def test_cnpj_curto_e_completado_com_zeros():
    tupla, _ = linha({**REGISTRO, "cnpj": "17782000139"})
    assert tupla[0] == "00017782000139"


def test_registro_sem_cnpj_e_descartado():
    tupla, dominios = linha({**REGISTRO, "cnpj": ""})
    assert tupla is None
    assert dominios == ()
