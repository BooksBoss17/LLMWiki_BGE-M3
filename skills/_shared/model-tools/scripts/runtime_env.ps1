$ErrorActionPreference = "Stop"

function Get-SharedModelRuntimeRoot {
    if ($env:LLMWIKI_MODEL_RUNTIME_ROOT) {
        return [System.IO.Path]::GetFullPath($env:LLMWIKI_MODEL_RUNTIME_ROOT)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\runtime"))
}

function Initialize-SharedModelRuntime {
    $root = Get-SharedModelRuntimeRoot
    $models = Join-Path $root "models"
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    [Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
    $env:LLMWIKI_MODEL_RUNTIME_ROOT = $root
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:TOKENIZERS_PARALLELISM = "false"
    $env:HF_HOME = Join-Path $models "huggingface"
    $env:HF_HUB_CACHE = Join-Path $models "huggingface\hub"
    $env:TRANSFORMERS_CACHE = Join-Path $models "huggingface\transformers"
    $env:PADDLE_HOME = Join-Path $models "paddle"
    $env:PADDLEOCR_HOME = Join-Path $models "paddleocr"
    $env:PADDLE_PDX_CACHE_HOME = Join-Path $models "paddlex"
    if (-not $env:PADDLE_PDX_MODEL_SOURCE) { $env:PADDLE_PDX_MODEL_SOURCE = "bos" }
    $env:CNOCR_HOME = Join-Path $models "cnocr"
    $env:CNSTD_HOME = Join-Path $models "cnstd"
    @(
        $root,
        $models,
        (Join-Path $root "envs"),
        (Join-Path $root "tools"),
        (Join-Path $root "benchmarks"),
        (Join-Path $root ".staging"),
        $env:HF_HOME,
        $env:HF_HUB_CACHE,
        $env:TRANSFORMERS_CACHE,
        $env:PADDLE_HOME,
        $env:PADDLEOCR_HOME,
        $env:PADDLE_PDX_CACHE_HOME,
        $env:CNOCR_HOME,
        $env:CNSTD_HOME
    ) | ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }
    return $root
}

function Get-SharedModelPython {
    param([Parameter(Mandatory = $true)][ValidateSet("cli-py311", "paddle-py311", "formula-py311", "document-stt-py310")][string]$Name)
    $root = Get-SharedModelRuntimeRoot
    if ($Name -eq "document-stt-py310") {
        return Join-Path $root "envs\$Name\python.exe"
    }
    return Join-Path $root "envs\$Name\Scripts\python.exe"
}
