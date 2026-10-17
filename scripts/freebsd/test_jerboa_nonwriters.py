#!/usr/bin/env python3
"""Exercise non-catalog Jerboa MCP tools against an isolated scratch project.

The six durable knowledge catalog writers are covered by test_jerboa_scratch.py.
This harness covers every other tool returned by the current 51-tool catalog,
including commands and source edits, with all effects confined to a fresh
directory under ~/work.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import select
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
JERBOA_HOME = Path(os.environ.get("CODEX_TEST_JERBOA_HOME", Path.home() / "mine" / "jerboa"))
DEFAULT_SERVER = Path(os.environ.get("CODEX_TEST_JERBOA_SERVER", Path.home() / ".local" / "bin" / "jmcp"))


class CompletionHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(size))
        assert request["messages"]
        body = json.dumps({"choices": [{"message": {"content": "```scheme\n(define (fixture-identity x) x)\n```"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        pass


class McpClient:
    def __init__(self, command: str, env: dict[str, str]):
        self.proc = subprocess.Popen(
            [command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env,
        )
        self.next_id = 1
        init = self.request("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "codex-freebsd-nonwriter-test", "version": "1"},
        })
        if "error" in init:
            raise RuntimeError(f"MCP initialize failed: {init['error']}")
        self.initialize_result = init.get("result", {})
        self.notify("notifications/initialized")
        listed = self.request("tools/list", {})
        self.tools_result = listed.get("result", {})
        self.tools = {tool["name"] for tool in self.tools_result.get("tools", [])}

    def notify(self, method: str) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.proc.stdin and self.proc.stdout
        request_id = self.next_id
        self.next_id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.proc.stdout], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP server exited: {self.proc.stderr.read() if self.proc.stderr else ''}")
            if not line.strip():
                continue
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"MCP wrote non-JSON stdout: {line[:300]!r}") from exc
            if response.get("id") == request_id:
                return response
        raise TimeoutError(f"timed out waiting for JSON-RPC id {request_id}")

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": args})

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            self.proc.wait(timeout=5)


def executable_provenance(command: str, env: dict[str, str], client: McpClient) -> dict[str, Any]:
    """Describe the exact provider/runtime used without exposing its environment."""
    executable = Path(command).expanduser().resolve(strict=True)
    jerboa = Path(env["JERBOA_MCP_JERBOA_PATH"]).expanduser().resolve(strict=True)
    home = Path(env["JERBOA_HOME"]).expanduser().resolve(strict=True)
    library_candidates = (home / "lib" / "libjerboa_native.so", home / "libjerboa_native.so")
    library = next((path.resolve() for path in library_candidates if path.is_file()), None)

    def digest(path: Path) -> str:
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
        return value.hexdigest()

    return {
        "configured_command": str(command),
        "resolved_executable": str(executable),
        "executable_sha256": digest(executable),
        "jerboa_home": str(home),
        "jerboa_binary": str(jerboa),
        "jerboa_binary_sha256": digest(jerboa),
        "native_library": str(library) if library else None,
        "native_library_sha256": digest(library) if library else None,
        "server_info": client.initialize_result.get("serverInfo"),
        "protocol_version": client.initialize_result.get("protocolVersion"),
        "tool_count": len(client.tools),
        "tool_names_sha256": hashlib.sha256(
            "\n".join(sorted(client.tools)).encode("utf-8")
        ).hexdigest(),
    }

def result_text(response: dict[str, Any]) -> str:
    result = response.get("result", {})
    return "\n".join(
        item.get("text", "") for item in result.get("content", [])
        if isinstance(item, dict) and item.get("type") == "text"
    )


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def prepare_fixture(root: Path) -> tuple[dict[str, str], Path, Path]:
    """Create a bounded Jerboa project and env under caller-owned ``root``."""
    root = root.expanduser().absolute()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    home, data, repo_cache, cache = (root / n for n in ("home", "data", "repo-cache", "cache"))
    xdg_cache, xdg_config, tmpdir = (root / n for n in ("xdg-cache", "xdg-config", "tmp"))
    project = root / "project"
    src = project / "src"
    evidence = data / "release-evidence"
    ready_evidence = data / "release-evidence-ready"
    for path in (home, data, repo_cache, cache, xdg_cache, xdg_config, tmpdir, src, evidence):
        path.mkdir(parents=True, exist_ok=True)
    for relative in ("reproducibility", "sbom", "soak", "signing"):
        (ready_evidence / relative).mkdir(parents=True, exist_ok=True)
    source = src / "fixture.ss"
    write(source, "(import (jerboa prelude))\n\n(define (fixture-add x y) (+ x y))\n")
    test_file = src / "scratch-test.ss"
    test_marker = project / "scratch-test-ran.txt"
    write(test_file, f'(import (jerboa prelude))\n(call-with-output-file "{test_marker}" (lambda (port) (display "ran" port)))\n')
    write(src / "broken.ss", "(define (broken x) (+ x\n")
    write(evidence / "status.txt", "status=pass\nproof_redaction_status=pass\n")
    for relative, content in {
        "result.txt": "release_evidence_status=pass\n",
        "reproducibility/result.txt": "status=match\n",
        "sbom/status.txt": "status=pass\n",
        "soak/status.txt": "status=pass\n",
        "signing/status.txt": "status=pass\n",
        "binary-smoke.txt": "status=pass\n",
        "rustsec-audit.txt": "status=pass\n",
        "git-status.txt": "",
    }.items():
        write(ready_evidence / relative, content)

    matrix_workspace = root / "release-matrix-workspace"
    matrix_repo = matrix_workspace / "jerboa-release-pass"
    (matrix_repo / "dist" / "release-evidence" / "reproducibility").mkdir(parents=True)
    for relative in ("sbom", "soak", "signing"):
        (matrix_repo / "dist" / "release-evidence" / relative).mkdir()
    (matrix_repo / ".jerboa").mkdir()
    write(matrix_repo / "README", "bounded release evidence fixture\n")
    write(matrix_repo / "Makefile", "release-evidence:\n\t@true\n")
    write(matrix_repo / ".jerboa" / "security.json", json.dumps({
        "policy": {"releaseEvidence": {"smoke": {"requiredMarkers": ["status=pass"]}}},
    }) + "\n")
    for relative, content in {
        "result.txt": "release_evidence_status=pass\n",
        "reproducibility/result.txt": "status=match\n",
        "sbom/status.txt": "status=pass\n",
        "soak/status.txt": "status=pass\n",
        "signing/status.txt": "status=pass\n",
        "binary-smoke.txt": "status=pass\n",
        "rustsec-audit.txt": "status=pass\n",
        "git-status.txt": "",
    }.items():
        write(matrix_repo / "dist" / "release-evidence" / relative, content)
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is required for the bounded repository snapshot and release-matrix cases")
    subprocess.run([git, "init", "-q", str(matrix_repo)], check=True)
    write(data / "security-profile.sexp", "()\n")
    write(data / "cookbook.sexp", "()\n")
    write(data / "anti-patterns.sexp", "()\n")
    write(data / "error-fixes.sexp", "()\n")
    write(data / "features.sexp", "()\n")
    write(data / "security-rules.sexp", json.dumps([{
        "id": "scratch-test-never-match", "scope": "scheme", "severity": "low",
        "pattern": "never_match_scratch_probe_91f0a7",
    }]) + "\n")
    write(project / "Makefile", ".PHONY: test\ntest:\n\t@echo PASS scratch-make\n")
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is required for the bounded repository snapshot cases")
    subprocess.run([git, "init", "-q", str(project)], check=True)
    git_prefix = [git, "-C", str(project), "-c", "user.name=Scratch Test", "-c", "user.email=scratch@example.invalid"]
    subprocess.run(git_prefix + ["add", "."], check=True)
    subprocess.run(git_prefix + ["commit", "-qm", "fixture"], check=True)
    env = os.environ.copy()
    env.update({
        "HOME": str(home), "XDG_CACHE_HOME": str(xdg_cache), "XDG_CONFIG_HOME": str(xdg_config), "TMPDIR": str(tmpdir),
        "JERBOA_HOME": str(JERBOA_HOME), "JERBOA_MCP_REPO": str(project),
        "JERBOA_MCP_DATA_DIR": str(data), "JERBOA_MCP_DATA_REPO_DIR": str(repo_cache),
        "JERBOA_MCP_CACHE_HOME": str(cache), "JERBOA_MCP_DATA_OFFLINE": "1",
        "JERBOA_MCP_LOG": "0", "JERBOA_MCP_LOG_FILE": str(root / "jmcp.log"),
        "JERBOA_MCP_JERBOA_PATH": os.environ.get(
            "CODEX_TEST_JERBOA_BIN", str(Path.home() / ".local" / "bin" / "jerboa")
        ),
    })
    return env, project, data


def start_completion_server() -> tuple[http.server.ThreadingHTTPServer, threading.Thread, str]:
    """Return a deterministic local OpenAI-compatible stub and endpoint."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CompletionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}/v1"


def build_nonwriter_cases(project: Path, data: Path, scratch: Path, llm_endpoint: str) -> list[dict[str, Any]]:
    """Return the 45 non-catalog-writer tool calls used by both transports."""
    project, data, scratch = project.absolute(), data.absolute(), scratch.absolute()
    source = project / "src" / "fixture.ss"
    mutable = project / "src" / "mutable.ss"
    insert_file = project / "src" / "insert.ss"
    write(mutable, "(define (mutable x) (+ x 1))\n")
    write(insert_file, "(define (first x) x)\n")
    scaffold = project / "generated.ss"
    output_file = project / "written.ss"
    repaired = project / "repair.ss"
    write(repaired, "(define (repair x) (+ x 1\n")
    proof_script = """#!/bin/sh
case \"$CODEX_PROOF_REQUIRED\" in 1) ;; *) exit 1 ;; esac
echo CODEX_PROOF_FILE
echo status=present
echo status=pass
echo proof_redaction_status=pass
echo blocked-missing
echo blocked-marker-missing
echo missing_marker=
echo sha256sum
echo proof-status.txt
echo blocked-oversized
echo TARGET_PROOF_MAX_BYTES
echo blocked-sensitive-material PRIVATE KEY private-path-in-proof
"""
    proof_spec = {"name": "scratch", "proofFileEnv": "CODEX_PROOF_FILE", "requireEnv": "CODEX_PROOF_REQUIRED", "requiredMarkers": ["status=pass", "proof_redaction_status=pass"]}
    cases: list[tuple[str, dict[str, Any], bool, str | None]] = [
        ("jerboa_eval", {"expression": "(+ 1 2)", "project_path": str(project), "jerboa_home": str(JERBOA_HOME)}, False, None),
        ("jerboa_check_syntax", {"code": "(define (x) 1)", "project_path": str(project)}, False, None),
        ("jerboa_compile_check", {"code": "(import (jerboa prelude))\n(define (fixture-compiled x) (+ x 1))", "project_path": str(project), "jerboa_home": str(JERBOA_HOME)}, False, None),
        ("jerboa_verify", {"file_path": str(source), "project_path": str(project), "jerboa_home": str(JERBOA_HOME)}, False, None),
        ("jerboa_module_exports", {"module_path": "(std sort)", "jerboa_home": str(JERBOA_HOME)}, False, None),
        ("jerboa_module_exists", {"module_path": "(std sort)", "jerboa_home": str(JERBOA_HOME)}, False, None),
        ("jerboa_function_signature", {"symbol": "sort", "imports": ["(std sort)"], "jerboa_home": str(JERBOA_HOME)}, False, None),
        ("jerboa_apropos", {"patterns": ["sort"], "include_index": True}, False, None),
        ("jerboa_howto", {"query": "scratch fixture", "max_results": 2, "compact": True}, False, None),
        ("jerboa_howto_get", {"id": "__scratch_missing_recipe__"}, True, "Recipe not found:"),
        ("jerboa_howto_get", {"id": "codex-freebsd-tool-positive-recipe", "cookbook_path": str(data / "cookbooks.sexp")}, False, "codex-freebsd-tool-positive-recipe"),
        ("jerboa_cookbook_task_bundle", {"task": "inspect fixture", "cookbook_path": str(data / "cookbook.sexp"), "anti_patterns_path": str(data / "anti-patterns.sexp"), "project_path": str(project), "file_path": str(source)}, False, None),
        ("jerboa_request_advisor", {"task": "inspect fixture", "cookbook_path": str(data / "cookbook.sexp"), "anti_patterns_path": str(data / "anti-patterns.sexp"), "project_path": str(project), "file_path": str(source), "max_recipes": 1, "max_anti_patterns": 1}, False, None),
        ("jerboa_verify_plan", {"task": "inspect fixture", "project_path": str(project), "file_path": str(source)}, False, None),
        ("jerboa_failure_advisor", {"error_message": "unbound identifier", "snippet": "(fixture-add 1 2)", "project_path": str(project)}, False, None),
        ("jerboa_anti_pattern_lookup", {"query": "scratch", "anti_patterns_path": str(data / "anti-patterns.sexp")}, False, None),
        ("jerboa_security_scan", {"file_path": str(source), "project_path": str(project), "severity_threshold": "low"}, False, None),
        ("jerboa_run_tests", {"file_path": str(project / "src" / "scratch-test.ss"), "project_path": str(project), "jerboa_home": str(JERBOA_HOME)}, False, None),
        ("jerboa_make", {"project_path": str(project), "target": "test"}, False, None),
        ("jerboa_test_baseline_diff", {"project_path": str(project), "target": "test", "baseline_path": str(data / "baseline.txt"), "update": False}, False, None),
        ("jerboa_release_evidence_status", {"project_path": str(project), "evidence_dir": str(data / "release-evidence")}, True, "Overall readiness: not-ready"),
        ("jerboa_release_evidence_status", {"project_path": str(project), "evidence_dir": str(data / "release-evidence-ready")}, False, "Overall readiness: ready"),
        ("jerboa_release_evidence_toolchain_staleness", {"project_path": str(project), "core_path": str(JERBOA_HOME), "mode": "warn"}, False, None),
        ("jerboa_target_proof_marker_verifier", {"project_path": str(project), "mode": "validate", "script": proof_script, "proofs": [proof_spec]}, False, None),
        ("jerboa_release_evidence_privacy_audit", {"project_path": str(project), "evidence_dir": str(data / "release-evidence"), "target_evidence_dir": str(data / "target-evidence"), "fix": False}, False, None),
        ("jerboa_cross_repo_release_evidence_matrix", {"workspace_path": str(scratch), "max_repos": 1}, True, "target-missing"),
        ("jerboa_cross_repo_release_evidence_matrix", {"workspace_path": str(scratch / "release-matrix-workspace"), "max_repos": 1}, False, "jerboa-release-pass | present | pass | present | pass"),
        ("jerboa_security_profile", {"project_path": str(project), "profile_path": str(data / "security-profile.sexp")}, False, None),
        ("jerboa_read_source", {"file_path": str(source), "start_line": 1, "end_line": 5}, False, None),
        ("jerboa_source_form_spans", {"file_path": str(source)}, False, None),
        ("jerboa_repo_snapshot", {"project_path": str(project), "target_path": str(scratch / "snapshot"), "dry_run": False}, False, None),
        ("jerboa_generated_sls_source_layout_audit", {"project_path": str(project), "output_roots": [str(project / "build")], "libdirs": [str(project / "lib")]}, False, None),
        ("jerboa_fleet_security_scan", {"workspace_path": str(scratch), "max_repos": 1, "max_findings": 5, "include_status": True, "include_make_targets": True}, False, None),
        ("jerboa_check_balance", {"file_path": str(source)}, False, None),
        ("jerboa_balanced_replace", {"file_path": str(mutable), "old_string": "(+ x 1)", "new_string": "(+ x 2)", "dry_run": False}, False, None),
        ("jerboa_balanced_insert", {"file_path": str(insert_file), "anchor": "(define (first x) x)", "form": "(define (second x) x)", "position": "after", "dry_run": False}, False, None),
        ("jerboa_error_fix_lookup", {"error_message": "unbound identifier", "error_fixes_path": str(data / "error-fixes.sexp")}, False, None),
        ("jerboa_explain_error", {"error_message": "unbound identifier: scratch"}, False, None),
        ("jerboa_invalid_context_definition_diagnoser", {"file_path": str(source), "error": "unbound identifier", "line": 1}, False, None),
        ("jerboa_repair_error", {"error_message": "unbound identifier", "project_path": str(project), "file_path": str(source)}, False, None),
        ("jerboa_list_features", {"features_path": str(data / "features.sexp"), "compact": True, "limit": 2}, False, None),
        ("jerboa_task_workflow_advisor", {"task": "inspect fixture", "project_path": str(project), "file_path": str(source)}, False, None),
        ("jerboa_compact_tool_manifest", {"query": "read source and compile", "max_results": 5}, False, None),
        ("jerboa_script_scaffold_verify", {"task": "echo an argument", "kind": "echo-args", "file_path": str(scaffold), "project_path": str(project), "write": True, "overwrite": False, "verify": True, "args": ["scratch-ok"], "expected_output": "scratch-ok"}, False, "Expected match: yes"),
        ("jerboa_script_scaffold_verify", {"task": "count command arguments", "kind": "count-args", "file_path": str(project / "count-args.ss"), "project_path": str(project), "write": True, "overwrite": False, "verify": True, "args": [], "expected_output": "0"}, False, "Expected match: yes"),
        ("jerboa_prompt_to_code", {"prompt": "define identity function", "endpoint": llm_endpoint, "model": "scratch-stub", "max_iterations": 1, "timeout_ms": 10000, "project_path": str(project), "jerboa_home": str(JERBOA_HOME)}, False, None),
        ("jerboa_write_file", {"file_path": str(output_file), "content": "(import (jerboa prelude))\n(define (written x) (+ x 1))\n", "project_path": str(project), "overwrite": False, "verify": True, "jerboa_home": str(JERBOA_HOME)}, False, None),
        ("jerboa_repair_balance", {"file_path": str(repaired), "apply": True}, False, None),
        ("jerboa", {"tool": "search", "args": {"query": "howto cookbook", "max_results": 3}}, False, None),
    ]
    return [{"name": name, "arguments": arguments, "expected_is_error": expected_error, "required_text": required_text} for name, arguments, expected_error, required_text in cases]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default=os.environ.get("JERBOA_MCP_COMMAND", str(DEFAULT_SERVER)))
    parser.add_argument("--report", type=Path, help="write sanitized JSON results here")
    args = parser.parse_args()
    # Keep the requested basename: Jerboa dispatches by argv[0], so resolving
    # the `jmcp` symlink to `jerboa` would launch the REPL instead of MCP.
    server = Path(os.path.abspath(args.server))
    if not server.is_file() or not os.access(server, os.X_OK):
        raise SystemExit(f"MCP server is not executable: {server}")

    work = Path.home() / "work"
    work.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="codex-freebsd-jerboa-nonwriters-", dir=work))
    env, project, data = prepare_fixture(scratch)
    source_file = project / "src" / "fixture.ss"
    mutable = project / "src" / "mutable.ss"
    insert_file = project / "src" / "insert.ss"
    scaffold = project / "generated.ss"
    output_file = project / "written.ss"
    repaired = project / "repair.ss"
    test_marker = project / "scratch-test-ran.txt"
    snapshot = scratch / "snapshot"
    llm, llm_thread, llm_endpoint = start_completion_server()
    client = McpClient(str(server), env)
    cases = build_nonwriter_cases(project, data, scratch, llm_endpoint)
    failed: list[dict[str, Any]] = []
    known_provider_findings: list[dict[str, str]] = []
    report: list[dict[str, Any]] = []
    try:
        absent = sorted({case["name"] for case in cases} - client.tools)
        if absent:
            raise AssertionError(f"MCP catalog is missing expected tools: {absent}")
        client.call("jerboa_howto_add", {
            "id": "codex-freebsd-tool-positive-recipe",
            "title": "Bounded positive howto-get fixture",
            "code": "(+ 1 2)",
            "tags": ["scratch"],
            "notes": "Temporary fixture only.",
            "cookbook_path": str(data / "cookbooks.sexp"),
        })
        for case in cases:
            name = case["name"]
            tool_args = case["arguments"]
            expected_error = case["expected_is_error"]
            response = client.call(name, tool_args)
            rpc_error = response.get("error")
            result = response.get("result", {})
            is_error = bool(result.get("isError"))
            text = result_text(response)
            ok = rpc_error is None and is_error == expected_error
            required_text = case.get("required_text")
            if required_text and required_text not in text:
                ok = False
            if name == "jerboa_script_scaffold_verify" and expected_error:
                if "Verify: PASS" not in text or "Expected match: no" not in text:
                    ok = False
                else:
                    known_provider_findings.append({
                        "tool": name,
                        "classification": "known-provider-mismatch",
                        "detail": "Generated script compiles and executes, but the run alias leaves `run <script-path>` in the command-line arguments and disagrees with expected_output.",
                    })
            classification = (
                "known-provider-mismatch" if name == "jerboa_script_scaffold_verify" and expected_error and ok
                else "expected-blocker" if expected_error and ok
                else "pass" if ok else "failure"
            )
            row = {"tool": name, "ok": ok, "classification": classification, "expected_isError": expected_error, "isError": is_error, "rpc_error": rpc_error, "required_text": required_text, "text": text[:1000]}
            report.append(row)
            label = {"pass": "PASS", "expected-blocker": "BLOCKED-AS-EXPECTED", "known-provider-mismatch": "KNOWN-PROVIDER-MISMATCH", "failure": "FAIL"}[classification]
            print(f"{label} {name}: isError={is_error}; {text[:180].replace(chr(10), ' ')}")
            if not ok:
                failed.append(row)
        # Verify scratch edits occurred and the source project stayed within scratch.
        postconditions = {
            "howto_get_seeded_recipe": (data / "cookbooks.sexp").is_file() and "codex-freebsd-tool-positive-recipe" in (data / "cookbooks.sexp").read_text(),
            "balanced_replace": "(+ x 2)" in mutable.read_text(),
            "balanced_insert": "(define (second x) x)" in insert_file.read_text(),
            "scaffold_written": Path(scaffold).is_file(),
            "write_file_written": Path(output_file).is_file(),
            "repair_applied": Path(repaired).read_text().count("(") == Path(repaired).read_text().count(")"),
            "snapshot_created": snapshot.is_dir(),
            "run_tests_executed_fixture": test_marker.is_file() and test_marker.read_text() == "ran",
        }
        for name, ok in postconditions.items():
            print(("PASS" if ok else "FAIL") + f" postcondition {name}")
            if not ok:
                failed.append({"postcondition": name, "ok": False})
    finally:
        client.close()
        llm.shutdown()
        llm.server_close()
        llm_thread.join(timeout=5)

    # Six persistent catalog mutations are intentionally tested by the sibling harness.
    writer_tools = {"jerboa_anti_pattern_add", "jerboa_howto_add", "jerboa_error_fix_add", "jerboa_suggest_feature", "jerboa_vote_feature", "jerboa_security_pattern_add"}
    tested = {case["name"] for case in cases}
    if len(client.tools) != 51:
        failed.append({"catalog_count": len(client.tools), "expected": 51, "ok": False})
    if tested | writer_tools != client.tools:
        failed.append({"catalog_partition": sorted(client.tools - tested - writer_tools), "ok": False})
    output = {"server": str(server), "tool_count": len(client.tools), "tested_nonwriter_count": len(tested), "writer_harness_tools": sorted(writer_tools), "scratch_root": str(scratch), "results": report, "known_provider_findings": known_provider_findings, "post_status": "fail" if failed else "pass-with-known-provider-mismatch" if known_provider_findings else "pass", "failures": failed}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(output, indent=2) + "\n")
    else:
        print(json.dumps({k: output[k] for k in ("tool_count", "tested_nonwriter_count", "scratch_root", "post_status", "known_provider_findings", "failures")}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
