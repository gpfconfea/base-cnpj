import argparse
import pathlib
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import orjson
from psycopg.types.json import Jsonb

from app import config, db
from app.schema import COLUNAS, COLUNAS_JSON, DDL, INDICES, linha

INDICES_JSON = tuple(COLUNAS.index(coluna) for coluna in COLUNAS_JSON)


def _copy_sql():
    return "COPY empresa ({colunas}) FROM STDIN".format(colunas=", ".join(COLUNAS))


def carregar_arquivo(caminho):
    dominios = {}
    lidos = 0
    invalidos = 0
    with db.conectar() as conn:
        with conn.cursor() as cur, cur.copy(_copy_sql()) as copy:
            with open(caminho, "rb") as entrada:
                for bruta in entrada:
                    bruta = bruta.strip()
                    if not bruta:
                        continue
                    try:
                        registro = orjson.loads(bruta)
                    except orjson.JSONDecodeError:
                        invalidos += 1
                        continue
                    tupla, citados = linha(registro)
                    if tupla is None:
                        invalidos += 1
                        continue
                    valores = list(tupla)
                    for posicao in INDICES_JSON:
                        valores[posicao] = Jsonb(valores[posicao])
                    copy.write_row(valores)
                    lidos += 1
                    for tipo, codigo, descricao in citados:
                        if descricao and (tipo, codigo) not in dominios:
                            dominios[(tipo, codigo)] = descricao
        conn.commit()
    return caminho.name, lidos, invalidos, dominios


def _trabalho(caminho):
    return carregar_arquivo(pathlib.Path(caminho))


def preparar_tabela(conn):
    conn.execute("DROP VIEW IF EXISTS empresa CASCADE")
    conn.execute("DROP TABLE IF EXISTS empresa_a CASCADE")
    conn.execute("DROP TABLE IF EXISTS empresa_b CASCADE")
    conn.execute("DROP TABLE IF EXISTS empresa CASCADE")
    conn.execute(DDL)
    conn.commit()


def indexar(conn):
    for comando in INDICES:
        inicio = time.monotonic()
        conn.execute(comando)
        conn.commit()
        print(f"[indice] {comando[:60]}... {time.monotonic() - inicio:.0f}s")
    conn.execute("ANALYZE empresa")
    conn.commit()


def gravar_dominios(conn, dominios):
    if not dominios:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO dominio (tipo, codigo, descricao) VALUES (%s, %s, %s) "
            "ON CONFLICT (tipo, codigo) DO UPDATE SET descricao = EXCLUDED.descricao",
            [(tipo, codigo, descricao) for (tipo, codigo), descricao in dominios.items()],
        )
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Ingere os ndjson da base OpenCNPJ no PostgreSQL")
    parser.add_argument("--dir", default=None, help="diretorio com os .ndjson (padrao: DATA_DIR/ndjson)")
    parser.add_argument("--workers", type=int, default=config.WORKERS)
    parser.add_argument("--limite-arquivos", type=int, default=0, help="carrega apenas os N primeiros arquivos")
    parser.add_argument("--apagar", action="store_true", help="exclui os .ndjson extraídos ao final com sucesso")
    args = parser.parse_args()

    origem = pathlib.Path(args.dir) if args.dir else pathlib.Path(config.DATA_DIR) / "ndjson"
    arquivos = sorted(origem.glob("*.ndjson"))
    if args.limite_arquivos:
        arquivos = arquivos[: args.limite_arquivos]
    if not arquivos:
        print(f"[erro] nenhum .ndjson em {origem}", file=sys.stderr)
        return 1

    conn = db.conectar()
    db.bootstrap(conn)

    carga_id = conn.execute(
        "INSERT INTO carga (arquivos) VALUES (%s) RETURNING id",
        (len(arquivos),),
    ).fetchone()[0]
    conn.commit()

    print(f"[carga {carga_id}] arquivos={len(arquivos)}")
    inicio = time.monotonic()
    total = 0
    total_invalidos = 0
    dominios = {}

    try:
        preparar_tabela(conn)
        tarefas = [str(caminho) for caminho in arquivos]
        with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for indice, (nome, lidos, invalidos, citados) in enumerate(pool.map(_trabalho, tarefas), 1):
                total += lidos
                total_invalidos += invalidos
                dominios.update(citados)
                decorrido = time.monotonic() - inicio
                print(
                    f"[{indice}/{len(arquivos)}] {nome} +{lidos} "
                    f"(total {total}, {total / max(decorrido, 1):.0f} reg/s)"
                )

        gravar_dominios(conn, dominios)
        indexar(conn)

        conn.execute(
            "UPDATE carga SET status = 'concluida', concluida_em = now(), total_registros = %s WHERE id = %s",
            (total, carga_id),
        )
        conn.commit()
    except Exception as erro:
        conn.rollback()
        conn.execute(
            "UPDATE carga SET status = 'falhou', concluida_em = now(), erro = %s WHERE id = %s",
            (str(erro)[:2000], carga_id),
        )
        conn.commit()
        raise

    if args.apagar:
        for caminho in arquivos:
            caminho.unlink(missing_ok=True)
        print(f"[limpeza] {len(arquivos)} arquivos removidos")

    print(
        f"[carga {carga_id}] concluida: {total} registros, {total_invalidos} descartados, "
        f"{time.monotonic() - inicio:.0f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
