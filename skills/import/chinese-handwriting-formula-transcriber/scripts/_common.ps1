$ErrorActionPreference = "Stop"

$SharedRuntimeScript = Join-Path $PSScriptRoot "..\..\..\_shared\model-tools\scripts\runtime_env.ps1"
if (-not (Test-Path -LiteralPath $SharedRuntimeScript)) {
    throw "缺少共享模型运行库脚本：$SharedRuntimeScript"
}
. $SharedRuntimeScript

function Get-SkillRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

function Initialize-SkillRuntime {
    param(
        [string]$SkillRoot = (Get-SkillRoot)
    )

    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    [Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
    Initialize-SharedModelRuntime | Out-Null
}

function Get-VenvPython {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$SkillRoot = (Get-SkillRoot)
    )
    $mapping = @{cli="cli-py311"; paddle="paddle-py311"; formula="formula-py311"}
    if (-not $mapping.ContainsKey($Name)) { throw "未知的转写器环境：$Name" }
    return Get-SharedModelPython -Name $mapping[$Name]
}

function Assert-Venv {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$SkillRoot = (Get-SkillRoot)
    )
    $python = Get-VenvPython -Name $Name -SkillRoot $SkillRoot
    if (-not (Test-Path -LiteralPath $python)) {
        throw "缺少 venv '$Name'。请先运行 scripts\setup_env.ps1。"
    }
    return $python
}

function Invoke-PythonJson {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $output = & $Python @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        return [PSCustomObject]@{
            ok = $false
            exit_code = $code
            stdout = ($output -join "`n")
        }
    }
    try {
        return ($output -join "`n") | ConvertFrom-Json
    }
    catch {
        return [PSCustomObject]@{
            ok = $false
            error = "invalid-json"
            stdout = ($output -join "`n")
        }
    }
}
