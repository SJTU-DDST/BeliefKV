"""Preserve an evaluated workflow predictor while removing only its cost denominator."""

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
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from scripts import run_causal_comparison as causal
from scripts.train_workflow_work import write


KIND = "r5_frozen_batch_cost_proxy_capacity_v7"


def benefit_only_artifact(original):
    profile = original.get("admission_cost_profile", {})
    if (
        profile.get("kind") != "r5_frozen_batch_cost_proxy_capacity_v6"
        or CombinationCosts(**profile["costs"]).decode_cost_basis != "full_batch"
        or CombinationCosts(**profile["costs"]).selection_objective != "benefit_per_cost"
    ):
        raise ValueError("remove the denominator only from the existing capacity-v6 full-batch profile")
    expected = hashlib.sha256(json.dumps({
        "costs": profile["costs"], "capacity_summary_kind": profile["kind"],
    }, sort_keys=True).encode()).hexdigest()
    if profile.get("identity") != expected:
        raise ValueError("the existing ratio profile fingerprint changed")
    result = deepcopy(original)
    updated = result["admission_cost_profile"]
    updated["kind"] = KIND
    updated["costs"]["selection_objective"] = "benefit"
    updated["identity"] = hashlib.sha256(json.dumps({
        "costs": updated["costs"], "capacity_summary_kind": KIND,
    }, sort_keys=True).encode()).hexdigest()
    CombinationCosts(**updated["costs"])
    restored = deepcopy(result)
    restored["admission_cost_profile"] = deepcopy(profile)
    if restored != original:
        raise ValueError("benefit-only preparation changed prediction or observation fields")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier objective-only preparations")
    config = causal.load(args.base)
    env = config["common_environment"]
    original_path = Path(env["WORKFLOW_WORK_MODEL"])
    inputs = {str(path.resolve()): sha256_file(path) for path in (
        args.base, args.evaluation, original_path, Path(__file__),
        ROOT / "beliefkv/policy/workflow_combination.py",
        ROOT / "beliefkv/runtime/workflow_combination.py",
    )}
    if any(env.get(prefix + "WORKFLOW_WORK_MODEL_SHA256") != sha256_file(original_path)
           or env.get(prefix + "WORKFLOW_WORK_MODEL") != str(original_path)
           for prefix in ("", "BELIEFKV_")):
        raise ValueError("objective-only preparation requires identical prediction aliases")
    evaluation = causal.load(args.evaluation)
    if evaluation["model_sha256"] != sha256_file(original_path):
        raise ValueError("preserve the exact evaluated prediction artifact")
    original = causal.load(original_path)
    result = benefit_only_artifact(original)
    model_path = args.output_dir / "model.json"
    write(model_path, result)
    WorkflowWorkModel.load(model_path, sha256_file(model_path))
    evaluation.update(
        model_sha256=sha256_file(model_path),
        deployment_changes_prediction_parameters=False,
        deployment_selection_objective="benefit",
        deployment_objective_source_model=str(original_path.resolve()),
        deployment_objective_source_model_sha256=sha256_file(original_path),
    )
    write(args.output_dir / "evaluation.json", evaluation)
    for prefix in ("", "BELIEFKV_"):
        env[prefix + "WORKFLOW_WORK_MODEL"] = str(model_path.resolve())
        env[prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(model_path)
    config["purpose"] = "Workflow/JOIN benefit-only admission; unchanged predictor, traffic and resource rules"
    config["comparison_contract"].update(
        combination_selection_objective="benefit",
        only_combination_selection_objective_changed=True,
        objective_comparison_source_configuration=str(args.base.resolve()),
        objective_comparison_source_sha256=sha256_file(args.base),
        cost_tie_break_added=False,
        prediction_parameters_and_support_unchanged=True,
        predictor_and_serving_policy_source_unchanged=False,
    )
    config["gpu_execution"]["registered"] = False
    config["status"] = "cpu_candidate_waiting_for_source_bound_qualification"
    write(args.output_dir / "full.json", config)
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("an objective-only preparation input changed")
    write(args.output_dir / "preparation.json", {
        "inputs_sha256": inputs, "profile": result["admission_cost_profile"],
        "configuration_sha256": sha256_file(args.output_dir / "full.json"),
        "model_sha256": sha256_file(model_path),
        "prediction_parameters_support_and_inputs_unchanged": True,
        "resource_growth_and_required_JOIN_checks_unchanged": True,
        "reference_relative_ties_and_search_budget_unchanged": True,
        "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({"profile": KIND, "objective": "benefit", "GPU_action": False}), flush=True)


if __name__ == "__main__":
    main()
