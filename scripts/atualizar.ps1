$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

$limpar = $false
$extras = @()

if ($args.Count -gt 0 -and $args[0] -eq "--limpar") {
    $limpar = $true
    if ($args.Count -gt 1) {
        $extras = $args[1..($args.Count - 1)]
    }
} else {
    $extras = $args
}

Write-Host "== baixando e extraindo =="

if ($limpar) {
    docker compose --profile manual run --rm ingestor -m app.baixar --remover-zip @extras
} else {
    docker compose --profile manual run --rm ingestor -m app.baixar @extras
}

Write-Host "== ingerindo =="

if ($limpar) {
    docker compose --profile manual run --rm ingestor -m app.ingerir --apagar
} else {
    docker compose --profile manual run --rm ingestor -m app.ingerir
}

Write-Host "== estado =="

$porta = if ($env:PORTA_API) { $env:PORTA_API } else { "8006" }

try {
    Invoke-RestMethod "http://localhost:$porta/carga"
} catch {
    Write-Warning "Não foi possível consultar o estado da carga."
}