#!/usr/bin/env python3
"""Queue bounded model, namespace and Host-recovery checks for a frozen engine."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from dataclasses import replace
import fcntl
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
from beliefkv.experiments.agentx_tokens import AgentXTokenCorpus
from beliefkv.experiments.causal_replay_backend import CausalHTTPBackend
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_metrics import StreamingWorkflowMetrics
from beliefkv.runtime.event_channel import JsonlRuntimeEventSink
from scripts import run_causal_comparison as controls
from scripts.run_agentx_baseline import freeze, predecessor_finished, qualify_native_prefix


class ProbeCorpus:
    def __init__(self, source, request):
        self.source, self.request, self.bindings = source, request, {}

    def bind(self, request, context):
        identity = "agentx-probe:" + context, "agent:" + context, "context:" + context, 0
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
            base_url=f"http://127.0.0.1:{registration['port']}", timeout=180.,
            limits=httpx.Limits(max_connections=4),
        ) as client:
            backend = CausalHTTPBackend(
                corpus, client, log.emit_batch, observe_request=record,
                streaming=True, response_metrics=metrics,
            )

            async def call(label, context):
                rid = "agentx-data-plane:" + label
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
                        agent_definition_id="agentx_data_plane_probe", agent_instance_id=iid,
                    ))
                    live.add(context)
                event = RuntimeEvent(
                    "submit:" + label, time.monotonic() * 1000., Kind.LLM_SUBMIT, wid,
                    invocation_id=iid, context_id=cid, context_epoch=epoch,
                    attributes={"request_id": rid, "sampling_seed": 21},
                )
                await backend.emit(event)
                await backend.execute(rid, event)

            try:
                await call("cold", "original")
                await call("resident_repeat", "original")
                await call("other_workflow", "isolated")
                for ordinal in range(11):
                    await call(f"pressure_{ordinal:02d}", f"pressure_{ordinal:02d}")
                await call("host_restore", "original")
                for context in sorted(live):
                    await backend.emit(RuntimeEvent(
                        "return:" + context, time.monotonic() * 1000., Kind.RETURN,
                        "agentx-probe:" + context, invocation_id="agent:" + context,
                        context_id="context:" + context, context_epoch=0,
                        attributes={"context_retired": True},
                    ))
                    await backend.emit(RuntimeEvent(
                        "end:" + context, time.monotonic() * 1000., Kind.WORKFLOW_END,
                        "agentx-probe:" + context,
                    ))
            finally:
                await backend.drain()
    if len(rows) != 15 or any(row.get("exact_tokens_verified") is not True for row in rows):
        raise ValueError("bounded data-plane requests did not preserve all published tokens")
    return {"requests": len(rows), "streaming": metrics.summary()}


def validate_cache_observations(observations, width):
    expected = {
        "agentx-data-plane:" + label for label in (
            "cold", "resident_repeat", "other_workflow", "host_restore",
            *(f"pressure_{ordinal:02d}" for ordinal in range(11)),
        )
    }
    if (
        set(observations) != expected
        or any(
            type(row.get(name)) is not int or row[name] < 0
            for row in observations.values()
            for name in ("prompt_tokens", "cached_tokens_device", "cached_tokens_host")
        )
        or observations["agentx-data-plane:resident_repeat"]["cached_tokens_device"] < width - 64
        or observations["agentx-data-plane:other_workflow"]["cached_tokens_device"] != 0
        or observations["agentx-data-plane:other_workflow"]["cached_tokens_host"] != 0
        or observations["agentx-data-plane:host_restore"]["cached_tokens_host"] <= 0
    ):
        raise ValueError("native residency, namespace isolation or Host recovery was not observed")


def start(args):
    run = args.run_root.resolve()
    if run.exists():
        raise FileExistsError("preserve prior data-plane qualification artifacts")
    reference = json.loads(args.reference.read_bytes())
    controls.fresh.check_hold(reference, released=True)
    with AgentXTokenCorpus(args.tokens, args.prepared) as corpus:
        evidence = corpus.evidence
    index = json.loads((args.tokens / "index.json").read_bytes())
    eligible = [
        (row["output_tokens"], rid) for rid, row in index.items()
        if 65536 <= row["prompt_tokens"] <= 131072 and 1 < row["output_tokens"] <= 64
    ]
    if not eligible:
        raise ValueError("shared token corpus has no bounded long-prefix probe")
    selected = min(eligible)[1]
    run.mkdir(parents=True)
    frozen = freeze(args.engine, args.native_patch)
    registration = {
        **frozen, "run_root": str(run), "port": args.port,
        "prepared": str(args.prepared.resolve()), "tokens": str(args.tokens.resolve()),
        "preparation_sha256": sha256_file(args.prepared / "preparation.json"),
        "token_manifest_sha256": sha256_file(args.tokens / "manifest.json"),
        "token_artifacts": evidence["artifacts"],
        "hold_file": reference["hold_file"],
        "reference_environment": reference["reference_environment"],
        "source_server_parameters": reference["source_server_parameters"],
        "source_capacity": reference["source_capacity"],
        "probe_request": selected, "probe_usage": index[selected], "request_count": 15,
        "original_source_trajectory_collected": False, "performance_accepted": False,
        "after_run_root": str(args.after_run_root.resolve()) if args.after_run_root else None,
    }
    controls.reservation.write_json(run / "registration.json", registration)
    with (run / "supervisor.log").open("x") as log:
        process = subprocess.Popen([
            sys.executable, str(Path(frozen["checkout"]) / "scripts/qualify_agentx_data_plane.py"),
            "watch", "--registration", str(run / "registration.json"),
        ], cwd=frozen["checkout"], env={**os.environ, "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    identity = controls.reservation.process(process.pid)
    controls.reservation.write_json(run / "supervisor.json", identity)
    print(json.dumps({"status": "supervisor_started", "run": str(run), "supervisor": identity}, indent=2))


def watch(path):
    registration = json.loads(path.read_bytes())
    run = Path(registration["run_root"])
    (run / "server").mkdir()
    state = {"status": "waiting_resources", "performance_accepted": False}
    identities, server, stopped = [], None, False

    def stop(*_):
        nonlocal stopped
        stopped = True

    def pulse():
        nonlocal identities
        if stopped:
            raise ValueError("bounded data-plane supervisor received a stop request")
        controls.fresh.check_hold(registration, released=True)
        identities = controls.owned_processes(identities)
        state.update(checked_at=controls.reservation.now().isoformat(), owned=identities)
        controls.reservation.write_json(run / "state.json", state)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (run / "supervisor.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for target, expected in (
                (Path(registration["prepared"]) / "preparation.json", registration["preparation_sha256"]),
                (Path(registration["tokens"]) / "manifest.json", registration["token_manifest_sha256"]),
            ):
                if sha256_file(target) != expected:
                    raise ValueError("bounded data-plane input changed after registration")
            while not predecessor_finished(registration.get("after_run_root")) or not (
                controls.qualified.resources(registration["port"])["ready"]
            ):
                pulse()
                time.sleep(5.)
            pulse()
            state["status"] = "qualifying_native_prefix_transfers"
            qualifier_owned = []

            def qualifier_pulse():
                identities.extend(
                    row for row in qualifier_owned
                    if not any(old["pid"] == row["pid"] for old in identities)
                )
                pulse()

            state["native_prefix_qualification"] = qualify_native_prefix(
                registration, run, pulse=qualifier_pulse, owned=qualifier_owned,
            )
            with (run / "plugin_install.log").open("x") as log:
                subprocess.run([
                    sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                    "--no-build-isolation", "--target", str(run / "plugin"),
                    str(ROOT / "configs/causal_replay_plugin"),
                ], check=True, stdout=log, stderr=subprocess.STDOUT, timeout=60)
            settings, flags, _ = controls.environment(
                {"arms": {"full": registration["reference_environment"]}}, "native_matched",
                ROOT, Path(registration["engine_checkout"]), run, registration["port"],
                run / "plugin", sampling_mode="position", max_running_requests=24,
                tool_restore_mode="execution",
            )
            controls.reservation.write_json(run / "launch_environment.json", settings)
            pulse()
            if not controls.qualified.resources(registration["port"])["ready"]:
                raise ValueError("resources changed before bounded model startup")
            with (run / "server.log").open("x") as log:
                server = subprocess.Popen(
                    ["bash", str(ROOT / "scripts/launch_qwen35_native_v0520.sh"), *flags],
                    cwd=ROOT, env=controls.qualified.driver_environment({"environment": settings}, os.environ),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            identities = [controls.reservation.process(server.pid)]
            state["status"] = "starting_server"
            deadline = time.monotonic() + 480.
            while True:
                pulse()
                if server.poll() is not None:
                    raise ValueError("bounded native server exited during startup")
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
                    raise TimeoutError("bounded native model startup expired")
                time.sleep(1.)
            info = httpx.get(
                f"http://127.0.0.1:{registration['port']}/get_server_info", timeout=10.,
            ).json()
            expected = {
                **registration["source_server_parameters"],
                "model_path": settings["MODEL_PATH"], "max_running_requests": 24,
                "context_length": 262144, "random_seed": 21,
                "enable_custom_logit_processor": True, "enable_session_radix_cache": True,
                "enable_beliefkv_admission": False,
            }
            if any(info.get(name) != value for name, value in expected.items()):
                raise ValueError("bounded check changed the common native data plane")
            census = json.loads((run / "server/native_capacity_census.json").read_bytes())["capacity"]
            if any(census.get(name) != registration["source_capacity"][name] for name in (
                "device_full_tokens", "host_full_tokens",
            )):
                raise ValueError("bounded model check changed native KV pool capacities")
            controls.reservation.write_json(run / "server/server_info.json", info)
            scheduler = json.loads((run / "server/native_telemetry_ready.json").read_bytes())["scheduler_pid"]
            identities = controls.owned_processes(identities)
            apps = controls.qualified.command([
                "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
            ]).splitlines()
            if scheduler not in {row["pid"] for row in identities} or str(scheduler) not in apps:
                raise ValueError("bounded native scheduler is not an owned GPU process")
            state["status"], state["gpu_process_verified"] = "running_bounded_probes", True

            async def observed_probe():
                task = asyncio.create_task(probe(registration, run))
                try:
                    while not task.done():
                        pulse()
                        if server.poll() is not None:
                            raise ValueError("bounded native server exited during probes")
                        await asyncio.wait((task,), timeout=.5)
                    return await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

            state["probe"] = asyncio.run(asyncio.wait_for(observed_probe(), timeout=600.))
            controls.wait_for_telemetry_drain(run, server=server, pulse=pulse)
            observations = {}
            with (run / "server/runtime_events.sglang.jsonl").open() as stream:
                for line in stream:
                    row = json.loads(line)
                    if row.get("kind") == "llm_submit":
                        attrs = row["attributes"]
                        observations[attrs["request_id"]] = {
                            key: attrs.get(key) for key in (
                                "prompt_tokens", "cached_tokens_device", "cached_tokens_host",
                            )
                        }
            validate_cache_observations(observations, registration["probe_usage"]["prompt_tokens"])
            state.update(
                status="data_plane_qualified", gpu_qualified=True,
                engine_tree=registration["engine_tree"],
                native_patch_sha256=registration["native_patch_sha256"],
                native_cache_observations=observations,
                original_source_trajectory_collected=False,
                whole_llm_scheduler_or_baseline_qualified=False,
            )
        except BaseException as error:
            state.update(status="failed", error_type=type(error).__name__, error=str(error))
        finally:
            try:
                controls.cleanup(run, identities, state["status"])
            except BaseException as error:
                state.update(status="failed", cleanup_error=repr(error))
            state["finished_at"] = controls.reservation.now().isoformat()
            state["source_sha256"] = sha256_file(ROOT / "scripts/qualify_agentx_data_plane.py")
            controls.reservation.write_json(run / "summary.json", state)
            controls.reservation.write_json(run / "state.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("start")
    for name in ("run-root", "reference", "engine", "native-patch", "prepared", "tokens"):
        launch.add_argument("--" + name, required=True, type=Path)
    launch.add_argument("--port", required=True, type=int)
    launch.add_argument("--after-run-root", type=Path)
    monitor = commands.add_parser("watch")
    monitor.add_argument("--registration", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "start":
        start(args)
    else:
        watch(args.registration)


if __name__ == "__main__":
    main()
