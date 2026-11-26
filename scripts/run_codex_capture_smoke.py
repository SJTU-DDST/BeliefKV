#!/usr/bin/env python3
"""Exercise the pinned Codex app-server and capture bridge on localhost with no GPU."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import subprocess
import sys
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEvent
from scripts.run_codex_subagent_benchmark import main as run_benchmark, write_json


ORIGINAL_TASK = "  Scripted original task.\nLeave this text as supplied.  \n"


def require(condition: Any, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


class ScriptedChatProvider:
    """Return ordinary Chat Completions so Codex itself executes every tool."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict] = []
        self.errors: list[str] = []
        self.steps: Counter[str] = Counter()
        self.release_child = [threading.Event(), threading.Event()]
        self.release_interrupt = threading.Event()
        self.mode = "probe"
        self.children: list[str] = []

    @staticmethod
    def tool_output(body: dict, previous_call: str) -> str:
        return next(
            message["content"]
            for message in reversed(body["messages"])
            if message.get("tool_call_id") == previous_call
        )

    def reply(self, body: dict) -> dict:
        meta = body["beliefkv_metadata"]
        owner = meta["agent_instance_id"]
        rid = body["rid"]
        with self.lock:
            step = self.steps[owner]
            self.steps[owner] += 1
            self.requests.append({"rid": rid, "metadata": meta})
        require(step == meta["context_epoch"], "upstream request epoch differs from dispatch order")
        require(rid == f"codex-{owner}-{step}", "upstream rid differs from capture identity")
        tool = None
        if self.mode == "interrupt":
            require(step == 0 and meta["relation_type"] == "root", "unexpected interrupted request")
            self.release_interrupt.wait(15)
        elif self.mode == "original":
            require(step == 0 and meta["relation_type"] == "root", "unexpected original-task request")
            user_messages = [
                message["content"] for message in body["messages"] if message["role"] == "user"
            ]
            require(
                user_messages and user_messages[-1] == ORIGINAL_TASK,
                "original task prompt changed",
            )
            require(
                not any(
                    "This benchmark requires exactly two spawn_agent calls" in message["content"]
                    for message in body["messages"] if message["role"] == "system"
                ),
                "subagent probe instructions leaked into the original task",
            )
        elif meta["relation_type"] == "root":
            if step in (0, 1):
                if step == 1:
                    first = self.tool_output(body, f"call_{owner}_0")
                    self.children.append(json.loads(first)["agent_id"])
                tool = "spawn_agent", {"message": f"Smoke worker {step + 1}."}
            elif step == 2:
                second = self.tool_output(body, f"call_{owner}_1")
                self.children.append(json.loads(second)["agent_id"])
                tool = "wait_agent", {"targets": self.children, "timeout_ms": 10_000}
            elif step == 3:
                timeout = json.loads(self.tool_output(body, f"call_{owner}_2"))
                require(timeout == {"status": {}, "timed_out": True}, "wrong wait timeout payload")
                self.release_child[0].set()
                tool = "wait_agent", {"targets": self.children, "timeout_ms": 20_000}
            elif step == 4:
                partial = json.loads(self.tool_output(body, f"call_{owner}_3"))
                require(
                    partial["timed_out"] is False and set(partial["status"]) == {self.children[0]},
                    "wait did not return the one completed child",
                )
                self.release_child[1].set()
                tool = "wait_agent", {"targets": [self.children[1]], "timeout_ms": 20_000}
            elif step == 5:
                final = json.loads(self.tool_output(body, f"call_{owner}_4"))
                require(
                    final["timed_out"] is False and set(final["status"]) == {self.children[1]},
                    "singleton wait did not return the last child",
                )
            else:
                raise RuntimeError(f"unexpected root step {step}")
        else:
            require(meta["relation_type"] == "spawn", "child routed as a root")
            if step == 0:
                child_index = next(
                    index for index in (0, 1)
                    if any(
                        message["content"] == f"Smoke worker {index + 1}."
                        for message in body["messages"]
                        if message["role"] == "user"
                    )
                )
                require(self.release_child[child_index].wait(30), "child release timed out")
                tool = "exec_command", {"cmd": "sleep 0.05; printf 'codex-smoke\\n'"}
            elif step == 1:
                output = self.tool_output(body, f"call_{owner}_0")
                require("code 0" in output and "codex-smoke" in output, "shell probe failed")
            else:
                raise RuntimeError(f"unexpected child step {step}")

        message: dict = {"role": "assistant", "content": "Codex smoke complete."}
        if tool:
            name, arguments = tool
            exposed = {item["function"]["name"] for item in body["tools"]}
            require(name in exposed, f"required tool missing from Codex schema: {name}")
            message["content"] = None
            message["tool_calls"] = [{
                "id": f"call_{owner}_{step}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }]
        return {
            "id": f"chatcmpl-{rid}",
            "choices": [{
                "index": 0, "message": message,
                "finish_reason": "tool_calls" if tool else "stop",
            }],
            "usage": {"prompt_tokens": 15, "completion_tokens": 3},
        }

    def http_server(self) -> ThreadingHTTPServer:
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_POST(self) -> None:
                try:
                    require(self.path == "/v1/chat/completions", f"unexpected route: {self.path}")
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    payload = provider.reply(body)
                    status = 200
                except Exception as error:
                    with provider.lock:
                        provider.errors.append(repr(error))
                    payload = {"error": str(error)}
                    status = 500
                data = json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    if provider.mode != "interrupt":
                        with provider.lock:
                            provider.errors.append("unexpected upstream disconnect")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = False
        return server


def records(directory: Path, filename: str) -> list[dict]:
    return [json.loads(line) for line in (directory / filename).read_text().splitlines()]


def verify_completed(run: Path, upstream_requests: list[dict]) -> dict[str, int]:
    runtime = records(run, "runtime_events.codex.jsonl")
    app_server = records(run, "codex_app_server_events.jsonl")
    bridge = records(run, "responses_bridge.jsonl")
    counts = Counter(event["kind"] for event in runtime)
    require(
        all(counts[kind] == expected for kind, expected in {
            "spawn": 2, "join_create": 3, "join_wait": 3, "join_timeout": 1,
            "join_satisfied": 2, "tool_start": 4, "tool_end": 4, "llm_submit": 10,
            "llm_result": 10, "return": 3, "workflow_end": 1,
        }.items()),
        f"unexpected runtime population: {counts}",
    )
    require(
        all(before["ts_ms"] <= after["ts_ms"] for before, after in zip(runtime, runtime[1:])),
        "runtime event clock regressed",
    )
    graph = RuntimeCausalContextGraph()
    graph.apply_batch(tuple(RuntimeEvent.from_dict(event) for event in runtime))
    requests = {row["rid"]: row["metadata"] for row in upstream_requests}
    acknowledgements = {
        row["params"]["responseId"]: (row["params"]["threadId"], row["params"]["turnId"])
        for row in app_server if row["method"] == "rawResponse/completed"
    }
    require(len(acknowledgements) == len(requests) == len(bridge) == 10, "wire counts differ")
    for row in bridge:
        require(row["event"] == "request_completed", "upstream request failed")
        metadata = requests[row["request_id"]]
        require(
            (row["thread_id"], row["turn_id"]) == acknowledgements[row["response_id"]],
            "raw response acknowledgement differs from bridge",
        )
        for key in ("invocation_id", "context_id", "context_epoch"):
            require(row[key] == metadata[key], f"bridge and upstream disagree on {key}")
        if row["parent_invocation_id"]:
            require(row["thread_id"] != row["prompt_cache_key"], "expected child cache-key alias")
    reqs = {
        row["attributes"]["request_id"]: row["sequence"]
        for row in runtime if row["kind"] == "llm_submit"
    }
    spawn_seqs = {
        row["target_invocation_id"]: row["sequence"] for row in runtime if row["kind"] == "spawn"
    }
    first_wait = next(row["sequence"] for row in runtime if row["kind"] == "join_wait")
    require(max(spawn_seqs.values()) < first_wait, "parent waited before both spawns finished")
    for rid, meta in requests.items():
        if meta["relation_type"] == "spawn":
            require(spawn_seqs[meta["invocation_id"]] < reqs[rid], "child dispatched before SPAWN")
    boundary_sizes = [len(row["member_invocation_ids"]) for row in runtime if row["kind"] == "join_wait"]
    require(boundary_sizes == [2, 2, 1], "wait targets were not recorded verbatim")
    for row in runtime:
        if row["kind"] == "join_satisfied":
            require(len(row["member_invocation_ids"]) == 1, "ANY wait lost its actual winner")
        if row["kind"] == "tool_end":
            attr = row["attributes"]
            require(attr.get("request_id") in requests, "tool is missing its triggering request")
            if attr["tool_name"] == "exec_command":
                require(
                    attr["returncode"] == 0 and isinstance(attr["runtime_duration_ms"], int)
                    and attr["duration_ms"] >= 40,
                    "tool duration or status is missing",
                )
    return dict(counts)


def verify_interrupted(run: Path) -> None:
    runtime = records(run, "runtime_events.codex.jsonl")
    app_server = records(run, "codex_app_server_events.jsonl")
    require(
        any(
            row["method"] == "turn/completed" and row["params"]["turn"]["status"] == "interrupted"
            for row in app_server
        ),
        "missing real Codex interrupt completion",
    )
    require(
        [row["kind"] for row in runtime] == [
            "workflow_start", "invocation_create", "llm_submit", "call_censored",
            "invocation_cancel", "workflow_end",
        ],
        "interrupt fabricated a result or lost a cancellation",
    )
    require(
        runtime[2]["attributes"]["request_id"] == runtime[3]["attributes"]["request_id"],
        "cancelled request identity did not match dispatch",
    )
    require(
        runtime[4]["attributes"]["terminal_source"] == "turn/completed",
        "cancellation came from local teardown",
    )
    bridge = records(run, "responses_bridge.jsonl")
    require(
        any("Codex disconnected" in row.get("error", "") for row in bridge),
        "bridge did not observe the interrupted HTTP connection",
    )


def verify_original_task(run: Path, *, sandbox: str) -> None:
    manifest = json.loads((run / "manifest.json").read_text())
    require(
        manifest["capture_mode"] == "original_task"
        and not manifest["subagent_gate"]["enforced"]
        and manifest["subagent_count"] == 0 and manifest["request_count"] == 1,
        "original task was gated on subagent behavior",
    )
    require(
        manifest["root_sandbox_policies"]["codex-cpu-smoke"]["type"] == sandbox,
        "wrong sandbox reported by Codex",
    )
    runtime = records(run, "runtime_events.codex.jsonl")
    require(
        manifest["workflow_task_ids"] == {runtime[0]["workflow_id"]: "codex-cpu-smoke"},
        "harness workflow-to-task binding differs from the event log",
    )
    app_server = records(run, "codex_app_server_events.jsonl")
    prompts = [
        part["text"]
        for row in app_server
        if row["method"] == "item/started"
        and row["params"]["item"]["type"] == "userMessage"
        for part in row["params"]["item"]["content"]
    ]
    require(prompts == [ORIGINAL_TASK], "app-server did not record the original user text")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--codex", default=shutil.which("codex") or "codex")
    parser.add_argument("--task-image", help="Run the tool fixture inside a pinned local task image")
    args = parser.parse_args()
    run_root = args.run_dir.resolve()
    run_root.mkdir(parents=True, exist_ok=False)
    worktree = run_root / "worktree"
    subprocess.run(["git", "init", "--quiet", str(worktree)], check=True)
    subprocess.run([
        "git", "-C", str(worktree), "-c", "user.name=Codex Smoke",
        "-c", "user.email=smoke@localhost", "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "CPU smoke",
    ], check=True)
    commit = subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"], text=True)
    manifest = run_root / "workloads.json"
    write_json(manifest, {"workloads": [{
        "instance_id": "codex-cpu-smoke", "worktree": str(worktree),
        "base_commit": commit.strip(), "problem_statement": ORIGINAL_TASK,
    }]})
    provider = ScriptedChatProvider()
    with provider.http_server() as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        command = [
            "--manifest", str(manifest), "--codex", args.codex, "--concurrency", "1",
            "--instance", "codex-cpu-smoke", "--context-limit", "65536",
            "--model", "codex-cpu-mock", "--bridge-port", "0", "--skip-gpu-monitor",
            "--sglang-base-url", f"http://127.0.0.1:{server.server_port}/v1",
        ]
        isolation = (
            ["--original-task", "--workspace-write", "--task-container", "--task-image", args.task_image]
            if args.task_image else []
        )
        fixture_sandbox = None if isolation else "danger-full-access"
        try:
            # Restricted Linux namespaces may be unavailable. The scripted provider
            # issues only the fixed printf probe in this temporary worktree.
            with contextlib.redirect_stdout(io.StringIO()):
                run_benchmark(
                    command + isolation + ["--run-dir", str(run_root / "completed"), "--timeout-seconds", "40"],
                    fixture_sandbox=fixture_sandbox,
                )
            require(not provider.errors, f"mock provider errors: {provider.errors}")
            counts = verify_completed(run_root / "completed", provider.requests)
            provider.mode = "original"
            for sandbox, flags in (("readOnly", []), ("workspaceWrite", ["--workspace-write"])):
                original_run = run_root / sandbox
                with contextlib.redirect_stdout(io.StringIO()):
                    run_benchmark(command + [
                        "--run-dir", str(original_run), "--timeout-seconds", "10", "--original-task",
                        *flags,
                    ])
                verify_original_task(original_run, sandbox=sandbox)
            provider.mode = "interrupt"
            try:
                run_benchmark(
                    command + isolation + ["--run-dir", str(run_root / "interrupted"), "--timeout-seconds", "2"],
                    fixture_sandbox=fixture_sandbox,
                )
            except TimeoutError:
                pass
            else:
                raise RuntimeError("stalled provider did not reach the benchmark deadline")
            verify_interrupted(run_root / "interrupted.incomplete")
        finally:
            provider.release_interrupt.set()
            for release in provider.release_child:
                release.set()
            server.shutdown()
            thread.join()
    require(not provider.errors, f"mock provider errors: {provider.errors}")
    write_json(run_root / "smoke_summary.json", {
        "event_counts": counts, "interruption_verified": True,
        "original_task_sandboxes": ["readOnly", "workspaceWrite"],
        "task_image": args.task_image,
        "upstream_requests": provider.requests,
    })
    print(f"Codex CPU capture smoke passed: {run_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
