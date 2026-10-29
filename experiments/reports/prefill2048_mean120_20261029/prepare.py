"""Prepare one matched 2048-token profile without changing recorded work."""

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

ORDER = ("thunderagent_acting_decay", "full")
BASES = {
    label: ROOT / "experiments/reports/arrival_mean120_20261029" / (label + ".json")
    for label in ORDER
}


def validate_profiles(specifications, bases):
    if set(specifications) != set(ORDER) or set(bases) != set(ORDER):
        raise ValueError("require exactly one matched ThunderAgent/Full profile")
    for label in ORDER:
        specification, base = specifications[label], bases[label]
        expected = {
            **base["common_environment"], "CHUNKED_PREFILL_SIZE": "2048", "ENABLE_MIXED_CHUNK": "0",
        }
        if (
            specification["order"] != [label]
            or specification["common_environment"] != expected
            or specification["workflow_arrivals"] != base["workflow_arrivals"]
            or specification.get("tool_timing_overlay") != base.get("tool_timing_overlay")
        ):
            raise ValueError("change only the declared prefill profile, preserving method and work")
        effective = (
            causal.paper_environment(expected, label) if label in causal.PAPER_CONTROLS
            else arm_environment(expected, label)
        )
        if (
            effective.get("BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS", "0") != "0"
            or effective.get("BELIEFKV_ENABLE_TOOL_PREFETCH") != "0"
            or effective.get("BELIEFKV_ENABLE_EXECUTION_FRONTIER_RESTORE", "0") != "0"
        ):
            raise ValueError("do not promote dispatch or re-enable speculative prefetch")
    arrivals = specifications["full"]["workflow_arrivals"]
    if (
        arrivals != specifications["thunderagent_acting_decay"]["workflow_arrivals"]
        or sha256_file(Path(arrivals["path"])) != arrivals["sha256"]
    ):
        raise ValueError("both methods must retain the same complete mean120 arrivals")


def main():
    bases = {label: causal.load(path) for label, path in BASES.items()}
    specifications = {}
    for label, base in bases.items():
        specification = deepcopy(base)
        specification["status"] = "prefill2048_cpu_and_fresh_position_qualification_pending"
        specification["common_environment"].update(
            CHUNKED_PREFILL_SIZE="2048", ENABLE_MIXED_CHUNK="0",
        )
        specification["serving_profile_experiment"] = {
            "previous_configuration": str(BASES[label]),
            "previous_configuration_sha256": sha256_file(BASES[label]),
            "chunked_prefill_size": 2048,
            "enable_mixed_chunk": False,
            "shared_serving_tuning_not_agent_policy_innovation": True,
            "dispatch_interval_ms": 0,
            "fresh_position_token_session_stop_and_sampler_checks_required": True,
            "normal_tails_retained": True,
            "native_capture_repeated": False,
            "performance_accepted": False,
        }
        if label == "full":
            specification["gpu_execution"].update(
                complete_control=str(
                    ROOT.parent / "ops/prefill2048_mean120_thunder_20261029_r1.registration.json"
                ),
                requires_fresh_position_and_sampling=True,
                reuse_only_exact_unchanged_qualified_native_and_data_plane=False,
            )
        specifications[label] = specification
    validate_profiles(specifications, bases)
    for label, specification in specifications.items():
        write(HERE / (label + ".json"), specification)
    print({"prepared": list(ORDER), "chunked_prefill_size": 2048, "GPU_experiment_started": False})


if __name__ == "__main__":
    main()
