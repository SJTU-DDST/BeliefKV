"""Prepare matched mixed-chunk serving profiles without changing method policies."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write

SHARED_KEYS = (
    "MODEL_PATH", "SERVED_MODEL_NAME", "MODEL_CACHE_LAYOUT", "MODEL_CONTEXT_TOKENS",
    "ROOT_COUNT", "MAX_RUNNING_REQUESTS", "SERVER_RANDOM_SEED", "SAMPLING_SEED",
    "PYTHONHASHSEED", "SAMPLING_PROFILE", "CUDA_HOME", "CHUNKED_PREFILL_SIZE",
    "HICACHE_SIZE_GB", "HICACHE_WRITE_POLICY", "HICACHE_MEM_LAYOUT",
    "WORKLOAD_MANIFEST", "MAX_COMPLETION_TOKENS",
)
ORDER = ("thunderagent_acting_decay", "full")


def prepare(full_path, thunder_path):
    paths = {"full": full_path, "thunderagent_acting_decay": thunder_path}
    bases = {name: json.loads(path.read_bytes()) for name, path in paths.items()}
    full, thunder = bases["full"], bases["thunderagent_acting_decay"]
    for name in ("source_registration", "workflow_arrivals", "tool_timing_overlay"):
        if not full.get(name) or full[name] != thunder.get(name):
            raise ValueError("mixed comparison requires identical recorded work, arrivals and tools")
    for name in SHARED_KEYS:
        if name not in full["common_environment"] or (
            full["common_environment"][name] != thunder["common_environment"].get(name)
        ):
            raise ValueError("mixed comparison shared serving parameters differ: " + name)
    result = {}
    for name in ORDER:
        base = bases[name]
        if base["order"] != [name] or base["common_environment"].get("ENABLE_MIXED_CHUNK") != "0":
            raise ValueError("expected the declared complete mixed-off method configuration")
        if any(base["common_environment"].get(field, "0") != "0" for field in (
            "TOOL_PREFETCH", "BELIEFKV_ENABLE_TOOL_PREFETCH",
            "EXECUTION_FRONTIER_RESTORE", "BELIEFKV_ENABLE_FRONTIER_RESTORE",
            "LAYER_HANDOFF", "BELIEFKV_LAYER_HANDOFF", "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT",
        )):
            raise ValueError("mixed serving must retain the user-disabled features")
        candidate = deepcopy(base)
        candidate["common_environment"]["ENABLE_MIXED_CHUNK"] = "1"
        effective = (
            arm_environment(candidate["common_environment"], "full")
            if name == "full" else paper_environment(candidate["common_environment"], name)
        )
        if any(effective.get(key, "0") != "0" for key in (
            "BELIEFKV_ENABLE_TOOL_PREFETCH", "BELIEFKV_ENABLE_FRONTIER_RESTORE",
            "BELIEFKV_LAYER_HANDOFF",
        )):
            raise ValueError("mixed serving must retain disabled tool/frontier prefetch and handoff")
        candidate.update(
            status="mixed_chunk_requires_cpu_and_fresh_gpu_qualification",
            performance_accepted=False,
            serving_profile_experiment={
                "previous_configuration": str(paths[name].resolve()),
                "previous_configuration_sha256": sha256_file(paths[name]),
                "only_changed_common_environment_field": "ENABLE_MIXED_CHUNK",
                "chunked_prefill_size": int(candidate["common_environment"]["CHUNKED_PREFILL_SIZE"]),
                "enable_mixed_chunk": True,
                "shared_serving_tuning_not_agent_policy_innovation": True,
                "matched_methods": list(ORDER),
                "requires_current_dispatch_full_complete_review": True,
                "requires_actual_mixed_output_conservation": True,
                "fresh_position_token_session_stop_and_sampler_checks_required": True,
                "normal_tails_retained": True, "native_capture_repeated": False,
                "GPU_experiment_started": False, "performance_accepted": False,
            },
        )
        execution = candidate.setdefault("gpu_execution", {})
        execution.pop("complete_control", None)
        execution.update(
            registered=False, requires_matched_mixed_control=True,
            requires_fresh_position_and_sampling=True,
        )
        result[name] = candidate
    return result


def validate_profiles(specifications):
    if set(specifications) != set(ORDER):
        raise ValueError("mixed comparison requires both declared methods")
    paths = {}
    for name in ORDER:
        provenance = specifications[name]["serving_profile_experiment"]
        path = Path(provenance["previous_configuration"])
        if sha256_file(path) != provenance["previous_configuration_sha256"]:
            raise ValueError("mixed comparison base configuration changed")
        paths[name] = path
    if specifications != prepare(paths["full"], paths["thunderagent_acting_decay"]):
        raise ValueError("mixed comparison changed more than the shared serving profile")
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-base", type=Path, required=True)
    parser.add_argument("--thunder-base", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    candidates = prepare(args.full_base, args.thunder_base)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for name, candidate in candidates.items():
        write(args.output_dir / (name + ".json"), candidate)
    print(json.dumps({
        "prepared": list(ORDER), "enable_mixed_chunk": True,
        "GPU_experiment_started": False, "performance_accepted": False,
    }))


if __name__ == "__main__":
    main()
