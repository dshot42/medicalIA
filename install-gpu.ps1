[CmdletBinding()]
param(
    [switch]$SkipDependencies,
    [switch]$SourceBuild,
    [switch]$CheckOnly,
    [string]$CudaVersion = "13.2",
    [string]$CudaArchitecture = "120"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements.txt"
$requirementLines = Get-Content -LiteralPath $Requirements
$llamaCppRequirement = $requirementLines | Where-Object { $_ -match '^llama-cpp-python==' }
if ($llamaCppRequirement.Count -ne 1) {
    throw "requirements.txt doit contenir exactement une version epinglee de llama-cpp-python."
}
$LlamaCppVersion = ($llamaCppRequirement -split '==', 2)[1]

function Write-Step([string]$Message) {
    Write-Host "[MedConnect] $Message" -ForegroundColor Cyan
}

function Invoke-Python([string[]]$PythonArguments) {
    & $Python @PythonArguments
    if ($LASTEXITCODE -ne 0) {
        throw "La commande Python a echoue (code $LASTEXITCODE)."
    }
}

function Test-CudaBackend {
    $diagnosticCommand = @"
import json
from pathlib import Path
import llama_cpp

library_dir = Path(llama_cpp.__file__).resolve().parent / 'lib'
print(json.dumps({
    'version': getattr(llama_cpp, '__version__', 'unknown'),
    'gpu_offload_supported': bool(llama_cpp.llama_supports_gpu_offload()),
    'cuda_library_present': any(library_dir.glob('ggml-cuda*.dll')),
    'library_dir': str(library_dir),
}))
"@
    $rawDiagnostic = & $Python -c $diagnosticCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Impossible d'importer llama-cpp-python dans .venv."
    }
    $diagnostic = $rawDiagnostic | Select-Object -Last 1 | ConvertFrom-Json
    Write-Host "  llama-cpp-python : $($diagnostic.version)"
    Write-Host "  Bibliotheques    : $($diagnostic.library_dir)"
    Write-Host "  ggml-cuda.dll    : $($diagnostic.cuda_library_present)"
    Write-Host "  GPU offload      : $($diagnostic.gpu_offload_supported)"
    return [bool]($diagnostic.cuda_library_present -and $diagnostic.gpu_offload_supported)
}

Set-Location -LiteralPath $ProjectRoot

try {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        Write-Step "Creation de l'environnement Python 3.11..."
        $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($null -eq $pyLauncher) {
            throw "Python Launcher est introuvable. Installez Python 3.11 (64 bits), puis relancez ce script."
        }
        & $pyLauncher.Source -3.11 -m venv (Join-Path $ProjectRoot ".venv")
        if ($LASTEXITCODE -ne 0) {
            throw "La creation de .venv avec Python 3.11 a echoue."
        }
    }

    if ($CheckOnly) {
        Write-Step "Verification du backend CUDA..."
        if (-not (Test-CudaBackend)) {
            throw "Le backend CUDA n'est pas actif. Executez .\install-gpu.ps1."
        }
        Write-Host "Backend CUDA valide." -ForegroundColor Green
        exit 0
    }

    if (-not $SkipDependencies) {
        Write-Step "Installation des dependances du projet (hors binaire CPU llama-cpp-python)..."
        $projectDependencies = $requirementLines | Where-Object {
            $_ -and $_ -notmatch '^\s*#' -and $_ -notmatch '^llama-cpp-python=='
        }
        $dependencyArguments = @("-m", "pip", "install") + [string[]]$projectDependencies
        Invoke-Python $dependencyArguments
    }

    if ($SourceBuild) {
        $cudaRoot = $env:CUDA_PATH
        if (-not $cudaRoot) {
            $cudaRoot = Join-Path $env:ProgramFiles "NVIDIA GPU Computing Toolkit\CUDA\v$CudaVersion"
        }
        $nvcc = Join-Path $cudaRoot "bin\nvcc.exe"
        if (-not (Test-Path -LiteralPath $nvcc -PathType Leaf)) {
            throw "Compilateur CUDA introuvable : $nvcc"
        }

        Write-Step "Compilation native CUDA $CudaVersion pour l'architecture $CudaArchitecture..."
        $env:CMAKE_ARGS = "-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=$CudaArchitecture"
        $env:CUDACXX = $nvcc
        $env:CUDAToolkit_ROOT = $cudaRoot
        $env:FORCE_CMAKE = "1"
        $env:CMAKE_BUILD_PARALLEL_LEVEL = [Math]::Min([Environment]::ProcessorCount, 8).ToString()
        Invoke-Python @(
            "-m", "pip", "install", "llama-cpp-python==$LlamaCppVersion",
            "--force-reinstall", "--no-cache-dir", "--no-deps",
            "--no-binary=llama-cpp-python"
        )
    }
    else {
        $cudaTag = "cu" + ($CudaVersion -replace "\.", "")
        $cudaIndex = "https://abetlen.github.io/llama-cpp-python/whl/$cudaTag"
        Write-Step "Installation du wheel officiel CUDA $CudaVersion..."
        try {
            Invoke-Python @(
                "-m", "pip", "install", "llama-cpp-python==$LlamaCppVersion",
                "--index-url", $cudaIndex,
                "--force-reinstall", "--no-cache-dir", "--no-deps"
            )
        }
        catch {
            throw "$($_.Exception.Message) Relancez avec .\install-gpu.ps1 -SourceBuild pour compiler localement."
        }
    }

    Write-Step "Validation du backend installe..."
    if (-not (Test-CudaBackend)) {
        throw "L'installation s'est terminee, mais llama-cpp-python ne fournit toujours pas CUDA."
    }

    Invoke-Python @("-m", "pip", "check")
    Write-Host ""
    Write-Host "Installation GPU terminee. Vous pouvez lancer .\launcher.bat." -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "Echec de l'installation GPU : $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
