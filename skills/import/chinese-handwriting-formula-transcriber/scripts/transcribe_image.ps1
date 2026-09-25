param(
    [Parameter(Mandatory = $true)][string]$TaskId,
    [string]$TaskCard = "",
    [ValidateSet("full", "paddle", "formula")]
    [string]$Profile = "full",
    [string]$PrivateRoot = "$env:USERPROFILE\Desktop\ocr_private_runs\chinese_handwriting_formula_transcriber"
)

. "$PSScriptRoot\_common.ps1"
$SkillRoot = Get-SkillRoot
Initialize-SkillRuntime -SkillRoot $SkillRoot

if (-not $TaskCard) {
    $TaskCard = Join-Path $PrivateRoot "tasks\$TaskId.json"
}
if (-not (Test-Path -LiteralPath $TaskCard)) {
    throw "未找到任务卡：$TaskCard"
}

$cliPython = Assert-Venv -Name "cli" -SkillRoot $SkillRoot
& $cliPython (Join-Path $PSScriptRoot "_transcribe_image.py") `
    --skill-root $SkillRoot `
    --task-card $TaskCard `
    --profile $Profile `
    --private-root $PrivateRoot
exit $LASTEXITCODE
