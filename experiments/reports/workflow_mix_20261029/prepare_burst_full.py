"""Prepare the retained Full control on the complete new scope18 burst."""

from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import population_fingerprint
from scripts import run_causal_comparison as causal
from scripts.train_workflow_work import write


def main():
    here = Path(__file__).resolve().parent
    source = Path("/home/longhao/experiment/ops/workflow_mix_scope18_source_20261029_r1.registration.json")
    population_path = here / "scope18_finish_r1/population.json"
    population = causal.load(population_path)
    if population["population_sha256"] != population_fingerprint(population["workflows"]):
        raise ValueError("measured scope18 population changed")
    base = ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"
    configuration = deepcopy(causal.load(base))
    timing, arrivals = here / "scope18_tool_timing_r1.json", here / "scope18_finish_r1/burst_arrivals.json"
    overlay = causal.load(timing)
    prior = Path(overlay["offline_prior"]["path"])
    common = configuration["common_environment"]
    common.update({
        "WORKLOAD_MANIFEST": "configs/workloads/locobench_mixed_scope18_20261029.json",
        "WORKFLOW_SIZE_RESUME": "0", "BELIEFKV_WORKFLOW_SIZE_RESUME": "0",
        "WORKFLOW_WORK_SOURCE": "learned", "BELIEFKV_WORKFLOW_WORK_SOURCE": "learned",
        "WORKFLOW_WORK_MODEL": "", "BELIEFKV_WORKFLOW_WORK_MODEL": "",
        "WORKFLOW_WORK_MODEL_SHA256": "", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256": "",
        "AGENT_FREE_DISPATCH_INTERVAL_MS": "0", "BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS": "0",
        "BELIEFKV_TOOL_TIMING_PRIOR": str(prior), "BELIEFKV_TOOL_TIMING_PRIOR_SHA256": sha256_file(prior),
    })
    for name in ("candidate_provenance", "arrival_experiment"):
        configuration.pop(name, None)
    configuration.update(
        status="retained_Full_new_scope18_burst_requires_fresh_position_qualification",
        source_registration=str(source), order=["full"],
        tool_timing_overlay={
            "path": str(timing), "sha256": sha256_file(timing),
            "timing_domain": overlay["timing_domain"], "synthetic_tool_timing": True,
        },
        workflow_arrivals={"path": str(arrivals), "sha256": sha256_file(arrivals)},
        gpu_execution={
            "registered": False, "requires_fresh_position_and_sampling": True,
            "reuse_old_performance_controls": False,
        },
        candidate_provenance={
            "retained_Full": str(base), "retained_Full_sha256": sha256_file(base),
            "population": str(population_path), "population_sha256": population["population_sha256"],
            "changed_dimensions": ["original_tasks", "fixed_timing_map", "shuffled_burst_arrivals"],
            "no_new_policy_or_predictor_for_this_control": True,
            "future_oracle_is_a_separate_followup": True,
        },
        workload_diagnostic={
            "actual_model_work_p90_p10": population["size_summary"]["model_work_units"]["p90_p10"],
            "joins": 97, "nested_joins": 0,
            "sufficient_recovery_competition_not_yet_verified": True,
            "five_to_ten_ratio_is_not_a_hard_filter": True,
        },
        performance_accepted=False,
    )
    configuration["comparison_contract"].update(
        workflow_work_ranking=False, future_information_used=False,
        source_native_JCT_is_not_a_matched_overlay_control=True,
    )
    preflights = {}
    for mode in ("full", "scheduler_only", "native_matched"):
        configured = arm_environment(common, mode)
        for name in causal.SERVER_FIELDS:
            if name in configured:
                configured["BELIEFKV_" + name] = configured[name]
        preflights[mode] = causal.preflight_runtime(configured, common["MODEL_PATH"])
    path = here / "burst_full.json"
    write(path, configuration)
    write(here / "burst_full_preparation.json", {
        "configuration": str(path), "configuration_sha256": sha256_file(path),
        "source_registration_sha256": sha256_file(source),
        "workload_sha256": sha256_file(ROOT / common["WORKLOAD_MANIFEST"]),
        "population_sha256": population["population_sha256"], "preflights": preflights,
        "tool_prior_matches_retained_prior": (
            sha256_file(prior) == causal.load(base)["common_environment"]["BELIEFKV_TOOL_TIMING_PRIOR_SHA256"]
        ),
        "same_fixed_tools_and_arrivals_required_for_oracle_and_Thunder": True,
        "operator_sha256": sha256_file(Path(__file__)), "GPU_action": False,
    })
    print({"configuration": str(path), "workflow_size_ranking": False, "GPU_action": False})


if __name__ == "__main__":
    main()
