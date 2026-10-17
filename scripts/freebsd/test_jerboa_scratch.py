#!/usr/bin/env python3
"""Exercise Jerboa MCP knowledge writers against a disposable data root.

This is a protocol-level FreeBSD server test, not proof of Codex dispatch. The
separate native app-server matrix covers that client path. Every writer gets
an explicit path under a temporary root and every readback uses the same file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT_SERVER = Path.home() / ".local/bin/jmcp"
JERBOA_HOME = Path.home() / "mine/jerboa"
JERBOA_BIN = Path.home() / ".local/bin/jerboa"
SCRATCH_PARENT = Path.home() / "work/codex-runtime-repro/freebsd-jerboa-fixtures"
WRITERS = {
    "jerboa_howto_add",
    "jerboa_anti_pattern_add",
    "jerboa_error_fix_add",
    "jerboa_suggest_feature",
    "jerboa_vote_feature",
    "jerboa_security_pattern_add",
}


class McpProcess:
    def __init__(self, server: Path, env: dict[str, str], cwd: Path, timeout: float) -> None:
        self.timeout = timeout
        self.process = subprocess.Popen(
            [str(server)],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self.next_id = 0

    def close(self) -> str:
        if self.process.poll() is None:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait()
        return self.process.stderr.read() if self.process.stderr else ""

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.process.stdin and self.process.stdout
        self.next_id += 1
        request_id = self.next_id
        request: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

        deadline = time.monotonic() + self.timeout
        fd = self.process.stdout.fileno()
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if not select.select([fd], [], [], remaining)[0]:
                break
            line = self.process.stdout.readline()
            if not line:
                break
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RuntimeError(f"{method} JSON-RPC error: {response['error']}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"{method} returned no result object")
            return result
        raise TimeoutError(f"timed out waiting for {method} response {request_id}")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        assert self.process.stdin
        notification: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            notification["params"] = params
        self.process.stdin.write(json.dumps(notification, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(f"{name} tool result marked isError: {text_from(result)}")
        if not isinstance(result.get("content"), list):
            raise RuntimeError(f"{name} returned no content array")
        return result


def text_from(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        own = value.get("text")
        return (own if isinstance(own, str) else "") + "\n" + "\n".join(text_from(child) for child in value.values())
    if isinstance(value, list):
        return "\n".join(text_from(child) for child in value)
    return ""


def require_contains(label: str, result: dict[str, Any], marker: str) -> None:
    observed = text_from(result.get("content", []))
    if marker not in observed:
        raise AssertionError(f"{label} result did not contain {marker!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, default=DEFAULT_SERVER)
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    if not args.server.is_file() or not os.access(args.server, os.X_OK):
        print(f"FAIL Jerboa MCP executable unavailable: {args.server}")
        return 2

    try:
        SCRATCH_PARENT.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent_stat = SCRATCH_PARENT.stat()
        if parent_stat.st_uid != os.geteuid() or parent_stat.st_mode & 0o022:
            raise PermissionError(f"scratch parent is not private to this user: {SCRATCH_PARENT}")
        with tempfile.TemporaryDirectory(prefix="codex-jerboa-scratch-", dir=SCRATCH_PARENT) as temp:
            root = Path(temp).resolve()
            home = root / "home"
            data = root / "data"
            data_repo = root / "data-repo"
            cache = root / "cache"
            xdg_cache = root / "xdg-cache"
            xdg_config = root / "xdg-config"
            project = root / "project"
            tmpdir = root / "tmp"
            for directory in (home, data, data_repo, cache, xdg_cache, xdg_config, project, tmpdir):
                directory.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "XDG_CACHE_HOME": str(xdg_cache),
                "XDG_CONFIG_HOME": str(xdg_config),
                "TMPDIR": str(tmpdir),
                "JERBOA_MCP_REPO": str(project),
                "JERBOA_MCP_DATA_DIR": str(data),
                "JERBOA_MCP_DATA_REPO_DIR": str(data_repo),
                "JERBOA_MCP_CACHE_HOME": str(cache),
                "JERBOA_MCP_DATA_OFFLINE": "1",
                "JERBOA_MCP_LOG": "0",
                "JERBOA_MCP_LOG_FILE": str(root / "jmcp.log"),
                "JERBOA_MCP_JERBOA_PATH": str(JERBOA_BIN),
                "JERBOA_HOME": str(JERBOA_HOME),
            })

            prefix = "codex-freebsd-scratch-20261016"
            paths = {
                "cookbook": data / "cookbooks.sexp",
                "anti": data / "anti-patterns.sexp",
                "errors": data / "error-fixes.sexp",
                "features": data / "features.sexp",
                "rules": data / "security-rules.sexp",
            }
            if any(not path.resolve().is_relative_to(root) for path in paths.values()):
                raise AssertionError("a fixture writer path escaped its temporary root")

            # Keep the symlink basename `jmcp`: the multicall binary selects
            # MCP server mode from argv[0]. Resolving to `jerboa` is different.
            server = McpProcess(args.server.expanduser().absolute(), env, project, args.timeout)
            try:
                initialized = server.request("initialize", {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "codex-freebsd-scratch-writer-test", "version": "1"},
                })
                if initialized.get("protocolVersion") != "2025-03-26":
                    raise AssertionError(f"unexpected MCP protocol version: {initialized.get('protocolVersion')}")
                server.notify("notifications/initialized", {})
                listed = server.request("tools/list", {})
                exposed = {tool.get("name") for tool in listed.get("tools", []) if isinstance(tool, dict)}
                if not WRITERS <= exposed:
                    raise AssertionError(f"Jerboa tools/list omitted writer tools: {sorted(WRITERS - exposed)}")
                print(f"PASS Jerboa initialize/tools-list: {len(exposed)} tools, protocol {initialized['protocolVersion']}")

                recipe_id = f"{prefix}-recipe"
                antipattern_id = f"{prefix}-anti"
                error_id = f"{prefix}-error"
                feature_id = f"{prefix}-feature"
                rule_id = f"{prefix}-rule"

                server.call("jerboa_howto_add", {
                    "id": recipe_id,
                    "title": "FreeBSD disposable MCP test recipe",
                    "code": "(+ 1 2)",
                    "tags": ["scratch", "freebsd"],
                    "notes": "Temporary test data only.",
                    "cookbook_path": str(paths["cookbook"]),
                })
                require_contains("howto readback", server.call("jerboa_howto_get", {
                    "id": recipe_id, "cookbook_path": str(paths["cookbook"]),
                }), recipe_id)
                print("PASS jerboa_howto_add -> jerboa_howto_get")

                server.call("jerboa_anti_pattern_add", {
                    "id": antipattern_id,
                    "title": "Disposable FreeBSD test anti-pattern",
                    "avoid": "Do not write outside the fixture.",
                    "advice": "Use the supplied scratch root.",
                    "pattern": "scratch-only fixture",
                    "anti_patterns_path": str(paths["anti"]),
                })
                require_contains("anti-pattern lookup", server.call("jerboa_anti_pattern_lookup", {
                    "query": antipattern_id, "anti_patterns_path": str(paths["anti"]),
                }), antipattern_id)
                print("PASS jerboa_anti_pattern_add -> jerboa_anti_pattern_lookup")

                server.call("jerboa_error_fix_add", {
                    "id": error_id,
                    "pattern": f"{prefix}-error-pattern",
                    "fix": "Use the scratch fixture path.",
                    "explanation": "Disposable MCP writer roundtrip test.",
                    "error_fixes_path": str(paths["errors"]),
                })
                require_contains("error-fix lookup", server.call("jerboa_error_fix_lookup", {
                    "error_message": f"observed {prefix}-error-pattern in a fixture",
                    "error_fixes_path": str(paths["errors"]),
                }), error_id)
                print("PASS jerboa_error_fix_add -> jerboa_error_fix_lookup")

                server.call("jerboa_suggest_feature", {
                    "id": feature_id,
                    "title": "Disposable FreeBSD MCP test feature",
                    "description": "Scratch-only feature roundtrip.",
                    "use_case": "Validate isolated feature persistence.",
                    "example_scenario": "Run the native FreeBSD MCP test.",
                    "status": "proposed",
                    "impact": "low",
                    "features_path": str(paths["features"]),
                })
                before_vote = server.call("jerboa_list_features", {
                    "query": feature_id, "features_path": str(paths["features"]), "limit": 3,
                })
                require_contains("feature list before vote", before_vote, feature_id)
                server.call("jerboa_vote_feature", {"id": feature_id, "features_path": str(paths["features"])})
                after_vote = server.call("jerboa_list_features", {
                    "query": feature_id, "features_path": str(paths["features"]), "limit": 3,
                })
                after_vote_text = text_from(after_vote.get("content", []))
                if feature_id not in after_vote_text:
                    raise AssertionError(f"feature list after vote omitted {feature_id!r}: {after_vote_text[:2000]!r}")
                persisted_feature = paths["features"].read_text(encoding="utf-8")
                if feature_id not in persisted_feature or not re.search(r'\("votes"\s+\.\s+1\)', persisted_feature):
                    raise AssertionError(f"feature vote was not persisted in {paths['features']}: {persisted_feature[:2000]!r}")
                print("PASS jerboa_suggest_feature -> jerboa_list_features -> jerboa_vote_feature -> votes 1")

                server.call("jerboa_security_pattern_add", {
                    "id": rule_id,
                    "scope": "test",
                    "severity": "low",
                    "pattern": f"{prefix}-security-pattern",
                    "message": "Disposable scratch rule.",
                    "rules_path": str(paths["rules"]),
                })
                print("PASS jerboa_security_pattern_add: stored only under scratch data root")

                for label, path, marker in (
                    ("howto", paths["cookbook"], recipe_id),
                    ("anti-pattern", paths["anti"], antipattern_id),
                    ("error-fix", paths["errors"], error_id),
                    ("feature", paths["features"], feature_id),
                    ("security-rule", paths["rules"], rule_id),
                ):
                    if not path.is_file() or marker not in path.read_text(encoding="utf-8"):
                        raise AssertionError(f"{label} writer did not persist its marker under the scratch data root")

                observed_paths = [path.resolve() for path in root.rglob("*")]
                if any(not path.is_relative_to(root) for path in observed_paths):
                    raise AssertionError("observed a path outside the isolated temporary root")
                if server.process.returncode not in (None, 0):
                    raise AssertionError(f"Jerboa MCP server is still running with status {server.process.returncode}")
                print("PASS native Jerboa MCP scratch store: initialize/tools-list, six writer APIs, read-after-write, feature vote, and all data paths confined to temporary root")
                return 0
            finally:
                server_stderr = server.close()
                if server.process.returncode not in (0, None):
                    raise RuntimeError(f"Jerboa MCP exited with status {server.process.returncode}: {server_stderr[-2000:]}")
    except Exception as error:
        print(f"FAIL native Jerboa MCP scratch store: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
