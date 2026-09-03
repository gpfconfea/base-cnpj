import argparse
import os
import pathlib
import sys
import zipfile

import requests

from app import config

BLOCO = 1024 * 1024


def baixar(url, destino):
    destino = pathlib.Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    cabecalho = requests.head(url, timeout=60, allow_redirects=True)
    cabecalho.raise_for_status()
    total = int(cabecalho.headers.get("Content-Length") or 0)
    aceita_range = cabecalho.headers.get("Accept-Ranges", "").lower() == "bytes"

    baixado = destino.stat().st_size if destino.exists() else 0
    if total and baixado == total:
        print(f"[baixar] {destino.name} completo ({total} bytes), nada a fazer")
        return destino

    headers = {}
    modo = "wb"
    if baixado and aceita_range:
        headers["Range"] = f"bytes={baixado}-"
        modo = "ab"
        print(f"[baixar] retomando de {baixado} bytes")
    elif baixado:
        print("[baixar] servidor nao aceita retomada, recomecando")
        baixado = 0

    with requests.get(url, stream=True, timeout=(60, 300), headers=headers) as resposta:
        resposta.raise_for_status()
        with open(destino, modo) as saida:
            for pedaco in resposta.iter_content(BLOCO):
                saida.write(pedaco)
                baixado += len(pedaco)
                if total:
                    pct = baixado * 100 // total
                    print(f"\r[baixar] {pct}% ({baixado}/{total})", end="", file=sys.stderr)
    print("", file=sys.stderr)
    return destino


def extrair(zip_path, destino):
    destino = pathlib.Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as pacote:
        membros = [m for m in pacote.infolist() if not m.is_dir()]
        for indice, membro in enumerate(membros, 1):
            nome = pathlib.Path(membro.filename).name
            if not nome.endswith(".ndjson"):
                continue
            alvo = destino / nome
            if alvo.exists() and alvo.stat().st_size == membro.file_size:
                continue
            with pacote.open(membro) as origem, open(alvo, "wb") as saida:
                while True:
                    pedaco = origem.read(BLOCO)
                    if not pedaco:
                        break
                    saida.write(pedaco)
            print(f"[extrair] {indice}/{len(membros)} {nome}")
    return destino


def main():
    parser = argparse.ArgumentParser(description="Baixa e extrai a base OpenCNPJ")
    parser.add_argument("--url", default=config.FONTE_ZIP)
    parser.add_argument("--dir", default=config.DATA_DIR)
    parser.add_argument("--pular-download", action="store_true")
    parser.add_argument("--pular-extracao", action="store_true")
    parser.add_argument("--remover-zip", action="store_true")
    args = parser.parse_args()

    base = pathlib.Path(args.dir)
    zip_path = base / "data.zip"
    ndjson_dir = base / "ndjson"

    if not args.pular_download:
        baixar(args.url, zip_path)
    if not args.pular_extracao:
        extrair(zip_path, ndjson_dir)
        if args.remover_zip:
            os.remove(zip_path)

    arquivos = sorted(ndjson_dir.glob("*.ndjson"))
    print(f"[pronto] {len(arquivos)} arquivos em {ndjson_dir}")


if __name__ == "__main__":
    main()
