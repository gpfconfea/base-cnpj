[CmdletBinding()]
param(
    [switch]$Rollback,
    [switch]$Simular,
    [switch]$SemCofreDeCredenciais,
    [int]$TempoLimiteSaude = 90
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$Servico = "api"
$Imagem = "base-cnpj-api:latest"
$ImagemAnterior = "base-cnpj-api:anterior"
$PastaDeploy = ".deploy"
$ArquivoImplantado = Join-Path $PastaDeploy "implantado"
$ArquivoAnterior = Join-Path $PastaDeploy "anterior"

function Usar-DockerSemCofreDeCredenciais {
    param([string]$Motivo)
    $pasta = Join-Path $env:TEMP "base-cnpj-docker-ssh"
    New-Item -ItemType Directory -Force $pasta | Out-Null
    Set-Content -Path (Join-Path $pasta "config.json") -Value '{"auths":{"registro-inexistente.local":{}}}' -Encoding ascii
    $env:DOCKER_CONFIG = $pasta
    Write-Host "${Motivo}: o Docker vai baixar as imagens publicas sem o cofre de credenciais do Windows." -ForegroundColor DarkGray
}

if ($SemCofreDeCredenciais) {
    Usar-DockerSemCofreDeCredenciais "Opcao -SemCofreDeCredenciais"
}
elseif ($env:SSH_CONNECTION -and -not $env:DOCKER_CONFIG) {
    Usar-DockerSemCofreDeCredenciais "Sessao SSH"
}

function Escrever-Passo {
    param([string]$Texto)
    Write-Host ""
    Write-Host "==> $Texto" -ForegroundColor Cyan
}

function Executar {
    param([string]$Descricao, [scriptblock]$Comando)
    Escrever-Passo $Descricao
    if ($Simular) {
        Write-Host "    [simulacao] $($ExecutionContext.InvokeCommand.ExpandString($Comando.ToString().Trim()))" -ForegroundColor DarkGray
        return
    }
    & $Comando
    if ($LASTEXITCODE -ne 0) {
        throw "Falhou: $Descricao (codigo $LASTEXITCODE)"
    }
}

function Explicar-ErroDeCredenciais {
    Write-Host ""
    Write-Host "O Docker nao conseguiu ler o cofre de credenciais do Windows." -ForegroundColor Yellow
    Write-Host "  Esse cofre so existe na sessao aberta na tela do servidor. Por SSH, agendador de tarefas ou servico,"
    Write-Host "  o Windows responde 'Uma sessao de inicio de sessao especificada nao existe'."
    Write-Host "  A imagem base (python:3.12-slim) e publica e nao precisa de senha."
    Write-Host ""
    Write-Host "Como resolver:" -ForegroundColor Yellow
    if ($env:DOCKER_CONFIG) {
        Write-Host "  A variavel DOCKER_CONFIG ja estava definida ($env:DOCKER_CONFIG) e o script a respeitou."
        Write-Host "  Rode de novo com -SemCofreDeCredenciais para usar a configuracao temporaria do script."
    }
    else {
        Write-Host "  Rode de novo com -SemCofreDeCredenciais. Nada no seu usuario e alterado."
    }
    Write-Host "  Ou rode o deploy numa sessao aberta na tela do servidor (console ou area de trabalho remota)."
}

function Construir-Imagem {
    Escrever-Passo "Construindo a imagem da API (a versao atual continua no ar)"
    if ($Simular) {
        Write-Host "    [simulacao] docker compose build $Servico" -ForegroundColor DarkGray
        return
    }
    $saida = @()
    cmd /c "docker compose build $Servico 2>&1" | Tee-Object -Variable saida | Out-Host
    $codigo = $LASTEXITCODE
    if ($codigo -ne 0) {
        if (($saida -join "`n") -match "error getting credentials") {
            Explicar-ErroDeCredenciais
            throw "Build interrompida pelo cofre de credenciais. A versao atual continua no ar."
        }
        throw "Falhou a construcao da imagem (codigo $codigo). A versao atual continua no ar."
    }
}

function Ler-PortaApi {
    if ($env:PORTA_API) { return $env:PORTA_API }
    if (Test-Path ".env") {
        $linha = Get-Content ".env" | Where-Object { $_ -match "^\s*PORTA_API\s*=" } | Select-Object -First 1
        if ($linha) { return ($linha -split "=", 2)[1].Trim() }
    }
    return "8006"
}

function Aguardar-Saude {
    $url = "http://localhost:$(Ler-PortaApi)/health"
    Escrever-Passo "Conferindo a saude da API em $url (ate $TempoLimiteSaude s)"
    if ($Simular) {
        Write-Host "    [simulacao] GET $url ate responder 200" -ForegroundColor DarkGray
        return $true
    }
    $limite = (Get-Date).AddSeconds($TempoLimiteSaude)
    $ultimoErro = ""
    while ((Get-Date) -lt $limite) {
        try {
            $resposta = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
            if ($resposta.StatusCode -eq 200) {
                Write-Host "    API respondeu: $($resposta.Content)" -ForegroundColor Green
                return $true
            }
            $ultimoErro = "codigo $($resposta.StatusCode)"
        }
        catch {
            $ultimoErro = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    Write-Host "    A API nao respondeu a tempo. Ultimo erro: $ultimoErro" -ForegroundColor Yellow
    Write-Host "    Ultimas linhas do log:" -ForegroundColor Yellow
    docker compose logs --tail 60 $Servico | Out-Host
    return $false
}

function Trocar-Conteiner {
    Executar "Trocando o conteiner da API (fora do ar por alguns segundos)" {
        docker compose up -d --no-deps --no-build --force-recreate $Servico
    }
}

function Existe-Imagem {
    param([string]$Nome)
    docker image inspect $Nome *> $null
    return ($LASTEXITCODE -eq 0)
}

function Ler-Arquivo {
    param([string]$Caminho)
    if (Test-Path $Caminho) { return (Get-Content $Caminho -Raw).Trim() }
    return "desconhecida"
}

function Versao-DoCodigo {
    if (-not (Test-Path ".git")) { return "sem git" }
    $commit = git rev-parse --short HEAD 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $commit) { return "sem git" }
    return $commit.Trim()
}

function Voltar-Versao {
    if (-not $Simular -and -not (Existe-Imagem $ImagemAnterior)) {
        throw "Nao ha imagem anterior ($ImagemAnterior) para voltar. Corrija o problema e rode o deploy de novo."
    }
    Executar "Voltando para a imagem anterior" { docker tag $ImagemAnterior $Imagem }
    Trocar-Conteiner
    if (-not (Aguardar-Saude)) {
        throw "A versao anterior tambem nao ficou saudavel. Veja o log com 'docker compose logs $Servico'."
    }
    if (-not $Simular) {
        Copy-Item $ArquivoAnterior $ArquivoImplantado -Force -ErrorAction SilentlyContinue
    }
}

function Conferir-Docker {
    if ($Simular) { return }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "O Docker nao esta respondendo. Abra o Docker Desktop (ou inicie o servico) e tente de novo."
    }
    $ingestor = @(docker ps -q --filter "name=^base-cnpj-ingestor$") | Where-Object { $_ }
    if ($ingestor.Count -gt 0) {
        Write-Host "    Ha uma carga em andamento. Ela continua com a versao em que comecou." -ForegroundColor Yellow
    }
}

function Mostrar-OQueVaiSubir {
    param([string]$NoAr, [string]$Novo)
    Escrever-Passo "O que vai subir"
    Write-Host "    No ar: $NoAr. Codigo nesta pasta: $Novo."
    if ($Novo -eq "sem git") { return }
    $alterados = @(git status --porcelain --untracked-files=no) | Where-Object { $_ }
    if ($alterados.Count -gt 0) {
        Write-Host "    Aviso: ha arquivos alterados em relacao ao commit $Novo. Eles entram na imagem assim mesmo:" -ForegroundColor Yellow
        $alterados | ForEach-Object { Write-Host "      $_" -ForegroundColor Yellow }
    }
    if ($NoAr -ne "desconhecida" -and $NoAr -ne $Novo) {
        $commits = @(git log --oneline "$NoAr..HEAD" 2>$null) | Where-Object { $_ }
        $commits | ForEach-Object { Write-Host "    $_" }
    }
}

if ($Simular) {
    Write-Host "Modo simulacao: nenhum comando que altera o servidor sera executado." -ForegroundColor Yellow
}

$inicio = Get-Date
New-Item -ItemType Directory -Force $PastaDeploy | Out-Null
Conferir-Docker

if ($Rollback) {
    Escrever-Passo "Rollback manual para a imagem anterior (versao $(Ler-Arquivo $ArquivoAnterior))"
    Voltar-Versao
    Write-Host ""
    Write-Host "Rollback concluido. O codigo da pasta nao foi alterado." -ForegroundColor Green
    return
}

$versaoNoAr = Ler-Arquivo $ArquivoImplantado
$versaoNova = Versao-DoCodigo
Mostrar-OQueVaiSubir -NoAr $versaoNoAr -Novo $versaoNova

Escrever-Passo "Guardando a imagem atual para rollback"
if ($Simular) {
    Write-Host "    [simulacao] docker tag $Imagem $ImagemAnterior" -ForegroundColor DarkGray
}
elseif (Existe-Imagem $Imagem) {
    docker tag $Imagem $ImagemAnterior
    if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel guardar a imagem atual." }
    Set-Content -Path $ArquivoAnterior -Value $versaoNoAr -Encoding ascii
    Write-Host "    Imagem atual guardada como $ImagemAnterior."
}
else {
    Write-Host "    Nao ha imagem atual. Se a versao nova falhar, nao havera para onde voltar." -ForegroundColor Yellow
}

Construir-Imagem
Trocar-Conteiner

if (-not (Aguardar-Saude)) {
    Write-Host ""
    Write-Host "A versao nova ($versaoNova) nao ficou saudavel. Voltando para a imagem anterior..." -ForegroundColor Yellow
    Voltar-Versao
    Write-Host ""
    Write-Host "Deploy desfeito: a versao anterior esta no ar de novo. Corrija o problema e rode o deploy outra vez." -ForegroundColor Red
    exit 1
}

if (-not $Simular) {
    Set-Content -Path $ArquivoImplantado -Value $versaoNova -Encoding ascii
    docker image prune -f | Out-Null
}

$duracao = [int]((Get-Date) - $inicio).TotalSeconds
Write-Host ""
Write-Host "Deploy concluido em $duracao s. Versao no ar: $versaoNova." -ForegroundColor Green
