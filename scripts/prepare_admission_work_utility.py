"""Prepare matched continuous work utilities without changing trained forecasts."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_continuous_work import fit_admission_utility, forecast_fingerprint
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts.audit_workflow_combination_choices import verify_model_work_pair
from scripts.evaluate_workflow_continuous_projects import combine_datasets
from scripts.prepare_continuous_work_readout import training_rows
from scripts.prepare_maximal_cost_admission import configuration
from scripts.train_workflow_work import write


def prepare_artifacts(learned, oracle, rows, *, reference_work):
    reference = oracle.get("combination_work_reference")
    if (
        not isinstance(reference, dict)
        or reference.get("forecast_sha256") != forecast_fingerprint(learned)
        or learned.get("continuous_work_readout") not in (None, reference)
        or any(value.get("cost_aware_maximal_admission") is not True for value in (learned, oracle))
        or learned.get("admission_cost_profile") != oracle.get("admission_cost_profile")
    ):
        raise ValueError("utility preparation requires matched forecasts, reference and maximal policy")
    updated = fit_admission_utility(reference, rows, reference_work=reference_work)
    result, truth = deepcopy(learned), deepcopy(oracle)
    result["continuous_work_readout"] = updated
    truth["combination_work_reference"] = deepcopy(updated)
    if forecast_fingerprint(result) != forecast_fingerprint(learned):
        raise ValueError("admission utility changed trained forecasts")
    WorkflowWorkModel(result)
    return result, truth


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-plan", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, action="append", required=True)
    parser.add_argument("--utility-reference-work", type=float, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve existing preparations and use nice19 with hidden CUDA")
    plan = json.loads(args.campaign_plan.read_bytes())
    paths = {name: Path(plan["stages"][name]["configuration"]) for name in ("full", "oracle", "learned")}
    configs = {name: json.loads(path.read_bytes()) for name, path in paths.items()}
    verified = verify_model_work_pair(configs["full"], configs["oracle"], configs["learned"])
    models = {
        name: Path(configs[name]["common_environment"]["WORKFLOW_WORK_MODEL"])
        for name in ("oracle", "learned")
    }
    sources = [
        args.campaign_plan, *paths.values(), *verified, *models.values(), *args.dataset,
        Path(__file__), ROOT / "beliefkv/predictor/workflow_continuous_work.py",
        ROOT / "beliefkv/runtime/workflow_work_oracle.py",
        ROOT / "beliefkv/runtime/workflow_work_worker.py",
        ROOT / "beliefkv/runtime/workflow_combination.py",
        ROOT / "beliefkv/policy/workflow_maximal_admission.py",
        ROOT / "scripts/finish_model_work_oracle.py",
        ROOT / "scripts/prepare_continuous_work_readout.py",
        ROOT / "scripts/evaluate_workflow_continuous_projects.py",
    ]
    pins = {str(path.resolve()): sha256_file(path) for path in sources}
    original = {name: json.loads(path.read_bytes()) for name, path in models.items()}
    rows = training_rows(
        original["learned"], combine_datasets([json.loads(path.read_bytes()) for path in args.dataset]),
    )
    learned, oracle = prepare_artifacts(
        original["learned"], original["oracle"], rows, reference_work=args.utility_reference_work,
    )
    if any(sha256_file(Path(name)) != digest for name, digest in pins.items()):
        raise ValueError("utility preparation source or training data changed")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    model_path, truth_path = args.output_dir / "learned_model.json", args.output_dir / "oracle_work.json"
    write(model_path, learned)
    oracle["combination_reference_source_model_sha256"] = sha256_file(model_path)
    WorkflowWorkOracleArtifact(oracle)
    write(truth_path, oracle)
    prepared = {}
    for name, path in (("learned", model_path), ("oracle", truth_path)):
        spec = configuration(configs[name], path, sha256_file(path))
        spec.update(
            status="CPU_prepared_matched_continuous_utility_not_GPU_registered",
            purpose="Matched continuous remaining-work utility with unchanged trained forecasts",
        )
        spec["comparison_contract"].update(
            admission_utility=learned["continuous_work_readout"]["admission_utility"],
            learned_and_oracle_use_same_continuous_utility=True,
            prediction_parameters_unchanged=True,
        )
        prepared[name] = spec
    verify_model_work_pair(configs["full"], prepared["oracle"], prepared["learned"])
    for name, spec in prepared.items():
        write(args.output_dir / (name + ".json"), spec)
    write(args.output_dir / "preparation.json", {
        "source_campaign": str(args.campaign_plan.resolve()),
        "training_executions": learned["continuous_work_readout"]["training_executions"],
        "forecast_sha256": forecast_fingerprint(learned),
        "admission_utility": learned["continuous_work_readout"]["admission_utility"],
        "training_reference_retained": learned["continuous_work_readout"]["reference_work"],
        "prior_reweighted_from_exact_declared_training_executions_only": True,
        "training_and_calibration_parameters_unchanged": True,
        "same_work_tools_arrivals_cohort_costs_and_native_authority": True,
        "background_coverage_must_be_repaired_before_steady_serving_GPU_comparison": True,
        "GPU_action": False, "GPU_registered": False, "performance_accepted": False,
        "inputs_sha256": pins,
        "outputs_sha256": {
            name: sha256_file(args.output_dir / name) for name in (
                "learned_model.json", "oracle_work.json", "learned.json", "oracle.json",
            )
        },
    })
    print(json.dumps({"output": str(args.output_dir.resolve()), "GPU_action": False}))


if __name__ == "__main__":
    main()
