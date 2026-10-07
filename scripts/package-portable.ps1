$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$backendOutput = Join-Path $repoRoot "build\backend"
$portableOutput = Join-Path $repoRoot "dist\portable"
$portableDirectory = Join-Path $portableOutput "FineJob"
$archivePath = Join-Path $portableOutput "FineJob-portable-windows-x64.zip"
$backendWork = Join-Path $repoRoot "build\pyinstaller"
$cityData = Join-Path $repoRoot "backend\app\services\fine_job\boss_scraper\data"
$electronOutput = Join-Path $repoRoot "build\electron"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$buildMachineAppData = Join-Path $env:APPDATA "fine-job-desktop"
$portableData = Join-Path $portableDirectory "data"
$electronProfileEntries = @(
    "blob_storage",
    "Cache",
    "Code Cache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "GPUCache",
    "Local Storage",
    "Network",
    "Session Storage",
    "Shared Dictionary",
    "WebStorage",
    "DevToolsActivePort",
    "DIPS",
    "DIPS-shm",
    "DIPS-wal",
    "Local State",
    "Preferences",
    "SharedStorage",
    "SharedStorage-wal"
)

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $false)][string[]]$Arguments = @()
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $Command $($Arguments -join ' ')"
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv was not found."
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm was not found."
}

# 每次只清理本次构建的明确输出目录，避免把开发数据带入便携包。
Remove-Item -LiteralPath $backendOutput -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $electronOutput -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $backendWork -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $portableOutput -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $backendOutput, $electronOutput, $portableOutput | Out-Null

Push-Location $repoRoot
try {
    # 构建机只在本地环境安装打包工具，运行包不携带 uv 或 Python 环境。
    Invoke-Checked "uv" @("sync")
    Invoke-Checked "uv" @("pip", "install", "--python", $venvPython, "pyinstaller>=6.14,<7.0")

    $pyInstallerArguments = @(
        "run", "--no-sync", "python", "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name", "FineJob-Backend",
        "--distpath", $backendOutput,
        "--workpath", $backendWork,
        "--specpath", $backendWork,
        "--paths", $repoRoot,
        "--collect-submodules", "backend.app",
        "--collect-submodules", "mcp.server",
        "--collect-all", "rapidocr",
        "--collect-all", "onnxruntime",
        "--collect-all", "cv2",
        "--collect-all", "pymupdf",
        "--collect-all", "pymupdf4llm",
        "--add-data", "$cityData;backend/app/services/fine_job/boss_scraper/data",
        (Join-Path $repoRoot "packaging\finejob_runtime.py")
    )
    Invoke-Checked "uv" $pyInstallerArguments

    Invoke-Checked "pnpm" @("--filter", "fine-job-desktop", "build")

    $electronPackage = Resolve-Path (Join-Path $repoRoot "apps\desktop\node_modules\electron")
    $electronRuntime = Join-Path $electronPackage "dist"
    if (-not (Test-Path -LiteralPath (Join-Path $electronRuntime "electron.exe"))) {
        throw "Electron Windows runtime was not found: $electronRuntime"
    }

    # 使用 Electron 官方运行目录结构，resources/app 由 Electron 直接加载。
    New-Item -ItemType Directory -Force -Path $portableDirectory | Out-Null
    Get-ChildItem -LiteralPath $electronRuntime | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $portableDirectory -Recurse -Force
    }
    Rename-Item -LiteralPath (Join-Path $portableDirectory "electron.exe") -NewName "FineJob.exe"

    $appRoot = Join-Path $portableDirectory "resources\app"
    $appNodeModules = Join-Path $appRoot "node_modules"
    New-Item -ItemType Directory -Path $appRoot, $appNodeModules | Out-Null
    Copy-Item -LiteralPath (Join-Path $repoRoot "apps\desktop\dist") -Destination $appRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $repoRoot "apps\desktop\dist-electron") -Destination $appRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $repoRoot "apps\desktop\package.json") -Destination $appRoot

    $nodePtySource = Resolve-Path (Join-Path $repoRoot "apps\desktop\node_modules\node-pty")
    Copy-Item -LiteralPath $nodePtySource -Destination $appNodeModules -Recurse

    $resourceRoot = Join-Path $portableDirectory "resources"
    $portableBackend = Join-Path $resourceRoot "backend"
    Copy-Item -LiteralPath (Join-Path $backendOutput "FineJob-Backend") -Destination $portableBackend -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "apps\desktop\resources\codex") -Destination $resourceRoot -Recurse

    # 只迁移业务数据，排除 Electron 浏览器缓存，确保 ZIP 可以携带已有岗位和配置。
    New-Item -ItemType Directory -Force -Path $portableData | Out-Null
    if (Test-Path -LiteralPath $buildMachineAppData) {
        Get-ChildItem -LiteralPath $buildMachineAppData -Force |
            Where-Object { $_.Name -notin $electronProfileEntries } |
            ForEach-Object {
                Copy-Item -LiteralPath $_.FullName -Destination $portableData -Recurse -Force
            }
    }

    Compress-Archive -Path $portableDirectory -DestinationPath $archivePath -CompressionLevel Optimal
    Write-Host "Portable package created: $archivePath"
}
finally {
    Pop-Location
}
