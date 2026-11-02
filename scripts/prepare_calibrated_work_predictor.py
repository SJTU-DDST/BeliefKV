"""Bind an unchanged learned R5 distribution to current completed-control costs."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts
from beliefkv.predictor.workflow_work import R5_SCHEMA, WorkflowWorkModel
from scripts import run_causal_comparison as causal
from scripts.prepare_calibrated_combination_oracle import context_prefill_costs
from scripts.prepare_workflow_size_candidate import prepare
from scripts.train_workflow_work import write


KIND = "r5_frozen_batch_cost_proxy_capacity_v6"


def calibrated_predictor(original, calibration, context, *, calibration_binding):
    predictor = WorkflowWorkModel(original)
    if predictor.schema != R5_SCHEMA or predictor.metadata.get("decision_eligible") is not True:
        raise ValueError("deployment requires an eligible conditional-work predictor")
    costs = deepcopy(context_prefill_costs(context, calibration))
    costs["decode_cost_basis"] = "full_batch"
    CombinationCosts(**costs)
    profile = original.get("admission_cost_profile")
    if not isinstance(profile, dict) or not profile.get("oracle_reference_path_s"):
        raise ValueError("preserve the learned predictor's explicit path normalization")
    result = deepcopy(original)
    result["admission_cost_profile"] = {
        "kind": KIND, "costs": costs,
        "identity": hashlib.sha256(json.dumps({
            "costs": costs, "capacity_summary_kind": KIND,
        }, sort_keys=True).encode()).hexdigest(),
        "oracle_reference_path_s": profile["oracle_reference_path_s"],
    }
    result["deployment_cost_calibration"] = {
        **calibration_binding,
        "source": "completed_matching_nonranking_Full_worker_intervals",
        "prediction_weights_thresholds_splits_and_support_unchanged": True,
        "capacity_scope": "observed_FULL_pool_geometry_not_current_free_pages",
        "native_allocation_Host_locks_and_DMA_authority_unchanged": True,
        "pure_GPU_time_or_immediate_physical_fit_claimed": False,
    }
    WorkflowWorkModel(result)
    return result


def deployment_evaluation(evaluation, original, *, original_sha256, calibrated_sha256, binding):
    split = original["metadata"].get("split_contract")
    if evaluation["model_sha256"] != original_sha256 or not split or (
        split != evaluation.get("split_contract", evaluation.get("expanded_split"))
    ):
        raise ValueError("deployment must preserve the exact evaluated predictor and execution split")
    result = deepcopy(evaluation)
    result.update(
        model_sha256=calibrated_sha256, split_contract=deepcopy(split),
        source_prediction_evaluation=binding,
        deployment_changes_prediction_parameters=False,
        deployment_changes_only_cost_profile_and_provenance=True,
        source_model_sha256=original_sha256,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "model", "evaluation", "calibration", "context-calibration", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--serving-compatibility", type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve previous learned deployment preparations")
    paths = (args.base, args.model, args.evaluation, args.calibration, args.context_calibration)
    inputs = {str(path.resolve()): sha256_file(path) for path in paths}
    if args.serving_compatibility is not None:
        inputs[str(args.serving_compatibility.resolve())] = sha256_file(args.serving_compatibility)
    calibration, context = causal.load(args.calibration), causal.load(args.context_calibration)
    control = causal.load(Path(calibration["registration"]))
    if Path(control["experiment_configuration"]).resolve() != args.base.resolve():
        raise ValueError("inherit the exact completed control configuration")
    for report in (calibration, context):
        for name, digest in report["inputs_sha256"].items():
            if sha256_file(Path(name)) != digest:
                raise ValueError("a completed-control calibration input changed")
    original, evaluation = causal.load(args.model), causal.load(args.evaluation)
    new_model = args.output_dir / "model.json"
    new_evaluation = args.output_dir / "evaluation.json"
    binding = {
        "calibration_report": str(args.calibration.resolve()),
        "calibration_report_sha256": inputs[str(args.calibration.resolve())],
        "context_report": str(args.context_calibration.resolve()),
        "context_report_sha256": inputs[str(args.context_calibration.resolve())],
    }
    result = calibrated_predictor(original, calibration, context, calibration_binding=binding)
    write(new_model, result)
    normalized = deployment_evaluation(
        evaluation, original, original_sha256=inputs[str(args.model.resolve())],
        calibrated_sha256=sha256_file(new_model),
        binding={"path": str(args.evaluation.resolve()), "sha256": inputs[str(args.evaluation.resolve())]},
    )
    write(new_evaluation, normalized)
    config = prepare(args.base, new_model, new_evaluation, free_dispatch_interval_ms=500)
    config["candidate_provenance"].update(
        calibrated_control=calibration["registration"],
        cost_calibration=binding,
        comparison_scope="learned_distribution_plus_combination_and_current_cost_planning",
        direct_global_oracle_is_a_different_selector=True,
        unchanged_existing_control_reused=True,
    )
    config["comparison_contract"].update(
        combination_decode_cost_basis="full_batch",
        combination_device_planning="observed_FULL_pool_geometry",
        physical_capacity_credit=False,
    )
    config["gpu_execution"].update(
        requires_new_paired_control=False,
        existing_complete_control=calibration["registration"],
        qualified_unchanged_data_plane_may_reuse_position_and_sampling=True,
    )
    if args.serving_compatibility is not None:
        compatibility = causal.load(args.serving_compatibility)
        if (
            compatibility.get("candidate_model_sha256") != sha256_file(new_model)
            or compatibility.get("all_exported_prediction_probabilities_and_support_match") is not True
            or compatibility["frozen_serving_source"]["dirty"]
            or len(compatibility.get("exact_selection_source_modules", {})) < 6
        ):
            raise ValueError("the selected deployment lacks its complete frozen serving compatibility")
        config["gpu_execution"].update(
            requires_fresh_position_and_sampling=False,
            requires_same_qualified_serving_and_engine_identity=True,
            serving_commit=compatibility["frozen_serving_source"]["commit"],
            serving_compatibility_report=str(args.serving_compatibility.resolve()),
            serving_compatibility_report_sha256=inputs[str(args.serving_compatibility.resolve())],
        )
    write(args.output_dir / "full.json", config)
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("a learned deployment input changed")
    write(args.output_dir / "preparation.json", {
        "inputs_sha256": inputs, "model_sha256": sha256_file(new_model),
        "evaluation_sha256": sha256_file(new_evaluation),
        "configuration_sha256": sha256_file(args.output_dir / "full.json"),
        "profile": result["admission_cost_profile"],
        "prediction_parameters_and_evaluation_populations_unchanged": True,
        "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({
        "output": str(args.output_dir), "cost_profile": KIND,
        "conditional_distribution_and_combination_enabled": True,
        "new_training_or_evaluation": False, "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
