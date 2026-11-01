"""Prepare one cadence-only candidate on complete matched replay traffic."""

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
    from beliefkv.experiments.source_provenance import sha256_file
    from scripts import run_causal_comparison as causal
    from scripts.train_workflow_work import write
    from scripts.verify_measured_execution import verify_execution

    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve candidates and keep nice19 CPU-only preparation")
    completed = verify_execution(args.predictive, args.cohort)
    control = causal.load(args.predictive)
    base_path = Path(control["experiment_configuration"])
    base = causal.load(base_path)
    if control["order"] != ["full"] or base["common_environment"].get("ENABLE_MIXED_CHUNK", "0") != "0":
        raise ValueError("cadence candidate needs a completed nonmixed Full control")
    candidate = deepcopy(base)
    candidate["common_environment"]["PREFILL_DECODE_INTERVAL"] = "1"
    old = causal.prefill_server_parameters({}, base["common_environment"])
    new = causal.prefill_server_parameters({}, candidate["common_environment"])
    if {**old, "prefill_decode_interval": 1} != new:
        raise ValueError("cadence candidate changed another native prefill control")
    candidate.update(
        status="CPU_only_native_cadence_after_measured_burst_opportunity",
        candidate_provenance={
            "completed_control_registration": str(args.predictive.resolve()),
            "completed_control_registration_sha256": sha256_file(args.predictive),
            "only_native_prefill_decode_interval_changed": True,
            "new_agent_scheduling_innovation_claimed": False,
            "work_tokens_tool_times_arrivals_and_KV_resources_unchanged": True,
        },
        gpu_execution={"registered": False, "requires_fresh_position_and_sampling": True,
                       "requires_final_Oracle_and_selected_native_completion": True},
        performance_accepted=False,
    )
    candidate["comparison_contract"].update(
        native_prefill_decode_interval=1,
        unchanged_active_agent_and_KV_policy=True,
        no_mixed_prefill_decode=True,
        no_forced_ready_agent_preemption=True,
        no_new_future_information=True,
        common_engine_tuning_only=True,
    )
    settings = arm_environment(candidate["common_environment"], "full")
    for name in causal.SERVER_FIELDS:
        if name in settings:
            settings["BELIEFKV_" + name] = settings[name]
    entry = causal.preflight_runtime(settings, settings["MODEL_PATH"])
    path = args.output_dir / "full.json"
    write(path, candidate)
    write(args.output_dir / "entry.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "measured_requests": completed["measured_requests"],
        "measured_outputs": completed["measured_output_tokens"],
        "actual_Full_runtime_preflight": entry,
        "old_native_prefill_parameters": old, "new_native_prefill_parameters": new,
        "inputs_sha256": {str(item.resolve()): sha256_file(item) for item in (
            args.predictive, args.cohort, base_path, Path(completed["certificate"]), path, Path(__file__),
        )},
        "GPU_action": False, "performance_accepted": False,
    })
    print(json.dumps({"configuration": str(path), "changed_prefill_control": {
        "prefill_decode_interval": [old.get("prefill_decode_interval", 0), 1],
    }}), flush=True)


if __name__ == "__main__":
    main()
