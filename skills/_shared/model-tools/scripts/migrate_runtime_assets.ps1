param(
    [switch]$Apply,
    [switch]$UserAuthorized,
    [switch]$CleanupOld,
    [switch]$UseVerifiedReport
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\runtime_env.ps1"
$runtimeRoot = Initialize-SharedModelRuntime
$modelRuntimeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$skillsRoot = (Resolve-Path -LiteralPath (Join-Path $modelRuntimeRoot "..\..")).Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $skillsRoot "..")).Path

if ($Apply -and -not $UserAuthorized) {
    throw "-Apply 需要同时传入 -UserAuthorized。"
}
if ($UseVerifiedReport -and (-not $Apply -or -not $CleanupOld)) {
    throw "-UseVerifiedReport 需要同时传入 -Apply -UserAuthorized -CleanupOld。"
}

function Test-IsInside {
    param([Parameter(Mandatory = $true)][string]$Child, [Parameter(Mandatory = $true)][string]$Parent)
    $childFull = [System.IO.Path]::GetFullPath($Child).TrimEnd('\')
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\')
    return $childFull.StartsWith($parentFull + '\', [System.StringComparison]::OrdinalIgnoreCase) -or $childFull.Equals($parentFull, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-TreeSummary {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return [pscustomobject]@{exists=$false; files=0; bytes=0} }
    $files = @(Get-ChildItem -LiteralPath $Path -Force -Recurse -File -ErrorAction SilentlyContinue)
    return [pscustomobject]@{exists=$true; files=$files.Count; bytes=(($files | Measure-Object Length -Sum).Sum)}
}

function Test-TreeEquivalent {
    param([Parameter(Mandatory = $true)][string]$Source, [Parameter(Mandatory = $true)][string]$Destination)
    $sourceFiles = @(Get-ChildItem -LiteralPath $Source -Force -Recurse -File | Sort-Object FullName)
    foreach ($src in $sourceFiles) {
        $rel = [System.IO.Path]::GetRelativePath($Source, $src.FullName)
        $dst = Join-Path $Destination $rel
        if (-not (Test-Path -LiteralPath $dst)) { return $false }
        if ($src.Length -ne (Get-Item -LiteralPath $dst).Length) { return $false }
        if ((Get-FileHash -LiteralPath $src.FullName -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash) { return $false }
    }
    return $true
}

$transcriberRoot = Join-Path $repoRoot 'skills\import\chinese-handwriting-formula-transcriber'
$mappings = @(
    [pscustomobject]@{id='cnocr'; source=(Join-Path $transcriberRoot 'models\cnocr\2.3'); destination=(Join-Path $runtimeRoot 'models\cnocr\2.3'); source_root=$repoRoot},
    [pscustomobject]@{id='cnstd'; source=(Join-Path $transcriberRoot 'models\cnstd\1.2'); destination=(Join-Path $runtimeRoot 'models\cnstd\1.2'); source_root=$repoRoot},
    [pscustomobject]@{id='paddle-cache'; source=(Join-Path $transcriberRoot 'models\paddle'); destination=(Join-Path $runtimeRoot 'models\paddle'); source_root=$repoRoot},
    [pscustomobject]@{id='paddleocr-cache'; source=(Join-Path $transcriberRoot 'models\paddleocr'); destination=(Join-Path $runtimeRoot 'models\paddleocr'); source_root=$repoRoot},
    [pscustomobject]@{id='paddlex-cache'; source=(Join-Path $transcriberRoot 'models\paddlex'); destination=(Join-Path $runtimeRoot 'models\paddlex'); source_root=$repoRoot},
    [pscustomobject]@{id='whisper-large-v3'; source=(Join-Path $repoRoot 'skills\import\video-transcript-import\runtime\models\faster-whisper-large-v3'); destination=(Join-Path $runtimeRoot 'models\faster-whisper\faster-whisper-large-v3'); source_root=$repoRoot},
    [pscustomobject]@{id='whisper-medium'; source=(Join-Path $repoRoot 'skills\import\video-transcript-import\runtime\models\faster-whisper-medium'); destination=(Join-Path $runtimeRoot 'models\faster-whisper\faster-whisper-medium'); source_root=$repoRoot},
    [pscustomobject]@{id='cli-env'; source=(Join-Path $transcriberRoot '.venvs\cli'); destination=(Join-Path $runtimeRoot 'envs\cli-py311'); source_root=$repoRoot},
    [pscustomobject]@{id='paddle-env'; source=(Join-Path $transcriberRoot '.venvs\paddle'); destination=(Join-Path $runtimeRoot 'envs\paddle-py311'); source_root=$repoRoot},
    [pscustomobject]@{id='formula-env'; source=(Join-Path $transcriberRoot '.venvs\formula'); destination=(Join-Path $runtimeRoot 'envs\formula-py311'); source_root=$repoRoot},
    [pscustomobject]@{id='document-stt-env'; source=(Join-Path $repoRoot 'skills\_shared\runtime\envs\python310-ocr-stt'); destination=(Join-Path $runtimeRoot 'envs\document-stt-py310'); source_root=$repoRoot},
    [pscustomobject]@{id='paddleocr-source'; source=(Join-Path $env:USERPROFILE 'Desktop\My_project\下载的开源项目\PaddleOCR-main'); destination=(Join-Path $runtimeRoot 'tools\vendor\PaddleOCR-main'); source_root=(Join-Path $env:USERPROFILE 'Desktop')}
)

$texRoot = Join-Path $env:USERPROFILE '.cache\huggingface\hub\models--OleehyO--TexTeller\snapshots'
if (Test-Path -LiteralPath $texRoot) {
    $snapshot = Get-ChildItem -LiteralPath $texRoot -Directory | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if ($snapshot) {
        $mappings += [pscustomobject]@{id='texteller'; source=$snapshot.FullName; destination=(Join-Path $runtimeRoot 'models\huggingface\OleehyO\TexTeller'); source_root=$env:USERPROFILE}
    }
}

$priorItems = @{}
$priorReportPath = Join-Path $runtimeRoot 'migration-report.json'
if ($UseVerifiedReport) {
    if (-not (Test-Path -LiteralPath $priorReportPath)) {
        throw "未找到已验证的迁移报告：$priorReportPath"
    }
    $priorReport = Get-Content -Raw -LiteralPath $priorReportPath | ConvertFrom-Json
    if (-not $priorReport.ok) { throw "上一份迁移报告未成功。" }
    foreach ($prior in $priorReport.items) { $priorItems[$prior.id] = $prior }
}

$report = [ordered]@{mode=if($Apply){'apply'}else{'dry-run'}; runtime_root=$runtimeRoot; items=@()}
foreach ($mapping in $mappings) {
    if (-not (Test-IsInside -Child $mapping.source -Parent $mapping.source_root)) { throw "不安全的源路径：$($mapping.source)" }
    if (-not (Test-IsInside -Child $mapping.destination -Parent $runtimeRoot)) { throw "不安全的目标路径：$($mapping.destination)" }
    $sourceSummary = Get-TreeSummary -Path $mapping.source
    $item = [ordered]@{id=$mapping.id; source=$mapping.source; destination=$mapping.destination; source_summary=$sourceSummary; copied=$false; verified=$false; cleaned=$false}
    if ($sourceSummary.exists) {
        if ($Apply) {
            if ($UseVerifiedReport) {
                $prior = $priorItems[$mapping.id]
                $sameSource = $prior -and ([System.IO.Path]::GetFullPath($prior.source) -eq [System.IO.Path]::GetFullPath($mapping.source))
                $sameDestination = $prior -and ([System.IO.Path]::GetFullPath($prior.destination) -eq [System.IO.Path]::GetFullPath($mapping.destination))
                $item.verified = [bool]($prior -and $prior.verified -and $sameSource -and $sameDestination -and (Test-Path -LiteralPath $mapping.destination))
            } else {
                New-Item -ItemType Directory -Force -Path $mapping.destination | Out-Null
                Copy-Item -Path (Join-Path $mapping.source '*') -Destination $mapping.destination -Recurse -Force
                $item.copied = $true
                $item.verified = Test-TreeEquivalent -Source $mapping.source -Destination $mapping.destination
            }
            if (-not $item.verified) { throw "$($mapping.id) 校验失败" }
            if ($CleanupOld) {
                Remove-Item -LiteralPath $mapping.source -Recurse -Force
                $item.cleaned = $true
            }
        }
    }
    $item.destination_summary = Get-TreeSummary -Path $mapping.destination
    $report.items += $item
}

$report.ok = -not ($report.items | Where-Object { $_.source_summary.exists -and $Apply -and -not $_.verified })
$reportPath = Join-Path $runtimeRoot 'migration-report.json'
if ($Apply) { $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $reportPath -Encoding utf8 }
$report | ConvertTo-Json -Depth 10
if (-not $report.ok) { exit 1 }
