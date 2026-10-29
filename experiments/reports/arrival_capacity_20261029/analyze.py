"""Offline load sizing from complete work and allocator samples, not a queue simulator."""

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.replay_arrivals import poisson_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file


def main():
    output = HERE / "summary.json"
    if output.exists():
        raise FileExistsError("preserve the original load estimate")
    source = ROOT / "experiments/reports/tool_offload_before_admission_20261027/recent_memory_comparison_20261029.json"
    evidence = json.loads(source.read_bytes())
    inputs = {str(source): sha256_file(source)}
    arms = {}
    for name in ("ACK repair Full", "ThunderAgent decay"):
        arm = evidence["arms"][name]
        events = Path(arm["arm"]) / "replay/runtime_events.replay.jsonl"
        raw = events.read_bytes()
        if sha256_file(events) != evidence["input_jsonl_prefixes"][str(events)]["complete_prefix_sha256"]:
            raise ValueError("completed source events changed")
        inputs[str(events)] = sha256_file(events)
        rows = [json.loads(line) for line in raw.splitlines()]
        starts = sorted(row["ts_ms"] for row in rows if row["kind"] == "workflow_start")
        ends = sorted(row["ts_ms"] for row in rows if row["kind"] == "workflow_end")
        if len(starts) != 18 or len(ends) != 18:
            raise ValueError("require the complete unfiltered 18-workflow population")
        anchor = starts[0]
        end = arm["workflow_makespan_s"] * 1000.
        boundaries = sorted({0., end, *(value - anchor for value in starts + ends)})
        buckets = defaultdict(lambda: [0., 0., 0., 0.])
        # Split sample-held intervals at every workflow boundary before grouping.
        for sample, successor in zip(arm["samples"], arm["samples"][1:]):
            left, right = max(300000., sample["t_ms"]), min(end, successor["t_ms"])
            if right <= left:
                continue
            points = [left, *(value for value in boundaries if left < value < right), right]
            for lo, hi in zip(points, points[1:]):
                at = anchor + (lo + hi) / 2.
                count = bisect_right(starts, at) - bisect_right(ends, at)
                weight = (hi - lo) / 1000.
                bucket = buckets[count]
                bucket[0] += weight
                for index, field in enumerate((
                    "resident_units", "non_evictable_units", "evictable_units",
                ), 1):
                    bucket[index] += weight * sample[field] / arm["device_full_capacity_tokens"]
        grouped = {
            count: {
                "duration_s": row[0],
                "resident_fraction": row[1] / row[0],
                "non_evictable_fraction": row[2] / row[0],
                "evictable_fraction": row[3] / row[0],
            } for count, row in sorted(buckets.items(), reverse=True) if row[0] > 0
        }
        arms[name] = {
            "workflows": len(ends), "requests": evidence["requests"],
            "output_tokens": evidence["output_tokens"],
            "makespan_s": arm["workflow_makespan_s"],
            "mean_jct_s": arm["mean_workflow_jct_s"],
            "finite_burst_completion_rate_per_s": len(ends) / arm["workflow_makespan_s"],
            "output_tokens_per_workflow": evidence["output_tokens"] / len(ends),
            "device_full_capacity_bytes": arm["device_full_capacity_bytes"],
            "by_unfinished_workflows_after_300_s": grouped,
        }
    hypothetical = []
    for rate, count in ((.5, 18), (.008, 18), (.008, 36), (.008, 48), (.01, 48)):
        # Placeholder labels only; these do not authorize duplicate-project work.
        plan = poisson_arrival_plan(
            [f"unbound-project-{index}" for index in range(count)],
            rate_per_second=rate, seed=21,
        )
        offsets = [row["offset_seconds"] for row in plan["arrival_schedule"]]
        hypothetical.append({
            "rate_per_s": rate, "count": count, "seed": 21,
            "expected_interarrival_s": 1. / rate,
            "expected_first_to_last_s": (count - 1) / rate,
            "seeded_first_s": offsets[0], "seeded_last_s": offsets[-1],
            "seeded_first_to_last_s": offsets[-1] - offsets[0],
            "little_law_population_proxy_using_burst_jct": rate * arms["ACK repair Full"]["mean_jct_s"],
            "bound_to_actual_workload": False,
        })
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Offline sizing, no GPU job and no predicted JCT/performance claim.",
        "arms": arms, "hypothetical_arrivals": hypothetical,
        "recommended_initial_rate_per_s": .008,
        "recommended_total_distinct_projects": 48,
        "smaller_calibration_distinct_projects": 36,
        "target_unfinished_workflows_observation_only": [12, 18],
        "limitations": [
            "Burst completion rate is an initial operating-point estimate, not measured stationary capacity.",
            "Burst JCT changes under Poisson arrivals; lambda*burst_JCT is only a sizing proxy.",
            "Unfinished workflows include external blocking; non-evictable is not pure compute-active KV.",
            "Allocator occupancy includes retired cache; neither resident nor evictable proves useful cold migration.",
            "Existing matched capture has only 18 original projects; 36/48 need a new project-disjoint population and natural source.",
            "Arrival realization is fixed seed21; do not select a favorable seed or silently repeat projects.",
            "All arms need identical scheduled arrivals and JCT including external and local admission waiting.",
            "Full JCT retains every workflow including warmup and drain; a separately declared central window can describe throughput.",
        ],
        "inputs_sha256": inputs, "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "complete_work_rates": {name: row["finite_burst_completion_rate_per_s"] for name, row in arms.items()},
        "hypothetical_arrivals": hypothetical,
    }, indent=2))


if __name__ == "__main__":
    main()
