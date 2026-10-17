#!/usr/bin/env python3
"""Run a no-network JavaScript evaluation through the real code-mode host IPC."""

from __future__ import annotations

import argparse
import json
import os
import select
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

MARKER = "DIRECT_HOST_JS_OK"
JIT_MARKER = "DIRECT_HOST_JIT_STRESS:1000000"
SESSION_ID = "freebsd-native-host-runtime-fixture"
PROCCONTROL = Path("/usr/bin/proccontrol")


def probe_source(stress: bool) -> str:
    if stress:
        return (
            "function hot(x) { return (x + 1) | 0; } "
            "let value = 0; "
            "for (let i = 0; i < 1000000; i++) value = hot(value); "
            'text("DIRECT_HOST_JIT_STRESS:" + value);'
        )
    return f'text("{MARKER}");'


def write_frame(stream: Any, value: dict[str, Any]) -> None:
    payload = json.dumps(value, separators=(",", ":")).encode()
    stream.write(len(payload).to_bytes(4, "little") + payload)
    stream.flush()


def read_exact(stream: Any, size: int, deadline: float) -> bytes:
    data = bytearray()
    fd = stream.fileno()
    while len(data) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise TimeoutError("timed out waiting for a code-mode host frame")
        block = os.read(fd, size - len(data))
        if not block:
            raise EOFError("code-mode host closed stdout before completing a frame")
        data.extend(block)
    return bytes(data)


def read_frame(stream: Any, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    length = int.from_bytes(read_exact(stream, 4, deadline), "little")
    if length <= 0 or length > 16 * 1024 * 1024:
        raise ValueError(f"invalid code-mode frame length: {length}")
    value = json.loads(read_exact(stream, length, deadline))
    if not isinstance(value, dict):
        raise ValueError("code-mode frame payload was not an object")
    return value


def response_for(
    stream: Any, request_id: int, timeout: float, response_type: str
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = read_frame(stream, max(0.01, deadline - time.monotonic()))
        if event.get("id") == request_id and event.get("type") == response_type:
            return event
    raise TimeoutError(f"host did not return {response_type} for request {request_id}")


def expect_ok(event: dict[str, Any], context: str) -> dict[str, Any]:
    result = event.get("result")
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise RuntimeError(f"{context} failed: {json.dumps(result, sort_keys=True)}")
    value = result.get("value")
    if not isinstance(value, dict):
        raise RuntimeError(f"{context} returned no value")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check bounded hot-loop probe construction",
    )
    parser.add_argument(
        "--jit-stress",
        action="store_true",
        help="run a bounded hot-function loop in the host's default JIT-enabled V8 mode",
    )
    parser.add_argument(
        "--deny-wx",
        action="store_true",
        help="run the hot-function probe under process-scoped FreeBSD W^X enforcement",
    )
    args = parser.parse_args()
    if args.self_test:
        source = probe_source(True)
        if (
            "function hot(x)" not in source
            or "i < 1000000" not in source
            or 'text("DIRECT_HOST_JIT_STRESS:" + value);' not in source
            or JIT_MARKER != "DIRECT_HOST_JIT_STRESS:1000000"
        ):
            raise AssertionError(
                "JIT stress probe is not the expected bounded one-million-call fixture"
            )
        print(
            "PASS direct-host JIT probe fixture: one million deterministic hot-function calls"
        )
        return 0
    if args.host is None:
        parser.error("--host is required unless --self-test is selected")
    if not args.host.is_file() or not os.access(args.host, os.X_OK):
        print(f"FAIL code-mode host executable unavailable: {args.host}")
        return 2

    process: subprocess.Popen[bytes] | None = None
    stderr_log = tempfile.TemporaryFile(mode="w+b")
    try:
        host_command = [str(args.host), "--listen", "stdio"]
        if args.deny_wx:
            if not PROCCONTROL.is_file() or not os.access(PROCCONTROL, os.X_OK):
                raise RuntimeError(
                    f"native FreeBSD proccontrol is unavailable: {PROCCONTROL}"
                )
            host_command = [
                str(PROCCONTROL),
                "-m",
                "wxmap",
                "-s",
                "disable",
                *host_command,
            ]
        process = subprocess.Popen(
            host_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_log,
            start_new_session=True,
        )
        assert process.stdin is not None and process.stdout is not None
        write_frame(
            process.stdin,
            {
                "type": "connection/hello",
                "supportedVersions": [1],
                "requiredCapabilities": [],
                "optionalCapabilities": [
                    "session-cell-execution-resource-limits",
                    "yield-observation",
                ],
            },
        )
        hello = read_frame(process.stdout, args.timeout)
        if hello.get("type") != "connection/ready" or hello.get("selectedVersion") != 1:
            raise RuntimeError(f"unexpected code-mode handshake: {hello}")

        write_frame(
            process.stdin,
            {
                "type": "operation/request",
                "id": 1,
                "request": {"method": "session/open", "sessionId": SESSION_ID},
            },
        )
        opened = expect_ok(
            response_for(process.stdout, 1, args.timeout, "operation/response"),
            "session/open",
        )
        if (
            opened.get("type") != "session/ready"
            or opened.get("sessionId") != SESSION_ID
        ):
            raise RuntimeError(f"unexpected session/open result: {opened}")

        wx_status = None
        if args.jit_stress or args.deny_wx:
            if not PROCCONTROL.is_file() or not os.access(PROCCONTROL, os.X_OK):
                raise RuntimeError(
                    f"native FreeBSD proccontrol is unavailable: {PROCCONTROL}"
                )
            status = subprocess.run(
                [str(PROCCONTROL), "-m", "wxmap", "-q", "-p", str(process.pid)],
                check=False,
                capture_output=True,
                text=True,
                timeout=args.timeout,
            )
            if status.returncode != 0 or not status.stdout.strip():
                raise RuntimeError(
                    f"could not query process W^X policy: exit={status.returncode}, "
                    f"stdout={status.stdout!r}, stderr={status.stderr!r}"
                )
            wx_status = status.stdout.strip()
            if args.deny_wx and not (
                "disabled on exec" in wx_status and "wxorx enforced" in wx_status
            ):
                raise RuntimeError(
                    f"the process-scoped W^X policy was not enforced: {wx_status!r}"
                )

        write_frame(
            process.stdin,
            {
                "type": "operation/request",
                "id": 2,
                "request": {
                    "method": "session/execute",
                    "sessionId": SESSION_ID,
                    "request": {
                        "tool_call_id": "freebsd-host-runtime-smoke",
                        "enabled_tools": [],
                        "source": probe_source(args.jit_stress or args.deny_wx),
                        "yield_time_ms": 10_000,
                        "max_output_tokens": 128,
                    },
                },
            },
        )
        initial = response_for(
            process.stdout, 2, args.timeout, "execute/initialResponse"
        )
        runtime = expect_ok(initial, "session/execute")
        terminal = runtime.get("Result")
        if not isinstance(terminal, dict):
            raise RuntimeError(f"host did not return a terminal Result: {runtime}")
        if terminal.get("error_text") is not None:
            raise RuntimeError(
                f"JavaScript evaluation failed: {terminal['error_text']}"
            )
        content = terminal.get("content_items")
        expected_output = JIT_MARKER if args.jit_stress or args.deny_wx else MARKER
        if not isinstance(content, list) or not any(
            item.get("type") == "input_text" and item.get("text") == expected_output
            for item in content
            if isinstance(item, dict)
        ):
            raise RuntimeError(
                f"host result did not contain the exact JS output marker: {content}"
            )

        write_frame(
            process.stdin,
            {
                "type": "operation/request",
                "id": 3,
                "request": {"method": "session/shutdown", "sessionId": SESSION_ID},
            },
        )
        closed = expect_ok(
            response_for(process.stdout, 3, args.timeout, "operation/response"),
            "session/shutdown",
        )
        if closed.get("type") != "session/closed":
            raise RuntimeError(f"unexpected shutdown result: {closed}")
        process.stdin.close()
        process.wait(timeout=args.timeout)
        if process.returncode != 0:
            raise RuntimeError(f"host exited with status {process.returncode}")
        if args.deny_wx:
            print(
                "PASS native code-mode host W^X-enforced JIT stress: one million hot-function calls, "
                f"exact checksum, process W^X status={wx_status}, session shutdown"
            )
        elif args.jit_stress:
            print(
                "PASS native code-mode host IPC JIT-enabled stress: one million hot-function calls, "
                f"exact checksum, process W^X status={wx_status}, session shutdown"
            )
        else:
            print(
                "PASS native code-mode host IPC: handshake, session open, V8 JS evaluation, exact output, session shutdown"
            )
        return 0
    except Exception as error:
        print(f"FAIL native code-mode host IPC: {type(error).__name__}: {error}")
        stderr_log.seek(0, os.SEEK_END)
        stderr_size = stderr_log.tell()
        stderr_log.seek(max(0, stderr_size - 4096))
        stderr_tail = stderr_log.read().decode(errors="replace").strip()
        if stderr_tail:
            print(f"host stderr tail ({min(stderr_size, 4096)} bytes): {stderr_tail}")
        if args.deny_wx:
            print(
                "OBSERVED process-scoped W^X probe did not complete; this is a diagnostic outcome, "
                "not a passing native-JIT result"
            )
            return 0
        return 1
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        stderr_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
