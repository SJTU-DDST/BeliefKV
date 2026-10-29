"""Observe the exact repaired full arm after its fresh model/position checks."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent / "fragmented_retry_20261029"
OPS = ROOT.parent / "ops"
REGISTRATION = OPS / "tokencake_fragmented_capacity_20261029_r1.registration.json"
FAILED = OPS / "tokencake_locobench_native_hooks_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
LABEL = "tokencake_paper_observed"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import TOKENCAKE_TREE, engine_patch
from beliefkv.experiments.paper_baselines import (
    tokencake_parameters, validate_state, validate_tokencake_parameters,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tokencake_engine import verify_native_hooks
from scripts.audit_collecting_agent_pipeline import audit
from scripts import run_causal_comparison as controls
from scripts.qualify_paper_prefix_transfers import validate_report as validate_prefix
from scripts.qualify_tokencake_runtime import validate_report as validate_lifecycle


def main():
    output = HERE / "live_start.json"
    if output.exists():
        raise FileExistsError("preserve the original repaired full startup observation")
    plan, failed, control = (controls.load(path) for path in (REGISTRATION, FAILED, CONTROL))
    common = (
        "source_arm", "source_plan", "token_source_sha256", "source_capacity",
        "source_server_parameters", "workflow_ids", "quality_exclusions",
        "quality_exclusion_details", "sampling_mode", "stream_response_metrics",
        "request_timeout_s",
    )
    for prior in (failed, control):
        if any(plan[name] != prior[name] for name in common):
            raise ValueError("repaired full arm changed complete work, capacity or serving")
        overlays = [
            {name: value for name, value in row["tool_timing_overlay"].items() if name != "path"}
            for row in (plan, prior)
        ]
        if overlays[0] != overlays[1] or any(
            plan["reference_environment"][name] != prior["reference_environment"][name]
            for name in ("SERVER_RANDOM_SEED", "SAMPLING_SEED", "PYTHONHASHSEED")
        ):
            raise ValueError("repaired full arm changed common timers or seeds")
    parameters = tokencake_parameters(plan["reference_environment"])
    if plan["engine_tree"] != TOKENCAKE_TREE or parameters != tokencake_parameters(
        failed["reference_environment"],
    ):
        raise ValueError("repaired arm changed native engine or the seven paper parameters")
    arm, frozen = Path(plan["run_root"]) / LABEL, Path(plan["execution_checkout"])
    expected = Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl"
    result = audit(REGISTRATION, LABEL, expected)
    if result["exact_completed_receipts"] <= 0:
        raise ValueError("repaired full arm has no exact completed model requests yet")
    runtime = result["latest_runtime_snapshot"]
    validate_state(LABEL, runtime)
    validate_tokencake_parameters(runtime["paper_baseline"], parameters)
    controls.observed_configuration(plan, LABEL, arm)
    manifest_path = HERE.parent / "fragmented_capacity_cpu.sources.json"
    manifest = controls.load(manifest_path)
    controls.comparison.verify_cpu_snapshot(frozen, manifest)
    if any(
        plan["source_sha256"][name] != manifest[name]
        for name in ("beliefkv/runtime/tokencake_capacity.py", "beliefkv/runtime/tokencake_paper.py")
    ):
        raise ValueError("actual full arm lacks the two CPU-qualified repairs")
    patch = engine_patch(frozen, TOKENCAKE_TREE)
    qualifiers = {}
    for name, directory, validator in (
        ("prefix", "native_prefix_qualification", validate_prefix),
        ("lifecycle", "native_tokencake_qualification", validate_lifecycle),
    ):
        path = arm / directory / "summary.json"
        validator(
            controls.load(path), engine_tree=TOKENCAKE_TREE,
            patch_sha256=sha256_file(patch), root=frozen,
        )
        qualifiers[name] = {"path": str(path), "sha256": sha256_file(path)}
    state = controls.load(REGISTRATION.with_suffix(".state.json"))
    ready = controls.load(arm / "server/native_telemetry_ready.json")
    scheduler = controls.reservation.process(ready["scheduler_pid"])
    if scheduler is None or not any(
        row["pid"] == scheduler["pid"] and row["start_ticks"] == scheduler["start_ticks"]
        for row in controls.owned_processes([state["server"]])
    ):
        raise ValueError("repaired GPU scheduler is not actually owned")
    processes = {**result["actual_live_processes"], "gpu_scheduler": scheduler}
    if any(os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0 for row in processes.values()):
        raise ValueError("repaired full serving or driver has changed CPU priority")
    gpu_pids = [
        int(value) for value in subprocess.check_output([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ], text=True).splitlines() if value.strip()
    ]
    if gpu_pids != [scheduler["pid"]]:
        raise ValueError("GPU is absent or contains another concurrent process")
    position_path = OPS / "tokencake_fragmented_capacity_position_20261029_r1.registration.json"
    position_plan = controls.load(position_path)
    position_state = controls.load(position_path.with_suffix(".state.json"))
    for row in (position_state.get("watcher"), position_state.get("server")):
        if row is not None and controls.reservation.alive(row):
            raise ValueError("position supervisor or server is still live")
    smoke_path = Path(position_plan["run_root"]) / "gpu_smoke.json"
    calibration_path = Path(position_plan["run_root"]) / "sampling_calibration.json"
    smoke = controls.load(smoke_path)
    calibration = controls.load(calibration_path)
    if (
        smoke.get("requests") != 24 or smoke.get("native_stop_requests") != 24
        or smoke.get("sampling_mode") != "position"
        or any(smoke.get(name) is not True for name in (
            "gpu_process_verified", "native_data_plane_sample_verified",
            "native_position_sampling_verified",
        ))
        or sha256_file(Path(smoke["native_requests"])) != smoke["native_requests_sha256"]
        or calibration.get("native_sampler_preserved") is not True
        or calibration.get("position_dispatch_included") is not True
        or calibration.get("seed") != 21
        or {row["batch_size"] for row in calibration["cases"]} != {1, 8, 24}
    ):
        raise ValueError("fresh exact model/session/stopping or sampler evidence differs")
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        actual_owned_nice_zero_processes=processes, sole_native_gpu_pid=scheduler["pid"],
        actual_tokencake_parameters=parameters, fresh_physical_qualifiers=qualifiers,
        native_scheduler_hooks=verify_native_hooks(Path(plan["engine_checkout"])),
        fresh_position_smoke=smoke, fresh_sampler_calibration=calibration,
        same_complete_work_timing_capacity_seeds_and_native_stopping=True,
        failed_2922_prefix_preserved_and_not_resumed=True,
        current_cpu_qualified_repairs_installed=True,
        cpu_manifest={"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        fresh_position_inputs_sha256={
            str(path): sha256_file(path) for path in (position_path, smoke_path, calibration_path)
        },
        operator_sha256=sha256_file(Path(__file__)),
        complete_workload_or_performance_verified=False,
    )
    controls.reservation.write_json(output, result)
    print(json.dumps({
        "exact_completed_receipts": result["exact_completed_receipts"],
        "expected_requests": result["expected_requests"], "sole_gpu_pid": scheduler["pid"],
        "current_cpu_qualified_repairs_installed": True,
        "complete_workload_or_performance_verified": False,
    }, indent=2))


if __name__ == "__main__":
    main()
