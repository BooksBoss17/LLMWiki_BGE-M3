param(
    [switch]$Json
)

. "$PSScriptRoot\_common.ps1"
$SkillRoot = Get-SkillRoot
Initialize-SkillRuntime -SkillRoot $SkillRoot
$modelRuntime = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..\_shared\model-tools\scripts\model_runtime.py")
$cliPython = Assert-Venv -Name "cli" -SkillRoot $SkillRoot
& $cliPython $modelRuntime doctor --all --json
exit $LASTEXITCODE
