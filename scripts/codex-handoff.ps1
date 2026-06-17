param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"
$Script = Join-Path $PSScriptRoot "codex_handoff.py"

if ($env:CODEX_HANDOFF_PYTHON) {
    & $env:CODEX_HANDOFF_PYTHON $Script @Args
    exit $LASTEXITCODE
}

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PyLauncher) {
    & $PyLauncher.Source -3 $Script @Args
    exit $LASTEXITCODE
}

$Python = Get-Command python -ErrorAction SilentlyContinue
if ($Python) {
    & $Python.Source $Script @Args
    exit $LASTEXITCODE
}

throw "Python 3 was not found. Install Python 3 or set CODEX_HANDOFF_PYTHON to a Python executable path."
