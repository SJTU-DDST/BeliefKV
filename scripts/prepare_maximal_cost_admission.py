"""Prepare matched cost-aware maximal packing on the existing short-work traffic."""

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
from scripts.prepare_shortest_work_admission import configuration as short_configuration
from scripts.train_workflow_work import write


FLAG = "cost_aware_maximal_admission"


def prepare_artifacts(learned, oracle, *, selection_objective="benefit_per_cost"):
    if selection_objective not in ("benefit_per_cost", "benefit"):
        raise ValueError("unknown maximal admission objective")
    from_maximal_cost = selection_objective == "benefit"
    if any(
        artifact.get("shortest_work_first_admission") is not True
        or artifact.get(FLAG, False) is not from_maximal_cost
        or artifact.get("maximal_admission_objective", "benefit_per_cost") != "benefit_per_cost"
        for artifact in (learned, oracle)
    ):
        raise ValueError("prepare from matched unchanged short-work artifacts")
    profile = learned.get("admission_cost_profile", {})
    if (
        profile.get("kind") != "r5_frozen_batch_cost_proxy_capacity_v7"
        or profile != oracle.get("admission_cost_profile")
        or profile["costs"].get("selection_objective") != "benefit"
    ):
        raise ValueError("retain the exact short-work full-batch cost profile")
    result = deepcopy(learned), deepcopy(oracle)
    for artifact in result:
        artifact[FLAG] = True
        if selection_objective == "benefit":
            artifact["maximal_admission_objective"] = "benefit"
    if forecast_fingerprint(result[0]) != forecast_fingerprint(learned):
        raise ValueError("packing changed the prediction parameters")
    WorkflowWorkModel(result[0])
    return result


def configuration(original, model_path, digest, *, maximal_objective="benefit_per_cost"):
    if maximal_objective not in ("benefit_per_cost", "benefit"):
        raise ValueError("unknown maximal admission objective")
    result = short_configuration(original, model_path, digest)
    result["comparison_contract"].update(
        cost_aware_maximal_admission=True,
        competitive_set_selection_objective="maximal_benefit_per_cost",
        selection_strategy="JOIN_rights_then_cost_aware_maximal_feasible_set",
        all_jointly_fitting_candidates_selected=True,
    )
    result["status"] = "CPU_prepared_maximal_cost_admission_not_GPU_registered"
    result["purpose"] = "Cost-aware maximal packing with unchanged workflow prediction and traffic"
    if maximal_objective == "benefit":
        result["comparison_contract"].update(
            maximal_admission_objective="benefit",
            competitive_set_selection_objective="maximal_benefit",
            selection_strategy="JOIN_rights_then_benefit_maximal_feasible_set",
        )
        result["status"] = "CPU_prepared_maximal_benefit_admission_not_GPU_registered"
        result["purpose"] = "Maximal workflow benefit with unchanged prediction, costs and traffic"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--maximal-objective", choices=("benefit_per_cost", "benefit"), default="benefit_per_cost")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier preparations and negative results")
    plan = json.loads(args.campaign_plan.read_bytes())
    paths = {name: Path(plan["stages"][name]["configuration"]) for name in ("full", "oracle", "learned")}
    configs = {name: json.loads(path.read_bytes()) for name, path in paths.items()}
    verified = verify_model_work_pair(configs["full"], configs["oracle"], configs["learned"])
    originals = {
        name: Path(configs[name]["common_environment"]["WORKFLOW_WORK_MODEL"])
        for name in ("oracle", "learned")
    }
    learned, oracle = prepare_artifacts(
        *(json.loads(originals[name].read_bytes()) for name in ("learned", "oracle")),
        selection_objective=args.maximal_objective,
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    model_path, truth_path = args.output_dir / "learned_model.json", args.output_dir / "oracle_work.json"
    write(model_path, learned)
    oracle["combination_reference_source_model_sha256"] = sha256_file(model_path)
    WorkflowWorkOracleArtifact(oracle)
    write(truth_path, oracle)
    prepared = {
        "learned": configuration(
            configs["learned"], model_path, sha256_file(model_path), maximal_objective=args.maximal_objective,
        ),
        "oracle": configuration(
            configs["oracle"], truth_path, sha256_file(truth_path), maximal_objective=args.maximal_objective,
        ),
    }
    verify_model_work_pair(configs["full"], prepared["oracle"], prepared["learned"])
    for name, value in prepared.items():
        write(args.output_dir / (name + ".json"), value)
    inputs = (
        args.campaign_plan, *paths.values(), *originals.values(), *verified, Path(__file__),
        ROOT / "beliefkv/policy/workflow_maximal_admission.py",
        ROOT / "beliefkv/runtime/workflow_combination.py",
        ROOT / "scripts/finish_model_work_oracle.py",
        ROOT / "scripts/review_learned_work_ranking.py",
    )
    write(args.output_dir / "preparation.json", {
        "source_campaign": str(args.campaign_plan.resolve()),
        "reused_completed_Full_registration": plan["stages"]["full"]["registration"],
        "configurations": {
            name: {
                "path": str((args.output_dir / (name + ".json")).resolve()),
                "sha256": sha256_file(args.output_dir / (name + ".json")),
            } for name in prepared
        },
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "same_prediction_parameters_costs_work_tools_arrivals_and_cohort": True,
        **({"maximal_admission_objective": "benefit"} if args.maximal_objective == "benefit" else {}),
        "same_JOINEOS_active_continuity_and_native_capacity_authority": True,
        "Full_rerun_registered": False, "GPU_action": False, "performance_accepted": False,
    })
    print(json.dumps({"output": str(args.output_dir.resolve()), "GPU_action": False}))


if __name__ == "__main__":
    main()
