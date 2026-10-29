"""Select rate 0.5 after both baselines; retain the prior CPU-bound candidates."""

from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.replay_arrivals import load_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
NAMES = ("chunk2048", "chunk1024", "chunk2048_mixed")


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def main():
    outputs = [HERE / (name + ".json") for name in NAMES] + [HERE / "active_plan.json"]
    if any(path.exists() for path in outputs):
        raise FileExistsError("preserve previously prepared experiment plans")
    arrival_path = PARENT / "poisson_0.5_seed21.json"
    raw = json.loads(arrival_path.read_bytes())
    arrivals = load_arrival_plan(
        arrival_path, [row["workflow_id"] for row in raw["arrival_schedule"]],
    )
    if arrivals["rate_per_second"] != .5 or arrivals["seed"] != 21:
        raise ValueError("only the user-selected rate 0.5 and seed21 are active")
    requirements = {
        "complete_before_gpu_execution": ["TokenCake", "Agentix"],
        "completion_boundary": "complete_formal_workload_and_actual_owned_exit",
        "component_qualification_is_not_formal_completion": True,
    }
    parent_hashes = {}
    for name in NAMES:
        source = PARENT / (name + ".json")
        parent_hashes[str(source)] = sha256_file(source)
        candidate = deepcopy(json.loads(source.read_bytes()))
        candidate.update(
            status="waiting_for_tokencake_and_agentix_completion",
            workflow_arrivals={"path": str(arrival_path), "sha256": sha256_file(arrival_path)},
            execution_prerequisites=requirements,
            supersedes_prepared_candidate={
                "path": str(source), "sha256": parent_hashes[str(source)],
            },
        )
        write(HERE / (name + ".json"), candidate)
    if any(sha256_file(Path(path)) != digest for path, digest in parent_hashes.items()):
        raise ValueError("historical CPU-bound candidates changed")
    write(HERE / "active_plan.json", {
        "schema_version": 1, "status": "waiting_for_baseline_completion",
        "arrival_rate_per_second": .5, "arrival_seed": 21,
        "workflow_arrivals": {"path": str(arrival_path), "sha256": sha256_file(arrival_path)},
        "deferred_arrival_rates_per_second": [.2, .35],
        "execution_prerequisites": requirements,
        "first_candidate": str(HERE / "chunk2048.json"),
        "conditional_followups": [str(HERE / (name + ".json")) for name in NAMES[1:]],
        "gpu_experiments_registered": False,
        "historical_cpu_bound_candidates_unchanged": parent_hashes,
        "fresh_gpu_parameter_and_model_checks_required": True,
    })
    print(json.dumps({
        "active_arrival_rate": .5, "deferred_arrival_rates": [.2, .35],
        "wait_for_complete_experiments": requirements["complete_before_gpu_execution"],
        "candidates": list(NAMES), "gpu_experiments_registered": False,
    }))


if __name__ == "__main__":
    main()
