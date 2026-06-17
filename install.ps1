param(
    [string]$ProjectPath = (Get-Location).Path,
    [switch]$Force,
    [switch]$NoVerify
)

$ErrorActionPreference = "Stop"
$SourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetRoot = Resolve-Path -LiteralPath $ProjectPath
$TargetScripts = Join-Path $TargetRoot "scripts"

New-Item -ItemType Directory -Path $TargetScripts -Force | Out-Null

$Files = @(
    "codex_handoff.py",
    "codex-handoff.ps1",
    "handoff-smoke.ps1",
    "test_codex_handoff.py"
)

foreach ($File in $Files) {
    $Source = Join-Path (Join-Path $SourceRoot "scripts") $File
    $Target = Join-Path $TargetScripts $File
    if ((Test-Path -LiteralPath $Target) -and -not $Force) {
        throw "Target file already exists: $Target. Re-run with -Force to overwrite."
    }
    Copy-Item -LiteralPath $Source -Destination $Target -Force:$Force
}

Push-Location $TargetRoot
try {
    & (Join-Path $TargetScripts "codex-handoff.ps1") init
    if ($LASTEXITCODE -ne 0) {
        throw "codex-handoff init failed with exit code $LASTEXITCODE"
    }
    if (-not $NoVerify) {
        & (Join-Path $TargetScripts "codex-handoff.ps1") verify
        if ($LASTEXITCODE -ne 0) {
            throw "codex-handoff verify failed with exit code $LASTEXITCODE"
        }
    }
}
finally {
    Pop-Location
}

Write-Output "Installed Codex Handoff Bridge into: $TargetRoot"
Write-Output "Run: .\scripts\codex-handoff.ps1 status"
