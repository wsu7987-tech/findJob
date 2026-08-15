param(
    [string]$Cases = "docs/testing/qa-eval-sample.json",
    [int]$Limit = 5,
    [string]$Output,
    [string]$Baseline
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found at $pythonExe"
}

$arguments = @(
    "-m",
    "backend.app.qa_eval_cli",
    "--cases",
    $Cases,
    "--limit",
    "$Limit"
)

if (-not [string]::IsNullOrWhiteSpace($Output)) {
    $arguments += @("--output", $Output)
}

if (-not [string]::IsNullOrWhiteSpace($Baseline)) {
    $arguments += @("--baseline", $Baseline)
}

Push-Location $repoRoot
try {
    & $pythonExe @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
