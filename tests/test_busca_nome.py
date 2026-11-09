from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from psycopg.errors import QueryCanceled

from app import api
from app.api import (
    NATUREZAS_PESSOA_FISICA,
    TEMPO_LIMITE_NACIONAL,
    busca_nacional,
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
        ("enge", "contem", None, "Ativa"),
        ("e.n.g.e", "inicio", "", "Ativa"),
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


def test_sem_uf_trecho_de_cinco_caracteres_vale_no_pais_todo():
    filtro, parametros = montar_filtro_nome("engen", "contem")

    assert "uf" not in parametros
    assert parametros["padrao"] == "%ENGEN%"
    assert "situacao_cadastral = 'Ativa'" in filtro


def test_com_uf_trecho_de_tres_caracteres_continua_valendo():
    _, parametros = montar_filtro_nome("eng", "contem", uf="GO")

    assert parametros["padrao"] == "%ENG%"


def test_busca_nacional_e_so_trecho_ou_inicio_sem_uf():
    assert busca_nacional("contem", None)
    assert busca_nacional("inicio", " ")
    assert not busca_nacional("exato", None)
    assert not busca_nacional("contem", "GO")


class _Resultado:
    def __init__(self, linhas):
        self.linhas = linhas

    def fetchall(self):
        return self.linhas

    def fetchone(self):
        return self.linhas[0]


class _Conexao:
    def __init__(self, linhas, erro=None):
        self.linhas = linhas
        self.erro = erro
        self.comandos = []

    def execute(self, sql, parametros=None):
        self.comandos.append((sql, parametros))
        if sql.startswith("SELECT count"):
            return _Resultado([{"total": len(self.linhas)}])
        if sql.startswith("SELECT") and self.erro:
            raise self.erro
        if sql.startswith("SELECT"):
            limite = parametros["limit"]
            return _Resultado(self.linhas[:limite])
        return _Resultado([])


class _Pool:
    def __init__(self, conexao):
        self.conexao = conexao

    @contextmanager
    def connection(self):
        yield self.conexao


def _pedir(monkeypatch, conexao, **params):
    monkeypatch.setattr(api, "pool", _Pool(conexao))
    return TestClient(app).get("/nome", params=params)


def _linhas(quantidade):
    return [{**LINHA, "cnpj": f"{n:014d}"} for n in range(quantidade)]


def test_sem_uf_nao_conta_e_avisa_que_ha_mais(monkeypatch):
    conexao = _Conexao(_linhas(4))

    corpo = _pedir(monkeypatch, conexao, q="engenharia", limit=3).json()

    assert corpo["total"] is None
    assert corpo["mais"] is True
    assert len(corpo["empresas"]) == 3
    assert not [sql for sql, _ in conexao.comandos if sql.startswith("SELECT count")]


def test_sem_uf_na_ultima_pagina_nao_ha_mais(monkeypatch):
    corpo = _pedir(monkeypatch, _Conexao(_linhas(3)), q="engenharia", limit=3).json()

    assert corpo["mais"] is False
    assert len(corpo["empresas"]) == 3


def test_sem_uf_a_consulta_tem_tempo_limite(monkeypatch):
    conexao = _Conexao(_linhas(1))

    _pedir(monkeypatch, conexao, q="engenharia")

    assert conexao.comandos[0][0] == f"SET LOCAL statement_timeout = '{TEMPO_LIMITE_NACIONAL}'"


def test_sem_uf_tempo_estourado_vira_504(monkeypatch):
    resposta = _pedir(monkeypatch, _Conexao([], erro=QueryCanceled()), q="engenharia")

    assert resposta.status_code == 504
    assert "uf" in resposta.json()["detail"]


def test_sem_uf_limite_passa_de_50_e_recusado(monkeypatch):
    resposta = _pedir(monkeypatch, _Conexao([]), q="engenharia", limit=51)

    assert resposta.status_code == 400
    assert "50" in resposta.json()["detail"]


def test_sem_uf_termo_de_quatro_caracteres_e_recusado(monkeypatch):
    resposta = _pedir(monkeypatch, _Conexao([]), q="enge")

    assert resposta.status_code == 400


def test_com_uf_continua_contando_sem_tempo_limite(monkeypatch):
    conexao = _Conexao(_linhas(4))

    corpo = _pedir(monkeypatch, conexao, q="eng", uf="GO", limit=3).json()

    assert corpo["total"] == 4
    assert corpo["mais"] is True
    assert not [sql for sql, _ in conexao.comandos if "statement_timeout" in sql]


def test_com_uf_aceita_limite_acima_de_50(monkeypatch):
    resposta = _pedir(monkeypatch, _Conexao(_linhas(1)), q="eng", uf="GO", limit=200)

    assert resposta.status_code == 200
    assert resposta.json()["mais"] is False


def test_get_nome_recusa_modo_desconhecido():
    resposta = TestClient(app).get("/nome", params={"q": "engenharia", "modo": "parecido", "uf": "GO"})

    assert resposta.status_code == 422


def test_post_nomes_exige_ao_menos_um_nome():
    resposta = TestClient(app).post("/nomes", json={"nomes": []})

    assert resposta.status_code == 422
