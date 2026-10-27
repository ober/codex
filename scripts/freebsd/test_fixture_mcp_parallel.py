#!/usr/bin/env python3
"""Prove the disposable fixture MCP server handles read-only calls concurrently."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time
from typing import Any


SERVER = Path(__file__).with_name("fixture_mcp_server.py").resolve()
TOOL_NAMES = {"fixture_parallel_left", "fixture_parallel_right"}


def send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()


def receive(
    process: subprocess.Popen[str], selector: selectors.BaseSelector, timeout: float
) -> dict[str, Any]:
    assert process.stdout is not None
    if not selector.select(timeout):
        raise TimeoutError(f"fixture MCP server did not reply within {timeout:g}s")
    line = process.stdout.readline()
    if not line:
        raise RuntimeError(
            f"fixture MCP server exited early with status {process.poll()}"
        )
    value = json.loads(line)
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON-RPC object, got {value!r}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scratch-root",
        type=Path,
        default=Path(tempfile.gettempdir()),
    )
    args = parser.parse_args()
    args.scratch_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="fixture-mcp-parallel-", dir=args.scratch_root
    ) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["CODEX_FIXTURE_ROOT"] = str(root)
        process = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=environment,
        )
        selector = selectors.DefaultSelector()
        assert process.stdout is not None
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "fixture-test", "version": "1"},
                    },
                },
            )
            initialized = receive(process, selector, 3.0)
            if initialized.get("id") != 1 or "error" in initialized:
                raise AssertionError(f"MCP initialize failed: {initialized!r}")
            send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            send(
                process,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            listed = receive(process, selector, 3.0)
            schemas = {
                tool.get("name"): tool
                for tool in listed.get("result", {}).get("tools", [])
                if isinstance(tool, dict)
            }
            if not TOOL_NAMES <= schemas.keys():
                raise AssertionError(
                    f"parallel probe schema missing: {sorted(TOOL_NAMES - schemas.keys())}"
                )
            for name in TOOL_NAMES:
                if schemas[name].get("annotations", {}).get("readOnlyHint") is not True:
                    raise AssertionError(f"{name} is not annotated read-only")

            for request_id, name in enumerate(sorted(TOOL_NAMES), start=10):
                send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": {}},
                    },
                )
            responses: dict[int, dict[str, Any]] = {}
            deadline = time.monotonic() + 7.0
            while len(responses) < 2 and time.monotonic() < deadline:
                response = receive(
                    process, selector, max(0.01, deadline - time.monotonic())
                )
                request_id = response.get("id")
                if request_id in {10, 11}:
                    responses[request_id] = response
            if set(responses) != {10, 11}:
                raise AssertionError(
                    f"expected both concurrent responses, got IDs {sorted(responses)}"
                )
            for request_id, response in responses.items():
                result = response.get("result", {})
                structured = result.get(
                    "structuredContent", result.get("structured_content", {})
                )
                if (
                    result.get("isError") is not False
                    or structured.get("parallel") is not True
                    or structured.get("max_concurrent") != 2
                ):
                    raise AssertionError(
                        f"call {request_id} did not prove measured overlap: {response!r}"
                    )
            if any(path.is_file() for path in root.iterdir()):
                raise AssertionError(
                    "read-only concurrency fixture unexpectedly created files"
                )
            print(
                "PASS fixture MCP JSON-RPC read-only pair proved max_concurrent=2 with no filesystem writes"
            )
            return 0
        finally:
            selector.close()
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)


if __name__ == "__main__":
    raise SystemExit(main())
