"""Prepare matched Full/Oracle/R5 inputs for the shared short-workflow selector."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.runtime.workflow_work_oracle import COMBINATION_PATH_KIND, KIND, WorkflowWorkOracleArtifact
from scripts.run_causal_comparison import environment, preflight_runtime
from scripts.train_workflow_work import write


POLICY_FIELDS = ("free_dispatch_coalescing_window_ms", "tool_retention_policy")
MODEL_FIELDS = {
    prefix + name for prefix in ("", "BELIEFKV_")
    for name in ("WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256", "WORKFLOW_WORK_SOURCE")
}


def path_oracle_artifact(model, labels, model_sha256):
    serving = WorkflowWorkModel(model)
    original = WorkflowWorkOracleArtifact(labels)
    if (
        serving.completion_path is None or model.get("strict_remaining_work_admission") is not True
        or labels["kind"] != KIND
        or original.model_seconds_per_work_token
        != serving.completion_path.raw["model_seconds_per_work_token"]
        or not isinstance(model_sha256, str) or len(model_sha256) != 64
        or any(char not in "0123456789abcdef" for char in model_sha256)
    ):
        raise ValueError("require the strict path predictor and identical original model/tool calibration")
    raw = deepcopy(labels)
    for name in (
        "tool_boundary_preemption", "ready_wait_priority_ms", "ready_wait_priority_grace_ms",
        "ready_wait_priority_max_boost", "ready_capacity_accumulation_ms",
        "cost_aware_maximal_admission", "maximal_admission_objective", "pressure_work_order",
    ):
        raw.pop(name, None)
    raw.update(
        kind=COMBINATION_PATH_KIND, admission_order="r5_combination_only",
        combination_work_readout="continuous",
        combination_work_reference=deepcopy(serving.continuous_readout.contract),
        combination_reference_source_model_sha256=model_sha256,
        admission_cost_profile=deepcopy(model["admission_cost_profile"]),
        strict_remaining_work_admission=True, shortest_work_first_admission=True,
        remaining_work_ranking_enabled=True, work_thresholds=list(model["work_thresholds"]),
    )
    for name in POLICY_FIELDS:
        raw.pop(name, None)
        if name in model:
            raw[name] = deepcopy(model[name])
    raw["admission_cost_profile"]["oracle_reference_path_s"] = serving.completion_path.reference_s
    WorkflowWorkOracleArtifact(raw)
    return raw


def nonranking_artifact(model):
    raw = deepcopy(model)
    raw.pop("completion_path_readout", None)
    raw.update(
        strict_remaining_work_admission=False, shortest_work_first_admission=False,
        remaining_work_ranking_enabled=False,
    )
    WorkflowWorkModel(raw)
    return raw


def configuration(control, path, digest, *, source, ranking):
    result = deepcopy(control)
    env = result["common_environment"]
    for prefix in ("", "BELIEFKV_"):
        env[prefix + "WORKFLOW_WORK_MODEL"] = str(path.resolve())
        env[prefix + "WORKFLOW_WORK_MODEL_SHA256"] = digest
        env[prefix + "WORKFLOW_WORK_SOURCE"] = source
    result.update(status="CPU_prepared_shared_R5_path_comparison", performance_accepted=False)
    result.pop("source_CPU_binding", None)
    result["workflow_work_qualification"] = {
        "model": str(path.resolve()), "model_sha256": digest,
        "decision_eligible": True, "future_information_used": source == "oracle",
        "deployable": source != "oracle",
        "validation_scope": "reused_development_population_not_confirmatory",
    }
    contract = result["comparison_contract"]
    for name in (
        "admission_utility", "continuous_utility_definition", "continuous_utility_reference_work",
        "continuous_utility_training_reference_work",
    ):
        contract.pop(name, None)
    contract.update(
        workflow_work_ranking=ranking, future_information_used=source == "oracle",
        workflow_work_prediction=(
            "exact_remaining_model_tool_critical_path_seconds" if source == "oracle" else
            "predicted_remaining_model_tool_critical_path_seconds" if ranking else "disabled"
        ),
        strict_remaining_work_admission=ranking,
        same_R5_selector_resources_sibling_groups_and_tool_pressure_rules=True,
        nonranking_retains_work_model_but_omits_path_head=True,
        CPU_inference_overhead_is_part_of_method_cost=True,
        ranking_gain_isolated=False, public_timeout_force_resume_enabled=False,
        ready_wait_priority_ms=0, ready_capacity_accumulation_ms=0,
        priority_is_not_divided_by_request_cost_or_JOIN_fanout=True,
        additional_tool_boundary_preemption_enabled=False,
        matched_control_changes_only_materialization_policy=False,
        matched_pair_changes_only_remaining_work_ranking=False,
        configuration_pair_changes_only_remaining_work_ranking=False,
        learned_and_oracle_use_same_continuous_utility=False,
        prediction_parameters_unchanged=False,
        workflow_join_progress="observed_ready_sibling_group_if_fits",
        workflow_ready_join_sibling_preference="observed_ready_sibling_group_if_fits",
        competitive_set_selection_objective="strict_remaining_path_then_joint_sibling_fit",
        selection_strategy="protected_JOIN_then_remaining_model_tool_path_then_ready_sibling_fit",
        workflow_resume_key=["protected_ready_JOIN", "remaining_path_seconds", "ready_sibling_group"],
        workflow_distribution_changes_pressure_victims=ranking,
        workflow_pressure_victim_selection=(
            "blocked_tool_retention_then_path_preference" if ranking
            else "blocked_tool_retention_then_original_context_order"
        ),
    )
    result["gpu_execution"] = {
        "registered": False, "automatic_GPU_followup": False,
        "requires_current_pair_completion_and_linear_source_integration": True,
    }
    return result


def prepare(args):
    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("require fresh CPU-only comparison preparation")
    control = json.loads(args.control_configuration.read_bytes())
    model = WorkflowWorkModel.load(args.model, sha256_file(args.model)).raw
    labels = WorkflowWorkOracleArtifact.load(args.oracle_labels, sha256_file(args.oracle_labels)).raw
    if (
        control["order"] != ["full"]
        or control.get("candidate_source_arm") != labels["source_binding"]["source_arm"]
        or control["common_environment"].get("WORKFLOW_COMBINATION_ADMISSION") != "1"
    ):
        raise ValueError("require the existing shared-controller configuration and exact source population")
    for name in ("workflow_arrivals", "private_analysis_cohort"):
        binding = control[name]
        if sha256_file(Path(binding["path"])) != binding["sha256"]:
            raise ValueError("the fixed traffic or measured members changed")
    oracle = path_oracle_artifact(model, labels, sha256_file(args.model))
    arms = {"full": nonranking_artifact(model), "oracle": oracle, "learned": model}
    paths, configs, preflights = {}, {}, {}
    for index, (name, raw) in enumerate(arms.items()):
        path = args.output_dir / (name + "_work.json")
        write(path, raw)
        config = configuration(
            control, path, sha256_file(path),
            source="oracle" if name == "oracle" else "learned", ranking=name != "full",
        )
        output = args.output_dir / (name + ".json")
        write(output, config)
        paths[name], configs[name] = str(output.resolve()), config
        common = config["common_environment"]
        with tempfile.TemporaryDirectory(prefix="bkv-r5-path-preflight-") as directory:
            settings, _, _ = environment(
                {"arms": {"full": common}}, "full", ROOT, args.engine, Path(directory),
                19970 + index, ROOT / "plugins", sampling_mode="position",
                max_running_requests=int(common["MAX_RUNNING_REQUESTS"]),
                tool_restore_mode=common["TOOL_RESTORE_MODE"],
            )
            preflights[name] = preflight_runtime(settings, settings["MODEL_PATH"])
    learned_env = configs["learned"]["common_environment"]
    for config in configs.values():
        if {
            name: value for name, value in config["common_environment"].items() if name not in MODEL_FIELDS
        } != {name: value for name, value in learned_env.items() if name not in MODEL_FIELDS}:
            raise ValueError("a matched arm changed non-model serving settings")
    pins = {
        str(path.resolve()): sha256_file(path) for path in (
            args.control_configuration, args.model, args.oracle_labels, Path(__file__),
            Path(control["workflow_arrivals"]["path"]),
            Path(control["private_analysis_cohort"]["path"]),
            *(args.output_dir / (name + "_work.json") for name in arms),
            *(Path(value) for value in paths.values()),
            ROOT / "beliefkv/runtime/workflow_work_oracle.py",
            ROOT / "beliefkv/runtime/workflow_oracle_admission.py",
            ROOT / "beliefkv/runtime/workflow_size_admission.py",
            ROOT / "beliefkv/runtime/workflow_combination.py",
            ROOT / "scripts/run_causal_comparison.py",
        )
    }
    result = {
        "configurations": paths, "runtime_cpu_preflight": preflights,
        "same_tasks_tokens_tools_dependencies_arrivals_resources_and_fixed_members": True,
        "oracle_and_learned_share_selection_algorithm_and_seconds_units": True,
        "full_omits_path_inference_and_bypasses_ranked_selection": True,
        "source_bound_CPU_and_GPU_native_qualification_pending": True,
        "GPU_action": False, "GPU_registered": False, "performance_accepted": False,
        "inputs_sha256": pins,
    }
    write(args.output_dir / "preparation.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control-configuration", "model", "oracle-labels", "engine", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    fresh = not args.output_dir.exists()
    try:
        result = prepare(args)
    except Exception as error:
        if fresh and args.output_dir.is_dir():
            write(args.output_dir / "failure.json", {
                "error": repr(error), "stdout": getattr(error, "stdout", None),
                "stderr": getattr(error, "stderr", None), "GPU_action": False,
            })
        raise
    print(json.dumps({"configurations": result["configurations"], "GPU_action": False}), flush=True)


if __name__ == "__main__":
    main()
