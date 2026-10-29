"""Retain the pre-import failure and observe the replacement CPU-only waiter."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as controls


def main():
    output = HERE / "component_queue_observed.json"
    if output.exists():
        raise FileExistsError("preserve the original queue observation")
    failed, queued = HERE / "gpu_native_consumers_r1", HERE / "gpu_native_consumers_r2"
    old_identity = controls.load(failed / "supervisor.json")
    if controls.reservation.alive(old_identity):
        raise ValueError("failed first queue entry is still alive")
    error_log = failed / "supervisor.log"
    if "ModuleNotFoundError: No module named 'server_reservation'" not in error_log.read_text():
        raise ValueError("first queue failure does not match the preserved import traceback")
    if (failed / "state.json").exists() or any((failed / name).exists() for name in (
        "swap_plain", "swap_native_sessions", "pressure_plain", "pressure_native_sessions",
    )):
        raise ValueError("first entry progressed beyond the declared pre-import failure")
    registration = queued / "registration.json"
    plan = controls.load(registration)
    identity, state = controls.load(queued / "supervisor.json"), controls.load(queued / "state.json")
    actual = controls.reservation.process(identity["pid"])
    if (
        actual is None or actual["start_ticks"] != identity["start_ticks"]
        or actual.get("argv", [])[1:] != [
            str(Path(plan["checkout"]) / Path(__file__).with_name("queue_components.py").relative_to(ROOT)),
            "watch", "--registration", str(registration),
        ]
        or state["status"] != "waiting_complete_tokencake" or state.get("owned") != []
    ):
        raise ValueError("replacement queue is not the exact live CPU-only frozen waiter")
    controls.comparison.verify_cpu_snapshot(
        Path(plan["checkout"]), controls.load(Path(plan["cpu_manifest"])),
    )
    for path, digest in plan["inputs_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("frozen queue input changed")
    native = git_source_state(
        Path(plan["engine_checkout"]),
        canonical_patch=Path(plan["checkout"]) / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if not native["matches_worktree"] or native["actual_tree"] != plan["engine_tree"]:
        raise ValueError("queued native tree differs from the CPU qualification")
    previous = controls.load(Path(plan["predecessor"]).with_suffix(".state.json"))
    if not controls.reservation.alive(previous["watcher"]):
        raise ValueError("TokenCake is no longer live; record its terminal handoff separately")
    inputs = (
        Path(__file__), error_log, failed / "registration.json", failed / "supervisor.json",
        registration, queued / "supervisor.json",
    )
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "failed_entry": {
            "scope": "pre_gpu_import_failure", "original_process": old_identity,
            "actual_exit_verified": True, "no_component_started": True,
            "cause": "nested worktree could not locate machine reservation helper",
        },
        "queued_registration": str(registration), "actual_waiter": actual,
        "state": state, "predecessor_watcher_live": True,
        "native_engine": native, "all_762_case_sources_match_frozen_application": True,
        "GPU_experiment_started": False, "whole_model_or_formal_baseline_qualified": False,
        "inputs_sha256": {str(path): sha256_file(path) for path in inputs},
        "performance_accepted": False,
    }
    controls.reservation.write_json(output, result)
    print(json.dumps({
        "queued_pid": actual["pid"], "status": state["status"],
        "failed_entry_actual_exit_verified": True, "GPU_experiment_started": False,
    }, indent=2))


if __name__ == "__main__":
    main()
