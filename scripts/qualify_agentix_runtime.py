#!/usr/bin/env python3
"""Queue source-bound KV-byte and bounded native-LLM preemption qualification."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from dataclasses import replace
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
from beliefkv.experiments.agentix_qualification import (
    QUALIFICATION_POLICY, QUALIFICATION_PURPOSE, load_qualification,
)
from beliefkv.experiments.agentx_tokens import AgentXTokenCorpus
from beliefkv.experiments.causal_replay_backend import CausalHTTPBackend
from beliefkv.experiments.paper_baselines import DISABLED_POLICIES, isolated_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_metrics import StreamingWorkflowMetrics
from beliefkv.runtime.event_channel import JsonlRuntimeEventSink
from scripts import run_causal_comparison as controls
from scripts.run_agentx_baseline import (
    freeze, predecessor_finished, prefix_qualification_environment, qualify_native_prefix,
)


SOURCES = (
    "scripts/qualify_agentix_runtime.py", "scripts/qualify_agentix_swap.py",
    "scripts/run_agentx_baseline.py", "beliefkv/experiments/agentix_qualification.py",
    "beliefkv/experiments/paper_baselines.py", "beliefkv/experiments/causal_replay_backend.py",
    "beliefkv/experiments/agentx_tokens.py", "scripts/run_causal_comparison.py",
    "beliefkv/policy/agentix.py", "beliefkv/policy/program_service.py",
    "beliefkv/runtime/agentix_baseline.py", "beliefkv/runtime/agentix_capacity.py",
    "beliefkv/runtime/agentix_executor.py", "beliefkv/runtime/agentix_lifecycle.py",
    "beliefkv/runtime/agentix_swap.py", "beliefkv/runtime/paper_prefix_transfers.py",
    "beliefkv/runtime/paper_baseline.py", "beliefkv/runtime/sglang_v0520_runtime.py",
    "beliefkv/runtime/v0520_native_telemetry.py",
    "beliefkv/runtime/sglang_v0520_admission.py", "beliefkv/runtime/event_channel.py",
)
SWAP_PROPERTIES = (
    "key_and_value_bytes_preserved_all_layers", "live_peer_kv_preserved",
    "released_tail_poisoned_before_restore", "no_device_capacity_release_before_d2h_ack",
    "host_pin_protected_until_actual_resumption", "native_h2d_lock_survived_pin_handoff",
    "split_complete_host_prefix_protected_under_pressure", "input_output_and_sampler_preserved",
)


def select_probe(index):
    eligible = [
        (row["output_tokens"], row["prompt_tokens"], identity)
        for identity, row in index.items()
        if 65536 <= row["prompt_tokens"] <= 131072 and 16 <= row["output_tokens"] <= 128
    ]
    if not eligible:
        raise ValueError("published tokens have no bounded long-prefix preemption probe")
    return min(eligible)[2]


def validate_swap(report, registration):
    if (
        report.get("status") != "native_kv_swap_interface_qualified"
        or report.get("native_engine", {}).get("actual_tree") != registration["engine_tree"]
        or report.get("native_engine", {}).get("sha256") != registration["native_patch_sha256"]
        or any(report.get(name) is not True for name in SWAP_PROPERTIES)
        or report.get("whole_llm_scheduler_or_baseline_qualified") is not False
        or report.get("performance_accepted") is not False
        or any(
            report.get("source_sha256", {}).get(name) != sha256_file(ROOT / name)
            for name in (
                "scripts/qualify_agentix_swap.py", "beliefkv/runtime/agentix_swap.py",
                "beliefkv/runtime/paper_prefix_transfers.py",
            )
        )
    ):
        raise ValueError("native Agentix swap evidence is missing, stale or overclaimed")


def validate_runtime(state, document):
    paper = state.get("paper_baseline") or {}
    calls, execution, swap = (
        paper.get("calls") or {}, paper.get("execution") or {}, paper.get("swap") or {},
    )
    executor, policy = execution.get("executor") or {}, paper.get("policy_state") or {}
    scheduling = paper.get("native_scheduling") or {}
    completed = document["max_requests"]
    expected_config = {
        name: document["parameters"][name] for name in (
            "mode", "band_edges_ms", "quanta_ms", "starvation_ratio",
            "decode_steps_per_schedule",
        )
    }
    elapsed = executor.get("measured_executor_ms", 0)
    if (
        any(state.get(name) is not False for name in DISABLED_POLICIES)
        or state.get("physical_disabled") is not False
        or state.get("agent_admission") is not None
        or state.get("internal_comparison_mode") != "scheduler_only"
        or paper.get("policy") != "agentix_atlas_paper_native"
        or paper.get("program_identity") != "root_workflow_id"
        or paper.get("future_information_used") is not False
        or paper.get("native_capacity_authority_preserved") is not True
        or paper.get("declared_config") != expected_config
        or any(
            paper.get(name) != document["parameters"][name]
            for name in ("context_limit", "running_limit", "decode_growth_tokens")
        )
        or paper.get("qualification") != {
            "purpose": QUALIFICATION_PURPOSE, "max_requests": completed,
        }
        or paper.get("complete_baseline") is not False
        or paper.get("gpu_llm_qualified") is not False
        or calls.get("counts", {}).get("registered") != completed
        or calls.get("counts", {}).get("completed") != completed
        or calls.get("counts", {}).get("cancelled", 0) != 0
        or calls.get("native_calls") != 0
        or calls.get("terminal_calls_waiting_for_executor") != 0
        or calls.get("arrivals_waiting_for_program_completion") != 0
        or policy.get("pending_calls") != 0 or policy.get("pending_executor_launches") != 0
        or execution.get("worker_forward_hook_installed") is not True
        or execution.get("native_scheduler_bound") is not True
        or execution.get("worker_forward_failed") is not False
        or execution.get("counts", {}).get("native_worker_forwards", 0) <= completed
        or executor.get("pending_launches") != 0 or executor.get("open_forward") is not False
        or executor.get("counts", {}).get("launched", 0) != executor.get("counts", {}).get("accounted")
        or type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed <= 0
        or executor.get("gpu_synchronization_for_accounting") is not False
        or executor.get("dropped_or_estimated_executor_intervals") != 0
        or swap.get("counts", {}).get("paused_requests", 0) <= 0
        or swap.get("counts", {}).get("resumed_requests", 0) <= 0
        or swap.get("counts", {}).get("demoted_full_tokens", 0) <= 0
        or swap.get("pending_requests") != 0 or swap.get("paused_host_pins") != 0
        or scheduling.get("native_selection_hook_installed") is not True
        or scheduling.get("preemption", {}).get("counts", {}).get("committed_rounds", 0) <= 0
        or scheduling.get("preemption", {}).get("pending_selection") is not None
    ):
        raise ValueError("bounded native Agentix execution or preemption/resumption was not verified")


def qualification_environment(registration, run):
    document, _, serving = load_qualification(ROOT / registration["policy_relative_path"])
    common = {
        **registration["reference_environment"],
        "MAX_RUNNING_REQUESTS": str(serving["running_limit"]),
    }
    settings, flags, socket = controls.environment(
        {"arms": {"full": common}}, "scheduler_only", ROOT,
        Path(registration["engine_checkout"]), run, registration["port"], run / "plugin",
        sampling_mode="position", max_running_requests=serving["running_limit"],
        tool_restore_mode="execution",
    )
    settings = isolated_environment(settings)
    settings.update({
        "BELIEFKV_PAPER_BASELINE": QUALIFICATION_POLICY,
        "BELIEFKV_AGENTIX_QUALIFICATION_ONLY": "1",
        "BELIEFKV_AGENTIX_QUALIFICATION_CONFIG": str(ROOT / registration["policy_relative_path"]),
    })
    if document != registration["policy_document"] or socket is None:
        raise ValueError("Agentix qualification declaration or native event socket differs")
    return settings, flags, socket


class ProbeCorpus:
    def __init__(self, source, request):
        self.source, self.request, self.bindings = source, request, {}

    def bind(self, request, context):
        identity = "agentix-probe:" + context, "agent:" + context, "context:" + context, 0
        self.bindings[request] = identity
        return identity

    def get(self, request):
        identity = self.bindings[request]
        return replace(
            self.source.get(self.request), request_id=request, identity=identity,
            extra_key=identity[0], cache_salt=identity[0],
        )


async def probe(registration, run):
    rows, live = [], set()
    with ExitStack() as stack:
        source = stack.enter_context(AgentXTokenCorpus(
            Path(registration["tokens"]), Path(registration["prepared"]),
        ))
        corpus = ProbeCorpus(source, registration["probe_request"])
        log = stack.enter_context(JsonlRuntimeEventSink(run / "probe_events.jsonl"))
        output = stack.enter_context((run / "probe_native_requests.jsonl").open("x"))
        metrics = StreamingWorkflowMetrics()

        def record(row):
            rows.append(row)
            output.write(json.dumps(row, allow_nan=False) + "\n")
            output.flush()

        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{registration['port']}", timeout=300.,
            limits=httpx.Limits(max_connections=8),
        ) as client:
            backend = CausalHTTPBackend(
                corpus, client, log.emit_batch, observe_request=record,
                streaming=True, response_metrics=metrics,
            )

            async def call(label, context):
                rid = "agentix-native:" + label
                wid, iid, cid, epoch = corpus.bind(rid, context)
                if context not in live:
                    await backend.emit(RuntimeEvent(
                        "start:" + context, time.monotonic() * 1000., Kind.WORKFLOW_START, wid,
                    ))
                    await backend.emit(RuntimeEvent(
                        "create:" + context, time.monotonic() * 1000., Kind.INVOCATION_CREATE,
                        wid, invocation_id=iid, context_id=cid, context_epoch=epoch,
                        relation_type=RelationType.ROOT, context_mode=ContextMode.FRESH,
                        execution_mode=ExecutionMode.FOREGROUND,
                        agent_definition_id="agentix_native_qualification", agent_instance_id=iid,
                    ))
                    live.add(context)
                submit = RuntimeEvent(
                    "submit:" + label, time.monotonic() * 1000., Kind.LLM_SUBMIT, wid,
                    invocation_id=iid, context_id=cid, context_epoch=epoch,
                    attributes={"request_id": rid, "sampling_seed": 21},
                )
                await backend.emit(submit)
                await backend.execute(rid, submit)

            try:
                tasks = [
                    asyncio.create_task(call(f"parallel_{index}", str(index)))
                    for index in range(3)
                ]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                await call("resident_repeat", "0")
                for context in sorted(live):
                    await backend.emit(RuntimeEvent(
                        "return:" + context, time.monotonic() * 1000., Kind.RETURN,
                        "agentix-probe:" + context, invocation_id="agent:" + context,
                        context_id="context:" + context, context_epoch=0,
                        attributes={"context_retired": True},
                    ))
                    await backend.emit(RuntimeEvent(
                        "end:" + context, time.monotonic() * 1000., Kind.WORKFLOW_END,
                        "agentix-probe:" + context,
                    ))
            finally:
                await backend.drain()
    if len(rows) != 4 or any(row.get("exact_tokens_verified") is not True for row in rows):
        raise ValueError("bounded Agentix requests did not preserve their token contracts")
    return {
        "requests": len(rows), "streaming": metrics.summary(),
        "token_contract_verified": True, "model_kv_numerical_equivalence_verified": False,
    }


def check_registration(registration):
    expected = set(SOURCES) | {registration["policy_relative_path"]}
    if set(registration["source_sha256"]) != expected:
        raise ValueError("Agentix qualification source manifest is incomplete")
    for relative, expected in registration["source_sha256"].items():
        if sha256_file(ROOT / relative) != expected:
            raise ValueError("Agentix qualification source changed after freezing")
    document, _, _ = load_qualification(ROOT / registration["policy_relative_path"])
    if document != registration["policy_document"]:
        raise ValueError("Agentix qualification policy differs from its frozen declaration")
    for path, expected in (
        (Path(registration["prepared"]) / "preparation.json", registration["preparation_sha256"]),
        (Path(registration["tokens"]) / "manifest.json", registration["token_manifest_sha256"]),
        (Path(registration["after_run_root"]) / "registration.json", registration["predecessor_registration_sha256"]),
    ):
        if sha256_file(path) != expected:
            raise ValueError("Agentix qualification input or predecessor registration changed")
    identity = json.loads((Path(registration["after_run_root"]) / "supervisor.json").read_bytes())
    if identity != registration["predecessor_supervisor"]:
        raise ValueError("Agentix qualification predecessor process identity changed")


def start(args):
    run = args.run_root.resolve()
    if run.exists():
        raise FileExistsError("preserve earlier Agentix qualification artifacts")
    reference = json.loads(args.reference.read_bytes())
    controls.fresh.check_hold(reference, released=True)
    document, _, serving = load_qualification(args.policy_config)
    if (
        document["max_requests"] != 4 or serving["running_limit"] != 1
        or serving["context_limit"] != reference["source_server_parameters"]["context_length"]
    ):
        raise ValueError("bounded Agentix probe requires its four-request, single-running declaration")
    relative = str(args.policy_config.resolve().relative_to(ROOT))
    predecessor = args.after_run_root.resolve()
    prior = json.loads((predecessor / "registration.json").read_bytes())
    if any(prior[key] != reference[key] for key in ("prepared", "tokens", "source_capacity")) or (
        str(args.prepared.resolve()) != str(Path(reference["prepared"]).resolve())
        or str(args.tokens.resolve()) != str(Path(reference["tokens"]).resolve())
    ):
        raise ValueError("Agentix qualifier predecessor does not use the same published population")
    with AgentXTokenCorpus(args.tokens, args.prepared) as corpus:
        evidence = corpus.evidence
    index = json.loads((args.tokens / "index.json").read_bytes())
    selected = select_probe(index)
    if (
        sha256_file(args.prepared / "preparation.json") != reference["preparation_sha256"]
        or sha256_file(args.tokens / "manifest.json") != reference["token_manifest_sha256"]
    ):
        raise ValueError("Agentix qualifier published inputs differ from the predecessor")
    run.mkdir(parents=True)
    frozen = freeze(args.engine, args.native_patch)
    frozen_root = Path(frozen["checkout"])
    registration = {
        **frozen, "run_root": str(run), "port": args.port,
        "prepared": str(args.prepared.resolve()), "tokens": str(args.tokens.resolve()),
        "preparation_sha256": sha256_file(args.prepared / "preparation.json"),
        "token_manifest_sha256": sha256_file(args.tokens / "manifest.json"),
        "token_artifacts": evidence["artifacts"], "hold_file": reference["hold_file"],
        "reference_environment": reference["reference_environment"],
        "source_server_parameters": reference["source_server_parameters"],
        "source_capacity": reference["source_capacity"], "probe_request": selected,
        "probe_usage": index[selected], "policy_relative_path": relative,
        "policy_document": document, "after_run_root": str(predecessor),
        "predecessor_registration_sha256": sha256_file(predecessor / "registration.json"),
        "predecessor_supervisor": json.loads((predecessor / "supervisor.json").read_bytes()),
        "source_sha256": {name: sha256_file(frozen_root / name) for name in (*SOURCES, relative)},
        "scope": "bounded_native_runtime_qualification_not_full_baseline_or_source_capture",
        "declared_serving_change": {"max_running_requests": serving["running_limit"]},
        "performance_accepted": False,
    }
    controls.reservation.write_json(run / "registration.json", registration)
    with (run / "supervisor.log").open("x") as log:
        process = subprocess.Popen([
            sys.executable, str(frozen_root / "scripts/qualify_agentix_runtime.py"),
            "watch", "--registration", str(run / "registration.json"),
        ], cwd=frozen_root, env={**os.environ, "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    identity = controls.reservation.process(process.pid)
    controls.reservation.write_json(run / "supervisor.json", identity)
    print(json.dumps({"status": "supervisor_started", "run": str(run), "supervisor": identity}, indent=2))


def qualify_swap(registration, run, *, pulse, owned):
    output = run / "native_swap_qualification"
    with (run / "native_swap_qualification.log").open("x") as log:
        process = subprocess.Popen([
            sys.executable, str(ROOT / "scripts/qualify_agentix_swap.py"),
            "--engine-checkout", registration["engine_checkout"],
            "--canonical-patch", str(ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
            "--output-dir", str(output),
        ], cwd=ROOT, env=prefix_qualification_environment(registration, os.environ),
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        identity = controls.reservation.process(process.pid)
        if identity is None:
            raise ValueError("native Agentix swap qualifier has no owned process")
        owned.append(identity)
        deadline = time.monotonic() + 180.
        while process.poll() is None:
            pulse()
            if time.monotonic() >= deadline:
                raise TimeoutError("native Agentix swap qualification expired")
            time.sleep(.5)
        if process.wait():
            raise ValueError("native Agentix swap qualification failed; retain its evidence")
    report = json.loads((output / "summary.json").read_bytes())
    validate_swap(report, registration)
    return {"report": str(output / "summary.json"), **report}


def watch(path):
    registration = json.loads(path.read_bytes())
    run = Path(registration["run_root"])
    (run / "server").mkdir()
    state = {"status": "waiting_predecessor_or_resources", "performance_accepted": False}
    identities, server, stopped = [], None, False

    def stop(*_):
        nonlocal stopped
        stopped = True

    def pulse():
        nonlocal identities
        if stopped:
            raise ValueError("Agentix qualification supervisor received a stop request")
        controls.fresh.check_hold(registration, released=True)
        identities = controls.owned_processes(identities)
        state.update(checked_at=controls.reservation.now().isoformat(), owned=identities)
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
            qualifier_owned = []

            def qualifier_pulse():
                identities.extend(
                    row for row in qualifier_owned
                    if not any(old["pid"] == row["pid"] for old in identities)
                )
                pulse()

            state["status"] = "qualifying_native_prefix_and_swap"
            state["native_prefix"] = qualify_native_prefix(
                registration, run, pulse=qualifier_pulse, owned=qualifier_owned,
            )
            state["native_swap"] = qualify_swap(
                registration, run, pulse=qualifier_pulse, owned=qualifier_owned,
            )
            with (run / "plugin_install.log").open("x") as log:
                subprocess.run([
                    sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                    "--no-build-isolation", "--target", str(run / "plugin"),
                    str(ROOT / "configs/causal_replay_plugin"),
                ], check=True, stdout=log, stderr=subprocess.STDOUT, timeout=60)
            settings, flags, socket = qualification_environment(registration, run)
            if socket.exists():
                raise ValueError("Agentix qualifier requires a fresh native event socket")
            controls.reservation.write_json(run / "launch_environment.json", settings)
            pulse()
            if not controls.qualified.resources(registration["port"])["ready"]:
                raise ValueError("resources changed before bounded Agentix startup")
            with (run / "server.log").open("x") as log:
                server = subprocess.Popen(
                    ["bash", str(ROOT / "scripts/launch_qwen35_native_v0520.sh"), *flags],
                    cwd=ROOT, env=controls.qualified.driver_environment({"environment": settings}, os.environ),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
            identity = controls.reservation.process(server.pid)
            if identity is None:
                raise ValueError("bounded Agentix server has no owned process identity")
            identities.append(identity)
            state["status"] = "starting_server"
            deadline = time.monotonic() + 480.
            while True:
                pulse()
                if server.poll() is not None:
                    raise ValueError("bounded Agentix server exited during startup")
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
                    raise TimeoutError("bounded Agentix startup expired")
                time.sleep(1.)
            info = httpx.get(
                f"http://127.0.0.1:{registration['port']}/get_server_info", timeout=10.,
            ).json()
            expected = {
                **registration["source_server_parameters"],
                "model_path": settings["MODEL_PATH"], "max_running_requests": 1,
                "context_length": 262144, "random_seed": 21,
                "enable_beliefkv_admission": True, "enable_custom_logit_processor": True,
                "enable_session_radix_cache": True,
            }
            if any(info.get(name) != value for name, value in expected.items()):
                raise ValueError("bounded Agentix changed its declared native serving contract")
            census = json.loads((run / "server/native_capacity_census.json").read_bytes())["capacity"]
            if any(census.get(name) != registration["source_capacity"][name] for name in (
                "device_full_tokens", "host_full_tokens",
            )):
                raise ValueError("bounded Agentix changed the common KV pool capacities")
            controls.reservation.write_json(run / "server/server_info.json", info)
            scheduler = json.loads((run / "server/native_telemetry_ready.json").read_bytes())["scheduler_pid"]
            pulse()
            apps = controls.qualified.command([
                "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
            ]).splitlines()
            if scheduler not in {row["pid"] for row in identities} or str(scheduler) not in apps:
                raise ValueError("bounded Agentix scheduler is not an owned GPU process")
            state.update(status="running_bounded_native_probes", gpu_process_verified=True)

            async def observed_probe():
                task = asyncio.create_task(probe(registration, run))
                try:
                    while not task.done():
                        pulse()
                        if server.poll() is not None:
                            raise ValueError("bounded Agentix server exited during probes")
                        await asyncio.wait((task,), timeout=.5)
                    return await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

            state["probe"] = asyncio.run(asyncio.wait_for(observed_probe(), timeout=600.))
            controls.wait_for_telemetry_drain(run, server=server, pulse=pulse)
            deadline = time.monotonic() + 10.
            while True:
                pulse()
                if server.poll() is not None:
                    raise ValueError("bounded Agentix server exited before its final runtime snapshot")
                try:
                    runtime = controls.qualified.last_runtime_state(
                        run / "opportunities/admission_opportunities.jsonl",
                    )
                    validate_runtime(runtime, registration["policy_document"])
                    break
                except (FileNotFoundError, ValueError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.5)
            state.update(
                status="agentix_native_runtime_qualified", runtime=runtime,
                scope=registration["scope"], engine_tree=registration["engine_tree"],
                native_patch_sha256=registration["native_patch_sha256"],
                host_backing_gpu_pressure_qualified=False, full_population_qualified=False,
                performance_accepted=False,
            )
        except BaseException as error:
            state.update(status="failed", error_type=type(error).__name__, error=str(error))
        finally:
            try:
                controls.cleanup(run, identities, state["status"])
            except BaseException as error:
                state.update(status="failed", cleanup_error=repr(error))
            state.update(
                finished_at=controls.reservation.now().isoformat(),
                source_sha256=registration["source_sha256"],
            )
            controls.reservation.write_json(run / "summary.json", state)
            controls.reservation.write_json(run / "state.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("start")
    for name in ("run-root", "reference", "engine", "native-patch", "prepared", "tokens", "after-run-root"):
        launch.add_argument("--" + name, required=True, type=Path)
    launch.add_argument(
        "--policy-config", type=Path,
        default=ROOT / "configs/baselines/agentix_native_qualification_20261025.json",
    )
    launch.add_argument("--port", required=True, type=int)
    monitor = commands.add_parser("watch")
    monitor.add_argument("--registration", required=True, type=Path)
    args = parser.parse_args()
    start(args) if args.command == "start" else watch(args.registration)


if __name__ == "__main__":
    main()
