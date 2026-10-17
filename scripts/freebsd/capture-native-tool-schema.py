#!/usr/bin/env python3
"""Capture native Codex's outgoing tool schemas against a local fake provider.

The recorder binds to loopback, never forwards requests, rejects any API key
other than its process-local dummy value, and writes only the `tools` field.
It is for catalog inspection; the fake provider returns a fixed final message
and never asks Codex to invoke a tool.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


MODEL = "gpt-5.6-luna"
API_KEY = "freebsd-local-schema-capture-only"
FINAL_MESSAGE = "LOCAL_SCHEMA_CAPTURE_COMPLETE"


def sse_response() -> bytes:
    events = [
        {"type": "response.created", "response": {"id": "resp-freebsd-schema-capture"}},
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "role": "assistant",
                "id": "msg-freebsd-schema-capture",
                "content": [{"type": "output_text", "text": FINAL_MESSAGE}],
            },
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp-freebsd-schema-capture",
                "usage": {
                    "input_tokens": 0,
                    "input_tokens_details": None,
                    "output_tokens": 0,
                    "output_tokens_details": None,
                    "total_tokens": 0,
                },
            },
        },
    ]
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        for event in events
    ).encode()


class SchemaCapture:
    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] | None = None
        self.error: str | None = None
        self.request_keys: list[str] | None = None
        self.tool_value_type: str | None = None
        self.request_shape: dict[str, Any] | None = None
        self.schema_source: str | None = None
        self.lock = threading.Lock()


SCHEMA_FIELDS = {
    "type",
    "name",
    "namespace",
    "description",
    "parameters",
    "input_schema",
    "defer_loading",
    "strict",
    "format",
    "tools",
}


def project_schema(node: dict[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in SCHEMA_FIELDS:
        if key not in node:
            continue
        value = node[key]
        if key == "tools" and isinstance(value, list):
            projected[key] = [project_schema(tool) for tool in value if isinstance(tool, dict)]
        else:
            projected[key] = value
    return projected


def nested_schema_groups(value: Any) -> list[dict[str, Any]]:
    """Find tool namespaces in structured request input without saving text."""
    groups: list[dict[str, Any]] = []
    if isinstance(value, dict):
        children = value.get("tools")
        if (
            isinstance(children, list)
            and children
            and all(isinstance(child, dict) for child in children)
            and any("tools" in child or "parameters" in child for child in children)
        ):
            groups.append(project_schema(value))
        for key, child in value.items():
            if key != "tools":
                groups.extend(nested_schema_groups(child))
    elif isinstance(value, list):
        for child in value:
            groups.extend(nested_schema_groups(child))
    return groups


def schema_count(value: Any) -> int:
    if isinstance(value, dict):
        if "name" in value and "type" in value and not isinstance(value.get("tools"), list):
            return 1
        return sum(schema_count(child) for child in value.values())
    if isinstance(value, list):
        return sum(schema_count(child) for child in value)
    return 0


def configured_jerboa_environment(config_path: Path) -> dict[str, str]:
    if not config_path.is_file():
        raise FileNotFoundError(f"configured Codex file not found: {config_path}")
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    server = config.get("mcp_servers", {}).get("jerboa")
    expected_command = str(Path.home() / ".local/bin/jmcp")
    if not isinstance(server, dict) or server.get("command") != expected_command:
        raise ValueError("configured Jerboa server command did not match the expected local jmcp executable")
    configured_env = server.get("env", {})
    if not isinstance(configured_env, dict):
        raise ValueError("configured Jerboa environment was not a TOML table")
    # Only copy the two non-credential path selectors required by Jerboa.
    allowed = {"JERBOA_MCP_DATA_DIR", "JERBOA_MCP_REPO"}
    selected = {key: value for key, value in configured_env.items() if key in allowed}
    if not all(isinstance(value, str) for value in selected.values()):
        raise ValueError("configured Jerboa path selectors must be strings")
    return selected


def shape_summary(value: Any, depth: int = 0) -> Any:
    if isinstance(value, dict):
        keys = sorted(value)
        result: dict[str, Any] = {"keys": keys}
        if depth < 5:
            result["children"] = {
                key: shape_summary(value[key], depth + 1)
                for key in keys
            }
        return result
    if isinstance(value, list):
        return {
            "list_length": len(value),
            "first_items": [shape_summary(item, depth + 1) for item in value[:3]],
        }
    return type(value).__name__


def make_handler(capture: SchemaCapture) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            # Avoid logging request headers, prompts, or payloads.
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/v1/responses":
                self.send_error(404)
                return
            if self.headers.get("Authorization") != f"Bearer {API_KEY}":
                self.send_error(401)
                return
            length_header = self.headers.get("Content-Length", "")
            try:
                length = int(length_header)
            except ValueError:
                self.send_error(411)
                return
            if not 0 <= length <= 10_000_000:
                self.send_error(413)
                return
            try:
                body = json.loads(self.rfile.read(length))
                if isinstance(body, dict):
                    with capture.lock:
                        capture.request_keys = sorted(body)
                        capture.tool_value_type = type(body.get("tools")).__name__
                        capture.request_shape = shape_summary(body)
                tools = body.get("tools") if isinstance(body, dict) else None
                if not isinstance(tools, list):
                    tools = nested_schema_groups(body.get("input", [])) if isinstance(body, dict) else []
                    with capture.lock:
                        capture.schema_source = "nested structured input" if tools else None
                else:
                    with capture.lock:
                        capture.schema_source = "top-level tools field"
                if not tools:
                    with capture.lock:
                        capture.error = "outgoing request exposed no structured tool schemas"
                if not all(isinstance(tool, dict) for tool in tools):
                    raise ValueError("tool schema entries were not JSON objects")
                # Keep only actual schema fields; never persist instructions,
                # conversation input, authentication headers, or environment.
                sanitized = [project_schema(tool) for tool in tools]
                with capture.lock:
                    capture.tools = sanitized
            except Exception as error:  # surfaced after CLI exits
                with capture.lock:
                    capture.error = f"request capture failed: {type(error).__name__}: {error}"
                self.send_error(400)
                return

            payload = sse_response()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def capture_native_schema(
    codex: Path, output: Path, timeout: float, include_jerboa: bool, config_path: Path
) -> int:
    if not codex.is_file():
        raise FileNotFoundError(f"native Codex binary not found: {codex}")
    output.parent.mkdir(parents=True, exist_ok=True)
    capture = SchemaCapture()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(capture))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}/v1"

    with tempfile.TemporaryDirectory(prefix="codex-local-schema-capture-") as codex_home:
        config = f'''model = "{MODEL}"
model_provider = "freebsd_schema_capture"

[model_providers.freebsd_schema_capture]
name = "FreeBSD local schema capture"
base_url = "{base_url}"
wire_api = "responses"
env_key = "OPENAI_API_KEY"

'''
        if include_jerboa:
            env = configured_jerboa_environment(config_path)
            config += f'''[mcp_servers.jerboa]
enabled = true
command = {json.dumps(str(Path.home() / ".local/bin/jmcp"))}
args = []
'''
            if env:
                inline_env = ", ".join(
                    f"{key} = {json.dumps(value)}" for key, value in sorted(env.items())
                )
                config += f"env = {{ {inline_env} }}\n"
        Path(codex_home, "config.toml").write_text(config, encoding="utf-8")
        command = [
            str(codex),
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "--enable",
            "code_mode_host",
            "--model",
            MODEL,
            "--cd",
            codex_home,
            "Return exactly LOCAL_SCHEMA_CAPTURE_COMPLETE. Do not call any tool.",
        ]
        environment = os.environ.copy()
        environment["CODEX_HOME"] = codex_home
        environment["OPENAI_API_KEY"] = API_KEY
        environment.pop("OPENAI_BASE_URL", None)
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
            print(f"FAIL local schema capture timed out after {timeout:g}s")
            return 1
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)

    if completed.returncode != 0 and capture.tools is None:
        print(f"FAIL native Codex exited {completed.returncode}")
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item") if isinstance(event, dict) else None
            if isinstance(item, dict) and item.get("type") == "error":
                print(f"native error item: {item.get('message', item.get('error', 'unknown'))}")
            if isinstance(event, dict) and event.get("type") in {"error", "turn.failed"}:
                error = event.get("error", event.get("message", "unknown"))
                print(f"native {event['type']}: {error}")
        if capture.error:
            print(capture.error)
        if capture.request_keys is not None:
            print(
                "local request shape: "
                f"keys={capture.request_keys!r}; tools field type={capture.tool_value_type}; "
                f"nested shape={capture.request_shape!r}"
            )
        for line in completed.stderr.splitlines():
            if any(word in line.lower() for word in ("error", "failed", "invalid", "denied")):
                print(line.replace(API_KEY, "[redacted-local-test-key]")[:1000])
        return 1

    if capture.tools is None:
        print(capture.error or "FAIL Codex made no request to the local Responses stub")
        return 1
    if capture.error:
        print(f"FAIL {capture.error}; no catalog artifact written")
        print(
            "local request shape: "
            f"keys={capture.request_keys!r}; tools field type={capture.tool_value_type}; "
            f"nested shape={capture.request_shape!r}"
        )
        return 1
    if FINAL_MESSAGE not in completed.stdout:
        print("FAIL fake provider response did not complete the native turn")
        return 1

    startup_errors = []
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if isinstance(item, dict) and item.get("type") == "error":
            message = item.get("message")
            if isinstance(message, str) and message.startswith("Code Mode is unavailable because failed to spawn code-mode host"):
                startup_errors.append(message)

    artifact = {
        "native_binary": str(codex.resolve()),
        "model": MODEL,
        "source": "actual outgoing local-provider request; no external model was used",
        "schema_source": capture.schema_source,
        "schema_count": schema_count(capture.tools),
        "cli_exit_code": completed.returncode,
        "startup_errors": startup_errors,
        "tools": capture.tools,
    }
    output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"PASS captured {schema_count(capture.tools)} outgoing tool schemas "
        f"in {len(capture.tools)} groups to {output} (CLI exit {completed.returncode})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex", type=Path, default=Path.home() / ".local/bin/codex-freebsd"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / "work/codex-runtime-repro/native-tool-schemas.json",
    )
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument(
        "--include-jerboa", action=argparse.BooleanOptionalAction, default=True,
        help="initialize configured local Jerboa MCP and capture its advertised schemas",
    )
    parser.add_argument(
        "--config", type=Path, default=Path.home() / ".codex/config.toml",
        help="read only the configured Jerboa command and non-credential path selectors",
    )
    args = parser.parse_args()
    return capture_native_schema(args.codex, args.output, args.timeout, args.include_jerboa, args.config)


if __name__ == "__main__":
    raise SystemExit(main())
