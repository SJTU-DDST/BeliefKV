#!/usr/bin/env python3
"""Check native session demand/JOIN recovery with real, unforced model output."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
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

from beliefkv.core.events import (
    ContextMode, ExecutionMode, RelationType, RuntimeEvent, RuntimeEventKind as Kind,
)
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.event_channel import JsonlRuntimeEventSink, UnixDatagramRuntimeEventSink
from beliefkv.runtime.replay_token_capture import native_request_expectations
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from beliefkv.runtime.sglang_v0520_sessions import NativeRadixSessionLeases
from scripts import qualify_free_page_h2d_fence as components
from scripts import qualify_full_native_model as numerical
from scripts import run_causal_comparison as controls
from scripts.run_agentx_baseline import freeze, prefix_qualification_environment
from scripts.run_causal_gpu_replay import source_plan


CONFIG = "configs/migration/session_native_model_qualification_20261026.json"
LABELS = ("cold", "resident", "child", "recovered")
SOURCES = tuple(dict.fromkeys((
    "scripts/qualify_session_native_model.py", CONFIG,
    "scripts/qualify_full_native_model.py", "scripts/run_agentx_baseline.py",
    "scripts/run_causal_comparison.py", "scripts/run_causal_gpu_replay.py",
    "scripts/launch_qwen35_native_v0520.sh",
    "beliefkv/runtime/sglang_v0520_sessions.py", "beliefkv/runtime/event_channel.py",
    "beliefkv/runtime/replay_token_capture.py", "beliefkv/runtime/v0520_native_telemetry.py",
    "beliefkv/runtime/sglang_v0520_runtime.py", "beliefkv/runtime/sglang_v0520_physical.py",
    "beliefkv/runtime/agent_working_set.py", "beliefkv/policy/thunder_agent_migration.py",
    "beliefkv/policy/paper_baselines.py", "beliefkv/experiments/internal_comparison.py",
    "beliefkv/experiments/causal_replay_tokens.py",
    *components.SOURCES,
)))


def configuration():
    document = json.loads((ROOT / CONFIG).read_bytes())
    required = {
        "schema_version": 1, "purpose": "bounded_full_session_model_qualification",
        "candidate": components.CONFIG, "arms": ["native_matched", "full"],
        "requests_per_arm": 4, "prompt_tokens": 32768, "generated_tokens": 8,
        "device_full_tokens": 49152, "context_length": 49152, "host_size_gb": 8,
        "max_running_requests": 24, "minimum_restored_tokens": 8192,
        "sampling_seed": 21, "max_logprob_absolute_delta": .05,
        "stage_timeout_seconds": 180,
        "whole_population_qualified": False, "performance_accepted": False,
    }
    if any(
        type(document.get(key)) is not type(value) or document[key] != value
        for key, value in required.items()
    ):
        raise ValueError("session model check requires its declared bounded pressure fixture")
    return document


def check_hold():
    return components.check_reservation()


def select_probes(expected, corpus, document):
    chosen, workflows = [], set()
    for request, entry in sorted(expected.items(), key=lambda item: (item[1]["llm_submit"][1], item[0])):
        identity, count = entry["llm_submit"]
        if count < document["prompt_tokens"] or identity[0] in workflows:
            continue
        captured = corpus.get(request)
        if (
            captured.session_id is None or type(captured.session_generation) is not int
            or captured.session_generation <= 0
        ):
            continue
        chosen.append(request)
        workflows.add(identity[0])
        if len(chosen) == 2:
            return chosen
    raise ValueError("session model fixture requires long session prompts from two source workflows")


def environment(registration, mode, arm):
    common = json.loads((ROOT / components.CONFIG).read_bytes())["common_environment"]
    settings, flags, socket = controls.environment(
        {"arms": {"full": common}}, mode, ROOT, Path(registration["engine_checkout"]),
        arm, registration["port"], arm / "unused_plugin",
        sampling_mode="position", max_running_requests=24,
    )
    document = registration["configuration"]
    settings.update(
        CONTEXT_LENGTH=str(document["context_length"]),
        HICACHE_SIZE_GB=str(document["host_size_gb"]),
        BELIEFKV_CAUSAL_REPLAY_BRIDGE="0", SGLANG_PLUGINS="",
        BELIEFKV_REPLAY_TOKEN_CAPTURE="1",
        SGLANG_SOURCE_BASE_COMMIT=registration["engine_base_commit"],
    )
    flags.remove("--enable-custom-logit-processor")
    flags += ["--max-total-tokens", str(document["device_full_tokens"])]
    return settings, flags, socket


def payload(request, prompt, metadata, session, document):
    body = numerical.payload(request, prompt, metadata, document)
    body["session_id"] = session
    body["extra_key"] = body["cache_salt"] = "session-model:" + metadata.context_id
    return body


def records(path):
    if not path.exists():
        return []
    with path.open() as stream:
        return [json.loads(line) for line in stream if line.endswith("\n")]


def restored_join(arm, minimum_bytes):
    acknowledgements = [
        row for row in records(arm / "server/physical_action_ack.jsonl")
        if row.get("event") == "beliefkv_physical_action_ack"
        and row.get("action") == "PREFETCH_GPU" and row.get("source") == "join_ticket"
        and row.get("context_id") == "parent" and row.get("context_epoch") == 0
        and row.get("evidence") == "native_child_commit_reconciled_with_live_context"
    ]
    if len({row["command_id"] for row in acknowledgements}) != len(acknowledgements):
        raise ValueError("session model fixture has duplicate controlled restore ACKs")
    return acknowledgements if sum(row["num_bytes"] for row in acknowledgements) >= minimum_bytes else None


async def probe(registration, mode, arm, socket, prompts, *, pulse=lambda: None):
    document = registration["configuration"]
    workflow, sequence = "session-model-" + mode, 0
    submitted, rows, stages, closed = {}, {}, {}, []
    with ExitStack() as stack:
        log = stack.enter_context(JsonlRuntimeEventSink(arm / "probe_events.jsonl"))
        sink = stack.enter_context(UnixDatagramRuntimeEventSink(socket)) if socket is not None else None
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{registration['port']}", timeout=300.,
        ) as client:
            async def close(session):
                response = await client.post("/close_session", json={"session_id": session})
                response.raise_for_status()
                closed.append(session)

            leases = NativeRadixSessionLeases(lambda session: None)

            async def send(kind, *, invocation=None, **kwargs):
                nonlocal sequence
                pulse()
                sequence += 1
                event = RuntimeEvent(
                    "session-model:" + str(sequence), time.monotonic() * 1000., kind, workflow,
                    invocation_id=invocation, context_id=invocation, context_epoch=0, **kwargs,
                )
                log.emit_batch((event,))
                if sink is not None:
                    await asyncio.to_thread(sink.emit_batch, (event,))

            def metadata(identity):
                return BeliefKVRequestMetadata(
                    workflow, identity, identity, 0,
                    agent_definition_id="controlled_session_model_fixture", agent_instance_id=identity,
                    parent_invocation_id="parent" if identity == "child" else None,
                    relation_type="spawn" if identity == "child" else "root",
                    execution_mode="background" if identity == "child" else "foreground",
                    full_prompt_replay_guaranteed=True,
                )

            async def create(identity):
                await send(
                    Kind.INVOCATION_CREATE, invocation=identity,
                    parent_invocation_id="parent" if identity == "child" else None,
                    relation_type=RelationType.SPAWN if identity == "child" else RelationType.ROOT,
                    context_mode=ContextMode.FRESH,
                    execution_mode=ExecutionMode.BACKGROUND if identity == "child" else ExecutionMode.FOREGROUND,
                    agent_definition_id="controlled_session_model_fixture", agent_instance_id=identity,
                )

            async def wait_stage(label, ready):
                deadline = time.monotonic() + document["stage_timeout_seconds"]
                while True:
                    pulse()
                    value = ready()
                    if value:
                        stages[label] = value
                        controls.reservation.write_json(arm / "probe_stages.json", stages)
                        return value
                    if time.monotonic() >= deadline:
                        raise TimeoutError("actual session model stage not observed: " + label)
                    await asyncio.sleep(.05)

            async def call(label, identity, prompt):
                request = "session-model:" + mode + ":" + label
                await send(Kind.LLM_SUBMIT, invocation=identity, attributes={"request_id": request})
                owner = metadata(identity)
                body = payload(request, prompt, owner, leases.for_request(owner), document)
                submitted[label] = body
                controls.reservation.write_json(arm / ("request_" + label + ".json"), body)
                response = await client.post("/generate", json=body)
                response.raise_for_status()
                result = response.json()
                controls.reservation.write_json(arm / ("response_" + label + ".json"), result)
                rows[label] = numerical.response_record(
                    result, request=request, prompt_tokens=len(prompt), document=document,
                )
                await send(Kind.LLM_RESULT, invocation=identity, attributes={"request_id": request})

            await send(Kind.WORKFLOW_START)
            await create("parent")
            await call("cold", "parent", prompts[0])
            await call("resident", "parent", prompts[0])
            await create("child")
            await send(
                Kind.SPAWN, invocation="parent", target_invocation_id="child",
                execution_mode=ExecutionMode.BACKGROUND,
            )
            await send(Kind.JOIN_CREATE, join_id="model-join", member_invocation_ids=("child",))
            await send(Kind.JOIN_WAIT, invocation="parent", join_id="model-join")
            if mode == "full":
                def backed():
                    state = controls.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
                    return state if state is not None and (
                        state.get("counts", {}).get("spawn_full_prepare_settled_contexts", 0) > 0
                        and state.get("spawn_full_prepare", {}).get("pending_contexts") == 0
                    ) else None

                await wait_stage("backed", backed)
            await call("child", "child", prompts[1])
            await send(Kind.RETURN, invocation="child", attributes={"context_retired": True})
            await close(submitted["child"]["session_id"])
            await send(Kind.JOIN_SATISFIED, join_id="model-join")
            if mode == "full":
                await wait_stage("restored", lambda: restored_join(
                    arm, document["minimum_restored_tokens"] * registration["bytes_per_token"],
                ))
            await call("recovered", "parent", prompts[0])
            await send(Kind.RETURN, invocation="parent", attributes={"context_retired": True})
            await close(submitted["cold"]["session_id"])
            await send(Kind.WORKFLOW_END)
    return {"requests": rows, "submitted": submitted, "stages": stages, "closed_sessions": closed}


def validate_model(results, document):
    if set(results) != set(document["arms"]):
        raise ValueError("session model check lacks both complete arms")
    reference = results["native_matched"]["requests"]["resident"]
    reference_prompt = results["native_matched"]["submitted"]["resident"]["input_ids"]
    deltas = {}
    for mode, result in results.items():
        rows, bodies = result["requests"], result["submitted"]
        if set(rows) != set(LABELS) or set(bodies) != set(LABELS):
            raise ValueError("session model check lacks all actual model requests")
        parent_session = bodies["cold"].get("session_id")
        if (
            type(parent_session) is not str or not parent_session.startswith("beliefkv-")
            or any(bodies[label].get("session_id") != parent_session for label in ("resident", "recovered"))
            or bodies["child"].get("session_id") in (None, parent_session)
            or sorted(result["closed_sessions"]) != sorted((parent_session, bodies["child"]["session_id"]))
            or rows["resident"]["cached_tokens_details"].get("device", 0) < document["prompt_tokens"] - 1
        ):
            raise ValueError("model fixture did not preserve and close actual native sessions")
        for label, body in bodies.items():
            if (
                "custom_logit_processor" in body or "custom_params" in body["sampling_params"]
                or body["sampling_params"] != {
                    "temperature": 0., "top_k": 1, "max_new_tokens": 8, "min_new_tokens": 8,
                    "ignore_eos": True, "sampling_seed": 21,
                }
                or len(body["input_ids"]) != document["prompt_tokens"]
                or rows[label]["prompt_tokens"] != document["prompt_tokens"]
            ):
                raise ValueError("session model fixture changed its input or unforced sampler")
        if any(
            bodies[label]["input_ids"] != bodies["cold"]["input_ids"]
            or bodies[label]["extra_key"] != bodies["cold"]["extra_key"]
            or bodies[label]["cache_salt"] != bodies["cold"]["cache_salt"]
            for label in ("resident", "recovered")
        ):
            raise ValueError("session recovery compared different parent prompts or namespaces")
        if bodies["cold"]["input_ids"] != reference_prompt:
            raise ValueError("model fixture arms did not execute the same parent input")
        for label in ("cold", "resident", "recovered"):
            row = rows[label]
            if row["output_token_ids"] != reference["output_token_ids"]:
                raise ValueError("actual greedy model tokens changed after session recovery")
            delta = max(abs(left - right) for left, right in zip(
                row["output_logprobs"], reference["output_logprobs"], strict=True,
            ))
            if delta > document["max_logprob_absolute_delta"]:
                raise ValueError("session model logprob delta exceeds the declared tolerance")
            deltas[mode + ":" + label] = delta
    if results["native_matched"]["requests"]["recovered"]["cached_tokens_details"].get("host", 0) < document["minimum_restored_tokens"]:
        raise ValueError("native model fixture did not execute enough real Host demand recovery")
    if results["full"]["requests"]["recovered"]["cached_tokens_details"].get("device", 0) < document["prompt_tokens"] - 1:
        raise ValueError("Full session restore was not resident at actual model service")
    return {"actual_greedy_tokens_equal": True, "maximum_logprob_absolute_delta": deltas}


def validate_native_capture(arm, result, document):
    expected = native_request_expectations(records(arm / "server/runtime_events.sglang.jsonl"))
    bodies = {body["rid"]: body for body in result["submitted"].values()}
    if set(expected) != set(bodies):
        raise ValueError("native model capture did not serve exactly the declared requests")
    generations = {}
    with CapturedTokenCorpus(arm / "server/request_tokens.jsonl", expected) as corpus:
        for label, row in result["requests"].items():
            captured = corpus.get(row["request_id"])
            body = bodies[row["request_id"]]
            if (
                captured.session_id != body["session_id"]
                or type(captured.session_generation) is not int or captured.session_generation <= 0
                or list(captured.prompt_tokens) != body["input_ids"]
                or list(captured.output_tokens) != row["output_token_ids"]
                or captured.extra_key != body["extra_key"] or captured.cache_salt != body["cache_salt"]
                or captured.identity != tuple(body["beliefkv_metadata"][key] for key in (
                    "root_workflow_id", "invocation_id", "context_id", "context_epoch",
                ))
                or captured.native_finish_reason != row["native_finish_reason"]
                or any(captured.sampling_parameters[key] != body["sampling_params"][key] for key in (
                    "max_new_tokens", "min_new_tokens", "ignore_eos",
                ))
                or not isinstance(captured.generation_parameters, dict)
                or captured.generation_parameters.get("top_k") != 1
                or captured.generation_parameters.get("sampling_seed") != document["sampling_seed"]
            ):
                raise ValueError("actual native session capture differs from the model request")
            previous = generations.setdefault(captured.session_id, captured.session_generation)
            if previous != captured.session_generation:
                raise ValueError("model recovery silently reopened its native session")
        return {"native_session_generations": generations, "capture": corpus.evidence}


def validate_join_use(arm, result, registration):
    minimum = registration["configuration"]["minimum_restored_tokens"] * registration["bytes_per_token"]
    acknowledged = restored_join(arm, minimum)
    if not acknowledged:
        raise ValueError("Full model fixture lacks verified controlled JOIN restore bytes")
    used = {
        row["command_id"] for row in records(arm / "server/physical_action_use.jsonl")
        if row.get("event") == "beliefkv_prefetch_first_service"
        and row.get("request_id") == result["requests"]["recovered"]["request_id"]
        and row.get("context_id") == "parent"
        and row.get("full_node_reused") is True
        and row.get("full_reuse_proof_version") == 2
        and row.get("full_reuse_proof") == "acknowledged_allocation_in_materialized_service_prefix"
    }
    consumed = sum(row["num_bytes"] for row in acknowledged if row["command_id"] in used)
    state = controls.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    if (
        consumed < minimum or state is None or state.get("physical_disabled") is not False
        or state.get("internal_comparison_mode") != "full" or state.get("paper_baseline") is not None
        or state.get("full_prefix_migration") is not None
        or state.get("agent_admission", {}).get("counts", {}).get("native_restore_consumers", 0) < 1
    ):
        raise ValueError("Full JOIN restore lacks same-consumer native reuse and agent admission")
    controls.validate_free_page_fence({"reference_environment": {"FREE_PAGE_H2D_FENCE": "1"}}, state)
    return {"verified_join_bytes_reused": consumed, "native_runtime": state}


def verify_server(info, capacity, settings, document):
    required = {
        "model_path": settings["MODEL_PATH"], "dtype": "bfloat16", "kv_cache_dtype": "bfloat16",
        "context_length": document["context_length"], "max_running_requests": 24,
        "chunked_prefill_size": 4096, "page_size": 1, "random_seed": 21,
        "enable_session_radix_cache": True, "enable_custom_logit_processor": False,
        "enable_beliefkv_admission": settings["BELIEFKV_INTERNAL_COMPARISON_MODE"] == "full",
        "hicache_size": document["host_size_gb"], "hicache_write_policy": "write_back",
        "disable_overlap_schedule": False,
    }
    if any(info.get(name) != value for name, value in required.items()) or (
        capacity.get("device_full_tokens") != document["device_full_tokens"]
        or capacity.get("device_mamba_slots") != 0
    ):
        raise ValueError("native session model server differs from the declared fixture")


def probe_inputs(reference, document):
    capacity = reference["source_capacity"]
    if (
        capacity["pool_layout"] != "static_full_only"
        or capacity["host_full_bytes"] % capacity["host_full_tokens"] != 0
    ):
        raise ValueError("session model fixture requires observed exact FULL-only Host geometry")
    source = Path(reference["source_arm"])
    _, expected, source_evidence = source_plan(source)
    with CapturedTokenCorpus(source / "server/request_tokens.jsonl", expected) as corpus:
        selected = select_probes(expected, corpus, document)
        prompts = [list(corpus.get(request).prompt_tokens[:document["prompt_tokens"]]) for request in selected]
        capture = corpus.evidence
    return {
        "source_evidence": source_evidence, "source_capture": capture, "probe_requests": selected,
        "bytes_per_token": capacity["host_full_bytes"] // capacity["host_full_tokens"],
        "prompts": prompts,
    }


def check_registration(registration):
    if registration["configuration"] != configuration() or registration["source_sha256"] != {
        name: sha256_file(ROOT / name) for name in SOURCES
    }:
        raise ValueError("session model sources differ from the frozen qualification")
    if registration.get("reference_environment") != json.loads(
        (ROOT / components.CONFIG).read_bytes()
    )["common_environment"]:
        raise ValueError("session model component environment differs from the declared candidate")
    reference_path = Path(registration["reference"])
    if sha256_file(reference_path) != registration["reference_sha256"]:
        raise ValueError("session model source registration changed")
    inputs = probe_inputs(json.loads(reference_path.read_bytes()), registration["configuration"])
    if any(registration.get(name) != value for name, value in inputs.items()):
        raise ValueError("session model selected prompts or original source changed")
    candidate = json.loads((ROOT / components.CONFIG).read_bytes())["engine_candidate"]
    if (
        registration["engine_base_commit"] != candidate["base_commit"]
        or registration["engine_tree"] != candidate["candidate_tree"]
    ):
        raise ValueError("session model engine differs from the declared candidate")


def run(args):
    check_hold()
    document = configuration()
    if args.output_dir.exists():
        raise FileExistsError("preserve previous session model evidence")
    inputs = probe_inputs(json.loads(args.reference.read_bytes()), document)
    check_hold()
    frozen = freeze(args.engine_checkout, args.canonical_patch)
    checkout = Path(frozen["checkout"])
    registration = {
        **frozen, "configuration": document, "port": args.port,
        "gpu_authorization": check_hold(),
        "reference_environment": json.loads(
            (checkout / components.CONFIG).read_bytes()
        )["common_environment"],
        **inputs,
        "reference": str(args.reference.resolve()), "reference_sha256": sha256_file(args.reference),
        "engine_base_commit": subprocess.check_output(
            ["git", "-C", str(args.engine_checkout), "rev-parse", "HEAD"], text=True,
        ).strip(),
        "source_sha256": {name: sha256_file(checkout / name) for name in SOURCES},
        "performance_accepted": False,
    }
    args.output_dir.mkdir(parents=True, exist_ok=False)
    controls.reservation.write_json(args.output_dir / "registration.json", registration)
    with (args.output_dir / "supervisor.log").open("x") as log:
        process = subprocess.run([
            sys.executable, str(checkout / "scripts/qualify_session_native_model.py"),
            "execute", "--registration", str(args.output_dir / "registration.json"),
        ], cwd=checkout, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    if process.returncode:
        raise ValueError("native session model check failed; preserve its summary")


def execute(path):
    registration = json.loads(path.read_bytes())
    run_root = path.parent
    owned, results, stopped = [], {}, False
    state = {
        "status": "preflight", "whole_population_qualified": False,
        "model_numerical_equivalence_verified": False, "performance_accepted": False,
    }

    def stop(*_args):
        nonlocal stopped
        stopped = True

    def pulse():
        if stopped:
            raise ValueError("session model check received a stop request")
        check_hold()
        owned[:] = controls.owned_processes(owned)
        if owned and set(components.gpu_processes()) - {row["pid"] for row in owned}:
            raise ValueError("GPU is occupied outside this session model check")

    def command(argv, environment, log_path, *, timeout):
        pulse()
        with log_path.open("x") as log:
            process = subprocess.Popen(
                argv, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        identity = controls.reservation.process(process.pid)
        if identity is None:
            raise ValueError("session model child lacks an owned process identity")
        owned.append(identity)
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            pulse()
            if time.monotonic() >= deadline:
                raise TimeoutError("session model component check exceeded its deadline")
            time.sleep(.5)
        if process.wait():
            raise ValueError("source-bound native component check failed")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        pulse()
        check_registration(registration)
        controls.wait_for_resources(registration, state, pulse=pulse)
        state["status"] = "qualifying_native_fence_components"
        command([
            sys.executable, str(ROOT / "scripts/qualify_free_page_h2d_fence.py"),
            "--engine-checkout", registration["engine_checkout"],
            "--canonical-patch", str(ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
            "--output-dir", str(run_root / "native_components"),
        ], prefix_qualification_environment(registration, os.environ),
            run_root / "native_components.log", timeout=600.)
        component_report = json.loads((run_root / "native_components/summary.json").read_bytes())
        components.validate_report(
            component_report, engine_tree=registration["engine_tree"],
            patch_sha256=registration["native_patch_sha256"],
        )
        for mode in registration["configuration"]["arms"]:
            controls.wait_for_resources(registration, state, pulse=pulse)
            pulse()
            arm = run_root / mode
            (arm / "server").mkdir(parents=True, exist_ok=False)
            settings, flags, socket = environment(registration, mode, arm)
            controls.reservation.write_json(arm / "launch_environment.json", settings)
            if socket is not None and socket.exists():
                raise ValueError("session model check requires a fresh event socket")
            if not controls.qualified.resources(registration["port"])["ready"]:
                raise ValueError("session model resources changed before startup")
            state["status"] = "starting_" + mode
            with (arm / "server.log").open("x") as log:
                server = subprocess.Popen(
                    ["bash", str(ROOT / "scripts/launch_qwen35_native_v0520.sh"), *flags],
                    cwd=ROOT, env=controls.qualified.driver_environment({"environment": settings}, os.environ),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
            identity = controls.reservation.process(server.pid)
            if identity is None:
                raise ValueError("session model server lacks an owned identity")
            owned.append(identity)
            deadline = time.monotonic() + 480.
            while True:
                pulse()
                if server.poll() is not None:
                    raise ValueError("session model server exited during startup")
                try:
                    ready = httpx.get(
                        f"http://127.0.0.1:{registration['port']}/health", timeout=2.,
                    ).is_success and (arm / "server/native_capacity_census.json").is_file() and (
                        arm / "server/native_telemetry_ready.json"
                    ).is_file()
                except httpx.HTTPError:
                    ready = False
                if ready:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("session model server startup exceeded its deadline")
                time.sleep(1.)
            info = httpx.get(f"http://127.0.0.1:{registration['port']}/get_server_info", timeout=10.).json()
            capacity = json.loads((arm / "server/native_capacity_census.json").read_bytes())["capacity"]
            verify_server(info, capacity, settings, registration["configuration"])
            if capacity["host_full_bytes"] != capacity["host_full_tokens"] * registration["bytes_per_token"]:
                raise ValueError("session model pool does not have the declared per-token KV bytes")
            scheduler = json.loads((arm / "server/native_telemetry_ready.json").read_bytes())["scheduler_pid"]
            pulse()
            if scheduler not in {row["pid"] for row in owned} or scheduler not in components.gpu_processes():
                raise ValueError("session model scheduler is not an owned GPU process")
            state["status"] = "probing_" + mode

            async def observed():
                task = asyncio.create_task(probe(
                    registration, mode, arm, socket, registration["prompts"], pulse=pulse,
                ))
                try:
                    while not task.done():
                        pulse()
                        if server.poll() is not None:
                            raise ValueError("session model server exited during probe")
                        await asyncio.wait((task,), timeout=.5)
                    return await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

            results[mode] = asyncio.run(asyncio.wait_for(observed(), timeout=1200.))
            controls.wait_for_telemetry_drain(arm, server=server, pulse=pulse)
            results[mode]["native_capture"] = validate_native_capture(arm, results[mode], registration["configuration"])
            if mode == "full":
                results[mode]["controlled_join_reuse"] = validate_join_use(arm, results[mode], registration)
            controls.reservation.write_json(arm / "probe_result.json", results[mode])
            controls.cleanup(arm, owned, "session_model_arm_complete")
            server.wait(timeout=15.)
            owned.clear()
        state.update(
            status="session_native_model_qualified", results=results,
            **validate_model(results, registration["configuration"]),
            model_numerical_equivalence_verified=True,
            scope=registration["configuration"]["scope"],
        )
    except BaseException as error:
        state.update(status="failed", error_type=type(error).__name__, error=str(error))
    finally:
        try:
            controls.cleanup(run_root, owned, state["status"])
        except BaseException as error:
            state.update(status="failed", cleanup_error=repr(error))
        state["finished_at"] = controls.reservation.now().isoformat()
        controls.reservation.write_json(run_root / "summary.json", state)
    return state["status"] == "session_native_model_qualified"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("run")
    for name in ("reference", "engine-checkout", "canonical-patch", "output-dir"):
        launch.add_argument("--" + name, type=Path, required=True)
    launch.add_argument("--port", type=int, required=True)
    worker = commands.add_parser("execute")
    worker.add_argument("--registration", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        args.output_dir = args.output_dir.resolve()
        run(args)
    else:
        raise SystemExit(0 if execute(args.registration) else 1)


if __name__ == "__main__":
    main()
