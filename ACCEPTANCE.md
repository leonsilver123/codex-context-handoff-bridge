# Codex Handoff Bridge Acceptance

## Implemented Requirements

| Requirement | Evidence |
| --- | --- |
| Project-local state source | `.codex-handoff/current_state.yaml`, `decisions.yaml`, `handoff.md`, `next_prompt.md`, `evidence.jsonl`, `thread_registry.json`, `risk_rules.yaml` |
| AGENTS.md bootstrap rules | `AGENTS.md` requires new threads to read handoff files before continuing |
| Structured handoff | `scripts/codex_handoff.py checkpoint` generates `handoff.md` with `FACT`, `DECISION`, `TODO`, `OPEN`, `REJECTED`, and `EVIDENCE` tags |
| Deep Link primary path | `scripts/codex_handoff.py link` emits `codex://threads/new?path=...&prompt=...` with URL encoding |
| Controlled Deep Link open attempt | `scripts/codex_handoff.py open-link --json --write` records a dry-run open plan; `--execute` intentionally asks the OS to open the generated `codex://` link |
| App Server enhancement boundary | `scripts/codex_handoff.py prepare-server` writes `.codex-handoff/app_server_request.json` with planned `thread/start` and `turn/start` bodies without executing unknown interfaces |
| Backend selection | `scripts/codex_handoff.py start --backend deep-link` and `start --backend app-server-plan` expose a stable backend interface |
| Optional App Server execution | `scripts/codex_handoff.py start --backend app-server-http --server-url ...` executes the planned `thread/start` and `turn/start` calls only when an explicit server URL is supplied |
| App Server HTTP self-test | `scripts/codex_handoff.py self-test app-server-http --json --write` verifies request ordering, thread id forwarding, and result parsing against an in-process fake server |
| Configurable backend defaults | `.codex-handoff/config.json` stores Deep Link prompt and App Server defaults; CLI flags override config values |
| HTTP execution audit | `.codex-handoff/app_server_result.json` records executed App Server request results, timeout, retry count, and thread id |
| Automatic compression entry | `scripts/codex_handoff.py auto --link` decides whether a checkpoint is needed, verifies it, and prints a Deep Link |
| Evidence log compression | `scripts/codex_handoff.py compact-evidence` archives the full evidence log, writes `.codex-handoff/evidence_summary.md`, and keeps a recent event tail; `auto --compact` invokes the same path |
| Context pressure estimation | `scripts/codex_handoff.py context-status --json` estimates token pressure for the files a new Codex thread must read and feeds the automatic compaction trigger |
| Duplicate prevention | `thread_registry.json` stores handoff hash and migration state; duplicate checkpoints within cooldown are suppressed |
| Migration tracking | `scripts/codex_handoff.py mark-migrated --thread-id <id>` marks the latest handoff as consumed |
| Safety scanning | `scripts/codex_handoff.py verify` checks secret patterns, required tags, JSONL structure, registry shape, and YAML-like schema keys |
| Machine-readable status | `scripts/codex_handoff.py status --json` emits JSON for future automation |
| External validation visibility | `scripts/codex_handoff.py status --json` reports the latest Deep Link open attempt and external acceptance summary |
| Health readiness check | `scripts/codex_handoff.py doctor --json` combines verification and status into a single handoff readiness report |
| Completion audit | `scripts/codex_handoff.py completion-audit --json --write` evaluates `.codex-handoff/requirements.json` and writes `.codex-handoff/completion_audit.json` |
| External acceptance recording | `scripts/codex_handoff.py record-external` records real Codex App validation into `.codex-handoff/external_acceptance.json` and feeds completion audit requirement `R012` |
| Target-thread handoff acceptance | `scripts/codex_handoff.py accept-handoff` records target-thread acceptance, writes `.codex-handoff/handoff_acceptance.json`, and marks the latest registry entry as migrated |
| Smoke verification entry | `scripts/handoff-smoke.ps1` runs test, verify, doctor, completion audit, maintenance dry-run, and optional auto handoff |
| Non-destructive probes | `scripts/codex_handoff.py probe deep-link` checks protocol registration without opening Codex; `probe app-server` checks TCP connectivity without sending thread payloads |
| Maintenance dry-run and safety checks | `scripts/codex_handoff.py maintenance --json --write` reports cleanup candidates; `--simulate-apply` verifies delete safety checks without deleting |
| Git optionality | Git state collection degrades to `unavailable` when `git` is not installed |
| Implementation drift capture | `handoff.md` records sha256 fingerprints for `AGENTS.md`, `README.md`, and core scripts |

## Verification Commands

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\test_codex_handoff.py
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py verify
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py auto --link
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py prepare-server
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py doctor --json
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py context-status --json --write
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py open-link --json --write
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py self-test app-server-http --json --write
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py compact-evidence --dry-run --json
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py completion-audit --json --write
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py record-external --target deep-link-open --status pass --evidence "Opened the generated codex:// link in the installed Codex App." --thread-id <new-thread-id>
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py record-external --target new-thread-read --status pass --evidence "The new thread read .codex-handoff/next_prompt.md and continued from the handoff."
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py probe deep-link --json --write
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py maintenance --json --write
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py maintenance --simulate-apply --json --write
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\codex_handoff.py start --backend deep-link
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\test_codex_handoff.py
.\scripts\handoff-smoke.ps1
```

Current verified results:

```text
test_codex_handoff: ok
verify: ok
```

## Known Boundaries

1. The first version is intentionally file-backed and local.
2. Deep Link opening must be tested in the installed Codex App UI.
3. App Server automatic thread creation requires either an explicit `--server-url` or a configured local `app_server.server_url`; the test suite verifies this execution path with a local fake server, not with the real Codex App Server.
4. The tool does not read true Codex internal token pressure, so automatic compression uses file-state and explicit-reason triggers.
5. Real Codex App validation should be recorded with `record-external`; until then, requirement `R012` remains a boundary item in the completion audit.
