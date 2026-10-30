"""Prepare a model/tool oracle from this workload's completed retained Full."""

import argparse
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import population_fingerprint
from beliefkv.runtime.workflow_work_oracle import KIND
from experiments.reports.workflow_mix_20261029.review_burst import complete_work, model_cost
from experiments.reports.workflow_resume_oracle_20261029.prepare import artifact
from scripts import run_causal_comparison as causal
from scripts.train_workflow_work import write


RANK_FIELDS = frozenset({
    "WORKFLOW_SIZE_RESUME", "BELIEFKV_WORKFLOW_SIZE_RESUME",
    "WORKFLOW_WORK_SOURCE", "BELIEFKV_WORKFLOW_WORK_SOURCE",
    "WORKFLOW_WORK_MODEL", "BELIEFKV_WORKFLOW_WORK_MODEL",
    "WORKFLOW_WORK_MODEL_SHA256", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256",
})


def validate_control(configuration, plan, review):
    if (
        configuration["order"] != ["full"] or plan["order"] != ["full"]
        or review["complete_work_and_actual_exit_verified"] is not True
        or review["registration_sha256"] != sha256_file(Path(review["registration"]))
        or review["match_contract"]["source_plan"] != plan["source_plan"]
        or review["match_contract"]["token_source_sha256"] != plan["token_source_sha256"]
        or review["match_contract"]["workflow_arrivals"] != plan["workflow_arrivals"]
        or review["match_contract"]["tool_timing_overlay_sha256"] != plan["tool_timing_overlay"]["sha256"]
    ):
        raise ValueError("oracle calibration must use the complete matching new-workload control")
    common = configuration["common_environment"]
    for name in (
        "WORKFLOW_SIZE_RESUME", "BELIEFKV_WORKFLOW_SIZE_RESUME",
        "AGENT_FREE_DISPATCH_INTERVAL_MS", "BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS",
        "TOOL_PREFETCH", "EXECUTION_FRONTIER_RESTORE", "LAYER_HANDOFF", "ENABLE_MIXED_CHUNK",
    ):
        if common[name] != "0":
            raise ValueError("retained Full changed a fixed control: " + name)
    if common["AGENT_CONTROL_INTERVAL_MS"] != "2000":
        raise ValueError("preserve the retained Full control cadence")
    cost = model_cost(review["metrics"]["decode"])
    if cost != review["model_cost_calibration"]["model_seconds_per_work_token"]:
        raise ValueError("oracle model cost differs from complete decode observations")
    return cost


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--control-review", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve prior oracle preparation")
    if complete_work(args.control) is None:
        raise ValueError("wait for the control's full work, fresh drain and actual exit")
    here = ROOT / "experiments/reports/workflow_mix_20261029"
    base = here / "burst_full.json"
    configuration, plan, review = (
        causal.load(path) for path in (base, args.control, args.control_review)
    )
    if causal.load(Path(plan["experiment_configuration"])) != configuration:
        raise ValueError("prepared Full configuration differs from the actually executed control")
    if Path(review["registration"]).resolve() != args.control.resolve():
        raise ValueError("control review belongs to another execution")
    cost = validate_control(configuration, plan, review)
    population_path = here / "scope18_finish_r1/population.json"
    population = causal.load(population_path)
    if population["population_sha256"] != population_fingerprint(population["workflows"]):
        raise ValueError("measured population changed after tier assignment")
    source = Path(plan["source_arm"])
    labels = here / "scope18_oracle_labels_r1.json"
    overlay = Path(configuration["tool_timing_overlay"]["path"])
    if sha256_file(overlay) != configuration["tool_timing_overlay"]["sha256"]:
        raise ValueError("oracle tool timings differ from the control")
    # These boundaries label diagnostics only; oracle ordering uses continuous seconds.
    sizes = sorted(row["model_work_units"] for row in population["workflows"])
    cuts = [(sizes[index - 1] + sizes[index]) / 2. for index in (6, 12)]
    units = {"metadata": {"prefill_token_weight": .01}, "work_thresholds": cuts}
    oracle = artifact(source, units, causal.load(labels), overlay, cost)
    if (
        set(oracle["workflows"]) != set(plan["workflow_ids"])
        or len(oracle["requests"]) != plan["source_plan"]["requests"]
        or oracle["source_binding"]["token_sha256"] != plan["token_source_sha256"]
    ):
        raise ValueError("oracle source differs from the complete new control")
    oracle["model_cost_calibration"] = {
        **review["model_cost_calibration"],
        "path": str(args.control_review.resolve()), "sha256": sha256_file(args.control_review),
        "declared_before_oracle_GPU_execution": True,
    }
    oracle["threshold_scope"] = "measured_population_model_work_tertiles_for_diagnostics_only"
    args.output_dir.mkdir(parents=True, exist_ok=False)
    oracle_path = args.output_dir / "oracle.json"
    write(oracle_path, oracle)
    candidate = deepcopy(configuration)
    common = candidate["common_environment"]
    common.update({
        "WORKFLOW_SIZE_RESUME": "1", "BELIEFKV_WORKFLOW_SIZE_RESUME": "1",
        "WORKFLOW_WORK_SOURCE": "oracle", "BELIEFKV_WORKFLOW_WORK_SOURCE": "oracle",
        "WORKFLOW_WORK_MODEL": str(oracle_path.resolve()),
        "BELIEFKV_WORKFLOW_WORK_MODEL": str(oracle_path.resolve()),
        "WORKFLOW_WORK_MODEL_SHA256": sha256_file(oracle_path),
        "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256": sha256_file(oracle_path),
    })
    if any(
        common.get(name) != value for name, value in configuration["common_environment"].items()
        if name not in RANK_FIELDS
    ) or common.keys() != configuration["common_environment"].keys():
        raise ValueError("oracle must change only the within-category rank provider")
    candidate.update({
        "status": "new_workload_cached_model_tool_oracle_pending_CPU_and_GPU_qualification",
        "candidate_provenance": {
            "base": str(base), "base_sha256": sha256_file(base),
            "control_registration": str(args.control.resolve()),
            "control_registration_sha256": sha256_file(args.control),
            "control_review": str(args.control_review.resolve()),
            "control_review_sha256": sha256_file(args.control_review),
            "population_sha256": population["population_sha256"],
            "changed_policy": "within_original_category_continuous_remaining_model_tool_path",
            "free_dispatch_remains_disabled": True, "active_agents_not_preempted_for_size": True,
            "future_information_used": True, "deployable": False,
        },
        "gpu_execution": {
            "registered": False, "requires_fresh_position_and_sampling": True,
            "requires_explicit_oracle": True, "requires_decision_eligible_model": False,
        },
        "workflow_work_qualification": {
            "kind": KIND, "artifact": str(oracle_path.resolve()),
            "artifact_sha256": sha256_file(oracle_path),
            "future_information_used": True, "deployable": False,
            "scope": "remaining_model_tool_critical_path_seconds",
            "learned_calibration_requirement_applies": False,
        },
        "performance_accepted": False,
    })
    candidate["comparison_contract"].update({
        "workflow_work_ranking": True, "future_information_used": True, "deployable": False,
        "workflow_resume_key": [
            "original_category", "oracle_remaining_model_tool_path_seconds", "context_tokens", "queue_sequence",
        ],
        "tool_and_model_wait_target": "causal_critical_path_parallel_max",
        "free_dispatch_interval_ms": 0,
        "oracle_is_not_an_optimal_JCT_bound": True,
    })
    preflights = {}
    for mode in ("full", "scheduler_only", "native_matched"):
        configured = arm_environment(common, mode)
        for name in causal.SERVER_FIELDS:
            if name in configured:
                configured["BELIEFKV_" + name] = configured[name]
        preflights[mode] = causal.preflight_runtime(configured, common["MODEL_PATH"])
        if mode != "full" and configured["BELIEFKV_WORKFLOW_WORK_SOURCE"] != "learned":
            raise ValueError("oracle information leaked into a control")
    configuration_path = args.output_dir / "full.json"
    write(configuration_path, candidate)
    inputs = (base, args.control, args.control_review, population_path, labels, overlay, Path(__file__))
    write(args.output_dir / "preparation.json", {
        "configuration": str(configuration_path.resolve()),
        "configuration_sha256": sha256_file(configuration_path),
        "artifact_sha256": sha256_file(oracle_path),
        "constructor_preflights": preflights,
        "requests": len(oracle["requests"]), "workflows": len(oracle["workflows"]),
        "model_seconds_per_work_token": cost,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "only_changed_environment_fields": sorted(
            name for name, value in common.items()
            if value != configuration["common_environment"][name]
        ),
        "future_information_used": True, "deployable": False, "GPU_action": False,
    })
    print({"requests": len(oracle["requests"]), "configuration": str(configuration_path)})


if __name__ == "__main__":
    main()
