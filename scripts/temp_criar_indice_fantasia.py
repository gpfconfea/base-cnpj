import argparse
import os
import pathlib
import sys
import time

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from temp_criar_indices_nome import FOLGA, carregar_env, espaco_livre, estimar, gb, indices_invalidos

INDICE = "empresa_fantasia_chave_idx"


def comando_do_indice():
    from app.schema import comandos_concorrentes

    preparo, indices = comandos_concorrentes()
    return preparo, dict(indices)[INDICE]


def ja_existe(conn):
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (INDICE,)).fetchone()[0]


def main():
    parser = argparse.ArgumentParser(
        description=f"Temporario: cria {INDICE} na tabela empresa atual, sem bloquear a API"
    )
    parser.add_argument("--host", help="padrao: POSTGRES_HOST")
    parser.add_argument("--porta", help="padrao: POSTGRES_PORT")
    parser.add_argument("--estimar", action="store_true", help="so mostra estimativa e espaco livre, nao cria nada")
    parser.add_argument("--livre-gb", type=float, help="espaco livre informado a mao, em vez de medir no servidor")
    args = parser.parse_args()

    carregar_env()
    if args.host:
        os.environ["POSTGRES_HOST"] = args.host
    if args.porta:
        os.environ["POSTGRES_PORT"] = args.porta

    import psycopg

    from app import config

    preparo, comando = comando_do_indice()
    with psycopg.connect(config.DB_DSN, autocommit=True) as conn:
        invalidos = indices_invalidos(conn, [INDICE])
        if invalidos:
            print(f"[parado] {INDICE} ficou invalido numa tentativa anterior", file=sys.stderr)
            print(f"[parado] rode DROP INDEX CONCURRENTLY {INDICE}; e tente de novo", file=sys.stderr)
            return 1
        if ja_existe(conn):
            tamanho = conn.execute("SELECT pg_relation_size(%s::regclass)", (INDICE,)).fetchone()[0]
            print(f"[pronto] {INDICE} ja existe ({gb(tamanho)}), nada a fazer")
            return 0

        estimativa = estimar(conn)
        estimado = estimativa[INDICE]
        print(f"[estimativa] {estimativa['linhas']} linhas, {estimativa['ativas']} ativas")
        print(f"[estimativa] {INDICE}: {gb(estimado)} (razao social, para comparar: "
              f"{gb(estimativa['empresa_razao_chave_idx'])})")
        print(f"[estimativa] exigido livre: {gb(estimado * FOLGA)}")

        livre = args.livre_gb * 1024 ** 3 if args.livre_gb is not None else None
        if livre is None and not args.estimar:
            livre = espaco_livre(conn)
        if livre is not None:
            print(f"[disco] livre antes: {gb(livre)}")
        if args.estimar:
            return 0
        if livre < estimado * FOLGA:
            print("[parado] espaco livre menor que o dobro do estimado", file=sys.stderr)
            return 1

        for sql in preparo:
            conn.execute(sql)
        print("[preparo] pg_trgm e nome_chave prontos")

        print(f"[indice] criando {INDICE} (varre a tabela inteira; a API segue respondendo)...", flush=True)
        inicio = time.monotonic()
        conn.execute(comando)
        tamanho = conn.execute("SELECT pg_relation_size(%s::regclass)", (INDICE,)).fetchone()[0]
        print(f"[indice] {INDICE}: {gb(tamanho)} em {time.monotonic() - inicio:.0f}s")

        inicio = time.monotonic()
        conn.execute("ANALYZE empresa")
        print(f"[analyze] estatisticas atualizadas em {time.monotonic() - inicio:.0f}s")
        if args.livre_gb is None:
            print(f"[disco] livre depois: {gb(espaco_livre(conn))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
