import time
import unicodedata
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from psycopg.errors import UndefinedTable
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import config
from app.schema import normalizar_nome

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
    "capital_social",
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
        "capital_social": float(linha["capital_social"]) if linha["capital_social"] is not None else None,
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


def municipios_excluidos(texto):
    """Lista de codigos de municipio a deixar de fora, vinda da query string."""
    if not texto:
        return []
    return [pedaco.strip() for pedaco in texto.split(",") if pedaco.strip()]


def clausula_de_ordem(amostra):
    """ORDER BY da consulta por CNAE.

    Sem `amostra`, a ordem e o CNPJ, que e estavel e pagina bem. Com `amostra`,
    a ordem passa a ser o md5 do CNPJ com a semente: uma permutacao arbitraria e
    reprodutivel do conjunto filtrado. A diferenca importa porque CNPJ nao e
    identificador neutro, ele cresce com o tempo de registro. Ordenar por CNPJ e
    cortar no limite devolve as empresas mais antigas do universo, nunca uma
    fatia representativa dele.
    """
    if amostra:
        return "ORDER BY md5(cnpj || %(semente)s) LIMIT %(amostra)s"
    return "ORDER BY cnpj LIMIT %(limit)s OFFSET %(offset)s"


def _lista(valor):
    """Lista de codigos vinda de query string (separados por virgula) ou de JSON."""
    if isinstance(valor, str):
        return municipios_excluidos(valor)
    return [str(item).strip() for item in valor or () if str(item).strip()]


def _cnaes(valor):
    return ["".join(c for c in item if c.isdigit()) for item in _lista(valor) if any(c.isdigit() for c in item)]


def montar_filtro(
    cnae,
    uf=None,
    situacao=None,
    codigo_municipio=None,
    excluir_municipios=None,
    porte=None,
    matriz=None,
    inclui_secundario=False,
    capital_min=None,
    capital_max=None,
    inicio_de=None,
    inicio_ate=None,
    cnaes_secundarios=None,
    excluir_cnpjs=None,
):
    """WHERE da consulta por CNAE e os parametros que ele referencia.

    Serve GET /cnae/{codigo} e POST /busca: os dois respondem a mesma pergunta,
    o segundo so aceita listas grandes demais para uma query string.
    """
    codigos = [cnae] if isinstance(cnae, str) else list(cnae)
    parametros = {"cnaes": codigos}
    # = ANY(...) no cnae_principal continua usando o composto (btree aceita a
    # lista); no secundario e && (sobreposicao), que o GIN atende
    if inclui_secundario:
        condicoes = ["(cnae_principal = ANY(%(cnaes)s) OR cnaes_secundarios && %(cnaes)s)"]
    else:
        condicoes = ["cnae_principal = ANY(%(cnaes)s)"]
    if uf:
        condicoes.append("uf = %(uf)s")
        parametros["uf"] = uf.strip().upper()[:2]
    if situacao:
        condicoes.append("situacao_cadastral = %(situacao)s")
        parametros["situacao"] = situacao.strip()
    if codigo_municipio:
        condicoes.append("codigo_municipio = %(municipio)s")
        parametros["municipio"] = str(codigo_municipio).strip()
    excluidos = _lista(excluir_municipios)
    if excluidos:
        # Empresa sem municipio tambem sai: nao da para garantir que ela nao
        # esteja justamente num dos codigos que o chamador quis evitar.
        condicoes.append("codigo_municipio <> ALL(%(excluidos)s)")
        parametros["excluidos"] = excluidos
    if porte:
        condicoes.append("porte_empresa = %(porte)s")
        parametros["porte"] = porte.strip()
    if matriz is not None:
        condicoes.append("matriz_filial = %(matriz)s")
        parametros["matriz"] = "Matriz" if matriz else "Filial"
    if capital_min is not None:
        condicoes.append("capital_social >= %(capital_min)s")
        parametros["capital_min"] = capital_min
    if capital_max is not None:
        condicoes.append("capital_social <= %(capital_max)s")
        parametros["capital_max"] = capital_max
    if inicio_de:
        condicoes.append("data_inicio_atividade >= %(inicio_de)s")
        parametros["inicio_de"] = inicio_de
    if inicio_ate:
        condicoes.append("data_inicio_atividade <= %(inicio_ate)s")
        parametros["inicio_ate"] = inicio_ate
    secundarios = _cnaes(cnaes_secundarios)
    if secundarios:
        # && (sobreposicao) tambem e atendido pelo GIN: basta ter um dos codigos
        condicoes.append("cnaes_secundarios && %(secundarios)s")
        parametros["secundarios"] = secundarios
    cnpjs_fora = [c for c in (_limpar_cnpj(item) for item in _lista(excluir_cnpjs)) if c]
    if cnpjs_fora:
        condicoes.append("cnpj <> ALL(%(cnpjs_fora)s)")
        parametros["cnpjs_fora"] = cnpjs_fora
    return " AND ".join(condicoes), parametros


def consultar_por_cnae(codigo, filtro, parametros, formato, limit, offset, amostra=None, semente="", com_total=False):
    campos = "*" if formato == "completo" else ", ".join(CAMPOS_RESUMO)
    parametros = dict(parametros, limit=limit, offset=offset)
    if amostra:
        parametros["amostra"] = amostra
        parametros["semente"] = semente
    sql = f"SELECT {campos} FROM empresa WHERE {filtro} {clausula_de_ordem(amostra)}"

    total = None
    with pool.connection() as conn:
        linhas = conn.execute(sql, parametros).fetchall()
        if amostra or com_total:
            total = conn.execute(
                f"SELECT count(*) AS total FROM empresa WHERE {filtro}", parametros
            ).fetchone()["total"]

    montar = montar_completo if formato == "completo" else montar_resumo
    resposta = {
        "cnae": codigo,
        "limit": limit,
        "offset": offset,
        "retornados": len(linhas),
        "empresas": [montar(item) for item in linhas],
    }
    if amostra:
        # Ecoados de proposito: e por eles que um cliente descobre se a base ja
        # sabe sortear ou se ignorou os parametros e devolveu a primeira pagina.
        resposta["amostra"] = amostra
        resposta["semente"] = semente
    if amostra or com_total:
        resposta["total"] = total
    return resposta


def _codigo_cnae(valor):
    codigo = "".join(c for c in str(valor or "") if c.isdigit())
    if not codigo:
        raise HTTPException(status_code=400, detail="CNAE invalido")
    return codigo


@app.get("/cnae/{codigo}")
def por_cnae(
    codigo: str,
    uf: str | None = None,
    situacao: str | None = Query(None, description="ex.: Ativa, Baixada, Suspensa"),
    codigo_municipio: str | None = None,
    excluir_municipios: str | None = Query(
        None, description="codigos de municipio a deixar de fora, separados por virgula"
    ),
    porte: str | None = None,
    matriz: bool | None = Query(None, description="true devolve somente matriz"),
    inclui_secundario: bool = Query(False, description="considera tambem quem tem o CNAE como secundario"),
    capital_min: Decimal | None = Query(None, ge=0, description="capital social minimo"),
    capital_max: Decimal | None = Query(None, ge=0, description="capital social maximo"),
    inicio_de: date | None = Query(None, description="inicio de atividade a partir de (AAAA-MM-DD)"),
    inicio_ate: date | None = Query(None, description="inicio de atividade ate (AAAA-MM-DD)"),
    cnaes_secundarios: str | None = Query(
        None, description="devolve quem tem ao menos um destes codigos como CNAE secundario, separados por virgula"
    ),
    com_total: bool = Query(False, description="tambem conta quantas empresas o filtro alcanca"),
    formato: str = Query("resumo", pattern="^(resumo|completo)$"),
    amostra: int | None = Query(
        None, ge=1, le=1000, description="sorteia esta quantidade no conjunto todo, em vez de paginar"
    ),
    semente: str = Query("", description="fixa o sorteio: mesma semente, mesma amostra"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    codigo = _codigo_cnae(codigo)
    filtro, parametros = montar_filtro(
        codigo,
        uf=uf,
        situacao=situacao,
        codigo_municipio=codigo_municipio,
        excluir_municipios=excluir_municipios,
        porte=porte,
        matriz=matriz,
        inclui_secundario=inclui_secundario,
        capital_min=capital_min,
        capital_max=capital_max,
        inicio_de=inicio_de,
        inicio_ate=inicio_ate,
        cnaes_secundarios=cnaes_secundarios,
    )
    return consultar_por_cnae(
        codigo, filtro, parametros, formato, limit, offset,
        amostra=amostra, semente=semente, com_total=com_total,
    )


class Busca(BaseModel):
    """Mesmos filtros de GET /cnae/{codigo}, em JSON.

    Existe por causa de `excluir_cnpjs`: quem monta a lista de alvos de um
    evento precisa tirar do resultado quem ja esta nela, e sao centenas ou
    milhares de CNPJs, mais do que cabe numa query string.
    """

    cnae: str | None = None
    cnaes: list[str] = Field(default_factory=list, max_length=100)
    uf: str | None = None
    situacao: str | None = None
    codigo_municipio: str | None = None
    excluir_municipios: list[str] = Field(default_factory=list)
    porte: str | None = None
    matriz: bool | None = None
    inclui_secundario: bool = False
    capital_min: Decimal | None = Field(None, ge=0)
    capital_max: Decimal | None = Field(None, ge=0)
    inicio_de: date | None = None
    inicio_ate: date | None = None
    cnaes_secundarios: list[str] = Field(default_factory=list)
    excluir_cnpjs: list[str] = Field(default_factory=list, max_length=20000)
    com_total: bool = True
    formato: str = Field("resumo", pattern="^(resumo|completo)$")
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


@app.post("/busca")
def busca(consulta: Busca):
    """Aceita `cnae` (um) ou `cnaes` (varios); a empresa entra se tiver qualquer um deles."""
    codigos = list(dict.fromkeys(_cnaes(consulta.cnaes) + ([_codigo_cnae(consulta.cnae)] if consulta.cnae else [])))
    if not codigos:
        raise HTTPException(status_code=400, detail="Informe ao menos um CNAE")
    filtro, parametros = montar_filtro(
        codigos,
        uf=consulta.uf,
        situacao=consulta.situacao,
        codigo_municipio=consulta.codigo_municipio,
        excluir_municipios=consulta.excluir_municipios,
        porte=consulta.porte,
        matriz=consulta.matriz,
        inclui_secundario=consulta.inclui_secundario,
        capital_min=consulta.capital_min,
        capital_max=consulta.capital_max,
        inicio_de=consulta.inicio_de,
        inicio_ate=consulta.inicio_ate,
        cnaes_secundarios=consulta.cnaes_secundarios,
        excluir_cnpjs=consulta.excluir_cnpjs,
    )
    return consultar_por_cnae(
        ",".join(codigos), filtro, parametros, consulta.formato, consulta.limit, consulta.offset,
        com_total=consulta.com_total,
    )


def _sem_acento(texto):
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(texto or "")) if not unicodedata.combining(c)
    ).casefold()


def filtrar_cnaes(itens, termo, limit, offset=0):
    """CNAEs cujo codigo comeca pelo termo ou cuja descricao o contem.

    Roda em memoria: sao pouco mais de mil codigos, ja carregados para
    recompor as descricoes das empresas. Devolve (pagina, total) para o
    cliente saber se ha mais.
    """
    termo = str(termo or "").strip()
    digitos = "".join(c for c in termo if c.isdigit())
    so_digitos = bool(digitos) and not any(c.isalpha() for c in termo)
    texto = _sem_acento(termo)
    encontrados = []
    for codigo, descricao in sorted(itens):
        if not termo:
            bate = True
        elif so_digitos:
            bate = codigo.startswith(digitos)
        else:
            bate = texto in _sem_acento(descricao)
        if bate:
            encontrados.append({"codigo": codigo, "descricao": descricao})
    return encontrados[offset : offset + limit], len(encontrados)


@app.get("/cnaes")
def cnaes(
    q: str = Query("", description="inicio do codigo ou trecho da descricao"),
    codigos: str | None = Query(None, description="devolve so estes codigos, separados por virgula"),
    limit: int = Query(50, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    itens = [(codigo, descricao) for (tipo, codigo), descricao in dominios().items() if tipo == "cnae"]
    pedidos = set(_cnaes(codigos))
    if pedidos:
        itens = [item for item in itens if item[0] in pedidos]
    pagina, total = filtrar_cnaes(itens, q, limit, offset)
    return {"cnaes": pagina, "total": total, "limit": limit, "offset": offset}


@app.get("/municipios")
def municipios(
    uf: str = Query(..., min_length=2, max_length=2),
    q: str = Query("", description="trecho do nome"),
    limit: int = Query(500, ge=1, le=2000),
):
    """Municipios de uma UF com empresas na base, do mais populoso ao menos."""
    parametros = {"uf": uf.strip().upper(), "limit": limit}
    condicao = "uf = %(uf)s"
    if q.strip():
        condicao += " AND nome ILIKE %(nome)s"
        parametros["nome"] = f"%{q.strip()}%"
    try:
        with pool.connection() as conn:
            linhas = conn.execute(
                f"SELECT uf, codigo, nome, empresas FROM municipio WHERE {condicao} "
                f"ORDER BY empresas DESC, nome LIMIT %(limit)s",
                parametros,
            ).fetchall()
    except UndefinedTable:
        raise HTTPException(
            status_code=503,
            detail="tabela municipio ainda nao existe nesta carga; rode python -m app.derivar",
        )
    return {"uf": parametros["uf"], "municipios": linhas}


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


NATUREZAS_PESSOA_FISICA = (
    "Empresário (Individual)",
    "Produtor Rural (Pessoa Física)",
    "Candidato a Cargo Político Eletivo",
)

CAMPOS_NOME = (
    "cnpj",
    "razao_social",
    "nome_fantasia",
    "natureza_juridica",
    "situacao_cadastral",
    "matriz_filial",
    "uf",
    "municipio",
    "codigo_municipio",
    "cnae_principal",
)

FILTRO_PESSOA_FISICA = "coalesce(natureza_juridica, '') <> ALL(%(naturezas_pf)s) AND opcao_mei IS DISTINCT FROM 'S'"


def _like_literal(texto):
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def montar_filtro_nome(q, modo, uf=None, municipio=None, situacao="Ativa", excluir_pessoa_fisica=False):
    termo = normalizar_nome(q) or ""
    if len("".join(c for c in termo if c.isalnum())) < 3:
        raise ValueError("informe ao menos 3 letras ou numeros")
    if (situacao or "").strip().casefold() != "ativa":
        raise ValueError("a busca por nome alcanca so empresas ativas")
    uf = (uf or "").strip().upper()[:2]
    if modo != "exato" and not uf:
        raise ValueError("sem uf, so o modo exato e aceito")

    condicoes = ["situacao_cadastral = 'Ativa'"]
    parametros = {}
    if modo == "exato":
        condicoes.append("nome_chave(razao_social) = nome_chave(%(termo)s)")
        parametros["termo"] = termo
    else:
        padrao = _like_literal(termo) + "%"
        parametros["padrao"] = padrao if modo == "inicio" else "%" + padrao
        condicoes.append("(razao_social LIKE %(padrao)s OR nome_fantasia LIKE %(padrao)s)")
    if uf:
        condicoes.append("uf = %(uf)s")
        parametros["uf"] = uf
    if municipio and municipio.strip():
        condicoes.append("codigo_municipio = %(municipio)s")
        parametros["municipio"] = municipio.strip()
    if excluir_pessoa_fisica:
        condicoes.append(FILTRO_PESSOA_FISICA)
        parametros["naturezas_pf"] = list(NATUREZAS_PESSOA_FISICA)
    return " AND ".join(condicoes), parametros


def montar_nome(linha):
    empresa = {campo: _texto(linha[campo]) for campo in CAMPOS_NOME}
    empresa["matriz"] = linha["matriz_filial"] == "Matriz"
    return empresa


def agrupar_candidatos(linhas):
    grupos = {}
    for linha in linhas:
        grupo = grupos.setdefault(
            linha["ordem"],
            {"nome": linha["nome"], "chave": linha["chave"], "total": linha["total"] or 0, "candidatos": []},
        )
        if linha["cnpj"]:
            grupo["candidatos"].append(montar_nome(linha))
    return [grupos[ordem] for ordem in sorted(grupos)]


@app.get("/nome")
def por_nome(
    q: str = Query(..., description="nome ou trecho, ao menos 3 caracteres"),
    uf: str | None = None,
    municipio: str | None = Query(None, description="codigo do municipio na Receita"),
    situacao: str = Query("Ativa", description="so Ativa"),
    modo: str = Query("contem", pattern="^(contem|inicio|exato)$"),
    excluir_pessoa_fisica: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    try:
        filtro, parametros = montar_filtro_nome(q, modo, uf, municipio, situacao, excluir_pessoa_fisica)
    except ValueError as erro:
        raise HTTPException(status_code=400, detail=str(erro))
    campos = ", ".join(CAMPOS_NOME)
    with pool.connection() as conn:
        linhas = conn.execute(
            f"SELECT {campos} FROM empresa WHERE {filtro} ORDER BY razao_social, cnpj "
            "LIMIT %(limit)s OFFSET %(offset)s",
            dict(parametros, limit=limit, offset=offset),
        ).fetchall()
        total = conn.execute(f"SELECT count(*) AS total FROM empresa WHERE {filtro}", parametros).fetchone()["total"]
    return {
        "q": q,
        "modo": modo,
        "total": total,
        "limit": limit,
        "offset": offset,
        "empresas": [montar_nome(linha) for linha in linhas],
    }


class ConsultaNomes(BaseModel):
    nomes: list[str] = Field(min_length=1, max_length=1000)
    uf: str | None = None
    excluir_pessoa_fisica: bool = True
    max_candidatos: int = Field(20, ge=1, le=100)


@app.post("/nomes")
def por_nomes(consulta: ConsultaNomes):
    parametros = {"nomes": consulta.nomes, "max": consulta.max_candidatos}
    extra = ""
    if consulta.uf and consulta.uf.strip():
        extra += " AND uf = %(uf)s"
        parametros["uf"] = consulta.uf.strip().upper()[:2]
    if consulta.excluir_pessoa_fisica:
        extra += " AND " + FILTRO_PESSOA_FISICA
        parametros["naturezas_pf"] = list(NATUREZAS_PESSOA_FISICA)
    campos = ", ".join(f"c.{campo}" for campo in CAMPOS_NOME)
    sql = f"""
        WITH entrada AS (
            SELECT t.nome, t.ordem, nome_chave(t.nome) AS chave
            FROM unnest(%(nomes)s::text[]) WITH ORDINALITY AS t(nome, ordem)
        )
        SELECT e.ordem, e.nome, e.chave, c.total, {campos}
        FROM entrada e
        LEFT JOIN LATERAL (
            SELECT {", ".join(CAMPOS_NOME)}, count(*) OVER () AS total
            FROM empresa
            WHERE situacao_cadastral = 'Ativa' AND nome_chave(razao_social) = e.chave{extra}
            ORDER BY (matriz_filial = 'Matriz') DESC, cnpj
            LIMIT %(max)s
        ) c ON true
        ORDER BY e.ordem, (c.matriz_filial = 'Matriz') DESC, c.cnpj
    """
    with pool.connection() as conn:
        linhas = conn.execute(sql, parametros).fetchall()
    return {"nomes": agrupar_candidatos(linhas)}


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
