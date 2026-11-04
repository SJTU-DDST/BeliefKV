"""Verify the requested matched scheduling control and separate its two comparisons."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.review_predictive_thunder_cohort import TRAFFIC_FIELDS
from scripts.review_sustained_oracle import compare_cohorts
from scripts.start_workflow_ranking_disabled import verify_pair
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


def matching(plans):
    reference = plans[0]
    for plan in plans:
        if plan["order"] != ["full"]:
            raise ValueError("matched ranking comparisons require completed Full arms")
        for name in (*TRAFFIC_FIELDS, "expected_replay_capacity", "native_execution_configuration"):
            if plan.get(name) != reference.get(name):
                raise ValueError("the matched comparison changed fixed traffic or resources: " + name)


def review(registrations, directories, cohort):
    plans = [causal.load(path) for path in registrations]
    matching(plans)
    specs = [causal.load(Path(plan["experiment_configuration"])) for plan in plans]
    verify_pair(specs[0], specs[1])
    executions, cohorts, decisions = [], [], []
    for registration, plan, directory in zip(registrations, plans, directories):
        causal.comparison.verify_sources(plan)
        execution = verify_execution(registration, cohort)
        review_matches_execution(causal.load(directory / "review.json"), execution)
        current = causal.load(directory / "cohort.json")
        if current["cohort"] != causal.load(cohort):
            raise ValueError("the fixed complete-JCT cohort changed")
        for name, prefix in current.get("input_jsonl_prefixes", {}).items():
            path = Path(name)
            if (
                path.stat().st_size != prefix["complete_prefix_bytes"]
                or sha256_file(path) != prefix["complete_prefix_sha256"]
            ):
                raise ValueError("completed cohort evidence changed")
        executions.append(execution)
        cohorts.append(current)
        decisions.append(causal.load(directory / "workload_decision.json"))
    if any(execution["work"] != executions[0]["work"] for execution in executions[1:]):
        raise ValueError("the complete measured work differs")
    return {
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "ranking_enabled_vs_latest_disabled": compare_cohorts(cohorts[1], cohorts[0]),
        "latest_disabled_vs_old_disabled": compare_cohorts(cohorts[2], cohorts[1]),
        "latest_enabled_vs_old_disabled": compare_cohorts(cohorts[2], cohorts[0]),
        "ranking_effect_includes_its_own_selection_overhead": True,
        "latest_disabled_keeps_prediction_worker": True,
        "old_disabled_comparison_is_whole_control_revision_not_single_interval": True,
        "original_workload_decisions_preserved": decisions,
        "single_run_statistical_significance_claimed": False,
        "complete_measured_work_fresh_drain_and_actual_owned_exit": True,
        "work": executions[0]["work"], "GPU_action": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("enabled", "disabled", "old-disabled", "enabled-review", "disabled-review",
                 "old-disabled-review", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve prior matched-control results")
    registrations = (args.enabled, args.disabled, args.old_disabled)
    directories = (args.enabled_review, args.disabled_review, args.old_disabled_review)
    state_path = args.output.with_suffix(".watcher.json")
    if args.watch:
        state = {"process": causal.reservation.process(os.getpid()), "status": "waiting_fixed18"}
        while True:
            plan_path = args.disabled
            state["checked_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            if plan_path.exists():
                plan = causal.load(plan_path)
                state_doc = causal.load(plan_path.with_suffix(".state.json"))
                owners = [state_doc.get(name) for name in ("watcher", "server", "driver")]
                complete = Path(plan["run_root"]) / "cohort_termination/completion.json"
                if complete.exists() and all(
                    (args.disabled_review / name).exists()
                    for name in ("review.json", "cohort.json", "workload_decision.json")
                ) and not any(causal.reservation.alive(owner) for owner in owners if owner):
                    break
                if state_doc["status"].startswith("stopped_") and not complete.exists():
                    state.update(status="stopped_incomplete", error=state_doc.get("error"))
                    write(state_path, state)
                    raise RuntimeError("the comparison exited before complete measured work")
            write(state_path, state)
            time.sleep(15.)
    result = review(registrations, directories, args.cohort)
    pins = [
        *registrations, args.cohort, Path(__file__), ROOT / "scripts/review_sustained_oracle.py",
        ROOT / "scripts/verify_measured_execution.py",
        ROOT / "scripts/start_workflow_ranking_disabled.py",
    ]
    for registration, directory in zip(registrations, directories):
        pins.extend((Path(causal.load(registration)["experiment_configuration"]), *(
            directory / name for name in ("review.json", "cohort.json", "workload_decision.json")
        )))
    result["inputs_sha256"] = {str(path.resolve()): sha256_file(path) for path in pins}
    write(args.output, result)
    if args.watch:
        write(state_path, {
            "status": "complete_verified", "process": causal.reservation.process(os.getpid()),
            "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        })
    print(json.dumps({
        name: value["groups"]["measured"]["mean_JCT_reduction_percent"]
        for name, value in result.items() if name.endswith("_disabled")
    }), flush=True)


if __name__ == "__main__":
    main()
