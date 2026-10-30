"""Declare one pressure-policy change on retained complete-work Full."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from scripts.run_causal_comparison import SERVER_FIELDS, preflight_runtime
from scripts.train_workflow_work import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    base = ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"
    original = json.loads(base.read_bytes())
    candidate = deepcopy(original)
    common = candidate["common_environment"]
    common.update({
        "AGENT_PRESSURE_PAUSE_POLICY": "predicted_acting",
        "BELIEFKV_AGENT_PRESSURE_PAUSE_POLICY": "predicted_acting",
    })
    allowed = {"AGENT_PRESSURE_PAUSE_POLICY", "BELIEFKV_AGENT_PRESSURE_PAUSE_POLICY"}
    if {key: value for key, value in common.items() if key not in allowed} != {
        key: value for key, value in original["common_environment"].items() if key not in allowed
    }:
        raise ValueError("pressure candidate changed another serving or workload setting")
    for name in ("TOOL_PREFETCH", "EXECUTION_FRONTIER_RESTORE", "LAYER_HANDOFF",
                 "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT", "ENABLE_MIXED_CHUNK"):
        if common.get(name, "0") != "0":
            raise ValueError("retain user-disabled prefetch/handoff and original mixed-off")
    candidate.update({
        "status": "predicted_acting_pressure_requires_source_bound_cpu_and_gpu_qualification",
        "candidate_provenance": {
            "base": str(base.resolve()), "base_sha256": sha256_file(base),
            "only_changed_policy": "mandatory_acting_pressure_victim_preference",
            "new_timer": False, "new_predictor": False,
            "paper_working_set_core_changed": False,
            "native_engine_changed": False,
        },
        "gpu_execution": {
            "registered": False, "requires_fresh_position_and_sampling": True,
            "requires_complete_preceding_full_review": True,
        },
        "performance_accepted": False,
    })
    candidate["comparison_contract"].update({
        "pressure_pause_policy": "predicted_acting",
        "pressure_prediction_fallback": "reference_smallest_context",
        "pressure_prediction_scope": "mandatory_acting_pressure_only",
        "pressure_prediction_horizon": "configured_control_period_plus_full_owner_restore_P90",
        "pressure_prediction_uses_future_tool_duration": False,
        "pressure_prediction_queries_per_owner_per_control": 1,
        "pressure_prediction_new_physical_authority": False,
        "workflow_size_resume": False,
    })
    preflights = {}
    for mode in ("full", "scheduler_only", "native_matched"):
        settings = arm_environment(common, mode)
        for name in SERVER_FIELDS:
            if name in settings:
                settings["BELIEFKV_" + name] = settings[name]
        try:
            preflights[mode] = preflight_runtime(settings, common["MODEL_PATH"])
        except Exception as error:
            write(args.output_dir / "preflight_failure.json", {
                "mode": mode, "error": repr(error),
                "stdout": getattr(error, "stdout", ""),
                "stderr": getattr(error, "stderr", ""),
                "GPU_experiment_started": False,
            })
            raise
        if mode != "full" and settings["BELIEFKV_AGENT_PRESSURE_PAUSE_POLICY"] != "reference":
            raise ValueError("predictive pressure leaked into a control")
    write(args.output_dir / "full.json", candidate)
    write(args.output_dir / "preparation.json", {
        "source_base": {"path": str(base), "sha256": sha256_file(base)},
        "constructor_preflights": preflights,
        "configuration": {
            "path": str((args.output_dir / "full.json").resolve()),
            "sha256": sha256_file(args.output_dir / "full.json"),
        },
        "GPU_experiment_started": False, "performance_accepted": False,
    })
    print(json.dumps({"preflights": preflights, "GPU_experiment_started": False}))


if __name__ == "__main__":
    main()
