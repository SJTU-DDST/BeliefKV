"""Independently bind the twelve completed GPU scenes and their actual cleanup."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from experiments.reports.tokencake_radix_session_20261028 import defer_gpu_components as queue


def main():
    output = HERE / "gpu_review.json"
    if output.exists():
        raise FileExistsError("preserve original component review")
    plan = queue.controls.load(queue.REGISTRATION)
    state_path = queue.REGISTRATION.with_suffix(".state.json")
    state = queue.controls.load(state_path)
    queue.verify(plan)
    previous = queue.controls.load(queue.PREDECESSOR)
    if not queue.controls.predecessor_ready({
        **previous, "after_registration": str(queue.PREDECESSOR),
    }):
        raise ValueError("KVFlow predecessor has not actually exited")
    queue.verify_predecessor_cleanup(previous)
    if (
        state["status"] != "native_session_components_qualified"
        or state["component_returncode"] != 0
        or state["owned"]
        or not state.get("actual_component_exit_verified")
        or not state.get("owned_gpu_process_absent")
        or any(queue.controls.reservation.alive(state[name]) for name in (
            "watcher", "component_process",
        ))
    ):
        raise ValueError("component supervisor or owned process has not completed")
    report_path = Path(state["component_report"]["path"])
    if queue.sha256_file(report_path) != state["component_report"]["sha256"]:
        raise ValueError("actual TokenCake component report changed")
    report = queue.controls.load(report_path)
    queue.validate_report(
        report, engine_tree=plan["engine_tree"],
        patch_sha256=plan["native_patch_sha256"], root=queue.APPLICATION,
    )
    if report["owned_gpu_pid"] != state["component_process"]["pid"]:
        raise ValueError("actual GPU report and owned process differ")
    cleanup_path = queue.RUN / "owned_process_cleanup.json"
    cleanup = queue.controls.load(cleanup_path)
    if cleanup.get("remaining_processes") != [] or not cleanup.get("finished_at"):
        raise ValueError("component cleanup lacks its completed boundary")
    if queue.gpu_processes():
        raise ValueError("a GPU process remains before formal baseline preparation")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "twelve_native_session_components_independently_verified",
        "application": plan["application"], "engine_tree": plan["engine_tree"],
        "scenarios": list(report["scenarios"]),
        "native_session_scenarios": list(report["native_session_scenarios"]),
        "same_native_and_policy_sources_verified": True,
        "actual_component_and_supervisor_exit_verified": True,
        "GPU_compute_processes_absent": True,
        "whole_model_or_performance_qualified": False, "performance_accepted": False,
        "inputs_sha256": {
            str(path): queue.sha256_file(path) for path in (
                Path(__file__), queue.REGISTRATION, state_path, report_path, cleanup_path,
                HERE / "component_queue_cpu.xml", HERE / "bound_cpu.json",
            )
        },
    }
    queue.controls.reservation.write_json(output, result)
    print(json.dumps({
        "status": result["status"], "plain_scenes": len(result["scenarios"]),
        "native_session_scenes": len(result["native_session_scenarios"]),
        "actual_component_and_supervisor_exit_verified": True,
    }, indent=2))


if __name__ == "__main__":
    main()
