import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import config

CAMPOS_RESUMO = (
    "cnpj",
    "razao_social",
    "nome_fantasia",
    "cnae_principal",
    "situacao_cadastral",
    "data_inicio_atividade",
    "porte_empresa",
    "matriz_filial",
    "uf",
    "municipio",
    "codigo_municipio",
)

TTL_DOMINIOS = 600
_dominios = {"carregado_em": 0.0, "dados": {}}

pool = ConnectionPool(config.DB_DSN, min_size=1, max_size=8, open=False, kwargs={"row_factory": dict_row})


@asynccontextmanager
async def lifespan(_app):
    pool.open()
    yield
    pool.close()


app = FastAPI(
    title="Base CNPJ",
    description="Espelho local da base OpenCNPJ para consulta por CNPJ e por CNAE.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def dominios():
    agora = time.monotonic()
    if agora - _dominios["carregado_em"] > TTL_DOMINIOS:
        with pool.connection() as conn:
            linhas = conn.execute("SELECT tipo, codigo, descricao FROM dominio").fetchall()
        _dominios["dados"] = {(item["tipo"], item["codigo"]): item["descricao"] or "" for item in linhas}
        _dominios["carregado_em"] = agora
    return _dominios["dados"]


def _descricao(tipo, codigo):
    if not codigo:
        return ""
    return dominios().get((tipo, codigo), "")


def _texto(valor):
    return "" if valor is None else str(valor)


def _data(valor):
    return valor.isoformat() if valor else ""


def _moeda(valor):
    if valor is None:
        return ""
    return f"{valor:.2f}".replace(".", ",")


def _bloco(tipo, codigo):
    return {"codigo": _texto(codigo), "descricao": _descricao(tipo, codigo)}


def _limpar_cnpj(valor):
    digitos = "".join(c for c in str(valor or "") if c.isdigit())
    return digitos if len(digitos) == 14 else None


def montar_completo(linha):
    """Reproduz o payload de api.opencnpj.org campo por campo.

    A compatibilidade e proposital: quem ja consome a API publica troca so a
    URL base e nao mexe em mais nada.
    """
    principal = linha["cnae_principal"]
    secundarios = linha["cnaes_secundarios"] or []
    cnaes = []
    if principal:
        cnaes.append({"codigo": principal, "descricao": _descricao("cnae", principal), "is_principal": True})
    for codigo in secundarios:
        cnaes.append({"codigo": codigo, "descricao": _descricao("cnae", codigo), "is_principal": False})

    return {
        "cnpj": _texto(linha["cnpj"]),
        "razao_social": _texto(linha["razao_social"]),
        "nome_fantasia": _texto(linha["nome_fantasia"]),
        "situacao_cadastral": _texto(linha["situacao_cadastral"]),
        "data_situacao_cadastral": _data(linha["data_situacao_cadastral"]),
        "matriz_filial": _texto(linha["matriz_filial"]),
        "data_inicio_atividade": _data(linha["data_inicio_atividade"]),
        "cnae_principal": _texto(principal),
        "cnaes_secundarios": list(secundarios),
        "cnaes": cnaes,
        "natureza_juridica": _texto(linha["natureza_juridica"]),
        "tipo_logradouro": _texto(linha["tipo_logradouro"]),
        "logradouro": _texto(linha["logradouro"]),
        "numero": _texto(linha["numero"]),
        "complemento": _texto(linha["complemento"]),
        "bairro": _texto(linha["bairro"]),
        "cep": _texto(linha["cep"]),
        "uf": _texto(linha["uf"]),
        "municipio": _texto(linha["municipio"]),
        "codigo_municipio": _texto(linha["codigo_municipio"]),
        "email": _texto(linha["email"]),
        "telefones": linha["telefones"] or [],
        "capital_social": _moeda(linha["capital_social"]),
        "qualificacao_responsavel": _bloco("qualificacao", linha["qualificacao_responsavel_codigo"]),
        "ente_federativo": _texto(linha["ente_federativo"]),
        "porte_empresa": _texto(linha["porte_empresa"]),
        "opcao_simples": _texto(linha["opcao_simples"]),
        "data_opcao_simples": _data(linha["data_opcao_simples"]),
        "data_exclusao_simples": _data(linha["data_exclusao_simples"]),
        "opcao_mei": _texto(linha["opcao_mei"]),
        "data_opcao_mei": _data(linha["data_opcao_mei"]),
        "data_exclusao_mei": _data(linha["data_exclusao_mei"]),
        "motivo_situacao_cadastral": _bloco("motivo", linha["motivo_situacao_codigo"]),
        "nome_cidade_exterior": _texto(linha["nome_cidade_exterior"]),
        "codigo_pais": _texto(linha["codigo_pais"]),
        "pais": _bloco("pais", linha["codigo_pais"]),
        "situacao_especial": _texto(linha["situacao_especial"]),
        "data_situacao_especial": _data(linha["data_situacao_especial"]),
        "QSA": linha["qsa"] or [],
    }


def montar_resumo(linha):
    return {
        "cnpj": _texto(linha["cnpj"]),
        "razao_social": _texto(linha["razao_social"]),
        "nome_fantasia": _texto(linha["nome_fantasia"]),
        "cnae_principal": _texto(linha["cnae_principal"]),
        "cnae_descricao": _descricao("cnae", linha["cnae_principal"]),
        "situacao_cadastral": _texto(linha["situacao_cadastral"]),
        "data_inicio_atividade": _data(linha["data_inicio_atividade"]),
        "porte_empresa": _texto(linha["porte_empresa"]),
        "matriz_filial": _texto(linha["matriz_filial"]),
        "uf": _texto(linha["uf"]),
        "municipio": _texto(linha["municipio"]),
        "codigo_municipio": _texto(linha["codigo_municipio"]),
    }


@app.get("/health")
def health():
    try:
        with pool.connection() as conn:
            pronto = conn.execute("SELECT to_regclass('empresa') IS NOT NULL AS ok").fetchone()["ok"]
    except Exception as erro:
        raise HTTPException(status_code=503, detail=f"banco indisponivel: {erro}")
    return {"status": "ok" if pronto else "sem_dados", "base_carregada": bool(pronto)}


@app.get("/carga")
def cargas():
    with pool.connection() as conn:
        linhas = conn.execute(
            "SELECT id, status, iniciada_em, concluida_em, arquivos, total_registros, erro "
            "FROM carga ORDER BY id DESC LIMIT 10"
        ).fetchall()
    return {"cargas": linhas}


@app.get("/cnae/{codigo}")
def por_cnae(
    codigo: str,
    uf: str | None = None,
    situacao: str | None = Query(None, description="ex.: Ativa, Baixada, Suspensa"),
    codigo_municipio: str | None = None,
    porte: str | None = None,
    matriz: bool | None = Query(None, description="true devolve somente matriz"),
    inclui_secundario: bool = Query(False, description="considera tambem quem tem o CNAE como secundario"),
    formato: str = Query("resumo", pattern="^(resumo|completo)$"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    codigo = "".join(c for c in codigo if c.isdigit())
    if not codigo:
        raise HTTPException(status_code=400, detail="CNAE invalido")

    campos = "*" if formato == "completo" else ", ".join(CAMPOS_RESUMO)
    parametros = {"cnae": codigo, "limit": limit, "offset": offset}

    if inclui_secundario:
        # @> em vez de "= ANY(...)": e a forma que o indice GIN de
        # cnaes_secundarios consegue atender
        condicoes = ["(cnae_principal = %(cnae)s OR cnaes_secundarios @> ARRAY[%(cnae)s])"]
    else:
        condicoes = ["cnae_principal = %(cnae)s"]
    if uf:
        condicoes.append("uf = %(uf)s")
        parametros["uf"] = uf.strip().upper()[:2]
    if situacao:
        condicoes.append("situacao_cadastral = %(situacao)s")
        parametros["situacao"] = situacao.strip()
    if codigo_municipio:
        condicoes.append("codigo_municipio = %(municipio)s")
        parametros["municipio"] = codigo_municipio.strip()
    if porte:
        condicoes.append("porte_empresa = %(porte)s")
        parametros["porte"] = porte.strip()
    if matriz is not None:
        condicoes.append("matriz_filial = %(matriz)s")
        parametros["matriz"] = "Matriz" if matriz else "Filial"

    filtro = " AND ".join(condicoes)
    sql = f"SELECT {campos} FROM empresa WHERE {filtro} ORDER BY cnpj LIMIT %(limit)s OFFSET %(offset)s"
    with pool.connection() as conn:
        linhas = conn.execute(sql, parametros).fetchall()

    montar = montar_completo if formato == "completo" else montar_resumo
    return {
        "cnae": codigo,
        "limit": limit,
        "offset": offset,
        "retornados": len(linhas),
        "empresas": [montar(item) for item in linhas],
    }


class ConsultaLote(BaseModel):
    cnpjs: list[str] = Field(min_length=1, max_length=1000)
    formato: str = Field("resumo", pattern="^(resumo|completo)$")


@app.post("/cnpjs")
def por_lote(consulta: ConsultaLote):
    """Consulta em lote, para enriquecer uma lista de CNPJs de uma vez.

    Existe porque o caso de uso que motivou a base e montar grupo de controle:
    milhares de CNPJs precisam do CNAE, e uma requisicao por empresa levaria
    minutos onde uma consulta leva milissegundos.
    """
    limpos = {c for c in (_limpar_cnpj(valor) for valor in consulta.cnpjs) if c}
    if not limpos:
        raise HTTPException(status_code=400, detail="nenhum CNPJ valido na lista")

    campos = "*" if consulta.formato == "completo" else ", ".join(CAMPOS_RESUMO)
    with pool.connection() as conn:
        linhas = conn.execute(
            f"SELECT {campos} FROM empresa WHERE cnpj = ANY(%s)", (sorted(limpos),)
        ).fetchall()

    montar = montar_completo if consulta.formato == "completo" else montar_resumo
    encontrados = [montar(item) for item in linhas]
    achados = {empresa["cnpj"] for empresa in encontrados}
    return {
        "solicitados": len(limpos),
        "encontrados": len(encontrados),
        "empresas": encontrados,
        "nao_encontrados": sorted(limpos - achados),
    }


@app.get("/{cnpj}")
def por_cnpj(cnpj: str):
    limpo = _limpar_cnpj(cnpj)
    if not limpo:
        raise HTTPException(status_code=400, detail="CNPJ deve ter 14 digitos")
    with pool.connection() as conn:
        linha = conn.execute("SELECT * FROM empresa WHERE cnpj = %s", (limpo,)).fetchone()
    if not linha:
        raise HTTPException(status_code=404, detail="CNPJ nao encontrado")
    return montar_completo(linha)
