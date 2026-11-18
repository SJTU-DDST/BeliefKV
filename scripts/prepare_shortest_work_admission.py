"""Prepare matched short-work admission without changing model parameters or traffic."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts.audit_workflow_combination_choices import verify_model_work_pair
from scripts.train_workflow_work import write

FLAG = "shortest_work_first_admission"


def prepare_artifacts(learned, oracle):
    if learned.get(FLAG, False) is not False or oracle.get(FLAG, False) is not False:
        raise ValueError("prepare from the unchanged original combination artifacts")
    profile = learned.get("admission_cost_profile", {})
    if (
        profile.get("kind") != "r5_frozen_batch_cost_proxy_capacity_v7"
        or profile != oracle.get("admission_cost_profile")
        or profile["costs"].get("selection_objective") != "benefit"
        or oracle.get("kind") != "workflow_continuous_model_work_combination_oracle_v1"
    ):
        raise ValueError("require matched continuous model-work truth and the benefit R5 profile")
    result = deepcopy(learned), deepcopy(oracle)
    for artifact in result:
        artifact[FLAG] = True
    if forecast_fingerprint(result[0]) != forecast_fingerprint(learned):
        raise ValueError("short-work admission changed the evaluated prediction parameters")
    WorkflowWorkModel(result[0])
    return result


def configuration(original, model_path, digest):
    result = deepcopy(original)
    for prefix in ("", "BELIEFKV_"):
        result["common_environment"][prefix + "WORKFLOW_WORK_MODEL"] = str(model_path.resolve())
        result["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = digest
    result.pop("source_CPU_binding", None)
    qualification = result.get("workflow_work_qualification")
    if isinstance(qualification, dict) and "model_sha256" in qualification:
        if "evaluation" in qualification:
            qualification.update(
                serving_artifact=str(model_path.resolve()),
                serving_artifact_sha256=digest,
                prediction_parameters_same_as_evaluated_export=True,
            )
        else:
            qualification["model_sha256"] = digest
    result["comparison_contract"].update(
        shortest_work_first_admission=True,
        tool_replacement_selection="shortest_work_first_without_free_budget_filter",
        selection_strategy="JOIN_rights_then_shortest_workflow_and_ready_sibling_adjacency",
        active_continuity_and_native_capacity_authority_preserved=True,
        ranking_gain_isolated=False,
    )
    result["status"] = "CPU_prepared_shortest_work_admission_not_GPU_registered"
    result["purpose"] = "Bounded short-work admission and immediate tool-pair ordering on unchanged traffic"
    if "gpu_execution" in result:
        result["gpu_execution"]["registered"] = False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve all earlier configurations and comparison results")
    plan = json.loads(args.campaign_plan.read_bytes())
    paths = {
        label: Path(plan["stages"][label]["configuration"])
        for label in ("full", "oracle", "learned")
    }
    configs = {label: json.loads(path.read_bytes()) for label, path in paths.items()}
    verified = verify_model_work_pair(configs["full"], configs["oracle"], configs["learned"])
    model_paths = {
        label: Path(configs[label]["common_environment"]["WORKFLOW_WORK_MODEL"])
        for label in ("oracle", "learned")
    }
    originals = {label: json.loads(path.read_bytes()) for label, path in model_paths.items()}
    learned, oracle = prepare_artifacts(originals["learned"], originals["oracle"])
    args.output_dir.mkdir(parents=True, exist_ok=False)
    learned_path, oracle_path = args.output_dir / "learned_model.json", args.output_dir / "oracle_work.json"
    write(learned_path, learned)
    oracle["combination_reference_source_model_sha256"] = sha256_file(learned_path)
    WorkflowWorkOracleArtifact(oracle)
    write(oracle_path, oracle)
    prepared = {
        "learned": configuration(configs["learned"], learned_path, sha256_file(learned_path)),
        "oracle": configuration(configs["oracle"], oracle_path, sha256_file(oracle_path)),
    }
    prepared["oracle"]["comparison_contract"].update(
        oracle_future_work_used_outside_combination=True,
        oracle_future_work_outside_combination_scope="existing_immediate_tool_donor_pair_order",
    )
    verify_model_work_pair(configs["full"], prepared["oracle"], prepared["learned"])
    for label, config in prepared.items():
        write(args.output_dir / (label + ".json"), config)
    inputs = [
        args.campaign_plan, *paths.values(), *model_paths.values(), *verified,
        Path(__file__), ROOT / "beliefkv/policy/workflow_shortest_frontier.py",
        ROOT / "beliefkv/runtime/workflow_combination.py",
        ROOT / "beliefkv/runtime/workflow_size_admission.py",
        ROOT / "scripts/finish_model_work_oracle.py",
        ROOT / "scripts/review_learned_work_ranking.py",
    ]
    write(args.output_dir / "preparation.json", {
        "source_campaign": str(args.campaign_plan.resolve()),
        "reused_completed_Full_configuration": str(paths["full"]),
        "reused_completed_Full_registration": plan["stages"]["full"]["registration"],
        "configurations": {
            label: {
                "path": str((args.output_dir / (label + ".json")).resolve()),
                "sha256": sha256_file(args.output_dir / (label + ".json")),
            } for label in prepared
        },
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "same_forecast_parameters_and_training_scale": True,
        "same_work_tokens_tools_dependencies_arrivals_cohort_and_resources": True,
        "same_ordinary_window_and_existing_immediate_event_paths": True,
        "Full_rerun_registered": False, "GPU_action": False, "performance_accepted": False,
    })
    print(json.dumps({"output": str(args.output_dir.resolve()), "GPU_action": False}))


if __name__ == "__main__":
    main()
