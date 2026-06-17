param(
    [switch]$SkipAuto
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Cli = Join-Path $PSScriptRoot "codex_handoff.py"
$Test = Join-Path $PSScriptRoot "test_codex_handoff.py"

function Resolve-Python {
    if ($env:CODEX_HANDOFF_PYTHON) {
        return @($env:CODEX_HANDOFF_PYTHON)
    }
    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        return @($PyLauncher.Source, "-3")
    }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) {
        return @($Python.Source)
    }
    throw "Python 3 was not found. Install Python 3 or set CODEX_HANDOFF_PYTHON to a Python executable path."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$Arguments = @()
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

Push-Location $Root
try {
    $PythonCommand = @(Resolve-Python)
    $PythonExecutable = [string]$PythonCommand[0]
    $PythonPrefix = @()
    if ($PythonCommand.Count -gt 1) {
        $PythonPrefix = $PythonCommand[1..($PythonCommand.Count - 1)]
    }

    Invoke-Checked -FilePath $PythonExecutable -Arguments (@($PythonPrefix) + @($Test))
    Invoke-Checked -FilePath $PythonExecutable -Arguments (@($PythonPrefix) + @($Cli, "verify"))
    Invoke-Checked -FilePath $PythonExecutable -Arguments (@($PythonPrefix) + @($Cli, "doctor", "--json"))
    Invoke-Checked -FilePath $PythonExecutable -Arguments (@($PythonPrefix) + @($Cli, "context-status", "--json", "--write"))
    Invoke-Checked -FilePath $PythonExecutable -Arguments (@($PythonPrefix) + @($Cli, "open-link", "--json"))
    Invoke-Checked -FilePath $PythonExecutable -Arguments (@($PythonPrefix) + @($Cli, "external-next", "--json"))
    Invoke-Checked -FilePath $PythonExecutable -Arguments (@($PythonPrefix) + @($Cli, "accept-handoff", "--dry-run", "--json"))
    Invoke-Checked -FilePath $PythonExecutable -Arguments (@($PythonPrefix) + @($Cli, "self-test", "app-server-http", "--json", "--write"))
    Invoke-Checked -FilePath $PythonExecutable -Arguments (@($PythonPrefix) + @($Cli, "compact-evidence", "--dry-run", "--json"))
    Invoke-Checked -FilePath $PythonExecutable -Arguments (@($PythonPrefix) + @($Cli, "completion-audit", "--json", "--write"))
    Invoke-Checked -FilePath $PythonExecutable -Arguments (@($PythonPrefix) + @($Cli, "maintenance", "--json", "--write"))
    if (-not $SkipAuto) {
        Invoke-Checked -FilePath $PythonExecutable -Arguments (@($PythonPrefix) + @($Cli, "auto", "--link"))
    }
}
finally {
    Pop-Location
}
