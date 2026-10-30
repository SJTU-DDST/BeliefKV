"""Complete CPU review for the single model/tool oracle, with no GPU follow-up."""

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts import run_causal_comparison as causal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("complete review must hide CUDA")
    ops = ROOT.parent / "ops"
    thunder_path = ops / "prefill2048_mean120_thunder_20261029_r1.registration.json"
    original_path = ops / "decay_tick2s_no_handoff_20261030_r1.registration.json"
    learned_path = ops / "workflow_dispatch_20261029_r1.registration.json"
    controls = [thunder_path, original_path, learned_path]
    if args.wait:
        started = time.monotonic()
        while complete_work(args.full) is None:
            state = causal.load(args.full.with_suffix(".state.json"))
            if not causal.reservation.alive(state.get("watcher")):
                raise ValueError("oracle exited without complete work; no GPU retry")
            if time.monotonic() - started > 4 * 3600:
                raise TimeoutError("oracle complete review exceeded four hours")
            time.sleep(10.)
    work = complete_work(args.full)
    if work is None or any(complete_work(path) != work for path in controls):
        raise ValueError("require complete equal work, fresh drain and actual owned exits")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    full = causal.load(args.full)
    arm = Path(full["run_root"]) / "full"
    plotting_python = "/home/longhao/experiment/ops/cpu_plotting_20261027/bin/python"
    actions = {
        "pair": [
            plotting_python, str(ROOT / "experiments/reports/arrival_mean120_20261029/review_pair.py"),
            "--thunder", str(thunder_path), "--full", str(args.full),
            "--output-dir", str(args.output_dir / "pair"),
        ],
        "oracle_choices": [
            sys.executable, str(Path(__file__).with_name("review_choices.py")),
            "--registration", str(args.full), "--output", str(args.output_dir / "oracle_choices.json"),
        ],
        "native_demand": [
            sys.executable, str(ROOT / "scripts/audit_demand_prefill_pipeline.py"),
            "--registration", str(args.full), "--output", str(args.output_dir / "native_demand.json"),
        ],
        "prefetch": [
            sys.executable, str(ROOT / "scripts/audit_prefetch_lifecycle.py"), "--arm", str(arm),
            "--summary-only", "--output", str(args.output_dir / "prefetch.json"),
        ],
        "siblings": [
            sys.executable, str(ROOT / "scripts/audit_sibling_native_service.py"), "--arm", str(arm),
            "--output", str(args.output_dir / "siblings.json"),
        ],
        "eos": [
            sys.executable, str(ROOT / "scripts/audit_eos_trigger_lifecycle.py"),
            "--registration", str(args.full), "--expected-receipts",
            str(Path(causal.load(thunder_path)["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl"),
            "--output", str(args.output_dir / "eos.json"),
        ],
        "free_dispatch": [
            sys.executable, str(ROOT / "scripts/audit_free_dispatch_service.py"),
            "--registration", str(args.full), "--expected-receipts",
            str(Path(causal.load(thunder_path)["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl"),
            "--complete", "--output", str(args.output_dir / "free_dispatch.json"),
        ],
    }
    pins = {
        str(path.resolve()): sha256_file(path)
        for path in [args.full, *controls, Path(__file__), *(Path(cmd[1]) for cmd in actions.values())]
    }
    for name, command in actions.items():
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=900.)
        causal.reservation.write_json(args.output_dir / (name + ".entry.json"), {
            "command": command, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        })
        if result.returncode:
            raise RuntimeError("preserve incomplete complete-oracle review: " + name)
    pair = causal.load(args.output_dir / "pair/review.json")
    metrics = {"oracle": pair["metrics"]["FULL"], "ThunderAgent decay": pair["metrics"]["ThunderAgent decay"]}
    original_review = ROOT / "experiments/reports/decay_tick2s_no_handoff_20261030/queue_r1/complete_review/review.json"
    pins[str(original_review)] = sha256_file(original_review)
    metrics["original Full"] = causal.load(original_review)["metrics"]["FULL"]
    learned = causal.load(learned_path)
    summary_path = Path(learned["run_root"]) / "full/replay/summary.json"
    summary = causal.load(summary_path)
    offsets = {
        row["workflow_id"]: row["offset_seconds"]
        for row in learned["workflow_arrivals"]["arrival_schedule"]
    }
    elapsed = summary["workflow_elapsed_ms"]
    makespan = max(offsets[workflow] + duration / 1000. for workflow, duration in elapsed.items())
    metrics["learned dispatch"] = {
        "mean_jct_s": statistics.mean(elapsed.values()) / 1000., "makespan_s": makespan,
        "end_to_end_output_tokens_per_s": full["source_plan"]["output_tokens"] / makespan,
    }
    pins[str(summary_path)] = sha256_file(summary_path)
    current = metrics["oracle"]
    comparisons = {
        label: {
            "mean_jct_reduction_percent": 100 * (1 - current["mean_jct_s"] / values["mean_jct_s"]),
            "whole_run_output_throughput_gain_percent": 100 * (
                current["end_to_end_output_tokens_per_s"] / values["end_to_end_output_tokens_per_s"] - 1
            ),
        } for label, values in metrics.items() if label != "oracle"
    }
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("complete review inputs changed")
    causal.reservation.write_json(args.output_dir / "review.json", {
        "metrics": metrics, "oracle_relative_to_controls": comparisons,
        "oracle_choices": causal.load(args.output_dir / "oracle_choices.json")["counts"],
        "complete_equal_work_and_actual_exit": True, "inputs_sha256": pins,
        "future_information_used": True, "deployable": False,
        "optimal_JCT_bound": False, "GPU_followup_registered": False,
        "performance_accepted": False,
    })
    print(json.dumps({"metrics": metrics, "oracle_relative_to_controls": comparisons}, indent=2))


if __name__ == "__main__":
    main()
