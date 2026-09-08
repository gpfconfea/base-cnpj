from datetime import date
from decimal import Decimal, InvalidOperation

COLUNAS_JSON = ("telefones", "qsa")

COLUNAS = (
    "cnpj",
    "razao_social",
    "nome_fantasia",
    "situacao_cadastral",
    "data_situacao_cadastral",
    "motivo_situacao_codigo",
    "matriz_filial",
    "data_inicio_atividade",
    "cnae_principal",
    "cnaes_secundarios",
    "natureza_juridica",
    "tipo_logradouro",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cep",
    "uf",
    "municipio",
    "codigo_municipio",
    "email",
    "telefones",
    "capital_social",
    "qualificacao_responsavel_codigo",
    "ente_federativo",
    "porte_empresa",
    "opcao_simples",
    "data_opcao_simples",
    "data_exclusao_simples",
    "opcao_mei",
    "data_opcao_mei",
    "data_exclusao_mei",
    "nome_cidade_exterior",
    "codigo_pais",
    "situacao_especial",
    "data_situacao_especial",
    "qsa",
)

DDL = """
CREATE TABLE empresa (
    cnpj                            char(14) NOT NULL,
    razao_social                    text,
    nome_fantasia                   text,
    situacao_cadastral              text,
    data_situacao_cadastral         date,
    motivo_situacao_codigo          text,
    matriz_filial                   text,
    data_inicio_atividade           date,
    cnae_principal                  text,
    cnaes_secundarios               text[],
    natureza_juridica               text,
    tipo_logradouro                 text,
    logradouro                      text,
    numero                          text,
    complemento                     text,
    bairro                          text,
    cep                             text,
    uf                              char(2),
    municipio                       text,
    codigo_municipio                text,
    email                           text,
    telefones                       jsonb,
    capital_social                  numeric(18,2),
    qualificacao_responsavel_codigo text,
    ente_federativo                 text,
    porte_empresa                   text,
    opcao_simples                   text,
    data_opcao_simples              date,
    data_exclusao_simples           date,
    opcao_mei                       text,
    data_opcao_mei                  date,
    data_exclusao_mei               date,
    nome_cidade_exterior            text,
    codigo_pais                     text,
    situacao_especial               text,
    data_situacao_especial          date,
    qsa                             jsonb
)
"""

INDICES = (
    "ALTER TABLE empresa ADD CONSTRAINT empresa_pkey PRIMARY KEY (cnpj)",
    # um indice so para os tres recortes que /cnae/{codigo} aceita: pela regra da
    # coluna a esquerda ele atende CNAE sozinho, CNAE+UF e CNAE+UF+municipio.
    # Filtrar por municipio sem passar a UF junto so aproveita o CNAE.
    "CREATE INDEX empresa_cnae_uf_municipio_idx ON empresa (cnae_principal, uf, codigo_municipio)",
    # GIN porque cnaes_secundarios e text[]; so serve para o operador de
    # continencia (@>), nao para o "= ANY(...)" que a consulta usava antes
    "CREATE INDEX empresa_cnae_sec_idx ON empresa USING gin (cnaes_secundarios)",
)


def _texto(valor):
    if valor is None:
        return None
    valor = str(valor).replace("\x00", "").strip()
    return valor or None


def _sem_nulo(valor):
    """Tira o byte 0x00 de dentro de telefones e QSA, recursivamente.

    A fonte traz o escape do byte nulo no meio de alguns nomes. O Postgres o recusa
    em text quanto em jsonb, e um unico registro assim aborta o COPY do arquivo
    inteiro, por isso a limpeza acontece aqui.
    """
    if isinstance(valor, str):
        return valor.replace("\x00", "")
    if isinstance(valor, list):
        return [_sem_nulo(item) for item in valor]
    if isinstance(valor, dict):
        return {_sem_nulo(chave): _sem_nulo(item) for chave, item in valor.items()}
    return valor


def _data(valor):
    """Aceita AAAA-MM-DD e AAAAMMDD; vazio e data zerada viram nulo.

    A base traz ausencia como string vazia, nao como null, e alguns campos
    opcionais aparecem como "0000-00-00". Deixar isso chegar no COPY aborta o
    arquivo inteiro, por isso a normalizacao acontece aqui e nao no banco.
    """
    valor = _texto(valor)
    if not valor:
        return None
    digitos = "".join(c for c in valor if c.isdigit())
    if len(digitos) != 8:
        return None
    try:
        return date(int(digitos[0:4]), int(digitos[4:6]), int(digitos[6:8]))
    except ValueError:
        return None


def _decimal(valor):
    valor = _texto(valor)
    if not valor:
        return None
    if "," in valor:
        valor = valor.replace(".", "").replace(",", ".")
    try:
        return Decimal(valor)
    except InvalidOperation:
        return None


def _cnpj(valor):
    digitos = "".join(c for c in str(valor or "") if c.isdigit())
    return digitos.zfill(14) if 0 < len(digitos) <= 14 else None


def _codigo(bloco):
    if isinstance(bloco, dict):
        return _texto(bloco.get("codigo")), _texto(bloco.get("descricao"))
    return None, None


def linha(registro):
    """Converte um objeto do ndjson na tupla de COPY e nos dominios que ele cita.

    Devolve (tupla, dominios) onde dominios e uma lista de (tipo, codigo,
    descricao). As descricoes ficam fora da tabela de empresas porque sao
    poucas centenas de valores repetidos em dezenas de milhoes de linhas.
    """
    cnpj = _cnpj(registro.get("cnpj"))
    if not cnpj:
        return None, ()

    dominios = []

    for cnae in registro.get("cnaes") or ():
        if isinstance(cnae, dict):
            codigo = _texto(cnae.get("codigo"))
            if codigo:
                dominios.append(("cnae", codigo, _texto(cnae.get("descricao"))))

    qualificacao, descricao = _codigo(registro.get("qualificacao_responsavel"))
    if qualificacao:
        dominios.append(("qualificacao", qualificacao, descricao))

    motivo, descricao = _codigo(registro.get("motivo_situacao_cadastral"))
    if motivo:
        dominios.append(("motivo", motivo, descricao))

    pais, descricao = _codigo(registro.get("pais"))
    if pais:
        dominios.append(("pais", pais, descricao))

    secundarios = [c for c in (_texto(x) for x in registro.get("cnaes_secundarios") or ()) if c]
    telefones = _sem_nulo(registro.get("telefones") or [])
    qsa = _sem_nulo(registro.get("QSA") or [])
    uf = _texto(registro.get("uf"))

    tupla = (
        cnpj,
        _texto(registro.get("razao_social")),
        _texto(registro.get("nome_fantasia")),
        _texto(registro.get("situacao_cadastral")),
        _data(registro.get("data_situacao_cadastral")),
        motivo,
        _texto(registro.get("matriz_filial")),
        _data(registro.get("data_inicio_atividade")),
        _texto(registro.get("cnae_principal")),
        secundarios,
        _texto(registro.get("natureza_juridica")),
        _texto(registro.get("tipo_logradouro")),
        _texto(registro.get("logradouro")),
        _texto(registro.get("numero")),
        _texto(registro.get("complemento")),
        _texto(registro.get("bairro")),
        _texto(registro.get("cep")),
        uf[:2] if uf else None,
        _texto(registro.get("municipio")),
        _texto(registro.get("codigo_municipio")),
        _texto(registro.get("email")),
        telefones,
        _decimal(registro.get("capital_social")),
        qualificacao,
        _texto(registro.get("ente_federativo")),
        _texto(registro.get("porte_empresa")),
        _texto(registro.get("opcao_simples")),
        _data(registro.get("data_opcao_simples")),
        _data(registro.get("data_exclusao_simples")),
        _texto(registro.get("opcao_mei")),
        _data(registro.get("data_opcao_mei")),
        _data(registro.get("data_exclusao_mei")),
        _texto(registro.get("nome_cidade_exterior")),
        pais,
        _texto(registro.get("situacao_especial")),
        _data(registro.get("data_situacao_especial")),
        qsa,
    )
    return tupla, dominios
