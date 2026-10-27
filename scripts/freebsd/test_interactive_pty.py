#!/usr/bin/env python3
"""Exercise the native interactive Codex TUI through a real PTY.

The live case requires an already-built FreeBSD ``codex-freebsd`` binary and
its sibling ``codex-code-mode-host-freebsd``. It copies only auth.json into a
temporary CODEX_HOME, sends one bounded prompt through the TUI, and checks both
the resulting scratch file and a persisted command_execution event. It never
uses ``--no-daemon`` and never prints authentication or rollout contents.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path
from typing import Any


DEFAULT_CODEX = Path.home() / ".local/bin/codex-freebsd"
DEFAULT_SCRATCH_ROOT = Path(tempfile.gettempdir())
MODEL = "gpt-5.6-sol"
PLACEHOLDER = b"Ask Codex to do anything"
TOOL_OUTPUT = "TUI_PTY_TOOL_RAN"
FILE_CONTENT = "TUI_PTY_FILE_OK\n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def valid_command_execution(item: Any, marker: str) -> bool:
    if not isinstance(item, dict) or item.get("type") not in {
        "command_execution",
        "CommandExecution",
    }:
        return False
    # The rollout's item id is the command call id. Require it alongside the
    # command and result so prose or an unrelated completed item cannot pass.
    if not isinstance(item.get("id"), str) or not item["id"]:
        return False
    if item.get("status") != "completed" or item.get("exit_code") != 0:
        return False
    output = item.get("aggregated_output")
    command = item.get("command")
    command_text = (
        " ".join(command) if isinstance(command, list) else str(command or "")
    )
    return marker in str(output or "") and marker in command_text


def contains_tool_event(records: list[dict[str, Any]], marker: str) -> bool:
    """Accept rollout line encodings only when they contain the typed item."""
    for record in records:
        candidates: list[Any] = [record]
        while candidates:
            value = candidates.pop()
            if isinstance(value, dict):
                if valid_command_execution(value, marker):
                    return True
                candidates.extend(value.values())
            elif isinstance(value, list):
                candidates.extend(value)
    return False


def parse_rollouts(home: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    sessions = home / "sessions"
    if not sessions.is_dir():
        return records
    for path in sessions.rglob("*.jsonl"):
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict):
                        records.append(value)
        except OSError:
            continue
    return records


def sanitized_terminal_tail(terminal: bytearray) -> str:
    """Keep enough visible failure context without printing credentials."""
    text = terminal.decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1<redacted>", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "<redacted-api-key>", text)
    text = re.sub(r"/home/[^\s/:]+/\.codex/auth\.json", "<redacted-auth-path>", text)
    lines = [
        line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()
    ]
    return " | ".join(lines[-12:])[-2400:]


def rollout_event_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose event identity/status only, never tool arguments or outputs."""
    output: list[dict[str, Any]] = []
    stack: list[Any] = list(records)
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if value.get("type") in {
                "command_execution",
                "CommandExecution",
                "mcp_tool_call",
                "error",
                "agent_message",
            }:
                row = {
                    key: value[key]
                    for key in ("type", "id", "status", "server", "tool", "exit_code")
                    if key in value
                }
                output.append(row)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return output


def native_elf(path: Path) -> bool:
    result = subprocess.run(
        ["/usr/bin/file", "-L", "-b", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return (
        result.returncode == 0 and "FreeBSD" in result.stdout and "ELF" in result.stdout
    )


def procstat_image(pid: int) -> str:
    result = subprocess.run(
        ["/usr/bin/procstat", "-b", str(pid)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"procstat could not inspect the live TUI process (exit {result.returncode})"
        )
    return result.stdout


def wait_readable(fd: int, deadline: float) -> bool:
    remaining = deadline - time.monotonic()
    return remaining > 0 and bool(select.select([fd], [], [], min(0.25, remaining))[0])


def read_pty(fd: int, buffer: bytearray, deadline: float) -> None:
    while wait_readable(fd, deadline):
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            continue
        except OSError:
            return
        if not chunk:
            return
        buffer.extend(chunk)


def terminate_process(process: subprocess.Popen[bytes], master: int) -> None:
    """Reap only the exact process group started by this test."""

    def signal_group(sig: int) -> None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    def group_exists() -> bool:
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False

    signal_group(signal.SIGINT)
    if process.poll() is None:
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            signal_group(signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                signal_group(signal.SIGKILL)
                process.wait(timeout=3)
    if group_exists():
        signal_group(signal.SIGTERM)
        deadline = time.monotonic() + 3
        while group_exists() and time.monotonic() < deadline:
            time.sleep(0.05)
    if group_exists():
        signal_group(signal.SIGKILL)
        deadline = time.monotonic() + 3
        while group_exists() and time.monotonic() < deadline:
            time.sleep(0.05)
    if group_exists():
        raise RuntimeError(
            "the test-owned TUI process group still has live descendants after cleanup"
        )
    try:
        os.close(master)
    except OSError:
        pass


def self_test() -> None:
    completed = {
        "type": "command_execution",
        "id": "call-fixture-1",
        "status": "completed",
        "exit_code": 0,
        "command": ["sh", "-c", "printf TUI_PTY_TOOL_RAN"],
        "aggregated_output": "TUI_PTY_TOOL_RAN\n",
    }
    if not contains_tool_event(
        [
            {
                "timestamp": "fixture",
                "item": {
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "item": completed,
                    },
                },
            }
        ],
        TOOL_OUTPUT,
    ):
        raise AssertionError("typed completed tool event fixture was not recognized")
    # Native rollout JSON serializes the Rust ThreadItemDetails variant as
    # CommandExecution inside event_msg.payload.item. This is the shape seen in
    # the live FreeBSD TUI run.
    native_completed = {
        "type": "CommandExecution",
        "id": "exec-native-fixture-1",
        "status": "completed",
        "exit_code": 0,
        "command": ["sh", "-c", "printf TUI_PTY_TOOL_RAN"],
        "aggregated_output": "TUI_PTY_TOOL_RAN\n",
    }
    if not contains_tool_event(
        [
            {
                "timestamp": "fixture",
                "type": "event_msg",
                "payload": {"type": "item_completed", "item": native_completed},
            }
        ],
        TOOL_OUTPUT,
    ):
        raise AssertionError("native CommandExecution rollout item was not recognized")
    for wrong in (
        {**completed, "type": "agent_message"},
        {**native_completed, "type": "AgentMessage"},
        {**completed, "id": ""},
        {**completed, "status": "failed"},
        {**completed, "exit_code": 1},
        {**native_completed, "status": "failed"},
        {**native_completed, "exit_code": 1},
        {**native_completed, "id": ""},
        {**completed, "aggregated_output": "assistant claimed success"},
        {**completed, "command": ["printf", "unrelated"]},
    ):
        if contains_tool_event([{"item": wrong}], TOOL_OUTPUT):
            raise AssertionError(
                "TUI tool event parser accepted prose, failure, or unrelated command"
            )
    # Ensure assistant text that merely repeats the marker cannot stand in for
    # an actual typed command item.
    if contains_tool_event(
        [{"type": "agent_message", "message": TOOL_OUTPUT}], TOOL_OUTPUT
    ):
        raise AssertionError("assistant text was mistaken for an executed tool")
    print(
        "PASS interactive PTY evidence self-test: typed success required; prose/failures rejected"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", type=Path, default=DEFAULT_CODEX)
    parser.add_argument("--scratch-root", type=Path, default=DEFAULT_SCRATCH_ROOT)
    parser.add_argument(
        "--timeout", type=float, default=180, help="overall bounded live-test timeout"
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if sys.platform != "freebsd15":
        raise RuntimeError(
            f"run this test with native FreeBSD Python; got {sys.platform!r}"
        )

    codex = args.codex.resolve(strict=True)
    if codex.name != "codex-freebsd":
        raise RuntimeError(
            f"expected the native executable basename codex-freebsd, got {codex.name!r}"
        )
    helper = codex.with_name("codex-code-mode-host-freebsd")
    if not helper.is_file() or not os.access(helper, os.X_OK):
        raise RuntimeError(f"native sibling Code Mode helper is unavailable: {helper}")
    if not native_elf(codex) or not native_elf(helper):
        raise RuntimeError(
            "CLI and companion must both be native FreeBSD ELF executables"
        )
    source_auth_home = Path(
        os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    ).expanduser()
    source_auth = source_auth_home / "auth.json"
    if not source_auth.is_file():
        raise RuntimeError(
            f"file-backed account auth is unavailable at {source_auth}; no credential was printed"
        )
    args.scratch_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="codex-freebsd-tui-pty-", dir=args.scratch_root
    ) as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir(mode=0o700)
        isolated_home = root / "codex-home"
        isolated_home.mkdir(mode=0o700)
        shutil.copyfile(source_auth, isolated_home / "auth.json")
        os.chmod(isolated_home / "auth.json", 0o600)
        marker_file = workspace / "native-tui-tool-marker.txt"
        command_marker = f"TUI_PTY_TOOL_RAN_{os.getpid()}"
        file_content = f"TUI_PTY_FILE_OK_{os.getpid()}\n"
        prompt = (
            "Use the code-mode shell execution tool exactly once. Run a shell command that writes "
            f"exactly {file_content.strip()} followed by one newline to {shell_quote(str(marker_file))}, "
            f"then prints {command_marker}. Return the command output. Do not claim success without running it."
        )
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(isolated_home)
        environment["TERM"] = "xterm-256color"
        # Use native FreeBSD utility directories only; in particular, do not
        # fall through to ~/.local/bin where `codex` is the Linux default.
        environment["PATH"] = os.pathsep.join(
            (
                "/usr/local/bin",
                "/bin",
                "/usr/bin",
                "/usr/sbin",
                "/sbin",
            )
        )
        # Force authentication through the isolated auth.json copy; do not
        # inherit API-key or alternate-endpoint environment overrides.
        for name in (
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
            "CODEX_ACCESS_TOKEN",
            "OPENAI_BASE_URL",
            "CODEX_AUTHAPI_BASE_URL",
            "CODEX_AGENT_IDENTITY_AUTHAPI_BASE_URL",
            "CODEX_AGENT_IDENTITY_JWKS_BASE_URL",
        ):
            environment.pop(name, None)
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        os.set_blocking(master, False)
        command = [
            str(codex),
            "--model",
            MODEL,
            "--sandbox",
            "danger-full-access",
            "--ask-for-approval",
            "never",
            "--cd",
            str(workspace),
            "--enable",
            "code_mode_host",
        ]
        process: subprocess.Popen[bytes] | None = None
        terminal = bytearray()
        try:
            process = subprocess.Popen(
                command,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=environment,
                start_new_session=True,
                close_fds=True,
            )
            os.close(slave)
            live_image = procstat_image(process.pid)
            if str(codex) not in live_image:
                raise RuntimeError(
                    "procstat did not identify the launched native codex-freebsd executable"
                )

            start_deadline = time.monotonic() + min(args.timeout, 45)
            while time.monotonic() < start_deadline:
                read_pty(master, terminal, min(start_deadline, time.monotonic() + 0.5))
                if PLACEHOLDER in terminal:
                    break
                if process.poll() is not None:
                    raise RuntimeError(
                        f"interactive TUI exited before its composer appeared (status {process.returncode})"
                    )
            else:
                raise TimeoutError("interactive TUI did not render its composer prompt")

            os.write(master, prompt.encode("utf-8"))
            # Match the repository's PTY integration helper: wait until the
            # typed draft is visible, then pause beyond the startup paste
            # ambiguity window before sending Enter as a separate keypress.
            echo_deadline = time.monotonic() + 30
            while (
                command_marker.encode("utf-8") not in terminal
                and time.monotonic() < echo_deadline
            ):
                read_pty(master, terminal, min(echo_deadline, time.monotonic() + 0.5))
            if command_marker.encode("utf-8") not in terminal:
                raise TimeoutError(
                    "interactive composer did not render the submitted draft"
                )
            time.sleep(0.25)
            os.write(master, b"\r")
            deadline = time.monotonic() + args.timeout
            tool_event_seen = False
            while time.monotonic() < deadline:
                read_pty(master, terminal, min(deadline, time.monotonic() + 0.5))
                if (
                    marker_file.is_file()
                    and marker_file.read_text(encoding="utf-8") == file_content
                ):
                    records = parse_rollouts(isolated_home)
                    if contains_tool_event(records, command_marker):
                        tool_event_seen = True
                        break
                if process.poll() is not None:
                    break
            if (
                not marker_file.is_file()
                or marker_file.read_text(encoding="utf-8") != file_content
            ):
                records = parse_rollouts(isolated_home)
                print("TUI_FAILURE_TERMINAL_TAIL: " + sanitized_terminal_tail(terminal))
                print(
                    "TUI_FAILURE_ROLLOUT_EVENTS: "
                    + json.dumps(rollout_event_summary(records), sort_keys=True)
                )
                raise RuntimeError(
                    "interactive TUI did not create the exact scratch marker file"
                )
            if not tool_event_seen:
                records = parse_rollouts(isolated_home)
                print("TUI_FAILURE_TERMINAL_TAIL: " + sanitized_terminal_tail(terminal))
                print(
                    "TUI_FAILURE_ROLLOUT_EVENTS: "
                    + json.dumps(rollout_event_summary(records), sort_keys=True)
                )
                raise RuntimeError(
                    "rollout contained no completed command_execution matching the unique command/output marker"
                )

            # The marker proves a real dispatched command; the rollout item
            # proves successful tool completion; this is a full TUI PTY path.
            print(
                "PASS native interactive TUI PTY: composer rendered and prompt submitted through the terminal"
            )
            print(
                "PASS native Code Mode dispatch: completed command_execution event and exact scratch file contents"
            )
            print(f"NATIVE_TUI_CLI_SHA256: {sha256(codex)}")
            print(f"NATIVE_TUI_HOST_SHA256: {sha256(helper)}")
            os.write(master, b"\x03")
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.write(master, b"\x04")
        finally:
            if process is not None:
                terminate_process(process, master)
            else:
                try:
                    os.close(master)
                except OSError:
                    pass
                try:
                    os.close(slave)
                except OSError:
                    pass
        # FreeBSD's normal TUI path embeds the app-server instead of launching
        # a managed daemon. Catch any unexpected daemon artifacts in this
        # private home before TemporaryDirectory removes it.
        daemon_artifacts = [
            isolated_home / "app-server-daemon" / "daemon.pid",
            isolated_home / "app-server-control" / "server.sock",
        ]
        leftovers = [
            str(path.relative_to(isolated_home))
            for path in daemon_artifacts
            if path.exists()
        ]
        if leftovers:
            raise RuntimeError(
                f"unexpected daemon artifacts remained in isolated CODEX_HOME: {leftovers}"
            )
    return 0


def shell_quote(value: str) -> str:
    """Small local quote helper to avoid logging or shelling out for quoting."""
    return "'" + value.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FAIL interactive TUI PTY: {error}", file=sys.stderr)
        raise SystemExit(1)
