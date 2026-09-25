param([string]$Python = "3.11", [switch]$SkipInstall)

$setup = Join-Path $PSScriptRoot "..\..\..\_shared\model-tools\scripts\setup_runtime.ps1"
if (-not (Test-Path -LiteralPath $setup)) { throw "缺少共享运行库安装脚本：$setup" }
& $setup -Scope all -Python311 $Python -SkipInstall:$SkipInstall
if ($LASTEXITCODE -ne 0) { throw "共享模型运行库安装失败。" }
