<#
.SYNOPSIS
    First-run setup for the FULL build's embedded AI. Invoked by the full
    installer so the bundled Ollama serves the bundled Qwen 3 1.7B weights.

.PARAMETER AppDir
    The installed application directory (contains \ollama and \models).
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir
)

$ErrorActionPreference = 'SilentlyContinue'
$OllamaExe = Join-Path $AppDir "ollama\ollama.exe"
$ModelsDir = Join-Path $AppDir "models"

if (-not (Test-Path $OllamaExe)) {
    Write-Host "Bundled Ollama not found; this should only run for the full build."
    exit 0
}

# Point the embedded Ollama at the bundled model store and start it.
$env:OLLAMA_MODELS = $ModelsDir
Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Hidden
Start-Sleep -Seconds 2

# Ensure the model is registered (no-op if the bundled blobs already provide it).
& $OllamaExe list | Select-String -SimpleMatch "qwen3:1.7b" | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $OllamaExe pull qwen3:1.7b
}
Write-Host "Embedded AI ready."
