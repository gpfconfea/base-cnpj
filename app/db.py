import pathlib
import re

import psycopg

from app import config

SQL_DIR = pathlib.Path(__file__).resolve().parent.parent / "sql"


def conectar(autocommit=False):
    return psycopg.connect(config.DB_DSN, autocommit=autocommit)


def bootstrap(conn):
    for arquivo in sorted(SQL_DIR.glob("*.sql")):
        conn.execute(arquivo.read_text(encoding="utf-8"))
    conn.commit()


def slot_ativo(conn):
    existe = conn.execute("SELECT to_regclass('empresa')").fetchone()[0]
    if not existe:
        return None
    definicao = conn.execute("SELECT pg_get_viewdef('empresa'::regclass)").fetchone()[0]
    achado = re.search(r"empresa_([ab])\b", definicao)
    return achado.group(1) if achado else None


def slot_destino(conn):
    """Escreve sempre no slot que a view nao esta usando.

    E o que permite recarregar a base inteira sem tirar a consulta do ar: o
    slot antigo continua respondendo enquanto o novo e populado e indexado.
    """
    ativo = slot_ativo(conn)
    return "b" if ativo == "a" else "a"
