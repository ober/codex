#!/usr/bin/env python3
"""Exercise nested Code Mode tools through native app-server item events.

Unlike `codex exec --json`, the app-server protocol emits ImageView items. The
custom Code Mode result is not a ThreadItem, so the test checks the actual
`custom_tool_call_output` in the next model request. A local Responses stub
sends fixed JavaScript, and the test passes only when native Code Mode executes
it and the expected returned output reaches that next request.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from test_jerboa_nonwriters import McpClient, build_nonwriter_cases, prepare_fixture, start_completion_server
from test_tools import tiny_test_png

MODEL = "gpt-5.6-luna"
API_KEY = "freebsd-code-mode-fixture-only"
MCP_SERVER = Path(__file__).with_name("fixture_mcp_server.py").resolve()
JERBOA_SERVER = Path(os.environ.get("CODEX_TEST_JERBOA_SERVER", Path.home() / ".local/bin/jmcp"))
JERBOA_HOME = Path(os.environ.get("CODEX_TEST_JERBOA_HOME", Path.home() / "mine/jerboa"))
JERBOA_BIN = Path(os.environ.get("CODEX_TEST_JERBOA_BIN", Path.home() / ".local/bin/jerboa"))
JERBOA_SCRATCH_PARENT = Path.home() / "work/codex-runtime-repro/freebsd-jerboa-fixtures"
EXPECTED_NESTED_TOOLS = {
    "apply_patch",
    "multi_agent_v1__close_agent",
    "multi_agent_v1__resume_agent",
    "multi_agent_v1__send_input",
    "multi_agent_v1__spawn_agent",
    "multi_agent_v1__wait_agent",
    "create_goal",
    "exec_command",
    "get_goal",
    "list_mcp_resource_templates",
    "list_mcp_resources",
    "read_mcp_resource",
    "update_goal",
    "view_image",
    "write_stdin",
}


def sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        for event in events
    ).encode()


def tool_response(code: str) -> bytes:
    return sse([
        {"type": "response.created", "response": {"id": "resp-code-mode-call"}},
        {"type": "response.output_item.done", "item": {
            "type": "custom_tool_call", "call_id": "call-code-mode-fixture",
            "name": "exec", "namespace": "functions", "input": code,
        }},
        {"type": "response.completed", "response": {
            "id": "resp-code-mode-call",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }},
    ])


def final_response() -> bytes:
    return sse([
        {"type": "response.created", "response": {"id": "resp-code-mode-final"}},
        {"type": "response.output_item.done", "item": {
            "type": "message", "role": "assistant", "id": "msg-code-mode-final",
            "content": [{"type": "output_text", "text": "FIXTURE_TURN_FINISHED"}],
        }},
        {"type": "response.completed", "response": {
            "id": "resp-code-mode-final",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }},
    ])


class Capture:
    def __init__(self, code: str) -> None:
        self.code = code
        self.requests: list[dict[str, Any]] = []
        self.error: str | None = None
        self.lock = threading.Lock()


def tool_schema_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        if isinstance(value.get("name"), str):
            names.append(value["name"])
        for child in value.values():
            names.extend(tool_schema_names(child))
    elif isinstance(value, list):
        for child in value:
            names.extend(tool_schema_names(child))
    return names


def nested_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [part for child in value.values() for part in nested_strings(child)]
    if isinstance(value, list):
        return [part for child in value for part in nested_strings(child)]
    return []


def validate_completed_turn(notification: dict[str, Any], turn_id: str) -> None:
    """Reject interrupted/failed terminal notifications for a completed probe."""
    if notification.get("method") != "turn/completed":
        raise AssertionError(f"expected turn/completed notification, got {notification.get('method')!r}")
    turn = notification.get("params", {}).get("turn", {})
    if turn.get("id") != turn_id:
        raise AssertionError(f"terminal notification belongs to {turn.get('id')!r}, expected {turn_id!r}")
    if turn.get("status") != "completed":
        raise AssertionError(f"turn {turn_id} ended with status {turn.get('status')!r}, expected 'completed'")


def self_test() -> int:
    valid = {"method": "turn/completed", "params": {"turn": {"id": "turn-1", "status": "completed"}}}
    validate_completed_turn(valid, "turn-1")
    for invalid in (
        {"method": "turn/completed", "params": {"turn": {"id": "turn-1", "status": "failed"}}},
        {"method": "turn/completed", "params": {"turn": {"id": "turn-1", "status": "interrupted"}}},
        {"method": "turn/completed", "params": {"turn": {"id": "turn-1"}}},
        {"method": "turn/completed", "params": {"turn": {"id": "turn-2", "status": "completed"}}},
        {"method": "turn/started", "params": {"turn": {"id": "turn-1", "status": "completed"}}},
    ):
        try:
            validate_completed_turn(invalid, "turn-1")
        except AssertionError:
            continue
        raise AssertionError(f"terminal-turn validator accepted invalid notification: {invalid!r}")
    print("PASS app-server terminal status validator rejects failed, interrupted, missing-status, wrong-ID and wrong-method notifications")
    return 0


def make_handler(capture: Capture) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/v1/responses" or self.headers.get("Authorization") != f"Bearer {API_KEY}":
                self.send_error(401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("request body was not an object")
                with capture.lock:
                    capture.requests.append(body)
                    number = len(capture.requests)
                payload = tool_response(capture.code) if number == 1 else final_response()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as error:  # surfaced after the app-server exits
                with capture.lock:
                    capture.error = f"{type(error).__name__}: {error}"
                self.send_error(400)

    return Handler


def read_messages(stdout: Any, messages: queue.Queue[Any]) -> None:
    for line in stdout:
        try:
            messages.put(json.loads(line))
        except json.JSONDecodeError:
            messages.put({"_invalid_json_line": True})
    messages.put(EOFError("app-server stdout closed"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="test terminal-notification assertions without starting Codex")
    parser.add_argument("--codex", type=Path, default=Path.home() / ".local/bin/codex-freebsd")
    parser.add_argument("--case", choices=("host-smoke", "tool-catalog", "agent-schema", "agent-lifecycle", "image", "resources", "templates", "resource-read", "goals", "apply-patch", "workspace-write-fail-closed", "jerboa-read", "jerboa-writers", "jerboa-matrix-probe", "jerboa-all", "openai-docs", "mcp-missing-provider"))
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--allow-known-provider-errors", action="store_true", help="exploration only: return zero after fully recording known provider protocol errors")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.case is None:
        parser.error("--case is required unless --self-test is selected")
    if not args.codex.is_file() or not os.access(args.codex, os.X_OK):
        print(f"FAIL native Codex executable unavailable: {args.codex}")
        return 2
    timeout = max(args.timeout, 600.0) if args.case == "jerboa-all" else args.timeout

    temp_parent: Path | None = None
    if args.case.startswith("jerboa-"):
        JERBOA_SCRATCH_PARENT.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent_stat = JERBOA_SCRATCH_PARENT.stat()
        if parent_stat.st_uid != os.geteuid() or parent_stat.st_mode & 0o022:
            print(f"FAIL Jerboa fixture root is not private: {JERBOA_SCRATCH_PARENT}")
            return 2
        if not JERBOA_SERVER.is_file() or not os.access(JERBOA_SERVER, os.X_OK):
            print(f"FAIL native Jerboa MCP executable unavailable: {JERBOA_SERVER}")
            return 2
        temp_parent = JERBOA_SCRATCH_PARENT
    with tempfile.TemporaryDirectory(prefix="codex-code-mode-appserver-", dir=temp_parent) as temporary:
        root = Path(temporary)
        home = root / "home"
        home.mkdir()
        workspace = root / "workspace"
        workspace.mkdir()
        expected_kind: str
        expected_marker: str
        jerboa_tools_to_enable: list[str] | None = None
        jerboa_mcp_environment: dict[str, str] | None = None
        all_cases: list[dict[str, Any]] | None = None
        provider_server: http.server.ThreadingHTTPServer | None = None
        provider_thread: threading.Thread | None = None
        if args.case == "host-smoke":
            code = 'text("CODE_MODE_HOST_OK");'
            expected_kind, expected_marker = "hostSmoke", "CODE_MODE_HOST_OK"
        elif args.case == "tool-catalog":
            required = json.dumps(sorted(EXPECTED_NESTED_TOOLS), separators=(",", ":"))
            code = (
                'const names = ALL_TOOLS.map((tool) => tool.name).sort(); const unique = new Set(names); '
                f'const required = new Set({required}); '
                'const missing = [...required].filter((name) => !names.includes(name)); '
                'const unexpected = [...unique].filter((name) => !required.has(name)); '
                'if (missing.length || unexpected.length || names.length !== unique.size) throw new Error("nested tool catalog differs: missing=" + missing.join(",") + " unexpected=" + unexpected.join(",")); '
                'text("CODE_MODE_NESTED_CATALOG_OK:" + names.join(","));'
            )
            expected_kind, expected_marker = "toolCatalog", "CODE_MODE_NESTED_CATALOG_OK"
        elif args.case == "agent-schema":
            names = sorted(name for name in EXPECTED_NESTED_TOOLS if name.startswith("multi_agent_v1__"))
            code = (
                f'const required = new Set({json.dumps(names)}); '
                'const found = ALL_TOOLS.filter(x => required.has(x.name)).map(x => ({name:x.name,description:x.description,inputSchema:x.inputSchema,parameters:x.parameters})); '
                'if (found.length !== required.size) throw new Error("agent tool schemas missing: " + JSON.stringify(found)); '
                'text("AGENT_TOOL_SCHEMAS:" + JSON.stringify(found));'
            )
            expected_kind, expected_marker = "agentSchema", "AGENT_TOOL_SCHEMAS:"
        elif args.case == "agent-lifecycle":
            code = (
                'const get = (suffix) => { const hit = ALL_TOOLS.filter(x => x.name.endsWith(suffix)); '
                'if (hit.length !== 1) throw new Error("agent tool is not unique: " + suffix); return tools[hit[0].name]; }; '
                'let stage = "spawn"; let id = null; try { '
                'const spawned = await get("multi_agent_v1__spawn_agent")({fork_context:false,model:"gpt-5.6-luna",message:"This is a disposable Codex tool test. Do not use tools, access files, or contact anyone. Reply exactly AGENT_FIXTURE_READY and finish."}); '
                'if (!spawned || typeof spawned.agent_id !== "string" || !spawned.agent_id) throw new Error("spawn_agent returned no owned agent id"); id = spawned.agent_id; '
                'stage = "wait-first"; const waited = await get("multi_agent_v1__wait_agent")({targets:[id],timeout_ms:120000}); '
                'const first = waited && waited.status && waited.status[id]; if (!first || typeof first !== "object" || !("completed" in first)) throw new Error("wait_agent did not observe owned agent completion: " + JSON.stringify(waited)); '
                'stage = "send-before-close"; '
                'const sentBeforeClose = await get("multi_agent_v1__send_input")({target:id,message:"Reply exactly AGENT_FIXTURE_BEFORE_CLOSE and finish."}); '
                'if (!sentBeforeClose || typeof sentBeforeClose.submission_id !== "string" || !sentBeforeClose.submission_id) throw new Error("send_input omitted submission id before close"); '
                'stage = "wait-before-close"; '
                'const waitedBeforeClose = await get("multi_agent_v1__wait_agent")({targets:[id],timeout_ms:120000}); const beforeClose = waitedBeforeClose && waitedBeforeClose.status && waitedBeforeClose.status[id]; '
                'if (!beforeClose || typeof beforeClose !== "object" || !("completed" in beforeClose)) throw new Error("wait_agent did not observe pre-close send_input completion: " + JSON.stringify(waitedBeforeClose)); '
                'stage = "close-first"; '
                'const closed = await get("multi_agent_v1__close_agent")({target:id}); if (!closed || !closed.previous_status) throw new Error("close_agent omitted prior status"); '
                'stage = "resume"; '
                'const resumed = await get("multi_agent_v1__resume_agent")({id}); if (!resumed || !resumed.status || resumed.status === "not_found") throw new Error("resume_agent failed: " + JSON.stringify(resumed)); '
                'stage = "send-input"; '
                'const sent = await get("multi_agent_v1__send_input")({target:id,message:"Reply exactly AGENT_FIXTURE_RESUMED and finish."}); '
                'if (!sent || typeof sent.submission_id !== "string" || !sent.submission_id) throw new Error("send_input omitted submission id"); '
                'stage = "wait-second"; '
                'const waitedAgain = await get("multi_agent_v1__wait_agent")({targets:[id],timeout_ms:120000}); const second = waitedAgain && waitedAgain.status && waitedAgain.status[id]; '
                'if (!second || typeof second !== "object" || !("completed" in second)) throw new Error("wait_agent did not observe resumed completion: " + JSON.stringify(waitedAgain)); '
                'stage = "close-second"; '
                'const closedAgain = await get("multi_agent_v1__close_agent")({target:id}); if (!closedAgain || !closedAgain.previous_status) throw new Error("final close_agent omitted prior status"); '
                'stage = "complete"; text("AGENT_TOOL_LIFECYCLE_OK:" + JSON.stringify({agent_id_present:true,first_status:first,before_close_status:beforeClose,closed_previous_status:closed.previous_status,resumed_status:resumed.status,submission_id_present:true,second_status:second,final_close_previous_status:closedAgain.previous_status})); '
                '} catch (error) { throw new Error("agent lifecycle stage " + stage + ": " + String(error)); }'
            )
            expected_kind, expected_marker = "agentLifecycle", "AGENT_TOOL_LIFECYCLE_OK"
        elif args.case == "image":
            image_path = workspace / "tiny-red.png"
            image_path.write_bytes(tiny_test_png())
            code = (
                f'const r = await tools.view_image({{path: {json.dumps(str(image_path))}, detail: "original"}}); '
                'if (!r || typeof r.image_url !== "string") throw new Error("view_image omitted image_url"); '
                'const prefix = r.image_url.startsWith("data:image/png;base64,") ? "data:image/png;base64," : '
                '(r.image_url.startsWith("data:application/octet-stream;base64,") ? "data:application/octet-stream;base64," : null); '
                'if (!prefix) throw new Error("view_image returned unexpected data URL MIME: " + r.image_url.split(",",1)[0]); '
                'const payload = r.image_url.slice(prefix.length); '
                'if (!payload.startsWith("iVBORw0KGgo") || !/^[A-Za-z0-9+/=]+$/.test(payload) || payload.length % 4 !== 0) '
                'throw new Error("view_image returned malformed PNG base64 signature/length"); '
                'const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"; '
                'const bytes = []; let acc = 0, bits = 0; '
                'for (const ch of payload) { if (ch === "=") break; const n = alphabet.indexOf(ch); '
                'if (n < 0) throw new Error("invalid base64 character"); acc = (acc << 6) | n; bits += 6; '
                'if (bits >= 8) { bits -= 8; bytes.push((acc >> bits) & 255); acc &= (1 << bits) - 1; } } '
                'const sig = [137,80,78,71,13,10,26,10]; '
                'if (sig.some((b,i) => bytes[i] !== b) || bytes[12] !== 73 || bytes[13] !== 72 || bytes[14] !== 68 || bytes[15] !== 82) '
                'throw new Error("decoded image is not a PNG with an IHDR chunk"); '
                'const width = bytes[16]*16777216 + bytes[17]*65536 + bytes[18]*256 + bytes[19]; '
                'const height = bytes[20]*16777216 + bytes[21]*65536 + bytes[22]*256 + bytes[23]; '
                'const end = [0,0,0,0,73,69,78,68,174,66,96,130]; '
                'if (width !== 1 || height !== 1 || bytes.length < 45 || end.some((b,i) => bytes[bytes.length-12+i] !== b)) '
                'throw new Error("decoded PNG dimensions or terminal IEND chunk do not match fixture"); '
                'text("VIEW_IMAGE_PNG_BASE64_OK:" + JSON.stringify({data_url_header:prefix.slice(0,-1),detected_format:"PNG",decoded_bytes:bytes.length,width,height,terminal_chunk:"IEND"}));'
            )
            expected_kind, expected_marker = "imageView", "VIEW_IMAGE_PNG_BASE64_OK"
        elif args.case in {"resources", "templates", "resource-read"}:
            calls = {
                "resources": ('list_mcp_resources({server:"codex_fixture"})', "codex-fixture-note"),
                "templates": ('list_mcp_resource_templates({server:"codex_fixture"})', "codex-fixture-item"),
                "resource-read": ('read_mcp_resource({server:"codex_fixture",uri:"fixture://codex/resource"})', "FIXTURE_RESOURCE_CONTENT_OK"),
            }
            expression, expected_marker = calls[args.case]
            code = (
                f'const r = await tools.{expression}; const s = JSON.stringify(r); '
                f'if (!s.includes({json.dumps(expected_marker)})) throw new Error("fixture result missing"); '
                f'text({json.dumps("NESTED_TOOL_RESULT_OK:") } + s);'
            )
            expected_kind = "mcpToolCall"
        elif args.case == "goals":
            code = (
                'const g = await tools.get_goal({}); '
                'if (g && g.goal && g.goal.status === "complete") throw new Error("isolated home was not empty"); '
                'const c = await tools.create_goal({objective:"Verify Code Mode goal tool dispatch in a disposable fixture"}); '
                'const u = await tools.update_goal({status:"complete"}); '
                'const f = await tools.get_goal({}); '
                'if (!f || !f.goal || f.goal.status !== "complete") throw new Error("goal lifecycle did not complete: " + JSON.stringify({initial:g,created:c,updated:u,final:f})); '
                'text("GOAL_LIFECYCLE_COMPLETE:" + JSON.stringify({created:c,updated:u,final:f}));'
            )
            expected_kind, expected_marker = "goalLifecycle", "GOAL_LIFECYCLE_COMPLETE"
        elif args.case == "apply-patch":
            patch_path = workspace / "patch-marker.txt"
            patch = (
                "*** Begin Patch\n"
                "*** Add File: patch-marker.txt\n"
                "+CODEMODE_PATCH_APPLIED_OK\n"
                "*** End Patch\n"
            )
            code = (
                f'const r = await tools.apply_patch({json.dumps(patch)}); '
                'if (r === null || r === undefined || (r && (r.isError === true || r.is_error === true))) '
                'throw new Error("apply_patch returned no successful result: " + JSON.stringify(r)); '
                'text("APPLY_PATCH_CALL_RESULT:" + JSON.stringify(r));'
            )
            expected_kind, expected_marker = "applyPatchCall", "APPLY_PATCH_CALL_RESULT:"
        elif args.case == "workspace-write-fail-closed":
            marker_path = workspace / "workspace-write-marker.txt"
            code = (
                'let result = null, thrown = null; try { '
                f'result = await tools.exec_command({{cmd:{json.dumps("printf CODEMODE_WORKSPACE_WRITE_UNEXPECTED_OK > " + str(marker_path))}}}); '
                '} catch (error) { thrown = String(error); } '
                'const evidence = JSON.stringify({result,thrown}); '
                'if (!evidence.includes("filesystem sandbox cannot be enforced on this host")) '
                'throw new Error("workspace-write did not fail closed with the platform diagnostic: " + evidence); '
                'text("WORKSPACE_WRITE_FAIL_CLOSED_RESULT:" + evidence);'
            )
            expected_kind, expected_marker = "workspaceWriteFailClosed", "WORKSPACE_WRITE_FAIL_CLOSED_RESULT:"
        elif args.case == "jerboa-read":
            jerboa_tools_to_enable = ["jerboa_list_features"]
            feature_path = root / "data" / "features.sexp"
            code = (
                'const hit = ALL_TOOLS.find(x => x.name.endsWith("__jerboa_list_features")); '
                'if (!hit) throw new Error("enabled Jerboa list_features tool is absent"); '
                f'const r = await tools[hit.name]({{query:"{root.name}", features_path:{json.dumps(str(feature_path))}, limit:3}}); '
                'text("JERBOA_LIST_FEATURES_RESULT:" + JSON.stringify(r));'
            )
            expected_kind, expected_marker = "mcpToolCall", "JERBOA_LIST_FEATURES_RESULT"
        elif args.case == "jerboa-matrix-probe":
            jerboa_tools_to_enable = ["jerboa_cross_repo_release_evidence_matrix"]
            matrix_root = root / "matrix-workspace"
            matrix_root.mkdir()
            code = (
                'const hit = ALL_TOOLS.filter(x => x.name.endsWith("__jerboa_cross_repo_release_evidence_matrix")); '
                'if (hit.length !== 1) throw new Error("matrix tool schema is not unique"); '
                f'const r = await tools[hit[0].name]({{workspace_path:{json.dumps(str(matrix_root))},max_repos:1}}); '
                'text("MATRIX_MISSING_TARGET_RESULT:" + JSON.stringify(r));'
            )
            expected_kind, expected_marker = "jerboaMatrixProbe", "MATRIX_MISSING_TARGET_RESULT"
        elif args.case == "openai-docs":
            remote_cases = [
                {"name": "list_openai_docs", "arguments": {"limit": 3}, "required_text": "https://"},
                {"name": "search_openai_docs", "arguments": {"query": "Responses API", "limit": 2}, "required_text": "responses"},
                {"name": "fetch_openai_doc", "arguments": {"url": "https://developers.openai.com/api/docs/guides/tools-connectors-mcp"}, "required_text": "# MCP servers"},
                {"name": "list_api_endpoints", "arguments": {}, "required_text": "/responses"},
                {"name": "get_openapi_spec", "arguments": {"url": "https://api.openai.com/v1/responses", "codeExamplesOnly": True}, "required_text": "Create a model response"},
            ]
            remote_json = json.dumps(remote_cases, separators=(",", ":"))
            code = (
                f'const cases = {remote_json}; const summary = []; '
                'const pick = (name) => { const hits = ALL_TOOLS.filter(x => x.name.endsWith("__" + name)); '
                'if (hits.length !== 1) throw new Error("expected one OpenAI docs tool " + name + ", found " + hits.length); return hits[0]; }; '
                'for (const c of cases) { const spec = pick(c.name); const r = await tools[spec.name](c.arguments); const s = JSON.stringify(r); '
                'const err = !!(r && (r.is_error === true || r.isError === true)); '
                'if (err) throw new Error(c.name + " returned an error: " + s.slice(0,600)); '
                'if (!s.includes(c.required_text)) throw new Error(c.name + " result omitted " + c.required_text + ": " + s.slice(0,600)); '
                'summary.push({name:c.name,ok:!err,has_result:!!r,has_required_text:s.includes(c.required_text)}); } '
                'text("OPENAI_DOCS_ALL_TOOLS_OK:" + JSON.stringify(summary));'
            )
            expected_kind, expected_marker = "openaiDocsCalls", "OPENAI_DOCS_ALL_TOOLS_OK"
        else:
            jerboa_tools_to_enable = [
                "jerboa_howto_add", "jerboa_howto_get",
                "jerboa_anti_pattern_add", "jerboa_anti_pattern_lookup",
                "jerboa_error_fix_add", "jerboa_error_fix_lookup",
                "jerboa_suggest_feature", "jerboa_list_features",
                "jerboa_vote_feature", "jerboa_security_pattern_add",
            ]
            paths = {
                "cookbook": root / "data" / "cookbooks.sexp",
                "anti": root / "data" / "anti-patterns.sexp",
                "errors": root / "data" / "error-fixes.sexp",
                "features": root / "data" / "features.sexp",
                "rules": root / "data" / "security-rules.sexp",
            }
            args_json = json.dumps({key: str(path) for key, path in paths.items()}, separators=(",", ":"))
            code = (
                'const pick = (suffix) => { const hits = ALL_TOOLS.filter(x => x.name.endsWith("__" + suffix)); '
                'if (hits.length !== 1) throw new Error("expected one enabled Jerboa tool " + suffix + ", found " + hits.length); '
                'return tools[hits[0].name]; }; '
                f'const p = {args_json}; const tag = "codex-freebsd-native-scratch"; '
                'await pick("jerboa_howto_add")({id:tag+"-recipe",title:"Native disposable recipe",code:"(+ 1 2)",tags:["scratch"],notes:"Temporary test only.",cookbook_path:p.cookbook}); '
                'const howto = await pick("jerboa_howto_get")({id:tag+"-recipe",cookbook_path:p.cookbook}); '
                'await pick("jerboa_anti_pattern_add")({id:tag+"-anti",title:"Native disposable anti-pattern",avoid:"Outside fixture",advice:"Use fixture path",pattern:"scratch-native-test",anti_patterns_path:p.anti}); '
                'const anti = await pick("jerboa_anti_pattern_lookup")({query:tag+"-anti",anti_patterns_path:p.anti}); '
                'await pick("jerboa_error_fix_add")({id:tag+"-error",pattern:tag+"-pattern",fix:"Use fixture",explanation:"Temporary test only.",error_fixes_path:p.errors}); '
                'const error = await pick("jerboa_error_fix_lookup")({error_message:tag+"-pattern",error_fixes_path:p.errors}); '
                'await pick("jerboa_suggest_feature")({id:tag+"-feature",title:"Native disposable feature",description:"Scratch-only.",use_case:"Validate persistence.",example_scenario:"Run test.",status:"proposed",impact:"low",features_path:p.features}); '
                'const featureBefore = await pick("jerboa_list_features")({query:tag+"-feature",features_path:p.features,limit:3}); '
                'await pick("jerboa_vote_feature")({id:tag+"-feature",features_path:p.features}); '
                'const featureAfter = await pick("jerboa_list_features")({query:tag+"-feature",features_path:p.features,limit:3}); '
                'await pick("jerboa_security_pattern_add")({id:tag+"-rule",scope:"test",severity:"low",pattern:tag+"-security",message:"Temporary test rule.",rules_path:p.rules}); '
                'for (const [name,value] of [["howto",howto],["anti",anti],["error",error],["feature-before",featureBefore],["feature-after",featureAfter]]) '
                'if (!JSON.stringify(value).includes(tag)) throw new Error(name + " readback missed the fixture ID"); '
                'text("JERBOA_SCRATCH_WRITERS_OK:" + JSON.stringify({howto,anti,error,featureBefore,featureAfter}));'
            )
            expected_kind, expected_marker = "jerboaMcpCalls", "JERBOA_SCRATCH_WRITERS_OK"
        if args.case == "jerboa-all":
            fixture_root = root / "jerboa-fixture"
            mcp_fixture_env, project, data = prepare_fixture(fixture_root)
            provider_server, provider_thread, provider_endpoint = start_completion_server()
            nonwriter_cases = build_nonwriter_cases(project, data, fixture_root, provider_endpoint)
            # The missing-target matrix case is a valid MCP tool error now:
            # require its normal result payload and `isError` status rather
            # than accepting an adapter/transport exception.
            writer_prefix = "codex-freebsd-native-scratch"
            writer_cases = [
                {"name": "jerboa_howto_add", "arguments": {"id": "codex-freebsd-tool-positive-recipe", "title": "Positive lookup fixture", "code": "(+ 1 2)", "tags": ["scratch"], "notes": "Temporary positive lookup test.", "cookbook_path": str(data / "cookbooks.sexp")}, "expected_is_error": False, "required_text": None},
                {"name": "jerboa_howto_get", "arguments": {"id": "codex-freebsd-tool-positive-recipe", "cookbook_path": str(data / "cookbooks.sexp")}, "expected_is_error": False, "required_text": "codex-freebsd-tool-positive-recipe"},
                {"name": "jerboa_anti_pattern_add", "arguments": {"id": writer_prefix + "-anti", "title": "Native disposable anti-pattern", "avoid": "Outside fixture", "advice": "Use fixture path", "pattern": "scratch-native-test", "anti_patterns_path": str(data / "anti-patterns.sexp")}, "expected_is_error": False, "required_text": None},
                {"name": "jerboa_anti_pattern_lookup", "arguments": {"query": writer_prefix + "-anti", "anti_patterns_path": str(data / "anti-patterns.sexp")}, "expected_is_error": False, "required_text": writer_prefix + "-anti"},
                {"name": "jerboa_error_fix_add", "arguments": {"id": writer_prefix + "-error", "pattern": writer_prefix + "-pattern", "fix": "Use fixture", "explanation": "Temporary test only.", "error_fixes_path": str(data / "error-fixes.sexp")}, "expected_is_error": False, "required_text": None},
                {"name": "jerboa_error_fix_lookup", "arguments": {"error_message": writer_prefix + "-pattern", "error_fixes_path": str(data / "error-fixes.sexp")}, "expected_is_error": False, "required_text": writer_prefix + "-error"},
                {"name": "jerboa_suggest_feature", "arguments": {"id": writer_prefix + "-feature", "title": "Native disposable feature", "description": "Scratch-only.", "use_case": "Validate persistence.", "example_scenario": "Run test.", "status": "proposed", "impact": "low", "features_path": str(data / "features.sexp")}, "expected_is_error": False, "required_text": None},
                {"name": "jerboa_list_features", "arguments": {"query": writer_prefix + "-feature", "features_path": str(data / "features.sexp"), "limit": 3}, "expected_is_error": False, "required_text": writer_prefix + "-feature"},
                {"name": "jerboa_vote_feature", "arguments": {"id": writer_prefix + "-feature", "features_path": str(data / "features.sexp")}, "expected_is_error": False, "required_text": writer_prefix + "-feature"},
                {"name": "jerboa_list_features", "arguments": {"query": writer_prefix + "-feature", "features_path": str(data / "features.sexp"), "limit": 3}, "expected_is_error": False, "required_text": writer_prefix + "-feature"},
                {"name": "jerboa_security_pattern_add", "arguments": {"id": writer_prefix + "-rule", "scope": "test", "severity": "low", "pattern": writer_prefix + "-security", "message": "Temporary test rule.", "rules_path": str(data / "security-rules.sexp")}, "expected_is_error": False, "required_text": None},
            ]
            all_cases = writer_cases + nonwriter_cases
            tool_names = [case["name"] for case in all_cases]
            if len(set(tool_names)) != 51:
                raise AssertionError(f"scratch case manifest does not cover 51 unique tools: {len(set(tool_names))} unique across {len(tool_names)} calls")
            schema_probe = McpClient(str(JERBOA_SERVER), mcp_fixture_env)
            try:
                provider_tool_names = set(schema_probe.tools)
            finally:
                schema_probe.close()
            if provider_tool_names != set(tool_names):
                raise AssertionError(
                    "case manifest and native Jerboa tools/list differ: "
                    f"missing={sorted(provider_tool_names - set(tool_names))}, "
                    f"unexpected={sorted(set(tool_names) - provider_tool_names)}"
                )
            jerboa_tools_to_enable = sorted(set(tool_names))
            jerboa_environment_keys = (
                "HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "TMPDIR", "JERBOA_HOME",
                "JERBOA_MCP_REPO", "JERBOA_MCP_DATA_DIR", "JERBOA_MCP_DATA_REPO_DIR",
                "JERBOA_MCP_CACHE_HOME", "JERBOA_MCP_DATA_OFFLINE", "JERBOA_MCP_LOG",
                "JERBOA_MCP_LOG_FILE", "JERBOA_MCP_JERBOA_PATH",
            )
            jerboa_mcp_environment = {key: mcp_fixture_env[key] for key in jerboa_environment_keys}
            cases_json = json.dumps(all_cases, separators=(",", ":"))
            names_json = json.dumps(sorted(set(tool_names)), separators=(",", ":"))
            code = (
                '// @exec: {"yield_time_ms": 30000, "max_output_tokens": 3000}\n'
                f'const cases = {cases_json}; const expected = new Set({names_json}); '
                'const find = (name) => { const hits = ALL_TOOLS.filter(x => name === "jerboa" ? x.name.endsWith("__jerboa") : x.name.endsWith("__" + name)); '
                'if (hits.length !== 1) throw new Error("expected one enabled MCP tool " + name + ", found " + hits.length); return hits[0]; }; '
                'const mismatched = [...expected].filter(name => ALL_TOOLS.filter(x => x.name.endsWith("__" + name)).length !== 1); '
                'if (mismatched.length) throw new Error("Code Mode schema does not expose each tools/list method exactly once: " + mismatched.join(",")); '
                'const summary = []; for (const c of cases) { const spec = find(c.name); let r = null, thrown = null; '
                'try { r = await tools[spec.name](c.arguments); } catch (e) { thrown = String(e); } '
                'const s = JSON.stringify(r); const evidence = thrown === null ? s : thrown; '
                'const err = thrown !== null || !!(r && (r.is_error === true || r.isError === true)); '
                'if (c.expected_adapter_error) { const adapterFailure = (thrown && thrown.includes(c.expected_adapter_error)) || (!!r && (r.isError === true || r.is_error === true) && s.includes(c.expected_adapter_error)); '
                'if (!adapterFailure) throw new Error(c.name + " did not produce expected adapter error " + c.expected_adapter_error + ": " + evidence.slice(0,600)); } '
                'else { if (err !== c.expected_is_error) throw new Error(c.name + " is_error=" + err + " expected=" + c.expected_is_error + ": " + evidence.slice(0,600)); '
                'if (c.required_text && !evidence.includes(c.required_text)) throw new Error(c.name + " result omitted required marker " + c.required_text + ": " + evidence.slice(0,600)); } '
                'summary.push({name:c.name,is_error:err,thrown:thrown!==null,adapter_error:!!c.expected_adapter_error,has_result:!!r||thrown!==null,has_required_text:!!c.expected_adapter_error||!c.required_text||evidence.includes(c.required_text)}); } '
                'text("JERBOA_ALL_TOOLS_OK:" + JSON.stringify(summary));'
            )
            expected_kind, expected_marker = "jerboaAllCalls", "JERBOA_ALL_TOOLS_OK"

        capture = Capture(code)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(capture))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        process: subprocess.Popen[str] | None = None
        try:
            sandbox_config = (
                'sandbox_mode = "danger-full-access"\n'
                if args.case == "apply-patch"
                else 'sandbox_mode = "workspace-write"\n'
                if args.case == "workspace-write-fail-closed"
                else ""
            )
            config = (
                f'model = "{MODEL}"\nmodel_provider = "fixture"\napproval_policy = "never"\n{sandbox_config}'
                '[model_providers.fixture]\nname = "Local Code Mode fixture"\n'
                f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
                'wire_api = "responses"\nenv_key = "OPENAI_API_KEY"\n'
                '[features]\ncode_mode_only = true\n'
                '[features.code_mode]\nenabled = true\n'
                '[features.code_mode_host]\nenabled = true\n'
                '[plugins._default]\nenabled = false\n'
            )
            if args.case in {"resources", "templates", "resource-read"}:
                config += (
                    '\n[mcp_servers.codex_fixture]\ncommand = "python3"\n'
                    f'args = [{json.dumps(str(MCP_SERVER))}]\n'
                    f'[mcp_servers.codex_fixture.env]\nCODEX_FIXTURE_ROOT = {json.dumps(str(workspace))}\n'
                )
            if args.case.startswith("jerboa-"):
                if args.case == "jerboa-all":
                    assert jerboa_mcp_environment is not None and jerboa_tools_to_enable is not None
                    mcp_environment = jerboa_mcp_environment
                else:
                    for name in ("data", "data-repo", "jerboa-cache", "xdg-cache", "xdg-config", "tmp"):
                        (root / name).mkdir(mode=0o700)
                    mcp_environment = {
                        "HOME": str(home),
                        "XDG_CACHE_HOME": str(root / "xdg-cache"),
                        "XDG_CONFIG_HOME": str(root / "xdg-config"),
                        "TMPDIR": str(root / "tmp"),
                        "JERBOA_HOME": str(JERBOA_HOME),
                        "JERBOA_MCP_REPO": str(workspace),
                        "JERBOA_MCP_DATA_DIR": str(root / "data"),
                        "JERBOA_MCP_DATA_REPO_DIR": str(root / "data-repo"),
                        "JERBOA_MCP_CACHE_HOME": str(root / "jerboa-cache"),
                        "JERBOA_MCP_DATA_OFFLINE": "1",
                        "JERBOA_MCP_LOG": "0",
                        "JERBOA_MCP_LOG_FILE": str(root / "jmcp.log"),
                        "JERBOA_MCP_JERBOA_PATH": str(JERBOA_BIN),
                    }
                    assert jerboa_tools_to_enable is not None
                config += (
                    '\n[mcp_servers.jerboa]\n'
                    f'command = {json.dumps(str(JERBOA_SERVER.absolute()))}\n'
                    'default_tools_approval_mode = "approve"\n'
                    f'enabled_tools = {json.dumps(jerboa_tools_to_enable)}\n'
                    '[mcp_servers.jerboa.env]\n'
                    + "".join(f'{key} = {json.dumps(value)}\n' for key, value in mcp_environment.items())
                )
                if args.case == "jerboa-read":
                    config += (
                        '[mcp_servers.jerboa.tools.jerboa_list_features]\n'
                        'approval_mode = "approve"\n'
                    )
            if args.case == "openai-docs":
                config += (
                    '\n[mcp_servers.openaiDeveloperDocs]\n'
                    'url = "https://developers.openai.com/mcp"\n'
                    'default_tools_approval_mode = "approve"\n'
                    'enabled_tools = ["search_openai_docs", "list_openai_docs", "fetch_openai_doc", "list_api_endpoints", "get_openapi_spec"]\n'
                )
            missing_provider = root / "absent-provider" / "codex-freebsd-missing-mcp-provider"
            if args.case == "mcp-missing-provider":
                config += (
                    '\n[mcp_servers.codex_missing_provider_probe]\n'
                    f'command = {json.dumps(str(missing_provider))}\n'
                    'startup_timeout_sec = 2\n'
                )
            (home / "config.toml").write_text(config, encoding="utf-8")
            environment = os.environ.copy()
            environment["CODEX_HOME"] = str(home)
            isolated_tmp = root / "tmp"
            isolated_tmp.mkdir(exist_ok=True)
            environment["TMPDIR"] = str(isolated_tmp)
            environment["XDG_CACHE_HOME"] = str(root / "xdg-cache")
            environment["XDG_CONFIG_HOME"] = str(root / "xdg-config")
            Path(environment["XDG_CACHE_HOME"]).mkdir()
            Path(environment["XDG_CONFIG_HOME"]).mkdir()
            environment["OPENAI_API_KEY"] = API_KEY
            environment.pop("OPENAI_BASE_URL", None)
            native_path_bin = root / "native-path-bin"
            native_path_bin.mkdir()
            native_rg = Path("/usr/local/bin/rg")
            if not native_rg.is_file():
                raise RuntimeError(f"FreeBSD-native ripgrep executable is unavailable: {native_rg}")
            os.symlink(os.path.abspath(args.codex), native_path_bin / "codex")
            os.symlink(native_rg, native_path_bin / "rg")
            environment["PATH"] = os.pathsep.join(map(str, (
                native_path_bin, Path("/usr/local/bin"), Path("/bin"), Path("/usr/bin"),
                Path("/usr/sbin"), Path("/sbin"), Path.home() / ".local/bin",
            )))
            resolutions = subprocess.run(
                ["/bin/sh", "-c", "command -v codex; command -v rg; command -v sh"],
                check=True, capture_output=True, text=True, env=environment,
            ).stdout.splitlines()
            expected_resolutions = [str(native_path_bin / "codex"), str(native_path_bin / "rg"), "/bin/sh"]
            if resolutions != expected_resolutions:
                raise RuntimeError(f"native test PATH resolved unexpected executables: {resolutions!r}")
            for executable in (args.codex, native_rg, JERBOA_SERVER):
                description = subprocess.run(
                    ["/usr/bin/file", "-L", "-b", str(executable)],
                    check=True, capture_output=True, text=True,
                ).stdout
                if "FreeBSD" not in description:
                    raise RuntimeError(f"native test executable is not a FreeBSD binary: {executable}: {description.strip()}")
            print(f"NATIVE_TEST_PATH_RESOLUTION: {'; '.join(resolutions)}; FreeBSD ELF CLI, rg, and Jerboa MCP")
            process = subprocess.Popen(
                [
                    str(args.codex), "app-server", "--enable", "code_mode",
                    "--enable", "code_mode_only", "--enable", "code_mode_host",
                ], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, env=environment,
            )
            assert process.stdin is not None and process.stdout is not None
            messages: queue.Queue[Any] = queue.Queue()
            threading.Thread(target=read_messages, args=(process.stdout, messages), daemon=True).start()
            request_id = 0

            def send(method: str, params: dict[str, Any] | None = None) -> int:
                nonlocal request_id
                request_id += 1
                request = {"jsonrpc": "2.0", "id": request_id, "method": method}
                if params is not None:
                    request["params"] = params
                process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
                process.stdin.flush()
                return request_id

            def await_response(target: int) -> dict[str, Any]:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        message = messages.get(timeout=min(1.0, max(0.01, deadline - time.monotonic())))
                    except queue.Empty:
                        continue
                    if isinstance(message, BaseException):
                        raise message
                    if message.get("id") == target and ("result" in message or "error" in message):
                        if "error" in message:
                            raise RuntimeError(str(message["error"]))
                        return message
                raise TimeoutError(f"timed out waiting for app-server response {target}")

            await_response(send("initialize", {
                "clientInfo": {"name": "codex-freebsd-code-mode-fixture", "version": "1"},
                "capabilities": {"experimentalApi": True},
            }))
            send("initialized", {})
            thread_id = await_response(send("thread/start", {
                "model": MODEL, "cwd": str(workspace),
                # Goal APIs intentionally reject ephemeral threads; this case
                # persists only inside the disposable CODEX_HOME below.
                # Goals and agent close/resume probes need durable thread history.
                "ephemeral": args.case not in {"goals", "agent-lifecycle"},
            }))['result']['thread']['id']
            if args.case == "mcp-missing-provider":
                response = await_response(send("mcpServerStatus/list", {
                    "serverName": "codex_missing_provider_probe",
                    "detail": "toolsAndAuthOnly",
                }))
                statuses = response.get("result", {}).get("data", [])
                matching = [item for item in statuses if item.get("name") == "codex_missing_provider_probe"]
                if len(matching) != 1:
                    raise AssertionError(f"Codex returned no unique status for the missing fixture provider: {statuses!r}")
                diagnostic = matching[0].get("toolsError") or ""
                if "MCP startup failed" not in diagnostic or "No such file or directory" not in diagnostic or "os error 2" not in diagnostic:
                    raise AssertionError(f"Codex did not return the expected missing-provider ENOENT diagnostic: {diagnostic!r}")
                print(f"PASS native Codex missing-provider diagnostic: {diagnostic}")
                return 0
            turn = await_response(send("turn/start", {
                "threadId": thread_id,
                "input": [{"type": "text", "text": f"Run the fixed {args.case} fixture probe.", "textElements": []}],
                "model": MODEL,
            }))['result']['turn']['id']

            observed: list[dict[str, Any]] = []
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    message = messages.get(timeout=min(1.0, max(0.01, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if isinstance(message, BaseException):
                    raise message
                if message.get("method", "").startswith("item/"):
                    observed.append(message)
                if message.get("method") == "turn/completed" and message.get("params", {}).get("turn", {}).get("id") == turn:
                    validate_completed_turn(message, turn)
                    break
                if message.get("method") == "error":
                    raise RuntimeError("app-server emitted an error notification")
            else:
                raise TimeoutError("turn did not complete within the fixture timeout")

            with capture.lock:
                requests = list(capture.requests)
                provider_error = capture.error
            if provider_error:
                raise RuntimeError(provider_error)
            minimum_requests = 3 if args.case == "agent-lifecycle" else 2
            if len(requests) < minimum_requests:
                raise AssertionError(f"expected at least {minimum_requests} model requests, saw {len(requests)}")
            advertised = tool_schema_names(requests[0])
            if "exec" not in advertised:
                raise AssertionError(f"Code Mode exec was not present in the actual request tool schema; advertised names: {advertised}")
            all_model_inputs = [item for request in requests for item in request.get("input", [])]
            input_dump = json.dumps(all_model_inputs, sort_keys=True)
            returned_calls = [
                item for item in requests[1].get("input", [])
                if isinstance(item, dict)
                and item.get("type") == "custom_tool_call_output"
                and item.get("call_id") == "call-code-mode-fixture"
            ]
            if args.case == "agent-lifecycle":
                returned_calls = [
                    item for item in all_model_inputs
                    if isinstance(item, dict)
                    and item.get("type") == "custom_tool_call_output"
                    and item.get("call_id") == "call-code-mode-fixture"
                ]
            if expected_kind == "mcpToolCall":
                expected_tool = {"resources": "list_mcp_resources", "templates": "list_mcp_resource_templates", "resource-read": "read_mcp_resource", "jerboa-read": "jerboa_list_features"}[args.case]
                expected_name = {"resources": "codex-fixture-note", "templates": "codex-fixture-item", "resource-read": "FIXTURE_RESOURCE_CONTENT_OK", "jerboa-read": root.name}[args.case]
                expected_server = "jerboa" if args.case == "jerboa-read" else "codex_fixture"
                if not any(
                    event.get("params", {}).get("item", {}).get("type") == expected_kind
                    and event.get("params", {}).get("item", {}).get("status") == "completed"
                    and event.get("params", {}).get("item", {}).get("server") == expected_server
                    and event.get("params", {}).get("item", {}).get("tool") == expected_tool
                    and expected_name in json.dumps(event.get("params", {}).get("item", {}))
                    for event in observed
                ):
                    summaries = []
                    for event in observed:
                        item = event.get("params", {}).get("item", {})
                        if item.get("type") == "mcpToolCall":
                            summaries.append({key: item.get(key) for key in ("server", "tool", "status", "error", "result")})
                    raise AssertionError(
                        f"no raw app-server {expected_tool} item carried its exact fixture result; "
                        f"observed MCP item summaries: {json.dumps(summaries, sort_keys=True)[:2400]}"
                    )
            if expected_kind == "jerboaMatrixProbe":
                matching = [
                    event.get("params", {}).get("item", {}) for event in observed
                    if event.get("params", {}).get("item", {}).get("type") == "mcpToolCall"
                    and event.get("params", {}).get("item", {}).get("server") == "jerboa"
                    and event.get("params", {}).get("item", {}).get("tool") == "jerboa_cross_repo_release_evidence_matrix"
                ]
                if len(matching) != 1:
                    summaries = [
                        {key: item.get(key) for key in ("type", "server", "tool", "status", "error", "arguments", "result")}
                        for event in observed
                        for item in [event.get("params", {}).get("item", {})]
                    ]
                    raise AssertionError(
                        f"expected exactly one raw release-matrix MCP event, saw {len(matching)}; "
                        f"all observed items: {json.dumps(summaries, sort_keys=True)[:5000]}; "
                        f"next model input: {json.dumps(requests[1].get('input', []), sort_keys=True)[:3000]}"
                    )
                item = matching[0]
                expected_args = {"workspace_path": str(root / "matrix-workspace"), "max_repos": 1}
                if item.get("arguments") != expected_args:
                    raise AssertionError(f"release-matrix event arguments differ from scratch fixture: {item.get('arguments')!r}")
                if item.get("status") != "failed" or item.get("result") is not None:
                    raise AssertionError(f"expected the isolated provider-error reproduction, observed {item!r}")
                error = item.get("error", {}).get("message", "")
                output = returned_calls[0].get("output", "") if returned_calls else ""
                if "Unexpected response type" not in error or "Unexpected response type" not in output:
                    raise AssertionError(f"expected exact Codex MCP adapter error, event={error!r}, output={output!r}")
                print(f"REPRODUCED native MCP adapter failure: {item['server']}/{item['tool']}: {error}")
            if expected_kind == "jerboaMcpCalls":
                expected_counts = {
                    "jerboa_howto_add": 1,
                    "jerboa_howto_get": 1,
                    "jerboa_anti_pattern_add": 1,
                    "jerboa_anti_pattern_lookup": 1,
                    "jerboa_error_fix_add": 1,
                    "jerboa_error_fix_lookup": 1,
                    "jerboa_suggest_feature": 1,
                    "jerboa_list_features": 2,
                    "jerboa_vote_feature": 1,
                    "jerboa_security_pattern_add": 1,
                }
                observed_counts: dict[str, int] = {}
                for event in observed:
                    item = event.get("params", {}).get("item", {})
                    if item.get("type") == "mcpToolCall" and item.get("server") == "jerboa" and item.get("status") == "completed":
                        tool = item.get("tool")
                        observed_counts[tool] = observed_counts.get(tool, 0) + 1
                if observed_counts != expected_counts:
                    raise AssertionError(f"raw completed Jerboa MCP event counts differ; expected={expected_counts}, observed={observed_counts}")
                expected_files = ["cookbooks.sexp", "anti-patterns.sexp", "error-fixes.sexp", "features.sexp", "security-rules.sexp"]
                for filename in expected_files:
                    path = root / "data" / filename
                    if not path.is_file() or not path.resolve().is_relative_to(root):
                        raise AssertionError(f"Jerboa writer output missing or outside fixture root: {filename}")
                    content = path.read_text(encoding="utf-8")
                    if "codex-freebsd-native-scratch" not in content:
                        raise AssertionError(f"Jerboa writer file lacks its unique fixture marker: {filename}")
                    if filename == "features.sexp":
                        if "codex-freebsd-native-scratch-feature" not in content or not re.search(r'\("votes"\s+\.\s+1\)', content):
                            raise AssertionError("Jerboa vote did not persist an exact count of one in the fixture feature row")
            if expected_kind == "jerboaAllCalls":
                if all_cases is None:
                    raise AssertionError("all-tool fixture manifest was not retained")
                raw_calls = [
                    event.get("params", {}).get("item", {})
                    for event in observed
                    if event.get("params", {}).get("item", {}).get("type") == "mcpToolCall"
                    and event.get("params", {}).get("item", {}).get("server") == "jerboa"
                    and event.get("params", {}).get("item", {}).get("status") in {"completed", "failed"}
                ]
                if len(raw_calls) != len(all_cases):
                    summaries = [
                        {key: item.get(key) for key in ("tool", "status", "error")}
                        for event in observed
                        for item in [event.get("params", {}).get("item", {})]
                        if item.get("type") == "mcpToolCall" and item.get("server") == "jerboa"
                    ]
                    status_counts: dict[str, int] = {}
                    for item in summaries:
                        status = str(item.get("status"))
                        status_counts[status] = status_counts.get(status, 0) + 1
                    outputs = [call.get("output") for call in returned_calls]
                    raise AssertionError(
                        f"expected {len(all_cases)} completed/failed raw Jerboa events, saw {len(raw_calls)}; "
                        f"status counts={status_counts}; terminal names="
                        f"{[item.get('tool') for item in summaries if item.get('status') in {'completed','failed'}]}; "
                        f"last events={json.dumps(summaries[-12:], sort_keys=True)}; "
                        f"Code Mode return={json.dumps(outputs, sort_keys=True)[:2200]}"
                    )
                for case in all_cases:
                    expected_status = "failed" if case["expected_is_error"] else "completed"
                    expected_arguments = json.dumps(case["arguments"], sort_keys=True, separators=(",", ":"))
                    match_index = next((index for index, item in enumerate(raw_calls)
                        if item.get("tool") == case["name"]
                        and item.get("status") == expected_status
                        and json.dumps(item.get("arguments"), sort_keys=True, separators=(",", ":")) == expected_arguments), None)
                    if match_index is None:
                        raise AssertionError(f"no raw app-server MCP item matched {case['name']} status={expected_status} and exact arguments")
                    item = raw_calls.pop(match_index)
                    if case.get("expected_adapter_error"):
                        error = item.get("error", {}).get("message", "")
                        if item.get("result") is not None or case["expected_adapter_error"] not in error:
                            raise AssertionError(f"raw {case['name']} event did not preserve the expected provider protocol error: {item!r}")
                        continue
                    if not isinstance(item.get("result"), dict):
                        raise AssertionError(f"raw {case['name']} event omitted its MCP result payload")
                    content = item["result"].get("content")
                    structured = item["result"].get("structuredContent")
                    if not content and structured is None:
                        raise AssertionError(f"raw {case['name']} event carried no result content")
                    required_text = case.get("required_text")
                    if required_text and required_text not in json.dumps(item, sort_keys=True):
                        raise AssertionError(f"raw {case['name']} result omitted expected marker {required_text!r}")

                returned_tool_output = returned_calls[0].get("output", "") if returned_calls else ""
                summary_line = next((
                    value.split("JERBOA_ALL_TOOLS_OK:", 1)[1]
                    for value in nested_strings(returned_tool_output)
                    if "JERBOA_ALL_TOOLS_OK:" in value
                ), None)
                if summary_line is None:
                    raise AssertionError("completed Code Mode result omitted its all-tool call summary")
                summary_rows = json.loads(summary_line)
                expected_summary = [(case["name"], case["expected_is_error"]) for case in all_cases]
                actual_summary = [(row.get("name"), row.get("is_error")) for row in summary_rows]
                if actual_summary != expected_summary:
                    raise AssertionError("Code Mode summary did not match expected MCP success/error results in call order")
                if raw_calls:
                    raise AssertionError(f"unmatched raw Jerboa MCP calls remain: {len(raw_calls)}")
                for case, row in zip(all_cases, summary_rows, strict=True):
                    if row.get("has_result") is not True:
                        raise AssertionError(f"Code Mode result summary omitted the {case['name']} result")
                    if row.get("has_required_text") is not True:
                        raise AssertionError(f"Code Mode result summary omitted expected content for {case['name']}")

                adapter_errors = [row["name"] for row in summary_rows if row.get("adapter_error")]
                if adapter_errors:
                    print(f"KNOWN MCP PROVIDER PROTOCOL ERROR (not a Code Mode pass): {adapter_errors}")
                    if not args.allow_known_provider_errors:
                        raise AssertionError(f"strict all-tools gate failed on known provider protocol errors: {adapter_errors}")

                fixture_root = root / "jerboa-fixture"
                data_root = fixture_root / "data"
                for filename in ("cookbooks.sexp", "anti-patterns.sexp", "error-fixes.sexp", "features.sexp", "security-rules.sexp"):
                    path = data_root / filename
                    if not path.is_file() or not path.resolve().is_relative_to(root):
                        raise AssertionError(f"Jerboa catalog writer output missing/outside scratch: {filename}")
                feature_text = (data_root / "features.sexp").read_text(encoding="utf-8")
                if "codex-freebsd-native-scratch-feature" not in feature_text or not re.search(r'\("votes"\s+\.\s+1\)', feature_text):
                    raise AssertionError("Jerboa feature vote did not persist exact count one in the scratch catalog")
                if "codex-freebsd-tool-positive-recipe" not in (data_root / "cookbooks.sexp").read_text(encoding="utf-8"):
                    raise AssertionError("positive howto_get fixture recipe was not persisted before lookup")
                project = fixture_root / "project"
                repaired_text = (project / "repair.ss").read_text(encoding="utf-8")
                postconditions = {
                    "balanced replace": "(+ x 2)" in (project / "src" / "mutable.ss").read_text(encoding="utf-8"),
                    "balanced insert": "(define (second x) x)" in (project / "src" / "insert.ss").read_text(encoding="utf-8"),
                    "scaffold file": (project / "generated.ss").is_file(),
                    "count-args scaffold file": (project / "count-args.ss").is_file(),
                    "valid release evidence fixture": (data_root / "release-evidence-ready" / "rustsec-audit.txt").is_file(),
                    "release matrix repo fixture": (fixture_root / "release-matrix-workspace" / "jerboa-release-pass" / ".jerboa" / "security.json").is_file(),
                    "written source": (project / "written.ss").is_file(),
                    "repair balanced": repaired_text.count("(") == repaired_text.count(")"),
                    "run_tests marker": (project / "scratch-test-ran.txt").is_file(),
                    "snapshot": (fixture_root / "snapshot").is_dir(),
                }
                failed_postconditions = [name for name, passed in postconditions.items() if not passed]
                if failed_postconditions:
                    raise AssertionError(f"native Code Mode fixture postconditions failed: {failed_postconditions}")
                print(f"NATIVE_JERBOA_CODEMODE_TOOL_CALLS: {len(all_cases)} calls across {len(set(case['name'] for case in all_cases))} unique tools")
            if expected_kind == "openaiDocsCalls":
                remote_cases = [
                    {"name": "list_openai_docs", "arguments": {"limit": 3}, "required_text": "https://"},
                    {"name": "search_openai_docs", "arguments": {"query": "Responses API", "limit": 2}, "required_text": "responses"},
                    {"name": "fetch_openai_doc", "arguments": {"url": "https://developers.openai.com/api/docs/guides/tools-connectors-mcp"}, "required_text": "# MCP servers"},
                    {"name": "list_api_endpoints", "arguments": {}, "required_text": "/responses"},
                    {"name": "get_openapi_spec", "arguments": {"url": "https://api.openai.com/v1/responses", "codeExamplesOnly": True}, "required_text": "Create a model response"},
                ]
                raw_calls = [
                    event.get("params", {}).get("item", {}) for event in observed
                    if event.get("params", {}).get("item", {}).get("type") == "mcpToolCall"
                    and event.get("params", {}).get("item", {}).get("server") == "openaiDeveloperDocs"
                    and event.get("params", {}).get("item", {}).get("status") in {"completed", "failed"}
                ]
                if len(raw_calls) != len(remote_cases):
                    raise AssertionError(f"expected {len(remote_cases)} raw OpenAI docs MCP calls, saw {len(raw_calls)}")
                for case in remote_cases:
                    expected_arguments = json.dumps(case["arguments"], sort_keys=True, separators=(",", ":"))
                    match_index = next((i for i, item in enumerate(raw_calls)
                        if item.get("tool") == case["name"]
                        and item.get("status") == "completed"
                        and json.dumps(item.get("arguments"), sort_keys=True, separators=(",", ":")) == expected_arguments), None)
                    if match_index is None:
                        raise AssertionError(f"no successful raw OpenAI docs event matched {case['name']} and exact arguments")
                    item = raw_calls.pop(match_index)
                    if case["required_text"] not in json.dumps(item.get("result", {}), sort_keys=True):
                        raise AssertionError(f"raw {case['name']} result omitted required text {case['required_text']!r}")
                returned_tool_output = returned_calls[0].get("output", "") if returned_calls else ""
                summary_line = next((value.split("OPENAI_DOCS_ALL_TOOLS_OK:", 1)[1]
                    for value in nested_strings(returned_tool_output) if "OPENAI_DOCS_ALL_TOOLS_OK:" in value), None)
                if summary_line is None:
                    raise AssertionError("Code Mode result omitted the five-tool OpenAI docs summary")
                summary = json.loads(summary_line)
                if [row.get("name") for row in summary] != [case["name"] for case in remote_cases] or any(
                    row.get("ok") is not True or row.get("has_result") is not True or row.get("has_required_text") is not True
                    for row in summary
                ):
                    raise AssertionError("Code Mode OpenAI docs summary omitted a successful validated result")
                if raw_calls:
                    raise AssertionError(f"unmatched raw OpenAI docs MCP calls remain: {len(raw_calls)}")
                print(f"NATIVE_OPENAI_DOCS_CODEMODE_CALLS: {len(summary)} calls across {len({row['name'] for row in summary})} unique tools")
            if args.case == "image" and not any(
                event.get("params", {}).get("item", {}).get("type") == "imageView"
                and "tiny-red.png" in event.get("params", {}).get("item", {}).get("path", "")
                for event in observed
            ):
                observed_items = [
                    event.get("params", {}).get("item", {})
                    for event in observed
                    if isinstance(event.get("params", {}).get("item"), dict)
                ]
                raise AssertionError(
                    "no raw app-server imageView item identified the PNG fixture; "
                    f"observed item summaries: {json.dumps([{key: item.get(key) for key in ('type', 'name', 'status', 'path', 'output', 'text')} for item in observed_items], sort_keys=True)}; "
                    f"second-request input summary: {json.dumps([{key: item.get(key) for key in ('type', 'name', 'call_id', 'output')} for item in requests[1].get('input', []) if isinstance(item, dict)], sort_keys=True)}"
                )
            output_marker = expected_marker if args.case in {"host-smoke", "tool-catalog", "agent-schema", "agent-lifecycle", "image", "goals", "apply-patch", "workspace-write-fail-closed", "jerboa-read", "jerboa-writers", "jerboa-matrix-probe", "jerboa-all", "openai-docs"} else "NESTED_TOOL_RESULT_OK"
            if len(returned_calls) != 1 or output_marker not in json.dumps(returned_calls[0].get("output", "")):
                actual_outputs = [call.get("output") for call in returned_calls]
                raise AssertionError(
                    "the next model request lacked exactly one Code Mode call result containing "
                    f"{output_marker!r}; observed outputs: {json.dumps(actual_outputs, sort_keys=True)}"
                )
            if args.case == "tool-catalog":
                catalog_strings = nested_strings(returned_calls[0].get("output", ""))
                catalog_line = next(
                    (value for value in catalog_strings if "CODE_MODE_NESTED_CATALOG_OK:" in value),
                    None,
                )
                if catalog_line is None:
                    raise AssertionError("Code Mode output did not include the actual nested tool catalog")
                catalog_text = catalog_line.split("CODE_MODE_NESTED_CATALOG_OK:", 1)[1].strip()
                observed_names = sorted(set(name for name in catalog_text.split(",") if name))
                missing_output_names = sorted(EXPECTED_NESTED_TOOLS - set(observed_names))
                unexpected_output_names = sorted(set(observed_names) - EXPECTED_NESTED_TOOLS)
                raw_name_count = len([name for name in catalog_text.split(",") if name])
                if missing_output_names or unexpected_output_names or raw_name_count != len(EXPECTED_NESTED_TOOLS):
                    raise AssertionError(
                        "the actual Code Mode result differs from the expected nested tool catalog: "
                        f"missing={missing_output_names}; unexpected={unexpected_output_names}; observed={observed_names}"
                    )
                print(f"NATIVE_NESTED_TOOL_CATALOG: {','.join(observed_names)}")
            if args.case == "agent-schema":
                schema_line = next((
                    value for value in nested_strings(returned_calls[0].get("output", ""))
                    if "AGENT_TOOL_SCHEMAS:" in value
                ), None)
                if schema_line is None:
                    raise AssertionError("agent schema result was not returned")
                serialized = schema_line.split("AGENT_TOOL_SCHEMAS:", 1)[1].strip()
                schemas = json.loads(serialized)
                expected_names = sorted(name for name in EXPECTED_NESTED_TOOLS if name.startswith("multi_agent_v1__"))
                if sorted(item.get("name") for item in schemas) != expected_names:
                    raise AssertionError(f"agent schemas differ from actual catalog: {schemas!r}")
                print("NATIVE_AGENT_TOOL_SCHEMAS: " + json.dumps(schemas, sort_keys=True))
            if args.case == "image":
                image_summary = next((
                    value.split("VIEW_IMAGE_PNG_BASE64_OK:", 1)[1]
                    for value in nested_strings(returned_calls[0].get("output", ""))
                    if "VIEW_IMAGE_PNG_BASE64_OK:" in value
                ), None)
                if image_summary is None:
                    raise AssertionError("view_image PNG validation summary was not returned to the local model stub")
                print(f"NATIVE_VIEW_IMAGE_PNG: {image_summary}")
            if args.case == "apply-patch":
                patch_path = workspace / "patch-marker.txt"
                if not patch_path.is_file() or patch_path.read_bytes() != b"CODEMODE_PATCH_APPLIED_OK\n":
                    raise AssertionError("apply_patch did not create the exact marker bytes in the isolated workspace")
                print("NATIVE_APPLY_PATCH: completed Code Mode result and exact isolated file bytes verified")
            if args.case == "workspace-write-fail-closed":
                marker_path = workspace / "workspace-write-marker.txt"
                if marker_path.exists():
                    raise AssertionError("workspace-write fail-closed probe created its forbidden marker")
                output = "\n".join(nested_strings(returned_calls[0].get("output", "")))
                if "filesystem sandbox cannot be enforced on this host" not in output:
                    raise AssertionError(
                        "correlated Code Mode result omitted the exact sandbox-unavailable diagnostic; "
                        f"captured output: {output[:1200]}"
                    )
                print("NATIVE_WORKSPACE_WRITE_FAIL_CLOSED: correlated Code Mode diagnostic verified; marker absent")
            if output_marker not in input_dump:
                raise AssertionError("the next actual model request did not contain the Code Mode result marker")
            print(f"PASS native app-server Code Mode {args.case}: raw item evidence and returned result reached the local model stub")
            return 0
        except Exception as error:
            print(f"FAIL native app-server Code Mode {args.case}: {type(error).__name__}: {error}")
            return 1
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            server.shutdown()
            server.server_close()
            if provider_server is not None:
                provider_server.shutdown()
                provider_server.server_close()
            if provider_thread is not None:
                provider_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
