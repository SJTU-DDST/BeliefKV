"""Audit complete native19 tails and original tasks; never launch GPU successors."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-original19-source-operator-75414c7e")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "remaining_node_original19_finish_r1"
REGISTRATION = OPS / "workflow_remaining_node19_source_20261031_r1.registration.json"
SELF_REGISTRATION = OPS / "workflow_remaining_node19_source_finish_20261031_r1.registration.json"
BINDING = REPORTS / "remaining_node_original19_source_r1/binding.json"
RANKING = REPORTS / "original69_initial12_mean70_prepare_r1/control_oracle.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import inventory_capture, population_summary
from scripts import run_causal_comparison as causal
from scripts import run_causal_source_capture as capture
from scripts.audit_parallel_workstreams import audit as audit_shape
from scripts.audit_workload_tool_execution import audit as audit_tools
from scripts.prepare_original_task_mix import source_integrity, validate_population
from scripts.train_workflow_work import write


def hashes(paths):
    return {str(path.resolve()): sha256_file(path) for path in paths}


def watch():
    registration = causal.load(SELF_REGISTRATION)
    output = HERE / "state.json"
    me = capture.reservation.process(os.getpid())
    if os.getpriority(os.PRIO_PROCESS, 0) != 19 or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("source analysis must stay nice19 CPU-only")
    state = {"status": "waiting_complete_native_source", "observer": me, "GPU_action": False}
    try:
        while True:
            if hashes(Path(name) for name in registration["inputs_sha256"]) != registration["inputs_sha256"]:
                raise ValueError("frozen source analysis inputs changed")
            plan = causal.load(REGISTRATION)
            path = REGISTRATION.with_suffix(".state.json")
            observed = causal.load(path) if path.exists() else {}
            state.update(
                checked_at=datetime.now(timezone.utc).isoformat(),
                source_status=observed.get("status"), source_requests=observed.get("source_requests"),
            )
            capture.reservation.write_json(output, state)
            if observed.get("finished_at"):
                if observed.get("status") != "source_captured_gpu_replay_pending":
                    raise ValueError("source capture did not qualify: " + repr(observed))
                if not any(capture.reservation.alive(observed[name]) for name in ("watcher", "driver")):
                    break
            time.sleep(30.)
        run = Path(plan["run_root"])
        arm = run / "native_capture"
        cleanup = causal.load(arm / "owned_process_cleanup.json")
        if cleanup["remaining_processes"] or any(
            capture.reservation.alive(row) for row in cleanup["identities"]
        ):
            raise ValueError("source-owned processes remain after completion")
        capture.observe(arm, final=True, expected_capacity=plan.get("capture_capacity"))
        outcome = observed["outcome"]
        if observed["original_exit_code"] != 0 or any(
            outcome.get(name) != 19 for name in ("completed_workflows", "native_agent_jct_eligible_workflows")
        ) or observed["source_selection"]["accepted"] is not True:
            raise ValueError("not all nineteen original tasks naturally completed the source protocol")
        manifest = causal.load(Path(plan["source_workload"]))
        with zipfile.ZipFile(manifest["archive"]) as archive:
            validate_population(manifest["workloads"], archive)
            integrity = {row["repo"]: source_integrity(row, archive) for row in manifest["workloads"]}
        if integrity != causal.load(BINDING)["original_source_integrity"]:
            raise ValueError("the original source/tests/context changed")
        coefficient = causal.load(RANKING)["model_seconds_per_work_token"]
        inventory = inventory_capture(arm, seconds_per_model_unit=coefficient)
        if len(inventory["workflows"]) != 19 or any(not row["eligible"] for row in inventory["workflows"]):
            raise ValueError("whole original source inventory is incomplete or ineligible")
        write(HERE / "inventory.json", inventory)
        shaped = audit_shape(arm)
        write(HERE / "shape.json", shaped)
        clients = list(arm.glob("client_*/manifest.json"))
        if len(clients) != 1:
            raise ValueError("source must have one exact native client population")
        tool_report = audit_tools(clients[0].parent)
        write(HERE / "tool_execution.json", tool_report)
        population = population_summary(inventory["workflows"])
        write(HERE / "complete.json", {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "capture_registration": str(REGISTRATION), "capture_state": observed,
            "complete_original_projects": 19, "source_population": population,
            "natural_source_shape": shaped["summary"],
            "root_test_result_statuses": tool_report["root_test_result_statuses"],
            "application_correctness_or_project_test_success_claimed": False,
            "normal_tails_and_original_tool_times_preserved": True,
            "previous_complete_sources_need_not_be_recaptured": True,
            "ready_supply_KV_pressure_and_performance_remain_separate_checks": True,
            "fixed_cohort_oracle_or_baseline_automatically_started": False,
            "inputs_sha256": hashes((
                REGISTRATION, REGISTRATION.with_suffix(".state.json"), BINDING, RANKING,
                HERE / "inventory.json", HERE / "shape.json", HERE / "tool_execution.json",
                arm / "telemetry_drain.json", arm / "owned_process_cleanup.json", Path(__file__),
            )),
            "GPU_action": False, "formal_workload_accepted": False, "performance_accepted": False,
        })
        state.update(status="complete_original19_inventory_ready", completed_projects=19,
                     finished_at=datetime.now(timezone.utc).isoformat())
        capture.reservation.write_json(output, state)
        print(json.dumps({"completed_projects": 19, "population": population,
                          "shape": shaped["summary"], "GPU_action": False}), flush=True)
    except BaseException as error:
        state.update(status="source_analysis_failed", error=repr(error),
                     finished_at=datetime.now(timezone.utc).isoformat())
        capture.reservation.write_json(output, state)
        raise


def register():
    if SELF_REGISTRATION.exists() or HERE.exists():
        raise ValueError("preserve prior analysis registrations")
    plan = causal.load(REGISTRATION)
    if plan["environment"]["ROOT_COUNT"] != "19":
        raise ValueError("analysis must follow the exact native19 source")
    HERE.mkdir(parents=True, exist_ok=False)
    paths = (
        REGISTRATION, BINDING, RANKING, Path(__file__),
        ROOT / "beliefkv/experiments/workload_mix.py",
        ROOT / "scripts/audit_parallel_workstreams.py",
        ROOT / "scripts/audit_workload_tool_execution.py",
    )
    capture.reservation.write_json(SELF_REGISTRATION, {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "source": str(REGISTRATION), "inputs_sha256": hashes(paths),
        "GPU_action": False, "automatic_GPU_followup": False,
    })
    environment = {
        **os.environ, "CUDA_VISIBLE_DEVICES": "99999", "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
    }
    with (HERE / "run.log").open("x") as stream:
        child = subprocess.Popen(
            ["nice", "-n", "19", sys.executable, str(Path(__file__).resolve()), "--watch"],
            cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=stream,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(json.dumps({"registration": str(SELF_REGISTRATION),
                      "initial_handle": capture.reservation.process(child.pid),
                      "GPU_action": False}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    arguments = parser.parse_args()
    watch() if arguments.watch else register()
