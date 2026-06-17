# Installation

Codex Handoff Bridge is a small file-backed helper for Codex projects. It only
requires Python 3 and PowerShell on Windows. The Python code uses the standard
library only.

## Install From A Downloaded Repository

1. Download or clone this repository.

```powershell
git clone https://github.com/<your-name>/<your-repo>.git
cd <your-repo>
```

2. Install the helper into the project that should use context handoff.

```powershell
.\install.ps1 -ProjectPath "C:\path\to\your-project"
```

This copies the scripts into `C:\path\to\your-project\scripts`, creates the
`.codex-handoff` scaffold, and runs `verify`.

3. If files already exist and you intentionally want to replace them:

```powershell
.\install.ps1 -ProjectPath "C:\path\to\your-project" -Force
```

## Python Selection

The PowerShell wrapper tries these in order:

1. `CODEX_HANDOFF_PYTHON`
2. Windows `py -3`
3. `python`

If Python is installed in a custom location, set:

```powershell
$env:CODEX_HANDOFF_PYTHON = "C:\Python312\python.exe"
```

## Manual Install

Copy these files into your project:

```text
scripts/codex_handoff.py
scripts/codex-handoff.ps1
scripts/handoff-smoke.ps1
scripts/test_codex_handoff.py
```

Then initialize:

```powershell
.\scripts\codex-handoff.ps1 init
.\scripts\codex-handoff.ps1 verify
```

## Verify Installation

Run:

```powershell
.\scripts\codex-handoff.ps1 doctor --json
.\scripts\handoff-smoke.ps1 -SkipAuto
```

`doctor` should report `ready_for_handoff: true`. The smoke script should end
without throwing an error.
