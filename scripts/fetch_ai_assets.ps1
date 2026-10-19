<#
.SYNOPSIS
    Fully automatic staging of the embedded AI assets (Ollama Windows runtime +
    Qwen 3 1.7B weights) for the FULL build. Called by build.ps1 -Flavor full.

.DESCRIPTION
    Runs end-to-end with NO prerequisites and NO required environment variables:
      1. Downloads the official Ollama Windows runtime zip and extracts it.
      2. Starts that freshly downloaded Ollama against an ISOLATED model store
         (the stage folder only), on a non-default port, and pulls ONLY
         qwen3:1.7b into it - so no unrelated models from a user's machine are
         ever bundled.
      3. Verifies both the runtime and the model actually landed; if anything is
         missing it throws a FATAL error so the build aborts (never produces an
         empty, Lite-like "full" package).

    Optional offline overrides (not required): set FPSCOACH_OLLAMA_SRC and/or
    FPSCOACH_QWEN_SRC to pre-downloaded folders to skip the corresponding
    download. These are still verified before the build continues.

    NOTE: Confirm redistribution licensing for the Ollama runtime and the Qwen
    weights before shipping an installer that embeds them.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$StageDir,
    [string]$Model = "qwen3:1.7b",
    [string]$OllamaUrl = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip",
    [string]$OllamaHost = "127.0.0.1:11435",
    [int]$MinModelBytes = 104857600  # 100 MB sanity floor for the staged weights
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # faster Invoke-WebRequest for big files

$OllamaDir = Join-Path $StageDir "ollama"
$ModelsDir = Join-Path $StageDir "models"

# Start from a clean stage so a previous partial run cannot look "complete".
Remove-Item -Recurse -Force $StageDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $OllamaDir, $ModelsDir | Out-Null

# --- 1. Ollama runtime ------------------------------------------------------
if ($env:FPSCOACH_OLLAMA_SRC -and (Test-Path $env:FPSCOACH_OLLAMA_SRC)) {
    Write-Host "Using pre-downloaded Ollama runtime from $env:FPSCOACH_OLLAMA_SRC"
    Copy-Item -Recurse -Force "$env:FPSCOACH_OLLAMA_SRC\*" $OllamaDir
} else {
    $zip = Join-Path $StageDir "ollama-windows-amd64.zip"
    Write-Host "Downloading Ollama Windows runtime..."
    Invoke-WebRequest -Uri $OllamaUrl -OutFile $zip -UseBasicParsing
    Write-Host "Extracting Ollama runtime..."
    Expand-Archive -Path $zip -DestinationPath $OllamaDir -Force
    Remove-Item $zip -Force
}

$OllamaExe = Get-ChildItem -Path $OllamaDir -Filter ollama.exe -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $OllamaExe) {
    throw "FATAL: Ollama runtime was not staged - ollama.exe not found under $OllamaDir. Aborting Full build."
}
$OllamaExe = $OllamaExe.FullName

# --- 2. Qwen 3 1.7B weights (pulled into an ISOLATED store) ------------------
if ($env:FPSCOACH_QWEN_SRC -and (Test-Path $env:FPSCOACH_QWEN_SRC)) {
    Write-Host "Using pre-downloaded model weights from $env:FPSCOACH_QWEN_SRC"
    Copy-Item -Recurse -Force "$env:FPSCOACH_QWEN_SRC\*" $ModelsDir
} else {
    # Isolate the store to the stage dir so ONLY the model we pull is bundled.
    $env:OLLAMA_MODELS = $ModelsDir
    $env:OLLAMA_HOST = $OllamaHost
    Write-Host "Starting a temporary Ollama server to pull $Model ..."
    $serve = Start-Process -FilePath $OllamaExe -ArgumentList "serve" -PassThru -WindowStyle Hidden
    try {
        $ready = $false
        for ($i = 0; $i -lt 30; $i++) {
            try {
                Invoke-WebRequest -Uri "http://$OllamaHost/api/tags" -UseBasicParsing -TimeoutSec 2 | Out-Null
                $ready = $true
                break
            } catch {
                Start-Sleep -Seconds 1
            }
        }
        if (-not $ready) {
            throw "FATAL: The temporary Ollama server did not become ready. Aborting Full build."
        }
        Write-Host "Pulling $Model (this downloads ~1 GB)..."
        & $OllamaExe pull $Model
        if ($LASTEXITCODE -ne 0) {
            throw "FATAL: 'ollama pull $Model' failed (exit $LASTEXITCODE). Aborting Full build."
        }
    } finally {
        if ($serve -and -not $serve.HasExited) {
            Stop-Process -Id $serve.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

# --- 3. Verify everything actually landed (fail loud otherwise) -------------
$modelFiles = Get-ChildItem -Path $ModelsDir -Recurse -File -ErrorAction SilentlyContinue
$modelBytes = ($modelFiles | Measure-Object -Property Length -Sum).Sum
if (-not $modelFiles -or $modelBytes -lt $MinModelBytes) {
    throw "FATAL: Qwen weights not staged - models dir has only $([math]::Round($modelBytes/1MB,1)) MB (< $([math]::Round($MinModelBytes/1MB)) MB). Aborting Full build."
}

Write-Host "AI assets staged successfully ($([math]::Round($modelBytes/1MB)) MB of model weights) under $StageDir" -ForegroundColor Green
