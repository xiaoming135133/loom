param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "未找到 .venv。请先按 README 的本地运行章节安装依赖。"
}

Push-Location $repoRoot
try {
    & $venvPython -m uvicorn server.main:app --host 127.0.0.1 --port $Port --reload
}
finally {
    Pop-Location
}
