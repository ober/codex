#!/usr/bin/env python3
"""Test request_user_input through a local Responses stub and app-server client."""

from __future__ import annotations

import argparse
import http.server
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

MODEL = "gpt-5.6-luna"
API_KEY = "freebsd-appserver-fixture-only"
QUESTION_ARGS = {
    "questions": [
        {
            "id": "freebsd_probe",
            "header": "Fixture",
            "question": "Choose the successful smoke-test result.",
            "options": [
                {"label": "Fail", "description": "Return the negative fixture option."},
                {"label": "Pass", "description": "Return the positive fixture option."},
            ],
        }
    ]
}


def sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        for event in events
    ).encode()


def function_call_response() -> bytes:
    response_id = "resp-user-input-fixture"
    call = {
        "type": "function_call",
        "call_id": "call-user-input-fixture",
        "name": "request_user_input",
        "arguments": json.dumps(QUESTION_ARGS, separators=(",", ":")),
    }
    return sse(
        [
            {"type": "response.created", "response": {"id": response_id}},
            {"type": "response.output_item.done", "item": call},
            {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                },
            },
        ]
    )


def final_response() -> bytes:
    response_id = "resp-user-input-final"
    return sse(
        [
            {"type": "response.created", "response": {"id": response_id}},
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "id": "msg-user-input-final",
                    "content": [
                        {"type": "output_text", "text": "USER_INPUT_RESPONSE_ACCEPTED"}
                    ],
                },
            },
            {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                },
            },
        ]
    )


class Capture:
    def __init__(self) -> None:
        self.count = 0
        self.followup_input: Any = None
        self.error: str | None = None
        self.lock = threading.Lock()


def validate_question_request(
    params: dict[str, Any], thread_id: str, turn_id: str
) -> None:
    if params.get("threadId") != thread_id or params.get("turnId") != turn_id:
        raise AssertionError(
            "request_user_input was attributed to the wrong thread or turn"
        )
    if (
        params.get("itemId") != "call-user-input-fixture"
        or params.get("isBlocking") is not True
    ):
        raise AssertionError(
            "request_user_input call identity or blocking mode was unexpected"
        )
    expected_questions = [
        {
            **QUESTION_ARGS["questions"][0],
            # The request_user_input handler normalizes all option-backed
            # questions to expose an explicit free-form "other" response.
            "isOther": True,
            "isSecret": False,
        }
    ]
    if params.get("questions") != expected_questions:
        raise AssertionError(
            "request_user_input question/options did not match the fixture exactly: "
            f"observed={json.dumps(params.get('questions'), sort_keys=True)}"
        )


def validate_round_trip_input(input_items: Any) -> None:
    if not isinstance(input_items, list):
        raise AssertionError("follow-up Responses request input was not an array")
    outputs = [
        item
        for item in input_items
        if isinstance(item, dict)
        and item.get("type") == "function_call_output"
        and item.get("call_id") == "call-user-input-fixture"
    ]
    if len(outputs) != 1:
        raise AssertionError(
            "follow-up model request must contain exactly one function_call_output "
            "correlated to call-user-input-fixture"
        )
    output_text = outputs[0].get("output")
    if not isinstance(output_text, str):
        raise AssertionError(
            "correlated function_call_output did not contain text output"
        )
    try:
        output = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise AssertionError(
            "correlated function_call_output was not serialized JSON"
        ) from error
    expected = {"answers": {"freebsd_probe": {"answers": ["Pass"]}}}
    if output != expected:
        raise AssertionError(
            "correlated function_call_output did not contain the exact simulated answer"
        )


def validate_completed_turn(notification: dict[str, Any], turn_id: str) -> None:
    if notification.get("method") != "turn/completed":
        raise AssertionError("request_user_input turn did not end with turn/completed")
    turn = notification.get("params", {}).get("turn", {})
    if turn.get("id") != turn_id:
        raise AssertionError("terminal notification belonged to the wrong turn")
    if turn.get("status") != "completed":
        raise AssertionError(
            f"turn ended with status {turn.get('status')!r}, expected 'completed'"
        )


def self_test() -> int:
    valid_params = {
        "threadId": "thread-fixture",
        "turnId": "turn-fixture",
        "itemId": "call-user-input-fixture",
        "isBlocking": True,
        "questions": [
            {**QUESTION_ARGS["questions"][0], "isOther": True, "isSecret": False}
        ],
    }
    validate_question_request(valid_params, "thread-fixture", "turn-fixture")
    valid_input = [
        {
            "type": "function_call_output",
            "call_id": "call-user-input-fixture",
            "output": json.dumps({"answers": {"freebsd_probe": {"answers": ["Pass"]}}}),
        }
    ]
    validate_round_trip_input(valid_input)
    validate_completed_turn(
        {
            "method": "turn/completed",
            "params": {"turn": {"id": "turn-fixture", "status": "completed"}},
        },
        "turn-fixture",
    )

    negative_cases = [
        lambda: validate_question_request(
            {**valid_params, "questions": []}, "thread-fixture", "turn-fixture"
        ),
        lambda: validate_question_request(
            {
                **valid_params,
                "questions": [
                    {**valid_params["questions"][0], "question": "different question"}
                ],
            },
            "thread-fixture",
            "turn-fixture",
        ),
        lambda: validate_round_trip_input([]),
        lambda: validate_round_trip_input(
            [
                {
                    **valid_input[0],
                    "call_id": "wrong-call-id",
                }
            ]
        ),
        # The option label occurs in the original function-call arguments; it
        # must not be accepted as evidence that the user response was forwarded.
        lambda: validate_round_trip_input(
            [
                {
                    **valid_input[0],
                    "type": "function_call",
                    "output": json.dumps(QUESTION_ARGS),
                }
            ]
        ),
        lambda: validate_round_trip_input(
            [
                {
                    **valid_input[0],
                    "output": json.dumps(
                        {"answers": {"freebsd_probe": {"answers": ["Fail"]}}}
                    ),
                }
            ]
        ),
        lambda: validate_completed_turn(
            {
                "method": "turn/completed",
                "params": {"turn": {"id": "turn-fixture", "status": "failed"}},
            },
            "turn-fixture",
        ),
        lambda: validate_completed_turn(
            {
                "method": "turn/completed",
                "params": {"turn": {"id": "turn-fixture", "status": "interrupted"}},
            },
            "turn-fixture",
        ),
    ]
    for index, negative_case in enumerate(negative_cases, start=1):
        try:
            negative_case()
        except AssertionError:
            continue
        raise AssertionError(f"negative fixture case {index} was incorrectly accepted")
    print(
        "PASS request_user_input self-test: exact question, correlated answer, and completed-turn negatives rejected"
    )
    return 0


def make_handler(capture: Capture) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if (
                self.path != "/v1/responses"
                or self.headers.get("Authorization") != f"Bearer {API_KEY}"
            ):
                self.send_error(401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("request body was not an object")
                with capture.lock:
                    capture.count += 1
                    request_number = capture.count
                    if request_number == 2:
                        capture.followup_input = body.get("input", [])
                payload = (
                    function_call_response()
                    if request_number == 1
                    else final_response()
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as error:  # surfaced after the app-server test exits
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
    parser.add_argument(
        "--codex", type=Path, default=Path.home() / ".local/bin/codex-freebsd"
    )
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="test exact question, answer, and completion assertions",
    )
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.codex.is_file() or not os.access(args.codex, os.X_OK):
        print(f"FAIL native Codex executable unavailable: {args.codex}")
        return 2

    capture = Capture()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(capture))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    messages: queue.Queue[Any] = queue.Queue()
    process: subprocess.Popen[str] | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="codex-user-input-appserver-") as temp:
            home = Path(temp)
            provider = f'''model = "{MODEL}"
model_provider = "freebsd_user_input_fixture"
approval_policy = "never"

[model_providers.freebsd_user_input_fixture]
name = "FreeBSD local request_user_input fixture"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
env_key = "OPENAI_API_KEY"
'''
            (home / "config.toml").write_text(provider, encoding="utf-8")
            environment = os.environ.copy()
            environment["CODEX_HOME"] = str(home)
            environment["OPENAI_API_KEY"] = API_KEY
            environment.pop("OPENAI_BASE_URL", None)
            environment.pop("CODEX_API_KEY", None)
            environment.pop("CODEX_ACCESS_TOKEN", None)
            process = subprocess.Popen(
                [str(args.codex), "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=environment,
            )
            assert process.stdin is not None and process.stdout is not None
            threading.Thread(
                target=read_messages, args=(process.stdout, messages), daemon=True
            ).start()
            next_id = 1

            def send(method: str, params: dict[str, Any] | None = None) -> int:
                nonlocal next_id
                request_id = next_id
                next_id += 1
                request = {"jsonrpc": "2.0", "id": request_id, "method": method}
                if params is not None:
                    request["params"] = params
                process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
                process.stdin.flush()
                return request_id

            def await_response(request_id: int) -> dict[str, Any]:
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    try:
                        message = messages.get(
                            timeout=min(1.0, max(0.01, deadline - time.monotonic()))
                        )
                    except queue.Empty:
                        continue
                    if isinstance(message, BaseException):
                        raise message
                    if message.get("id") == request_id and (
                        "result" in message or "error" in message
                    ):
                        if "error" in message:
                            raise RuntimeError(f"{message['error']}")
                        return message
                raise TimeoutError(
                    f"timed out waiting for app-server response {request_id}"
                )

            init_id = send(
                "initialize",
                {
                    "clientInfo": {
                        "name": "codex-freebsd-tool-fixture",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            await_response(init_id)
            send("initialized", {})
            thread_id = await_response(
                send(
                    "thread/start",
                    {
                        "model": MODEL,
                        "cwd": str(home),
                        "ephemeral": True,
                    },
                )
            )["result"]["thread"]["id"]
            turn_start = send(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [
                        {
                            "type": "text",
                            "text": "Ask the smoke-test question and wait for the answer.",
                            "textElements": [],
                        }
                    ],
                    "model": MODEL,
                    "collaborationMode": {
                        "mode": "plan",
                        "settings": {"model": MODEL, "reasoningEffort": "medium"},
                    },
                },
            )
            turn_response = await_response(turn_start)
            turn_id = turn_response["result"]["turn"]["id"]

            saw_request = False
            saw_answer_result = False
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                try:
                    message = messages.get(
                        timeout=min(1.0, max(0.01, deadline - time.monotonic()))
                    )
                except queue.Empty:
                    continue
                if isinstance(message, BaseException):
                    raise message
                if message.get("method") == "item/tool/requestUserInput":
                    params = message.get("params", {})
                    validate_question_request(params, thread_id, turn_id)
                    process.stdin.write(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": message["id"],
                                "result": {
                                    "answers": {"freebsd_probe": {"answers": ["Pass"]}}
                                },
                            },
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    process.stdin.flush()
                    saw_request = True
                elif message.get("method") == "turn/completed":
                    payload = message.get("params", {})
                    if payload.get("turn", {}).get("id") == turn_id:
                        validate_completed_turn(message, turn_id)
                        saw_answer_result = True
                        break
                elif message.get("method") == "error":
                    raise RuntimeError("app-server emitted an error notification")

            if not saw_request:
                raise AssertionError(
                    "app-server never requested the fixture user's answer"
                )
            if not saw_answer_result:
                raise AssertionError(
                    "turn did not complete after the simulated user response"
                )

        with capture.lock:
            if capture.error:
                raise RuntimeError(f"local Responses fixture failed: {capture.error}")
            if capture.count != 2:
                raise AssertionError(
                    f"expected exactly two local model requests, observed {capture.count}"
                )
            validate_round_trip_input(capture.followup_input)
        if not saw_answer_result:
            raise AssertionError(
                "turn did not complete after the simulated user response"
            )
        print(
            "PASS request_user_input app-server round trip: exact Plan question, correlated Pass answer, and completed turn"
        )
        return 0
    except Exception as error:
        print(
            f"FAIL request_user_input app-server fixture: {type(error).__name__}: {error}"
        )
        return 1
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
