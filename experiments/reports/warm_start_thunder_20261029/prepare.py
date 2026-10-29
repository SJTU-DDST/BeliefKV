"""Prepare one immutable warm-start arrival contract and ThunderAgent control."""

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
    reference = ROOT.parent / "ops/v345_locobench_categorized_thunder_decay_20261027.registration.json"
    control = json.loads(reference.read_bytes())
    identities = control["workflow_ids"]
    if identities != sorted(set(identities)) or len(identities) != 18:
        raise ValueError("warm-start baseline requires the unchanged complete 18-workflow population")
    HERE.mkdir(parents=True, exist_ok=True)
    arrivals_path = HERE / "arrivals.json"
    arrivals = poisson_arrival_plan(
        identities, rate_per_second=1 / 60, seed=21, initial_workflows=10,
    )
    validate_arrival_plan(arrivals, identities)
    write(arrivals_path, arrivals)
    source = ROOT / "configs/baselines/kvflow_locobench_v230_v311_20261028.json"
    configuration = deepcopy(json.loads(source.read_bytes()))
    configuration.update(
        status="warm_start_thunderagent_cpu_qualification_pending",
        order=["thunderagent_acting_decay"],
        workflow_arrivals={"path": str(arrivals_path), "sha256": sha256_file(arrivals_path)},
        execution_prerequisites={
            "user_authorized_thunderagent_before_predictive_optimization_completion": True,
            "agentix_reproduction_cancelled_without_verified_author_code": True,
            "tokencake_reproduction_cancelled": True,
            "no_other_gpu_experiment_concurrent": True,
        },
    )
    configuration["common_environment"].update(CHUNKED_PREFILL_SIZE="4096", ENABLE_MIXED_CHUNK="0")
    configuration["comparison_contract"].update(
        only_changed_workload_dimension="external_workflow_arrival_schedule",
        initial_workflows=10, total_workflows=18,
        subsequent_mean_interarrival_seconds=60, arrival_seed=21,
        paper_policy="thunderagent_public_single_backend_acting_decay",
        fresh_source_bound_prefix_qualification_before_model=False,
        unchanged_common_native_model_and_sampling_proofs_reused=True,
        burst_controls_are_not_matched_arrival_controls=True,
    )
    write(HERE / "thunderagent.json", configuration)
    write(HERE / "preparation.json", {
        "source_registration": str(reference), "source_sha256": sha256_file(reference),
        "configuration_source": str(source), "configuration_source_sha256": sha256_file(source),
        "arrival_schedule_sha256": arrivals["arrival_schedule_sha256"],
        "last_arrival_seconds": arrivals["arrival_schedule"][-1]["offset_seconds"],
        "all_original_workflows_and_tails_retained": True,
        "native_capture_repeated": False, "GPU_experiment_started": False,
        "performance_accepted": False,
    })
    print(json.dumps({
        "workflows": 18, "initial": 10, "subsequent_mean_interarrival_seconds": 60,
        "last_arrival_seconds": arrivals["arrival_schedule"][-1]["offset_seconds"],
        "arrival_schedule_sha256": arrivals["arrival_schedule_sha256"],
    }))


if __name__ == "__main__":
    main()
