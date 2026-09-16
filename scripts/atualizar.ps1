$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

# --limpar continua aceito para nao quebrar chamadas antigas, mas a limpeza agora e automatica.
$extras = @($args | Where-Object { $_ -ne "--limpar" })

Write-Host "== baixando e extraindo =="
docker compose --profile manual run --rm ingestor -m app.baixar --remover-zip @extras

Write-Host "== ingerindo =="
docker compose --profile manual run --rm ingestor -m app.ingerir --apagar

Write-Host "== estado =="

$porta = if ($env:PORTA_API) { $env:PORTA_API } else { "8006" }

try {
    Invoke-RestMethod "http://localhost:$porta/carga"
} catch {
    Write-Warning "Não foi possível consultar o estado da carga."
}