#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== baixando e extraindo =="
docker compose --profile manual run --rm ingestor -m app.baixar "$@"

echo "== ingerindo =="
docker compose --profile manual run --rm ingestor -m app.ingerir

echo "== estado =="
curl -s "http://localhost:${PORTA_API:-8006}/carga" || true
