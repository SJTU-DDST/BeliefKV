"""Deterministic local OpenAI-compatible streaming fixture for OpenCode."""

from collections import Counter
from http.server import BaseHTTPRequestHandler
import json
import re
import select
import socket
import threading
import time


def fake_handler(scenario):
    counts = Counter()
    calls_changed = threading.Condition()

    class FakeModel(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            session = self.headers["x-opencode-session-id"]
            parent = self.headers["x-opencode-parent-session-id"]
            metadata = body.get("beliefkv_metadata") or {}
            if (
                not session or not self.headers["x-opencode-request"] or body["stream"] is not True
                or not body["stream_options"]["include_usage"] or not body.get("rid")
                or metadata.get("agent_instance_id") != session
                or metadata.get("parent_context_id") != (f"opencode-context:{parent}" if parent else None)
            ):
                self.send_error(400)
                return
            with calls_changed:
                counts[session] += 1
                turn = counts[session]
                calls_changed.notify_all()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            response_id = f"fixture-{session}-{turn}"

            def frame(delta, finish=None):
                return {
                    "id": response_id, "object": "chat.completion.chunk", "created": 1,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }

            def tool(index, name, arguments):
                assert any(entry["function"]["name"] == name for entry in body["tools"])
                return {
                    "index": index, "id": f"{response_id}-{index}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }

            def line(value):
                return b"data: " + json.dumps(value).encode() + b"\n\n"

            calls = []
            if not parent and turn == 2 and scenario == "reuse":
                child_ids = {
                    match for message in body["messages"] if message["role"] == "tool"
                    for match in re.findall(r'<task id="(ses_[A-Za-z0-9]+)" state="completed">', message["content"])
                }
                assert len(child_ids) == 1
                calls = [tool(0, "task", {
                    "description": "Continue prior child", "prompt": "beliefkv-reused-child",
                    "subagent_type": "general", "task_id": child_ids.pop(),
                })]
            elif not parent and turn == 1 and scenario != "cancel":
                modes = {
                    "foreground": (False, False),
                    "background": (True, True),
                    "mixed": (False, True),
                    "cancel-task": (False,),
                    "promote": (False,),
                    "reuse": (False,),
                    "stream-tool": (True,),
                }[scenario]
                calls = [
                    tool(index, "task", {
                        "description": f"Check fixture sibling {index}",
                        "prompt": f"beliefkv-child-{index}", "subagent_type": "general",
                        "background": background,
                    })
                    for index, background in enumerate(modes)
                ]
            try:
                if scenario == "cancel" or (scenario == "cancel-task" and parent):
                    self.wfile.write(line(frame({"role": "assistant", "content": "partial response"})))
                    readable, _, _ = select.select([self.connection], [], [], 4.)
                    if not readable or self.connection.recv(1, socket.MSG_PEEK):
                        raise RuntimeError("OpenCode bridge did not disconnect the cancelled upstream")
                    return
                if parent:
                    first_child = any(
                        "beliefkv-child-0" in str(message["content"])
                        for message in body["messages"] if message["role"] == "user"
                    )
                    if scenario == "foreground":
                        with calls_changed:
                            assert calls_changed.wait_for(lambda: len(counts) == 3, 6.)
                    if scenario in {"background", "promote"} or (scenario == "mixed" and not first_child):
                        with calls_changed:
                            assert calls_changed.wait_for(lambda: counts[parent] >= 2, 6.)
                    time.sleep(0.08 if first_child and scenario in {"foreground", "mixed"} else 0.25)
                delta = {"role": "assistant"}
                if calls:
                    delta["tool_calls"] = calls
                else:
                    delta["content"] = "child finished" if parent else "parent finished"
                usage = {
                    "prompt_tokens": 31 + turn,
                    "completion_tokens": 7 if calls else 3,
                    "total_tokens": 31 + turn + (7 if calls else 3),
                    "prompt_tokens_details": {"cached_tokens": 0},
                }
                if scenario == "stream-tool" and calls:
                    [call] = calls
                    name, arguments = call["function"]["name"], call["function"]["arguments"]
                    first = {**call, "function": {"name": name, "arguments": arguments[:-2]}}
                    last = {"index": call["index"], "function": {"arguments": arguments[-2:]}}
                    self.wfile.write(line({
                        **frame({"role": "assistant", "tool_calls": [first]}),
                        "usage": {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": 2},
                    }))
                    self.wfile.write(line({
                        **frame({"tool_calls": [last]}),
                        "usage": {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": 5},
                    }))
                    with calls_changed:
                        assert calls_changed.wait_for(lambda: len(counts) == 2, 8.)
                    published = line(frame({"content": " started"}))
                else:
                    published = line(frame(delta))
                self.wfile.write(
                    published + line(frame({}, "tool_calls" if calls else "stop"))
                    + line({**frame({}), "choices": [], "usage": usage}) + b"data: [DONE]\n\n"
                )
            except (BrokenPipeError, ConnectionResetError):
                pass

    return FakeModel
