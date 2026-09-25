[CmdletBinding()]
param(
    # 可选：指定 python.exe 路径；默认自动探测 py 启动器 / PATH
    [string]$PythonPath = ""
)
$ErrorActionPreference = "Stop"

# 以 UTF-8 输出，避免中文乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $ProjectRoot "BGE-M3\runtime\env"

function Find-Python {
    param([string]$Hint)
    $candidates = @()
    if ($Hint) { $candidates += $Hint }
    # py 启动器优先选择 3.11 / 3.12（与验证过的环境一致）
    foreach ($ver in @("3.11", "3.12")) {
        try { $p = & py -$ver -c "import sys; print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $p) { $candidates += $p } } catch { }
    }
    try { $p = & py -3 -c "import sys; print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $p) { $candidates += $p } } catch { }
    try { $p = (Get-Command python -ErrorAction Stop).Source; if ($p) { $candidates += $p } } catch { }

    foreach ($cand in $candidates) {
        if (-not $cand -or -not (Test-Path $cand)) { continue }
        $ver = & $cand -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0) { continue }
        $major, $minor = $ver.Split(".") | ForEach-Object { [int]$_ }
        if ($major -eq 3 -and $minor -ge 10 -and $minor -le 13) {
            return @($cand, "$major.$minor")
        }
    }
    return $null
}

Write-Host "==== LLMWiki_BGE-M3 模型下载 ====" -ForegroundColor Cyan

if (Test-Path (Join-Path $VenvDir "Scripts\python.exe")) {
    $PythonExe = Join-Path $VenvDir "Scripts\python.exe"
    Write-Host "使用项目 venv: $PythonExe"
} else {
    $found = Find-Python $PythonPath
    if (-not $found) {
        Write-Host "[ERROR] 未找到 Python 3.10-3.13。请先安装 Python 3.11/3.12，" -ForegroundColor Red
        Write-Host "        或先运行 setup.bat 创建项目虚拟环境。" -ForegroundColor Red
        exit 2
    }
    $PythonExe = $found[0]
    Write-Host "使用系统 Python: $PythonExe (Python $($found[1]))"
    & $PythonExe -m pip show huggingface-hub *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] 该 Python 缺少 huggingface-hub。请先运行 setup.bat。" -ForegroundColor Red
        exit 2
    }
}

& $PythonExe (Join-Path $PSScriptRoot "download_models.py")
exit $LASTEXITCODE
