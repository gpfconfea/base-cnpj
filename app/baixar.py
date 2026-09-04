import argparse
import os
import pathlib
import sys
import time
import zipfile

import requests

from app import config

BLOCO = 1024 * 1024
TENTATIVAS = int(os.getenv("DOWNLOAD_TENTATIVAS", "5"))
ESPERA = 15


def _tamanho_remoto(url):
    cabecalho = requests.head(url, timeout=60, allow_redirects=True)
    cabecalho.raise_for_status()
    return int(cabecalho.headers.get("Content-Length") or 0)


def _tentativa(url, destino, total):
    """Baixa o arquivo inteiro e devolve quantos bytes foram gravados."""
    baixado = 0
    with requests.get(url, stream=True, timeout=(60, 300)) as resposta:
        resposta.raise_for_status()
        with open(destino, "wb") as saida:
            for pedaco in resposta.iter_content(BLOCO):
                if not pedaco:
                    continue
                saida.write(pedaco)
                baixado += len(pedaco)
                if total:
                    pct = baixado * 100 // total
                    print(f"\r[baixar] {pct}% ({baixado}/{total})", end="", file=sys.stderr)
                else:
                    print(f"\r[baixar] {baixado} bytes", end="", file=sys.stderr)
    print("", file=sys.stderr)
    return baixado


def baixar(url, destino):
    """Baixa sempre do zero: a origem nao honra Range e retomar corrompe o zip."""
    destino = pathlib.Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(destino.suffix + ".parcial")

    total = _tamanho_remoto(url)
    for antigo in (destino, parcial):
        if antigo.exists():
            print(f"[baixar] removendo {antigo.name} anterior ({antigo.stat().st_size} bytes)")
            antigo.unlink()

    erro = None
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            baixado = _tentativa(url, parcial, total)
            if not total or baixado == total:
                break
            erro = RuntimeError(f"recebido {baixado} de {total} bytes")
        except (requests.RequestException, OSError) as exc:
            erro = exc
        if tentativa == TENTATIVAS:
            parcial.unlink(missing_ok=True)
            raise RuntimeError(f"download falhou apos {TENTATIVAS} tentativas: {erro}")
        print(f"[baixar] tentativa {tentativa}/{TENTATIVAS} falhou ({erro}), nova em {ESPERA}s")
        time.sleep(ESPERA)

    parcial.replace(destino)
    print(f"[baixar] concluido ({destino.stat().st_size} bytes)")
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
