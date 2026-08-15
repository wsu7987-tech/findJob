param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8000,
    [string]$AppDataDir = ".local/app-data/backend-dev",
    [string]$LlmProvider = "stub-llm",
    [string]$LlmModel = "stub-summary-model",
    [string]$EmbeddingProvider = "stub-embedding",
    [string]$EmbeddingModel = "stub-embedding-model",
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Missing .venv Python at '$venvPython'. Run 'uv venv .venv' and 'uv sync --group test' from the repository root first."
}

$appDataPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $AppDataDir))
$outputRoot = Join-Path $appDataPath "outputs"
$sqlitePath = Join-Path $appDataPath "knowledge-curator.db"
$qdrantPath = Join-Path $appDataPath "qdrant"

foreach ($path in @($appDataPath, $outputRoot, $qdrantPath)) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}

$env:KNOWLEDGE_CURATOR_APP_DATA_DIR = $appDataPath
$env:KNOWLEDGE_CURATOR_SQLITE_PATH = $sqlitePath
$env:KNOWLEDGE_CURATOR_OUTPUT_ROOT = $outputRoot
$env:KNOWLEDGE_CURATOR_QDRANT_PATH = $qdrantPath
$env:KNOWLEDGE_CURATOR_LLM_PROVIDER = $LlmProvider
$env:KNOWLEDGE_CURATOR_LLM_MODEL = $LlmModel
$env:KNOWLEDGE_CURATOR_EMBEDDING_PROVIDER = $EmbeddingProvider
$env:KNOWLEDGE_CURATOR_EMBEDDING_MODEL = $EmbeddingModel

$uvicornArgs = @(
    "-m",
    "uvicorn",
    "backend.app.main:create_app",
    "--factory",
    "--host",
    $BindHost,
    "--port",
    $Port
)

if (-not $NoReload) {
    $uvicornArgs += "--reload"
}

Write-Host "Starting backend from $repoRoot"
Write-Host "Using .venv interpreter: $venvPython"
Write-Host "App data dir: $appDataPath"
Write-Host "Listening on http://$BindHost`:$Port"

Push-Location $repoRoot
try {
    & $venvPython @uvicornArgs
}
finally {
    Pop-Location
}
