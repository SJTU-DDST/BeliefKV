"""Declare workflow work ranking separately from the frozen preceding Full."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowWorkModel


def prepare(base, model, evaluation):
    declaration = json.loads(base.read_bytes())
    digest = sha256_file(model)
    predictor = WorkflowWorkModel.load(model, digest)
    report = json.loads(evaluation.read_bytes())
    if report["model_sha256"] != digest:
        raise ValueError("work model and evaluation fingerprints differ")
    training = set(predictor.metadata.get("training_projects", ()))
    calibration = set(predictor.metadata.get("calibration_projects", ()))
    held_out = set(report.get("test_projects", ()))
    split = predictor.metadata.get("split_contract", {})
    if split.get("mode", "project") == "project":
        if not training or not calibration or not held_out or (
            training & calibration or (training | calibration) & held_out
        ):
            raise ValueError("workflow ranking requires separate training/calibration/evaluation projects")
    elif split.get("mode") in ("capture", "workflow"):
        if split != report.get("split_contract") or (
            split.get("mode") == "capture" and split.get("complete_captures_disjoint") is not True
            or split.get("complete_workflows_disjoint") is not True
            or split.get("unseen_project_generalization_claimed") is not False
        ):
            raise ValueError("workflow ranking capture holdout contract changed")
        for suffix in ("capture_paths", "execution_sha256"):
            if suffix == "capture_paths" and split["mode"] == "workflow":
                continue
            populations = [
                set(split.get(prefix + suffix, ())) for prefix in (
                    "training_", "calibration_", "evaluation_",
                )
            ]
            if not all(populations) or any(
                populations[i] & populations[j] for i in range(3) for j in range(i)
            ):
                raise ValueError("workflow ranking requires separate complete captures and executions")
    else:
        raise ValueError("unknown workflow ranking split contract")
    env = declaration["common_environment"]
    predictor.task_input_prefix(env)
    for name in (
        "LAYER_HANDOFF", "BELIEFKV_LAYER_HANDOFF", "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT",
        "TOOL_PREFETCH", "BELIEFKV_ENABLE_TOOL_PREFETCH", "EXECUTION_FRONTIER_RESTORE",
    ):
        if env.get(name, "0") != "0":
            raise ValueError("retain the user-disabled handoff/tool/frontier prefetch settings")
    env.update({
        "WORKFLOW_SIZE_RESUME": "1", "BELIEFKV_WORKFLOW_SIZE_RESUME": "1",
        "WORKFLOW_WORK_MODEL": str(model.resolve()),
        "BELIEFKV_WORKFLOW_WORK_MODEL": str(model.resolve()),
        "WORKFLOW_WORK_MODEL_SHA256": digest,
        "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256": digest,
    })
    arm_environment(env, "full")
    declaration.update({
        "status": (
            "cpu_candidate_waiting_for_gpu_qualification" if predictor.metadata.get("decision_eligible")
            else "cpu_candidate_waiting_for_qualified_work_predictor"
        ),
        "workflow_work_qualification": {
            "model_sha256": digest, "model": str(model.resolve()),
            "evaluation": str(evaluation.resolve()), "evaluation_sha256": sha256_file(evaluation),
            "decision_eligible": predictor.metadata.get("decision_eligible", False),
            "rejection_reasons": predictor.metadata.get("decision_rejection_reasons", ()),
            "work_unit": {
                "prefill_token_weight": predictor.metadata["prefill_token_weight"],
                "decode_token_weight": 1.,
                "kind": "logical_token_work_proxy_not_GPU_seconds",
            },
            "remaining_gpu_seconds_claimed": False,
            "validation_scope": predictor.metadata.get("validation_scope", "unseen_projects"),
            "split_contract": split,
            "task_input_kind": predictor.task_input_kind,
            "execution_controls": predictor.deployment_controls,
        },
        "candidate_provenance": {
            "base": str(base.resolve()), "base_sha256": sha256_file(base),
            "new_policy_scope": "paused_recovery_within_reference_categories",
            "base_control_interval_ms": int(env["AGENT_CONTROL_INTERVAL_MS"]),
            "base_full_complete_review_required_before_gpu": True,
            "running_frozen_experiments_changed": False,
        },
        "gpu_execution": {
            "registered": False, "requires_decision_eligible_model": True,
            "requires_fresh_position_and_sampling": True,
            "requires_complete_preceding_full_review": True,
        },
        "performance_accepted": False,
    })
    declaration["comparison_contract"].update({
        "workflow_size_resume": True,
        "workflow_resume_key": ["original_category", "workflow_bucket", "context_tokens", "queue_sequence"],
        "workflow_work_prediction": "background_coalesced_200ms_task_cached",
        "workflow_work_uncertainty_fallback": "medium_and_original_order",
        "workflow_new_active_victim_selection": False,
        "workflow_additional_full_raw_budget_gate": False,
        "reference_decay_and_pressure_preserved": True,
        "workflow_ready_join_sibling_preference": "within_category_and_bucket_fitting_subsets",
        "unchanged_event_grant_paths": True,
    })
    return declaration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    declaration = prepare(args.base, args.model, args.evaluation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        json.dump(declaration, handle, indent=2)
        handle.write("\n")
    print(json.dumps({"status": declaration["status"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
