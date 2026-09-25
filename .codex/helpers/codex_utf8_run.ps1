$ErrorActionPreference = "Stop"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$guard = Join-Path $scriptDir "windows_utf8_guard.py"

if (-not (Test-Path -LiteralPath $guard)) {
    throw "Missing guard script: $guard"
}

$python = $env:PYTHON
if ([string]::IsNullOrWhiteSpace($python)) {
    $python = "python"
}

$guardArgs = $args
$stdinItems = @($input)

if ($stdinItems.Count -gt 0) {
    $stdinItems -join [Environment]::NewLine | & $python $guard @guardArgs
}
else {
    & $python $guard @guardArgs
}
exit $LASTEXITCODE
