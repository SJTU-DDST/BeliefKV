#!/usr/bin/env python3
"""Unforced model calls through actual tool-predicted native layer handoff."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import ContextMode, ExecutionMode, RelationType, RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.event_channel import JsonlRuntimeEventSink, UnixDatagramRuntimeEventSink
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from beliefkv.runtime.sglang_v0520_sessions import NativeRadixSessionLeases
from scripts import qualify_free_page_h2d_fence as common
from scripts import qualify_full_native_model as numerical
from scripts import run_causal_comparison as controls
from scripts.run_agentx_baseline import prefix_qualification_environment
from scripts.run_causal_gpu_replay import source_plan


DOCUMENT = {"generated_tokens": 8, "sampling_seed": 21}
WARM_TOOL_SECONDS = 4.


def records(path):
    if not path.exists():
        return []
    rows = []
    with path.open() as source:
        for line in source:
            if not line.endswith("\n"):
                break
            rows.append(json.loads(line))
    return rows


async def wait_evidence(path, ready, pulse, *, timeout=5.):
    deadline = time.monotonic() + timeout
    while True:
        pulse()
        rows = records(path)
        if ready(rows):
            return rows
        if time.monotonic() >= deadline:
            raise TimeoutError("model qualification evidence did not arrive: " + str(path))
        await asyncio.sleep(.05)


async def probe(directory, port, prompts, pulse):
    socket = Path(f"/tmp/bkv-layer-model-{port}.sock")
    sequence, rows, bodies = 0, {}, {}
    observations = directory / "arm/opportunities/admission_opportunities.jsonl"
    stages = {}
    leases = NativeRadixSessionLeases(lambda _session: None)

    async def stage(label, ready, *, timeout=5.):
        try:
            evidence = await wait_evidence(observations, ready, pulse, timeout=timeout)
        except BaseException:
            evidence = records(observations)
            stages[label] = {"status": "failed"}
            raise
        else:
            stages[label] = {"status": "observed"}
            return evidence
        finally:
            latest = next((
                row for row in reversed(evidence)
                if row.get("event") == "admission_runtime_state"
            ), None)
            stages[label]["last_runtime_state"] = latest
            stages[label]["non_state_events"] = [
                row for row in evidence if row.get("event") != "admission_runtime_state"
            ]
            controls.reservation.write_json(directory / "probe_stages.json", stages)

    with (
        JsonlRuntimeEventSink(directory / "events.jsonl") as log,
        UnixDatagramRuntimeEventSink(socket) as sink,
    ):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=300.) as client:
            async def send(kind, identity=None, **kwargs):
                nonlocal sequence
                pulse()
                sequence += 1
                item = RuntimeEvent(
                    f"layer-model:{sequence}", time.monotonic() * 1000., kind, "layer-model",
                    invocation_id=identity, context_id=identity, context_epoch=0, **kwargs,
                )
                log.emit_batch((item,))
                await asyncio.to_thread(sink.emit_batch, (item,))
                return item

            async def create(identity):
                await send(
                    Kind.INVOCATION_CREATE, identity,
                    relation_type=RelationType.ROOT, context_mode=ContextMode.FRESH,
                    execution_mode=ExecutionMode.FOREGROUND,
                    agent_definition_id="layer_model_fixture", agent_instance_id=identity,
                )

            async def call(label, identity, tokens):
                owner = BeliefKVRequestMetadata(
                    "layer-model", identity, identity, 0,
                    agent_definition_id="layer_model_fixture", agent_instance_id=identity,
                    relation_type="root", execution_mode="foreground",
                    full_prompt_replay_guaranteed=True,
                )
                request = "layer-model:" + label
                body = numerical.payload(request, tokens, owner, DOCUMENT)
                body["session_id"] = leases.for_request(owner)
                bodies[label] = body
                await send(Kind.LLM_SUBMIT, identity, attributes={"request_id": request})
                controls.reservation.write_json(directory / f"request_{label}.json", body)
                response = await client.post("/generate", json=body)
                response.raise_for_status()
                result = response.json()
                controls.reservation.write_json(directory / f"response_{label}.json", result)
                rows[label] = numerical.response_record(
                    result, request=request, prompt_tokens=len(tokens), document=DOCUMENT,
                )
                await send(Kind.LLM_RESULT, identity, attributes={"request_id": request})
                return rows[label]["output_token_ids"]

            async def tool(kind, identity, tool_id):
                return await send(kind, identity, attributes={
                    "tool_family": "shell", "tool_name": "execute",
                    "tool_run_id": tool_id, "command": "fixture_wait",
                })

            await send(Kind.WORKFLOW_START)
            await create("b")
            b_output = await call("b_initial", "b", prompts[0])
            # These are measured fixture waits, not private future durations.
            for index in range(8):
                await tool(Kind.TOOL_START, "b", f"warm-{index}")
                await asyncio.sleep(WARM_TOOL_SECONDS)
                await tool(Kind.TOOL_END, "b", f"warm-{index}")
            await tool(Kind.TOOL_START, "b", "b_wait")
            await create("c")
            await call("c", "c", prompts[1])
            await send(Kind.RETURN, "c", attributes={"context_retired": False})
            await create("a")
            a_output = await call("a_initial", "a", prompts[2])
            a_started = await tool(Kind.TOOL_START, "a", "a_wait")
            a_timer = asyncio.create_task(asyncio.sleep(20.))
            await stage("a_prediction", lambda evidence: any(
                row.get("event") == "tool_wait_forecast"
                and row.get("invocation_id") == "a"
                and row.get("invocation_revision_ts_ms") == a_started.ts_ms
                for row in evidence
            ))
            await tool(Kind.TOOL_END, "b", "b_wait")
            await call("b_handoff", "b", prompts[0] + b_output)
            journal = await stage("b_handoff", lambda evidence: any(
                row.get("event") == "layer_kv_handoff_submitted"
                and row.get("beneficiary_request_id") == "layer-model:b_handoff"
                for row in evidence
            ))
            handoffs = [row for row in journal if row.get("event") == "layer_kv_handoff_submitted"]
            if len(handoffs) != 1 or handoffs[0]["beneficiary_request_id"] != "layer-model:b_handoff":
                raise ValueError("actual model consumer did not execute one predicted layer handoff")
            await send(Kind.RETURN, "b", attributes={"context_retired": False})
            await create("b_reference")
            await call("b_reference", "b_reference", prompts[0] + b_output)
            await send(Kind.RETURN, "b_reference", attributes={"context_retired": False})
            await a_timer
            await tool(Kind.TOOL_END, "a", "a_wait")
            await call("a_recovered", "a", prompts[2] + a_output)
            await send(Kind.RETURN, "a", attributes={"context_retired": False})
            await create("a_reference")
            await call("a_reference", "a_reference", prompts[2] + a_output)
            await send(Kind.RETURN, "a_reference", attributes={"context_retired": False})
            for session in {body["session_id"] for body in bodies.values()}:
                response = await client.post("/close_session", json={"session_id": session})
                response.raise_for_status()
            await send(Kind.WORKFLOW_END)
            ended = time.monotonic() * 1000.
            await stage("workflow_drained", lambda evidence: any(
                row.get("event") == "admission_runtime_state"
                and row.get("monotonic_ms", 0) >= ended
                and row.get("agent_admission", {}).get("programs") == 0
                for row in evidence
            ))
    deltas = {}
    for label, reference in (("b_handoff", "b_reference"), ("a_recovered", "a_reference")):
        if rows[label]["output_token_ids"] != rows[reference]["output_token_ids"]:
            raise ValueError("actual unforced greedy output changed after handoff: " + label)
        delta = max(abs(a - b) for a, b in zip(
            rows[label]["output_logprobs"], rows[reference]["output_logprobs"], strict=True,
        ))
        if delta > .05:
            raise ValueError("unforced output logprob delta exceeds 0.05: " + label)
        deltas[label] = delta
    return {"requests": rows, "handoffs": handoffs, "maximum_logprob_delta": deltas}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--predecessor", type=Path, required=True)
    parser.add_argument("--components", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18668)
    args = parser.parse_args()
    directory = args.output_dir.resolve()
    if directory.exists():
        raise FileExistsError("preserve previous model qualification")
    prior = controls.load(args.predecessor)
    if not controls.predecessor_ready({**prior, "after_registration": str(args.predecessor)}):
        raise ValueError("wait for actual predecessor completion and owned exit")
    engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"]).resolve()
    state, component = git_source_state(engine), controls.load(args.components)
    if state["dirty"] or component.get("status") != "native_gpu_layer_handoff_components_qualified" or component["native_engine"] != state:
        raise ValueError("model qualifier requires identical clean component-qualified engine")
    common.guard_gpu()
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("model GPU supervisor requires nice0")
    _, expected, _ = source_plan(Path(prior["source_arm"]))
    selected, seen = [], set()
    for request, value in sorted(expected.items(), key=lambda item: (item[1]["llm_submit"][1], item[0])):
        identity, count = value["llm_submit"]
        if count >= 24576 and identity[0] not in seen:
            selected.append(request)
            seen.add(identity[0])
        if len(selected) == 3:
            break
    if len(selected) != 3:
        raise ValueError("three independent long source prefixes are unavailable")
    with CapturedTokenCorpus(Path(prior["source_arm"]) / "server/request_tokens.jsonl", expected) as corpus:
        prompts = [
            list(corpus.get(request).prompt_tokens[:size])
            for request, size in zip(selected, (16384, 16384, 24576), strict=True)
        ]
    directory.mkdir(parents=True)
    arm = directory / "arm"
    (arm / "server").mkdir(parents=True)
    (arm / "opportunities").mkdir()
    spec = controls.load(ROOT / "configs/migration/predictive_layer_handoff_20261027.json")
    env, flags, socket = controls.environment(
        {"arms": {"full": spec["common_environment"]}}, "full", ROOT, engine,
        arm, args.port, arm / "unused_plugin", sampling_mode="position",
        max_running_requests=24,
    )
    if socket.exists() or Path(f"/tmp/bkv-layer-model-{args.port}.sock").exists():
        raise ValueError("preserve existing model event socket and use a fresh port")
    env.update(
        SGLANG_SOURCE_BASE_COMMIT=state["commit"], SGLANG_PATCH_FLAVOR="layer_handoff",
        CONTEXT_LENGTH="49152", HICACHE_SIZE_GB="8", SGLANG_PLUGINS="",
        BELIEFKV_CAUSAL_REPLAY_BRIDGE="0", BELIEFKV_REPLAY_TOKEN_CAPTURE="1",
        BELIEFKV_ENABLE_BACKGROUND_PREPARE="0",
        BELIEFKV_ENABLE_TOOL_PREFETCH="0",
        BELIEFKV_TOOL_TIMING_PRIOR="", BELIEFKV_TOOL_TIMING_PRIOR_SHA256="",
    )
    flags.remove("--enable-custom-logit-processor")
    flags += ["--max-total-tokens", "49152"]
    flags[flags.index("--beliefkv-event-socket-path") + 1] = f"/tmp/bkv-layer-model-{args.port}.sock"
    inherited = prefix_qualification_environment({"reference_environment": env}, os.environ)
    env = {**inherited, **env}
    report = {
        "status": "failed", "performance_accepted": False,
        "native_engine": state, "component_sha256": sha256_file(args.components),
        "source_requests": selected, "fixture": {
            "device_tokens": 49152, "host_gb": 8, "warm_tools": 8,
            "warm_tool_seconds": WARM_TOOL_SECONDS, "donor_tool_seconds": 20,
            "speculative_tool_prefetch": False,
            "model_inputs": [len(tokens) for tokens in prompts],
            "sampling": DOCUMENT, "max_logprob_delta": .05,
        },
        "operator_sha256": sha256_file(Path(__file__)),
    }
    server = None
    owned = []
    started = time.monotonic()

    def pulse():
        common.check_reservation()
        if time.monotonic() - started > 900.:
            raise TimeoutError("model qualification exceeded 900 seconds")
        if server is not None and server.poll() is not None:
            raise ValueError("model server exited before qualification")

    try:
        with (directory / "server.log").open("x") as log:
            server = subprocess.Popen(
                ["bash", str(ROOT / "scripts/launch_qwen35_native_v0520.sh"), *flags],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            report["server"] = controls.reservation.process(server.pid)
            owned.append(report["server"])
            while True:
                pulse()
                try:
                    response = httpx.get(f"http://127.0.0.1:{args.port}/health", timeout=1.)
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.5)
            report["model"] = asyncio.run(probe(directory, args.port, prompts, pulse))
            report.update(
                status="unforced_layer_handoff_model_qualified",
                observed_at=datetime.now(timezone.utc).isoformat(),
                whole_workload_qualified=False,
            )
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        if owned:
            owned = controls.owned_processes(owned)
        if server is not None and server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            try:
                server.wait(timeout=30.)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait(timeout=10.)
        if owned:
            controls.cleanup(directory, owned, "layer_handoff_model_qualification")
            cleanup = controls.load(directory / "owned_process_cleanup.json")
            if cleanup["remaining_processes"]:
                report["status"] = "failed"
                report["cleanup_error"] = "owned processes remain"
        report["server_exit_code"] = None if server is None else server.returncode
        controls.reservation.write_json(directory / "report.json", report)
    print(json.dumps({key: report[key] for key in ("status", "model")}, indent=2))


if __name__ == "__main__":
    main()
