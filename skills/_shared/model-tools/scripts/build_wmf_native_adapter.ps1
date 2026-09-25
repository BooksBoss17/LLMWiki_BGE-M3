param(
    [string]$Source = "",
    [string]$Output = ""
)

$ErrorActionPreference = 'Stop'
$modelToolsRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $Source) {
    $Source = Join-Path $modelToolsRoot '..\scripts\render_wmf_native.ps1'
}
if (-not $Output) {
    $Output = Join-Path $modelToolsRoot 'runtime\tools\wmf-native-adapter\v1.0.0\LLMWikiClassicWmfRenderer.v1.dll'
}
$Source = [System.IO.Path]::GetFullPath($Source)
$Output = [System.IO.Path]::GetFullPath($Output)
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($Output)) | Out-Null
if (Test-Path -LiteralPath $Output) {
    throw "Refusing to overwrite an existing native adapter: $Output"
}
$text = Get-Content -LiteralPath $Source -Raw -Encoding UTF8
$match = [regex]::Match(
    $text,
    "(?s)Add-Type -TypeDefinition @'\r?\n(.*?)\r?\n'@ -ReferencedAssemblies System.Drawing"
)
if (-not $match.Success) {
    throw "C# source block not found in $Source"
}
Add-Type `
    -TypeDefinition $match.Groups[1].Value `
    -ReferencedAssemblies System.Drawing `
    -OutputAssembly $Output `
    -OutputType Library
$hash = Get-FileHash -LiteralPath $Output -Algorithm SHA256
[ordered]@{ output = $Output; sha256 = $hash.Hash.ToLowerInvariant() } |
    ConvertTo-Json -Compress
