#!/usr/bin/env python3
"""Disposable stdio MCP server used by the FreeBSD Codex tool smoke tests."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(os.environ["CODEX_FIXTURE_ROOT"]).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
WRITE_LOCK = threading.Lock()
PARALLEL_CONDITION = threading.Condition()
PARALLEL_IN_FLIGHT: set[str] = set()
PARALLEL_OVERLAP = 0


def _tool(
    name: str,
    description: str,
    properties: dict[str, dict[str, str]],
    required: list[str],
    *,
    read_only: bool,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
        "annotations": {
            "readOnlyHint": read_only,
            "idempotentHint": read_only,
            "destructiveHint": False,
        },
    }


TOOLS = [
    _tool(
        "fixture_echo",
        "Return the supplied marker unchanged.",
        {"marker": {"type": "string"}},
        ["marker"],
        read_only=True,
    ),
    _tool(
        "fixture_write",
        "Write text to a path contained within the disposable test fixture root.",
        {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"],
        read_only=False,
    ),
    _tool(
        "fixture_read",
        "Read text from a path contained within the disposable test fixture root.",
        {"path": {"type": "string"}},
        ["path"],
        read_only=True,
    ),
    _tool(
        "fixture_fail",
        "Return a controlled MCP tool error for error propagation testing.",
        {},
        [],
        read_only=True,
    ),
    _tool(
        "fixture_parallel_left",
        "Read-only concurrency probe; rendezvous with the parallel-right call in memory and report measured overlap.",
        {},
        [],
        read_only=True,
    ),
    _tool(
        "fixture_parallel_right",
        "Read-only concurrency probe; rendezvous with the parallel-left call in memory and report measured overlap.",
        {},
        [],
        read_only=True,
    ),
]


def _error(message: str) -> dict[str, Any]:
    return {"isError": True, "content": [{"type": "text", "text": message}]}


def _success(value: Any) -> dict[str, Any]:
    return {
        "isError": False,
        "content": [{"type": "text", "text": json.dumps(value, sort_keys=True)}],
        "structuredContent": value,
    }


def _safe_path(value: Any) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("path must be a non-empty relative fixture path")
    target = (ROOT / value).resolve()
    if not target.is_relative_to(ROOT):
        raise ValueError("path escapes the fixture root")
    return target


def _parallel(name: str, partner: str) -> dict[str, Any]:
    global PARALLEL_OVERLAP
    deadline = time.monotonic() + 5.0
    with PARALLEL_CONDITION:
        PARALLEL_IN_FLIGHT.add(name)
        if len(PARALLEL_IN_FLIGHT) > PARALLEL_OVERLAP:
            PARALLEL_OVERLAP = len(PARALLEL_IN_FLIGHT)
        PARALLEL_CONDITION.notify_all()
        while PARALLEL_OVERLAP < 2 and time.monotonic() < deadline:
            PARALLEL_CONDITION.wait(timeout=max(0.0, deadline - time.monotonic()))
        overlap = PARALLEL_OVERLAP
        PARALLEL_IN_FLIGHT.discard(name)
        if not PARALLEL_IN_FLIGHT:
            PARALLEL_OVERLAP = 0
        PARALLEL_CONDITION.notify_all()
    if overlap < 2:
        return _error(f"parallel fixture observed only {overlap} overlapping call(s)")
    return _success({"marker": name, "partner": partner, "parallel": True, "max_concurrent": overlap})


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "fixture_echo":
            return _success({"marker": arguments["marker"]})
        if name == "fixture_write":
            target = _safe_path(arguments["path"])
            content = arguments["content"]
            if not isinstance(content, str):
                raise ValueError("content must be text")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
            return _success({"path": target.relative_to(ROOT).as_posix(), "bytes": len(content.encode())})
        if name == "fixture_read":
            target = _safe_path(arguments["path"])
            return _success({"path": target.relative_to(ROOT).as_posix(), "content": target.read_text(encoding="utf-8")})
        if name == "fixture_fail":
            return _error("fixture injected MCP error")
        if name == "fixture_parallel_left":
            return _parallel("left", "right")
        if name == "fixture_parallel_right":
            return _parallel("right", "left")
        return _error(f"unknown fixture tool: {name}")
    except (KeyError, OSError, ValueError) as error:
        return _error(f"fixture tool error: {error}")


def _write_response(message: dict[str, Any]) -> None:
    encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
    with WRITE_LOCK:
        sys.stdout.write(encoded + "\n")
        sys.stdout.flush()


def _handle(message: dict[str, Any]) -> None:
    request_id = message.get("id")
    method = message.get("method")
    if method == "notifications/initialized":
        return
    if method == "initialize":
        _write_response(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": message.get("params", {}).get("protocolVersion", "2024-11-05"),
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"listChanged": False, "subscribe": False},
                    },
                    "serverInfo": {"name": "codex-freebsd-test-fixture", "version": "1.0.0"},
                },
            }
        )
        return
    if method == "ping":
        _write_response({"jsonrpc": "2.0", "id": request_id, "result": {}})
        return
    if method == "tools/list":
        _write_response({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
        return
    if method == "resources/list":
        _write_response({
            "jsonrpc": "2.0", "id": request_id,
            "result": {"resources": [{
                "uri": "fixture://codex/resource",
                "name": "codex-fixture-note",
                "description": "Static local fixture resource.",
                "mimeType": "text/plain",
            }]},
        })
        return
    if method == "resources/templates/list":
        _write_response({
            "jsonrpc": "2.0", "id": request_id,
            "result": {"resourceTemplates": [{
                "uriTemplate": "fixture://codex/items/{name}",
                "name": "codex-fixture-item",
                "description": "Parameterized local fixture resource.",
                "mimeType": "text/plain",
            }]},
        })
        return
    if method == "resources/read":
        uri = message.get("params", {}).get("uri")
        if uri == "fixture://codex/resource":
            contents = [{
                "uri": uri,
                "mimeType": "text/plain",
                "text": "FIXTURE_RESOURCE_CONTENT_OK",
            }]
            _write_response({"jsonrpc": "2.0", "id": request_id, "result": {"contents": contents}})
        else:
            _write_response({
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32002, "message": f"unknown fixture resource: {uri}"},
            })
        return
    if method == "tools/call":
        params = message.get("params", {})
        result = _call(str(params.get("name", "")), params.get("arguments", {}))
        _write_response({"jsonrpc": "2.0", "id": request_id, "result": result})
        return
    if request_id is not None:
        _write_response(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        )


def main() -> int:
    for raw_line in sys.stdin:
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            if message.get("method") == "tools/call":
                threading.Thread(target=_handle, args=(message,), daemon=True).start()
            else:
                _handle(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
