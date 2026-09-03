param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "项目虚拟环境不存在。请先在 UDF 目录执行：uv sync --extra dev"
}

Set-Location -LiteralPath $projectRoot
& $python -m cascade_forge.cli serve `
    --dataset "datasets/geometry-v1.0.0" `
    --v2-dataset "datasets/geometry-v2.1.0" `
    --workspace "datasets/workspace-v2" `
    --naca65-dataset "datasets/naca65-benchmark-v3.0.0" `
    --host "127.0.0.1" `
    --port $Port
