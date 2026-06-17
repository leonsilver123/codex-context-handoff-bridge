# Contributing

Thanks for improving Codex Context Handoff Bridge.

## Development Setup

Requirements:

- Windows PowerShell.
- Python 3.

Run the local test suite:

```powershell
.\scripts\codex-handoff.ps1 verify
.\scripts\handoff-smoke.ps1 -SkipAuto
```

Run the Python regression test directly:

```powershell
python scripts\test_codex_handoff.py
```

## Design Rules

- Keep the first-class state source project-local and file-backed.
- Do not depend on UI automation clicks.
- Treat Deep Link as an optional probe, not the only proof of handoff.
- A handoff is complete only after the target thread reads the required files
  and runs `accept-handoff`.
- Keep write-capable checks out of completion audit unless they are explicitly
  modeled as dry-run or command-existence checks.

## Pull Request Checklist

- Update tests when behavior changes.
- Update `README.md` or `docs/` when user-facing commands change.
- Run `scripts\test_codex_handoff.py`.
- Run `scripts\handoff-smoke.ps1 -SkipAuto`.
- Do not commit `.codex-handoff/`, `.tmp-tests/`, secrets, or local machine
  paths from your own environment.
