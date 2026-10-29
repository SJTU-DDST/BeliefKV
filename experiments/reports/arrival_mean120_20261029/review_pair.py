"""Review complete matched arrivals and preserve decode timelines/distributions."""

import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.native_engine_variant import COMMON_TREE, FREE_EXTENT_TREE
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts import plot_causal_decode_diagnostics as plotting
from scripts import run_causal_comparison as causal


def validate_methods(rows):
    expected = {"ThunderAgent decay": COMMON_TREE, "FULL": FREE_EXTENT_TREE}
    if rows.keys() != expected.keys():
        raise ValueError("expected the declared ThunderAgent and Full methods")
    contracts = []
    for label, row in rows.items():
        contract = row["match_contract"]
        if contract["engine_tree"] != expected[label]:
            raise ValueError("method changed its qualified native engine")
        contracts.append({key: value for key, value in contract.items() if key != "engine_tree"})
    if contracts[0] != contracts[1]:
        raise ValueError("methods changed work, arrivals, capacities, serving or seeds")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thunder", type=Path, required=True)
    parser.add_argument("--full", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier complete reviews")
    rows, work, fairness, inputs = {}, {}, {}, {}
    registrations = {"ThunderAgent decay": args.thunder, "FULL": args.full}
    for label, path in registrations.items():
        item = complete_work(path)
        if item is None:
            raise ValueError("live prefixes cannot establish complete performance")
        work[label] = item
        plan = causal.load(path)
        arm = Path(plan["run_root"]) / plan["order"][0]
        causal.observed_configuration(plan, arm.name, arm, final=True)
        summary = causal.load(arm / "replay/summary.json")
        rows[label] = plotting.extract(label, path, arm, bin_s=60)
        fairness[label] = {
            key: summary["streaming_response_metrics"][key] for key in (
                "workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms",
            )
        }
        for source in (
            path, path.with_suffix(".state.json"), arm / "telemetry_drain.json",
            arm / "owned_process_cleanup.json", Path(plan["workflow_arrivals_path"]),
        ):
            inputs[str(source)] = sha256_file(source)
        inputs.update(rows[label]["evidence_sha256"])
    if work["ThunderAgent decay"] != work["FULL"]:
        raise ValueError("method work differs")
    validate_methods(rows)
    args.output_dir.mkdir(parents=True)
    extracted = args.output_dir / "decode_evidence.json.gz"
    with extracted.open("xb") as stream:
        stream.write(gzip.compress(json.dumps(rows, allow_nan=False).encode(), mtime=0))
    arrivals = rows["FULL"]["match_contract"]["workflow_arrivals"]
    mean_seconds = 1 / arrivals["rate_per_second"]
    figures = plotting.render(
        rows, args.output_dir,
        title=f"Matched warm start: mean {mean_seconds:g}-second arrivals", bin_s=60,
    )
    plotting.write_csv(args.output_dir / "time_bins.csv", [
        {"arm": label, **row} for label, arm in rows.items() for row in arm["bins"]
    ])
    metrics = {
        label: {key: row[key] for key in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
        )} for label, row in rows.items()
    }
    control, full = metrics["ThunderAgent decay"], metrics["FULL"]
    changes = {
        "mean_jct_reduction_percent": 100 * (1 - full["mean_jct_s"] / control["mean_jct_s"]),
        "whole_run_output_throughput_gain_percent": 100 * (
            full["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1
        ),
    }
    for row in rows.values():
        plotting.require_complete_prefixes(row["input_jsonl_prefixes"])
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("completed evidence changed during review")
    causal.reservation.write_json(args.output_dir / "review.json", {
        "status": "complete_matched_arrival_pair_reviewed",
        "observed_at": datetime.now(timezone.utc).isoformat(), "metrics": metrics,
        "full_relative_to_thunder": changes, "streaming_response_metrics": fairness,
        "requests": 3674, "prompt_tokens": 145565966, "output_tokens": 549671,
        "method_specific_qualified_native_trees": {
            label: row["match_contract"]["engine_tree"] for label, row in rows.items()
        },
        "all_normal_tails_exact_tokens_stopping_drained_telemetry_and_owned_exit_verified": True,
        "inputs_sha256": inputs, "decode_evidence_sha256": sha256_file(extracted),
        "figures": figures, "performance_accepted": False,
        "limitations": [
            "One paired execution is not a repeated-run superiority result.",
            "Finite warm-start arrivals do not establish stationary capacity.",
            "Whole-run output rate includes normal arrival gaps and all tails.",
            "Worker intervals include runtime waits, not exclusive GPU kernel time.",
        ],
    })
    print(json.dumps({"metrics": metrics, "full_relative_to_thunder": changes}, indent=2))


if __name__ == "__main__":
    main()
