"""Test current resident-frontier length as an optimistic offline sensitivity."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.audit_resident_decode_partition import simulate_frontier
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def classify_frontier(members):
    lengths = defaultdict(int)
    for item in members:
        lengths[item["workflow_id"]] = max(lengths[item["workflow_id"]], item["remaining"])
    threshold = statistics.median(lengths.values())
    return [
        {**item, "short": lengths[item["workflow_id"]] <= threshold} for item in members
    ]


def inspect(screen_path):
    screen = json.loads(screen_path.read_bytes())
    for filename, digest in screen["inputs_sha256"].items():
        if sha256_file(Path(filename)) != digest:
            raise ValueError("original cost screen inputs changed")
    registration = json.loads(Path(screen["registration"]).read_bytes())
    arm = Path(registration["run_root"]) / "full"
    info = json.loads((arm / "server/server_info.json").read_bytes())
    reader = SealedRecords()
    requests = {row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")}
    wanted = {row["forward_iter"]: row for row in screen["frontier_simulations"]}
    samples = []
    coefficients = np.array(screen["timing_screen"]["coefficients_ms"])
    buckets = info["cuda_graph_config"]["decode"]["bs"]
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample" or row["forward_iter"] not in wanted:
            continue
        members = [{
            "request_id": item["request_id"], "workflow_id": item["workflow_id"],
            "context": item["sequence_tokens_before"],
            "remaining": requests[item["request_id"]]["output_tokens"] - item["output_tokens_reported_before"],
            "short": False,
        } for item in row["request_samples"]]
        members = classify_frontier(members)
        baseline = simulate_frontier(members, coefficients, buckets)
        expected = wanted[row["forward_iter"]]["baseline"]
        if baseline.keys() != expected.keys() or (
            baseline["request_finish_ms"].keys() != expected["request_finish_ms"].keys()
            or any(not math.isclose(value, expected["request_finish_ms"][rid], rel_tol=1e-12)
                   for rid, value in baseline["request_finish_ms"].items())
        ):
            raise ValueError("the original closed-frontier baseline was not reproduced")
        outcomes = {
            "short_first": simulate_frontier(members, coefficients, buckets, short_first=True),
            "one_extra_short_per_8_full": simulate_frontier(
                members, coefficients, buckets, extra_short_every=8,
            ),
            "one_extra_short_per_32_full": simulate_frontier(
                members, coefficients, buckets, extra_short_every=32,
            ),
        }
        samples.append({
            "forward_iter": row["forward_iter"],
            "distinct_priority_groups": len({item["short"] for item in members}),
            "baseline": baseline, "variants": outcomes,
        })
    if {row["forward_iter"] for row in samples} != wanted.keys():
        raise ValueError("the new sensitivity did not cover the same sampled frontiers")
    if reader.prefixes != screen["input_jsonl_prefixes"]:
        raise ValueError("the completed native records differ from the original screen")
    summaries = {}
    for name in ("short_first", "one_extra_short_per_8_full", "one_extra_short_per_32_full"):
        gains, delays = [], []
        for row in samples:
            before, after = row["baseline"], row["variants"][name]
            gains.append(100 * (1 - after["frontier_workflow_mean_ms"] / before["frontier_workflow_mean_ms"]))
            delays.append(100 * (after["drain_ms"] / before["drain_ms"] - 1))
        summaries[name] = {
            "closed_frontier_mean_finish_gain_percent": distribution(gains),
            "closed_frontier_drain_time_increase_percent": distribution(delays),
            "frontiers_with_5pct_finish_gain_and_at_most_2pct_drain_increase": sum(
                gain >= 5 and delay <= 2 for gain, delay in zip(gains, delays)
            ),
            "frontiers_with_positive_finish_gain_and_at_most_2pct_drain_increase": sum(
                gain > 0 and delay <= 2 for gain, delay in zip(gains, delays)
            ),
        }
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "classification": "max remaining output of current resident requests per workflow; median across current workflows",
        "future_output_information_used_only_in_offline_analysis": True,
        "same_128_frontiers_and_original_baseline_exactly_reproduced": len(samples) == 128,
        "timing_model": screen["timing_screen"],
        "variant_summary": summaries, "frontiers": samples,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                screen_path, Path(__file__), ROOT / "scripts/audit_resident_decode_partition.py",
            )
        },
        "input_jsonl_prefixes": reader.prefixes,
        "scope": [
            "This intentionally uses true remaining output to screen a more favorable phase classification.",
            "Only requests already present in the observed GPU batch form the frontier.",
            "Max current-member completion is not a real ALL-JOIN or whole workflow completion.",
            "Future requests, branches, tools, arrivals, physical growth and control costs are not simulated.",
            "This is neither a whole-workflow JCT prediction nor a GPU performance qualification.",
        ],
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use nice19 CPU-only analysis")
    result = inspect(args.screen)
    write(args.output, result)
    print(json.dumps(result["variant_summary"]))


if __name__ == "__main__":
    main()
