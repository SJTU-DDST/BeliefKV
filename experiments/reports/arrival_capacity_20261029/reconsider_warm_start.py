"""Reconsider a finite warm-start trial using complete observed agent lifetimes."""

import json
import math
from pathlib import Path
import random
import statistics
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file


def describe(values):
    ordered = sorted(values)
    return {
        "count": len(ordered), "mean": statistics.mean(ordered),
        **{f"p{percent}": ordered[math.ceil(percent / 100 * len(ordered)) - 1]
           for percent in (25, 50, 75, 95)},
        "maximum": ordered[-1],
    }


def main():
    output = HERE / "warm_start_reconsideration.json"
    if output.exists():
        raise FileExistsError("preserve the prior arrival-size reasoning")
    source = ROOT / "experiments/reports/tool_offload_before_admission_20261027/recent_memory_comparison_20261029.json"
    prior = json.loads(source.read_bytes())
    inputs = {str(source): sha256_file(source)}
    observations, populations = {}, []
    for label in ("ACK repair Full", "ThunderAgent decay"):
        original = prior["arms"][label]
        events_path = Path(original["arm"]) / "replay/runtime_events.replay.jsonl"
        before = sha256_file(events_path)
        expected = prior["input_jsonl_prefixes"][str(events_path)]["complete_prefix_sha256"]
        if before != expected:
            raise ValueError("complete observed event stream changed")
        rows = [json.loads(line) for line in events_path.read_bytes().splitlines()]
        starts = {row["workflow_id"]: row["ts_ms"] for row in rows if row["kind"] == "workflow_start"}
        finishes = [row for row in rows if row["kind"] == "workflow_end"]
        creates = {row["invocation_id"]: row for row in rows if row["kind"] == "invocation_create"}
        returns = {row["invocation_id"]: row["ts_ms"] for row in rows if row["kind"] == "return"}
        children = [key for key, row in creates.items() if row.get("relation_type") != "root"]
        if (
            len(starts) != len(finishes) or len(starts) != 18 or len(children) != 92
            or set(creates) != set(returns)
            or sum(row["kind"] == "llm_submit" for row in rows) != 3674
            or before != sha256_file(events_path)
        ):
            raise ValueError("require the exact complete unfiltered workload")
        elapsed = [(returns[key] - creates[key]["ts_ms"]) / 1000 for key in children]
        observations[label] = {
            "child_create_to_return_seconds": describe(elapsed),
            "children_returning_within_120_seconds": sum(value < 120 for value in elapsed),
            "first_workflow_completion_seconds": min(
                row["ts_ms"] - min(starts.values()) for row in finishes
            ) / 1000,
            "finite_burst_completions_per_minute": 18 / original["workflow_makespan_s"] * 60,
        }
        populations.append(sorted(starts))
        inputs[str(events_path)] = before
    if populations[0] != populations[1]:
        raise ValueError("baseline workflow identities differ")
    arrivals = []
    for mean_gap in (120, 60):
        generator, offset = random.Random(21), 0.
        intervals = [generator.expovariate(1 / mean_gap) for _ in range(8)]
        for gap in intervals:
            offset += gap
        arrivals.append({
            "total_workflows": 18, "initial_workflows": 10,
            "subsequent_workflows": 8, "seed": 21,
            "mean_interarrival_seconds": mean_gap,
            "expected_last_arrival_seconds": 8 * mean_gap,
            "seeded_last_arrival_seconds": offset,
            "seeded_longest_gap_seconds": max(intervals),
        })
    evidence = {
        "scope": "Offline observed evidence and finite-trial advice, not GPU or queue simulation.",
        "observations": observations, "arrival_sizing": arrivals,
        "recommended_short_trial": {
            "total_workflows": 18, "initial_workflows": 10,
            "subsequent_mean_interarrival_seconds": 60, "arrival_seed": 21,
            "running_decode_limit": 24,
            "requires_mixed_arrival_contract_implementation": True,
            "not_a_registered_gpu_experiment": True,
        },
        "limitations": [
            "Observed lifetimes include scheduler waiting and change with arrivals.",
            "Finishing a child does not finish its workflow or prove useful KV pressure disappeared.",
            "The finite burst completion rate is not stationary capacity.",
            "All 18 arrivals finish within about 7.2 minutes; this does not establish steady load.",
            "Assess useful Host hits, actual transfers, old-input recomputation and ready work jointly.",
            "Do not resize the workload or fabricate additional distinct-project traces here.",
        ],
        "inputs_sha256": inputs, "operator_sha256": sha256_file(Path(__file__)),
        "GPU_experiment_started": False, "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "observations": observations, "arrival_sizing": arrivals,
        "recommended_short_trial": evidence["recommended_short_trial"],
    }, indent=2))


if __name__ == "__main__":
    main()
