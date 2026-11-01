"""Prepare exact native replay traffic without Full or Oracle scheduling."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
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
    from beliefkv.experiments.source_provenance import git_source_state, sha256_file
    from scripts import run_causal_comparison as causal
    from scripts.run_position_replay_calibration import valid_comparison_order
    from scripts.train_workflow_work import write
    from scripts.verify_measured_execution import verify_execution

    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19 or git_source_state(root)["dirty"]
    ):
        raise ValueError("preserve configurations and require clean nice19 hidden-CUDA execution")
    execution = verify_execution(args.predictive, args.cohort)
    predictive = causal.load(args.predictive)
    original_path = Path(predictive["experiment_configuration"])
    config = deepcopy(causal.load(original_path))
    if predictive["order"] != ["full"]:
        raise ValueError("native traffic must match a completed Predictive arm")
    config["common_environment"] = arm_environment(config["common_environment"], "native_matched")
    config.update(
        order=["native_matched"], status="explicit_same_workload_native_sglang_replay",
        comparison_contract={
            "native_replay_baseline": True, "future_information_used": False,
            "same_fixed_tools_arrivals_and_resources_for_all_arms": True,
            "stop_after_all_fixed_measured_workflows_complete": True,
            "measured_cohort_not_delivered_to_scheduler": True,
            "native_engine_tree": predictive["engine_tree"],
            "upstream_unmodified_source_claimed": False,
        },
        native_reference={
            "repeat_full_native_replay": True,
            "purpose": "user_requested_same_replay_arrivals_tools_and_fixed_measured_cohort",
            "natural_source_JCT_used_as_matched_baseline": False,
        },
        candidate_provenance={
            "same_traffic_completed_predictive": str(args.predictive.resolve()),
            "same_traffic_completed_predictive_sha256": sha256_file(args.predictive),
            "cohort": str(args.cohort.resolve()), "cohort_sha256": sha256_file(args.cohort),
            "selected_workload_evaluation_only": True,
            "policy_receives_no_measurement_membership": True,
        },
        gpu_execution={"registered": False, "requires_fresh_position_and_sampling": True},
        performance_accepted=False,
    )
    config.pop("workflow_work_qualification", None)
    config.pop("oracle_information", None)
    config.pop("historical_candidate_provenance", None)
    if not valid_comparison_order(config):
        raise ValueError("native singleton contract is invalid")
    common = config["common_environment"]
    if (
        common["BELIEFKV_AGENT_ADMISSION"] != "off"
        or common["BELIEFKV_WORKFLOW_WORK_SOURCE"] != "learned"
        or common["BELIEFKV_WORKFLOW_WORK_MODEL"]
        or any(common[name] != "0" for name in (
            "BELIEFKV_ENABLE_PREPARE_HOST", "BELIEFKV_ENABLE_TOOL_PREFETCH",
            "BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD", "BELIEFKV_WORKFLOW_SIZE_RESUME",
            "BELIEFKV_WORKFLOW_COMBINATION_ADMISSION",
        ))
    ):
        raise ValueError("native replay retains Full or future-information scheduling")
    entry = causal.preflight_runtime(common, common["MODEL_PATH"])
    write(args.output_dir / "native.json", config)
    write(args.output_dir / "entry.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "application": git_source_state(root),
        "measured_requests": execution["measured_requests"],
        "measured_outputs": execution["measured_output_tokens"],
        "actual_native_preflight": entry, "all_predictive_owners_exited": True,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.predictive, args.cohort, original_path,
            Path(execution["certificate"]), args.output_dir / "native.json", Path(__file__),
        )},
        "GPU_action": False, "performance_accepted": False,
    })
    print(json.dumps({"configuration": str(args.output_dir / "native.json"),
                      "measured_requests": execution["measured_requests"]}), flush=True)


if __name__ == "__main__":
    main()
