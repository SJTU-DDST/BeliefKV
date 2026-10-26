#!/usr/bin/env python3
"""Queue bounded real-model Full recovery checks with unforced token outputs."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import fcntl
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

from beliefkv.core.events import (
    ContextMode, ExecutionMode, RelationType, RuntimeEvent, RuntimeEventKind as Kind,
)
from beliefkv.experiments.agentx_tokens import AgentXTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.event_channel import JsonlRuntimeEventSink, UnixDatagramRuntimeEventSink
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from scripts import qualify_full_prefix_migration as components
from scripts import run_causal_comparison as controls
from scripts.run_agentx_baseline import freeze, predecessor_finished, prefix_qualification_environment


CONFIG = "configs/migration/full_native_model_qualification_20261025.json"
CANDIDATE = "configs/migration/full_sessionless_prefix_candidate_20261025.json"
SOURCES = tuple(dict.fromkeys((
    "scripts/qualify_full_native_model.py", CONFIG, CANDIDATE,
    "beliefkv/experiments/agentx_tokens.py", "beliefkv/runtime/event_channel.py",
    "beliefkv/runtime/sglang_adapter.py", "beliefkv/runtime/v0520_native_telemetry.py",
    *components.SOURCES,
)))
LABELS = ("cold", "resident", "tool_child", "demand", "join_child", "join")
PARENT_LABELS = ("cold", "resident", "demand", "join")


def configuration():
    document = json.loads((ROOT / CONFIG).read_bytes())
    if (
        document.get("schema_version") != 1
        or document.get("purpose") != "bounded_full_sessionless_model_qualification"
        or document.get("candidate") != CANDIDATE or document.get("max_requests") != 6
        or document.get("sampling_seed") != 21
        or document.get("whole_population_qualified") is not False
        or document.get("performance_accepted") is not False
        or any(type(document.get(name)) is not int or document[name] <= 0 for name in (
            "prompt_tokens_min", "prompt_tokens_max", "child_prompt_tokens",
            "generated_tokens", "stage_timeout_seconds",
        ))
        or not 65536 <= document["prompt_tokens_min"] <= document["prompt_tokens_max"] <= 131072
        or not 1 <= document["generated_tokens"] <= 32
        or not 1 <= document["child_prompt_tokens"] <= 1024
        or type(document.get("max_logprob_absolute_delta")) not in (int, float)
        or not math.isfinite(document["max_logprob_absolute_delta"])
        or not 0 < document["max_logprob_absolute_delta"] <= .05
    ):
        raise ValueError("Full model qualifier requires its bounded declared configuration")
    return document


def select_probe(index, document):
    candidates = [
        (row["prompt_tokens"], identity) for identity, row in index.items()
        if document["prompt_tokens_min"] <= row["prompt_tokens"] <= document["prompt_tokens_max"]
    ]
    if not candidates:
        raise ValueError("published population has no bounded long-prefix model probe")
    return min(candidates)[1]


def payload(request, tokens, metadata, document):
    return {
        "rid": request, "input_ids": list(tokens),
        "beliefkv_metadata": metadata.to_wire(), "extra_key": "full-model:" + metadata.invocation_id,
        "cache_salt": "full-model:" + metadata.invocation_id,
        "sampling_params": {
            "temperature": 0., "top_k": 1, "max_new_tokens": document["generated_tokens"],
            "min_new_tokens": document["generated_tokens"], "ignore_eos": True,
            "sampling_seed": document["sampling_seed"],
        },
        "stream": False, "return_logprob": True, "logprob_start_len": -1,
        "return_text_in_logprobs": False,
    }


def response_record(result, *, request, prompt_tokens, document):
    meta = result.get("meta_info") if type(result) is dict else None
    values = meta.get("output_token_logprobs") if type(meta) is dict else None
    count = document["generated_tokens"]
    if (
        type(meta) is not dict or meta.get("id") != request
        or meta.get("prompt_tokens") != prompt_tokens or meta.get("completion_tokens") != count
        or meta.get("finish_reason") != {"type": "length", "length": count}
        or type(values) is not list or len(values) != count
        or any(
            type(row) not in (list, tuple) or len(row) < 2
            or type(row[0]) not in (int, float) or not math.isfinite(row[0]) or row[0] > 1e-5
            or type(row[1]) is not int or row[1] < 0
            for row in values
        )
    ):
        raise ValueError("unforced model probe lacks actual finite token logprobs and usage")
    output = [row[1] for row in values]
    if "output_ids" in result and result["output_ids"] != output:
        raise ValueError("native output IDs differ from the model logprob token IDs")
    cached = meta.get("cached_tokens_details") or {}
    if any(type(cached.get(name, 0)) is not int or cached.get(name, 0) < 0 for name in ("device", "host")):
        raise ValueError("native model probe cache-source counters are invalid")
    return {
        "request_id": request, "prompt_tokens": prompt_tokens, "output_token_ids": output,
        "output_logprobs": [row[0] for row in values],
        "cached_tokens": meta.get("cached_tokens", 0), "cached_tokens_details": cached,
        "native_finish_reason": meta["finish_reason"],
        "unforced_sampling": True,
    }


def compare_outputs(rows, document):
    if set(rows) != set(LABELS):
        raise ValueError("Full model qualifier did not complete all six declared requests")
    reference = rows["resident"]
    deltas = {}
    for name in PARENT_LABELS:
        row = rows[name]
        if row["output_token_ids"] != reference["output_token_ids"]:
            raise ValueError("Full recovery changed actual greedy model output tokens: " + name)
        delta = max(abs(a - b) for a, b in zip(
            row["output_logprobs"], reference["output_logprobs"], strict=True,
        ))
        if delta > document["max_logprob_absolute_delta"]:
            raise ValueError("Full recovery changed model logprobs beyond the declared tolerance: " + name)
        deltas[name] = delta
    return {"maximum_logprob_absolute_delta": deltas, "actual_greedy_tokens_equal": True}


def validate_lifecycle(rows, stages, final, document):
    minimum = rows["resident"]["prompt_tokens"] - 1
    if (
        minimum < document["prompt_tokens_min"] - 1
        or rows["resident"]["cached_tokens_details"].get("device", 0) < minimum
        or rows["demand"]["cached_tokens_details"].get("host", 0) < minimum
        or rows["join"]["cached_tokens_details"].get("device", 0) < minimum
        or stages["tool_demoted"]["full_prefix_migration"]["counts"].get("demoted_tokens", 0) < minimum
        or stages["join_restored"]["full_prefix_migration"]["counts"].get("h2d_enqueued_tokens", 0) < minimum
        or stages["join_restored"]["full_prefix_migration"].get("native_pending_commands") != 0
        or final.get("physical_disabled") is not False or final.get("paper_baseline") is not None
        or final.get("internal_comparison_mode") != "full"
        or final.get("agent_admission", {}).get("counts", {}).get("native_restore_consumers", 0) != 1
        or final.get("agent_admission", {}).get("recovery_consumer") is not None
        or final.get("full_prefix_migration", {}).get("native_pending_commands") != 0
        or final.get("full_prefix_migration", {}).get("requests") != 0
        or final.get("full_prefix_migration", {}).get("contexts") != 0
        or final.get("full_prefix_migration", {}).get("counts", {}).get("completed") != 6
        or final.get("full_prefix_migration", {}).get("counts", {}).get("confirmed_join_restore_batches", 0) <= 0
    ):
        raise ValueError("Full model probes did not prove actual demotion, demand/JOIN recovery and consumption")
    return {
        "resident_prefix_reused": True, "native_demand_host_prefix_reused": True,
        "controlled_join_prefetch_reused": True, "actual_native_restore_consumers": 1,
        "all_contexts_and_receipts_drained": True,
    }


def environment(registration, run):
    settings, flags, socket = controls.environment(
        {"arms": {"full": registration["reference_environment"]}}, "full", ROOT,
        Path(registration["engine_checkout"]), run, registration["port"], run / "plugin",
        sampling_mode="position", max_running_requests=24, tool_restore_mode="execution",
    )
    declaration = json.loads((ROOT / CANDIDATE).read_bytes())
    for name, value in declaration["policy_overrides"].items():
        settings[name] = value
        settings["BELIEFKV_" + name] = value
    # No replay processor or forced sampler participates in the numerical check.
    settings.update(BELIEFKV_CAUSAL_REPLAY_BRIDGE="0", SGLANG_PLUGINS="")
    return settings, flags, socket


async def probe(registration, run, socket):
    document = registration["configuration"]
    observations = run / "opportunities/admission_opportunities.jsonl"
    workflow, sequence = "full-native-model-probe", 0
    rows, stages = {}, {}
    with ExitStack() as stack:
        corpus = stack.enter_context(AgentXTokenCorpus(
            Path(registration["tokens"]), Path(registration["prepared"]),
        ))
        tokens = corpus.get(registration["probe_request"]).prompt_tokens
        log = stack.enter_context(JsonlRuntimeEventSink(run / "probe_events.jsonl"))
        sink = stack.enter_context(UnixDatagramRuntimeEventSink(socket))

        async def send(kind, *, invocation=None, **kwargs):
            nonlocal sequence
            sequence += 1
            event = RuntimeEvent(
                "full-model:" + str(sequence), time.monotonic() * 1000., kind, workflow,
                invocation_id=invocation, context_id=invocation, context_epoch=0,
                **kwargs,
            )
            log.emit_batch((event,))
            await asyncio.to_thread(sink.emit_batch, (event,))

        async def create(identity, parent=None):
            await send(
                Kind.INVOCATION_CREATE, invocation=identity, parent_invocation_id=parent,
                relation_type=RelationType.SPAWN if parent else RelationType.ROOT,
                context_mode=ContextMode.FRESH,
                execution_mode=ExecutionMode.BACKGROUND if parent else ExecutionMode.FOREGROUND,
                agent_definition_id="controlled_model_fixture", agent_instance_id=identity,
            )
            if parent:
                await send(
                    Kind.SPAWN, invocation=parent, target_invocation_id=identity,
                    execution_mode=ExecutionMode.BACKGROUND,
                )

        async def wait_stage(label, ready):
            deadline = time.monotonic() + document["stage_timeout_seconds"]
            while True:
                runtime = controls.qualified.last_runtime_state(observations)
                if runtime is not None and ready(runtime):
                    stages[label] = runtime
                    controls.reservation.write_json(run / "probe_stages.json", stages)
                    return runtime
                if time.monotonic() >= deadline:
                    raise TimeoutError("Full real-model stage did not reach its actual lifecycle boundary: " + label)
                await asyncio.sleep(.05)

        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{registration['port']}", timeout=300.,
        ) as client:
            async def call(label, identity, prompt):
                request = "full-model:" + label
                await send(Kind.LLM_SUBMIT, invocation=identity, attributes={"request_id": request})
                metadata = BeliefKVRequestMetadata(
                    workflow, identity, identity, 0,
                    agent_definition_id="controlled_model_fixture", agent_instance_id=identity,
                    parent_invocation_id="parent" if identity != "parent" else None,
                    relation_type="spawn" if identity != "parent" else "root",
                    execution_mode="background" if identity != "parent" else "foreground",
                    full_prompt_replay_guaranteed=True,
                )
                body = payload(request, prompt, metadata, document)
                # Preserve the actual submitted sampler independently of response checks.
                controls.reservation.write_json(run / ("request_" + label + ".json"), body)
                response = await client.post("/generate", json=body)
                response.raise_for_status()
                result = response.json()
                controls.reservation.write_json(run / ("response_" + label + ".json"), result)
                rows[label] = response_record(
                    result, request=request, prompt_tokens=len(prompt), document=document,
                )
                await send(Kind.LLM_RESULT, invocation=identity, attributes={"request_id": request})
                return rows[label]

            await send(Kind.WORKFLOW_START)
            await create("parent")
            await call("cold", "parent", tokens)
            await call("resident", "parent", tokens)
            minimum = len(tokens) - 1
            await create("tool-child", "parent")
            await send(
                Kind.TOOL_START, invocation="parent",
                attributes={"tool_run_id": "controlled-tool", "tool_family": "controlled_fixture"},
            )
            await call("tool_child", "tool-child", tokens[:document["child_prompt_tokens"]])
            await send(Kind.RETURN, invocation="tool-child", attributes={"context_retired": False})
            await wait_stage("tool_demoted", lambda state: (
                state.get("full_prefix_migration", {}).get("counts", {}).get("demoted_tokens", 0) >= minimum
                and state.get("full_prefix_migration", {}).get("native_pending_commands") == 0
            ))
            await send(Kind.TOOL_END, invocation="parent", attributes={"tool_run_id": "controlled-tool"})
            await call("demand", "parent", tokens)
            previous = stages["tool_demoted"]["full_prefix_migration"]["counts"]["demoted_tokens"]
            await create("join-child", "parent")
            await send(Kind.JOIN_CREATE, join_id="controlled-join", member_invocation_ids=("join-child",))
            await send(Kind.JOIN_WAIT, invocation="parent", join_id="controlled-join")
            await wait_stage("join_demoted", lambda state: (
                state.get("full_prefix_migration", {}).get("counts", {}).get("demoted_tokens", 0) >= previous + minimum
                and state.get("full_prefix_migration", {}).get("native_pending_commands") == 0
            ))
            await call("join_child", "join-child", tokens[:document["child_prompt_tokens"]])
            await send(Kind.RETURN, invocation="join-child", attributes={"context_retired": False})
            await send(Kind.JOIN_SATISFIED, join_id="controlled-join")
            await wait_stage("join_restored", lambda state: (
                state.get("full_prefix_migration", {}).get("counts", {}).get("h2d_enqueued_tokens", 0) >= minimum
                and state.get("full_prefix_migration", {}).get("native_pending_commands") == 0
            ))
            await call("join", "parent", tokens)
            await send(Kind.RETURN, invocation="parent", attributes={"context_retired": False})
            await send(Kind.WORKFLOW_END)
            final = await wait_stage("drained", lambda state: (
                state.get("full_prefix_migration", {}).get("contexts") == 0
                and state.get("full_prefix_migration", {}).get("requests") == 0
                and state.get("full_prefix_migration", {}).get("native_pending_commands") == 0
            ))
    return {
        "requests": rows, "stages": stages,
        **compare_outputs(rows, document), **validate_lifecycle(rows, stages, final, document),
        "unforced_native_sampler": True, "original_agentx_semantic_eos_used": False,
    }


def check_registration(registration):
    if registration["source_sha256"] != {name: sha256_file(ROOT / name) for name in SOURCES}:
        raise ValueError("Full model sources differ from their frozen registration")
    if registration["configuration"] != configuration():
        raise ValueError("Full model probe declaration differs from the frozen configuration")
    for path, expected in (
        (Path(registration["prepared"]) / "preparation.json", registration["preparation_sha256"]),
        (Path(registration["tokens"]) / "manifest.json", registration["token_manifest_sha256"]),
        (Path(registration["after_run_root"]) / "registration.json", registration["predecessor_registration_sha256"]),
    ):
        if sha256_file(path) != expected:
            raise ValueError("Full model published input or predecessor registration changed")
    identity = json.loads((Path(registration["after_run_root"]) / "supervisor.json").read_bytes())
    if identity != registration["predecessor_supervisor"]:
        raise ValueError("Full model predecessor process identity changed")


def start(args):
    run = args.run_root.resolve()
    if run.exists():
        raise FileExistsError("preserve earlier Full model qualification evidence")
    reference = json.loads(args.reference.read_bytes())
    controls.fresh.check_hold(reference, released=True)
    document = configuration()
    if reference["source_server_parameters"]["max_running_requests"] != 24:
        raise ValueError("Full model fixture preserves the common running/decode capacity")
    predecessor = args.after_run_root.resolve()
    identity = json.loads((predecessor / "supervisor.json").read_bytes())
    with AgentXTokenCorpus(args.tokens, args.prepared) as corpus:
        evidence = corpus.evidence
    if (
        sha256_file(args.prepared / "preparation.json") != reference["preparation_sha256"]
        or sha256_file(args.tokens / "manifest.json") != reference["token_manifest_sha256"]
    ):
        raise ValueError("Full model qualifier changed the common published population")
    index = json.loads((args.tokens / "index.json").read_bytes())
    selected = select_probe(index, document)
    run.mkdir(parents=True)
    frozen = freeze(args.engine_checkout, args.canonical_patch)
    checkout = Path(frozen["checkout"])
    registration = {
        **frozen, "run_root": str(run), "port": args.port,
        "prepared": str(args.prepared.resolve()), "tokens": str(args.tokens.resolve()),
        "preparation_sha256": sha256_file(args.prepared / "preparation.json"),
        "token_manifest_sha256": sha256_file(args.tokens / "manifest.json"),
        "token_artifacts": evidence["artifacts"], "probe_request": selected,
        "probe_usage": index[selected], "configuration": document,
        "source_capacity": reference["source_capacity"],
        "source_server_parameters": reference["source_server_parameters"],
        "reference_environment": reference["reference_environment"], "hold_file": reference["hold_file"],
        "after_run_root": str(predecessor), "predecessor_supervisor": identity,
        "predecessor_registration_sha256": sha256_file(predecessor / "registration.json"),
        "source_sha256": {name: sha256_file(checkout / name) for name in SOURCES},
        "scope": "bounded_unforced_full_native_model_recovery_not_full_population_or_performance",
        "performance_accepted": False,
    }
    controls.reservation.write_json(run / "registration.json", registration)
    with (run / "supervisor.log").open("x") as log:
        process = subprocess.Popen([
            sys.executable, str(checkout / "scripts/qualify_full_native_model.py"),
            "watch", "--registration", str(run / "registration.json"),
        ], cwd=checkout, env={**os.environ, "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
    supervisor = controls.reservation.process(process.pid)
    if supervisor is None:
        raise ValueError("Full model supervisor has no owned process identity")
    controls.reservation.write_json(run / "supervisor.json", supervisor)
    print(json.dumps({"status": "supervisor_started", "run": str(run), "supervisor": supervisor}, indent=2))


def qualify_components(registration, run, pulse, owned):
    output = run / "native_components"
    with (run / "native_components.log").open("x") as log:
        process = subprocess.Popen([
            sys.executable, str(ROOT / "scripts/qualify_full_prefix_migration.py"), "run",
            "--engine-checkout", registration["engine_checkout"],
            "--canonical-patch", str(ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
            "--output-dir", str(output),
        ], cwd=ROOT, env=prefix_qualification_environment(registration, os.environ),
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        identity = controls.reservation.process(process.pid)
        if identity is None:
            raise ValueError("Full model component qualifier has no owned process")
        owned.append(identity)
        deadline = time.monotonic() + 600.
        while process.poll() is None:
            pulse()
            if time.monotonic() >= deadline:
                raise TimeoutError("Full model source-bound component qualification expired")
            time.sleep(.5)
        if process.wait():
            raise ValueError("Full model component qualification failed; preserve its evidence")
    report = json.loads((output / "summary.json").read_bytes())
    components.validate_report(
        report, engine_tree=registration["engine_tree"],
        patch_sha256=registration["native_patch_sha256"],
    )
    return report


def watch(path):
    registration = json.loads(path.read_bytes())
    run, owned, server, stopped = Path(registration["run_root"]), [], None, False
    (run / "server").mkdir()
    state = {"status": "waiting_predecessor_or_resources", "performance_accepted": False}

    def stop(*_args):
        nonlocal stopped
        stopped = True

    def pulse():
        if stopped:
            raise ValueError("Full model supervisor received a stop request")
        controls.fresh.check_hold(registration, released=True)
        owned[:] = controls.owned_processes(owned)
        state.update(checked_at=controls.reservation.now().isoformat(), owned=owned)
        controls.reservation.write_json(run / "state.json", state)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (run / "supervisor.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            check_registration(registration)
            while not predecessor_finished(registration["after_run_root"]) or not (
                controls.qualified.resources(registration["port"])["ready"]
            ):
                pulse()
                time.sleep(5.)
            pulse()
            check_registration(registration)
            state["status"] = "qualifying_source_bound_full_components"
            state["native_components"] = qualify_components(registration, run, pulse, owned)
            settings, flags, socket = environment(registration, run)
            if socket.exists():
                raise ValueError("Full model qualifier requires a fresh native event socket")
            controls.reservation.write_json(run / "launch_environment.json", settings)
            pulse()
            if not controls.qualified.resources(registration["port"])["ready"]:
                raise ValueError("GPU resources changed before Full model startup")
            with (run / "server.log").open("x") as log:
                server = subprocess.Popen(
                    ["bash", str(ROOT / "scripts/launch_qwen35_native_v0520.sh"), *flags],
                    cwd=ROOT, env=controls.qualified.driver_environment({"environment": settings}, os.environ),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            identity = controls.reservation.process(server.pid)
            if identity is None:
                raise ValueError("Full model server has no owned process identity")
            owned.append(identity)
            state["status"] = "starting_full_model"
            deadline = time.monotonic() + 480.
            while True:
                pulse()
                if server.poll() is not None:
                    raise ValueError("Full model server exited during startup")
                try:
                    ready = (
                        httpx.get(f"http://127.0.0.1:{registration['port']}/health", timeout=2.).is_success
                        and (run / "server/native_telemetry_ready.json").is_file()
                        and (run / "server/native_capacity_census.json").is_file()
                    )
                except httpx.HTTPError:
                    ready = False
                if ready:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Full model server startup expired")
                time.sleep(1.)
            info = httpx.get(
                f"http://127.0.0.1:{registration['port']}/get_server_info", timeout=10.,
            ).json()
            expected = {
                **registration["source_server_parameters"], "model_path": settings["MODEL_PATH"],
                "random_seed": 21, "enable_beliefkv_admission": True,
                "enable_custom_logit_processor": True, "enable_session_radix_cache": True,
            }
            if any(info.get(name) != value for name, value in expected.items()):
                raise ValueError("Full model server changed its common serving parameters")
            capacity = json.loads((run / "server/native_capacity_census.json").read_bytes())["capacity"]
            if any(capacity.get(name) != registration["source_capacity"][name] for name in (
                "device_full_tokens", "host_full_tokens",
            )):
                raise ValueError("Full model server changed the common KV pool capacities")
            controls.reservation.write_json(run / "server/server_info.json", info)
            scheduler = json.loads((run / "server/native_telemetry_ready.json").read_bytes())["scheduler_pid"]
            pulse()
            gpu_pids = controls.qualified.command([
                "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
            ]).splitlines()
            if scheduler not in {row["pid"] for row in state["owned"]} or str(scheduler) not in gpu_pids:
                raise ValueError("Full model scheduler is not an owned GPU process")
            state.update(status="running_unforced_model_probes", gpu_process_verified=True)

            async def observed():
                task = asyncio.create_task(probe(registration, run, socket))
                try:
                    while not task.done():
                        pulse()
                        if server.poll() is not None:
                            raise ValueError("Full model server exited during probes")
                        await asyncio.wait((task,), timeout=.5)
                    return await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

            state["probe"] = asyncio.run(asyncio.wait_for(observed(), timeout=1200.))
            controls.wait_for_telemetry_drain(run, server=server, pulse=pulse)
            state.update(
                status="full_native_model_qualified", model_recovery_probes_qualified=True,
                scope=registration["scope"], whole_population_qualified=False, performance_accepted=False,
            )
        except BaseException as error:
            state.update(status="failed", error_type=type(error).__name__, error=str(error))
        finally:
            try:
                controls.cleanup(run, owned, state["status"])
            except BaseException as error:
                state.update(status="failed", cleanup_error=repr(error))
            state.update(
                finished_at=controls.reservation.now().isoformat(),
                source_sha256=registration.get("source_sha256", {}),
            )
            controls.reservation.write_json(run / "summary.json", state)
            controls.reservation.write_json(run / "state.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("start")
    for name in ("run-root", "reference", "engine-checkout", "canonical-patch", "prepared", "tokens", "after-run-root"):
        launch.add_argument("--" + name, required=True, type=Path)
    launch.add_argument("--port", required=True, type=int)
    monitor = commands.add_parser("watch")
    monitor.add_argument("--registration", required=True, type=Path)
    args = parser.parse_args()
    start(args) if args.command == "start" else watch(args.registration)


if __name__ == "__main__":
    main()
