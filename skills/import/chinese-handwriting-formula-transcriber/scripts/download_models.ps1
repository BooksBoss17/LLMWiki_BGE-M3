param(
    [ValidateSet("full", "paddle", "formula")]
    [string]$Scope = "full",
    [string]$Device = "gpu:0"
)

. "$PSScriptRoot\_common.ps1"
$SkillRoot = Get-SkillRoot
Initialize-SkillRuntime -SkillRoot $SkillRoot

$cliPython = Assert-Venv -Name "cli" -SkillRoot $SkillRoot
$sharedScripts = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..\_shared\model-tools\scripts")

if ($Scope -in @("full", "paddle")) {
    $paddlePython = Assert-Venv -Name "paddle" -SkillRoot $SkillRoot
    & $paddlePython (Join-Path $sharedScripts "download_models.py") --scope paddle --device $Device --write-lock
    if ($LASTEXITCODE -ne 0) { throw "共享 Paddle 下载/构建检查失败。" }
}

if ($Scope -in @("full", "formula")) {
    & $cliPython (Join-Path $sharedScripts "benchmark_models.py") --suite smoke
    if ($LASTEXITCODE -ne 0) { throw "共享 OCR/公式冒烟基准测试失败。" }
}
Write-Host "[已完成] 共享模型下载和预热检查已完成。"
