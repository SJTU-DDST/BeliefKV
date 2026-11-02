"""Prepare a common execution-profile comparison while retaining each method policy."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments import native_execution
from beliefkv.experiments.source_provenance import sha256_file
from scripts.prepare_mixed_chunk_comparison import SHARED_KEYS
from scripts.train_workflow_work import write

ORDER = ("full", "thunderagent_acting_decay")


def prepare(
    full_path, thunder_path, profile_path, cohort_path, *, checkout=ROOT,
    replace_existing=False,
):
    paths = dict(zip(ORDER, (full_path, thunder_path), strict=True))
    bases = {name: json.loads(path.read_bytes()) for name, path in paths.items()}
    full, thunder = (bases[name] for name in ORDER)
    for key in ("source_registration", "workflow_arrivals", "tool_timing_overlay"):
        if not full.get(key) or full[key] != thunder.get(key):
            raise ValueError("common execution comparison must retain identical work, arrivals and tools")
    for key in SHARED_KEYS:
        if key not in full["common_environment"] or (
            full["common_environment"][key] != thunder["common_environment"].get(key)
        ):
            raise ValueError("common execution comparison serving parameters differ: " + key)
    relative = profile_path.resolve().relative_to(checkout.resolve())
    digest, result = sha256_file(profile_path), {}
    previous = {
        name: native_execution.configuration(base["common_environment"], checkout)
        for name, base in bases.items()
    }
    if any(previous.values()):
        if not replace_existing:
            raise ValueError("base already declares a native execution experiment")
        if not all(previous.values()) or (
            previous[ORDER[0]]["identity"] != previous[ORDER[1]]["identity"]
        ):
            raise ValueError("replacing execution requires the same qualified previous profile")
    elif replace_existing:
        raise ValueError("no existing execution profile to replace")
    for name in ORDER:
        base = bases[name]
        if base["order"] != [name] or any(
            base["common_environment"].get(key, "0") != "0" for key in (
                "ENABLE_MIXED_CHUNK", "TOOL_PREFETCH", "BELIEFKV_ENABLE_TOOL_PREFETCH",
                "EXECUTION_FRONTIER_RESTORE", "BELIEFKV_ENABLE_FRONTIER_RESTORE",
                "LAYER_HANDOFF", "BELIEFKV_LAYER_HANDOFF",
            )
        ):
            raise ValueError("retain the declared method and disabled tool prefetch/handoff")
        candidate = deepcopy(base)
        common = candidate["common_environment"]
        common.update({
            native_execution.CONFIG_KEY: str(relative),
            native_execution.HASH_KEY: digest,
        })
        selected = native_execution.configuration(common, checkout)
        candidate.update(
            status="common_native_execution_requires_bound_CPU_and_fresh_GPU_checks",
            performance_accepted=False,
            serving_profile_experiment={
                "previous_configuration": str(paths[name].resolve()),
                "previous_configuration_sha256": sha256_file(paths[name]),
                "previous_native_execution_configuration": (
                    previous[name]["identity"] if previous[name] else None
                ),
                "only_changed_common_environment_fields": [
                    native_execution.CONFIG_KEY, native_execution.HASH_KEY,
                ],
                "native_execution_configuration": selected["identity"],
                "common_engine_optimization_available_to_all_methods": True,
                "agent_policy_innovation_claimed": False,
                "same_actual_Device_and_Host_capacity_required": True,
                "fresh_position_token_session_stop_and_sampler_checks_required": True,
                "measured_stop_policy_unchanged": True,
                "GPU_experiment_started": False,
            },
            private_analysis_cohort={
                "path": str(cohort_path.resolve()), "sha256": sha256_file(cohort_path),
                "delivered_to_scheduler": False,
            },
        )
        candidate.setdefault("gpu_execution", {}).update(
            registered=False, requires_fresh_position_and_sampling=True,
        )
        result[name] = candidate
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-base", type=Path, required=True)
    parser.add_argument("--thunder-base", type=Path, required=True)
    parser.add_argument("--execution-profile", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--replace-existing-execution", action="store_true")
    args = parser.parse_args()
    candidates = prepare(
        args.full_base, args.thunder_base, args.execution_profile, args.cohort,
        replace_existing=args.replace_existing_execution,
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for name, candidate in candidates.items():
        write(args.output_dir / (name + ".json"), candidate)
    print(json.dumps({"prepared": list(ORDER), "GPU_experiment_started": False}))


if __name__ == "__main__":
    main()
