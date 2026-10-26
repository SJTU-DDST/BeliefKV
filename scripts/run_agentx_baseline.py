#!/usr/bin/env python3
"""Freeze and supervise a published-AgentX baseline without a native capture."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.agentx_replay import load_agentx_preparation
from beliefkv.experiments.agentx_tokens import AgentXTokenCorpus
from beliefkv.experiments.causal_replay_delivery import (
    JOIN_INFORMATION_CONTRACT, control_event_counts,
)
from beliefkv.experiments.paper_baselines import (
    RUNTIME_ARMS, TOKENCAKE_ARMS, TOKENCAKE_PARAMETERS, tokencake_parameters,
)
from beliefkv.experiments.source_provenance import _tree_with_patch, git_source_state, sha256_file
from scripts import run_causal_comparison as controls
from scripts.qualify_paper_prefix_transfers import validate_report as validate_prefix_report


PREFIX_TRANSFER_ARMS = {"kvflow_observed_graph", *TOKENCAKE_ARMS}


def predecessor_finished(run):
    if run is None:
        return True
    run = Path(run)
    identity = json.loads((run / "supervisor.json").read_bytes())
    if controls.reservation.alive(identity):
        return False
    state = json.loads((run / "state.json").read_bytes())
    if (
        state.get("status") not in (
            "complete_review_pending", "stopped_for_investigation",
            "data_plane_qualified", "tokencake_native_lifecycle_qualified", "failed",
            "agentix_native_runtime_qualified",
            "agentix_native_pressure_components_qualified",
            "full_native_prefix_components_qualified",
            "full_native_model_qualified",
        )
        or not state.get("finished_at") or state.get("cleanup_error")
    ):
        return False
    registration = json.loads((run / "registration.json").read_bytes())
    cleanup_path = run / registration.get("arm", "") / "owned_process_cleanup.json"
    if not cleanup_path.is_file():
        return False
    cleanup = json.loads(cleanup_path.read_bytes())
    return (
        cleanup.get("remaining_processes") == []
        and bool(cleanup.get("finished_at"))
        and not any(
            controls.reservation.alive(row) for row in cleanup.get("identities", ())
        )
    )


def configured_reference(reference, label, path):
    if label not in TOKENCAKE_ARMS:
        if path is not None:
            raise ValueError("TokenCake configuration supplied for another baseline")
        return reference, None
    if path is None:
        raise ValueError("TokenCake full run requires an explicit policy configuration")
    path = Path(path).resolve()
    document = json.loads(path.read_bytes())
    values = document.get("parameters") or {}
    if (
        document.get("schema_version") != 1 or document.get("policy") != RUNTIME_ARMS[label]
        or document.get("information_contract") != "observed_events_or_explicit_application_metadata"
        or set(values) != set(TOKENCAKE_PARAMETERS)
        or any(type(value) is not int for value in values.values())
    ):
        raise ValueError("TokenCake policy configuration or parameter types differ")
    common = {
        **reference["reference_environment"],
        **{variable: str(values[name]) for name, variable in TOKENCAKE_PARAMETERS.items()},
    }
    tokencake_parameters(common)
    native = reference["source_server_parameters"]
    expected = {
        "generation_budget": int(common["MAX_COMPLETION_TOKENS"]),
        "context_limit": native["context_length"],
        "running_limit": native["max_running_requests"],
        # Older common registrations omit this native default; startup checks
        # the effective argument before submitting any measured requests.
        "prefill_token_budget": native.get(
            "max_prefill_tokens", int(common.get("MAX_PREFILL_TOKENS", "16384")),
        ),
        "chunked_prefill_tokens": native["chunked_prefill_size"],
        "policy_block_tokens": native["page_size"],
    }
    if any(values[name] != value for name, value in expected.items()) or (
        values["eviction_window_tokens"] % values["policy_block_tokens"]
    ):
        raise ValueError("TokenCake configuration changes the common serving contract")
    return {**reference, "reference_environment": common}, {
        "path": str(path), "sha256": sha256_file(path), "document": document,
    }


def prefix_qualification_environment(registration, inherited):
    reference = registration["reference_environment"]
    cuda = Path(reference.get("CUDA_HOME") or (
        Path(sys.executable).resolve().parents[1]
        / "lib/python3.11/site-packages/nvidia/cu13"
    )).resolve()
    if not os.access(cuda / "bin/nvcc", os.X_OK) or not (
        cuda / "lib/libcudart.so.13"
    ).is_file():
        raise ValueError("native prefix qualifier requires the registered CUDA 13 toolchain")
    settings = {
        "CUDA_HOME": str(cuda),
        "CUDA_VISIBLE_DEVICES": reference.get("CUDA_VISIBLE_DEVICES", "0"),
        "PYTHONHASHSEED": "21", "PYTHONPATH": str(ROOT),
        "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
    }
    env = controls.qualified.driver_environment({"environment": settings}, inherited)
    for name in ("LIBRARY_PATH", "LD_LIBRARY_PATH"):
        env[name] = str(cuda / "lib") + (os.pathsep + env[name] if env.get(name) else "")
    env["PATH"] = str(cuda / "bin") + (os.pathsep + env["PATH"] if env.get("PATH") else "")
    return env


def qualify_native_prefix(registration, run, *, pulse, owned):
    """Run new physical primitives only after the resource wait, before the LLM."""
    output = run / "native_prefix_qualification"
    env = prefix_qualification_environment(registration, os.environ)
    controls.reservation.write_json(run / "native_prefix_qualification_environment.json", {
        name: env[name] for name in (
            "CUDA_HOME", "CUDA_VISIBLE_DEVICES", "PYTHONHASHSEED", "PYTHONPATH",
            "LIBRARY_PATH", "LD_LIBRARY_PATH",
            "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
        )
    })
    with (run / "native_prefix_qualification.log").open("x") as log:
        process = subprocess.Popen([
            sys.executable, str(ROOT / "scripts/qualify_paper_prefix_transfers.py"),
            "--engine-checkout", registration["engine_checkout"],
            "--canonical-patch", str(ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
            "--output-dir", str(output),
        ], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True)
        identity = controls.reservation.process(process.pid)
        if identity is None:
            raise ValueError("native prefix qualifier has no owned process identity")
        owned.append(identity)
        deadline = time.monotonic() + 180.
        while process.poll() is None:
            pulse()
            if time.monotonic() >= deadline:
                raise TimeoutError("native prefix qualification deadline expired")
            time.sleep(.5)
        if process.wait():
            raise ValueError("native prefix GPU qualification failed; preserve its report")
    report_path = output / "summary.json"
    report = json.loads(report_path.read_bytes())
    validate_prefix_report(
        report, engine_tree=registration["engine_tree"],
        patch_sha256=registration["native_patch_sha256"],
    )
    return {"report": str(report_path), "sha256": sha256_file(report_path), **report}


def qualify_native_tokencake(registration, run, *, pulse, owned):
    from scripts.qualify_tokencake_runtime import validate_report

    output = run / "native_tokencake_qualification"
    env = prefix_qualification_environment(registration, os.environ)
    with (run / "native_tokencake_qualification.log").open("x") as log:
        process = subprocess.Popen([
            sys.executable, str(ROOT / "scripts/qualify_tokencake_runtime.py"), "run",
            "--engine", registration["engine_checkout"],
            "--native-patch", str(ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
            "--output-dir", str(output),
        ], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True)
        identity = controls.reservation.process(process.pid)
        if identity is None:
            raise ValueError("TokenCake lifecycle qualifier has no owned identity")
        owned.append(identity)
        deadline = time.monotonic() + 180.
        while process.poll() is None:
            pulse()
            if time.monotonic() >= deadline:
                raise TimeoutError("TokenCake lifecycle qualification deadline expired")
            time.sleep(.5)
        if process.wait():
            raise ValueError("TokenCake lifecycle GPU qualification failed; preserve its report")
    report_path = output / "summary.json"
    report = json.loads(report_path.read_bytes())
    validate_report(
        report, engine_tree=registration["engine_tree"],
        patch_sha256=registration["native_patch_sha256"],
    )
    return {"report": str(report_path), "sha256": sha256_file(report_path), **report}


def freeze(engine, native_patch):
    native = git_source_state(engine, canonical_patch=native_patch)["canonical_patch"]
    extra = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, text=True,
    ).splitlines()
    tree = _tree_with_patch(ROOT, None, extra_paths=extra)
    with tempfile.TemporaryDirectory(prefix="agentx-baseline-index-") as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        subprocess.run(["git", "read-tree", tree], cwd=ROOT, env=env, check=True)
        blob = subprocess.check_output(
            ["git", "hash-object", "-w", "--stdin"], cwd=ROOT, input=native_patch.read_bytes(),
        ).decode().strip()
        subprocess.run([
            "git", "update-index", "--add", "--cacheinfo",
            "100644," + blob + ",patches/sglang-v0.5.20-beliefkv-staging.patch",
        ], cwd=ROOT, env=env, check=True)
        tree = subprocess.check_output(
            ["git", "write-tree"], cwd=ROOT, env=env, text=True,
        ).strip()
    parent = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    commit = subprocess.check_output(
        ["git", "commit-tree", tree, "-p", parent], cwd=ROOT, text=True,
        input="Freeze direct published-AgentX baseline; preserve development indexes.\n",
    ).strip()
    checkout = ROOT.parent / ".worktrees" / ("beliefkv-agentx-baseline-" + commit[:12])
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(checkout), commit],
        cwd=ROOT, check=True, capture_output=True,
    )
    frozen_engine = ROOT.parent / ".worktrees" / (
        "sglang-agentx-baseline-" + native["actual_tree"][:12] + "-" + commit[:12]
    )
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(frozen_engine), "HEAD"],
        cwd=engine, check=True, capture_output=True,
    )
    patch = checkout / "patches/sglang-v0.5.20-beliefkv-staging.patch"
    subprocess.run(["git", "apply", str(patch)], cwd=frozen_engine, check=True, capture_output=True)
    observed = git_source_state(frozen_engine, canonical_patch=patch)["canonical_patch"]
    if observed["actual_tree"] != native["actual_tree"]:
        raise ValueError("frozen engine differs from its canonical patch")
    return {
        "checkout": str(checkout), "engine_checkout": str(frozen_engine),
        "application_commit": commit, "application_tree": tree,
        "engine_tree": native["actual_tree"], "native_patch_sha256": sha256_file(native_patch),
        "development_branch_and_user_indexes_unchanged": True,
    }


def validate_published_result(registration, result, arm):
    source = registration["source_plan"]
    live = result.get("control_delivery", {})
    delivered = live.get("workflows", {})
    projection = registration.get("control_event_projection")
    expected_batches = source["nodes"] - source["requests"]
    if projection is not None:
        expected = projection.get("online_event_batches_by_workflow", {})
        private = projection.get("private_join_events_by_workflow", {})
        if (
            projection.get("join_information_contract") != JOIN_INFORMATION_CONTRACT
            or live.get("join_information_contract") != JOIN_INFORMATION_CONTRACT
            or live.get("private_join_events_by_workflow") != private
            or set(expected) != set(registration["workflow_ids"])
            or not set(private) <= set(expected)
            or any(type(count) is not int or count < 0 for count in (*expected.values(), *private.values()))
            or sum(expected.values()) + sum(private.values()) != expected_batches
            or {wid: row.get("accepted_batch_count", 0) for wid, row in delivered.items()} != expected
        ):
            raise ValueError("runtime-observable event projection differs from registration")
        expected_batches = sum(expected.values())
    if (
        result.get("causal_execution_completed") is not True
        or result.get("exact_tokens_verified") is not True
        or result.get("published_length_stopping_verified") is not True
        or result.get("original_native_stopping_verified") is not False
        or result.get("live_control_events_delivered") is not True
        or result.get("replay_process_python_hash_seed") != "21"
        or result.get("completed_requests") != source["requests"]
        or result.get("source_plan") != source
        or result.get("source_plan_sha256") != registration["plan_sha256"]
        or result.get("source_full_sha256") != registration["source_full_sha256"]
        or live.get("mode") != "per_workflow_harness_queued"
        or set(delivered) != set(registration["workflow_ids"])
        or sum(row.get("accepted_batch_count", 0) for row in delivered.values())
        != expected_batches
        or sha256_file(arm / "replay/native_requests.jsonl") != result.get("native_requests_sha256")
        or result.get("token_source", {}).get("artifacts") != registration["token_artifacts"]
    ):
        raise ValueError("complete published-source execution evidence differs")
    metrics = result.get("streaming_response_metrics") or {}
    if (
        metrics.get("all_workflows_completed") is not True
        or metrics.get("coverage", {}).get("completed_requests") != source["requests"]
        or set(result.get("workflow_elapsed_ms", {})) != set(registration["workflow_ids"])
    ):
        raise ValueError("streaming workflow population is incomplete")
    work = controls.receipt_work(arm / "replay/native_requests.jsonl", sampling_mode="position")
    index = json.loads((Path(registration["tokens"]) / "index.json").read_bytes())
    if work.keys() != index.keys() or any(
        actual[:4] != (
            index[rid]["prompt_tokens"], index[rid]["output_tokens"],
            index[rid]["prompt_sha256"], index[rid]["output_sha256"],
        )
        or actual[4] != {"type": "length", "length": index[rid]["output_tokens"]}
        for rid, actual in work.items()
    ):
        raise ValueError("baseline changed native request tokens or lengths")
    return controls.replay_event_metrics(result, arm, registration["workflow_ids"])


def review_completed_run(path):
    run = path.resolve()
    registration = json.loads((run / "registration.json").read_bytes())
    state = json.loads((run / "state.json").read_bytes())
    stored = json.loads((run / "result.json").read_bytes())
    label, source = registration["arm"], registration["source_plan"]
    arm = run / label
    if (
        state.get("status") != "complete_review_pending"
        or registration.get("all_normal_tails_retained") is not True
        or registration.get("quality_exclusions") != []
        or stored.get("all_normal_tails_retained") is not True
    ):
        raise ValueError("review requires a complete baseline with every normal tail")
    result = json.loads((arm / "replay/summary.json").read_bytes())
    metrics = validate_published_result(registration, result, arm)
    if any(stored.get(name) != value for name, value in metrics.items()) or any(
        stored.get(name) != source[name] for name in ("prompt_tokens", "output_tokens")
    ) or stored.get("requests") != source["requests"]:
        raise ValueError("stored baseline metrics differ from the native receipts and events")
    expected_tps = source["output_tokens"] * 1000. / metrics["workflow_event_makespan_ms"]
    if stored.get("whole_run_output_tokens_per_second") != expected_tps:
        raise ValueError("stored throughput does not include the complete workflow makespan")
    controls.observed_configuration(registration, label, arm, final=True)
    native = git_source_state(
        Path(registration["engine_checkout"]),
        canonical_patch=Path(registration["checkout"]) / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if (
        native["actual_tree"] != registration["engine_tree"]
        or native["sha256"] != registration["native_patch_sha256"]
    ):
        raise ValueError("reviewed native sources differ from the frozen registration")
    if label in TOKENCAKE_ARMS:
        from scripts.qualify_tokencake_runtime import validate_report

        report = json.loads((run / "native_tokencake_qualification/summary.json").read_bytes())
        validate_report(
            report, engine_tree=registration["engine_tree"],
            patch_sha256=registration["native_patch_sha256"],
            root=Path(registration["checkout"]),
        )
    drain = json.loads((arm / "telemetry_drain.json").read_bytes())
    idle = drain["drained_status"]
    if (
        not drain["previous_zero_snapshot_ts_ms"] < idle["snapshot_ts_ms"]
        or any(idle.get(name) != 0 for name in (
            "pending_batch_count", "pending_request_count", "writer_queue_depth",
            "dropped_records", "failed_records",
        ))
        or idle.get("writer_error") is not None
        or idle.get("request_cache_evidence", {}).get("all", {}).get("request_count")
        != source["requests"]
    ):
        raise ValueError("final telemetry does not prove two idle complete-population snapshots")
    cleanup = json.loads((arm / "owned_process_cleanup.json").read_bytes())
    if cleanup.get("remaining_processes") != [] or any(
        controls.reservation.alive(identity) for identity in cleanup.get("identities", ())
    ):
        raise ValueError("completed baseline still has a live owned server process")
    runtime = controls.qualified.last_runtime_state(
        arm / "opportunities/admission_opportunities.jsonl",
    )
    if not runtime or runtime.get("physical_disabled") is not False:
        raise ValueError("reviewed baseline has no healthy final runtime state")
    declared = runtime["paper_baseline"]
    return {
        "status": "complete_single_run_reviewed", "baseline": label,
        "workload": "agentx_published_traces",
        "requests": source["requests"], "workflows": len(registration["workflow_ids"]),
        **metrics, "whole_run_output_tokens_per_second": expected_tps,
        "paper_policy": declared, "native_engine_tree": native["actual_tree"],
        "join_information_contract": registration.get("control_event_projection", {}).get(
            "join_information_contract", "legacy_replay_events_including_inferred_joins",
        ),
        "same_information_comparison_requires_matching_contract": True,
        "all_normal_tails_retained": True, "original_application_correctness_verified": False,
        "source_token_and_length_contract_verified": True,
        "telemetry_drain_sha256": sha256_file(arm / "telemetry_drain.json"),
        "native_requests_sha256": result["native_requests_sha256"],
        "replay_summary_sha256": sha256_file(arm / "replay/summary.json"),
        "public_timeout_force_resumed": declared.get("counts", {}).get(
            "public_timeout_force_resumed", 0,
        ),
        "public_timeout_wait_included_in_metrics": True,
        "performance_accepted": False, "paired_comparison_and_repetitions_pending": True,
    }


def start(args):
    if args.arm not in RUNTIME_ARMS:
        raise ValueError("baseline lacks an enabled physical runtime adapter")
    run = args.run_root.resolve()
    if run.exists():
        raise FileExistsError("preserve prior baseline artifacts")
    plan, _, workflows, prepared = load_agentx_preparation(args.prepared)
    with AgentXTokenCorpus(args.tokens, args.prepared) as corpus:
        tokens = corpus.evidence
    reference = json.loads(args.reference.read_bytes())
    controls.fresh.check_hold(reference, released=True)
    reference, policy_configuration = configured_reference(
        reference, args.arm, getattr(args, "policy_config", None),
    )
    after_root = getattr(args, "after_run_root", None)
    after_root = Path(after_root).resolve() if after_root is not None else None
    predecessor_sha256 = None
    if after_root is not None:
        previous = json.loads((after_root / "registration.json").read_bytes())
        if any(
            previous.get(name) != value for name, value in {
                "source_plan": plan.summary(), "token_artifacts": tokens["artifacts"],
                "source_capacity": reference["source_capacity"],
                "workflow_ids": [row["workflow_id"] for row in workflows],
            }.items()
        ):
            raise ValueError("queued baseline predecessor uses different common work or capacity")
        predecessor_sha256 = sha256_file(after_root / "registration.json")
    bounded = json.loads(args.gpu_data_plane_report.read_bytes())
    actual = git_source_state(args.engine, canonical_patch=args.native_patch)["canonical_patch"]
    if (
        bounded.get("status") != "data_plane_qualified"
        or bounded.get("engine_tree") != actual["actual_tree"]
        or bounded.get("native_patch_sha256") != sha256_file(args.native_patch)
    ):
        raise ValueError("shared native engine differs from bounded AgentX qualification")
    settings, _, _ = controls.environment(
        {"arms": {"full": reference["reference_environment"]}}, args.arm, ROOT, args.engine,
        run / args.arm, args.port, run / "plugin", sampling_mode="position",
        max_running_requests=24, tool_restore_mode="execution",
    )
    preflight = controls.preflight_runtime(settings, settings["MODEL_PATH"])
    run.mkdir(parents=True)
    frozen = freeze(args.engine, args.native_patch)
    registration = {
        **frozen, "arm": args.arm, "run_root": str(run), "port": args.port,
        "prepared": str(args.prepared.resolve()), "tokens": str(args.tokens.resolve()),
        "preparation_sha256": sha256_file(args.prepared / "preparation.json"),
        "token_manifest_sha256": sha256_file(args.tokens / "manifest.json"),
        "token_artifacts": tokens["artifacts"], "plan_sha256": prepared["artifacts"]["plan.json"],
        "source_full_sha256": prepared["source_full_sha256"],
        "source_plan": plan.summary(), "workflow_ids": [row["workflow_id"] for row in workflows],
        "control_event_projection": control_event_counts(
            node.event for node in plan.nodes if node.event is not None
        ),
        "hold_file": reference["hold_file"], "reference_environment": reference["reference_environment"],
        "source_server_parameters": reference["source_server_parameters"],
        "source_capacity": reference["source_capacity"],
        "max_running_requests": 24, "tool_restore_mode": "execution", "sampling_mode": "position",
        "native_source_capture_required": False, "quality_exclusions": [],
        "all_normal_tails_retained": True, "performance_accepted": False,
        "registered_at": controls.reservation.now().isoformat(),
        "runtime_cpu_preflight": preflight,
        "native_prefix_gpu_qualification_required": args.arm in PREFIX_TRANSFER_ARMS,
        "native_tokencake_gpu_qualification_required": args.arm in TOKENCAKE_ARMS,
        "policy_configuration": policy_configuration,
        "after_run_root": str(after_root) if after_root is not None else None,
        "predecessor_registration_sha256": predecessor_sha256,
    }
    controls.reservation.write_json(run / "registration.json", registration)
    command = [
        sys.executable, str(Path(frozen["checkout"]) / "scripts/run_agentx_baseline.py"),
        "watch", "--registration", str(run / "registration.json"),
    ]
    with (run / "supervisor.log").open("x") as log:
        process = subprocess.Popen(
            command, cwd=frozen["checkout"], env={**os.environ, "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    identity = controls.reservation.process(process.pid)
    controls.reservation.write_json(run / "supervisor.json", identity)
    print(json.dumps({"status": "supervisor_started", "run": str(run), "supervisor": identity}, indent=2))


def watch(path):
    registration = json.loads(path.read_bytes())
    run, label = Path(registration["run_root"]), registration["arm"]
    arm = run / label
    arm.mkdir()
    (arm / "server").mkdir()
    (arm / "opportunities").mkdir()
    state = {"status": "preflight", "arm": label, "performance_accepted": False}
    identities, stopped, server, event_socket = [], False, None, None

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def pulse():
        nonlocal identities
        if stopped:
            raise ValueError("AgentX baseline supervisor received a stop request")
        controls.fresh.check_hold(registration, released=True)
        identities = controls.owned_processes(identities)
        state.update(checked_at=controls.reservation.now().isoformat(), owned=identities)
        controls.reservation.write_json(run / "state.json", state)

    with (run / "supervisor.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for target, expected in (
                (Path(registration["prepared"]) / "preparation.json", registration["preparation_sha256"]),
                (Path(registration["tokens"]) / "manifest.json", registration["token_manifest_sha256"]),
            ):
                if sha256_file(target) != expected:
                    raise ValueError("published-source artifacts changed after registration")
            dependency = registration.get("after_run_root")
            if dependency is not None and sha256_file(
                Path(dependency) / "registration.json"
            ) != registration["predecessor_registration_sha256"]:
                raise ValueError("queued baseline predecessor registration changed")
            state["status"] = "waiting_predecessor_or_resources"
            while not predecessor_finished(dependency) or not (
                controls.qualified.resources(registration["port"])["ready"]
            ):
                pulse()
                time.sleep(5.)
            pulse()
            if label in PREFIX_TRANSFER_ARMS:
                if registration.get("native_prefix_gpu_qualification_required") is not True:
                    raise ValueError("new prefix-transfer baseline lacks its GPU qualification")
                state["status"] = "qualifying_native_prefix_transfers"
                # pulse refreshes `identities`; keep the qualifier's identity in
                # the outer collection so every failure path can clean it up.
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
                pulse()
            if label in TOKENCAKE_ARMS:
                if registration.get("native_tokencake_gpu_qualification_required") is not True:
                    raise ValueError("TokenCake full run lacks its native lifecycle qualification")
                state["status"] = "qualifying_native_tokencake_lifecycle"
                tokencake_owned = []

                def tokencake_pulse():
                    identities.extend(
                        row for row in tokencake_owned
                        if not any(old["pid"] == row["pid"] for old in identities)
                    )
                    pulse()

                state["native_tokencake_qualification"] = qualify_native_tokencake(
                    registration, run, pulse=tokencake_pulse, owned=tokencake_owned,
                )
                pulse()
            with (run / "plugin_install.log").open("x") as log:
                subprocess.run([
                    sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                    "--no-build-isolation", "--target", str(run / "plugin"),
                    str(ROOT / "configs/causal_replay_plugin"),
                ], check=True, stdout=log, stderr=subprocess.STDOUT, timeout=60)
            settings, flags, event_socket = controls.environment(
                {"arms": {"full": registration["reference_environment"]}}, label, ROOT,
                Path(registration["engine_checkout"]), arm, registration["port"], run / "plugin",
                sampling_mode="position", max_running_requests=24, tool_restore_mode="execution",
            )
            if event_socket is None or event_socket.exists():
                raise ValueError("paper baseline requires a fresh live event socket")
            controls.reservation.write_json(arm / "launch_environment.json", settings)
            controls.reservation.write_json(arm / "server/launch_configuration.json", {
                "chat_token_block_cache": True, "internal_comparison_mode": "scheduler_only",
                "prefetch_residency_protection_requested": False,
                "prefill_scheduling_policy_requested": settings["PREFILL_SCHEDULING_POLICY"],
                "tool_restore_mode_requested": settings["TOOL_RESTORE_MODE"],
                "idle_prepare_policy_requested": settings["IDLE_PREPARE_POLICY"],
                "restore_tiers_requested": "complete",
                "join_last_child_priority_requested": False,
                "execution_frontier_restore_requested": False,
                "frontier_cold_reclaim_requested": False, "tool_continuity_requested": False,
            })
            pulse()
            if not controls.qualified.resources(registration["port"])["ready"]:
                raise ValueError("GPU resources changed before native launch")
            with (arm / "server.log").open("x") as log:
                server = subprocess.Popen(
                    ["bash", str(ROOT / "scripts/launch_qwen35_native_v0520.sh"), *flags],
                    cwd=ROOT, env=controls.qualified.driver_environment({"environment": settings}, os.environ),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
            identities = [controls.reservation.process(server.pid)]
            state["status"] = "starting_server"
            deadline = time.monotonic() + 480.
            while True:
                pulse()
                if server.poll() is not None:
                    raise ValueError("baseline native server exited during startup")
                try:
                    ready = (
                        httpx.get(f"http://127.0.0.1:{registration['port']}/health", timeout=2.).is_success
                        and (arm / "server/native_telemetry_ready.json").is_file()
                        and (arm / "server/native_capacity_census.json").is_file()
                    )
                except httpx.HTTPError:
                    ready = False
                if ready:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("baseline native startup deadline expired")
                time.sleep(1.)
            info = httpx.get(
                f"http://127.0.0.1:{registration['port']}/get_server_info", timeout=10.,
            ).json()
            required = {
                **registration["source_server_parameters"],
                "model_path": settings["MODEL_PATH"], "max_running_requests": 24,
                "context_length": 262144, "random_seed": 21, "enable_beliefkv_admission": True,
                "enable_custom_logit_processor": True, "enable_session_radix_cache": True,
            }
            if label in TOKENCAKE_ARMS:
                parameters = tokencake_parameters(registration["reference_environment"])
                required.update(
                    max_prefill_tokens=parameters["prefill_token_budget"],
                    chunked_prefill_size=parameters["chunked_prefill_tokens"],
                    context_length=parameters["context_limit"],
                    max_running_requests=parameters["running_limit"],
                    page_size=parameters["policy_block_tokens"],
                )
            if any(info.get(key) != value for key, value in required.items()):
                raise ValueError("actual native AgentX data-plane settings differ")
            census = json.loads((arm / "server/native_capacity_census.json").read_bytes())["capacity"]
            if census["device_full_tokens"] != 805198 or census["host_full_tokens"] != 2034506:
                raise ValueError("native capacity differs from the shared qualified pools")
            controls.reservation.write_json(arm / "server/server_info.json", info)
            scheduler = json.loads((arm / "server/native_telemetry_ready.json").read_bytes())["scheduler_pid"]
            identities = controls.owned_processes(identities)
            apps = controls.qualified.command([
                "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
            ]).splitlines()
            if scheduler not in {row["pid"] for row in identities} or str(scheduler) not in apps:
                raise ValueError("native scheduler is not an owned GPU process")
            state["gpu_process_verified"] = True
            controls.observed_configuration(registration, label, arm)
            with (arm / "replay.log").open("x") as log:
                driver = subprocess.Popen([
                    sys.executable, str(ROOT / "scripts/run_causal_gpu_replay.py"),
                    "--agentx-prepared", registration["prepared"],
                    "--agentx-token-dir", registration["tokens"],
                    "--output-dir", str(arm / "replay"),
                    "--base-url", f"http://127.0.0.1:{registration['port']}",
                    "--event-socket", str(event_socket), "--request-timeout-s", "3600",
                ], cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            identities.append(controls.reservation.process(driver.pid))
            state["status"] = "running_published_trace"
            while driver.poll() is None:
                pulse()
                if server.poll() is not None:
                    raise ValueError("native server exited during AgentX replay")
                controls.observed_configuration(registration, label, arm)
                time.sleep(5.)
            state["driver_exit_code"] = driver.wait()
            if state["driver_exit_code"]:
                raise ValueError("published-source baseline driver failed")
            result = json.loads((arm / "replay/summary.json").read_bytes())
            metrics = validate_published_result(registration, result, arm)
            state["status"] = "draining_telemetry"
            controls.wait_for_telemetry_drain(arm, server=server, pulse=pulse)
            controls.observed_configuration(registration, label, arm, final=True)
            controls.reservation.write_json(run / "result.json", {
                **metrics, "arm": label, "requests": registration["source_plan"]["requests"],
                "prompt_tokens": registration["source_plan"]["prompt_tokens"],
                "output_tokens": registration["source_plan"]["output_tokens"],
                "whole_run_output_tokens_per_second": (
                    registration["source_plan"]["output_tokens"] * 1000.
                    / metrics["workflow_event_makespan_ms"]
                ),
                "streaming_response_metrics": result["streaming_response_metrics"],
                "replay_timing": result["replay_timing"],
                "native_prefix_qualification": state.get("native_prefix_qualification"),
                "native_tokencake_qualification": state.get("native_tokencake_qualification"),
                "policy_configuration": registration.get("policy_configuration"),
                "full_population_gpu_replay_qualified": True,
                "native_source_capture_required": False, "all_normal_tails_retained": True,
                "performance_accepted": False, "paired_comparison_and_repetitions_pending": True,
            })
            state["status"] = "complete_review_pending"
        except BaseException as error:
            state.update(status="stopped_for_investigation", error_type=type(error).__name__, error=str(error))
        finally:
            try:
                controls.cleanup(arm, identities, state["status"])
                if event_socket is not None and identities:
                    event_socket.unlink(missing_ok=True)
            except BaseException as error:
                state["cleanup_error"] = repr(error)
            state["finished_at"] = controls.reservation.now().isoformat()
            controls.reservation.write_json(run / "state.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("start")
    launch.add_argument("--arm", choices=tuple(RUNTIME_ARMS), required=True)
    for name in ("run-root", "reference", "engine", "native-patch", "prepared", "tokens", "gpu-data-plane-report"):
        launch.add_argument("--" + name, type=Path, required=True)
    launch.add_argument("--port", type=int, required=True)
    launch.add_argument("--policy-config", type=Path)
    launch.add_argument("--after-run-root", type=Path)
    monitor = commands.add_parser("watch")
    monitor.add_argument("--registration", type=Path, required=True)
    review = commands.add_parser("review")
    review.add_argument("--run-root", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "start":
        start(args)
    elif args.command == "watch":
        watch(args.registration)
    else:
        if args.output.exists():
            raise FileExistsError("preserve the previous baseline review")
        result = review_completed_run(args.run_root)
        controls.reservation.write_json(args.output, result)
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
