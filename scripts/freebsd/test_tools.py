#!/usr/bin/env python3
"""Run manifest-driven native Codex tool probes and assert actual dispatch."""

from __future__ import annotations

import argparse
import binascii
import json
import os
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any


DEFAULT_CODEX = Path.home() / ".local/bin/codex-freebsd"
DEFAULT_CASES = Path(__file__).with_name("smoke-cases.json")
DEFAULT_FIXTURE_ROOT = Path.home() / "work/codex-runtime-repro/cases"
TOOL_ITEM_TYPES = {
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "collab_tool_call",
    "web_search",
}


def tiny_test_png() -> bytes:
    """Build a deterministic one-pixel red RGBA PNG for view_image smoke tests."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff"))
        + chunk(b"IEND", b"")
    )


def parse_jsonl(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at stdout line {line_number}: {error}")
        if isinstance(event, dict):
            events.append(event)
    return events


def completed_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for event in events:
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict):
            items.append(item)
    return items


def matches_started_completed_pair(
    events: list[dict[str, Any]], expectation: dict[str, Any]
) -> bool:
    """Require one live item to start and later complete with the same ID."""
    for event in events:
        item = event.get("item")
        if event.get("type") != "item.started" or not isinstance(item, dict):
            continue
        if item.get("type") != expectation.get("type"):
            continue
        command = item.get("command", "")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        if expectation.get("command_contains") not in str(command):
            continue
        item_id = item.get("id")
        if item_id is None:
            continue
        if any(
            completed_event.get("type") == "item.completed"
            and isinstance(completed_item := completed_event.get("item"), dict)
            and completed_item.get("id") == item_id
            and completed_item.get("type") == expectation.get("type")
            for completed_event in events
        ):
            return True
    return False


def event_error_summary(events: list[dict[str, Any]], stderr: str) -> str:
    for event in events:
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "error":
            return str(item.get("message", "Codex emitted an error item"))
    for event in events:
        if event.get("type") in {"turn.failed", "error"}:
            return str(event.get("message", event.get("error", "turn failed")))
    if stderr.strip():
        return stderr.strip().splitlines()[-1]
    return "no matching MCP tool event was emitted"


def build_command(
    codex: Path,
    model: str,
    case: dict[str, Any],
    fixture: Path,
) -> list[str]:
    sandbox = case.get("sandbox", "read-only")
    command = [
        str(codex),
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--enable",
        "code_mode_host",
        "--model",
        model,
        "--cd",
        str(fixture),
    ]
    # A `default` case deliberately omits --sandbox to exercise the normal
    # configured policy, including any FreeBSD fail-closed behavior.
    if sandbox != "default":
        sandbox_index = command.index("--enable")
        command[sandbox_index:sandbox_index] = ["--sandbox", sandbox]
    for override in case.get("config_overrides", []):
        expanded = override.replace("__FIXTURE_ROOT__", str(fixture)).replace(
            "__FIXTURE_MCP_SERVER__",
            str(Path(__file__).with_name("fixture_mcp_server.py").resolve()),
        )
        command.extend(["--config", expanded])
    prompt = case["prompt"].replace("__FIXTURE_ROOT__", str(fixture))
    if case.get("fixture_image"):
        prompt = prompt.replace("__FIXTURE_IMAGE_PATH__", str(fixture / case["fixture_image"]))
    command.append(prompt)
    return command


def matches_item(item: dict[str, Any], expectation: dict[str, Any]) -> bool:
    if item.get("type") != expectation.get("type"):
        return False
    for key in ("server", "tool", "status", "exit_code", "success", "name", "path"):
        if key in expectation and item.get(key) != expectation[key]:
            return False
    for key in ("aggregated_output", "interaction_input", "stdout", "stderr", "output", "path"):
        for marker in expectation.get(f"{key}_contains", []):
            if marker not in str(item.get(key, "")):
                return False
    if expectation.get("result_payload_present") and item.get("result") is None:
        return False
    if "changes" in expectation:
        changes = item.get("changes")
        if not isinstance(changes, list):
            return False
        observed = [
            {key: change.get(key) for key in ("path", "kind")}
            for change in changes
            if isinstance(change, dict)
        ]
        if observed != expectation["changes"]:
            return False
    for key, value in expectation.get("arguments", {}).items():
        if item.get("arguments", {}).get(key) != value:
            return False
    if expectation.get("arguments_exact") and item.get("arguments", {}) != expectation.get("arguments", {}):
        return False
    if expectation.get("result_contains"):
        serialized_result = json.dumps(item.get("result"), sort_keys=True)
        if expectation["result_contains"] not in serialized_result:
            return False
    return True


def matches_fail_closed(
    events: list[dict[str, Any]],
    marker: str,
    forbidden_types: list[str],
    stderr: str = "",
) -> bool:
    direct_errors = []
    items = completed_items(events)
    for event in events:
        item = event.get("item")
        if event.get("type") in {"error", "turn.failed"}:
            direct_errors.append(json.dumps(event, sort_keys=True).lower())
        elif event.get("type") == "item.completed" and isinstance(item, dict):
            if item.get("type") == "error" or (
                item.get("type") == "command_execution" and item.get("status") == "failed"
            ):
                direct_errors.append(json.dumps(item, sort_keys=True).lower())
    # FreeBSD may reject sandbox creation before an execution item is emitted.
    # In that path the router logs the concrete CreateProcess rejection to
    # stderr while JSONL contains only agent_message items. Require both the
    # router source and the exact fail-closed marker; assistant prose alone is
    # never evidence.
    direct_router_rejection = any(
        "codex_core::tools::router:" in line
        and "error=" in line
        and marker.lower() in line.lower()
        for line in stderr.splitlines()
    )
    if (
        not any(marker.lower() in evidence for evidence in direct_errors)
        and not direct_router_rejection
    ):
        return False
    forbidden = set(forbidden_types)
    return not any(
        item.get("type") in forbidden and item.get("status") == "completed"
        for item in items
    )


def unallowed_tool_items(
    items: list[dict[str, Any]], allowed_extras: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    unmatched_allowances = list(allowed_extras)
    unexpected = []
    for item in items:
        if item.get("type") not in TOOL_ITEM_TYPES:
            continue
        for index, allowance in enumerate(unmatched_allowances):
            if matches_item(item, allowance):
                del unmatched_allowances[index]
                break
        else:
            unexpected.append(item)
    return unexpected


def validate_fixture(
    fixture: Path, expected_files: dict[str, str], expected_binary_files: dict[str, bytes]
) -> str | None:
    observed = {}
    for path in fixture.rglob("*"):
        if path.is_file():
            relative = path.relative_to(fixture).as_posix()
            if relative.startswith(".codex-home/"):
                continue
            if relative in expected_binary_files:
                if path.read_bytes() != expected_binary_files[relative]:
                    return f"fixture binary contents differed for {relative}"
                observed[relative] = "<expected binary>"
            else:
                observed[relative] = path.read_text(encoding="utf-8")
    expected_observed: dict[str, str] = dict(expected_files)
    expected_observed.update({path: "<expected binary>" for path in expected_binary_files})
    if observed != expected_observed:
        return f"fixture contents differed: expected {expected_observed!r}, observed {observed!r}"
    return None


def self_test() -> None:
    """Check JSONL event parsing and require actual tool items, never prose."""
    mcp_item = {
        "type": "mcp_tool_call",
        "server": "codex_fixture",
        "tool": "fixture_read",
        "status": "completed",
        "arguments": {"path": "probe.txt"},
        "arguments_exact": True,
        "result": {"content": [{"type": "text", "text": "fixture-ok"}]},
    }
    line = json.dumps({"type": "item.completed", "item": mcp_item})
    events = parse_jsonl(line + "\n")
    items = completed_items(events)
    assert len(items) == 1 and matches_item(items[0], {
        "type": "mcp_tool_call",
        "server": "codex_fixture",
        "tool": "fixture_read",
        "status": "completed",
        "arguments": {"path": "probe.txt"},
        "arguments_exact": True,
        "result_payload_present": True,
        "result_contains": "fixture-ok",
    })
    assert not matches_item(items[0], {
        "type": "mcp_tool_call", "server": "jerboa", "tool": "fixture_read",
    }), "wrong server unexpectedly matched"
    assert not matches_item({**mcp_item, "arguments": {"path": "probe.txt", "unexpected": True}}, {
        "type": "mcp_tool_call", "server": "codex_fixture", "tool": "fixture_read",
        "arguments": {"path": "probe.txt"}, "arguments_exact": True,
    }), "unexpected MCP arguments unexpectedly matched"
    prose = completed_items(parse_jsonl(json.dumps({
        "type": "item.completed", "item": {"type": "agent_message", "text": "fixture-ok ran"}
    })))
    assert len(prose) == 1 and not matches_item(prose[0], {
        "type": "mcp_tool_call", "server": "codex_fixture", "tool": "fixture_read",
    }), "assistant prose was incorrectly treated as a tool call"
    assert matches_item({
        "type": "file_change", "status": "completed",
        "changes": [{"path": "patch-marker.txt", "kind": "add"}],
    }, {
        "type": "file_change", "status": "completed",
        "changes": [{"path": "patch-marker.txt", "kind": "add"}],
    })
    assert not matches_item({
        "type": "file_change", "status": "completed",
        "changes": [{"path": "unexpected.txt", "kind": "add"}],
    }, {
        "type": "file_change", "status": "completed",
        "changes": [{"path": "patch-marker.txt", "kind": "add"}],
    }), "unexpected patch path unexpectedly matched"
    assert unallowed_tool_items([{
        "type": "command_execution", "status": "completed", "command": ["touch", "unrequested"]
    }], []), "unexpected completed tool call was not detected"
    assert unallowed_tool_items([{
        "type": "collab_tool_call", "status": "completed", "tool": "send_input"
    }], []), "unexpected collaboration tool call was not detected"
    assert not unallowed_tool_items([{
        "type": "mcp_tool_call", "server": "jerboa", "tool": "jerboa_compact_tool_manifest",
        "status": "completed", "arguments": {"query": "fixture"},
    }], [{
        "type": "mcp_tool_call", "server": "jerboa", "tool": "jerboa_compact_tool_manifest",
        "status": "completed",
    }]), "an explicitly allowed harmless discovery call was rejected"
    fail_closed = parse_jsonl(json.dumps({
        "type": "item.completed",
        "item": {"type": "command_execution", "status": "failed", "aggregated_output": "filesystem sandbox cannot be enforced"},
    }))
    assert matches_fail_closed(fail_closed, "filesystem sandbox cannot be enforced", ["command_execution"])
    router_only_failure = parse_jsonl(json.dumps({
        "type": "item.completed",
        "item": {
            "type": "agent_message",
            "text": "Tool error: filesystem sandbox cannot be enforced",
        },
    }))
    assert matches_fail_closed(
        router_only_failure,
        "filesystem sandbox cannot be enforced",
        ["command_execution"],
        '2026-01-01 ERROR codex_core::tools::router: error=exec_command failed: '
        'CreateProcess { message: "Rejected(\\"filesystem sandbox cannot be enforced\\")" }',
    ), "native router rejection log was not accepted"
    live_interactive_command = parse_jsonl("\n".join([
        json.dumps({"type": "item.started", "item": {"id": "cmd-1", "type": "command_execution", "command": "sh -c 'read input'"}}),
        json.dumps({"type": "item.completed", "item": {"id": "cmd-1", "type": "command_execution", "status": "completed"}}),
    ]))
    assert matches_started_completed_pair(live_interactive_command, {
        "type": "command_execution", "command_contains": "read input",
    })
    assert not matches_started_completed_pair(live_interactive_command, {
        "type": "command_execution", "command_contains": "different command",
    })
    assert not matches_fail_closed(parse_jsonl(json.dumps({
        "type": "item.completed", "item": {"type": "agent_message", "text": "sandbox cannot be enforced"},
    })), "filesystem sandbox cannot be enforced", ["command_execution"]), "assistant prose passed fail-closed test"
    assert not matches_fail_closed(
        router_only_failure,
        "filesystem sandbox cannot be enforced",
        ["command_execution"],
        "Reading additional input from stdin...",
    ), "assistant prose without a router rejection log passed fail-closed test"


def run_case(
    codex: Path,
    model: str,
    fixture_root: Path,
    case: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": case["id"],
        "status": "fail",
    }
    with tempfile.TemporaryDirectory(prefix=f"codex-{case['id']}-", dir=fixture_root) as temp:
        fixture = Path(temp)
        expected_binary_files: dict[str, bytes] = {}
        if case.get("fixture_image"):
            png = tiny_test_png()
            image_path = fixture / case["fixture_image"]
            image_path.write_bytes(png)
            expected_binary_files[case["fixture_image"]] = png

        environment = os.environ.copy()
        if case.get("isolated_codex_home"):
            isolated_home = fixture / ".codex-home"
            isolated_home.mkdir(mode=0o700)
            source_home = Path(environment.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
            auth_file = source_home / "auth.json"
            if auth_file.is_file():
                shutil.copyfile(auth_file, isolated_home / "auth.json")
                os.chmod(isolated_home / "auth.json", 0o600)
            environment["CODEX_HOME"] = str(isolated_home)

        command = build_command(codex, model, case, fixture)
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            result["reason"] = f"Codex timed out after {timeout:g}s before a verified result"
            return result
        except OSError as error:
            result["reason"] = f"could not start native Codex: {error}"
            return result

        try:
            events = parse_jsonl(completed.stdout)
        except ValueError as error:
            result["reason"] = str(error)
            result["exit_code"] = completed.returncode
            return result

        expected_exit = case.get("expected_cli_exit", 0)
        allowed_exits = expected_exit if isinstance(expected_exit, list) else [expected_exit]
        if completed.returncode not in allowed_exits:
            result["reason"] = event_error_summary(events, completed.stderr)
            result["exit_code"] = completed.returncode
            return result

        items = completed_items(events)
        for expectation in case.get("started_item_expectations", []):
            if not matches_started_completed_pair(events, expectation):
                result["reason"] = (
                    "missing started/completed item pair matching "
                    f"{expectation!r}"
                )
                return result
        limitation = None
        if "fail_closed_error_contains" in case:
            if not matches_fail_closed(
                events,
                case["fail_closed_error_contains"],
                case.get("no_completed_item_types", []),
                completed.stderr,
            ):
                result["reason"] = (
                    "no direct failed-tool/event evidence contained the expected fail-closed "
                    f"marker {case['fail_closed_error_contains']!r}"
                )
                return result
            limitation = f"direct fail-closed error: {case['fail_closed_error_contains']}"
        expectations = case.get("expected_items", [])
        unmatched_items = list(items)
        for expectation in expectations:
            for index, item in enumerate(unmatched_items):
                if matches_item(item, expectation):
                    del unmatched_items[index]
                    break
            else:
                alternative = case.get("accepted_fail_closed")
                if alternative and matches_fail_closed(
                    events,
                    alternative["error_contains"],
                    alternative.get("no_completed_item_types", []),
                    completed.stderr,
                ):
                    limitation = f"direct fail-closed error: {alternative['error_contains']}"
                    break
                reason = (
                    "missing completed tool item matching "
                    f"{expectation!r}; observed {len(items)} completed items"
                )
                error_summary = event_error_summary(events, completed.stderr)
                if error_summary != "no matching MCP tool event was emitted":
                    reason += f"; {error_summary}"
                result["reason"] = reason
                return result

        if not limitation:
            unexpected = unallowed_tool_items(
                unmatched_items, case.get("allowed_extra_tool_items", [])
            )
            if unexpected:
                summary = [
                    {key: item.get(key) for key in ("type", "server", "tool", "status", "command") if key in item}
                    for item in unexpected
                ]
                result["reason"] = f"unexpected completed tool items were emitted: {summary!r}"
                return result

        message_text = "\n".join(
            str(item.get("text", ""))
            for item in items
            if item.get("type") == "agent_message"
        )
        for marker in case.get("agent_message_contains", []):
            if marker not in message_text:
                result["reason"] = f"agent message did not include expected marker {marker!r}"
                return result

        fixture_error = validate_fixture(
            fixture, case.get("expected_files", {}), expected_binary_files
        )
        if fixture_error:
            result["reason"] = fixture_error
            return result

        result["status"] = "limitation" if limitation else "pass"
        result["exit_code"] = completed.returncode
        result["completed_items"] = len(items)
        result["outcome"] = limitation or "expected tool event observed"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", type=Path, default=DEFAULT_CODEX)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURE_ROOT)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--case", action="append", help="run only this case id; repeatable")
    parser.add_argument(
        "--report",
        type=Path,
        help="write a sanitized JSON summary (default: <fixture-root>/tool-test-report.json)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="validate JSONL/event assertions without model calls")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print("PASS runner JSONL event, exact MCP identity/arguments, patch path, and no-prose assertions")
        return 0

    if not args.codex.is_file():
        print(f"FAIL: native Codex executable is missing: {args.codex}", file=sys.stderr)
        return 2
    if not os.access(args.codex, os.X_OK):
        print(f"FAIL: native Codex executable is not executable: {args.codex}", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(args.cases.read_text(encoding="utf-8"))
        cases = manifest["cases"]
        if not isinstance(cases, list) or not cases:
            raise ValueError("cases must be a non-empty array")
        for case in cases:
            for key in ("id", "prompt"):
                if not isinstance(case.get(key), str) or not case[key]:
                    raise ValueError(f"each case requires a non-empty string `{key}`")
        if args.case:
            cases = [case for case in cases if case["id"] in args.case]
            if not cases:
                raise ValueError(f"no cases matched {args.case!r}")
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as error:
        print(f"FAIL: invalid case manifest {args.cases}: {error}", file=sys.stderr)
        return 2

    outcomes = []
    fixture_root = args.fixture_root.expanduser().resolve()
    if not args.dry_run:
        fixture_root.mkdir(parents=True, exist_ok=True)
    for case in cases:
        command = build_command(args.codex, args.model, case, Path("/fixture"))
        if args.dry_run:
            printable_command = shlex.join(command).replace("\n", "\\n")
            print(f"DRY-RUN {case['id']}: {printable_command}")
            continue
        outcome = run_case(args.codex, args.model, fixture_root, case, args.timeout)
        outcomes.append(outcome)
        target = f" {case['server']}.{case['tool']}" if case.get("server") else ""
        outcome_label = f" [{outcome['outcome']}]" if outcome.get("outcome") else ""
        reason = f" — {outcome['reason']}" if outcome.get("reason") else ""
        print(f"{outcome['status'].upper()} {case['id']}:{target}{outcome_label}{reason}")

    if args.dry_run:
        return 0
    passed = sum(outcome["status"] == "pass" for outcome in outcomes)
    limitations = sum(outcome["status"] == "limitation" for outcome in outcomes)
    report_path = args.report or fixture_root / "tool-test-report.json"
    report_path = report_path.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    sanitized_outcomes = []
    for outcome in outcomes:
        row = {
            "id": outcome["id"],
            "status": outcome["status"],
            "exit_code": outcome.get("exit_code"),
            "completed_items": outcome.get("completed_items"),
        }
        if outcome["status"] != "pass":
            # Avoid retaining model output, MCP payloads, or arbitrary command
            # stderr. Store only the classification needed to diagnose CI.
            row["diagnostic"] = (
                "sandbox_rejection_observed"
                if "direct fail-closed error" in outcome.get("outcome", "")
                else "no_verified_expected_tool_result"
            )
        sanitized_outcomes.append(row)
    report = {
        "schema_version": 1,
        "codex": str(args.codex.resolve()),
        "model": args.model,
        "results": sanitized_outcomes,
        "passed": passed,
        "limitations": limitations,
        "total": len(outcomes),
    }
    temporary_report = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary_report.replace(report_path)
    print(f"REPORT: sanitized results saved to {report_path}")
    print(f"RESULT: {passed}/{len(outcomes)} tool dispatches passed; {limitations} platform limitations")
    return 0 if passed == len(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
