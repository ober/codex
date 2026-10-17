#!/usr/bin/env python3
"""Exercise all five configured OpenAI Developer Docs MCP read-only tools.

This checks the server directly over its configured Streamable HTTP endpoint.
It does not prove Codex dispatch; `test_code_mode_appserver.py --case
openai-docs` covers that separately when the native Code Mode companion exists.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

ENDPOINT = "https://developers.openai.com/mcp"
PROTOCOL = "2025-03-26"


def parse_response(raw: str) -> dict[str, Any]:
    data = "\n".join(line[6:] for line in raw.splitlines() if line.startswith("data: "))
    if data:
        return json.loads(data)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise AssertionError("MCP returned a non-object JSON response")
    return value


class Client:
    def __init__(self) -> None:
        self.session: str | None = None
        self.request_id = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not method.startswith("notifications/"):
            self.request_id += 1
            payload["id"] = self.request_id
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                raw = response.read().decode("utf-8", "replace")
                status = response.status
        except urllib.error.HTTPError as error:
            # Do not echo request headers or any credential-bearing data.
            raise RuntimeError(f"MCP {method} returned HTTP {error.code}") from None
        if method.startswith("notifications/"):
            if status not in {200, 202, 204}:
                raise AssertionError(f"MCP notification returned HTTP {status}")
            return {}
        response_value = parse_response(raw)
        if response_value.get("error"):
            raise RuntimeError(f"MCP {method} returned JSON-RPC error: {response_value['error']}")
        return response_value.get("result", {})

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})


def text_result(result: dict[str, Any]) -> str:
    return "\n".join(
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )


def main() -> int:
    client = Client()
    initialize = client.request("initialize", {
        "protocolVersion": PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "codex-freebsd-mcp-read-test", "version": "1"},
    })
    server_info = initialize.get("serverInfo", {})
    if server_info.get("name") != "openai-docs-mcp":
        raise AssertionError(f"unexpected MCP server identity: {server_info.get('name')!r}")
    client.request("notifications/initialized")
    catalog = client.request("tools/list")
    tools = {tool.get("name"): tool for tool in catalog.get("tools", [])}
    expected_names = {
        "search_openai_docs",
        "list_openai_docs",
        "fetch_openai_doc",
        "list_api_endpoints",
        "get_openapi_spec",
    }
    if set(tools) != expected_names:
        raise AssertionError(f"OpenAI docs MCP catalog differs: {sorted(tools)}")
    expected_properties = {
        "search_openai_docs": {"query", "limit", "cursor"},
        "list_openai_docs": {"limit", "cursor"},
        "fetch_openai_doc": {"url", "anchor"},
        "list_api_endpoints": set(),
        "get_openapi_spec": {"url", "languages", "codeExamplesOnly"},
    }
    expected_required = {
        "search_openai_docs": {"query"},
        "list_openai_docs": set(),
        "fetch_openai_doc": {"url"},
        "list_api_endpoints": set(),
        "get_openapi_spec": {"url"},
    }
    for name, tool in tools.items():
        annotations = tool.get("annotations", {})
        if annotations.get("readOnlyHint") is not True or annotations.get("destructiveHint") is not False:
            raise AssertionError(f"{name} is not annotated read-only and non-destructive")
        schema = tool.get("inputSchema", {})
        properties = set(schema.get("properties", {}))
        required = set(schema.get("required", []))
        if properties != expected_properties[name] or required != expected_required[name]:
            raise AssertionError(f"{name} argument schema differs: properties={sorted(properties)}, required={sorted(required)}")

    calls = [
        ("list_openai_docs", {"limit": 3}, "https://"),
        ("search_openai_docs", {"query": "Responses API", "limit": 2}, "responses"),
        ("fetch_openai_doc", {"url": "https://developers.openai.com/api/docs/guides/tools-connectors-mcp"}, "# MCP servers"),
        ("list_api_endpoints", {}, "/responses"),
        ("get_openapi_spec", {"url": "https://api.openai.com/v1/responses", "codeExamplesOnly": True}, "Create a model response"),
    ]
    for name, arguments, marker in calls:
        result = client.call(name, arguments)
        if result.get("isError") is True:
            raise AssertionError(f"{name} returned isError=true")
        body = text_result(result)
        if marker.lower() not in body.lower():
            raise AssertionError(f"{name} result omitted its expected content marker {marker!r}")
        print(f"PASS {name}: non-error result; expected content marker present")

    print(f"PASS OpenAI Developer Docs MCP {server_info.get('version')}: {len(tools)} read-only schemas and {len(calls)} actual calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
