"""Retain the complete pilot and its unserved declarations; do not launch GPUs."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_requirements import DEFAULT_DIAGNOSTICS, assess
from experiments.reports.workflow_work_training_20261029.finish_training import (
    audit_finished_source, source_ready,
)
from scripts import run_causal_source_capture as capture
from scripts.train_workflow_work import write


HERE = ROOT / "experiments/reports/parallel_checkpoint_pilot6_20261030"
REGISTRATION = Path(
    "/home/longhao/experiment/ops/workflow_parallel_checkpoint_pilot6_source_20261030_r1.registration.json"
)


def main():
    plan = capture.load(REGISTRATION)
    state = capture.load(REGISTRATION.with_suffix(".state.json"))
    if not source_ready(plan, state):
        raise ValueError("retain complete source work and actual owned exit")
    audit_finished_source(plan, state)
    reviewer = capture.load(HERE / "finish_r1/state.json")
    if (
        reviewer["status"] != "complete_pilot_requires_shape_review"
        or capture.reservation.alive(reviewer["process"])
    ):
        raise ValueError("complete offline reviewer has not actually exited")
    declaration = capture.load(HERE / "declaration.json")
    inventory = capture.load(HERE / "finish_r1/inventory.json")
    workstreams = capture.load(HERE / "finish_r1/workstreams.json")
    prompts = capture.load(HERE / "prompt_delivery_r3.json")
    targets = declaration["shape_review"]
    assessment = assess(inventory, workstreams, diagnostics={
        **DEFAULT_DIAGNOSTICS,
        "model_work_p90_p10": targets["model_work_p90_p10_diagnostic_target_range"][0],
        "multi_child_join_fraction": targets["multi_child_join_fraction_diagnostic_target"],
        "child_calls_median_max": targets["child_model_calls_median_diagnostic_target_max"],
        "child_calls_p90_max": targets["child_model_calls_p90_diagnostic_target_max"],
    })
    unserved = prompts["summary"]["unserved_child_declarations"]
    if unserved:
        assessment["shape_shortfalls"].append("unserved_declared_children_need_runtime_repair")
        assessment["shape_status"] = "needs_new_candidates"
    write(HERE / "declared_assessment_r1.json", assessment)
    paths = (
        REGISTRATION, HERE / "declaration.json", HERE / "finish_r1/inventory.json",
        HERE / "finish_r1/workstreams.json", HERE / "finish_r1/review.json",
        HERE / "prompt_delivery_r3.json", Path(__file__),
    )
    write(HERE / "complete_review_r1.json", {
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in paths},
        "complete_source_fresh_drain_and_actual_exit": True,
        "tokens": inventory["token_evidence"],
        "declared_shape_targets": assessment["diagnostic_targets"],
        "shape_shortfalls": assessment["shape_shortfalls"],
        "unserved_child_declarations": unserved,
        "all_raw_workflows_and_failed_attempts_retained": True,
        "source_rewritten": False, "formal_comparison_ready": False,
        "GPU_followup_registered": False, "performance_accepted": False,
    })
    print({
        "requests": inventory["token_evidence"]["requests"],
        "unserved_declarations": len(unserved),
        "shape_shortfalls": assessment["shape_shortfalls"], "GPU_action": False,
    })


if __name__ == "__main__":
    main()
