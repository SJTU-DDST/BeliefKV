"""Freeze the requested 120-second arrival comparison without changing work."""

from copy import deepcopy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.replay_arrivals import poisson_arrival_plan, validate_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def main():
    reference = ROOT.parent / "ops/warm_start_thunder_20261029_r1.registration.json"
    previous = json.loads(reference.read_bytes())
    identities = previous["workflow_ids"]
    if identities != sorted(set(identities)) or len(identities) != 18:
        raise ValueError("retain the entire eighteen-project population")
    arrivals = poisson_arrival_plan(
        identities, rate_per_second=1 / 120, seed=21, initial_workflows=10,
    )
    validate_arrival_plan(arrivals, identities)
    old = previous["workflow_arrivals"]
    if any(
        new["workflow_id"] != prior["workflow_id"]
        or abs(new["offset_seconds"] - 2 * prior["offset_seconds"]) > 1e-9
        for new, prior in zip(arrivals["arrival_schedule"], old["arrival_schedule"], strict=True)
    ):
        raise ValueError("only the exponential interarrival scale may change")
    arrival_path = HERE / "arrivals.json"
    write(arrival_path, arrivals)
    sources = {
        "thunderagent_acting_decay": ROOT / "experiments/reports/warm_start_thunder_20261029/thunderagent.json",
        "full": ROOT / "experiments/reports/layer_free_extent_20261029/full_warm_start.json",
    }
    for label, source in sources.items():
        configuration = deepcopy(json.loads(source.read_bytes()))
        configuration.update(
            status="mean120_arrivals_cpu_qualification_pending",
            workflow_arrivals={"path": str(arrival_path), "sha256": sha256_file(arrival_path)},
            arrival_experiment={
                "previous_configuration": str(source),
                "previous_configuration_sha256": sha256_file(source),
                "only_changed_workload_dimension": "external_workflow_arrival_schedule",
                "initial_workflows": 10, "total_workflows": 18,
                "subsequent_mean_interarrival_seconds": 120, "arrival_seed": 21,
                "matched_methods": ["thunderagent_acting_decay", "full"],
                "native_capture_repeated": False,
                "GPU_experiment_started": False, "performance_accepted": False,
            },
        )
        if label == "thunderagent_acting_decay":
            configuration["comparison_contract"]["subsequent_mean_interarrival_seconds"] = 120
        else:
            configuration["gpu_execution"].update(
                registered=False,
                complete_control=str(ROOT.parent / "ops/arrival_mean120_thunder_20261029_r1.registration.json"),
                requires_fresh_free_extent_components=False,
                requires_unforced_free_extent_model_restore=False,
                requires_fresh_position_and_sampling=False,
                reuse_only_exact_unchanged_qualified_native_and_data_plane=True,
            )
            configuration["candidate_provenance"]["arrival_schedule_sha256"] = arrivals["arrival_schedule_sha256"]
        write(HERE / (label + ".json"), configuration)
    offsets = [row["offset_seconds"] for row in arrivals["arrival_schedule"]]
    write(HERE / "preparation.json", {
        "reference": str(reference), "reference_sha256": sha256_file(reference),
        "previous_configurations_sha256": {str(path): sha256_file(path) for path in sources.values()},
        "arrival_schedule_sha256": arrivals["arrival_schedule_sha256"],
        "last_arrival_seconds": offsets[-1],
        "maximum_interarrival_seconds": max(b - a for a, b in zip(offsets, offsets[1:])),
        "same_requests_tokens_dependencies_tools_native_stops_and_normal_tails": True,
        "method_specific_policy_and_native_engine_unchanged": True,
        "GPU_experiment_started": False, "performance_accepted": False,
    })
    print(json.dumps({
        "initial_workflows": 10, "total_workflows": 18,
        "mean_interarrival_seconds": 120, "last_arrival_seconds": offsets[-1],
        "arrival_schedule_sha256": arrivals["arrival_schedule_sha256"],
    }))


if __name__ == "__main__":
    main()
