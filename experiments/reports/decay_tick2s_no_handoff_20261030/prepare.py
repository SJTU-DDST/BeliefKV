"""Declare the user-selected Full tick candidate with physical handoff disabled."""

from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.layer_free_extent_20261029.run_bound_cpu import write
from scripts import run_causal_comparison as causal

BASE = ROOT / "experiments/reports/prefill2048_mean120_20261029/full.json"
CONFIGURATION = ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"
CONTROL = ROOT.parent / "ops/prefill2048_mean120_thunder_20261029_r1.registration.json"


def main():
    base = causal.load(BASE)
    candidate = deepcopy(base)
    candidate["status"] = "two_second_full_control_cpu_and_position_qualification_pending"
    candidate["common_environment"].update({
        "AGENT_CONTROL_INTERVAL_MS": "2000", "BELIEFKV_AGENT_CONTROL_INTERVAL_MS": "2000",
        "LAYER_HANDOFF": "0", "BELIEFKV_LAYER_HANDOFF": "0",
        "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT": "0",
        "BELIEFKV_LAYER_HANDOFF_D2H_LAYERS": "1",
        "BELIEFKV_LAYER_HANDOFF_H2D_LAYERS": "1",
    })
    effective = arm_environment(candidate["common_environment"], "full")
    if any(effective.get(name) != value for name, value in {
        "BELIEFKV_AGENT_CONTROL_INTERVAL_MS": "2000",
        "BELIEFKV_LAYER_HANDOFF": "0",
        "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT": "0",
        "BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS": "0",
        "BELIEFKV_ENABLE_TOOL_PREFETCH": "0",
        "BELIEFKV_DEMAND_PREFILL_PIPELINE": "1",
        "BELIEFKV_AGENT_JOIN_BLOCKED_HANDOFF": "1",
    }.items()):
        raise ValueError("effective Full policy differs from the declared tick/no-handoff candidate")
    candidate["comparison_contract"].update({
        "reference_control_interval_ms": 2000,
        "acting_decay_half_life_s": 1,
        "reference_paper_control_interval_ms": 5000,
        "layer_handoff": False,
        "d2h_group_layers": 1, "h2d_group_layers": 1,
        "group_submission": "disabled",
        "single_layer_transaction_inflight": False,
        "native_handoff_free_extent": "disabled",
        "native_gpu_group_component_and_model_proofs_required": False,
    })
    candidate["candidate_provenance"] = {
        "base": str(BASE), "base_sha256": sha256_file(BASE),
        "user_selected_physical_layer_handoff_disabled": True,
        "full_control_interval_ms": 2000,
        "acting_decay_formula_and_one_second_half_life_unchanged": True,
        "same_complete_work_tools_arrivals_capacity_seeds_and_prefill_profile": True,
        "running_paper_control_registration": str(CONTROL),
        "paper_control_keeps_author_five_second_period": True,
        "performance_accepted": False,
    }
    candidate["gpu_execution"].update(
        complete_control=str(CONTROL),
        requires_fresh_position_and_sampling=True,
        reuse_only_exact_unchanged_qualified_native_and_data_plane=False,
    )
    write(CONFIGURATION, candidate)
    write(HERE / "preparation.json", {
        "configuration": str(CONFIGURATION), "sha256": sha256_file(CONFIGURATION),
        "base": str(BASE), "base_sha256": sha256_file(BASE),
        "supersedes_unregistered_full_only": True,
        "running_control_changed": False, "GPU_experiment_started": False,
    })
    print(CONFIGURATION)


if __name__ == "__main__":
    main()
