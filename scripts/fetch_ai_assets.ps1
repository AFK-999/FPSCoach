<#
.SYNOPSIS
    Stage the embedded AI assets (Ollama runtime + Qwen 3 1.7B weights) for the
    FULL build. Called by build.ps1 -Flavor full.

.DESCRIPTION
    Two sources are supported, in priority order:
      1. Pre-downloaded folders pointed to by env vars:
           FPSCOACH_OLLAMA_SRC  -> folder containing the Ollama Windows runtime
           FPSCOACH_QWEN_SRC    -> folder containing the Qwen 3 1.7B model blobs
      2. Otherwise, if 'ollama' is on PATH, pull qwen3:1.7b and copy its blob
         store from %USERPROFILE%\.ollama\models.

    NOTE: Confirm redistribution licensing for the Ollama runtime and the Qwen
    weights before shipping an installer that embeds them.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$StageDir
)

$ErrorActionPreference = 'Stop'
$OllamaDir = Join-Path $StageDir "ollama"
$ModelsDir = Join-Path $StageDir "models"
New-Item -ItemType Directory -Force -Path $OllamaDir, $ModelsDir | Out-Null

# --- Ollama runtime ---------------------------------------------------------
if ($env:FPSCOACH_OLLAMA_SRC -and (Test-Path $env:FPSCOACH_OLLAMA_SRC)) {
    Write-Host "Staging Ollama runtime from $env:FPSCOACH_OLLAMA_SRC"
    Copy-Item -Recurse -Force "$env:FPSCOACH_OLLAMA_SRC\*" $OllamaDir
} else {
    Write-Warning "FPSCOACH_OLLAMA_SRC not set. Download the Ollama Windows runtime"
    Write-Warning "and set FPSCOACH_OLLAMA_SRC to its folder, or place it in $OllamaDir."
}

# --- Qwen 3 1.7B weights ----------------------------------------------------
if ($env:FPSCOACH_QWEN_SRC -and (Test-Path $env:FPSCOACH_QWEN_SRC)) {
    Write-Host "Staging Qwen 3 1.7B weights from $env:FPSCOACH_QWEN_SRC"
    Copy-Item -Recurse -Force "$env:FPSCOACH_QWEN_SRC\*" $ModelsDir
} else {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if ($null -ne $ollama) {
        Write-Host "Pulling qwen3:1.7b via local Ollama..."
        & ollama pull qwen3:1.7b
        $store = Join-Path $env:USERPROFILE ".ollama\models"
        if (Test-Path $store) {
            Copy-Item -Recurse -Force "$store\*" $ModelsDir
        }
    } else {
        Write-Warning "Could not stage Qwen weights automatically. Set FPSCOACH_QWEN_SRC"
        Write-Warning "to a folder containing the model, or install Ollama and re-run."
    }
}

Write-Host "AI assets staged under $StageDir" -ForegroundColor Green
