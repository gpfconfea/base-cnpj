import pathlib


import psycopg

from app import config

SQL_DIR = pathlib.Path(__file__).resolve().parent.parent / "sql"


def conectar(autocommit=False):
    return psycopg.connect(config.DB_DSN, autocommit=autocommit)


def bootstrap(conn):
    for arquivo in sorted(SQL_DIR.glob("*.sql")):
        conn.execute(arquivo.read_text(encoding="utf-8"))
    conn.commit()


