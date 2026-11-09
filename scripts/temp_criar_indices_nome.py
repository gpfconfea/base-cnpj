import argparse
import os
import pathlib
import sys
import time

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

AMOSTRA_PCT = 0.5
BYTES_POR_TRIGRAMA = 3
FOLGA = 2


def carregar_env(caminho=RAIZ / ".env"):
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip())


def gb(valor):
    return f"{valor / 1024 ** 3:.2f} GB"


def estimar(conn):
    linhas_tabela = conn.execute("SELECT reltuples FROM pg_class WHERE oid = 'empresa'::regclass").fetchone()[0]
    amostra = conn.execute(
        f"""
        SELECT count(*),
               count(*) FILTER (WHERE situacao_cadastral = 'Ativa'),
               coalesce(sum(octet_length(razao_social) + 2) FILTER (WHERE situacao_cadastral = 'Ativa'), 0),
               coalesce(sum(octet_length(nome_fantasia) + 2) FILTER (WHERE situacao_cadastral = 'Ativa'), 0),
               coalesce(sum(((octet_length(razao_social) + 1 + 7) / 8) * 8 + 12)
                   FILTER (WHERE situacao_cadastral = 'Ativa'), 0)
        FROM empresa TABLESAMPLE SYSTEM ({AMOSTRA_PCT}) REPEATABLE (1)
        """
    ).fetchone()
    total, ativas, razao, fantasia, chave = amostra
    escala = linhas_tabela / total if total else 0
    return {
        "linhas": int(linhas_tabela),
        "ativas": int(ativas * escala),
        "empresa_razao_trgm_idx": razao * escala * BYTES_POR_TRIGRAMA,
        "empresa_fantasia_trgm_idx": fantasia * escala * BYTES_POR_TRIGRAMA,
        "empresa_razao_chave_idx": chave * escala / 0.9,
    }


def espaco_livre(conn):
    diretorio = conn.execute("SHOW data_directory").fetchone()[0]
    with conn.transaction(force_rollback=True):
        conn.execute("CREATE TEMP TABLE df_saida (linha text)")
        conn.execute(f"COPY df_saida FROM PROGRAM 'df -kP {diretorio}'")
        saida = [linha for (linha,) in conn.execute("SELECT linha FROM df_saida").fetchall()]
    return int(saida[-1].split()[3]) * 1024


def indices_invalidos(conn, nomes):
    return [
        nome
        for (nome,) in conn.execute(
            "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = ANY(%s) AND NOT i.indisvalid",
            (list(nomes),),
        ).fetchall()
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Temporario: cria na tabela empresa atual os indices de busca por nome, sem bloquear a API"
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
    from app.schema import comandos_concorrentes

    preparo, indices = comandos_concorrentes()
    with psycopg.connect(config.DB_DSN, autocommit=True) as conn:
        estimativa = estimar(conn)
        estimado = sum(estimativa[nome] for nome, _ in indices)
        print(f"[estimativa] {estimativa['linhas']} linhas, {estimativa['ativas']} ativas")
        for nome, _ in indices:
            print(f"[estimativa] {nome}: {gb(estimativa[nome])}")
        print(f"[estimativa] total: {gb(estimado)}, exigido livre: {gb(estimado * FOLGA)}")

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

        invalidos = indices_invalidos(conn, [nome for nome, _ in indices])
        if invalidos:
            print(f"[parado] indices invalidos de uma tentativa anterior: {', '.join(invalidos)}", file=sys.stderr)
            print("[parado] apague-os com DROP INDEX CONCURRENTLY e rode de novo", file=sys.stderr)
            return 1

        for comando in preparo:
            conn.execute(comando)
        print("[preparo] pg_trgm e nome_chave prontos")

        for nome, comando in indices:
            inicio = time.monotonic()
            conn.execute(comando)
            tamanho = conn.execute("SELECT pg_relation_size(%s::regclass)", (nome,)).fetchone()[0]
            print(f"[indice] {nome}: {gb(tamanho)} em {time.monotonic() - inicio:.0f}s")

        conn.execute("ANALYZE empresa")
        if args.livre_gb is None:
            print(f"[disco] livre depois: {gb(espaco_livre(conn))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
