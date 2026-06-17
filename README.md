# Codex Context Handoff Bridge

Codex Context Handoff Bridge is a project-local context compression and handoff
helper for long-running Codex App work.

It stores durable task state in `.codex-handoff/` instead of relying only on a
single long chat history. When a Codex thread gets long, you create a structured
handoff, start a new thread for the same workspace, and let the new thread read
the handoff files before continuing.

## What It Provides

- File-backed context state under `.codex-handoff/`.
- Structured handoff files with `FACT`, `DECISION`, `TODO`, `OPEN`,
  `REJECTED`, and `EVIDENCE` sections.
- Evidence-log compaction and context pressure estimation.
- Target-thread acceptance tracking.
- Completion audit for local and external handoff requirements.
- Optional `codex://` Deep Link probe.
- Optional App Server HTTP self-test path.
- Windows-friendly PowerShell wrapper and installer.

## Repository Layout

```text
install.ps1
LICENSE
CONTRIBUTING.md
SECURITY.md
CHANGELOG.md
scripts/codex_handoff.py
scripts/codex-handoff.ps1
scripts/handoff-smoke.ps1
scripts/test_codex_handoff.py
.github/workflows/ci.yml
docs/INSTALL.md
docs/USAGE.md
docs/PUBLISHING.md
AGENTS.md
ACCEPTANCE.md
```

## Requirements

- Windows PowerShell.
- Python 3.
- Codex App for real target-thread handoff validation.

The Python implementation uses only the standard library.

## Install Into A Project

Download or clone this repository:

```powershell
git clone https://github.com/<your-name>/<your-repo>.git
cd <your-repo>
```

Install into the project that should use handoff:

```powershell
.\install.ps1 -ProjectPath "C:\path\to\your-project"
```

Then verify from that project:

```powershell
cd "C:\path\to\your-project"
.\scripts\codex-handoff.ps1 doctor --json
```

If Python is installed in a custom location:

```powershell
$env:CODEX_HANDOFF_PYTHON = "C:\Python312\python.exe"
```

Full installation details are in [docs/INSTALL.md](docs/INSTALL.md).

## Basic Usage

Initialize or repair the scaffold:

```powershell
.\scripts\codex-handoff.ps1 init
.\scripts\codex-handoff.ps1 verify
```

Create or refresh a handoff:

```powershell
.\scripts\codex-handoff.ps1 auto --link
```

Check readiness:

```powershell
.\scripts\codex-handoff.ps1 status --json
.\scripts\codex-handoff.ps1 context-status --json
.\scripts\codex-handoff.ps1 completion-audit --json
```

Run local smoke verification:

```powershell
.\scripts\handoff-smoke.ps1 -SkipAuto
```

Full usage details are in [docs/USAGE.md](docs/USAGE.md).

## Recommended Handoff Flow

1. Run `.\scripts\codex-handoff.ps1 auto --link`.
2. Create a new Codex thread for the same workspace.
3. In the new thread, read:

```text
AGENTS.md
.codex-handoff/current_state.yaml
.codex-handoff/handoff.md
.codex-handoff/decisions.yaml
.codex-handoff/evidence_summary.md
.codex-handoff/evidence.jsonl
.codex-handoff/next_prompt.md
```

4. In the new thread, run:

```powershell
.\scripts\codex-handoff.ps1 accept-handoff --thread-id <new-thread-id> --json
```

5. Back in the original project, run:

```powershell
.\scripts\codex-handoff.ps1 completion-audit --json --write
```

## Current Boundary

Deep Link support is intentionally treated as an optional probe because behavior
depends on the installed Codex App build. The validated primary path is creating
a new Codex App thread for the same workspace and running `accept-handoff` in
that target thread.

## Publishing

Before publishing your fork, read [docs/PUBLISHING.md](docs/PUBLISHING.md) and
choose a license.

## License

MIT. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security guidance is in
[SECURITY.md](SECURITY.md).
