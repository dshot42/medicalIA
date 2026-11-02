[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [Alias("StreamlitPort")]
    [int]$FrontendPort = 5173,
    [switch]$SkipDemo,
    [switch]$NoBrowser,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ApiProcess = $null
$FrontendProcess = $null

function Write-Step([string]$Message) {
    Write-Host "[MedConnect] $Message" -ForegroundColor Cyan
}

function Assert-PortFree([int]$Port, [string]$ServiceName) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $processIds = @($listener | Select-Object -ExpandProperty OwningProcess -Unique)
        $processNames = @(
            foreach ($processId in $processIds) {
                $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
                if ($process) {
                    "$($process.ProcessName) (PID $processId)"
                }
                else {
                    "PID $processId"
                }
            }
        ) -join ", "

        $healthUrl = if ($ServiceName -eq "FastAPI") {
            "http://127.0.0.1:$Port/health"
        }
        else {
            "http://127.0.0.1:$Port/"
        }
        $existingService = $false
        try {
            $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
            $existingService = $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
        }
        catch {
            # The port is occupied, but it does not answer the expected service health check.
        }

        if ($existingService) {
            throw "$ServiceName MedConnect repond deja sur http://127.0.0.1:$Port ($processNames). Utilisez la fenetre du launcher deja ouverte, ou faites Ctrl+C dans celle-ci avant d'en relancer une autre."
        }
        throw "Le port $Port de $ServiceName est deja utilise par $processNames. Arretez ce processus ou changez le port."
    }
}

function Wait-HttpService(
    [string]$Url,
    [System.Diagnostics.Process]$Process,
    [int]$TimeoutSeconds,
    [string]$ServiceName
) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            throw "$ServiceName s'est arrete pendant son demarrage (code $($Process.ExitCode))."
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return
            }
        }
        catch [System.Net.WebException] {
            # Le service initialise encore SQLite, Qdrant ou les embeddings.
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Delai depasse pendant le demarrage de $ServiceName."
}

function Stop-ChildProcess([System.Diagnostics.Process]$Process, [string]$ServiceName) {
    if ($null -ne $Process -and -not $Process.HasExited) {
        Write-Step "Arret de $ServiceName..."
        # /T arrete egalement les eventuels sous-processus (par exemple esbuild
        # lance par Vite). Les services ne doivent jamais survivre au launcher.
        & "$env:SystemRoot\System32\taskkill.exe" /PID $Process.Id /T /F 2>$null | Out-Null
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
        $Process.WaitForExit(5000) | Out-Null
    }
}

Set-Location -LiteralPath $ProjectRoot
$Host.UI.RawUI.WindowTitle = "MedConnect AI"

try {
    Write-Host ""
    Write-Host "  MedConnect AI - lanceur local" -ForegroundColor Green
    Write-Host "  Prototype d'aide au raisonnement, pas un dispositif medical." -ForegroundColor Yellow
    Write-Host ""

    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Environnement .venv introuvable. Consultez README.md pour l'installation."
    }

    Write-Step "Verification de l'environnement Python et du modele local..."
    $configurationCommand = @"
import json
import llama_cpp
from app.core.config import Settings
s = Settings()
print(json.dumps({
    'provider': s.llm_provider,
    'model_path': str(s.gguf_model_path),
    'context_size': s.gguf_context_size,
    'gpu_layers': s.gguf_gpu_layers,
    'gpu_offload_supported': llama_cpp.llama_supports_gpu_offload(),
    'investigation_mode': s.investigation_mode,
}))
"@
    $configurationRaw = & $Python -c $configurationCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Impossible de lire la configuration MedConnect."
    }
    $configuration = $configurationRaw | ConvertFrom-Json
    if ($configuration.provider -ne "gguf") {
        throw "MEDCONNECT_LLM_PROVIDER doit valoir 'gguf' (valeur actuelle : '$($configuration.provider)')."
    }
    if (-not (Test-Path -LiteralPath $configuration.model_path -PathType Leaf)) {
        throw "Modele GGUF introuvable : $($configuration.model_path)"
    }
    if ($configuration.gpu_layers -ne -1) {
        throw "MEDCONNECT_GGUF_GPU_LAYERS doit valoir -1 pour decharger toutes les couches sur le GPU."
    }
    if (-not $configuration.gpu_offload_supported) {
        throw "llama-cpp-python ne contient pas le backend CUDA requis par le mode GPU. Executez .\install-gpu.bat puis relancez l'application."
    }

    & $Python -m pip check | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Les dependances de .venv sont incompletes. Executez pip install -r requirements.txt."
    }

    Assert-PortFree $ApiPort "FastAPI"
    Assert-PortFree $FrontendPort "Vue"

    $NodeCommand = Get-Command node -ErrorAction SilentlyContinue
    $ViteScript = Join-Path $ProjectRoot "frontend\node_modules\vite\bin\vite.js"
    if ($null -eq $NodeCommand) {
        throw "Node.js est requis pour l'interface Vue. Installez Node.js 20 ou plus recent."
    }
    if (-not (Test-Path -LiteralPath $ViteScript -PathType Leaf)) {
        throw "Dependances Vue absentes. Executez 'npm install' dans le dossier frontend."
    }

    Write-Host "  Python : $Python"
    Write-Host "  GGUF   : $($configuration.model_path)"
    Write-Host "  Contexte : $($configuration.context_size) tokens"
    Write-Host "  Mode   : $($configuration.investigation_mode) (GPU CUDA, toutes les couches)"
    Write-Host "  API    : http://127.0.0.1:$ApiPort"
    Write-Host "  UI Vue : http://127.0.0.1:$FrontendPort"

    if ($CheckOnly) {
        Write-Host ""
        Write-Host "Configuration valide." -ForegroundColor Green
        exit 0
    }

    if (-not $SkipDemo) {
        Write-Step "Chargement idempotent des dossiers fictifs..."
        & $Python -m scripts.load_clinical_demo | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Le chargement des donnees fictives a echoue."
        }
    }

    Write-Step "Demarrage de FastAPI..."
    $ApiProcess = Start-Process `
        -FilePath $Python `
        -ArgumentList @(
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", "$ApiPort"
        ) `
        -WorkingDirectory $ProjectRoot `
        -NoNewWindow `
        -PassThru
    # /health is exposed only after the FastAPI lifespan has initialized
    # SQLite, Qdrant and the embedding model.
    Wait-HttpService "http://127.0.0.1:$ApiPort/health" $ApiProcess 120 "FastAPI"

    Write-Step "Demarrage de l'interface Vue..."
    $env:MEDCONNECT_API_URL = "http://127.0.0.1:$ApiPort"
    $FrontendProcess = Start-Process `
        -FilePath $NodeCommand.Source `
        -ArgumentList @(
            $ViteScript,
            "--host", "127.0.0.1",
            "--port", "$FrontendPort",
            "--strictPort",
            "--clearScreen", "false",
            "--configLoader", "runner"
        ) `
        -WorkingDirectory (Join-Path $ProjectRoot "frontend") `
        -NoNewWindow `
        -PassThru
    Wait-HttpService "http://127.0.0.1:$FrontendPort/" $FrontendProcess 120 "Vue"

    $interfaceUrl = "http://127.0.0.1:$FrontendPort"
    if (-not $NoBrowser) {
        Start-Process $interfaceUrl
    }

    Write-Host ""
    Write-Host "MedConnect AI est pret : $interfaceUrl" -ForegroundColor Green
    Write-Host "Documentation API : http://127.0.0.1:$ApiPort/docs"
    Write-Host "FastAPI PID        : $($ApiProcess.Id)"
    Write-Host "Vue PID            : $($FrontendProcess.Id)"
    Write-Host "Les sorties FastAPI et Vue restent visibles dans cette console."
    Write-Host "Ctrl+C ou la fermeture de cette fenetre arrete tous les services." -ForegroundColor Yellow

    while (-not $ApiProcess.HasExited -and -not $FrontendProcess.HasExited) {
        Start-Sleep -Seconds 1
    }
    if ($ApiProcess.HasExited) {
        throw "FastAPI s'est arrete de maniere inattendue (code $($ApiProcess.ExitCode))."
    }
    throw "L'interface Vue s'est arretee de maniere inattendue (code $($FrontendProcess.ExitCode))."
}
catch {
    Write-Host ""
    Write-Host "Echec du lancement : $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Stop-ChildProcess $FrontendProcess "Vue"
    Stop-ChildProcess $ApiProcess "FastAPI"
}
