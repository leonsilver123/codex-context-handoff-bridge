# Codex Project Instructions

## Working Boundary

1. Do not redesign confirmed architecture from scratch.
2. Before continuing a long task, read `.codex-handoff/current_state.yaml`.
3. Before changing architecture, read `.codex-handoff/decisions.yaml`.
4. Do not read `.env`, credentials, SSH keys, private data, or production configuration unless the user explicitly approves it.
5. Do not modify unrelated modules.
6. After code changes, run the relevant verification command.
7. When context becomes long or task state becomes mixed, create a structured handoff instead of relying on chat history.

## Handoff Rules

When creating a handoff, update these files:

- `.codex-handoff/current_state.yaml`
- `.codex-handoff/handoff.md`
- `.codex-handoff/next_prompt.md`
- `.codex-handoff/evidence.jsonl`
- `.codex-handoff/thread_registry.json`

The handoff must distinguish:

- `FACT`
- `DECISION`
- `TODO`
- `OPEN`
- `REJECTED`
- `EVIDENCE`

## New Thread Bootstrap

A new Codex thread must first read:

1. `AGENTS.md`
2. `.codex-handoff/current_state.yaml`
3. `.codex-handoff/handoff.md`
4. `.codex-handoff/decisions.yaml`
5. `.codex-handoff/evidence.jsonl`

It must then continue from `.codex-handoff/next_prompt.md`.
