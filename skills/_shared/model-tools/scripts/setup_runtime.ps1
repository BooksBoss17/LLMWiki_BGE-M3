param(
    [ValidateSet("all", "paddle", "formula", "document-stt")]
    [string]$Scope = "all",
    [string]$Python311 = "",
    [switch]$SkipInstall,
    [switch]$DownloadModels
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\runtime_env.ps1"
$runtimeRoot = Initialize-SharedModelRuntime
$modelRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

$uvExe = Join-Path $runtimeRoot "tools\uv\v0.11.28\uv.exe"
if (-not (Test-Path -LiteralPath $uvExe)) { throw "缺少 bundled uv：$uvExe" }
if (-not $Python311) {
    $Python311 = Join-Path $runtimeRoot "python\cpython-3.11.15-windows-x86_64-none\python.exe"
}
if (-not (Test-Path -LiteralPath $Python311)) { throw "缺少 bundled CPython：$Python311" }

function New-SharedVenv {
    param([Parameter(Mandatory = $true)][string]$Name, [Parameter(Mandatory = $true)][string]$Python)
    $target = Join-Path $runtimeRoot "envs\$Name"
    $pythonExe = if ($Name -eq "document-stt-py310") { Join-Path $target "python.exe" } else { Join-Path $target "Scripts\python.exe" }
    if (Test-Path -LiteralPath $pythonExe) {
        Write-Host "[已就绪] 环境已存在：$Name"
        return $pythonExe
    }
    Write-Host "[安装] 正在创建环境：$Name"
    & $uvExe venv --python $Python $target
    if ($LASTEXITCODE -ne 0) { throw "uv 创建 $Name 失败" }
    return $pythonExe
}

function Invoke-Pip {
    param([Parameter(Mandatory = $true)][string]$Python, [Parameter(Mandatory = $true)][string[]]$Arguments)
    & $Python -m ensurepip --upgrade | Out-Null
    & $Python -m pip @Arguments
    if ($LASTEXITCODE -ne 0) { throw "pip 执行失败：$($Arguments -join ' ')" }
}

if ($Scope -in @("all", "paddle")) {
    $cliPython = New-SharedVenv -Name "cli-py311" -Python $Python311
    if (-not $SkipInstall) {
        Invoke-Pip -Python $cliPython -Arguments @("install", "--upgrade", "pip", "setuptools", "wheel")
        Invoke-Pip -Python $cliPython -Arguments @("install", "-r", (Join-Path $modelRoot "requirements\cli-py311.txt"))
    }
    $paddlePython = New-SharedVenv -Name "paddle-py311" -Python $Python311
    if (-not $SkipInstall) {
        Invoke-Pip -Python $paddlePython -Arguments @("install", "--upgrade", "pip", "setuptools", "wheel")
        Invoke-Pip -Python $paddlePython -Arguments @(
            "install",
            "https://paddle-whl.bj.bcebos.com/stable/cu126/paddlepaddle-gpu/paddlepaddle_gpu-3.3.1-cp311-cp311-win_amd64.whl"
        )
        Invoke-Pip -Python $paddlePython -Arguments @("install", "-r", (Join-Path $modelRoot "requirements\paddle-py311.txt"))
    }
}

if ($Scope -in @("all", "formula")) {
    $formulaPython = New-SharedVenv -Name "formula-py311" -Python $Python311
    if (-not $SkipInstall) {
        Invoke-Pip -Python $formulaPython -Arguments @("install", "--upgrade", "pip", "setuptools", "wheel")
        Invoke-Pip -Python $formulaPython -Arguments @("install", "-r", (Join-Path $modelRoot "requirements\formula-py311.txt"))
    }
}

if ($Scope -in @("all", "document-stt")) {
    $existing = Join-Path $runtimeRoot "envs\document-stt-py310\python.exe"
    if (-not (Test-Path -LiteralPath $existing)) {
        Write-Warning "document-stt-py310 通常从已验证的 Python 3.10 复制运行时迁移而来。"
        Write-Warning "重建此环境前，请先运行 migrate_runtime_assets.ps1。"
    }
}

if ($DownloadModels) {
    $paddlePython = Get-SharedModelPython -Name "paddle-py311"
    if (-not (Test-Path -LiteralPath $paddlePython)) { throw "缺少 paddle-py311 环境。" }
    & $paddlePython (Join-Path $PSScriptRoot "download_models.py") --scope paddle --write-lock
    if ($LASTEXITCODE -ne 0) { throw "Paddle 模型下载/预热失败。" }
}

Write-Host "[已完成] 共享模型运行库安装完成：$runtimeRoot"
