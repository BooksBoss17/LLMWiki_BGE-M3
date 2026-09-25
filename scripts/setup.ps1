[CmdletBinding()]
param(
    # 可选：指定 python.exe 路径
    [string]$PythonPath = "",
    # 跳过模型下载（例如想手动下载或稍后运行 scripts/download_models.ps1）
    [switch]$SkipModel,
    # 跳过 pip 依赖安装（只重建目录结构）
    [switch]$SkipDeps
)
$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $ProjectRoot "BGE-M3\runtime\env"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

function Find-Python {
    param([string]$Hint)
    $candidates = @()
    if ($Hint) { $candidates += $Hint }
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

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    [警告] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "    [错误] $msg" -ForegroundColor Red }

Write-Host "=============================================="
Write-Host " LLMWiki_BGE-M3 安装向导"
Write-Host "=============================================="

# ---------- 1. 检测 Python ----------
Step "检测 Python（需要 3.10 - 3.13，推荐 3.11/3.12）"
if (Test-Path $VenvPython) {
    Ok "项目 venv 已存在: $VenvPython（如需重建请删除该目录后重跑）"
} else {
    $found = Find-Python $PythonPath
    if (-not $found) {
        Fail "未找到 Python 3.10-3.13。"
        Write-Host "    请从 https://www.python.org/downloads/ 安装 Python 3.11 或 3.12"
        Write-Host "    （安装时勾选 'Add python.exe to PATH'），然后重新运行 setup.bat。"
        Write-Host "    也可使用 winget: winget install Python.Python.3.11"
        exit 2
    }
    $BasePython = $found[0]
    Ok "找到 Python $($found[1]): $BasePython"

    # ---------- 2. 创建 venv ----------
    Step "创建项目虚拟环境 BGE-M3\runtime\env"
    & $BasePython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Fail "venv 创建失败。"; exit 3 }
    Ok "venv 已创建"
}

# ---------- 3. 安装依赖 ----------
if (-not $SkipDeps) {
    Step "升级 pip"
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { Fail "pip 升级失败，请检查网络。"; exit 4 }

    Step "安装依赖（torch CUDA 版约 3 GB，首次下载较慢）"
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Fail "依赖安装失败。常见原因：网络不稳定（可重跑本脚本续装）、"
        Write-Host "    PyTorch 下载源被墙（可设置镜像后重试）、磁盘空间不足（约需 8 GB）。"
        exit 5
    }
    Ok "依赖安装完成"

    # ---------- 4. 验证关键 import ----------
    Step "验证关键依赖"
    & $VenvPython -c "import torch, faiss, numpy, FlagEmbedding, transformers, huggingface_hub; print('imports OK:', 'torch', torch.__version__, '| faiss OK | FlagEmbedding OK')"
    if ($LASTEXITCODE -ne 0) { Fail "关键依赖导入失败。"; exit 6 }
    Ok "torch / faiss / FlagEmbedding 导入正常"

    & $VenvPython -c "import torch; print('CUDA_AVAILABLE', torch.cuda.is_available())" | Tee-Object -Variable cudaOut | Out-Null
    if ("$cudaOut" -match "CUDA_AVAILABLE True") {
        $gpu = & $VenvPython -c "import torch; print(torch.cuda.get_device_name(0))" 2>$null
        Ok "检测到 NVIDIA GPU: $gpu（RAG 向量化将使用 GPU 加速）"
    } else {
        Warn "未检测到可用的 NVIDIA CUDA GPU。"
        Write-Host "    RAG 入库/检索（BGE-M3 向量化）需要 NVIDIA GPU + CUDA 驱动，"
        Write-Host "    其余子系统（StudentDataSQL / Wiki）不受影响。详见 README 系统要求。"
    }
} else {
    Step "按参数跳过依赖安装"
}

# ---------- 5. 运行时目录 ----------
Step "创建运行时目录"
foreach ($d in @(
        "BGE-M3\runtime\data",
        "BGE-M3\runtime\index",
        "BGE-M3\runtime\logs",
        "BGE-M3\runtime\models",
        "StudentDataSQL\runtime\db",
        "StudentDataSQL\runtime\imports",
        "StudentDataSQL\runtime\exports",
        "StudentDataSQL\runtime\backups",
        "StudentDataSQL\runtime\logs",
        "StudentDataSQL\runtime\exam-reports"
    )) {
    New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot $d) | Out-Null
}
Ok "运行时目录就绪"

# ---------- 6. 模型下载 ----------
if ($SkipModel) {
    Step "按参数跳过模型下载"
    Warn "请稍后运行 scripts\download_models.ps1 下载 BGE-M3 模型（约 2.3 GB）。"
} else {
    Step "下载 BGE-M3 模型（约 2.3 GB，已存在则跳过）"
    & $VenvPython (Join-Path $PSScriptRoot "download_models.py")
    if ($LASTEXITCODE -ne 0) {
        Warn "模型下载未完成（可稍后运行 scripts\download_models.ps1 重试，支持断点续传）。"
    }
}

# ---------- 7. 路径契约自检 ----------
Step "路径契约自检"
& $VenvPython (Join-Path $ProjectRoot "skills\_shared\scripts\project_paths.py") validate --allow-missing
if ($LASTEXITCODE -ne 0) { Warn "路径契约校验返回非零，请检查 PROJECT_LAYOUT.yaml。"; }
else { Ok "PROJECT_LAYOUT.yaml 路径契约有效" }

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " 安装完成。下一步：" -ForegroundColor Cyan
Write-Host "   1. run.bat        启动 RAG 检索（首次会自动用示例数据建索引）"
Write-Host "   2. 把你自己的 Markdown 资料放入 raw\ 对应目录后，"
Write-Host "      重新运行 BGE-M3\scripts\rag_pipeline.py 重建索引"
Write-Host "==============================================" -ForegroundColor Cyan
exit 0
