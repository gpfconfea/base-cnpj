import pytest
from fastapi.testclient import TestClient

from app.api import (
    NATUREZAS_PESSOA_FISICA,
    agrupar_candidatos,
    app,
    montar_filtro_nome,
    montar_nome,
)

LINHA = {
    "cnpj": "12345678000190",
    "razao_social": "M.A. ENGENHARIA LTDA",
    "nome_fantasia": None,
    "natureza_juridica": "Sociedade Empresária Limitada",
    "situacao_cadastral": "Ativa",
    "matriz_filial": "Matriz",
    "uf": "GO",
    "municipio": "GOIANIA",
    "codigo_municipio": "9373",
    "cnae_principal": "7112000",
}


def test_contem_procura_o_trecho_na_razao_e_no_fantasia_entre_as_ativas():
    filtro, parametros = montar_filtro_nome("engenharia", "contem", uf="go")

    assert "situacao_cadastral = 'Ativa'" in filtro
    assert "(razao_social LIKE %(padrao)s OR nome_fantasia LIKE %(padrao)s)" in filtro
    assert parametros["padrao"] == "%ENGENHARIA%"
    assert parametros["uf"] == "GO"


def test_inicio_ancora_o_padrao_no_comeco():
    _, parametros = montar_filtro_nome("são  jose", "inicio", uf="GO")

    assert parametros["padrao"] == "SAO JOSE%"


def test_curinga_digitado_vira_literal():
    _, parametros = montar_filtro_nome(r"100%_A\B", "contem", uf="GO")

    assert parametros["padrao"] == r"%100\%\_A\\B%"


def test_exato_compara_pela_chave_sem_pontuacao():
    filtro, parametros = montar_filtro_nome("M.A. Engenharia", "exato")

    assert "nome_chave(razao_social) = nome_chave(%(termo)s)" in filtro
    assert parametros["termo"] == "M.A. ENGENHARIA"
    assert "uf" not in parametros


def test_municipio_entra_como_codigo():
    filtro, parametros = montar_filtro_nome("engenharia", "contem", uf="GO", municipio=" 9373 ")

    assert "codigo_municipio = %(municipio)s" in filtro
    assert parametros["municipio"] == "9373"


def test_excluir_pessoa_fisica_tira_naturezas_e_mei():
    filtro, parametros = montar_filtro_nome("engenharia", "exato", excluir_pessoa_fisica=True)

    assert "coalesce(natureza_juridica, '') <> ALL(%(naturezas_pf)s)" in filtro
    assert "opcao_mei IS DISTINCT FROM 'S'" in filtro
    assert parametros["naturezas_pf"] == list(NATUREZAS_PESSOA_FISICA)


def test_sem_excluir_pessoa_fisica_nao_ha_filtro_de_natureza():
    filtro, parametros = montar_filtro_nome("engenharia", "exato")

    assert "natureza_juridica" not in filtro
    assert "naturezas_pf" not in parametros


@pytest.mark.parametrize(
    "termo, modo, uf, situacao",
    [
        ("ab", "exato", None, "Ativa"),
        ("  a.b  ", "contem", "GO", "Ativa"),
        ("engenharia", "contem", None, "Ativa"),
        ("engenharia", "inicio", "", "Ativa"),
        ("engenharia", "exato", None, "Baixada"),
    ],
)
def test_pedidos_que_varreriam_a_base_sao_recusados(termo, modo, uf, situacao):
    with pytest.raises(ValueError):
        montar_filtro_nome(termo, modo, uf=uf, situacao=situacao)


def test_situacao_ativa_ignora_caixa():
    filtro, _ = montar_filtro_nome("engenharia", "exato", situacao="ativa")

    assert "situacao_cadastral = 'Ativa'" in filtro


def test_resposta_enxuta_sem_contato_nem_socios():
    empresa = montar_nome({**LINHA, "email": "x@y", "telefones": [], "qsa": []})

    assert empresa["cnpj"] == "12345678000190"
    assert empresa["matriz"] is True
    assert empresa["nome_fantasia"] == ""
    assert empresa["municipio"] == "GOIANIA"
    assert empresa["cnae_principal"] == "7112000"
    assert not {"email", "telefones", "qsa", "QSA"} & set(empresa)


def test_candidatos_agrupados_na_ordem_dos_nomes_enviados():
    linhas = [
        {**LINHA, "ordem": 1, "nome": "m.a. engenharia", "chave": "MA ENGENHARIA", "total": 2},
        {**LINHA, "ordem": 1, "nome": "m.a. engenharia", "chave": "MA ENGENHARIA", "total": 2,
         "cnpj": "12345678000270", "matriz_filial": "Filial"},
        {**{k: None for k in LINHA}, "ordem": 2, "nome": "inexistente", "chave": "INEXISTENTE", "total": None},
    ]

    resultado = agrupar_candidatos(linhas)

    assert [item["nome"] for item in resultado] == ["m.a. engenharia", "inexistente"]
    assert resultado[0]["chave"] == "MA ENGENHARIA"
    assert resultado[0]["total"] == 2
    assert [c["matriz"] for c in resultado[0]["candidatos"]] == [True, False]
    assert resultado[1]["total"] == 0
    assert resultado[1]["candidatos"] == []


def test_get_nome_responde_400_para_termo_curto():
    resposta = TestClient(app).get("/nome", params={"q": "ab", "modo": "exato"})

    assert resposta.status_code == 400


def test_get_nome_sem_uf_so_aceita_exato():
    resposta = TestClient(app).get("/nome", params={"q": "engenharia", "modo": "contem"})

    assert resposta.status_code == 400
    assert "uf" in resposta.json()["detail"]


def test_get_nome_recusa_modo_desconhecido():
    resposta = TestClient(app).get("/nome", params={"q": "engenharia", "modo": "parecido", "uf": "GO"})

    assert resposta.status_code == 422


def test_post_nomes_exige_ao_menos_um_nome():
    resposta = TestClient(app).post("/nomes", json={"nomes": []})

    assert resposta.status_code == 422
