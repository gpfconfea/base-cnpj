#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

LIMPAR=""
if [ "${1:-}" == "--limpar" ]; then
    LIMPAR="1"
    shift
fi

echo "== baixando e extraindo =="
if [ -n "$LIMPAR" ]; then
    docker compose --profile manual run --rm ingestor -m app.baixar --remover-zip "$@"
else
    docker compose --profile manual run --rm ingestor -m app.baixar "$@"
fi

echo "== ingerindo =="
if [ -n "$LIMPAR" ]; then
    docker compose --profile manual run --rm ingestor -m app.ingerir --apagar
else
    docker compose --profile manual run --rm ingestor -m app.ingerir
fi

echo "== estado =="
curl -s "http://localhost:${PORTA_API:-8006}/carga" || true
