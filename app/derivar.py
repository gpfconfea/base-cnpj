"""Refaz as tabelas derivadas de `empresa` (hoje, `municipio`).

A ingestao chama isto no fim de cada carga. Tambem roda sozinho, para uma base
carregada antes de a tabela existir:

    docker compose --profile manual run --rm ingestor -m app.derivar
"""

import sys
import time

from app import db
from app.schema import DERIVADAS


def derivar(conn):
    inicio = time.monotonic()
    for comando in DERIVADAS:
        conn.execute(comando)
    conn.commit()
    print(f"[derivadas] municipio refeita em {time.monotonic() - inicio:.0f}s")


def main():
    with db.conectar() as conn:
        derivar(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
