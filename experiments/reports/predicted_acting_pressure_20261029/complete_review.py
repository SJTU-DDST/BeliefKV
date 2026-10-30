"""CPU-only complete Full review; never launch or retry a GPU experiment."""

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts import run_causal_comparison as causal


def pressure_decisions(records):
    counts = Counter()
    for row in records:
        if row.get("event") != "agent_predicted_acting_pressure":
            continue
        before, after, tokens = (row.get(name) for name in (
            "remaining_before_tokens", "remaining_after_tokens", "context_tokens",
        ))
        if (
            any(type(value) is not int for value in (before, after, tokens))
            or before >= 0 or tokens <= 0 or after != before + tokens + 100
            or type(row.get("acting_candidates")) is not int or row["acting_candidates"] < 1
            or row.get("physical_capacity_credit") is not False
            or any(type(row.get(name)) is not str or not row[name]
                   for name in ("selected", "reference_selected"))
        ):
            raise ValueError("pressure journal changed logical capacity or identity")
        changed, evidence = row["selected"] != row["reference_selected"], row.get("evidence")
        if changed and evidence is None:
            raise ValueError("changed pressure victim lacks prediction evidence")
        if evidence is not None:
            probability = evidence.get("completion_probability_within_threshold")
            survival, threshold = evidence.get("minimum_survival"), evidence.get("threshold_ms")
            if (
                evidence.get("key", {}).get("invocation_id") != row["selected"]
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in (survival, threshold))
                or not 0 < survival < 1 or threshold <= 0
                or probability is not None and (
                    type(probability) not in (int, float) or not math.isfinite(probability)
                    or not 0 <= probability <= 1 - survival + 1e-12
                )
            ):
                raise ValueError("pressure journal has unsupported recorded prediction bounds")
            counts["predicted_choices"] += 1
            counts["quantile_only_bounds_not_reconstructed"] += probability is None
        counts["mandatory_acting_pauses"] += 1
        counts["changed_local_victims"] += changed
        counts["logical_tokens_paused"] += tokens + 100
    return {
        "counts": dict(counts),
        "scope": (
            "Explicit mandatory logical pressure decisions only. No physical freed pages, "
            "saved migration, complete reference trajectory or causal JCT benefit follows."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--original-review", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("CPU review must hide CUDA")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    paths = (args.full, args.control, args.original, args.original_review, Path(__file__))
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    state_path = args.output_dir / "state.json"
    state = {
        "status": "waiting_complete_full_and_actual_exit",
        "watcher": causal.reservation.process(os.getpid()),
        "GPU_experiment_registered": False, "performance_accepted": False,
        "inputs_sha256": pins,
    }

    def stamp():
        causal.reservation.write_json(state_path, state)

    try:
        reference = complete_work(args.control)
        if reference is None or complete_work(args.original) != reference:
            raise ValueError("original Full and control are not complete equal work")
        started = time.monotonic()
        while (work := complete_work(args.full)) is None:
            status = causal.load(args.full.with_suffix(".state.json"))
            if not causal.reservation.alive(status.get("watcher")):
                raise ValueError("Full exited without complete work; no GPU retry")
            if time.monotonic() - started > 4 * 3600:
                raise TimeoutError("complete Full review exceeded four hours")
            state["checked_at"] = causal.reservation.now().isoformat()
            stamp()
            time.sleep(10.)
        if work != reference:
            raise ValueError("Full work differs from both retained methods")
        state["status"] = "reviewing_complete_work"
        stamp()
        full = causal.load(args.full)
        control = causal.load(args.control)
        arm = Path(full["run_root"]) / "full"
        commands = {
            "pair": [
                "/home/longhao/experiment/ops/cpu_plotting_20261027/bin/python",
                str(ROOT / "experiments/reports/arrival_mean120_20261029/review_pair.py"),
                "--thunder", str(args.control), "--full", str(args.full),
                "--output-dir", str(args.output_dir / "pair"),
            ],
            "native_demand": [
                sys.executable, str(ROOT / "scripts/audit_demand_prefill_pipeline.py"),
                "--registration", str(args.full),
                "--output", str(args.output_dir / "native_demand.json"),
            ],
            "prefetch": [
                sys.executable, str(ROOT / "scripts/audit_prefetch_lifecycle.py"),
                "--arm", str(arm), "--summary-only",
                "--output", str(args.output_dir / "prefetch.json"),
            ],
            "siblings": [
                sys.executable, str(ROOT / "scripts/audit_sibling_native_service.py"),
                "--arm", str(arm), "--output", str(args.output_dir / "siblings.json"),
            ],
            "eos": [
                sys.executable, str(ROOT / "scripts/audit_eos_trigger_lifecycle.py"),
                "--registration", str(args.full),
                "--expected-receipts", str(
                    Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl"
                ), "--output", str(args.output_dir / "eos.json"),
            ],
        }
        for label, command in commands.items():
            pins[str(Path(command[1]).resolve())] = sha256_file(Path(command[1]))
            state["current_action"] = label
            stamp()
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=600.)
            causal.reservation.write_json(args.output_dir / (label + ".entry.json"), {
                "command": command, "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr,
            })
            if result.returncode:
                raise RuntimeError("complete CPU review failed: " + label)
        journal = arm / "opportunities/admission_opportunities.jsonl"
        digest = sha256_file(journal)
        with journal.open() as stream:
            decisions = pressure_decisions(json.loads(line) for line in stream)
        if sha256_file(journal) != digest:
            raise ValueError("complete pressure journal changed")
        causal.reservation.write_json(args.output_dir / "pressure_decisions.json", {
            **decisions, "journal_sha256": digest,
        })
        pair = causal.load(args.output_dir / "pair/review.json")
        original = causal.load(args.original_review)["metrics"]["FULL"]
        current = pair["metrics"]["FULL"]
        if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
            raise ValueError("complete reviewer inputs changed")
        causal.reservation.write_json(args.output_dir / "review.json", {
            "pair": pair["full_relative_to_thunder"], "pressure_decisions": decisions,
            "relative_to_original_full": {
                "mean_jct_reduction_percent": 100 * (1 - current["mean_jct_s"] / original["mean_jct_s"]),
                "output_throughput_gain_percent": 100 * (
                    current["end_to_end_output_tokens_per_s"]
                    / original["end_to_end_output_tokens_per_s"] - 1
                ),
            },
            "inputs_sha256": pins,
            "GPU_experiment_registered": False, "performance_accepted": False,
        })
        state["status"] = "complete_cpu_review_no_GPU_followup"
    except BaseException as error:
        state.update(status="stopped_without_GPU_action", error=repr(error))
    finally:
        state["finished_at"] = causal.reservation.now().isoformat()
        stamp()


if __name__ == "__main__":
    main()
