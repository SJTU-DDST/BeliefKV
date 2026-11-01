"""Declare one CALL-only runtime repair on the frozen best matched workload."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkout", "predictive", "cohort", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    root = args.checkout.resolve()
    sys.path.insert(0, str(root))
    from beliefkv.experiments.internal_comparison import arm_environment
    from beliefkv.experiments.source_provenance import sha256_file
    from scripts import run_causal_comparison as causal
    from scripts.train_workflow_work import write
    from scripts.verify_measured_execution import verify_execution

    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve candidates and use hidden-CUDA nice19")
    completed = verify_execution(args.predictive, args.cohort)
    reference = causal.load(args.predictive)
    path = Path(reference["experiment_configuration"])
    base = causal.load(path)
    if reference["order"] != ["full"]:
        raise ValueError("require the completed selected nonranking Full")
    configuration = deepcopy(base)
    configuration["candidate_provenance"] = {
        "kind": "observed_foreground_CALL_cold_owner_coverage_repair",
        "completed_control_registration": str(args.predictive.resolve()),
        "completed_control_registration_sha256": sha256_file(args.predictive),
        "scheduling_environment_unchanged": True,
        "work_tokens_tools_arrivals_cohort_and_capacities_unchanged": True,
        "no_Oracle_or_workload_search": True,
        "no_new_prefetch_or_forced_ready_agent_preemption": True,
        "new_algorithm_innovation_claimed": False,
    }
    configuration["comparison_contract"]["foreground_CALL_cold_owner_coverage"] = {
        "authority": "observed_live_foreground_dependency_and_native_pressure",
        "settled_host_copy_and_native_locks_DMA_sessions_required": True,
        "RETURN_or_Cancel_revokes_immediately": True,
        "original_prepare_paths_unchanged": True,
    }
    configuration["candidate_promotion_gate"] = {
        "minimum_complete_measured_mean_JCT_reduction": 0.03,
        "minimum_common_window_output_throughput_fraction": 0.98,
        "check_actual_CALL_capacity_release_and_following_consumer_service": True,
        "check_useful_prefix_loss_and_Host_block_recomputation": True,
        "negative_result_keeps_previous_nonranking_Full": True,
        "single_pair_is_not_statistical_significance": True,
    }
    configuration["GPU_execution"] = {
        "registered": False, "requires_selected_native_completion": True,
        "requires_current_bound_CPU_clean_freeze_and_fresh_position_sampling": True,
    }
    configuration["performance_accepted"] = False
    settings = arm_environment(configuration["common_environment"], "full")
    for name in causal.SERVER_FIELDS:
        if name in settings:
            settings["BELIEFKV_" + name] = settings[name]
    entry = causal.preflight_runtime(settings, settings["MODEL_PATH"])
    if configuration["common_environment"] != base["common_environment"]:
        raise ValueError("the CALL repair must not change scheduling or resource parameters")
    output = args.output_dir / "full.json"
    write(output, configuration)
    write(args.output_dir / "entry.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "completed_control_requests": completed["measured_requests"],
        "completed_control_outputs": completed["measured_output_tokens"],
        "actual_Full_constructor": entry,
        "effective_environment_equal_to_control": True,
        "inputs_sha256": {str(item.resolve()): sha256_file(item) for item in (
            args.predictive, args.cohort, path, Path(completed["certificate"]),
            root / "beliefkv/runtime/sglang_v0520_runtime.py", output, Path(__file__),
        )},
        "GPU_action": False, "performance_accepted": False,
    })
    print({"configuration": str(output), "GPU_action": False}, flush=True)


if __name__ == "__main__":
    main()
