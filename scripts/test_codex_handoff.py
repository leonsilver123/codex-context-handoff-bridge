#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "codex_handoff.py"


def safe_print(value: str) -> None:
    sys.stdout.buffer.write(value.encode("utf-8", errors="replace"))
    if not value.endswith("\n"):
        sys.stdout.buffer.write(b"\n")


def run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def expect_success(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    proc = run(cwd, *args)
    if proc.returncode != 0:
        safe_print(proc.stdout)
        safe_print(proc.stderr)
        raise AssertionError(f"failed command: {' '.join(args)}")
    return proc


def expect_failure(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    proc = run(cwd, *args)
    if proc.returncode == 0:
        safe_print(proc.stdout)
        safe_print(proc.stderr)
        raise AssertionError(f"command should have failed: {' '.join(args)}")
    return proc


def copy_seed_files(dst: Path) -> None:
    for rel in [
        "AGENTS.md",
        ".codex-handoff/current_state.yaml",
        ".codex-handoff/decisions.yaml",
        ".codex-handoff/risk_rules.yaml",
        ".codex-handoff/config.json",
        ".codex-handoff/requirements.json",
        ".codex-handoff/app_server_request.json",
        ".codex-handoff/probe_deep_link.json",
        ".codex-handoff/evidence_summary.md",
        ".codex-handoff/external_acceptance.json",
        ".codex-handoff/evidence.jsonl",
        ".codex-handoff/thread_registry.json",
        ".codex-handoff/handoff.md",
        ".codex-handoff/next_prompt.md",
        "scripts/handoff-smoke.ps1",
    ]:
        src = REPO / rel
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    state_path = dst / ".codex-handoff/current_state.yaml"
    state_text = state_path.read_text(encoding="utf-8")
    escaped_workspace = str(dst).replace("\\", "\\\\")
    state_text = re.sub(
        r'workspace_path:\s*".*?"',
        f'workspace_path: "{escaped_workspace}"',
        state_text,
    )
    state_path.write_text(state_text, encoding="utf-8", newline="\n")
    app_plan_path = dst / ".codex-handoff/app_server_request.json"
    app_plan = json.loads(app_plan_path.read_text(encoding="utf-8"))
    app_plan["workspace_path"] = str(dst)
    app_plan_path.write_text(json.dumps(app_plan, ensure_ascii=False), encoding="utf-8", newline="\n")
    external_path = dst / ".codex-handoff/external_acceptance.json"
    external_path.write_text(
        json.dumps({"version": 1, "validations": []}, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )


class FakeAppServerHandler(BaseHTTPRequestHandler):
    calls: list[dict[str, object]] = []
    fail_first_thread_start: bool = False

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        payload = json.loads(body) if body else {}
        self.__class__.calls.append({"path": self.path, "body": payload})
        if self.path == "/thread/start" and self.__class__.fail_first_thread_start and len(self.__class__.calls) == 1:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"temporary failure")
            return
        if self.path == "/thread/start":
            response = {"thread_id": "fake-thread-id"}
        elif self.path == "/turn/start":
            response = {"turn_id": "fake-turn-id", "thread_id": payload.get("thread_id")}
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: object) -> None:
        return


def run_fake_app_server() -> tuple[ThreadingHTTPServer, str]:
    FakeAppServerHandler.calls = []
    FakeAppServerHandler.fail_first_thread_start = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAppServerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def main() -> int:
    tmp_root = REPO / ".tmp-tests"
    tmp_root.mkdir(exist_ok=True)

    empty_cwd = tmp_root / f"codex_handoff_empty_install_{int(time.time() * 1000)}"
    empty_cwd.mkdir()
    try:
        expect_success(empty_cwd, "init")
        expect_success(empty_cwd, "verify")
        empty_audit = json.loads(expect_success(empty_cwd, "completion-audit", "--json").stdout)
        if empty_audit.get("overall_status") != "pass":
            print("empty project init did not produce a passing default completion audit")
            return 1
        zh_verify = expect_success(empty_cwd, "verify", "--lang", "zh-CN").stdout
        if "\u9a8c\u8bc1\uff1a\u901a\u8fc7" not in zh_verify:
            print("verify --lang zh-CN did not print localized output")
            return 1
        zh_doctor = expect_success(empty_cwd, "doctor", "--lang", "zh-CN").stdout
        if "\u53ef\u4ea4\u63a5:" not in zh_doctor:
            print("doctor --lang zh-CN did not print localized output")
            return 1
        zh_audit = expect_success(empty_cwd, "completion-audit", "--lang", "zh-CN").stdout
        if "\u603b\u4f53\u72b6\u6001:" not in zh_audit:
            print("completion-audit --lang zh-CN did not print localized output")
            return 1
    except AssertionError as exc:
        print(str(exc))
        return 1
    cwd = tmp_root / f"codex_handoff_test_workspace_中文_{int(time.time() * 1000)}"
    cwd.mkdir()
    copy_seed_files(cwd)
    config_path = cwd / ".codex-handoff/config.json"
    original_config = config_path.read_text(encoding="utf-8")

    commands = [
        ("init",),
        ("checkpoint",),
        ("auto", "--link"),
        ("verify",),
        ("doctor",),
        ("doctor", "--json"),
        ("status",),
        ("status", "--json"),
        ("link",),
        ("start", "--backend", "deep-link"),
    ]
    for command in commands:
        try:
            expect_success(cwd, *command)
        except AssertionError as exc:
            print(str(exc))
            return 1

    link = run(cwd, "link").stdout.strip()
    if not link.startswith("codex://threads/new?"):
        print("deep link prefix missing")
        return 1
    if "中文" in link:
        print("deep link path was not URL encoded")
        return 1

    before = json.loads((cwd / ".codex-handoff/thread_registry.json").read_text(encoding="utf-8"))
    try:
        expect_success(cwd, "checkpoint")
    except AssertionError as exc:
        print(str(exc))
        return 1
    after = json.loads((cwd / ".codex-handoff/thread_registry.json").read_text(encoding="utf-8"))
    if len(after["threads"]) != len(before["threads"]):
        print("duplicate checkpoint was not suppressed")
        return 1

    try:
        expect_success(cwd, "open-link", "--json", "--write")
    except AssertionError as exc:
        print(str(exc))
        return 1
    open_attempt = json.loads((cwd / ".codex-handoff/deep_link_open_attempt.json").read_text(encoding="utf-8"))
    if open_attempt.get("dry_run") is not True or open_attempt.get("opened") is not False:
        print("open-link did not default to dry-run")
        return 1
    if not str(open_attempt.get("link", "")).startswith("codex://threads/new?"):
        print("open-link did not record a Codex deep link")
        return 1
    try:
        expect_success(cwd, "self-test", "app-server-http", "--json", "--write")
    except AssertionError as exc:
        print(str(exc))
        return 1
    self_test = json.loads((cwd / ".codex-handoff/app_server_self_test.json").read_text(encoding="utf-8"))
    if self_test.get("passed") is not True or self_test.get("paths") != ["/thread/start", "/turn/start"]:
        print("app-server-http self-test did not pass expected endpoint sequence")
        return 1

    try:
        expect_success(cwd, "maintenance", "--json", "--write")
    except AssertionError as exc:
        print(str(exc))
        return 1

    evidence_path = cwd / ".codex-handoff/evidence.jsonl"
    before_compact = evidence_path.read_text(encoding="utf-8")
    dry_run = json.loads(expect_success(cwd, "compact-evidence", "--max-events", "3", "--keep-events", "2", "--force", "--dry-run", "--json").stdout)
    if dry_run.get("would_compact") is not True or dry_run.get("compacted") is not False:
        print("compact-evidence dry-run did not report expected decision")
        return 1
    if evidence_path.read_text(encoding="utf-8") != before_compact:
        print("compact-evidence dry-run modified evidence log")
        return 1
    try:
        expect_success(cwd, "compact-evidence", "--max-events", "3", "--keep-events", "2", "--force", "--json")
    except AssertionError as exc:
        print(str(exc))
        return 1
    compacted_lines = [line for line in evidence_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(compacted_lines) > 4:
        print("compact-evidence did not reduce evidence log to recent window")
        return 1
    summary_text = (cwd / ".codex-handoff/evidence_summary.md").read_text(encoding="utf-8")
    if "archived_full_log" not in summary_text or "original_event_count" not in summary_text:
        print("compact-evidence did not write expected evidence summary")
        return 1
    context_status = json.loads(expect_success(cwd, "context-status", "--json").stdout)
    if context_status.get("estimated_tokens", 0) <= 0 or not isinstance(context_status.get("files"), list):
        print("context-status did not report estimated token pressure")
        return 1
    config_payload = json.loads(config_path.read_text(encoding="utf-8"))
    config_payload["auto"]["context_max_tokens"] = 10
    config_payload["auto"]["context_warn_ratio"] = 0.5
    config_path.write_text(json.dumps(config_payload, ensure_ascii=False), encoding="utf-8", newline="\n")
    try:
        expect_success(cwd, "auto", "--compact")
    except AssertionError as exc:
        print(str(exc))
        return 1
    config_path.write_text(original_config, encoding="utf-8", newline="\n")

    archive_dir = cwd / ".codex-handoff/archive"
    for idx in range(3):
        path = archive_dir / f"2000-01-0{idx}_000000_handoff.md"
        path.write_text(f"old archive {idx}\n", encoding="utf-8", newline="\n")
        os.utime(path, (946684800 + idx, 946684800 + idx))
    nested_tmp = cwd / ".tmp-tests"
    nested_tmp.mkdir(exist_ok=True)
    keep_dir = nested_tmp / "manual"
    keep_dir.mkdir(exist_ok=True)
    for idx in range(3):
        test_dir = nested_tmp / f"codex_handoff_test_workspace_apply_{idx}"
        test_dir.mkdir(exist_ok=True)
        (test_dir / "marker.txt").write_text("x", encoding="utf-8")
        old_time = 946684800 + idx
        os.utime(test_dir / "marker.txt", (old_time, old_time))
        os.utime(test_dir, (old_time, old_time))
    try:
        expect_success(cwd, "maintenance", "--keep-archives", "1", "--keep-test-workspaces", "1", "--simulate-apply", "--json", "--write")
    except AssertionError as exc:
        print(str(exc))
        return 1
    report = json.loads((cwd / ".codex-handoff/maintenance_report.json").read_text(encoding="utf-8"))
    if report.get("mode") != "simulate-apply":
        print("maintenance simulate apply did not record simulate mode")
        return 1
    removed = report.get("removed", [])
    if not isinstance(removed, list) or len(removed) < 5:
        print("maintenance simulate apply did not cover expected minimum candidate count")
        return 1
    for idx in range(3):
        if not any(f"2000-01-0{idx}_000000_handoff.md" in item for item in removed if isinstance(item, str)):
            print("maintenance simulate apply missed a seeded old archive")
            return 1
    removed_seeded_workspaces = [
        item for item in removed
        if isinstance(item, str) and "codex_handoff_test_workspace_apply_" in item
    ]
    if len(removed_seeded_workspaces) < 2:
        print("maintenance simulate apply missed seeded test workspace candidates")
        return 1
    if any("manual" in item for item in removed if isinstance(item, str)):
        print("maintenance simulate apply included non-matching .tmp-tests directory")
        return 1
    if not keep_dir.exists():
        print("maintenance simulate apply affected non-matching .tmp-tests directory")
        return 1

    try:
        expect_success(cwd, "completion-audit", "--json", "--write")
    except AssertionError as exc:
        print(str(exc))
        return 1

    audit_payload = json.loads((cwd / ".codex-handoff/completion_audit.json").read_text(encoding="utf-8"))
    if audit_payload.get("overall_status") == "fail":
        print("completion audit failed")
        return 1
    if audit_payload.get("boundary_count", 0) < 1:
        print("completion audit did not report known boundaries")
        return 1

    external_next = json.loads(expect_success(cwd, "external-next", "--json").stdout)
    if external_next.get("complete") is not False:
        print("external-next should report incomplete before real acceptance records")
        return 1
    unresolved = external_next.get("missing_or_unresolved", [])
    if "codex-app-thread-create" not in unresolved or "new-thread-read" not in unresolved:
        print("external-next did not report unresolved real acceptance targets")
        return 1
    if not str(external_next.get("deep_link", "")).startswith("codex://threads/new?"):
        print("external-next did not include a Codex deep link")
        return 1
    if "Create a new Codex App thread for this same workspace." not in external_next.get("steps", []):
        print("external-next did not include the App thread creation validation step")
        return 1
    if "scripts/codex_handoff.py open-link --execute --json --write" not in external_next.get("deep_link_probe_steps", []):
        print("external-next did not include the optional executable deep-link probe step")
        return 1
    if not isinstance(external_next.get("latest_open_attempt"), dict):
        print("external-next did not include the latest open-link attempt")
        return 1

    try:
        expect_success(
            cwd,
            "record-external",
            "--target",
            "codex-app-thread-create",
            "--status",
            "pass",
            "--evidence",
            "manual App thread created for handoff",
            "--thread-id",
            "external-thread-id",
        )
        expect_success(
            cwd,
            "record-external",
            "--target",
            "new-thread-read",
            "--status",
            "pass",
            "--evidence",
            "new thread confirmed reading next_prompt.md",
        )
        expect_success(cwd, "verify")
    except AssertionError as exc:
        print(str(exc))
        return 1
    external_payload = json.loads((cwd / ".codex-handoff/external_acceptance.json").read_text(encoding="utf-8"))
    validations = external_payload.get("validations", [])
    if not isinstance(validations, list) or len(validations) < 2:
        print("record-external did not persist validation entries")
        return 1
    latest_by_target = {}
    for item in validations:
        if isinstance(item, dict):
            latest_by_target[item.get("target")] = item.get("status")
    if latest_by_target.get("codex-app-thread-create") != "pass" or latest_by_target.get("new-thread-read") != "pass":
        print("record-external did not persist latest validation status")
        return 1

    concurrent_a = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT),
            "record-external",
            "--target",
            "codex-app-thread-create",
            "--status",
            "unknown",
            "--evidence",
            "concurrent app-thread-create validation",
        ],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    concurrent_b = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT),
            "record-external",
            "--target",
            "new-thread-read",
            "--status",
            "unknown",
            "--evidence",
            "concurrent new-thread-read validation",
        ],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out_a, err_a = concurrent_a.communicate(timeout=20)
    out_b, err_b = concurrent_b.communicate(timeout=20)
    if concurrent_a.returncode != 0 or concurrent_b.returncode != 0:
        safe_print(out_a + err_a + out_b + err_b)
        print("concurrent record-external command failed")
        return 1
    concurrent_payload = json.loads((cwd / ".codex-handoff/external_acceptance.json").read_text(encoding="utf-8"))
    concurrent_evidence = [
        item.get("evidence")
        for item in concurrent_payload.get("validations", [])
        if isinstance(item, dict)
    ]
    if "concurrent app-thread-create validation" not in concurrent_evidence or "concurrent new-thread-read validation" not in concurrent_evidence:
        print("concurrent record-external lost a validation entry")
        return 1

    audit_after_external = json.loads(expect_success(cwd, "completion-audit", "--json").stdout)
    external_item = next((item for item in audit_after_external.get("items", []) if item.get("id") == "R012"), None)
    if not external_item or external_item.get("status") != "boundary":
        print("completion audit did not reflect latest unresolved concurrent acceptance records")
        return 1
    external_next_after = json.loads(expect_success(cwd, "external-next", "--json").stdout)
    if external_next_after.get("complete") is not False or "codex-app-thread-create" not in external_next_after.get("missing_or_unresolved", []):
        print("external-next did not report unresolved latest concurrent acceptance records")
        return 1

    try:
        expect_success(cwd, "accept-handoff", "--thread-id", "accepted-thread-id", "--json")
        expect_success(cwd, "verify")
    except AssertionError as exc:
        print(str(exc))
        return 1
    acceptance_record = json.loads((cwd / ".codex-handoff/handoff_acceptance.json").read_text(encoding="utf-8"))
    if acceptance_record.get("migrated") is not True or acceptance_record.get("thread_id") != "accepted-thread-id":
        print("accept-handoff did not persist acceptance record")
        return 1
    registry_after_accept = json.loads((cwd / ".codex-handoff/thread_registry.json").read_text(encoding="utf-8"))
    if registry_after_accept["threads"][-1].get("new_thread_id") != "accepted-thread-id":
        print("accept-handoff did not mark latest registry entry as migrated")
        return 1
    current_state_after_accept = (cwd / ".codex-handoff/current_state.yaml").read_text(encoding="utf-8")
    if "handoff_created: true" not in current_state_after_accept or "migrated_to_new_thread: true" not in current_state_after_accept:
        print("accept-handoff did not synchronize current_state migration flags")
        return 1
    if 'new_thread_id: "accepted-thread-id"' not in current_state_after_accept:
        print("accept-handoff did not synchronize current_state target thread id")
        return 1

    try:
        expect_success(cwd, "start", "--backend", "app-server-plan")
    except AssertionError as exc:
        print(str(exc))
        return 1

    try:
        expect_success(cwd, "prepare-server")
    except AssertionError as exc:
        print(str(exc))
        return 1

    try:
        expect_success(cwd, "mark-migrated", "--thread-id", "test-thread-id")
    except AssertionError as exc:
        print(str(exc))
        return 1
    registry = json.loads((cwd / ".codex-handoff/thread_registry.json").read_text(encoding="utf-8"))
    latest = registry["threads"][-1]
    if latest.get("migrated") is not True or latest.get("new_thread_id") != "test-thread-id":
        print("mark-migrated did not update latest registry entry")
        return 1
    current_state_after_mark = (cwd / ".codex-handoff/current_state.yaml").read_text(encoding="utf-8")
    if "handoff_created: true" not in current_state_after_mark or "migrated_to_new_thread: true" not in current_state_after_mark:
        print("mark-migrated did not synchronize current_state migration flags")
        return 1

    server_plan = json.loads((cwd / ".codex-handoff/app_server_request.json").read_text(encoding="utf-8"))
    endpoints = [item.get("endpoint") for item in server_plan.get("requests", [])]
    if endpoints != ["thread/start", "turn/start"]:
        print("prepare-server did not write expected endpoint plan")
        return 1

    server, server_url = run_fake_app_server()
    try:
        expect_success(cwd, "start", "--backend", "app-server-http", "--server-url", server_url)
    finally:
        server.shutdown()
        server.server_close()
    paths = [call["path"] for call in FakeAppServerHandler.calls]
    if paths != ["/thread/start", "/turn/start"]:
        print("app-server-http did not call expected endpoints")
        return 1
    turn_body = FakeAppServerHandler.calls[1]["body"]
    if not isinstance(turn_body, dict) or turn_body.get("thread_id") != "fake-thread-id":
        print("app-server-http did not pass thread_id into turn/start")
        return 1
    result_payload = json.loads((cwd / ".codex-handoff/app_server_result.json").read_text(encoding="utf-8"))
    if result_payload.get("thread_id") != "fake-thread-id" or result_payload.get("status") != "executed":
        print("app-server-http did not persist executed result")
        return 1

    server, server_url = run_fake_app_server()
    FakeAppServerHandler.fail_first_thread_start = True
    config_payload = json.loads(original_config)
    config_payload["app_server"]["server_url"] = server_url
    config_payload["app_server"]["retries"] = 1
    config_path.write_text(json.dumps(config_payload, ensure_ascii=False), encoding="utf-8", newline="\n")
    try:
        expect_success(cwd, "start", "--backend", "app-server-http")
    finally:
        server.shutdown()
        server.server_close()
        config_path.write_text(original_config, encoding="utf-8", newline="\n")
    paths = [call["path"] for call in FakeAppServerHandler.calls]
    if paths != ["/thread/start", "/thread/start", "/turn/start"]:
        print("configured app-server-http did not retry expected endpoint sequence")
        return 1

    server, server_url = run_fake_app_server()
    try:
        expect_success(cwd, "probe", "app-server", "--server-url", server_url, "--json", "--write")
    finally:
        server.shutdown()
        server.server_close()
    probe_payload = json.loads((cwd / ".codex-handoff/probe_app_server.json").read_text(encoding="utf-8"))
    if probe_payload.get("available") is not True:
        print("app-server probe did not report available fake server")
        return 1

    deep_link_probe = run(cwd, "probe", "deep-link", "--json", "--write")
    if deep_link_probe.stdout:
        json.loads(deep_link_probe.stdout)

    bad_config = json.loads(original_config)
    bad_config["app_server"]["timeout_seconds"] = -1
    config_path.write_text(json.dumps(bad_config, ensure_ascii=False), encoding="utf-8", newline="\n")
    expect_failure(cwd, "verify")
    config_path.write_text(original_config, encoding="utf-8", newline="\n")

    next_prompt = cwd / ".codex-handoff/next_prompt.md"
    original_next_prompt = next_prompt.read_text(encoding="utf-8")
    next_prompt.write_text("bad prompt\n", encoding="utf-8", newline="\n")
    expect_failure(cwd, "verify")
    next_prompt.write_text(original_next_prompt, encoding="utf-8", newline="\n")

    app_plan = cwd / ".codex-handoff/app_server_request.json"
    original_plan = app_plan.read_text(encoding="utf-8")
    plan_payload = json.loads(original_plan)
    plan_payload["status"] = "executed"
    app_plan.write_text(json.dumps(plan_payload, ensure_ascii=False), encoding="utf-8", newline="\n")
    expect_failure(cwd, "verify")
    app_plan.write_text(original_plan, encoding="utf-8", newline="\n")

    evidence = cwd / ".codex-handoff/evidence.jsonl"
    original_evidence = evidence.read_text(encoding="utf-8")
    evidence.write_text(original_evidence + "not-json\n", encoding="utf-8", newline="\n")
    expect_failure(cwd, "verify")
    evidence.write_text(original_evidence, encoding="utf-8", newline="\n")

    print("test_codex_handoff: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
