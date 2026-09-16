#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# --limpar continua aceito para nao quebrar chamadas antigas, mas a limpeza agora e automatica.
EXTRAS=()
for arg in "$@"; do
    [ "$arg" == "--limpar" ] || EXTRAS+=("$arg")
done

echo "== baixando e extraindo =="
docker compose --profile manual run --rm ingestor -m app.baixar --remover-zip ${EXTRAS[@]+"${EXTRAS[@]}"}

echo "== ingerindo =="
docker compose --profile manual run --rm ingestor -m app.ingerir --apagar

echo "== estado =="
curl -s "http://localhost:${PORTA_API:-8006}/carga" || true
