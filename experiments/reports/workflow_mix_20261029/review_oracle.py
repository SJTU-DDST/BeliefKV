"""Review the complete mixed-workload oracle and its matched retained Full."""

import argparse
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.workflow_mix_20261029.review_burst import complete_work
from scripts import plot_causal_decode_diagnostics as plotting
from scripts import run_causal_comparison as causal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("the postprocessor must hide CUDA")
    if args.output_dir.exists():
        raise FileExistsError("preserve every prior complete oracle review")
    control_work = complete_work(args.control)
    if control_work is None:
        raise ValueError("the matched retained control must already be complete")
    if args.wait:
        started = time.monotonic()
        while complete_work(args.oracle) is None:
            if time.monotonic() - started > 4 * 3600:
                raise TimeoutError("oracle complete review exceeded four hours")
            time.sleep(10.)
    if complete_work(args.oracle) != control_work:
        raise ValueError("require exact complete matching work and actual owned exit")
    plans = {name: causal.load(path) for name, path in (
        ("oracle", args.oracle), ("retained Full", args.control),
    )}
    if any(plan["order"] != ["full"] for plan in plans.values()):
        raise ValueError("expected separate single-Full executions")
    if (
        plans["oracle"]["reference_environment"]["BELIEFKV_WORKFLOW_WORK_SOURCE"] != "oracle"
        or plans["retained Full"]["reference_environment"]["BELIEFKV_WORKFLOW_SIZE_RESUME"] != "0"
    ):
        raise ValueError("the declared ranked and unranked methods differ")
    specification = causal.load(Path(plans["oracle"]["experiment_configuration"]))
    if specification["candidate_provenance"]["control_registration_sha256"] != sha256_file(args.control):
        raise ValueError("this is not the oracle's declared retained control")
    rows = {
        name: plotting.extract(name, path, Path(plans[name]["run_root"]) / "full", bin_s=60)
        for name, path in (("oracle", args.oracle), ("retained Full", args.control))
    }
    plotting.require_matching(rows)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    extracted = args.output_dir / "decode_evidence.json.gz"
    extracted.write_bytes(gzip.compress(json.dumps(rows, allow_nan=False).encode(), mtime=0))
    figures = plotting.render(
        rows, args.output_dir, title="Mixed workload: cached model/tool oracle vs. retained Full", bin_s=60,
    )
    plotting.write_csv(args.output_dir / "time_bins.csv", [
        {"arm": name, **bucket} for name, row in rows.items() for bucket in row["bins"]
    ])
    arm = Path(plans["oracle"]["run_root"]) / "full"
    expected_receipts = Path(plans["retained Full"]["run_root"]) / "full/replay/native_requests.jsonl"
    actions = {
        "competition": [
            "scripts/audit_workflow_competition.py", "--registration", str(args.oracle),
            "--population", str(args.population), "--output", str(args.output_dir / "competition.json"),
        ],
        "native_demand": [
            "scripts/audit_demand_prefill_pipeline.py", "--registration", str(args.oracle),
            "--output", str(args.output_dir / "native_demand.json"),
        ],
        "prefetch": [
            "scripts/audit_prefetch_lifecycle.py", "--arm", str(arm), "--summary-only",
            "--output", str(args.output_dir / "prefetch.json"),
        ],
        "siblings": [
            "scripts/audit_sibling_native_service.py", "--arm", str(arm),
            "--output", str(args.output_dir / "siblings.json"),
        ],
        "eos": [
            "scripts/audit_eos_trigger_lifecycle.py", "--registration", str(args.oracle),
            "--expected-receipts", str(expected_receipts), "--output", str(args.output_dir / "eos.json"),
        ],
    }
    scripts = [ROOT / command[0] for command in actions.values()]
    inputs = {
        str(path): sha256_file(path)
        for path in (args.oracle, args.control, args.population, Path(__file__), *scripts)
    }
    for row in rows.values():
        inputs.update(row["evidence_sha256"])
    for name, arguments in actions.items():
        command = [sys.executable, str(ROOT / arguments[0]), *arguments[1:]]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=900.)
        causal.reservation.write_json(args.output_dir / (name + ".entry.json"), {
            "command": command, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        })
        if result.returncode:
            raise RuntimeError("preserve incomplete oracle mechanism audit: " + name)
    metrics = {
        name: {key: row[key] for key in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
        )} for name, row in rows.items()
    }
    candidate, reference = metrics["oracle"], metrics["retained Full"]
    changes = {
        "mean_jct_reduction_percent": 100 * (1 - candidate["mean_jct_s"] / reference["mean_jct_s"]),
        "whole_run_output_throughput_gain_percent": 100 * (
            candidate["end_to_end_output_tokens_per_s"] / reference["end_to_end_output_tokens_per_s"] - 1
        ),
    }
    for row in rows.values():
        plotting.require_complete_prefixes(row["input_jsonl_prefixes"])
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("completed comparison inputs changed")
    fairness = {}
    for name, plan in plans.items():
        summary = causal.load(Path(plan["run_root"]) / "full/replay/summary.json")
        fairness[name] = {
            key: summary["streaming_response_metrics"][key] for key in (
                "workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms",
            )
        }
    causal.reservation.write_json(args.output_dir / "review.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "complete_equal_work_fresh_drain_and_actual_exit": True,
        "requests": len(control_work), "workflows": len(plans["oracle"]["workflow_ids"]),
        "metrics": metrics, "oracle_relative_to_control": changes,
        "streaming_response_metrics": fairness,
        "competition_counts": causal.load(args.output_dir / "competition.json")["counts"],
        "decode_evidence_sha256": sha256_file(extracted), "figures": figures,
        "inputs_sha256": inputs, "future_information_used": True, "deployable": False,
        "optimal_JCT_bound": False, "repeated_run_superiority_established": False,
        "GPU_followup_registered": False, "performance_accepted": False,
    })
    print(json.dumps({"metrics": metrics, "oracle_relative_to_control": changes}))


if __name__ == "__main__":
    main()
