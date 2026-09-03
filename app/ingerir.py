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


def _copy_sql(slot):
    return "COPY empresa_{slot} ({colunas}) FROM STDIN".format(slot=slot, colunas=", ".join(COLUNAS))


def carregar_arquivo(caminho, slot):
    dominios = {}
    lidos = 0
    invalidos = 0
    with db.conectar() as conn:
        with conn.cursor() as cur, cur.copy(_copy_sql(slot)) as copy:
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


def _trabalho(argumentos):
    caminho, slot = argumentos
    return carregar_arquivo(pathlib.Path(caminho), slot)


def preparar_slot(conn, slot):
    conn.execute(f"DROP TABLE IF EXISTS empresa_{slot}")
    conn.execute(DDL.format(slot=slot))
    conn.commit()


def indexar(conn, slot):
    for comando in INDICES:
        inicio = time.monotonic()
        conn.execute(comando.format(slot=slot))
        conn.commit()
        print(f"[indice] {comando.format(slot=slot)[:60]}... {time.monotonic() - inicio:.0f}s")
    conn.execute(f"ANALYZE empresa_{slot}")
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


def publicar(conn, slot):
    """Aponta a view para o slot recem carregado dentro de uma transacao.

    A troca e o unico momento em que a consulta para, e dura o tempo de um
    CREATE OR REPLACE VIEW. Quem estiver no meio de um SELECT termina lendo o
    slot antigo, que so e descartado depois.
    """
    with conn.transaction():
        conn.execute(f"CREATE OR REPLACE VIEW empresa AS SELECT * FROM empresa_{slot}")


def main():
    parser = argparse.ArgumentParser(description="Ingere os ndjson da base OpenCNPJ no PostgreSQL")
    parser.add_argument("--dir", default=None, help="diretorio com os .ndjson (padrao: DATA_DIR/ndjson)")
    parser.add_argument("--workers", type=int, default=config.WORKERS)
    parser.add_argument("--slot", choices=("a", "b"), default=None)
    parser.add_argument("--descartar-anterior", action="store_true")
    parser.add_argument("--limite-arquivos", type=int, default=0, help="carrega apenas os N primeiros arquivos")
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

    anterior = db.slot_ativo(conn)
    slot = args.slot or db.slot_destino(conn)
    if slot == anterior:
        print(f"[erro] slot {slot} esta em uso pela view", file=sys.stderr)
        return 1

    carga_id = conn.execute(
        "INSERT INTO carga (slot, arquivos) VALUES (%s, %s) RETURNING id",
        (slot, len(arquivos)),
    ).fetchone()[0]
    conn.commit()

    print(f"[carga {carga_id}] slot={slot} anterior={anterior or '-'} arquivos={len(arquivos)}")
    inicio = time.monotonic()
    total = 0
    total_invalidos = 0
    dominios = {}

    try:
        preparar_slot(conn, slot)
        tarefas = [(str(caminho), slot) for caminho in arquivos]
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
        indexar(conn, slot)
        publicar(conn, slot)

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

    if args.descartar_anterior and anterior and anterior != slot:
        conn.execute(f"DROP TABLE IF EXISTS empresa_{anterior}")
        conn.commit()
        print(f"[limpeza] empresa_{anterior} removida")

    print(
        f"[carga {carga_id}] concluida: {total} registros, {total_invalidos} descartados, "
        f"{time.monotonic() - inicio:.0f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
