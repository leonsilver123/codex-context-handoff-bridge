#!/usr/bin/env python3
"""Project-local Codex context handoff helper.

This tool intentionally keeps the first version file-backed. It does not depend
on Codex App internals, App Server, browser automation, or git being installed.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path.cwd()
HANDOFF_DIR = ROOT / ".codex-handoff"
ARCHIVE_DIR = HANDOFF_DIR / "archive"
EXTERNAL_ACCEPTANCE_LOCK = HANDOFF_DIR / ".external_acceptance.lock"
COOLDOWN_SECONDS = 30 * 60
TEST_WORKSPACE_PREFIX = "codex_handoff_test_workspace_"
DEFAULT_EVIDENCE_MAX_EVENTS = 200
DEFAULT_EVIDENCE_KEEP_EVENTS = 80
DEFAULT_CONTEXT_MAX_TOKENS = 24000
DEFAULT_CONTEXT_WARN_RATIO = 0.75

REQUIRED_FILES = [
    ROOT / "AGENTS.md",
    HANDOFF_DIR / "current_state.yaml",
    HANDOFF_DIR / "decisions.yaml",
    HANDOFF_DIR / "evidence.jsonl",
    HANDOFF_DIR / "thread_registry.json",
    HANDOFF_DIR / "risk_rules.yaml",
    HANDOFF_DIR / "config.json",
    HANDOFF_DIR / "requirements.json",
    HANDOFF_DIR / "evidence_summary.md",
    HANDOFF_DIR / "external_acceptance.json",
    HANDOFF_DIR / "handoff.md",
    HANDOFF_DIR / "next_prompt.md",
]

DEFAULT_SHORT_PROMPT = "Continue this Codex project thread. First read .codex-handoff/next_prompt.md, then follow it exactly."


def escaped_workspace_path() -> str:
    return str(ROOT).replace("\\", "\\\\")


def default_agents_text() -> str:
    return """# Codex Project Instructions

## Context Handoff

Before continuing long-running work, read these files in order:

1. `AGENTS.md`
2. `.codex-handoff/current_state.yaml`
3. `.codex-handoff/handoff.md`
4. `.codex-handoff/decisions.yaml`
5. `.codex-handoff/evidence_summary.md`
6. `.codex-handoff/evidence.jsonl`
7. `.codex-handoff/next_prompt.md`

After a target thread accepts a handoff, run:

```powershell
.\\scripts\\codex-handoff.ps1 accept-handoff --thread-id <new-thread-id> --json
```

Do not copy full old conversations into new threads. Use the project-local
`.codex-handoff/` files as the durable context source.
"""


def default_current_state_text() -> str:
    return f"""project:
  name: "{ROOT.name}"
  workspace_path: "{escaped_workspace_path()}"
  codex_surface: "Codex App"
  handoff_strategy: "codex_app_thread_create_primary_deep_link_probe_app_server_optional"

current_thread:
  title: "Initial Codex handoff setup"
  status: "active"
  handoff_created: false
  migrated_to_new_thread: false

current_task:
  name: "Codex context handoff"
  objective: "Use project-local files to compress, verify, and hand off long-running Codex context."
  status: "initialized"

confirmed_decisions:
  - "Use project-local .codex-handoff/ as the durable context source."
  - "Use Codex App thread creation as the preferred real handoff path when available."
  - "Keep codex:// Deep Link as an optional probe."
  - "Do not treat internal chat compaction as a replacement for external handoff files."

rejected_options:
  - "Copying the complete old conversation into a new thread."
  - "Using UI automation clicks as the default migration mechanism."

open_issues:
  - "Real Codex App target-thread creation must be validated on this machine."
  - "Prompt length limits should be measured for this project."

next_actions:
  - "Run scripts/codex_handoff.py checkpoint."
  - "Run scripts/codex_handoff.py verify."
  - "Run scripts/codex_handoff.py external-next --json."
"""


def default_decisions_text() -> str:
    return """decisions:
  - id: "D001"
    status: "accepted"
    summary: "Use project-local .codex-handoff/ files as the durable context source."
    rationale: "Project files are inspectable, versionable, and more reliable than long chat history."
  - id: "D002"
    status: "accepted"
    summary: "Keep full handoff content in local files, not in a URL prompt."
    rationale: "Deep links and prompt fields have practical length and encoding limits."
  - id: "D003"
    status: "accepted"
    summary: "Use target-thread acceptance as the real migration proof."
    rationale: "A handoff is only complete after the target thread reads the files and records acceptance."
"""


def default_risk_rules_text() -> str:
    return """forbidden_paths:
  - ".env"
  - ".env.local"
  - "id_rsa"
  - "id_ed25519"
  - ".ssh/"
forbidden_commands:
  - "git reset --hard"
  - "git clean -fd"
  - "Remove-Item -Recurse -Force"
secret_patterns:
  - "sk-[A-Za-z0-9_-]{20,}"
  - "api[_-]?key\\s*[:=]"
  - "password\\s*[:=]"
"""


def default_requirements_payload() -> dict[str, object]:
    return {
        "version": 1,
        "requirements": [
            {
                "id": "R001",
                "name": "Project-local state source",
                "required_paths": [
                    "AGENTS.md",
                    ".codex-handoff/current_state.yaml",
                    ".codex-handoff/decisions.yaml",
                    ".codex-handoff/handoff.md",
                    ".codex-handoff/next_prompt.md",
                    ".codex-handoff/evidence.jsonl",
                    ".codex-handoff/thread_registry.json",
                    ".codex-handoff/risk_rules.yaml",
                    ".codex-handoff/config.json",
                    ".codex-handoff/evidence_summary.md",
                    ".codex-handoff/external_acceptance.json",
                ],
            },
            {
                "id": "R002",
                "name": "Structured handoff tags",
                "required_handoff_tags": ["[FACT]", "[DECISION]", "[TODO]", "[OPEN]", "[REJECTED]", "[EVIDENCE]"],
            },
            {
                "id": "R003",
                "name": "Core CLI commands",
                "required_cli_commands": [
                    "init",
                    "checkpoint",
                    "auto",
                    "verify",
                    "status",
                    "doctor",
                    "context-status",
                    "compact-evidence",
                    "external-next",
                    "accept-handoff",
                    "completion-audit",
                ],
            },
        ],
    }


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    last_error: OSError | None = None
    for _attempt in range(20):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05)
    if last_error is not None:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return


@contextlib.contextmanager
def file_lock(path: Path, timeout_seconds: float = 10.0, stale_seconds: float = 60.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        import msvcrt

        if path.exists() and path.is_dir():
            try:
                if time.time() - path.stat().st_mtime > stale_seconds:
                    path.rmdir()
            except OSError:
                pass
        with path.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            start = time.monotonic()
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() - start > timeout_seconds:
                        raise TimeoutError(f"timed out waiting for lock: {path}")
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    start = time.monotonic()
    while True:
        try:
            path.mkdir()
            break
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
                if age > stale_seconds:
                    if path.is_dir():
                        path.rmdir()
                    else:
                        path.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() - start > timeout_seconds:
                raise TimeoutError(f"timed out waiting for lock: {path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        for _attempt in range(20):
            try:
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
                break
            except FileNotFoundError:
                break
            except PermissionError:
                time.sleep(0.05)


def load_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError:
        return default


def load_config() -> dict[str, object]:
    default: dict[str, object] = {
        "deep_link": {"prompt": DEFAULT_SHORT_PROMPT},
        "app_server": {"server_url": None, "timeout_seconds": 10.0, "retries": 0},
        "auto": {
            "evidence_max_events": DEFAULT_EVIDENCE_MAX_EVENTS,
            "evidence_keep_events": DEFAULT_EVIDENCE_KEEP_EVENTS,
            "context_max_tokens": DEFAULT_CONTEXT_MAX_TOKENS,
            "context_warn_ratio": DEFAULT_CONTEXT_WARN_RATIO,
        },
    }
    config_path = HANDOFF_DIR / "config.json"
    payload = load_json(config_path, default)
    if not isinstance(payload, dict):
        return default
    merged = dict(default)
    for key in ["deep_link", "app_server", "auto"]:
        value = payload.get(key)
        base = dict(default.get(key, {})) if isinstance(default.get(key), dict) else {}
        if isinstance(value, dict):
            base.update(value)
        merged[key] = base
    return merged


def config_section(name: str) -> dict[str, object]:
    section = load_config().get(name, {})
    return section if isinstance(section, dict) else {}


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def handoff_fingerprint(value: str) -> str:
    normalized_lines = []
    for line in value.splitlines():
        if line.startswith("[FACT] Generated at:"):
            continue
        normalized_lines.append(line)
    return sha256_text("\n".join(normalized_lines).strip() + "\n")


def run_git(args: list[str]) -> tuple[str, str]:
    if shutil.which("git") is None:
        return "unavailable", "git executable was not found"
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return "unavailable", str(exc)
    if proc.returncode != 0:
        return "unavailable", (proc.stderr or proc.stdout).strip()
    return "ok", proc.stdout.strip()


def extract_yaml_list(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    values: list[str] = []
    in_key = False
    for line in lines:
        if re.match(rf"^{re.escape(key)}:\s*$", line):
            in_key = True
            continue
        if in_key:
            if line and not line.startswith((" ", "-")):
                break
            match = re.match(r"\s*-\s*(.+?)\s*$", line)
            if match:
                values.append(match.group(1).strip().strip('"'))
    return values


def extract_yaml_scalar(text: str, key: str, default: str = "") -> str:
    match = re.search(rf"^\s*{re.escape(key)}:\s*(.+?)\s*$", text, re.MULTILINE)
    if not match:
        return default
    return match.group(1).strip().strip('"')


def yaml_has_key(text: str, key: str) -> bool:
    return re.search(rf"^\s*(?:-\s*)?{re.escape(key)}:\s*", text, re.MULTILINE) is not None


def ensure_scaffold() -> None:
    HANDOFF_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(exist_ok=True)
    for path in REQUIRED_FILES:
        if not path.exists():
            if path.name == "AGENTS.md":
                write_text(path, default_agents_text())
            elif path.name == "current_state.yaml":
                write_text(path, default_current_state_text())
            elif path.name == "decisions.yaml":
                write_text(path, default_decisions_text())
            elif path.name == "risk_rules.yaml":
                write_text(path, default_risk_rules_text())
            elif path.name == "requirements.json":
                write_text(path, json.dumps(default_requirements_payload(), indent=2, ensure_ascii=False) + "\n")
            elif path.name == "thread_registry.json":
                write_text(path, json.dumps({"threads": []}, indent=2, ensure_ascii=False) + "\n")
            elif path.name == "config.json":
                write_text(path, json.dumps(load_config(), indent=2, ensure_ascii=False) + "\n")
            elif path.name == "external_acceptance.json":
                write_text(path, json.dumps({"version": 1, "validations": []}, indent=2, ensure_ascii=False) + "\n")
            elif path.name == "evidence_summary.md":
                write_text(path, "# Evidence Summary\n\nNo evidence compaction has been run yet.\n")
            elif path.name == "evidence.jsonl":
                append_evidence("FACT", "init", "Created missing evidence log.")
            else:
                write_text(path, "")


def append_evidence(kind: str, source: str, message: str, extra: dict[str, object] | None = None) -> None:
    payload: dict[str, object] = {
        "type": kind,
        "source": source,
        "message": message,
        "created_at": now_iso(),
    }
    if extra:
        payload.update(extra)
    with (HANDOFF_DIR / "evidence.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_evidence_events() -> list[dict[str, object]]:
    path = HANDOFF_DIR / "evidence.jsonl"
    events: list[dict[str, object]] = []
    if not path.exists():
        return events
    for line in read_text(path).splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def evidence_thresholds() -> tuple[int, int]:
    auto_config = config_section("auto")
    raw_max = auto_config.get("evidence_max_events")
    raw_keep = auto_config.get("evidence_keep_events")
    max_events = int(raw_max) if isinstance(raw_max, int) and raw_max > 0 else DEFAULT_EVIDENCE_MAX_EVENTS
    keep_events = int(raw_keep) if isinstance(raw_keep, int) and raw_keep > 0 else DEFAULT_EVIDENCE_KEEP_EVENTS
    keep_events = min(keep_events, max_events)
    return max_events, keep_events


def context_thresholds() -> tuple[int, float]:
    auto_config = config_section("auto")
    raw_max = auto_config.get("context_max_tokens")
    raw_ratio = auto_config.get("context_warn_ratio")
    max_tokens = int(raw_max) if isinstance(raw_max, int) and raw_max > 0 else DEFAULT_CONTEXT_MAX_TOKENS
    warn_ratio = float(raw_ratio) if isinstance(raw_ratio, (int, float)) and raw_ratio > 0 else DEFAULT_CONTEXT_WARN_RATIO
    warn_ratio = min(warn_ratio, 1.0)
    return max_tokens, warn_ratio


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, (ascii_chars + 3) // 4 + non_ascii_chars)


def handoff_context_files() -> list[Path]:
    return [
        ROOT / "AGENTS.md",
        HANDOFF_DIR / "current_state.yaml",
        HANDOFF_DIR / "handoff.md",
        HANDOFF_DIR / "decisions.yaml",
        HANDOFF_DIR / "evidence_summary.md",
        HANDOFF_DIR / "evidence.jsonl",
        HANDOFF_DIR / "next_prompt.md",
    ]


def context_pressure_payload() -> dict[str, object]:
    max_tokens, warn_ratio = context_thresholds()
    files: list[dict[str, object]] = []
    total_chars = 0
    total_tokens = 0
    for path in handoff_context_files():
        if not path.exists():
            files.append({"path": str(path.relative_to(ROOT)), "exists": False, "chars": 0, "estimated_tokens": 0})
            continue
        text = read_text(path)
        chars = len(text)
        tokens = estimate_tokens(text)
        total_chars += chars
        total_tokens += tokens
        files.append(
            {
                "path": str(path.relative_to(ROOT)),
                "exists": True,
                "chars": chars,
                "estimated_tokens": tokens,
                "sha256": sha256_file(path),
            }
        )
    warn_tokens = int(max_tokens * warn_ratio)
    return {
        "created_at": now_iso(),
        "workspace": str(ROOT),
        "max_tokens": max_tokens,
        "warn_ratio": warn_ratio,
        "warn_tokens": warn_tokens,
        "estimated_tokens": total_tokens,
        "chars": total_chars,
        "pressure_ratio": round(total_tokens / max_tokens, 4) if max_tokens else 0,
        "needs_compaction": total_tokens >= warn_tokens,
        "over_limit": total_tokens > max_tokens,
        "files": files,
    }


def summarize_evidence(events: list[dict[str, object]], archive_path: Path | None, kept_count: int) -> str:
    by_type: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("type", "unknown"))
        source = str(event.get("source", "unknown"))
        by_type[event_type] = by_type.get(event_type, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
    first = events[0].get("created_at") if events else None
    last = events[-1].get("created_at") if events else None
    lines = [
        "# Evidence Summary",
        "",
        f"- generated_at: {now_iso()}",
        f"- original_event_count: {len(events)}",
        f"- kept_recent_events: {kept_count}",
        f"- first_event_at: {first or 'n/a'}",
        f"- last_event_at: {last or 'n/a'}",
    ]
    if archive_path is not None:
        lines.append(f"- archived_full_log: `{archive_path.relative_to(ROOT)}`")
    lines.extend(["", "## Counts By Type", ""])
    for key in sorted(by_type):
        lines.append(f"- {key}: {by_type[key]}")
    lines.extend(["", "## Counts By Source", ""])
    for key in sorted(by_source):
        lines.append(f"- {key}: {by_source[key]}")
    lines.extend(["", "## Recent Events", ""])
    for event in events[-min(10, len(events)):]:
        lines.append(
            "- "
            + str(event.get("created_at", "n/a"))
            + " | "
            + str(event.get("type", "unknown"))
            + " | "
            + str(event.get("source", "unknown"))
            + " | "
            + str(event.get("message", "")).replace("\n", " ")
        )
    return "\n".join(lines).rstrip() + "\n"


def compact_evidence(max_events: int, keep_events: int, force: bool = False, dry_run: bool = False) -> tuple[bool, dict[str, object]]:
    ensure_scaffold()
    events = read_evidence_events()
    would_compact = force or len(events) > max_events
    result: dict[str, object] = {
        "event_count": len(events),
        "max_events": max_events,
        "keep_events": keep_events,
        "would_compact": would_compact,
        "compacted": False,
        "dry_run": dry_run,
        "archive": None,
    }
    if dry_run:
        return would_compact, result
    if not would_compact:
        write_text(HANDOFF_DIR / "evidence_summary.md", summarize_evidence(events, None, len(events)))
        return False, result
    ARCHIVE_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    archive_path = ARCHIVE_DIR / f"{stamp}_evidence.jsonl"
    evidence_path = HANDOFF_DIR / "evidence.jsonl"
    if evidence_path.exists():
        shutil.copy2(evidence_path, archive_path)
    kept = events[-keep_events:] if keep_events > 0 else []
    summary = summarize_evidence(events, archive_path, len(kept))
    write_text(HANDOFF_DIR / "evidence_summary.md", summary)
    lines = [json.dumps(event, ensure_ascii=False) for event in kept]
    compaction_event = {
        "type": "EVIDENCE",
        "source": "compact-evidence",
        "message": "Compacted evidence log into summary and archive.",
        "created_at": now_iso(),
        "archive": str(archive_path.relative_to(ROOT)),
        "original_event_count": len(events),
        "kept_recent_events": len(kept),
    }
    lines.append(json.dumps(compaction_event, ensure_ascii=False))
    write_text(evidence_path, "\n".join(lines).rstrip() + "\n")
    result.update({"compacted": True, "archive": str(archive_path.relative_to(ROOT))})
    return True, result


def make_handoff() -> str:
    state = read_text(HANDOFF_DIR / "current_state.yaml")
    decisions = read_text(HANDOFF_DIR / "decisions.yaml")
    git_status_state, git_status = run_git(["status", "--short"])
    git_stat_state, git_stat = run_git(["diff", "--stat"])

    objective = extract_yaml_scalar(state, "objective", "Continue the current Codex project task.")
    task_name = extract_yaml_scalar(state, "name", "Codex context handoff")
    confirmed = extract_yaml_list(state, "confirmed_decisions")
    rejected = extract_yaml_list(state, "rejected_options")
    open_issues = extract_yaml_list(state, "open_issues")
    next_actions = extract_yaml_list(state, "next_actions")

    lines = [
        "# Codex Thread Handoff",
        "",
        "## 1. Current Goal",
        "",
        f"[FACT] Current task: {task_name}",
        f"[FACT] Objective: {objective}",
        "",
        "## 2. Current Environment",
        "",
        f"[FACT] Workspace path: `{ROOT}`",
        "[FACT] Context state source: `.codex-handoff/`",
        f"[FACT] Generated at: {now_iso()}",
        "",
        "## 3. Confirmed Decisions",
        "",
    ]
    lines.extend([f"[DECISION] {item}" for item in confirmed] or ["[DECISION] No confirmed decisions were found."])
    lines.extend(["", "## 4. Rejected Options", ""])
    lines.extend([f"[REJECTED] {item}" for item in rejected] or ["[REJECTED] No rejected options were found."])
    lines.extend(["", "## 5. Open Risks", ""])
    lines.extend([f"[OPEN] {item}" for item in open_issues] or ["[OPEN] No open issues were found."])
    lines.extend(["", "## 6. Git State", ""])
    lines.append(f"[EVIDENCE] git status: {git_status_state}")
    lines.append("```text")
    lines.append(git_status or "(empty)")
    lines.append("```")
    lines.append(f"[EVIDENCE] git diff --stat: {git_stat_state}")
    lines.append("```text")
    lines.append(git_stat or "(empty)")
    lines.append("```")
    lines.extend(["", "## 7. Implementation State", ""])
    manifest_files = [
        ROOT / "AGENTS.md",
        ROOT / "ACCEPTANCE.md",
        ROOT / "README.md",
        HANDOFF_DIR / "config.json",
        HANDOFF_DIR / "requirements.json",
        HANDOFF_DIR / "app_server_request.json",
        HANDOFF_DIR / "app_server_result.json",
        HANDOFF_DIR / "app_server_self_test.json",
        HANDOFF_DIR / "completion_audit.json",
        HANDOFF_DIR / "context_status.json",
        HANDOFF_DIR / "deep_link_open_attempt.json",
        HANDOFF_DIR / "evidence_summary.md",
        HANDOFF_DIR / "external_acceptance.json",
        HANDOFF_DIR / "handoff_acceptance.json",
        HANDOFF_DIR / "maintenance_report.json",
        HANDOFF_DIR / "probe_deep_link.json",
        HANDOFF_DIR / "probe_app_server.json",
        ROOT / "scripts/codex_handoff.py",
        ROOT / "scripts/test_codex_handoff.py",
        ROOT / "scripts/handoff-smoke.ps1",
    ]
    for path in manifest_files:
        if path.exists():
            lines.append(f"[EVIDENCE] `{path.relative_to(ROOT)}` {sha256_file(path)}")
    lines.extend(["", "## 8. Decision Ledger Snapshot", "", "```yaml", decisions.strip(), "```"])
    lines.extend(["", "## 9. Next Steps", ""])
    lines.extend([f"[TODO] {item}" for item in next_actions] or ["[TODO] Ask the user for the next task."])
    return "\n".join(lines).rstrip() + "\n"


def make_next_prompt() -> str:
    return f"""You are continuing an existing Codex project thread. Do not start from scratch.

Before making design or code changes, read:

1. `AGENTS.md`
2. `.codex-handoff/current_state.yaml`
3. `.codex-handoff/handoff.md`
4. `.codex-handoff/decisions.yaml`
5. `.codex-handoff/evidence_summary.md`
6. `.codex-handoff/evidence.jsonl`

Current task:

Continue the Codex App project-local context compression bridge in `{ROOT}`.

Hard requirements:

1. Do not revisit the rejected UI automation plan.
2. Do not treat internal compact as a replacement for structured handoff files.
3. Prefer the Deep Link implementation first.
4. Treat App Server as a second-stage enhancement.
5. Keep durable state in project files, not only in chat.
6. Report which context files were read before making changes.
7. After confirming these files were read, run `scripts/codex_handoff.py accept-handoff --json` to record handoff acceptance.

Expected output:

1. Current understanding.
2. Files to create or modify.
3. Implementation steps.
4. Risks.
5. Next recommended action.
"""


def build_deep_link(prompt: str | None = None) -> str:
    configured_prompt = config_section("deep_link").get("prompt")
    short_prompt = prompt or (configured_prompt if isinstance(configured_prompt, str) else DEFAULT_SHORT_PROMPT)
    return f"codex://threads/new?path={quote(str(ROOT), safe='')}&prompt={quote(short_prompt, safe='')}"


def open_deep_link(link: str) -> tuple[bool, str | None]:
    try:
        if sys.platform.startswith("win"):
            os.startfile(link)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", link], check=True, capture_output=True, text=True)
        else:
            subprocess.run(["xdg-open", link], check=True, capture_output=True, text=True)
        return True, None
    except Exception as exc:
        return False, str(exc)


def build_app_server_request() -> dict[str, object]:
    prompt_path = HANDOFF_DIR / "next_prompt.md"
    prompt_text = read_text(prompt_path) if prompt_path.exists() else make_next_prompt()
    return {
        "version": 1,
        "created_at": now_iso(),
        "workspace_path": str(ROOT),
        "strategy": "app_server_second_stage_plan",
        "status": "planned_not_executed",
        "requests": [
            {
                "endpoint": "thread/start",
                "method": "POST",
                "body": {
                    "cwd": str(ROOT),
                    "handoff_source": str(HANDOFF_DIR.relative_to(ROOT)),
                },
            },
            {
                "endpoint": "turn/start",
                "method": "POST",
                "body": {
                    "prompt": prompt_text,
                    "requires_prior_response": "thread/start",
                },
            },
        ],
        "notes": [
            "This file is a reviewable plan for the App Server enhancement layer.",
            "The CLI executes App Server calls only through the explicit app-server-http backend and an explicit or configured server URL.",
            "Deep Link remains the primary supported backend.",
        ],
    }


def load_external_acceptance() -> dict[str, object]:
    payload = load_json(HANDOFF_DIR / "external_acceptance.json", {"version": 1, "validations": []})
    if not isinstance(payload, dict):
        return {"version": 1, "validations": []}
    validations = payload.get("validations")
    if not isinstance(validations, list):
        payload["validations"] = []
    return payload


def latest_external_validation(target: str) -> dict[str, object] | None:
    payload = load_external_acceptance()
    validations = payload.get("validations", [])
    if not isinstance(validations, list):
        return None
    latest: dict[str, object] | None = None
    for item in validations:
        if not isinstance(item, dict):
            continue
        if item.get("target") == target:
            latest = item
    return latest


def record_external_validation(
    target: str,
    status: str,
    evidence: str | None = None,
    thread_id: str | None = None,
) -> dict[str, object]:
    ensure_scaffold()
    out_path = HANDOFF_DIR / "external_acceptance.json"
    with file_lock(EXTERNAL_ACCEPTANCE_LOCK):
        payload = load_external_acceptance()
        validations = payload.setdefault("validations", [])
        if not isinstance(validations, list):
            validations = []
            payload["validations"] = validations
        entry: dict[str, object] = {
            "target": target,
            "status": status,
            "recorded_at": now_iso(),
            "evidence": evidence or "",
        }
        if thread_id:
            entry["thread_id"] = thread_id
        validations.append(entry)
        payload["version"] = 1
        payload["updated_at"] = now_iso()
        write_json_atomic(out_path, payload)
    append_evidence(
        "EVIDENCE",
        "record-external",
        "Recorded external Codex App acceptance result.",
        {"target": target, "status": status, "acceptance_file": str(out_path)},
    )
    return entry


def command_record_external(args: argparse.Namespace) -> int:
    entry = record_external_validation(args.target, args.status, args.evidence, args.thread_id)
    if args.json:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
    else:
        print(f"external_acceptance: {HANDOFF_DIR / 'external_acceptance.json'}")
        print(f"target: {args.target}")
        print(f"status: {args.status}")
    return 0


def external_next_payload() -> dict[str, object]:
    targets = ["codex-app-thread-create", "new-thread-read"]
    latest_by_target: dict[str, object] = {}
    missing_or_unresolved: list[str] = []
    for target in targets:
        validation = latest_external_validation(target)
        if validation is None:
            latest_by_target[target] = {"status": "missing", "evidence": ""}
            missing_or_unresolved.append(target)
            continue
        status = validation.get("status", "unknown")
        latest_by_target[target] = validation
        if status != "pass":
            missing_or_unresolved.append(target)

    return {
        "created_at": now_iso(),
        "workspace": str(ROOT),
        "complete": not missing_or_unresolved,
        "missing_or_unresolved": missing_or_unresolved,
        "latest_by_target": latest_by_target,
        "latest_open_attempt": load_json(HANDOFF_DIR / "deep_link_open_attempt.json", None),
        "deep_link": build_deep_link(),
        "target_thread_must_read": [
            "AGENTS.md",
            ".codex-handoff/current_state.yaml",
            ".codex-handoff/handoff.md",
            ".codex-handoff/decisions.yaml",
            ".codex-handoff/evidence_summary.md",
            ".codex-handoff/evidence.jsonl",
            ".codex-handoff/next_prompt.md",
        ],
        "steps": [
            "scripts/codex_handoff.py auto --link",
            "Create a new Codex App thread for this same workspace.",
            "In the target thread, read .codex-handoff/next_prompt.md and every file it lists.",
            "In the target thread, run scripts/codex_handoff.py accept-handoff --thread-id <new-thread-id> --json.",
            "Run scripts/codex_handoff.py completion-audit --json --write.",
        ],
        "deep_link_probe_steps": [
            "scripts/codex_handoff.py open-link --json --write",
            "scripts/codex_handoff.py open-link --execute --json --write",
            "Confirm whether a new Codex thread is visible for this workspace.",
        ],
        "fallback_if_no_new_thread": [
            "scripts/codex_handoff.py record-external --target codex-app-thread-create --status unknown --evidence \"A target Codex App thread could not be created for this workspace.\"",
            "scripts/codex_handoff.py record-external --target new-thread-read --status unknown --evidence \"No target thread was available to confirm handoff file reading.\"",
        ],
    }


def command_external_next(args: argparse.Namespace) -> int:
    payload = external_next_payload()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"complete: {payload['complete']}")
        print(f"workspace: {payload['workspace']}")
        unresolved = payload.get("missing_or_unresolved", [])
        print("missing_or_unresolved:")
        for item in unresolved if isinstance(unresolved, list) else []:
            print(f"- {item}")
        print("steps:")
        steps = payload.get("steps", [])
        for item in steps if isinstance(steps, list) else []:
            print(f"- {item}")
    return 0


def app_server_post(
    server_url: str,
    endpoint: str,
    body: dict[str, object],
    timeout: float = 10.0,
    retries: int = 0,
) -> dict[str, object]:
    base = server_url.rstrip("/")
    url = f"{base}/{endpoint.lstrip('/')}"
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    attempts = max(0, retries) + 1
    last_error: RuntimeError | None = None
    raw = ""
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
            last_error = None
            break
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"App Server HTTP {exc.code} from {endpoint}: {detail}")
            if 400 <= exc.code < 500:
                break
        except URLError as exc:
            last_error = RuntimeError(f"App Server connection failed for {endpoint}: {exc.reason}")
        if attempt < attempts:
            continue
    if last_error is not None:
        raise last_error
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"App Server returned non-JSON response from {endpoint}: {raw}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"App Server response from {endpoint} must be a JSON object")
    return data


def execute_app_server_plan(server_url: str, timeout: float = 10.0, retries: int = 0) -> dict[str, object]:
    plan = build_app_server_request()
    requests = plan.get("requests")
    if not isinstance(requests, list):
        raise RuntimeError("App Server plan has no requests list")
    results: list[dict[str, object]] = []
    thread_id: str | None = None
    for item in requests:
        if not isinstance(item, dict):
            raise RuntimeError("App Server request item must be an object")
        endpoint = item.get("endpoint")
        body = item.get("body")
        if not isinstance(endpoint, str) or not isinstance(body, dict):
            raise RuntimeError("App Server request item requires endpoint and body")
        if endpoint == "turn/start" and thread_id:
            body = dict(body)
            body["thread_id"] = thread_id
        response = app_server_post(server_url, endpoint, body, timeout=timeout, retries=retries)
        if endpoint == "thread/start":
            candidate = response.get("thread_id") or response.get("id")
            if isinstance(candidate, str):
                thread_id = candidate
        results.append({"endpoint": endpoint, "response": response})
    return {
        "version": 1,
        "created_at": now_iso(),
        "workspace_path": str(ROOT),
        "server_url": server_url,
        "status": "executed",
        "timeout_seconds": timeout,
        "retries": retries,
        "thread_id": thread_id,
        "results": results,
    }


class FakeAppServerHandler(BaseHTTPRequestHandler):
    calls: list[dict[str, object]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        payload = json.loads(raw) if raw.strip() else {}
        self.__class__.calls.append({"path": self.path, "body": payload})
        if self.path == "/thread/start":
            response = {"thread_id": "self-test-thread-id"}
        elif self.path == "/turn/start":
            response = {"turn_id": "self-test-turn-id", "thread_id": payload.get("thread_id")}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def run_app_server_self_test(timeout: float = 10.0) -> dict[str, object]:
    FakeAppServerHandler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAppServerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    server_url = f"http://{host}:{port}"
    try:
        result = execute_app_server_plan(server_url, timeout=timeout, retries=0)
    finally:
        server.shutdown()
        server.server_close()
    paths = [call.get("path") for call in FakeAppServerHandler.calls]
    turn_body = FakeAppServerHandler.calls[1].get("body") if len(FakeAppServerHandler.calls) > 1 else {}
    thread_id_forwarded = isinstance(turn_body, dict) and turn_body.get("thread_id") == "self-test-thread-id"
    passed = (
        paths == ["/thread/start", "/turn/start"]
        and result.get("thread_id") == "self-test-thread-id"
        and thread_id_forwarded
    )
    return {
        "created_at": now_iso(),
        "workspace": str(ROOT),
        "target": "app-server-http",
        "server_url": server_url,
        "passed": passed,
        "paths": paths,
        "thread_id_forwarded": thread_id_forwarded,
        "result": result,
    }


def parse_iso(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def find_recent_duplicate(threads: list[object], handoff_hash: str) -> dict[str, object] | None:
    current = dt.datetime.now(dt.timezone.utc).astimezone()
    for entry in reversed(threads):
        if not isinstance(entry, dict):
            continue
        if entry.get("handoff_hash") != handoff_hash:
            continue
        created_raw = entry.get("handoff_created_at")
        if not isinstance(created_raw, str):
            continue
        created = parse_iso(created_raw)
        if created is None:
            continue
        if (current - created).total_seconds() <= COOLDOWN_SECONDS:
            return entry
    return None


def update_registry(handoff_text: str, force: bool = False) -> tuple[Path | None, bool]:
    registry_path = HANDOFF_DIR / "thread_registry.json"
    registry = load_json(registry_path, {"threads": []})
    if not isinstance(registry, dict):
        registry = {"threads": []}
    threads = registry.setdefault("threads", [])
    if not isinstance(threads, list):
        registry["threads"] = threads = []

    handoff_hash = handoff_fingerprint(handoff_text)
    if not force:
        duplicate = find_recent_duplicate(threads, handoff_hash)
        if duplicate:
            existing = duplicate.get("handoff_file")
            if isinstance(existing, str):
                return ROOT / existing, False
            return None, False

    ARCHIVE_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    archive_path = ARCHIVE_DIR / f"{stamp}_handoff.md"
    write_text(archive_path, handoff_text)
    threads.append(
        {
            "source_thread_title": "unknown",
            "source_thread_id": None,
            "workspace_path": str(ROOT),
            "handoff_file": str(archive_path.relative_to(ROOT)),
            "handoff_hash": handoff_hash,
            "handoff_created_at": now_iso(),
            "new_thread_created": False,
            "migrated": False,
        }
    )
    write_text(registry_path, json.dumps(registry, indent=2, ensure_ascii=False) + "\n")
    return archive_path, True


def command_init(_: argparse.Namespace) -> int:
    ensure_scaffold()
    handoff_path = HANDOFF_DIR / "handoff.md"
    next_prompt_path = HANDOFF_DIR / "next_prompt.md"
    if not handoff_path.exists() or not read_text(handoff_path).strip():
        write_text(handoff_path, make_handoff())
    if not next_prompt_path.exists() or not read_text(next_prompt_path).strip():
        write_text(next_prompt_path, make_next_prompt())
    append_evidence("FACT", "init", "Initialized or repaired Codex handoff scaffold.")
    print(f"initialized: {HANDOFF_DIR}")
    return 0


def command_checkpoint(args: argparse.Namespace) -> int:
    ensure_scaffold()
    handoff_text = make_handoff()
    write_text(HANDOFF_DIR / "handoff.md", handoff_text)
    write_text(HANDOFF_DIR / "next_prompt.md", make_next_prompt())
    archive_path, created = update_registry(handoff_text, force=args.force)
    if created:
        append_evidence("EVIDENCE", "checkpoint", "Created handoff checkpoint.", {"archive": str(archive_path)})
    else:
        append_evidence("EVIDENCE", "checkpoint", "Skipped duplicate checkpoint during cooldown.", {"archive": str(archive_path)})
    print(f"handoff: {HANDOFF_DIR / 'handoff.md'}")
    print(f"next_prompt: {HANDOFF_DIR / 'next_prompt.md'}")
    print(f"archive: {archive_path}")
    print(f"archive_created: {created}")
    return 0


def needs_auto_checkpoint(reason: str | None = None) -> tuple[bool, str]:
    handoff = HANDOFF_DIR / "handoff.md"
    if reason:
        return True, f"explicit reason: {reason}"
    if not handoff.exists():
        return True, "handoff.md is missing"
    watched = [
        ROOT / "AGENTS.md",
        ROOT / "ACCEPTANCE.md",
        ROOT / "README.md",
        HANDOFF_DIR / "current_state.yaml",
        HANDOFF_DIR / "decisions.yaml",
        HANDOFF_DIR / "risk_rules.yaml",
        HANDOFF_DIR / "app_server_request.json",
        HANDOFF_DIR / "app_server_result.json",
        HANDOFF_DIR / "completion_audit.json",
        HANDOFF_DIR / "evidence_summary.md",
        HANDOFF_DIR / "external_acceptance.json",
        HANDOFF_DIR / "maintenance_report.json",
        HANDOFF_DIR / "probe_deep_link.json",
        HANDOFF_DIR / "probe_app_server.json",
    ]
    handoff_mtime = handoff.stat().st_mtime
    for path in watched:
        if path.exists() and path.stat().st_mtime > handoff_mtime:
            return True, f"{path.relative_to(ROOT)} is newer than handoff.md"
    scripts_dir = ROOT / "scripts"
    if scripts_dir.exists():
        for path in scripts_dir.rglob("*"):
            if path.is_file() and path.stat().st_mtime > handoff_mtime:
                return True, f"{path.relative_to(ROOT)} is newer than handoff.md"
    return False, "handoff is current"


def command_auto(args: argparse.Namespace) -> int:
    ensure_scaffold()
    max_events, keep_events = evidence_thresholds()
    event_count = len(read_evidence_events())
    if args.compact or event_count > max_events:
        compacted, compaction = compact_evidence(max_events=max_events, keep_events=keep_events, force=args.compact)
        if compacted:
            append_evidence("EVIDENCE", "auto", "Automatic evidence compaction completed.", compaction)
            args.reason = args.reason or "evidence log exceeded automatic compression threshold"
    pressure = context_pressure_payload()
    if pressure.get("needs_compaction"):
        compacted, compaction = compact_evidence(max_events=max_events, keep_events=keep_events, force=True)
        if compacted:
            append_evidence("EVIDENCE", "auto", "Automatic context-pressure compaction completed.", compaction)
        args.reason = args.reason or f"context pressure reached {pressure.get('estimated_tokens')} estimated tokens"
    needed, trigger = needs_auto_checkpoint(args.reason)
    print(f"auto_trigger: {trigger}")
    if needed:
        checkpoint_args = argparse.Namespace(force=args.force)
        result = command_checkpoint(checkpoint_args)
        if result != 0:
            return result
    else:
        append_evidence("EVIDENCE", "auto", "Skipped automatic checkpoint.", {"trigger": trigger})

    verify_result = command_verify(argparse.Namespace())
    if verify_result != 0:
        return verify_result
    if args.link:
        return command_link(argparse.Namespace(prompt=None))
    return 0


def command_compact_evidence(args: argparse.Namespace) -> int:
    ensure_scaffold()
    max_events = args.max_events
    keep_events = args.keep_events
    if max_events is None or keep_events is None:
        configured_max, configured_keep = evidence_thresholds()
        max_events = configured_max if max_events is None else max_events
        keep_events = configured_keep if keep_events is None else keep_events
    if max_events <= 0 or keep_events <= 0:
        print("max-events and keep-events must be positive", file=sys.stderr)
        return 1
    keep_events = min(keep_events, max_events)
    compacted, result = compact_evidence(max_events=max_events, keep_events=keep_events, force=args.force, dry_run=args.dry_run)
    if result.get("compacted") is True:
        append_evidence("EVIDENCE", "compact-evidence", "Compacted evidence log by explicit command.", result)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"event_count: {result['event_count']}")
        print(f"max_events: {result['max_events']}")
        print(f"keep_events: {result['keep_events']}")
        print(f"would_compact: {result['would_compact']}")
        print(f"dry_run: {result['dry_run']}")
        print(f"compacted: {result['compacted']}")
        if result.get("archive"):
            print(f"archive: {result['archive']}")
    return 0


def command_context_status(args: argparse.Namespace) -> int:
    ensure_scaffold()
    payload = context_pressure_payload()
    if args.write:
        out_path = HANDOFF_DIR / "context_status.json"
        write_text(out_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        append_evidence(
            "EVIDENCE",
            "context-status",
            "Wrote context pressure report.",
            {"context_status_file": str(out_path), "estimated_tokens": payload.get("estimated_tokens")},
        )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"estimated_tokens: {payload['estimated_tokens']}")
        print(f"warn_tokens: {payload['warn_tokens']}")
        print(f"max_tokens: {payload['max_tokens']}")
        print(f"needs_compaction: {payload['needs_compaction']}")
        print(f"over_limit: {payload['over_limit']}")
    return 0


def command_self_test(args: argparse.Namespace) -> int:
    ensure_scaffold()
    if args.target != "app-server-http":
        print(f"unsupported self-test target: {args.target}", file=sys.stderr)
        return 1
    result = run_app_server_self_test(timeout=args.timeout)
    if args.write:
        out_path = HANDOFF_DIR / "app_server_self_test.json"
        write_text(out_path, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        append_evidence(
            "EVIDENCE",
            "self-test",
            "Ran App Server HTTP self-test.",
            {"self_test_file": str(out_path), "passed": result.get("passed")},
        )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"target: {result['target']}")
        print(f"passed: {result['passed']}")
        print(f"paths: {result['paths']}")
        print(f"thread_id_forwarded: {result['thread_id_forwarded']}")
    return 0 if result.get("passed") is True else 1


def load_risk_patterns() -> list[str]:
    path = HANDOFF_DIR / "risk_rules.yaml"
    if not path.exists():
        return []
    text = read_text(path)
    return extract_yaml_list(text, "secret_patterns")


def validate_jsonl(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"missing JSONL file: {path}"]
    for idx, line in enumerate(read_text(path).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{idx} invalid JSON: {exc.msg}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{path.name}:{idx} must be a JSON object")
            continue
        for key in ["type", "source", "message", "created_at"]:
            if key not in payload:
                errors.append(f"{path.name}:{idx} missing key: {key}")
    return errors


def validate_state_schema() -> list[str]:
    state_path = HANDOFF_DIR / "current_state.yaml"
    if not state_path.exists():
        return [f"missing required file: {state_path}"]
    text = read_text(state_path)
    errors: list[str] = []
    for key in [
        "project",
        "workspace_path",
        "current_thread",
        "current_task",
        "objective",
        "confirmed_decisions",
        "rejected_options",
        "open_issues",
        "next_actions",
    ]:
        if not yaml_has_key(text, key):
            errors.append(f"current_state.yaml missing key: {key}")
    workspace = extract_yaml_scalar(text, "workspace_path")
    if workspace and Path(workspace) != ROOT:
        errors.append(f"current_state.yaml workspace_path mismatch: {workspace}")
    return errors


def validate_decisions_schema() -> list[str]:
    path = HANDOFF_DIR / "decisions.yaml"
    if not path.exists():
        return [f"missing required file: {path}"]
    text = read_text(path)
    errors: list[str] = []
    if not yaml_has_key(text, "decisions"):
        errors.append("decisions.yaml missing key: decisions")
    for key in ["id", "status", "summary", "rationale"]:
        if not yaml_has_key(text, key):
            errors.append(f"decisions.yaml missing decision field: {key}")
    return errors


def validate_risk_schema() -> list[str]:
    path = HANDOFF_DIR / "risk_rules.yaml"
    if not path.exists():
        return [f"missing required file: {path}"]
    text = read_text(path)
    errors: list[str] = []
    for key in ["forbidden_paths", "forbidden_commands", "secret_patterns"]:
        if not yaml_has_key(text, key):
            errors.append(f"risk_rules.yaml missing key: {key}")
        elif not extract_yaml_list(text, key):
            errors.append(f"risk_rules.yaml has empty list: {key}")
    return errors


def validate_registry_schema() -> list[str]:
    registry = load_json(HANDOFF_DIR / "thread_registry.json", {})
    errors: list[str] = []
    if not isinstance(registry, dict) or not isinstance(registry.get("threads"), list):
        return ["thread_registry.json must contain a top-level threads list"]
    for idx, entry in enumerate(registry["threads"]):
        if not isinstance(entry, dict):
            errors.append(f"thread_registry.json threads[{idx}] must be an object")
            continue
        for key in ["workspace_path", "handoff_file", "handoff_hash", "handoff_created_at", "migrated"]:
            if key not in entry:
                errors.append(f"thread_registry.json threads[{idx}] missing key: {key}")
        handoff_hash = entry.get("handoff_hash")
        if isinstance(handoff_hash, str) and not handoff_hash.startswith("sha256:"):
            errors.append(f"thread_registry.json threads[{idx}] has invalid handoff_hash")
    return errors


def validate_app_server_plan() -> list[str]:
    path = HANDOFF_DIR / "app_server_request.json"
    if not path.exists():
        return []
    payload = load_json(path, {})
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["app_server_request.json must contain a JSON object"]
    if payload.get("status") != "planned_not_executed":
        errors.append("app_server_request.json status must be planned_not_executed")
    if payload.get("workspace_path") != str(ROOT):
        errors.append("app_server_request.json workspace_path mismatch")
    requests = payload.get("requests")
    if not isinstance(requests, list) or len(requests) < 2:
        errors.append("app_server_request.json must include thread/start and turn/start requests")
        return errors
    endpoints = [item.get("endpoint") for item in requests if isinstance(item, dict)]
    for endpoint in ["thread/start", "turn/start"]:
        if endpoint not in endpoints:
            errors.append(f"app_server_request.json missing endpoint: {endpoint}")
    return errors


def validate_app_server_result() -> list[str]:
    path = HANDOFF_DIR / "app_server_result.json"
    if not path.exists():
        return []
    payload = load_json(path, {})
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["app_server_result.json must contain a JSON object"]
    if payload.get("status") != "executed":
        errors.append("app_server_result.json status must be executed")
    if payload.get("workspace_path") != str(ROOT):
        errors.append("app_server_result.json workspace_path mismatch")
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        errors.append("app_server_result.json must include non-empty results")
    return errors


def validate_app_server_self_test() -> list[str]:
    path = HANDOFF_DIR / "app_server_self_test.json"
    if not path.exists():
        return []
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        return ["app_server_self_test.json must contain a JSON object"]
    errors: list[str] = []
    if payload.get("target") != "app-server-http":
        errors.append("app_server_self_test.json target must be app-server-http")
    if not isinstance(payload.get("passed"), bool):
        errors.append("app_server_self_test.json passed must be a boolean")
    if not isinstance(payload.get("paths"), list):
        errors.append("app_server_self_test.json paths must be a list")
    if not isinstance(payload.get("thread_id_forwarded"), bool):
        errors.append("app_server_self_test.json thread_id_forwarded must be a boolean")
    return errors


def validate_config_schema() -> list[str]:
    path = HANDOFF_DIR / "config.json"
    if not path.exists():
        return [f"missing required file: {path}"]
    payload = load_json(path, {})
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["config.json must contain a JSON object"]
    deep_link = payload.get("deep_link")
    if not isinstance(deep_link, dict):
        errors.append("config.json deep_link must be an object")
    else:
        prompt = deep_link.get("prompt")
        if prompt is not None and not isinstance(prompt, str):
            errors.append("config.json deep_link.prompt must be a string or null")
    app_server = payload.get("app_server")
    if not isinstance(app_server, dict):
        errors.append("config.json app_server must be an object")
    else:
        server_url = app_server.get("server_url")
        if server_url is not None and not isinstance(server_url, str):
            errors.append("config.json app_server.server_url must be a string or null")
        timeout = app_server.get("timeout_seconds")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            errors.append("config.json app_server.timeout_seconds must be a positive number")
        retries = app_server.get("retries")
        if not isinstance(retries, int) or retries < 0:
            errors.append("config.json app_server.retries must be a non-negative integer")
    auto = payload.get("auto")
    if not isinstance(auto, dict):
        errors.append("config.json auto must be an object")
    else:
        max_events = auto.get("evidence_max_events")
        keep_events = auto.get("evidence_keep_events")
        context_max = auto.get("context_max_tokens")
        context_ratio = auto.get("context_warn_ratio")
        if not isinstance(max_events, int) or max_events <= 0:
            errors.append("config.json auto.evidence_max_events must be a positive integer")
        if not isinstance(keep_events, int) or keep_events <= 0:
            errors.append("config.json auto.evidence_keep_events must be a positive integer")
        if isinstance(max_events, int) and isinstance(keep_events, int) and keep_events > max_events:
            errors.append("config.json auto.evidence_keep_events must be less than or equal to evidence_max_events")
        if not isinstance(context_max, int) or context_max <= 0:
            errors.append("config.json auto.context_max_tokens must be a positive integer")
        if not isinstance(context_ratio, (int, float)) or context_ratio <= 0 or context_ratio > 1:
            errors.append("config.json auto.context_warn_ratio must be in the range (0, 1]")
    return errors


def validate_requirements_schema() -> list[str]:
    path = HANDOFF_DIR / "requirements.json"
    if not path.exists():
        return [f"missing required file: {path}"]
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        return ["requirements.json must contain a JSON object"]
    requirements = payload.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        return ["requirements.json must contain a non-empty requirements list"]
    errors: list[str] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(requirements):
        if not isinstance(item, dict):
            errors.append(f"requirements.json requirements[{idx}] must be an object")
            continue
        req_id = item.get("id")
        if not isinstance(req_id, str) or not req_id:
            errors.append(f"requirements.json requirements[{idx}] missing id")
        elif req_id in seen_ids:
            errors.append(f"requirements.json duplicate id: {req_id}")
        else:
            seen_ids.add(req_id)
        if not isinstance(item.get("name"), str):
            errors.append(f"requirements.json requirements[{idx}] missing name")
    return errors


def validate_completion_audit_schema() -> list[str]:
    path = HANDOFF_DIR / "completion_audit.json"
    if not path.exists():
        return []
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        return ["completion_audit.json must contain a JSON object"]
    errors: list[str] = []
    if payload.get("overall_status") not in ["pass", "boundary", "fail"]:
        errors.append("completion_audit.json has invalid overall_status")
    if not isinstance(payload.get("items"), list):
        errors.append("completion_audit.json must include items list")
    return errors


def validate_external_acceptance_schema() -> list[str]:
    path = HANDOFF_DIR / "external_acceptance.json"
    if not path.exists():
        return []
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        return ["external_acceptance.json must contain a JSON object"]
    validations = payload.get("validations")
    if not isinstance(validations, list):
        return ["external_acceptance.json must include validations list"]
    errors: list[str] = []
    for idx, item in enumerate(validations):
        if not isinstance(item, dict):
            errors.append(f"external_acceptance.json validations[{idx}] must be an object")
            continue
        if item.get("target") not in ["codex-app-thread-create", "deep-link-open", "new-thread-read", "app-server-real"]:
            errors.append(f"external_acceptance.json validations[{idx}] has invalid target")
        if item.get("status") not in ["pass", "fail", "unknown"]:
            errors.append(f"external_acceptance.json validations[{idx}] has invalid status")
        if not isinstance(item.get("recorded_at"), str):
            errors.append(f"external_acceptance.json validations[{idx}] missing recorded_at")
        if not isinstance(item.get("evidence", ""), str):
            errors.append(f"external_acceptance.json validations[{idx}] evidence must be a string")
    return errors


def validate_deep_link_open_attempt_schema() -> list[str]:
    path = HANDOFF_DIR / "deep_link_open_attempt.json"
    if not path.exists():
        return []
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        return ["deep_link_open_attempt.json must contain a JSON object"]
    errors: list[str] = []
    if payload.get("backend") != "deep-link":
        errors.append("deep_link_open_attempt.json backend must be deep-link")
    link = payload.get("link")
    if not isinstance(link, str) or not link.startswith("codex://threads/new?"):
        errors.append("deep_link_open_attempt.json must include a codex://threads/new link")
    for key in ["execute_requested", "opened", "dry_run"]:
        if not isinstance(payload.get(key), bool):
            errors.append(f"deep_link_open_attempt.json {key} must be a boolean")
    if payload.get("error") is not None and not isinstance(payload.get("error"), str):
        errors.append("deep_link_open_attempt.json error must be a string or null")
    return errors


def validate_handoff_acceptance_schema() -> list[str]:
    path = HANDOFF_DIR / "handoff_acceptance.json"
    if not path.exists():
        return []
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        return ["handoff_acceptance.json must contain a JSON object"]
    errors: list[str] = []
    if not isinstance(payload.get("accepted_at"), str):
        errors.append("handoff_acceptance.json missing accepted_at")
    if payload.get("workspace") != str(ROOT):
        errors.append("handoff_acceptance.json workspace mismatch")
    if payload.get("migrated") is not True:
        errors.append("handoff_acceptance.json migrated must be true")
    targets = payload.get("recorded_targets")
    if not isinstance(targets, list) or "new-thread-read" not in targets:
        errors.append("handoff_acceptance.json must include new-thread-read target")
    return errors


def validate_probe_schema() -> list[str]:
    errors: list[str] = []
    for name in ["probe_deep_link.json", "probe_app_server.json"]:
        path = HANDOFF_DIR / name
        if not path.exists():
            continue
        payload = load_json(path, {})
        if not isinstance(payload, dict):
            errors.append(f"{name} must contain a JSON object")
            continue
        if not isinstance(payload.get("target"), str):
            errors.append(f"{name} missing target")
        if not isinstance(payload.get("available"), bool):
            errors.append(f"{name} missing boolean available")
        if not isinstance(payload.get("errors", []), list):
            errors.append(f"{name} errors must be a list")
    return errors


def validate_maintenance_report_schema() -> list[str]:
    path = HANDOFF_DIR / "maintenance_report.json"
    if not path.exists():
        return []
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        return ["maintenance_report.json must contain a JSON object"]
    errors: list[str] = []
    if payload.get("mode") not in ["dry-run", "apply", "simulate-apply"]:
        errors.append("maintenance_report.json has invalid mode")
    candidates = payload.get("candidates")
    if not isinstance(candidates, dict):
        errors.append("maintenance_report.json must include candidates object")
    return errors


def command_verify(_: argparse.Namespace) -> int:
    errors, warnings = collect_verify_result()

    if errors:
        print("verify: failed")
        for item in errors:
            print(f"ERROR: {item}")
        for item in warnings:
            print(f"WARN: {item}")
        return 1

    print("verify: ok")
    for item in warnings:
        print(f"WARN: {item}")
    return 0


def command_link(args: argparse.Namespace) -> int:
    prompt_file = HANDOFF_DIR / "next_prompt.md"
    if not prompt_file.exists():
        print("next_prompt.md is missing; run checkpoint first", file=sys.stderr)
        return 1
    print(build_deep_link(args.prompt))
    return 0


def command_open_link(args: argparse.Namespace) -> int:
    ensure_scaffold()
    verify_result = command_verify(argparse.Namespace())
    if verify_result != 0:
        return verify_result
    link = build_deep_link(args.prompt)
    executed = bool(args.execute)
    opened = False
    error = None
    if executed:
        opened, error = open_deep_link(link)
    result: dict[str, object] = {
        "created_at": now_iso(),
        "workspace": str(ROOT),
        "backend": "deep-link",
        "link": link,
        "execute_requested": executed,
        "opened": opened,
        "dry_run": not executed,
        "error": error,
        "next_manual_validation": [
            "Confirm that the installed Codex App opened a new thread.",
            "Confirm that the new thread read .codex-handoff/next_prompt.md before continuing.",
            "Record those confirmations with record-external.",
        ],
    }
    if args.write:
        out_path = HANDOFF_DIR / "deep_link_open_attempt.json"
        write_text(out_path, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        append_evidence(
            "EVIDENCE",
            "open-link",
            "Recorded Deep Link open attempt.",
            {"attempt_file": str(out_path), "execute_requested": executed, "opened": opened},
        )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"link: {link}")
        print(f"execute_requested: {executed}")
        print(f"opened: {opened}")
        if error:
            print(f"error: {error}")
    return 0 if not executed or opened else 1


def command_prepare_server(_: argparse.Namespace) -> int:
    ensure_scaffold()
    request_path = HANDOFF_DIR / "app_server_request.json"
    payload = build_app_server_request()
    write_text(request_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    append_evidence("EVIDENCE", "prepare-server", "Prepared App Server request plan.", {"request_file": str(request_path)})
    print(f"app_server_request: {request_path}")
    return 0


def command_start(args: argparse.Namespace) -> int:
    ensure_scaffold()
    if args.checkpoint:
        checkpoint_result = command_checkpoint(argparse.Namespace(force=args.force))
        if checkpoint_result != 0:
            return checkpoint_result
    verify_result = command_verify(argparse.Namespace())
    if verify_result != 0:
        return verify_result
    if args.backend == "deep-link":
        print(build_deep_link(args.prompt))
        return 0
    if args.backend == "app-server-plan":
        return command_prepare_server(argparse.Namespace())
    if args.backend == "app-server-http":
        app_config = config_section("app_server")
        configured_server_url = app_config.get("server_url")
        server_url = args.server_url or (configured_server_url if isinstance(configured_server_url, str) else None)
        configured_timeout = app_config.get("timeout_seconds")
        timeout = args.timeout if args.timeout is not None else (float(configured_timeout) if isinstance(configured_timeout, (int, float)) else 10.0)
        configured_retries = app_config.get("retries")
        retries = args.retries if args.retries is not None else (int(configured_retries) if isinstance(configured_retries, int) else 0)
        if not server_url:
            print("--server-url is required for app-server-http", file=sys.stderr)
            return 1
        try:
            result = execute_app_server_plan(server_url, timeout=timeout, retries=retries)
        except RuntimeError as exc:
            print(f"app_server_http: failed: {exc}", file=sys.stderr)
            return 1
        result_path = HANDOFF_DIR / "app_server_result.json"
        write_text(result_path, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        thread_id = result.get("thread_id")
        if isinstance(thread_id, str):
            command_mark_migrated(argparse.Namespace(thread_id=thread_id))
        append_evidence("EVIDENCE", "app-server-http", "Executed App Server request plan.", {"result_file": str(result_path), "thread_id": thread_id})
        print(f"app_server_result: {result_path}")
        return 0
    print(f"unsupported backend: {args.backend}", file=sys.stderr)
    return 1


def get_status_payload() -> dict[str, object]:
    registry = load_json(HANDOFF_DIR / "thread_registry.json", {"threads": []})
    threads = registry.get("threads", []) if isinstance(registry, dict) else []
    payload: dict[str, object] = {
        "workspace": str(ROOT),
        "handoff_dir": str(HANDOFF_DIR),
        "handoff_exists": (HANDOFF_DIR / "handoff.md").exists(),
        "next_prompt_exists": (HANDOFF_DIR / "next_prompt.md").exists(),
        "registry_entries": len(threads) if isinstance(threads, list) else 0,
        "latest": None,
        "deep_link_open_attempt": None,
        "external_acceptance": {"validations": 0, "latest_by_target": {}},
    }
    if isinstance(threads, list) and threads:
        latest = threads[-1]
        if isinstance(latest, dict):
            payload["latest"] = latest
    attempt = load_json(HANDOFF_DIR / "deep_link_open_attempt.json", None)
    if isinstance(attempt, dict):
        payload["deep_link_open_attempt"] = {
            "created_at": attempt.get("created_at"),
            "execute_requested": attempt.get("execute_requested"),
            "opened": attempt.get("opened"),
            "dry_run": attempt.get("dry_run"),
            "error": attempt.get("error"),
        }
    acceptance = load_external_acceptance()
    validations = acceptance.get("validations", [])
    latest_by_target: dict[str, object] = {}
    if isinstance(validations, list):
        for item in validations:
            if isinstance(item, dict) and isinstance(item.get("target"), str):
                latest_by_target[str(item["target"])] = {
                    "status": item.get("status"),
                    "recorded_at": item.get("recorded_at"),
                    "thread_id": item.get("thread_id"),
                }
        payload["external_acceptance"] = {"validations": len(validations), "latest_by_target": latest_by_target}
    return payload


def command_status(args: argparse.Namespace) -> int:
    payload = get_status_payload()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"workspace: {payload['workspace']}")
    print(f"handoff_dir: {payload['handoff_dir']}")
    print(f"handoff_exists: {payload['handoff_exists']}")
    print(f"next_prompt_exists: {payload['next_prompt_exists']}")
    print(f"registry_entries: {payload['registry_entries']}")
    latest = payload.get("latest")
    if isinstance(latest, dict):
        print(f"latest_handoff: {latest.get('handoff_file')}")
        print(f"latest_created_at: {latest.get('handoff_created_at')}")
        print(f"latest_hash: {latest.get('handoff_hash')}")
        print(f"latest_migrated: {latest.get('migrated')}")
        print(f"latest_new_thread_id: {latest.get('new_thread_id')}")
    attempt = payload.get("deep_link_open_attempt")
    if isinstance(attempt, dict):
        print(f"deep_link_execute_requested: {attempt.get('execute_requested')}")
        print(f"deep_link_opened: {attempt.get('opened')}")
        print(f"deep_link_error: {attempt.get('error')}")
    return 0


def command_mark_migrated(args: argparse.Namespace) -> int:
    registry_path = HANDOFF_DIR / "thread_registry.json"
    registry = load_json(registry_path, {"threads": []})
    if not isinstance(registry, dict) or not isinstance(registry.get("threads"), list):
        print("thread_registry.json is invalid; run verify", file=sys.stderr)
        return 1
    threads = registry["threads"]
    if not threads:
        print("no handoff registry entry exists; run checkpoint first", file=sys.stderr)
        return 1
    latest = threads[-1]
    if not isinstance(latest, dict):
        print("latest registry entry is invalid; run verify", file=sys.stderr)
        return 1
    latest["new_thread_created"] = True
    latest["migrated"] = True
    latest["migrated_at"] = now_iso()
    if args.thread_id:
        latest["new_thread_id"] = args.thread_id
    write_text(registry_path, json.dumps(registry, indent=2, ensure_ascii=False) + "\n")
    sync_current_state_migration(args.thread_id)
    append_evidence("EVIDENCE", "mark-migrated", "Marked latest handoff as migrated.", {"new_thread_id": args.thread_id})
    print("mark_migrated: ok")
    return 0


def replace_yaml_bool(text: str, key: str, value: bool) -> str:
    rendered = "true" if value else "false"
    pattern = re.compile(rf"^(\s*{re.escape(key)}:\s*)(true|false)\s*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(rf"\g<1>{rendered}", text)
    return text


def sync_current_state_migration(thread_id: str | None) -> None:
    state_path = HANDOFF_DIR / "current_state.yaml"
    if not state_path.exists():
        return
    text = read_text(state_path)
    text = replace_yaml_bool(text, "handoff_created", True)
    text = replace_yaml_bool(text, "migrated_to_new_thread", True)
    if thread_id:
        if re.search(r"^\s*new_thread_id:\s*.*$", text, flags=re.MULTILINE):
            text = re.sub(r"^(\s*new_thread_id:\s*).*$", rf'\g<1>"{thread_id}"', text, count=1, flags=re.MULTILINE)
        else:
            text = re.sub(
                r"(^current_thread:\n(?:^[ \t]+.*\n)*)",
                rf'\g<1>  new_thread_id: "{thread_id}"' + "\n",
                text,
                count=1,
                flags=re.MULTILINE,
            )
    write_text(state_path, text)


def command_accept_handoff(args: argparse.Namespace) -> int:
    ensure_scaffold()
    errors, _warnings = collect_verify_result()
    if errors:
        print("accept_handoff: verify failed", file=sys.stderr)
        for item in errors:
            print(f"ERROR: {item}", file=sys.stderr)
        return 1
    evidence = args.evidence or "New Codex thread confirmed it read .codex-handoff/next_prompt.md and project-local handoff files."
    launch_evidence = args.launch_evidence or "Handoff was accepted from a newly created Codex App thread for this workspace."
    result = {
        "accepted_at": now_iso(),
        "workspace": str(ROOT),
        "thread_id": args.thread_id,
        "recorded_targets": [args.launch_target, "new-thread-read"],
        "migrated": not args.dry_run,
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print("accept_handoff: dry-run")
            print(f"thread_id: {args.thread_id}")
        return 0
    record_external_validation(args.launch_target, "pass", launch_evidence, args.thread_id)
    record_external_validation("new-thread-read", "pass", evidence, args.thread_id)
    migrated_result = command_mark_migrated(argparse.Namespace(thread_id=args.thread_id))
    if migrated_result != 0:
        return migrated_result
    out_path = HANDOFF_DIR / "handoff_acceptance.json"
    write_text(out_path, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    append_evidence("EVIDENCE", "accept-handoff", "Accepted handoff in target Codex thread.", {"acceptance_file": str(out_path), "thread_id": args.thread_id})
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("accept_handoff: ok")
        print(f"handoff_acceptance: {out_path}")
        print(f"thread_id: {args.thread_id}")
    return 0


def collect_verify_result() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for path in REQUIRED_FILES:
        if not path.exists():
            errors.append(f"missing required file: {path}")
        elif path.is_file() and path.stat().st_size == 0:
            warnings.append(f"empty file: {path}")

    errors.extend(validate_state_schema())
    errors.extend(validate_decisions_schema())
    errors.extend(validate_risk_schema())
    errors.extend(validate_registry_schema())
    errors.extend(validate_config_schema())
    errors.extend(validate_requirements_schema())
    errors.extend(validate_app_server_plan())
    errors.extend(validate_app_server_result())
    errors.extend(validate_app_server_self_test())
    errors.extend(validate_completion_audit_schema())
    errors.extend(validate_external_acceptance_schema())
    errors.extend(validate_deep_link_open_attempt_schema())
    errors.extend(validate_handoff_acceptance_schema())
    errors.extend(validate_probe_schema())
    errors.extend(validate_maintenance_report_schema())
    errors.extend(validate_jsonl(HANDOFF_DIR / "evidence.jsonl"))

    handoff = read_text(HANDOFF_DIR / "handoff.md") if (HANDOFF_DIR / "handoff.md").exists() else ""
    for tag in ["[FACT]", "[DECISION]", "[TODO]", "[OPEN]", "[REJECTED]", "[EVIDENCE]"]:
        if tag not in handoff:
            errors.append(f"handoff is missing tag: {tag}")

    next_prompt = read_text(HANDOFF_DIR / "next_prompt.md") if (HANDOFF_DIR / "next_prompt.md").exists() else ""
    if len(next_prompt) > 4000:
        warnings.append(f"next_prompt is long: {len(next_prompt)} characters")
    if ".codex-handoff/handoff.md" not in next_prompt:
        errors.append("next_prompt does not require reading handoff.md")
    if ".codex-handoff/evidence_summary.md" not in next_prompt:
        errors.append("next_prompt does not require reading evidence_summary.md")

    for pattern in load_risk_patterns():
        regex = re.escape(pattern).replace(r"\*", ".*")
        for target in ["handoff.md", "next_prompt.md", "current_state.yaml", "evidence.jsonl"]:
            path = HANDOFF_DIR / target
            if path.exists() and re.search(regex, read_text(path), re.IGNORECASE):
                errors.append(f"secret-like pattern found in {target}: {pattern}")
    return errors, warnings


def command_doctor(args: argparse.Namespace) -> int:
    status = get_status_payload()
    errors, warnings = collect_verify_result()
    latest = status.get("latest")
    ready_for_handoff = not errors and bool(status.get("handoff_exists")) and bool(status.get("next_prompt_exists"))
    payload: dict[str, object] = {
        "ready_for_handoff": ready_for_handoff,
        "workspace": status["workspace"],
        "handoff_exists": status["handoff_exists"],
        "next_prompt_exists": status["next_prompt_exists"],
        "registry_entries": status["registry_entries"],
        "latest_migrated": latest.get("migrated") if isinstance(latest, dict) else None,
        "errors": errors,
        "warnings": warnings,
        "next_commands": [
            "scripts/codex_handoff.py auto --link",
            "scripts/codex_handoff.py mark-migrated --thread-id <new-thread-id>",
        ],
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"ready_for_handoff: {ready_for_handoff}")
        print(f"workspace: {payload['workspace']}")
        print(f"registry_entries: {payload['registry_entries']}")
        print(f"latest_migrated: {payload['latest_migrated']}")
        for item in errors:
            print(f"ERROR: {item}")
        for item in warnings:
            print(f"WARN: {item}")
        print("next_commands:")
        for item in payload["next_commands"]:
            print(f"- {item}")
    return 0 if ready_for_handoff else 1


def available_cli_commands() -> set[str]:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def evaluate_requirement(req: dict[str, object]) -> dict[str, object]:
    evidence: list[str] = []
    failures: list[str] = []
    req_id = req.get("id")
    name = req.get("name")

    required_cli_commands = req.get("required_cli_commands", [])
    if isinstance(required_cli_commands, list):
        commands = available_cli_commands()
        for command in required_cli_commands:
            if not isinstance(command, str):
                continue
            if command in commands:
                evidence.append(f"cli command exists: {command}")
            else:
                failures.append(f"missing cli command: {command}")

    required_command = req.get("required_command")
    if isinstance(required_command, list) and all(isinstance(item, str) for item in required_command):
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main([str(item) for item in required_command])
        except SystemExit as exc:
            result = int(exc.code) if isinstance(exc.code, int) else 1
        if result == 0:
            evidence.append(f"command passed: {' '.join(required_command)}")
        else:
            detail = stderr.getvalue().strip() or stdout.getvalue().strip()
            failures.append(f"command failed: {' '.join(required_command)} {detail}".strip())

    required_paths = req.get("required_paths", [])
    if isinstance(required_paths, list):
        for raw_path in required_paths:
            if not isinstance(raw_path, str):
                continue
            path = ROOT / raw_path
            if path.exists():
                evidence.append(f"path exists: {raw_path}")
            else:
                failures.append(f"missing path: {raw_path}")

    required_tags = req.get("required_handoff_tags", [])
    if isinstance(required_tags, list):
        handoff = read_text(HANDOFF_DIR / "handoff.md") if (HANDOFF_DIR / "handoff.md").exists() else ""
        for tag in required_tags:
            if not isinstance(tag, str):
                continue
            if tag in handoff:
                evidence.append(f"handoff tag exists: {tag}")
            else:
                failures.append(f"handoff tag missing: {tag}")

    boundary = req.get("boundary")
    has_external_targets = False
    unresolved_external = False
    external_targets = req.get("external_validation_targets", [])
    if isinstance(external_targets, list):
        for target in external_targets:
            if not isinstance(target, str):
                continue
            has_external_targets = True
            validation = latest_external_validation(target)
            if validation is None:
                evidence.append(f"external validation not recorded: {target}")
                unresolved_external = True
                if not boundary:
                    boundary = "Requires external Codex App acceptance evidence."
                continue
            status = validation.get("status")
            evidence_text = validation.get("evidence", "")
            if status == "pass":
                detail = f"external validation passed: {target}"
                if isinstance(evidence_text, str) and evidence_text:
                    detail += f" ({evidence_text})"
                evidence.append(detail)
            elif status == "fail":
                failures.append(f"external validation failed: {target} {evidence_text}".strip())
            else:
                evidence.append(f"external validation unknown: {target}")
                unresolved_external = True
                if not boundary:
                    boundary = "Requires external Codex App acceptance evidence."
    status = "pass"
    if failures:
        status = "fail"
    elif isinstance(boundary, str) and (not has_external_targets or unresolved_external):
        status = "boundary"
        evidence.append(boundary)

    return {
        "id": req_id,
        "name": name,
        "status": status,
        "evidence": evidence,
        "failures": failures,
    }


def command_completion_audit(args: argparse.Namespace) -> int:
    path = HANDOFF_DIR / "requirements.json"
    payload = load_json(path, {})
    requirements = payload.get("requirements") if isinstance(payload, dict) else None
    if not isinstance(requirements, list):
        print("requirements.json must contain a requirements list", file=sys.stderr)
        return 1
    items = [evaluate_requirement(req) for req in requirements if isinstance(req, dict)]
    failures = [item for item in items if item["status"] == "fail"]
    boundaries = [item for item in items if item["status"] == "boundary"]
    audit = {
        "created_at": now_iso(),
        "workspace": str(ROOT),
        "overall_status": "fail" if failures else ("boundary" if boundaries else "pass"),
        "pass_count": len([item for item in items if item["status"] == "pass"]),
        "boundary_count": len(boundaries),
        "fail_count": len(failures),
        "items": items,
    }
    if args.write:
        out_path = HANDOFF_DIR / "completion_audit.json"
        write_text(out_path, json.dumps(audit, indent=2, ensure_ascii=False) + "\n")
        append_evidence("EVIDENCE", "completion-audit", "Wrote completion audit.", {"audit_file": str(out_path)})
    if args.json:
        print(json.dumps(audit, indent=2, ensure_ascii=False))
    else:
        print(f"overall_status: {audit['overall_status']}")
        print(f"pass_count: {audit['pass_count']}")
        print(f"boundary_count: {audit['boundary_count']}")
        print(f"fail_count: {audit['fail_count']}")
        for item in items:
            print(f"{item['id']} {item['status']}: {item['name']}")
    return 1 if failures else 0


def probe_deep_link_protocol() -> dict[str, object]:
    payload: dict[str, object] = {
        "target": "deep-link",
        "platform": sys.platform,
        "available": False,
        "evidence": [],
        "errors": [],
    }
    if not sys.platform.startswith("win"):
        payload["errors"].append("protocol registry probe is implemented for Windows only")
        return payload
    try:
        import winreg
    except ImportError as exc:
        payload["errors"].append(f"winreg unavailable: {exc}")
        return payload
    roots = [
        ("HKEY_CURRENT_USER", winreg.HKEY_CURRENT_USER, r"Software\Classes\codex"),
        ("HKEY_CLASSES_ROOT", winreg.HKEY_CLASSES_ROOT, "codex"),
    ]
    for root_name, root, key_path in roots:
        try:
            with winreg.OpenKey(root, key_path) as key:
                try:
                    protocol_marker, _ = winreg.QueryValueEx(key, "URL Protocol")
                except OSError:
                    protocol_marker = None
            command = None
            try:
                with winreg.OpenKey(root, key_path + r"\shell\open\command") as command_key:
                    command, _ = winreg.QueryValueEx(command_key, None)
            except OSError:
                command = None
            payload["evidence"].append({"root": root_name, "key": key_path, "url_protocol": protocol_marker is not None, "command": command})
            if command:
                payload["available"] = True
        except OSError as exc:
            payload["errors"].append(f"{root_name}\\{key_path}: {exc}")
    return payload


def probe_app_server_tcp(server_url: str | None, timeout: float = 3.0) -> dict[str, object]:
    payload: dict[str, object] = {
        "target": "app-server",
        "server_url": server_url,
        "available": False,
        "evidence": [],
        "errors": [],
    }
    if not server_url:
        payload["errors"].append("server_url is not configured or supplied")
        return payload
    parsed = urlparse(server_url)
    host = parsed.hostname
    port = parsed.port
    if not host:
        payload["errors"].append("server_url has no hostname")
        return payload
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    try:
        with socket.create_connection((host, port), timeout=timeout):
            payload["available"] = True
            payload["evidence"].append({"host": host, "port": port, "tcp_connect": True})
    except OSError as exc:
        payload["errors"].append(f"tcp connect failed for {host}:{port}: {exc}")
    return payload


def command_probe(args: argparse.Namespace) -> int:
    if args.target == "deep-link":
        result = probe_deep_link_protocol()
    else:
        app_config = config_section("app_server")
        configured_server_url = app_config.get("server_url")
        server_url = args.server_url or (configured_server_url if isinstance(configured_server_url, str) else None)
        result = probe_app_server_tcp(server_url, timeout=args.timeout)
    if args.write:
        out_path = HANDOFF_DIR / f"probe_{args.target.replace('-', '_')}.json"
        write_text(out_path, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        append_evidence("EVIDENCE", "probe", f"Ran {args.target} probe.", {"probe_file": str(out_path), "available": result.get("available")})
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"target: {result['target']}")
        print(f"available: {result['available']}")
        for item in result.get("errors", []):
            print(f"ERROR: {item}")
    if args.write:
        return 0
    return 0 if result.get("available") else 1


def maintenance_candidates(keep_archives: int, keep_test_workspaces: int) -> dict[str, list[str]]:
    candidates: dict[str, list[str]] = {"archives": [], "test_workspaces": []}
    if ARCHIVE_DIR.exists():
        archives = sorted(
            [path for path in ARCHIVE_DIR.glob("*_handoff.md") if path.is_file()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        candidates["archives"] = [str(path) for path in archives[max(0, keep_archives):]]
    tmp_root = ROOT / ".tmp-tests"
    if tmp_root.exists():
        test_dirs = sorted(
            [path for path in tmp_root.iterdir() if path.is_dir() and path.name.startswith(TEST_WORKSPACE_PREFIX)],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        candidates["test_workspaces"] = [str(path) for path in test_dirs[max(0, keep_test_workspaces):]]
    return candidates


def remove_path_best_effort(path: Path) -> tuple[bool, str | None]:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        return True, None
    except OSError as exc:
        return False, str(exc)


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_maintenance_delete_path(kind: str, path: Path) -> tuple[bool, str | None]:
    resolved = path.resolve()
    if kind == "archives":
        if not is_relative_to(resolved, ARCHIVE_DIR):
            return False, "archive candidate is outside archive directory"
        if not resolved.name.endswith("_handoff.md"):
            return False, "archive candidate does not match handoff archive suffix"
        return True, None
    if kind == "test_workspaces":
        tmp_root = ROOT / ".tmp-tests"
        if not is_relative_to(resolved, tmp_root):
            return False, "test workspace candidate is outside .tmp-tests"
        if not resolved.name.startswith(TEST_WORKSPACE_PREFIX):
            return False, "test workspace candidate does not match test prefix"
        return True, None
    return False, f"unknown maintenance candidate group: {kind}"


def command_maintenance(args: argparse.Namespace) -> int:
    candidates = maintenance_candidates(args.keep_archives, args.keep_test_workspaces)
    result: dict[str, object] = {
        "mode": "simulate-apply" if args.simulate_apply else ("apply" if args.apply else "dry-run"),
        "keep_archives": args.keep_archives,
        "keep_test_workspaces": args.keep_test_workspaces,
        "candidates": candidates,
        "removed": [],
        "errors": [],
    }
    if args.apply or args.simulate_apply:
        removed: list[str] = []
        errors: list[dict[str, str]] = []
        for kind, group in candidates.items():
            for raw_path in group:
                path = Path(raw_path)
                safe, reason = validate_maintenance_delete_path(kind, path)
                if not safe:
                    errors.append({"path": raw_path, "error": reason or "unsafe path"})
                    continue
                if args.simulate_apply:
                    ok, error = True, None
                else:
                    ok, error = remove_path_best_effort(path)
                if ok:
                    removed.append(raw_path)
                else:
                    errors.append({"path": raw_path, "error": error or "unknown error"})
        result["removed"] = removed
        result["errors"] = errors
        append_evidence("EVIDENCE", "maintenance", "Ran maintenance cleanup.", {"removed_count": len(removed), "error_count": len(errors)})
    if args.write:
        out_path = HANDOFF_DIR / "maintenance_report.json"
        write_text(out_path, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"mode: {result['mode']}")
        print(f"archive_candidates: {len(candidates['archives'])}")
        print(f"test_workspace_candidates: {len(candidates['test_workspaces'])}")
        if args.apply:
            print(f"removed: {len(result['removed'])}")
            print(f"errors: {len(result['errors'])}")
    return 0 if not result["errors"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-handoff")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init").set_defaults(func=command_init)
    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("--force", action="store_true", help="Create a new archive even if the handoff hash is still in cooldown.")
    checkpoint.set_defaults(func=command_checkpoint)
    auto = sub.add_parser("auto")
    auto.add_argument("--reason", help="Reason to force an automatic checkpoint decision.")
    auto.add_argument("--force", action="store_true", help="Bypass registry cooldown when auto creates a checkpoint.")
    auto.add_argument("--link", action="store_true", help="Print a codex:// link after successful verification.")
    auto.add_argument("--compact", action="store_true", help="Force evidence log compaction before deciding whether to checkpoint.")
    auto.set_defaults(func=command_auto)
    compact = sub.add_parser("compact-evidence")
    compact.add_argument("--max-events", type=int, default=None)
    compact.add_argument("--keep-events", type=int, default=None)
    compact.add_argument("--force", action="store_true", help="Compact even when the evidence log is below threshold.")
    compact.add_argument("--dry-run", action="store_true", help="Evaluate compaction without writing summary, archive, or evidence log.")
    compact.add_argument("--json", action="store_true")
    compact.set_defaults(func=command_compact_evidence)
    context_status = sub.add_parser("context-status")
    context_status.add_argument("--json", action="store_true")
    context_status.add_argument("--write", action="store_true", help="Write .codex-handoff/context_status.json.")
    context_status.set_defaults(func=command_context_status)
    self_test = sub.add_parser("self-test")
    self_test.add_argument("target", choices=["app-server-http"])
    self_test.add_argument("--timeout", type=float, default=10.0)
    self_test.add_argument("--json", action="store_true")
    self_test.add_argument("--write", action="store_true", help="Write .codex-handoff/app_server_self_test.json.")
    self_test.set_defaults(func=command_self_test)
    sub.add_parser("verify").set_defaults(func=command_verify)
    link = sub.add_parser("link")
    link.add_argument("--prompt", help="Short prompt to place in the codex:// link.")
    link.set_defaults(func=command_link)
    open_link = sub.add_parser("open-link")
    open_link.add_argument("--prompt", help="Short prompt to place in the codex:// link.")
    open_link.add_argument("--execute", action="store_true", help="Actually ask the operating system to open the codex:// link.")
    open_link.add_argument("--json", action="store_true")
    open_link.add_argument("--write", action="store_true", help="Write .codex-handoff/deep_link_open_attempt.json.")
    open_link.set_defaults(func=command_open_link)
    sub.add_parser("prepare-server").set_defaults(func=command_prepare_server)
    start = sub.add_parser("start")
    start.add_argument("--backend", choices=["deep-link", "app-server-plan", "app-server-http"], default="deep-link")
    start.add_argument("--checkpoint", action="store_true", help="Create or refresh checkpoint before preparing the backend.")
    start.add_argument("--force", action="store_true", help="Bypass checkpoint cooldown when used with --checkpoint.")
    start.add_argument("--prompt", help="Short prompt for the deep-link backend.")
    start.add_argument("--server-url", help="Base URL for the optional App Server HTTP backend.")
    start.add_argument("--timeout", type=float, default=None, help="HTTP timeout in seconds for App Server calls.")
    start.add_argument("--retries", type=int, default=None, help="Retry count for App Server HTTP calls.")
    start.set_defaults(func=command_start)
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON status.")
    status.set_defaults(func=command_status)
    migrated = sub.add_parser("mark-migrated")
    migrated.add_argument("--thread-id", help="Optional Codex target thread id to store in the registry.")
    migrated.set_defaults(func=command_mark_migrated)
    accept = sub.add_parser("accept-handoff")
    accept.add_argument("--thread-id", help="Optional target Codex thread id.")
    accept.add_argument("--evidence", help="Evidence text for new-thread-read acceptance.")
    accept.add_argument("--launch-target", choices=["codex-app-thread-create", "deep-link-open"], default="codex-app-thread-create", help="External launch route to mark as accepted.")
    accept.add_argument("--launch-evidence", help="Evidence text for the accepted launch route.")
    accept.add_argument("--dry-run", action="store_true", help="Validate and print the acceptance plan without writing external acceptance or migration state.")
    accept.add_argument("--json", action="store_true")
    accept.set_defaults(func=command_accept_handoff)
    external = sub.add_parser("record-external")
    external.add_argument("--target", choices=["codex-app-thread-create", "deep-link-open", "new-thread-read", "app-server-real"], required=True)
    external.add_argument("--status", choices=["pass", "fail", "unknown"], required=True)
    external.add_argument("--evidence", help="Short human-readable evidence from the real Codex App validation.")
    external.add_argument("--thread-id", help="Optional real Codex thread id associated with the validation.")
    external.add_argument("--json", action="store_true")
    external.set_defaults(func=command_record_external)
    external_next = sub.add_parser("external-next")
    external_next.add_argument("--json", action="store_true", help="Print the remaining real Codex App validation plan as JSON.")
    external_next.set_defaults(func=command_external_next)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable health report.")
    doctor.set_defaults(func=command_doctor)
    audit = sub.add_parser("completion-audit")
    audit.add_argument("--json", action="store_true", help="Print machine-readable completion audit.")
    audit.add_argument("--write", action="store_true", help="Write .codex-handoff/completion_audit.json.")
    audit.set_defaults(func=command_completion_audit)
    probe = sub.add_parser("probe")
    probe.add_argument("target", choices=["deep-link", "app-server"])
    probe.add_argument("--server-url", help="Base URL for app-server TCP probing.")
    probe.add_argument("--timeout", type=float, default=3.0)
    probe.add_argument("--json", action="store_true")
    probe.add_argument("--write", action="store_true")
    probe.set_defaults(func=command_probe)
    maintenance = sub.add_parser("maintenance")
    maintenance.add_argument("--keep-archives", type=int, default=20)
    maintenance.add_argument("--keep-test-workspaces", type=int, default=5)
    maintenance.add_argument("--apply", action="store_true", help="Actually remove candidates. Without this flag, only reports.")
    maintenance.add_argument("--simulate-apply", action="store_true", help="Run apply safety checks without deleting candidates.")
    maintenance.add_argument("--json", action="store_true")
    maintenance.add_argument("--write", action="store_true", help="Write .codex-handoff/maintenance_report.json.")
    maintenance.set_defaults(func=command_maintenance)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
