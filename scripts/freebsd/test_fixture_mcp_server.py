#!/usr/bin/env python3
"""Protocol-level tests for the disposable MCP fixture used by smoke tests."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SERVER = Path(__file__).with_name("fixture_mcp_server.py").resolve()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-fixture-mcp-test-") as temp:
        scratch = Path(temp)
        root = scratch / "fixture-root"
        root.mkdir()
        environment = os.environ.copy()
        environment["CODEX_FIXTURE_ROOT"] = str(root)
        process = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        assert process.stdin is not None and process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        output_buffer = bytearray()

        def send(message: dict[str, Any]) -> None:
            process.stdin.write((json.dumps(message) + "\n").encode())
            process.stdin.flush()

        def receive(timeout: float = 4.0) -> dict[str, Any]:
            while b"\n" not in output_buffer:
                ready = selector.select(timeout)
                require(bool(ready), f"timed out waiting for fixture response (exit={process.poll()})")
                chunk = os.read(process.stdout.fileno(), 65536)
                require(bool(chunk), "fixture server closed stdout before replying")
                output_buffer.extend(chunk)
            line, _, remaining = output_buffer.partition(b"\n")
            output_buffer[:] = remaining
            value = json.loads(line)
            require(isinstance(value, dict), "fixture response was not a JSON object")
            return value

        def call(request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            })
            response = receive()
            require(response.get("id") == request_id, f"response id mismatch: {response!r}")
            return response.get("result", {})

        try:
            send({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            })
            initialized = receive()
            require(initialized.get("result", {}).get("serverInfo", {}).get("name") == "codex-freebsd-test-fixture",
                    "initialize did not return fixture server identity")
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})

            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools = receive().get("result", {}).get("tools", [])
            names = {tool.get("name") for tool in tools}
            require(names == {
                "fixture_echo", "fixture_write", "fixture_read", "fixture_fail",
                "fixture_parallel_left", "fixture_parallel_right",
            }, f"unexpected fixture tools: {names!r}")

            send({"jsonrpc": "2.0", "id": 11, "method": "resources/list"})
            listed_resources = receive().get("result", {}).get("resources", [])
            require(len(listed_resources) == 1 and listed_resources[0].get("name") == "codex-fixture-note",
                    f"resources/list did not return the fixture resource: {listed_resources!r}")
            send({"jsonrpc": "2.0", "id": 12, "method": "resources/templates/list"})
            listed_templates = receive().get("result", {}).get("resourceTemplates", [])
            require(len(listed_templates) == 1 and listed_templates[0].get("name") == "codex-fixture-item",
                    f"resources/templates/list did not return the fixture template: {listed_templates!r}")
            send({
                "jsonrpc": "2.0", "id": 13, "method": "resources/read",
                "params": {"uri": "fixture://codex/resource"},
            })
            resource = receive().get("result", {}).get("contents", [])
            require(len(resource) == 1 and resource[0].get("text") == "FIXTURE_RESOURCE_CONTENT_OK",
                    f"resources/read did not return exact fixture contents: {resource!r}")

            written = call(3, "fixture_write", {"path": "nested/value.txt", "content": "safe fixture\n"})
            require(not written.get("isError"), f"fixture write failed: {written!r}")
            require((root / "nested/value.txt").read_text() == "safe fixture\n", "write did not create exact content")
            read = call(4, "fixture_read", {"path": "nested/value.txt"})
            require(not read.get("isError") and "safe fixture\\n" in json.dumps(read), f"read mismatch: {read!r}")

            traversal = call(5, "fixture_write", {"path": "../outside.txt", "content": "blocked"})
            require(traversal.get("isError") is True, f"path traversal was not rejected: {traversal!r}")
            require(not (scratch / "outside.txt").exists(), "path traversal escaped fixture root")

            failure = call(6, "fixture_fail", {})
            require(failure.get("isError") is True and "fixture injected MCP error" in json.dumps(failure),
                    f"injected error was not propagated: {failure!r}")
            malformed = call(9, "fixture_write", {"path": "missing-content.txt"})
            require(malformed.get("isError") is True and "fixture tool error" in json.dumps(malformed),
                    f"malformed arguments did not produce a tool error: {malformed!r}")
            unknown = call(10, "fixture_not_listed", {})
            require(unknown.get("isError") is True and "unknown fixture tool" in json.dumps(unknown),
                    f"unknown tool did not produce a tool error: {unknown!r}")

            for request_id, tool in ((7, "fixture_parallel_left"), (8, "fixture_parallel_right")):
                send({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": {}},
                })
            parallel = [receive(), receive()]
            require({response.get("id") for response in parallel} == {7, 8},
                    f"parallel responses missing: {parallel!r}")
            require(all(
                response.get("result", {}).get("structuredContent", {}).get("parallel") is True
                for response in parallel
            ), f"parallel handlers were not concurrent: {parallel!r}")
            require((root / "left.started").read_text() == "left", "left handler did not run")
            require((root / "right.started").read_text() == "right", "right handler did not run")

            print("PASS fixture MCP initialize/tool+resource list/read/write/path-boundary/error/concurrent dispatch")
            return 0
        finally:
            selector.close()
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if process.returncode not in (0, -15, -9):
                stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
                print(f"fixture MCP server exit={process.returncode}: {stderr}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
