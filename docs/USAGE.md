# Usage

Codex Handoff Bridge keeps long-running Codex context in project-local files
under `.codex-handoff/`. A new Codex thread can read those files and continue
without copying the whole old conversation.

## First Run

From your project root:

```powershell
.\scripts\codex-handoff.ps1 init
.\scripts\codex-handoff.ps1 verify
.\scripts\codex-handoff.ps1 doctor
```

## Create A Handoff

When a Codex thread is getting long or the task state is mixed:

```powershell
.\scripts\codex-handoff.ps1 auto --link
```

This refreshes:

```text
.codex-handoff/current_state.yaml
.codex-handoff/handoff.md
.codex-handoff/next_prompt.md
.codex-handoff/evidence.jsonl
.codex-handoff/thread_registry.json
```

It also prints a `codex://threads/new?...` link for optional Deep Link testing.

## Recommended Codex App Flow

1. Run:

```powershell
.\scripts\codex-handoff.ps1 auto --link
.\scripts\codex-handoff.ps1 external-next --json
```

2. Create a new Codex thread for the same workspace.

3. In the new thread, ask it to read:

```text
AGENTS.md
.codex-handoff/current_state.yaml
.codex-handoff/handoff.md
.codex-handoff/decisions.yaml
.codex-handoff/evidence_summary.md
.codex-handoff/evidence.jsonl
.codex-handoff/next_prompt.md
```

4. In the new target thread, run:

```powershell
.\scripts\codex-handoff.ps1 accept-handoff --thread-id <new-thread-id> --json
```

This records:

```text
codex-app-thread-create
new-thread-read
```

and marks the latest handoff as migrated.

## Check Status

```powershell
.\scripts\codex-handoff.ps1 status
.\scripts\codex-handoff.ps1 status --json
.\scripts\codex-handoff.ps1 context-status --json
```

`context-status` estimates the token pressure of the files the next thread must
read. If pressure is high, run:

```powershell
.\scripts\codex-handoff.ps1 compact-evidence --json
```

## Completion Audit

Run:

```powershell
.\scripts\codex-handoff.ps1 completion-audit --json --write
```

`overall_status: pass` means the local requirements and recorded external
acceptance evidence are currently satisfied.

## Deep Link Probe

Deep Link is optional because behavior depends on the installed Codex App build.

```powershell
.\scripts\codex-handoff.ps1 open-link --json --write
.\scripts\codex-handoff.ps1 open-link --execute --json --write
```

If Windows accepts the link but no visible target thread appears, record it as
unknown instead of pass.

## Maintenance

Dry-run cleanup candidates:

```powershell
.\scripts\codex-handoff.ps1 maintenance --json --write
```

Simulate delete safety checks without deleting:

```powershell
.\scripts\codex-handoff.ps1 maintenance --simulate-apply --json --write
```

Only use `--apply` when you intentionally want to remove old archives and test
workspaces.
