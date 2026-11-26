#!/usr/bin/env python3
"""Capture one OpenHands conversation or exercise the pinned SDK on localhost."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import ContextMode, RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import evaluate_causal_plan, event_node_id
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.openhands_capture import (
    MARS_REFERENCE_COMMIT, OPENHANDS_COMMIT, OpenHandsCapture,
    compile_openhands_capture, require_openhands,
)
from beliefkv.runtime.replay_token_capture import native_request_expectations, verify_capture
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata

EVENTS_FILENAME = "runtime_events.openhands.jsonl"
FIXTURE_FILENAME = "runtime_events.openhands_fake_model.jsonl"
FIXTURE_SOURCE = "openhands_localhost_model_fixture"
CAPTURE_SHELL = Path(__file__).resolve().with_name("openhands_capture_shell.sh")


def _setup_sdk():
    versions = require_openhands()
    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    return {**versions, "litellm": version("litellm"), "openai": version("openai")}


def _agent(model, base_url, api_key, *, stream=True, max_output_tokens=512):
    from openhands.sdk import Agent, LLM, Tool
    from openhands.sdk.subagent import register_agent_if_absent
    from openhands.tools.delegate import DelegateTool
    from openhands.tools.preset.default import get_default_tools
    from openhands.tools.terminal import TerminalTool
    from pydantic import SecretStr

    def build(llm, *, root=False):
        tools = [
            Tool(name=tool.name, params={
                "terminal_type": "subprocess", "shell_path": str(CAPTURE_SHELL),
            })
            if tool.name == TerminalTool.name else tool
            for tool in get_default_tools(enable_browser=False)
        ]
        if root:
            tools.append(Tool(name=DelegateTool.name))
        return Agent(llm=llm, tools=tools, system_prompt_kwargs={"cli_mode": True}, condenser=None)

    register_agent_if_absent("default", build, "General-purpose OpenHands CLI worker")
    return build(LLM(
        model=model if "/" in model else f"openai/{model}",
        base_url=base_url, api_key=SecretStr(api_key), stream=stream,
        native_tool_calling=True, force_string_serializer=True,
        timeout=30, num_retries=0, max_output_tokens=max_output_tokens,
    ), root=True)


def _jsonl(stream, row):
    stream.write(json.dumps(row, allow_nan=False) + "\n")
    stream.flush()


def _read_jsonl(path):
    with path.open() as stream:
        for line in stream:
            yield json.loads(line)


def _capture(output, agent, workspace, prompt, workflow_id, *, task_id=None, observer=None,
             max_iterations=100):
    with (output / EVENTS_FILENAME).open("x") as stream:
        def publish(event):
            _jsonl(stream, event.to_dict())
            if observer is not None:
                observer(event)

        capture = OpenHandsCapture(workflow_id, task_id=task_id, publish=publish)
        status = capture.run(agent, workspace, prompt, max_iterations=max_iterations)
    return status, capture.events


class _FixtureModel:
    """Return deterministic completions while the SDK executes real tools."""

    def __init__(self, workspace, sdk_tools):
        self.workspace = workspace
        self.sdk_tools = sdk_tools
        self.requests = {}
        self.events = []
        self.turn = Counter()
        self.errors = []
        self.lock = threading.Lock()
        self.both_children_in_http = threading.Barrier(2, timeout=15)
        self.sdk_wait_entered = threading.Event()
        self.clock_domain = local_monotonic_clock_domain()

    def observe(self, event):
        if event.kind is Kind.JOIN_WAIT and len(event.attributes["live_members_at_entry"]) == 2:
            self.sdk_wait_entered.set()

    def _wire_event(self, kind, rid, meta, **attributes):
        return {
            "kind": kind.value, "workflow_id": meta.root_workflow_id,
            "invocation_id": meta.invocation_id, "context_id": meta.context_id,
            "context_epoch": meta.context_epoch, "attributes": {
                "request_id": rid, "source": FIXTURE_SOURCE, **attributes,
            },
        }

    def reply(self, body):
        rid = body["rid"]
        meta = BeliefKVRequestMetadata.from_wire(body["beliefkv_metadata"])
        if not isinstance(rid, str) or not rid.startswith("openhands-"):
            raise ValueError("fixture expected an injected OpenHands rid")
        streaming = bool(body.get("stream"))
        root = meta.parent_invocation_id is None
        if streaming != root or (streaming and not body["stream_options"]["include_usage"]):
            raise ValueError("expected root SSE with usage and SDK-native non-streaming children")
        with self.lock:
            if rid in self.requests:
                raise ValueError("duplicate request ID on the wire")
            self.turn[meta.invocation_id] += 1
            turn = self.turn[meta.invocation_id]
            self.requests[rid] = (meta, streaming)
            usage = {"prompt_tokens": 40 + turn, "completion_tokens": 8}
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
            self.events.append(self._wire_event(
                Kind.LLM_SUBMIT, rid, meta, prompt_tokens=usage["prompt_tokens"],
            ))

        content, tools = self._script(body["messages"], meta, turn)
        names = {tool["function"]["name"] for tool in body["tools"]}
        if any(name not in names for name, _ in tools):
            raise ValueError("fixture called a tool missing from the SDK request schema")
        calls = [{
            "id": f"call_{rid}_{index}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        } for index, (name, arguments) in enumerate(tools)]
        finish = "tool_calls" if calls else "stop"
        with self.lock:
            self.events.append(self._wire_event(
                Kind.LLM_RESULT, rid, meta, output_tokens=usage["completion_tokens"],
                complete_monotonic_ms=time.monotonic_ns() / 1e6,
                monotonic_clock_domain=self.clock_domain,
            ))
        return streaming, rid, content, calls, finish, usage

    def _script(self, messages, meta, turn):
        terminal, editor, tracker, delegate, finish = self.sdk_tools
        payload = str(self.workspace / "payload.txt")
        tool_history = "\n".join(message["content"] for message in messages
                                 if message["role"] == "tool")
        view = (editor, {"command": "view", "path": payload})
        if meta.parent_invocation_id is None:
            if turn == 1:
                return None, [
                    (terminal, {"command": "printf OPENHANDS-SMOKE", "timeout": 5}),
                    (tracker, {"command": "view"}),
                ]
            if turn == 2 and "OPENHANDS-SMOKE" in tool_history and "No task list" in tool_history:
                return None, [(delegate, {"command": "spawn", "ids": ["left", "right"]})]
            if turn == 3 and "Successfully spawned 2" in tool_history:
                return None, [(delegate, {"command": "wait"})]
            if turn == 4 and "Error validating args" in tool_history:
                return None, [
                    (delegate, {"command": "delegate", "tasks": {
                        "left": "Read the payload as left worker.",
                        "right": "Read the payload as right worker.",
                    }}),
                    view,
                ]
            if turn == 5 and all(value in tool_history for value in (
                "Agent left: left done", "Agent right: right done", "OPENHANDS-PAYLOAD",
            )):
                return None, [(delegate, {"command": "delegate", "tasks": {
                    "left": "Read the payload again as left worker.",
                }})]
            if turn == 6 and "Agent left: left reused" in tool_history:
                return None, [(finish, {"message": "Root verified the tool results."})]
        else:
            if turn == 1:
                if meta.context_mode == "fresh":
                    self.both_children_in_http.wait()
                    if not self.sdk_wait_entered.wait(timeout=15):
                        raise ValueError("SDK did not reach its native wait with two live children")
                return None, [view]
            if turn == 2 and "OPENHANDS-PAYLOAD" in tool_history:
                task = next(message["content"] for message in reversed(messages)
                            if message["role"] == "user")
                if meta.context_mode == "resume":
                    return "left reused", []
                if "left worker" in task:
                    return None, [(finish, {"message": "left done"})]
                if "right worker" in task:
                    return "right done", []
        raise ValueError(f"unexpected SDK transcript at {meta.relation_type} turn {turn}")


def _chat_response(stream, rid, content, calls, finish, usage):
    common = {"id": rid, "created": int(time.time()), "model": "gpt-4o-mini"}
    if not stream:
        message = {"role": "assistant", "content": content}
        if calls:
            message["tool_calls"] = calls
        return "application/json", json.dumps({
            **common, "object": "chat.completion", "usage": usage,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        }).encode()
    common["object"] = "chat.completion.chunk"
    delta = {"role": "assistant", "content": content}
    if calls:
        delta["tool_calls"] = [{"index": index, **call} for index, call in enumerate(calls)]
    frames = (
        {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {**common, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        {**common, "choices": [], "usage": usage},
    )
    return "text/event-stream", (
        "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
    ).encode()


@contextmanager
def _serve_fixture(fixture):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                if self.path != "/v1/chat/completions":
                    raise ValueError(f"unexpected OpenAI-compatible path: {self.path}")
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                content_type, raw = _chat_response(*fixture.reply(body))
            except Exception as error:
                with fixture.lock:
                    fixture.errors.append(str(error))
                self.send_error(400, "OpenHands smoke transcript failed")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, name="openhands-smoke-http")
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1"
        finally:
            server.shutdown()
            thread.join()


def _verify_smoke(events, fixture):
    def check(condition, message):
        if not condition:
            raise ValueError(message)

    results = {event.attributes["request_id"]: event for event in events
               if event.kind is Kind.LLM_RESULT}
    expected = native_request_expectations(fixture.events)
    check(len(results) == 12 and results.keys() == fixture.requests.keys(), "missing HTTP requests")
    for rid, result in results.items():
        attrs = result.attributes
        check(attrs["rid_echoed"] and (
            attrs["prompt_tokens"], attrs["output_tokens"],
        ) == (expected[rid]["llm_submit"][1], expected[rid]["llm_result"][1]),
            f"unbound response ID or usage for {rid}")

    starts = [event for event in events if event.kind is Kind.TOOL_START]
    ends = [event for event in events if event.kind is Kind.TOOL_END]
    launches = [event for event in events if event.kind is Kind.SPAWN]
    waits = [event for event in events if event.kind is Kind.JOIN_WAIT]
    spawn_end = next(event for event in ends if event.attributes.get("sdk_delegate_command") == "spawn")
    check(len(starts) == len(ends) == 11 and all(event.attributes["outcome"] == "ok" for event in ends),
          "missing SDK tool execution")
    check(len(launches) == 3 and len(waits) == 2 and len(waits[0].attributes["live_members_at_entry"]) == 2,
          "missing SDK thread launches or observed ALL waits")
    check(spawn_end.ts_ms < launches[0].ts_ms, "idle SDK spawn was conflated with task launch")
    idle_children = [event for event in events if "sdk_child_context_id" in event.attributes]
    check(len(idle_children) == 2
          and all(event.ts_ms <= spawn_end.ts_ms for event in idle_children)
          and {event.attributes["sdk_child_context_id"] for event in idle_children}
          == {event.target_context_id for event in launches}, "idle SDK child identities do not match")
    check(sum(event.attributes.get("sdk_event_type") == "AgentErrorEvent" for event in events) == 1,
          "SDK did not reject the unsupported standalone wait action")
    created = [event for event in events if event.kind is Kind.INVOCATION_CREATE
               and event.attributes["sdk_agent_handle"] == "left"]
    check(len(created) == 2 and created[0].context_id == created[1].context_id
          and created[1].context_mode is ContextMode.RESUME
          and created[0].invocation_id != created[1].invocation_id, "child context reuse is missing")

    plan = compile_openhands_capture(events, fixture.events)
    zeros = dict.fromkeys(plan.request_usage, 0.)
    fast = evaluate_causal_plan(plan, zeros)["node_completed_ms"]
    slowed = dict(zeros)
    slowed[next(rid for rid, result in results.items()
                if result.invocation_id == created[0].invocation_id)] = 5000.
    slow = evaluate_causal_plan(plan, slowed)["node_completed_ms"]
    parent_post_wait_tool = next(
        event for event in starts if event.invocation_id == waits[0].invocation_id
        and event.ts_ms > waits[0].ts_ms
        and event.attributes["tool_name"] == fixture.sdk_tools[1]
    )
    key = event_node_id(parent_post_wait_tool.event_id)
    check(slow[key] > fast[key], "parent tool can replay before the slow child has joined")
    return plan.summary()


def smoke(output: Path) -> dict:
    packages = _setup_sdk()
    from openhands.sdk.tool.builtins.finish import FinishTool
    from openhands.tools.delegate import DelegateTool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    output.mkdir(parents=True)
    workspace = (output / "workspace").resolve()
    workspace.mkdir()
    (workspace / "payload.txt").write_text("OPENHANDS-PAYLOAD\n", encoding="utf-8")
    fixture = _FixtureModel(workspace, (
        TerminalTool.name, FileEditorTool.name, TaskTrackerTool.name, DelegateTool.name, FinishTool.name,
    ))
    with _serve_fixture(fixture) as base_url:
        status, events = _capture(
            output, _agent("gpt-4o-mini", base_url, "localhost-fixture"),
            workspace, "Verify the local payload using the available tools.",
            "openhands-smoke", task_id="openhands-localhost-smoke", observer=fixture.observe,
        )
    if status != "finished" or fixture.errors:
        raise ValueError(f"OpenHands smoke failed: {status}, {fixture.errors}")
    with (output / FIXTURE_FILENAME).open("x") as stream:
        for event in fixture.events:
            _jsonl(stream, event)
    summary = _verify_smoke(events, fixture)
    summary["fixture_completion_boundaries_verified"] = summary.pop("native_completion_boundaries_verified")
    return _receipt(output, {
        **summary, "sdk_status": status, "packages": packages,
        "scope": "Real SDK conversation, tools and delegation with scripted local model usage and timing.",
        "source_scope": "real_local_sdk_with_scripted_localhost_chat",
        "native_completion_boundaries_verified": False, "token_evidence_complete": False,
        "parallel_child_http_requests_verified": True, "standalone_wait_rejected": True,
        "fixture_server_events": FIXTURE_FILENAME,
    })


def _receipt(output, summary):
    receipt = {
        **summary, "openhands_commit": OPENHANDS_COMMIT, "mars_reference_commit": MARS_REFERENCE_COMMIT,
        "runtime_events": EVENTS_FILENAME, "monotonic_clock_domain": local_monotonic_clock_domain(),
    }
    with (output / "openhands_capture_receipt.json").open("x") as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("smoke", help="Run the real SDK against a localhost chat fixture")
    preflight.add_argument("--output", type=Path, required=True)
    capture = commands.add_parser("capture", help="Capture one trusted local Agent workload")
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--workspace", type=Path, required=True)
    capture.add_argument("--prompt-file", type=Path, required=True)
    capture.add_argument("--base-url", required=True)
    capture.add_argument("--model", required=True)
    capture.add_argument("--workflow-id")
    capture.add_argument("--task-id")
    capture.add_argument("--max-iterations", type=int, default=100)
    capture.add_argument("--max-output-tokens", type=int, default=512)
    capture.add_argument("--no-stream", action="store_true")
    compile_cmd = commands.add_parser("compile", help="Bind captured events to native server receipts")
    compile_cmd.add_argument("--events", type=Path, required=True)
    compile_cmd.add_argument("--server-events", type=Path, required=True)
    compile_cmd.add_argument("--request-tokens", type=Path)
    args = parser.parse_args()

    if args.command == "smoke":
        result = smoke(args.output)
    elif args.command == "capture":
        packages = _setup_sdk()
        prompt = args.prompt_file.read_bytes().decode("utf-8")
        agent = _agent(
            args.model, args.base_url, os.environ.get("OPENAI_API_KEY", "none"),
            stream=not args.no_stream, max_output_tokens=args.max_output_tokens,
        )
        args.output.mkdir(parents=True)
        status, events = _capture(
            args.output, agent, args.workspace.resolve(), prompt,
            args.workflow_id or f"openhands-{uuid4().hex}", task_id=args.task_id,
            max_iterations=args.max_iterations,
        )
        result = _receipt(args.output, {
            "sdk_status": status, "packages": packages,
            "source_scope": "local_sdk_client_capture", "token_evidence_complete": False,
            "event_counts": dict(Counter(event.kind.value for event in events)),
        })
        if status != "finished":
            print(json.dumps(result, indent=2))
            raise SystemExit(2)
    else:
        events = map(RuntimeEvent.from_dict, _read_jsonl(args.events))
        native = tuple(_read_jsonl(args.server_events))
        if any(row.get("attributes", {}).get("source") == FIXTURE_SOURCE for row in native):
            parser.error("fixture receipts belong to the smoke command")
        result = compile_openhands_capture(events, native).summary()
        if args.request_tokens is None:
            result["token_evidence_complete"] = False
        else:
            result.update(verify_capture(_read_jsonl(args.request_tokens), native_request_expectations(native)))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
