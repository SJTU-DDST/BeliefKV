"""Read a completed scope-diversity capture; register no further GPU action."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import (
    burst_arrivals, context_crossing, inventory_capture, population_fingerprint,
    population_summary, shuffled_workflows, stratify,
)
from experiments.reports.workflow_work_training_20261029.finish_training import (
    audit_finished_source, source_ready,
)
from scripts import run_causal_source_capture as capture
from scripts.train_workflow_work import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-cost-evidence", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") not in ("", "99999", "-1"):
        raise ValueError("post-capture workload profiling must remain CPU-only")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    plan = capture.load(args.registration)
    binding = {str(path): sha256_file(path) for path in (args.registration, args.model_cost_evidence)}
    while True:
        if binding != {name: sha256_file(Path(name)) for name in binding}:
            raise ValueError("post-capture inputs changed")
        state_path = args.registration.with_suffix(".state.json")
        state = capture.load(state_path)
        if source_ready(plan, state):
            break
        if state.get("status") in ("failed", "stopped", "administratively_stopped"):
            raise ValueError("preserve incomplete source collection")
        time.sleep(10.)
    completion = audit_finished_source(plan, state)
    write(args.output_dir / "complete_source.json", completion)
    metrics = capture.load(args.model_cost_evidence)["metrics"]["FULL"]["decode"]
    coefficient = metrics["request_step_ms"] / metrics["request_steps"] / 1000.
    inventory = inventory_capture(
        Path(plan["run_root"]) / "native_capture", seconds_per_model_unit=coefficient,
    )
    write(args.output_dir / "inventory.json", inventory)
    population = inventory["workflows"]
    if len(population) != 18 or any(row["eligible"] is not True for row in population):
        raise ValueError("new source lacks eighteen complete project-disjoint parallel workflows")
    labelled = stratify(population)
    order = shuffled_workflows(labelled, seed=21)
    write(args.output_dir / "population.json", {
        "kind": "measured_whole_workflow_mix", "population_sha256": population_fingerprint(order),
        "workflows": order, "size_summary": population_summary(population),
        "context_crossing": context_crossing(labelled),
        "GPU_performance_competition_verified": False,
        "diagnostic_p90_p10_target_is_not_a_hard_rejection_rule": True,
        "main_rate_calibration_pending": True, "new_GPU_action_registered": False,
        "performance_accepted": False,
    })
    write(args.output_dir / "burst_arrivals.json", burst_arrivals([row["workflow_id"] for row in order]))
    write(args.output_dir / "review.json", {
        "inputs_sha256": binding, "complete_source": True,
        "whole_workflows": len(population), "model_work": population_summary(population)["model_work_units"],
        "new_GPU_action_registered": False, "performance_accepted": False,
        "next_step": (
            "inspect actual size/context/JOIN diversity; then matched burst control and "
            "model+tool oracle; calibrate main rate from the new control, not the old trace"
        ),
    })
    print({"whole_workflows": len(population), "new_GPU_action_registered": False})


if __name__ == "__main__":
    main()
