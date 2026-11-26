#!/usr/bin/env python3
import argparse
import asyncio
import contextlib
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.runtime.codex_adapter import (
    CodexRuntimeAdapter,
    CodexThreadRegistry,
    MultiplexRuntimeEventSink,
)
from beliefkv.runtime.codex_app_server import (
    CodexAppServerClient,
    CodexAppServerError,
    verify_codex_capture_binary,
)
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.event_channel import (
    JsonlRuntimeEventSink,
    UnixDatagramRuntimeEventSink,
)
from beliefkv.runtime.responses_bridge import (
    ResponsesBridgeError,
    chat_response_to_responses_events,
    codex_turn_metadata,
    encode_responses_sse,
    responses_to_chat_request,
)
from beliefkv.runtime.responses_server import (
    ResponsesChatBridgeServer,
    sensitive_request_header_names,
)


DEFAULT_MANIFEST = (
    REPOSITORY_ROOT / "configs/workloads/codex_swebench_sympy_subagents.json"
)
PROBE_DEVELOPER_INSTRUCTIONS = (
    "This benchmark requires exactly two spawn_agent calls. After both spawns, "
    "use wait_agent with targets containing only the first child ID and timeout_ms=120000. "
    "Then wait for the second child alone. Retry any timeout and wait for both terminal "
    "results before synthesizing. Never edit files."
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


class AppServerAudit:
    def __init__(self, path: Path) -> None:
        self.stream = path.open("x", encoding="utf-8", buffering=1)
        self.lock = threading.Lock()
        self.sequence = 0

    def emit(self, message: dict[str, Any], *, observed_ts_ms: float) -> None:
        sanitized = copy.deepcopy(message)
        params = sanitized.get("params")
        item = params.get("item") if isinstance(params, dict) else None
        if isinstance(item, dict):
            for field in ("aggregatedOutput", "output"):
                if item.get(field) is not None:
                    output = item.pop(field)
                    text = output if isinstance(output, str) else json.dumps(output, sort_keys=True)
                    item[field + "Chars"] = len(text)
                    item[field + "Sha256"] = hashlib.sha256(text.encode()).hexdigest()
        if (
            isinstance(params, dict)
            and message.get("method") == "item/commandExecution/outputDelta"
        ):
            delta = str(params.pop("delta", ""))
            params["deltaChars"] = len(delta)
        with self.lock:
            self.sequence += 1
            payload = {
                "schema_version": 1,
                "sequence": self.sequence,
                "observed_ts_ms": observed_ts_ms,
                **sanitized,
            }
            self.stream.write(
                json.dumps(payload, sort_keys=True, allow_nan=False) + "\n"
            )

    def close(self) -> None:
        self.stream.close()


class CodexCaptureBridge(ResponsesChatBridgeServer):
    """Capture client boundaries around real upstream Chat Completions."""

    def __init__(self, adapter: CodexRuntimeAdapter, **kwargs: Any) -> None:
        super().__init__(adapter.registry, **kwargs)
        self.adapter = adapter

    @property
    def base_url(self) -> str:
        if self.port == 0 and self._server is not None and self._server.started:
            port = self._server.servers[0].sockets[0].getsockname()[1]
            return f"http://{self.host}:{port}/v1"
        return super().base_url

    def create_app(self):
        import httpx
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse, Response

        app = FastAPI()

        async def upstream_call(raw_request: Request, body: dict[str, Any]):
            async with httpx.AsyncClient(timeout=self.request_timeout_s, trust_env=False) as client:
                post = asyncio.create_task(client.post(
                    f"{self.upstream_base_url}/chat/completions", json=body,
                ))
                try:
                    while not post.done():
                        await asyncio.wait({post}, timeout=0.1)
                        if not post.done() and await raw_request.is_disconnected():
                            raise ResponsesBridgeError("Codex disconnected during the upstream request")
                    upstream = await post
                    upstream.raise_for_status()
                    return upstream.json()
                finally:
                    if not post.done():
                        post.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await post

        async def responses(raw_request: Request):
            started = time.monotonic()
            request = None
            try:
                if sensitive_request_header_names(raw_request.headers):
                    raise ResponsesBridgeError("credential-bearing request headers are forbidden")
                body = await raw_request.json()
                if not isinstance(body, dict):
                    raise ResponsesBridgeError("Responses body must be an object")
                turn = codex_turn_metadata(body)
                thread_id, turn_id = turn.get("thread_id"), turn.get("turn_id")
                if not all(isinstance(value, str) and value for value in (thread_id, turn_id)):
                    raise ResponsesBridgeError("Codex request is missing canonical thread/turn metadata")
                if turn.get("request_kind") != "turn" or body.get("previous_response_id"):
                    raise ResponsesBridgeError("capture requires a full-history turn request")
                client_request_id = raw_request.headers.get("x-client-request-id")
                if client_request_id != thread_id:
                    raise ResponsesBridgeError("Codex HTTP client ID disagrees with thread metadata")
                request = await asyncio.to_thread(
                    self.adapter.request_identity, thread_id, turn_id, turn.get("parent_thread_id")
                )
                converted = responses_to_chat_request(
                    body, request.metadata,
                    max_completion_tokens=self.max_completion_tokens,
                    request_id=request.request_id,
                )
                unexpected = set(converted.unsupported_tool_types) - self.ignored_tool_types
                if unexpected:
                    raise ResponsesBridgeError(f"unsupported Codex tool types: {sorted(unexpected)}")
                message_count = len(converted.chat_request["messages"])
                body_digest = hashlib.sha256(
                    json.dumps(body, sort_keys=True, allow_nan=False).encode()
                ).hexdigest()
                await asyncio.to_thread(
                    self.adapter.submit_request, request,
                    message_count=message_count, body_sha256=body_digest,
                )
                chat = await upstream_call(raw_request, converted.chat_request)
                aliases = dict(converted.tool_name_aliases)
                events = chat_response_to_responses_events(
                    chat, body, tool_name_aliases=aliases,
                    tool_namespaces=dict(converted.tool_namespaces),
                    response_id=request.response_id,
                )
                payload = encode_responses_sse(events)
                output = events[-1]["response"]["output"]
                calls = [item for item in output if item["type"] == "function_call"]
                tools = [call["name"] for call in calls]
                usage = chat.get("usage") or {}
                finish_reason = chat["choices"][0].get("finish_reason")
                published = await asyncio.to_thread(
                    self.adapter.complete_request, request,
                    tool_call_ids=tuple(call["call_id"] for call in calls),
                    tool_names=tools, finish_reason=finish_reason,
                    output_tokens=usage.get("completion_tokens"),
                )
                if not published:
                    raise ResponsesBridgeError("upstream result arrived after Codex cancellation")
                meta = request.metadata
                self._audit(
                    "request_completed",
                    thread_id=thread_id, turn_id=turn_id,
                    workflow_id=meta.root_workflow_id,
                    invocation_id=meta.invocation_id, context_id=meta.context_id,
                    context_epoch=meta.context_epoch,
                    parent_invocation_id=meta.parent_invocation_id,
                    request_id=request.request_id, response_id=request.response_id,
                    upstream_response_id=chat.get("id"),
                    client_request_id=client_request_id,
                    prompt_cache_key=body.get("prompt_cache_key"),
                    codex_sandbox_mode=turn.get("sandbox_mode"),
                    output_item_ids=[item["id"] for item in output],
                    tool_call_ids=[call["call_id"] for call in calls],
                    input_items=len(body.get("input") or []), chat_messages=message_count,
                    collaboration_tool_contracts=self._collaboration_tool_contracts(
                        converted.chat_request.get("tools") or []
                    ),
                    ignored_tool_types=converted.unsupported_tool_types,
                    tool_names=tools, finish_reason=finish_reason,
                    upstream_called=True, join_guard_injected=False,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    duration_ms=(time.monotonic() - started) * 1000.0,
                    body_sha256=body_digest,
                )
                return Response(content=payload, media_type="text/event-stream")
            except (Exception, asyncio.CancelledError) as error:
                if request is not None:
                    await asyncio.to_thread(
                        self.adapter.censor_request, request, reason=type(error).__name__
                    )
                self._audit(
                    "bridge_error",
                    request_id=request.request_id if request is not None else None,
                    error=f"{type(error).__name__}: {error}",
                )
                if isinstance(error, asyncio.CancelledError):
                    raise
                return JSONResponse(
                    {"error": {"message": str(error), "type": "capture_error"}}, status_code=400
                )

        app.post("/v1/responses")(responses)
        return app


class GPUStatsMonitor:
    def __init__(self, gpu_index: int, output_path: Path) -> None:
        self.gpu_index = gpu_index
        self.output_path = output_path
        self.process: subprocess.Popen[str] | None = None
        self.stream = None

    def start(self) -> None:
        self.stream = self.output_path.open("x", encoding="utf-8")
        command = [
            "nvidia-smi",
            "-i",
            str(self.gpu_index),
            "--query-gpu=timestamp,memory.used,memory.free,utilization.gpu,utilization.memory,power.draw",
            "--format=csv,noheader,nounits",
            "--loop-ms=250",
        ]
        self.process = subprocess.Popen(
            command,
            stdout=self.stream,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def close(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5.0)
            self.process = None
        if self.stream is not None:
            self.stream.close()
            self.stream = None


def git_head(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def build_prompt(workload: dict[str, Any]) -> str:
    return f"""Read-only SWE-bench diagnosis for {workload['instance_id']}.

You must exercise the Codex subagent runtime exactly as follows:
1. Call spawn_agent exactly twice before doing the final synthesis.
2. Ask one subagent to trace the likely implementation path and relevant symbols.
3. Ask the other subagent to inspect tests, reproduction paths, and regression risks.
4. After both children are spawned, call wait_agent with targets containing only the
   first child ID and timeout_ms=120000. Once it is terminal, wait for the second
   child alone. Retry the same child whenever a wait returns no terminal result.
5. Produce a concise diagnosis that cites concrete files and symbols.

Do not edit files. Do not apply a patch. Read-only shell inspection is allowed. Do not
replace subagents with your own sequential analysis.

SWE-bench problem statement:
{workload['problem_statement']}
"""


def codex_command(codex: str, bridge_url: str, model: str, *, context_limit: int = 32768) -> list[str]:
    return [
        codex,
        "app-server",
        "--stdio",
        "--enable",
        "multi_agent",
        "--disable",
        "multi_agent_v2",
        "--disable",
        "enable_request_compression",
        "--disable",
        "image_generation",
        "--disable",
        "browser_use",
        "--disable",
        "plugins",
        "--disable",
        "remote_plugin",
        "--disable",
        "plugin_sharing",
        "--disable",
        "apps",
        "-c",
        'model_provider="sglang"',
        "-c",
        f"model={json.dumps(model)}",
        "-c",
        'model_providers.sglang.name="SGLang"',
        "-c",
        f"model_providers.sglang.base_url={json.dumps(bridge_url)}",
        "-c",
        'model_providers.sglang.wire_api="responses"',
        "-c",
        "model_providers.sglang.requires_openai_auth=false",
        "-c",
        "model_providers.sglang.supports_websockets=false",
        "-c",
        "model_providers.sglang.request_max_retries=0",
        "-c",
        "model_providers.sglang.stream_max_retries=0",
        "-c",
        'web_search="disabled"',
        "-c",
        f"model_context_window={context_limit}",
        "-c",
        'approval_policy="never"',
    ]


def main(argv: list[str] | None = None, *, fixture_sandbox: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture Codex app-server workflows against an SGLang provider."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--sglang-base-url", default="http://127.0.0.1:18000/v1")
    parser.add_argument("--bridge-port", type=int, default=18080)
    parser.add_argument("--event-socket", type=Path)
    parser.add_argument("--model", default="Qwen2.5-14B-Instruct")
    parser.add_argument("--codex", default=shutil.which("codex") or "codex")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--instance", action="append", help="Select a task ID from the frozen manifest.")
    parser.add_argument("--context-limit", type=int, default=32768)
    parser.add_argument("--max-completion-tokens", type=int, default=768)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=7200.0,
        help="Shared deadline for all roots and subagents (default: 7200 seconds).",
    )
    parser.add_argument("--gpu-index", type=int, default=0)
    parser.add_argument("--min-subagents-per-workflow", type=int, default=2)
    parser.add_argument(
        "--original-task", action="store_true",
        help="Submit each problem_statement verbatim and record natural delegation.",
    )
    parser.add_argument(
        "--workspace-write", action="store_true",
        help="Allow workspace writes for --original-task. The default is read-only.",
    )
    parser.add_argument("--task-container", action="store_true", help="Run one task in its pinned local image.")
    parser.add_argument("--task-image", help="Immutable local image ID when the task manifest has no image.")
    parser.add_argument("--task-python", default="/opt/miniconda3/envs/testbed/bin/python")
    parser.add_argument(
        "--skip-gpu-monitor", action="store_true", help="Skip starting nvidia-smi.",
    )
    args = parser.parse_args(argv)
    if args.concurrency <= 0 or args.timeout_seconds <= 0:
        parser.error("concurrency and timeout must be positive")
    if not 0 < args.max_completion_tokens < args.context_limit:
        parser.error("completion cap must be positive and below the context limit")
    if args.workspace_write and not args.original_task:
        parser.error("--workspace-write requires --original-task")
    if args.task_container and (not args.original_task or args.concurrency != 1 or fixture_sandbox):
        parser.error("--task-container requires one original task")
    if args.task_image and not args.task_container:
        parser.error("--task-image requires --task-container")
    sandbox = fixture_sandbox or ("workspace-write" if args.workspace_write else "read-only")

    binary = verify_codex_capture_binary(args.codex)
    manifest_path = args.manifest.resolve()
    manifest_data = manifest_path.read_bytes()
    manifest = json.loads(manifest_data)
    if args.instance:
        indexed = {workload["instance_id"]: workload for workload in manifest["workloads"]}
        if len(indexed) != len(manifest["workloads"]) or any(
            instance not in indexed for instance in args.instance
        ):
            raise ValueError("require unique manifest entries for the specified tasks")
        workloads = [indexed[instance] for instance in args.instance]
    else:
        workloads = list(manifest["workloads"][: args.concurrency])
    if len(workloads) != args.concurrency:
        raise ValueError("selected task count does not match the requested concurrency")
    task_image = None
    if args.task_container:
        task_image = workloads[0].get("docker_image") or args.task_image
        if not task_image:
            raise ValueError("task container needs the pinned project image")
        if args.task_image and args.task_image != task_image:
            raise ValueError("requested image differs from the pinned project image")
        sandbox = "danger-full-access"  # Filesystem and network are confined by the task container.
    instance_ids = [workload["instance_id"] for workload in workloads]
    if len(set(instance_ids)) != len(instance_ids):
        raise ValueError("manifest contains duplicate selected task IDs")
    for workload in workloads:
        worktree = Path(workload["worktree"]).resolve()
        if git_head(worktree) != workload["base_commit"]:
            raise RuntimeError(f"worktree commit mismatch: {workload['instance_id']}")
        if subprocess.run(
            ["git", "-C", str(worktree), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout:
            raise RuntimeError(f"worktree is dirty: {workload['instance_id']}")

    run_dir = args.run_dir.resolve()
    staging = run_dir.with_name(run_dir.name + ".incomplete")
    if run_dir.exists() or staging.exists():
        raise FileExistsError(f"run output already exists: {run_dir}")
    staging.mkdir(parents=True)
    event_socket = args.event_socket.resolve() if args.event_socket else None
    if event_socket is not None and not event_socket.is_socket():
        raise RuntimeError(f"BeliefKV event socket is not ready: {event_socket}")

    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    registry = CodexThreadRegistry()
    app_audit = AppServerAudit(staging / "codex_app_server_events.jsonl")
    root_results: dict[str, dict[str, Any]] = {}
    thread_results: dict[str, dict[str, Any]] = {}
    root_sandboxes: dict[str, dict[str, Any]] = {}
    root_by_thread: dict[str, str] = {}
    notification_lock = threading.Lock()
    thread_completion = threading.Condition(notification_lock)

    with contextlib.ExitStack() as stack:
        stack.callback(app_audit.close)
        local_sink = stack.enter_context(
            JsonlRuntimeEventSink(staging / "runtime_events.codex.jsonl")
        )
        sinks = [local_sink]
        if event_socket is not None:
            sinks.append(
                stack.enter_context(UnixDatagramRuntimeEventSink(event_socket))
            )
        adapter = CodexRuntimeAdapter(MultiplexRuntimeEventSink(*sinks), registry)

        def on_notification(message: dict[str, Any]) -> None:
            observed_ts_ms = time.monotonic() * 1000.0
            app_audit.emit(message, observed_ts_ms=observed_ts_ms)
            adapter.handle_notification(message, observed_ts_ms=observed_ts_ms)
            if message.get("method") != "turn/completed":
                return
            params = message.get("params") or {}
            thread_id = str(params.get("threadId", ""))
            with thread_completion:
                thread_results[thread_id] = params.get("turn") or {}
                instance_id = root_by_thread.get(thread_id)
                if instance_id is not None:
                    root_results[instance_id] = params.get("turn") or {}
                thread_completion.notify_all()

        bridge = CodexCaptureBridge(
            adapter,
            upstream_base_url=args.sglang_base_url,
            port=args.bridge_port,
            max_completion_tokens=args.max_completion_tokens,
            audit_path=staging / "responses_bridge.jsonl",
        )
        stack.callback(bridge.close)
        bridge.start()

        bridge_url = bridge.base_url
        command = codex_command(binary["path"], bridge_url, args.model, context_limit=args.context_limit)
        if task_image:
            from beliefkv.runtime.codex_capture_container import codex_container

            command = stack.enter_context(codex_container(
                command, bridge_url=bridge_url, workload=workloads[0], image=task_image,
                python=args.task_python, workspace_write=args.workspace_write,
            ))
        client = CodexAppServerClient(
            command,
            codex_home=staging / "codex_home",
            notification_handler=on_notification,
            stderr_path=staging / "codex_app_server.stderr.log",
            environment={
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
            },
        )
        client.start()
        stack.callback(client.raise_if_reader_failed)
        stack.callback(client.close)

        for workload in workloads:
            instance_id = workload["instance_id"]
            result = client.request(
                "thread/start",
                {
                    "model": args.model,
                    "modelProvider": "sglang",
                    "cwd": str(Path(workload["worktree"]).resolve()),
                    "approvalPolicy": "never",
                    "sandbox": sandbox,
                    "ephemeral": True,
                    "experimentalRawEvents": True,
                    **(
                        {} if args.original_task else
                        {"developerInstructions": PROBE_DEVELOPER_INSTRUCTIONS}
                    ),
                },
                timeout_s=60.0,
            )
            thread = result["thread"]
            adapter.register_root(thread, workload_id=instance_id)
            root_by_thread[thread["id"]] = instance_id
            root_sandboxes[instance_id] = result["sandbox"]
        if not args.skip_gpu_monitor:
            gpu_monitor = GPUStatsMonitor(args.gpu_index, staging / "gpu_samples.csv")
            stack.callback(gpu_monitor.close)
            gpu_monitor.start()

        def start_turn(workload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            instance_id = workload["instance_id"]
            thread_id = next(
                value for value, item in root_by_thread.items() if item == instance_id
            )
            prompt = workload["problem_statement"] if args.original_task else build_prompt(workload)
            result = client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "approvalPolicy": "never",
                    "cwd": str(Path(workload["worktree"]).resolve()),
                    "model": args.model,
                    "responsesapiClientMetadata": {
                        "beliefkv_workload_id": instance_id,
                        "beliefkv_run_id": run_dir.name,
                    },
                },
                timeout_s=60.0,
            )
            return instance_id, result

        turn_start_results = {}
        deadline = time.monotonic() + args.timeout_seconds
        try:
            with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
                futures = [executor.submit(start_turn, workload) for workload in workloads]
                for future in as_completed(futures):
                    instance_id, result = future.result()
                    turn_start_results[instance_id] = result

            with thread_completion:
                while True:
                    client.raise_if_reader_failed()
                    unfinished = adapter.unfinished_threads()
                    if not unfinished and len(root_results) == len(workloads):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"Codex capture deadline reached: {sorted(unfinished)}")
                    thread_completion.wait(min(remaining, 0.25))
        except BaseException:
            # Keep the reader and bridge alive through the actual interrupt notifications.
            with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
                interrupts = [
                    executor.submit(client.interrupt, thread_id, turn_id)
                    for thread_id, turn_id in adapter.active_turns().items()
                ]
                for future in interrupts:
                    with contextlib.suppress(CodexAppServerError, TimeoutError):
                        future.result()
            drain_deadline = time.monotonic() + 2
            with thread_completion:
                while adapter.unfinished_threads() and time.monotonic() < drain_deadline:
                    thread_completion.wait(0.1)
            adapter.finish_incomplete_workflows(outcome="capture_aborted")
            raise

    finished_at = datetime.now(timezone.utc)
    identity_records = [
        {
            "thread_id": item.thread_id,
            "workflow_id": item.workflow_id,
            "invocation_id": item.invocation_id,
            "context_id": item.context_id,
            "parent_thread_id": item.parent_thread_id,
            "agent_role": item.agent_role,
            "agent_nickname": item.agent_nickname,
        }
        for item in registry.identities()
    ]
    runtime_event_records = [
        json.loads(line)
        for line in (staging / "runtime_events.codex.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    event_counts_by_workflow: dict[str, Counter[str]] = defaultdict(Counter)
    for record in runtime_event_records:
        event_counts_by_workflow[str(record["workflow_id"])][str(record["kind"])] += 1
    child_counts_by_workflow = Counter(
        item["workflow_id"]
        for item in identity_records
        if item["parent_thread_id"] is not None
    )
    return_sequence_by_invocation = {
        str(record["invocation_id"]): int(record["sequence"])
        for record in runtime_event_records
        if record["kind"] == "return" and record.get("invocation_id")
    }
    join_members_by_workflow: dict[str, set[str]] = defaultdict(set)
    for record in runtime_event_records:
        if record["kind"] == "join_satisfied":
            join_members_by_workflow[str(record["workflow_id"])].update(
                str(item) for item in record.get("member_invocation_ids", ())
            )
    gate_failures = list(adapter.capture_errors)
    if missing := adapter.missing_acknowledgements():
        gate_failures.append(f"Codex did not acknowledge Responses IDs: {missing}")
    for instance_id, turn in root_results.items():
        if turn.get("status") != "completed":
            gate_failures.append(f"{instance_id}: root turn status {turn.get('status')}")
        if turn.get("id") != turn_start_results[instance_id]["turn"]["id"]:
            gate_failures.append(f"{instance_id}: root turn ID differs from turn/start")
    bridge_records = [
        json.loads(line) for line in (staging / "responses_bridge.jsonl").read_text().splitlines()
    ]
    if any(record["event"] == "bridge_error" for record in bridge_records):
        gate_failures.append("Responses bridge recorded an error")
    boundary_ids = {
        kind: Counter(
            record["attributes"]["request_id"] for record in runtime_event_records
            if record["kind"] == kind
        )
        for kind in ("llm_submit", "llm_result")
    }
    completed_ids = Counter(
        record["request_id"] for record in bridge_records
        if record["event"] == "request_completed"
    )
    if (
        not completed_ids or any(count != 1 for count in completed_ids.values())
        or any(ids != completed_ids for ids in boundary_ids.values())
    ):
        gate_failures.append("incomplete or duplicate client request boundaries")
    workflow_task_ids = {
        item["workflow_id"]: root_by_thread[item["thread_id"]]
        for item in identity_records
        if item["parent_thread_id"] is None
    }
    required_event_kinds = () if args.original_task else ("spawn", "join_wait", "join_satisfied")
    probe_workflows = () if args.original_task else sorted(workflow_task_ids)
    for workflow_id in probe_workflows:
        child_count = child_counts_by_workflow[workflow_id]
        if child_count < args.min_subagents_per_workflow:
            gate_failures.append(
                f"{workflow_id}: observed {child_count} subagents, expected at least "
                f"{args.min_subagents_per_workflow}"
            )
        workflow_children = [
            item
            for item in identity_records
            if item["workflow_id"] == workflow_id
            and item["parent_thread_id"] is not None
        ]
        workflow_roots = [
            item
            for item in identity_records
            if item["workflow_id"] == workflow_id
            and item["parent_thread_id"] is None
        ]
        root_return_sequence = (
            return_sequence_by_invocation.get(workflow_roots[0]["invocation_id"])
            if len(workflow_roots) == 1
            else None
        )
        for child in workflow_children:
            if child["invocation_id"] not in join_members_by_workflow[workflow_id]:
                gate_failures.append(
                    f"{workflow_id}: child was never joined: {child['thread_id']}"
                )
            child_return_sequence = return_sequence_by_invocation.get(
                child["invocation_id"]
            )
            if child_return_sequence is None:
                gate_failures.append(
                    f"{workflow_id}: child did not return: {child['thread_id']}"
                )
            elif (
                root_return_sequence is not None
                and child_return_sequence > root_return_sequence
            ):
                gate_failures.append(
                    f"{workflow_id}: root returned before child {child['thread_id']}"
                )
        for kind in required_event_kinds:
            if event_counts_by_workflow[workflow_id][kind] == 0:
                gate_failures.append(f"{workflow_id}: missing {kind} event")
    subagent_gate = {
        "passed": not gate_failures,
        "enforced": not args.original_task,
        "min_subagents_per_workflow": None if args.original_task else args.min_subagents_per_workflow,
        "required_event_kinds": list(required_event_kinds),
        "child_counts_by_workflow": dict(sorted(child_counts_by_workflow.items())),
        "event_counts_by_workflow": {
            workflow_id: dict(sorted(counts.items()))
            for workflow_id, counts in sorted(event_counts_by_workflow.items())
        },
        "failures": gate_failures,
    }
    run_manifest = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": finished_at.isoformat(),
        "duration_seconds": time.monotonic() - started_monotonic,
        "runtime": "codex-app-server",
        "capture_mode": "original_task" if args.original_task else "subagent_probe",
        "codex_version": binary["version"],
        "codex_binary": binary,
        "codex_sandbox": sandbox,
        "task_container_image": task_image,
        "task_container_workspace_write": bool(task_image and args.workspace_write),
        "model_context_window": args.context_limit,
        "monotonic_clock_domain": local_monotonic_clock_domain(),
        "model": args.model,
        "sglang_base_url": args.sglang_base_url,
        "bridge_url": bridge_url,
        "beliefkv_event_socket": str(event_socket) if event_socket else None,
        "concurrency": args.concurrency,
        "max_completion_tokens": args.max_completion_tokens,
        "enforce_child_join_guard": False,
        "request_count": len(completed_ids),
        "workload_manifest": str(manifest_path),
        "workload_manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
        "instance_ids": instance_ids,
        "workflow_task_ids": workflow_task_ids,
        "thread_identities": identity_records,
        "root_sandbox_policies": root_sandboxes,
        "root_turn_start_results": turn_start_results,
        "root_turn_results": root_results,
        "thread_turn_results": thread_results,
        "root_workflow_count": len(workloads),
        "subagent_count": sum(
            item["parent_thread_id"] is not None for item in identity_records
        ),
        "subagent_gate": subagent_gate,
        "artifacts": {
            name: sha256(staging / name)
            for name in (
                "runtime_events.codex.jsonl",
                "codex_app_server_events.jsonl",
                "responses_bridge.jsonl",
                "codex_app_server.stderr.log",
                *(() if args.skip_gpu_monitor else ("gpu_samples.csv",)),
            )
        },
    }
    write_json(staging / "manifest.json", run_manifest)
    if gate_failures:
        raise RuntimeError("Codex capture gate failed: " + "; ".join(gate_failures))
    staging.replace(run_dir)
    print(json.dumps(run_manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Codex subagent benchmark failed: {error}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(2) from error
