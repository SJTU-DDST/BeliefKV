#!/usr/bin/env python3
"""Check unforced resident model progress during ordinary native Host restoration."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
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
from beliefkv.experiments.native_engine_variant import DEMAND_PREFILL_PIPELINE_TREE
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.event_channel import JsonlRuntimeEventSink, UnixDatagramRuntimeEventSink
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from beliefkv.runtime.sglang_v0520_sessions import NativeRadixSessionLeases
from scripts import qualify_free_page_h2d_fence as common
from scripts import qualify_full_native_model as numerical
from scripts import qualify_layer_handoff_model as model_common
from scripts import run_causal_comparison as controls
from scripts.audit_staged_native_restore import inspect as staged_inspect, service_evidence
from scripts.run_agentx_baseline import prefix_qualification_environment
from scripts.run_causal_gpu_replay import source_plan


PREFIX_TOKENS = 24576
PREFIX_SIZES = (PREFIX_TOKENS, PREFIX_TOKENS, 32768)
DEVICE_TOKENS = 61440
RESIDENT_OUTPUT = 512
DOCUMENT = {"generated_tokens": 8, "sampling_seed": 21}
LABELS = ("b_initial", "c", "a_initial", "a_resident", "b_demand", "a_reference", "b_reference")


def compare_outputs(rows):
    if set(rows) != set(LABELS):
        raise ValueError("demand model qualifier requires every unforced call")
    deltas = {}
    for name, reference in (("a_resident", "a_reference"), ("b_demand", "b_reference")):
        actual, expected = rows[name], rows[reference]
        if actual["output_token_ids"] != expected["output_token_ids"]:
            raise ValueError("demand pipeline changed greedy output: " + name)
        delta = max(abs(a - b) for a, b in zip(
            actual["output_logprobs"], expected["output_logprobs"], strict=True,
        ))
        if not math.isfinite(delta) or delta > .05:
            raise ValueError("demand pipeline logprob delta exceeds 0.05: " + name)
        deltas[name] = delta
    return {"actual_greedy_tokens_equal": True, "maximum_logprob_delta": deltas}


def verify_progress(journals, workers):
    stages = [
        row for row in journals
        if row.get("event") == "native_demand_restore_staged"
        and any(member["request_id"] == "demand-model:b_demand" for member in row["requests"])
    ]
    if len(stages) != 1 or stages[0]["native_full_tokens"] < PREFIX_TOKENS - 1:
        raise ValueError("model demand must stage its actual long native Host prefix exactly once")
    stage = stages[0]
    matches = []
    for row in journals:
        if (
            row.get("event") != "native_demand_restore_decode_completed_pending"
            or row.get("native_submit_ts_ms") != stage["native_submit_ts_ms"]
            or row.get("monotonic_clock_domain") != stage["monotonic_clock_domain"]
            or row.get("ack_pending_at_completed_batch_observation") is not True
        ):
            continue
        launch, observed = row["batch_launch_monotonic_ms"], row["completion_observed_monotonic_ms"]
        if not stage["monotonic_ms"] <= launch <= observed:
            continue
        members = [
            member for member in row["completed_decode_requests"]
            if member["request_id"] == "demand-model:a_resident"
            and type(member.get("output_tokens")) is int and member["output_tokens"] >= 2
        ]
        if not members:
            continue
        actual = [
            worker for worker in workers
            if worker.get("event") == "gpu_service_sample" and worker.get("phase") == "decode"
            and worker.get("forward_iter") == row["forward_iter"]
            and worker.get("monotonic_clock_domain") == stage["monotonic_clock_domain"]
            and any(
                sample.get("request_id") == members[0]["request_id"]
                and sample.get("invocation_id") == members[0]["invocation_id"]
                and sample.get("context_id") == members[0]["context_id"]
                and sample.get("context_epoch") == members[0]["context_epoch"]
                and sample.get("workflow_id") == members[0]["workflow_id"]
                and sample.get("token_delta", 0) > 0
                for sample in worker.get("request_samples", ())
            )
        ]
        if len(actual) != 1:
            raise ValueError("pending-ACK progress lacks its actual completed native decode batch")
        matches.append({
            "forward_iter": row["forward_iter"], "request_id": members[0]["request_id"],
            "output_tokens": members[0]["output_tokens"],
            "batch_launch_monotonic_ms": launch,
            "completion_observed_monotonic_ms": observed,
        })
    if not matches:
        raise ValueError("no actual resident decode completed during the native H2D window")
    return {
        "resident_decode_completed_while_native_ack_pending": True,
        "ordinary_native_h2d_without_artificial_delay": True,
        "staged_native_full_tokens": stage["native_full_tokens"], "completed_batches": matches,
        "scope": "processed actual model results while immutable ACK query was false; not kernel timing",
    }


async def probe(directory, port, prompts, pulse):
    socket = Path(f"/tmp/bkv-demand-model-{port}.sock")
    observations = directory / "arm/opportunities/admission_opportunities.jsonl"
    audit = directory / "arm/server/runtime_audit.jsonl"
    sequence, rows, bodies = 0, {}, {}
    leases = NativeRadixSessionLeases(lambda _session: None)

    async def wait(path, ready):
        return await model_common.wait_evidence(path, ready, pulse, timeout=30.)

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
                    f"demand-model:{sequence}", time.monotonic() * 1000., kind, "demand-model",
                    invocation_id=identity, context_id=identity, context_epoch=0, **kwargs,
                )
                log.emit_batch((item,))
                await asyncio.to_thread(sink.emit_batch, (item,))

            async def create(identity):
                await send(
                    Kind.INVOCATION_CREATE, identity, relation_type=RelationType.ROOT,
                    context_mode=ContextMode.FRESH, execution_mode=ExecutionMode.FOREGROUND,
                    agent_definition_id="demand_model_fixture", agent_instance_id=identity,
                )

            async def call(label, identity, tokens):
                owner = BeliefKVRequestMetadata(
                    "demand-model", identity, identity, 0,
                    agent_definition_id="demand_model_fixture", agent_instance_id=identity,
                    relation_type="root", execution_mode="foreground",
                    full_prompt_replay_guaranteed=True,
                )
                document = {
                    **DOCUMENT,
                    "generated_tokens": RESIDENT_OUTPUT if label.startswith("a_r") else 8,
                }
                request = "demand-model:" + label
                body = numerical.payload(request, tokens, owner, document)
                body["session_id"] = leases.for_request(owner)
                bodies[label] = body
                await send(Kind.LLM_SUBMIT, identity, attributes={"request_id": request})
                controls.reservation.write_json(directory / f"request_{label}.json", body)
                response = await client.post("/generate", json=body)
                response.raise_for_status()
                result = response.json()
                controls.reservation.write_json(directory / f"response_{label}.json", result)
                rows[label] = numerical.response_record(
                    result, request=request, prompt_tokens=len(tokens), document=document,
                )
                await send(Kind.LLM_RESULT, identity, attributes={"request_id": request})
                return rows[label]["output_token_ids"]

            await send(Kind.WORKFLOW_START)
            await create("b")
            b_output = await call("b_initial", "b", prompts[0])
            await send(Kind.TOOL_START, "b", attributes={
                "tool_family": "shell", "tool_name": "execute", "tool_run_id": "b_wait",
            })
            await create("c")
            await call("c", "c", prompts[1])
            await send(Kind.RETURN, "c", attributes={"context_retired": False})
            await create("a")
            a_output = await call("a_initial", "a", prompts[2])
            resident = asyncio.create_task(call("a_resident", "a", prompts[2] + a_output))
            try:
                await wait(audit, lambda evidence: any(
                    row.get("event") == "gpu_service_sample" and row.get("phase") == "decode"
                    and any(
                        sample.get("request_id") == "demand-model:a_resident"
                        and sample.get("output_tokens_before", 0) >= 8
                        for sample in row.get("request_samples", ())
                    ) for row in evidence
                ))
                await send(Kind.TOOL_END, "b", attributes={
                    "tool_family": "shell", "tool_name": "execute", "tool_run_id": "b_wait",
                })
                await call("b_demand", "b", prompts[0] + b_output)
                await resident
            finally:
                if not resident.done():
                    resident.cancel()
                    await asyncio.gather(resident, return_exceptions=True)
            await send(Kind.RETURN, "b", attributes={"context_retired": False})
            await send(Kind.RETURN, "a", attributes={"context_retired": False})
            await create("a_reference")
            await call("a_reference", "a_reference", prompts[2] + a_output)
            await send(Kind.RETURN, "a_reference", attributes={"context_retired": False})
            await create("b_reference")
            await call("b_reference", "b_reference", prompts[0] + b_output)
            await send(Kind.RETURN, "b_reference", attributes={"context_retired": False})
            for session in {body["session_id"] for body in bodies.values()}:
                response = await client.post("/close_session", json={"session_id": session})
                response.raise_for_status()
            await send(Kind.WORKFLOW_END)
            ended = time.monotonic() * 1000.
            await wait(observations, lambda evidence: any(
                row.get("event") == "admission_runtime_state"
                and row.get("monotonic_ms", 0) >= ended
                and row.get("agent_admission", {}).get("programs") == 0
                and row.get("demand_prefill_pipeline_installed") is True
                for row in evidence
            ))
    journals, workers = model_common.records(observations), model_common.records(audit)
    progress = verify_progress(journals, workers)
    transfers = model_common.records(directory / "arm/server/transfer_telemetry.jsonl")
    unit, remainder = divmod(
        sum(row["actual_bytes"] for row in transfers if row.get("direction") == "h2d"),
        sum((row.get("num_tokens_by_pool") or {}).get("kv", 0)
            for row in transfers if row.get("direction") == "h2d"),
    )
    if remainder or unit <= 0:
        raise ValueError("native model H2D byte geometry is not exact")
    staged = staged_inspect(journals, transfers, bytes_per_token=unit)
    services = service_evidence(staged["episodes"], journals, workers)
    return {
        "requests": rows, **compare_outputs(rows), "progress": progress,
        "staged_native_ack": staged, "subsequent_native_service": services,
        "model_outputs_forced": False, "artificial_copy_delay": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", type=Path, default=ROOT / "configs/migration/predictive_sibling_service_capacity_20261028.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--predecessor", type=Path, required=True)
    parser.add_argument("--components", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18687)
    args = parser.parse_args()
    directory = args.output_dir.resolve()
    if directory.exists():
        raise FileExistsError("preserve previous demand model qualification")
    prior = controls.load(args.predecessor)
    if not controls.predecessor_ready({**prior, "after_registration": str(args.predecessor)}):
        raise ValueError("wait for actual predecessor completion and owned exit")
    application = git_source_state(ROOT)
    engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"]).resolve()
    state, component = git_source_state(engine), controls.load(args.components)
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=engine, text=True).strip()
    if (
        application["dirty"] or state["dirty"] or tree != DEMAND_PREFILL_PIPELINE_TREE
        or component.get("status") != "native_gpu_demand_prefill_components_qualified"
        or component["native_engine"] != state or component["application"] != application
    ):
        raise ValueError("demand model qualifier requires the exact clean component-qualified sources")
    common.guard_gpu()
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("model GPU supervisor requires nice0")
    _, expected, _ = source_plan(Path(prior["source_arm"]))
    selected, seen = [], set()
    for request, (identity, count) in sorted(
        ((rid, value["llm_submit"]) for rid, value in expected.items()),
        key=lambda pair: (pair[1][1], pair[0]),
    ):
        if count >= max(PREFIX_SIZES) and identity[0] not in seen:
            selected.append(request)
            seen.add(identity[0])
        if len(selected) == 3:
            break
    if len(selected) != 3:
        raise ValueError("three independent long source prefixes are unavailable")
    with CapturedTokenCorpus(Path(prior["source_arm"]) / "server/request_tokens.jsonl", expected) as corpus:
        prompts = [
            list(corpus.get(request).prompt_tokens[:size])
            for request, size in zip(selected, PREFIX_SIZES, strict=True)
        ]
    directory.mkdir(parents=True)
    arm = directory / "arm"
    (arm / "server").mkdir(parents=True)
    (arm / "opportunities").mkdir()
    spec = controls.load(args.configuration)
    env, flags, socket = controls.environment(
        {"arms": {"full": {**spec["common_environment"], "DEMAND_PREFILL_PIPELINE": "1"}}},
        "full", ROOT, engine, arm, args.port, arm / "unused_plugin",
        sampling_mode="position", max_running_requests=24,
    )
    model_socket = Path(f"/tmp/bkv-demand-model-{args.port}.sock")
    if socket.exists() or model_socket.exists():
        raise ValueError("preserve existing model event socket and use a fresh port")
    env.update(
        SGLANG_SOURCE_BASE_COMMIT=state["commit"], SGLANG_PATCH_FLAVOR="demand_prefill_pipeline",
        CONTEXT_LENGTH="65536", HICACHE_SIZE_GB="8", SGLANG_PLUGINS="",
        BELIEFKV_CAUSAL_REPLAY_BRIDGE="0", BELIEFKV_REPLAY_TOKEN_CAPTURE="1",
        BELIEFKV_DEMAND_PREFILL_PIPELINE_PROGRESS_PROBE="1",
        BELIEFKV_ENABLE_BACKGROUND_PREPARE="0", BELIEFKV_ENABLE_TOOL_PREFETCH="0",
        BELIEFKV_TOOL_TIMING_PRIOR="", BELIEFKV_TOOL_TIMING_PRIOR_SHA256="",
    )
    flags.remove("--enable-custom-logit-processor")
    flags += ["--max-total-tokens", str(DEVICE_TOKENS)]
    flags[flags.index("--beliefkv-event-socket-path") + 1] = str(model_socket)
    inherited = prefix_qualification_environment({"reference_environment": env}, os.environ)
    env = {**inherited, **env}
    report = {
        "status": "failed", "performance_accepted": False, "application": application,
        "native_engine": state, "native_tree": tree, "component_sha256": sha256_file(args.components),
        "source_requests": selected, "configuration_sha256": sha256_file(args.configuration),
        "operator_sha256": sha256_file(Path(__file__)), "fixture": {
            "device_tokens": DEVICE_TOKENS, "host_gb": 8, "prefix_tokens": list(PREFIX_SIZES),
            "resident_output": RESIDENT_OUTPUT, "other_output": 8, "artificial_copy_delay": False,
            "progress_probe": True, "model_outputs_forced": False, "max_logprob_delta": .05,
        },
    }
    controls.reservation.write_json(directory / "environment.json", env)
    server, owned, started = None, [], time.monotonic()

    def pulse():
        common.check_reservation()
        if time.monotonic() - started > 900.:
            raise TimeoutError("demand model qualification exceeded 900 seconds")
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
                    if httpx.get(f"http://127.0.0.1:{args.port}/ready", timeout=5.).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.5)
            report["model"] = asyncio.run(probe(directory, args.port, prompts, pulse))
            report.update(
                status="unforced_demand_prefill_model_qualified",
                observed_at=datetime.now(timezone.utc).isoformat(), whole_workload_qualified=False,
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
            controls.cleanup(directory, owned, "demand_prefill_model_qualification")
            if controls.load(directory / "owned_process_cleanup.json")["remaining_processes"]:
                report["status"] = "failed"
                report["cleanup_error"] = "owned processes remain"
        report["server_exit_code"] = None if server is None else server.returncode
        controls.reservation.write_json(directory / "report.json", report)
    print(json.dumps({"status": report["status"], "model": report["model"]}, indent=2))


if __name__ == "__main__":
    main()
