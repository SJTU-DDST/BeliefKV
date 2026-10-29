#!/usr/bin/env python3
"""Run bounded unforced Agentix LLM checks after verified predecessor cleanup."""

import argparse
import asyncio
from datetime import datetime, timezone
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

from beliefkv.experiments.agentix_model_probe import (
    FIXTURES, compare_unforced_outputs, fixture_policy, unforced_probe,
)
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.event_channel import JsonlRuntimeEventSink, UnixDatagramRuntimeEventSink
from beliefkv.runtime.sglang_v0520_sessions import NativeRadixSessionLeases, close_native_radix_session
from scripts import qualify_full_native_model as numerical
from scripts import run_causal_comparison as controls
from scripts.qualify_agentix_runtime import validate_runtime
from scripts.run_agentx_baseline import prefix_qualification_environment
from scripts.run_causal_gpu_replay import source_plan
from beliefkv.experiments.paper_baselines import isolated_environment


POLICY = "configs/baselines/agentix_locobench_model_qualification_20261029.json"
TREE = "c489dbb85265efce25622a52abfd698e9199688d"


def completed_predecessor(path, *, allow_failed=False):
    prior = controls.load(path)
    state = controls.load(path.with_suffix(".state.json"))
    if allow_failed and state.get("status") == "stopped_for_investigation":
        if state.get("completed_arms") or not state.get("error"):
            raise ValueError("skipped TokenCake lacks its actual failed terminal state")
        identities = [
            *(state[name] for name in ("watcher", "server", "driver") if state.get(name)),
            *state.get("owned", ()),
        ]
        cleanup = controls.load(
            Path(prior["run_root"]) / "tokencake_paper_observed/owned_process_cleanup.json",
        )
        if (
            cleanup["remaining_processes"] or not cleanup.get("finished_at")
            or any(controls.reservation.alive(row) for row in (*identities, *cleanup["identities"]))
        ):
            return False
        return True
    if not controls.predecessor_ready({**prior, "after_registration": str(path)}):
        return False
    controls.predecessor_work({**prior, "after_registration": str(path)})
    arm = Path(prior["run_root"]) / "tokencake_paper_observed"
    cleanup, drain = (
        controls.load(arm / "owned_process_cleanup.json"), controls.load(arm / "telemetry_drain.json"),
    )
    idle = drain["drained_status"]
    if (
        cleanup["remaining_processes"] or not cleanup.get("finished_at")
        or any(controls.reservation.alive(identity) for identity in cleanup["identities"])
        or not drain["previous_zero_snapshot_ts_ms"] < idle["snapshot_ts_ms"]
        or any(idle.get(name) != 0 for name in (
            "pending_request_count", "pending_batch_count", "writer_queue_depth",
            "failed_records", "dropped_records",
        ))
        or idle.get("writer_error") is not None
    ):
        raise ValueError("TokenCake complete work has not actually drained and exited")
    return True


def verify_component_application(plan):
    """Reuse only the exact already-qualified physical server, including its CPU manifest."""
    application, engine = Path(plan["application"]), Path(plan["engine"])
    component = controls.load(Path(plan["components"]))
    if (
        component.get("status") != "four_native_consumer_gpu_checks_qualified"
        or component["registered_frozen_application"] != str(application)
        or component["engine_checkout"] != str(engine) or component["engine_tree"] != TREE
    ):
        raise ValueError("Agentix model server differs from its actual GPU byte components")
    cpu = controls.load(Path(plan["cpu_evidence"]))
    if (
        sha256_file(Path(cpu["source_manifest"]["path"])) != cpu["source_manifest"]["sha256"]
        or sha256_file(Path(cpu["report"]["path"])) != cpu["report"]["sha256"]
        or cpu.get("all_prior_752_identities_retained") is not True
        or cpu.get("totals") != {"tests": 762, "errors": 0, "failures": 0, "skipped": 0}
    ):
        raise ValueError("physical Agentix server lacks its unchanged CPU source binding")
    controls.comparison.verify_cpu_snapshot(
        application, controls.load(Path(cpu["source_manifest"]["path"])),
    )
    native = git_source_state(
        engine, canonical_patch=application / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if not native["matches_worktree"] or native["actual_tree"] != TREE:
        raise ValueError("Agentix model server native sources changed")
    for scene in component["reports"].values():
        if sha256_file(Path(scene["path"])) != scene["sha256"] or not scene["actual_exit_verified"]:
            raise ValueError("actual Agentix GPU byte evidence changed")
    cleanup = controls.load(Path(plan["components"]).parent / "owned_process_cleanup.json")
    if cleanup["remaining_processes"] or any(
        controls.reservation.alive(identity) for identity in cleanup["identities"]
    ):
        raise ValueError("prior Agentix GPU byte children have not actually exited")
    return native


def model_environment(plan, fixture, policy_path, run):
    prior = controls.load(Path(plan["predecessor"]))
    common = {
        **prior["reference_environment"], "MAX_RUNNING_REQUESTS": "1",
        "TOOL_RESTORE_MODE": "execution", "CHUNKED_PREFILL_SIZE": "4096",
        "ENABLE_MIXED_CHUNK": "0",
    }
    settings, flags, socket = controls.environment(
        {"arms": {"full": common}}, "scheduler_only",
        Path(plan["application"]), Path(plan["engine"]), run, plan["port"], run / "unused_plugin",
        sampling_mode="position", max_running_requests=1, tool_restore_mode="execution",
    )
    settings = isolated_environment(settings)
    settings.update(
        BELIEFKV_PAPER_BASELINE="agentix_atlas_native_qualification",
        BELIEFKV_AGENTIX_QUALIFICATION_ONLY="1",
        BELIEFKV_AGENTIX_QUALIFICATION_CONFIG=str(policy_path),
        SGLANG_PLUGINS="", BELIEFKV_CAUSAL_REPLAY_BRIDGE="0",
        CONTEXT_LENGTH=str(fixture.context_limit), HICACHE_SIZE_GB=str(fixture.host_gb),
        BELIEFKV_REPLAY_TOKEN_CAPTURE="1",
        SGLANG_PATCH_FLAVOR="staging",
        SGLANG_SOURCE_BASE_COMMIT=git_source_state(Path(plan["engine"]))["commit"],
    )
    flags.remove("--enable-custom-logit-processor")
    if "--max-total-tokens" in flags:
        flags[flags.index("--max-total-tokens") + 1] = str(fixture.device_tokens)
    else:
        flags += ["--max-total-tokens", str(fixture.device_tokens)]
    flags += ["--enable-deterministic-inference", "--attention-backend", "fa3"]
    inherited = prefix_qualification_environment(
        {"reference_environment": settings}, os.environ,
    )
    return {**inherited, **settings}, flags, socket


async def model_probe(plan, fixture, run, socket, prompts, pulse):
    rows = {}
    leases = NativeRadixSessionLeases(
        lambda session: close_native_radix_session(
            f"http://127.0.0.1:{plan['port']}", session,
        ),
    )
    document = {"generated_tokens": fixture.generated_tokens, "sampling_seed": 21}
    with JsonlRuntimeEventSink(run / "events.jsonl") as log, UnixDatagramRuntimeEventSink(socket) as sink:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{plan['port']}", timeout=600.,
            limits=httpx.Limits(max_connections=8),
        ) as client:
            async def emit(event):
                pulse()
                log.emit_batch((event,))
                await asyncio.to_thread(sink.emit_batch, (event,))

            async def generate(body):
                pulse()
                response = await client.post("/generate", json=body)
                response.raise_for_status()
                return response.json()

            def record(label, result, request, prompt_tokens):
                controls.reservation.write_json(run / f"response_{label}.json", result)
                row = numerical.response_record(
                    result, request=request, prompt_tokens=prompt_tokens, document=document,
                )
                rows[label] = row
                return row

            result, sessions = await unforced_probe(
                prompts, fixture, generate=generate, emit=emit, session_for=leases.for_request,
                observe_body=lambda label, body: controls.reservation.write_json(
                    run / f"request_{label}.json", body,
                ),
                record_response=record,
            )
            for session in sorted(sessions):
                response = await client.post("/close_session", json={"session_id": session})
                response.raise_for_status()
            return {"requests": result, **compare_unforced_outputs(rows),
                    "model_outputs_forced": False, "artificial_copy_delay": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("predecessor", "application", "engine", "components", "cpu-evidence", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--fixture", choices=tuple(FIXTURES), default="roomy")
    parser.add_argument("--port", type=int, default=18708)
    parser.add_argument("--allow-failed-predecessor", action="store_true")
    args = parser.parse_args()
    run = args.output_dir.resolve()
    if run.exists():
        raise FileExistsError("preserve every prior Agentix model attempt")
    run.mkdir(parents=True)
    plan = {name: str(getattr(args, name).resolve()) for name in (
        "predecessor", "application", "engine", "components", "cpu_evidence",
    )}
    plan.update(
        port=args.port, fixture=args.fixture, operator_commit=git_source_state(ROOT)["commit"],
        failed_tokencake_skipped_by_user=args.allow_failed_predecessor,
    )
    policy = fixture_policy(controls.load(ROOT / POLICY), FIXTURES[args.fixture])
    controls.reservation.write_json(run / "registration.json", plan)
    controls.reservation.write_json(run / "qualification_policy.json", policy)
    fixture, owned, stopped = FIXTURES[args.fixture], [], False
    state = {
        "status": "waiting_predecessor_exit", "performance_accepted": False,
        "supervisor": controls.reservation.process(os.getpid()),
    }

    def stop(*_):
        nonlocal stopped
        stopped = True

    def pulse():
        if stopped:
            raise ValueError("Agentix model qualifier received a stop request")
        prior = controls.load(args.predecessor)
        controls.fresh.check_hold(prior, released=True)
        owned[:] = controls.owned_processes(owned)
        state.update(checked_at=datetime.now(timezone.utc).isoformat(), owned=owned)
        controls.reservation.write_json(run / "state.json", state)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (run / "supervisor.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        server = None
        try:
            verify_component_application(plan)
            while not completed_predecessor(
                args.predecessor, allow_failed=args.allow_failed_predecessor,
            ):
                pulse()
                time.sleep(5.)
            state["status"] = "waiting_idle_gpu"
            while True:
                pulse()
                if controls.qualified.resources(args.port)["ready"]:
                    time.sleep(10.)
                    pulse()
                    if controls.qualified.resources(args.port)["ready"]:
                        break
                time.sleep(5.)
            if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
                raise ValueError("Agentix model GPU supervisor requires nice0")
            verify_component_application(plan)
            prior = controls.load(args.predecessor)
            _, expected, _ = source_plan(Path(prior["source_arm"]))
            selected, workflows, prompts = [], set(), []
            with CapturedTokenCorpus(Path(prior["source_arm"]) / "server/request_tokens.jsonl", expected) as corpus:
                for request, values in sorted(expected.items()):
                    identity, tokens = values["llm_submit"]
                    if tokens < fixture.prompt_tokens or identity[0] in workflows:
                        continue
                    selected.append(request)
                    workflows.add(identity[0])
                    prompts.append(list(corpus.get(request).prompt_tokens[:fixture.prompt_tokens]))
                    if len(prompts) == 3:
                        break
            if len(prompts) != 3:
                raise ValueError("three distinct source workflows lack the declared prompt prefixes")
            settings, flags, socket = model_environment(
                plan, fixture, run / "qualification_policy.json", run,
            )
            if socket.exists():
                raise ValueError("Agentix model qualifier requires a fresh event socket")
            (run / "server").mkdir()
            (run / "opportunities").mkdir()
            controls.reservation.write_json(run / "launch_environment.json", settings)
            state.update(status="starting_model_server", source_probe_requests=selected)
            with (run / "server.log").open("x") as log:
                server = subprocess.Popen(
                    ["bash", str(Path(plan["application"]) / "scripts/launch_qwen35_native_v0520.sh"), *flags],
                    cwd=plan["application"], env=controls.qualified.driver_environment({"environment": settings}, os.environ),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
            identity = controls.reservation.process(server.pid)
            if identity is None:
                raise ValueError("Agentix model server has no owned process identity")
            owned.append(identity)
            deadline = time.monotonic() + 480.
            ready_path = run / "server/native_telemetry_ready.json"
            while True:
                pulse()
                if server.poll() is not None:
                    raise ValueError("Agentix model server exited during startup")
                try:
                    info = httpx.get(f"http://127.0.0.1:{args.port}/server_info", timeout=2.)
                    if info.is_success and ready_path.is_file():
                        info = info.json()
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("Agentix model server startup expired")
                time.sleep(1.)
            parameters = {
                "model_path": settings["MODEL_PATH"], "max_running_requests": 1,
                "context_length": fixture.context_limit, "max_total_tokens": fixture.device_tokens,
                "enable_custom_logit_processor": False, "enable_session_radix_cache": True,
                "enable_deterministic_inference": True, "sampling_backend": "pytorch",
                "attention_backend": "fa3", "enable_mixed_chunk": False,
            }
            if any(info.get(name) != value for name, value in parameters.items()):
                raise ValueError("actual Agentix model parameters differ from the declared fixture")
            controls.reservation.write_json(run / "server/server_info.json", info)
            pulse()
            scheduler = controls.load(ready_path)["scheduler_pid"]
            applications = controls.qualified.command([
                "nvidia-smi", "--id=0", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
            ]).splitlines()
            if str(scheduler) not in applications or not any(row["pid"] == scheduler for row in owned):
                raise ValueError("Agentix model scheduler is not an actual owned GPU process")
            state["status"] = "running_unforced_model_probes"

            async def observed_probe():
                task = asyncio.create_task(model_probe(plan, fixture, run, socket, prompts, pulse))
                try:
                    while not task.done():
                        pulse()
                        if server.poll() is not None:
                            raise ValueError("Agentix model server exited during probes")
                        await asyncio.wait((task,), timeout=.5)
                    return await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

            state["probe"] = asyncio.run(asyncio.wait_for(observed_probe(), timeout=900.))
            controls.wait_for_telemetry_drain(run, server=server, pulse=pulse)
            deadline = time.monotonic() + 10.
            while True:
                pulse()
                runtime = controls.qualified.last_runtime_state(
                    run / "opportunities/admission_opportunities.jsonl",
                )
                try:
                    validate_runtime(runtime, policy)
                    if fixture.require_backing_drain and (
                        runtime["paper_baseline"]["backing"]["counts"].get("drain_rounds", 0) <= 0
                    ):
                        raise ValueError("finite model backing pressure did not activate")
                    break
                except (ValueError, TypeError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.5)
            verify_component_application(plan)
            state.update(
                status="agentix_unforced_model_qualified", runtime=runtime,
                deterministic_numerical_fixture_only=True, formal_baseline_qualified=False,
                full_population_qualified=False,
            )
        except BaseException as error:
            state.update(status="failed", error=repr(error))
        finally:
            try:
                controls.cleanup(run, owned, state["status"])
            except BaseException as error:
                state.update(status="failed", cleanup_error=repr(error))
            state.update(finished_at=datetime.now(timezone.utc).isoformat())
            controls.reservation.write_json(run / "summary.json", state)
            controls.reservation.write_json(run / "state.json", state)
    if state["status"] != "agentix_unforced_model_qualified":
        raise ValueError("Agentix model qualification failed; preserve this attempt")


if __name__ == "__main__":
    main()
