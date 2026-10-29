"""Review the complete dispatch candidate against its matched completed control."""

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

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts import plot_causal_decode_diagnostics as plotting
from scripts import run_causal_comparison as causal

PLOT_PYTHON = Path("/home/longhao/experiment/ops/cpu_plotting_20261027/bin/python")


def verify(plan):
    if git_source_state(ROOT) != plan["application"]:
        raise ValueError("frozen CPU reviewer changed")
    for path, digest in plan["inputs_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("review input changed: " + path)


def register(args):
    application = git_source_state(ROOT)
    if application["dirty"] or args.output_root.exists():
        raise ValueError("require a clean frozen reviewer and fresh output")
    candidate = causal.load(args.candidate)
    if candidate["order"] != ["full"]:
        raise ValueError("review only the declared Full candidate")
    args.output_root.mkdir(parents=True)
    reader = ROOT / "experiments/reports/arrival_mean120_20261029/review_pair.py"
    sources = (args.thunder, args.candidate, args.original, Path(__file__), reader)
    path = args.output_root / "registration.json"
    causal.reservation.write_json(path, {
        "application": application, "registered_at": datetime.now(timezone.utc).isoformat(),
        "thunder": str(args.thunder.resolve()), "candidate": str(args.candidate.resolve()),
        "original": str(args.original.resolve()), "reader": str(reader),
        "inputs_sha256": {str(source.resolve()): sha256_file(source) for source in sources},
        "GPU_experiment_registered": False,
    })
    with (args.output_root / "watcher.log").open("x") as log:
        child = subprocess.Popen(
            ["nice", "-n", "19", sys.executable, str(Path(__file__)), "--watch", str(path)],
            cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21", "OMP_NUM_THREADS": "1"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    print({"registration": str(path), "watcher": causal.reservation.process(child.pid)})


def watch(path):
    plan = causal.load(path)
    state_path = path.with_suffix(".state.json")
    if state_path.exists():
        raise FileExistsError("preserve previous reviewer state")
    state = {
        "status": "waiting_complete_candidate", "watcher": causal.reservation.process(os.getpid()),
        "GPU_experiment_registered": False, "performance_accepted": False,
    }
    try:
        verify(plan)
        missing_state_checks = 0
        while True:
            candidate_state_path = Path(plan["candidate"]).with_suffix(".state.json")
            if not candidate_state_path.exists():
                missing_state_checks += 1
                if missing_state_checks > 3:
                    raise ValueError("registered candidate has not published its supervisor state")
                state["checked_at"] = causal.reservation.now().isoformat()
                causal.reservation.write_json(state_path, state)
                time.sleep(20.)
                continue
            if all(complete_work(Path(plan[name])) is not None for name in ("thunder", "original", "candidate")):
                break
            candidate_state = causal.load(candidate_state_path)
            if not causal.reservation.alive(candidate_state.get("watcher")):
                raise ValueError("candidate supervisor exited without complete drained work")
            state["checked_at"] = causal.reservation.now().isoformat()
            causal.reservation.write_json(state_path, state)
            time.sleep(20.)
        verify(plan)
        state["status"] = "reviewing_complete_candidate"
        causal.reservation.write_json(state_path, state)
        with (path.parent / "reader.log").open("x") as log:
            subprocess.run(
                [str(PLOT_PYTHON), plan["reader"], "--thunder", plan["thunder"],
                 "--full", plan["candidate"], "--output-dir", str(path.parent / "complete_review")],
                cwd=ROOT, check=True, stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "OMP_NUM_THREADS": "1"},
            )
        original = causal.load(Path(plan["original"]))
        arm = Path(original["run_root"]) / "full"
        causal.observed_configuration(original, "full", arm, final=True)
        original_row = plotting.extract("Original FULL", Path(plan["original"]), arm, bin_s=60)
        review = causal.load(path.parent / "complete_review/review.json")
        with gzip.open(path.parent / "complete_review/decode_evidence.json.gz", "rt") as stream:
            candidate_row = json.load(stream)["FULL"]
        if original_row["match_contract"] != candidate_row["match_contract"]:
            raise ValueError("original and candidate Full work or serving contracts differ")
        candidate_metrics = review["metrics"]["FULL"]
        baseline = {key: original_row[key] for key in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s",
        )}
        comparison = {
            "original": baseline, "candidate": candidate_metrics,
            "mean_jct_reduction_percent": 100 * (1 - candidate_metrics["mean_jct_s"] / baseline["mean_jct_s"]),
            "whole_run_output_throughput_gain_percent": 100 * (
                candidate_metrics["end_to_end_output_tokens_per_s"] / baseline["end_to_end_output_tokens_per_s"] - 1
            ),
            "original_complete_evidence_sha256": original_row["evidence_sha256"],
            "performance_accepted": False,
        }
        plotting.require_complete_prefixes(original_row["input_jsonl_prefixes"])
        causal.reservation.write_json(path.parent / "candidate_vs_original.json", comparison)
        verify(plan)
        state.update(status="complete_candidate_reviewed", output=str(path.parent / "complete_review"))
    except BaseException as error:
        state.update(status="stopped_without_gpu_action", error=repr(error))
    finally:
        state["finished_at"] = causal.reservation.now().isoformat()
        causal.reservation.write_json(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("thunder", "candidate", "original", "output-root", "watch"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    elif args.thunder and args.candidate and args.original and args.output_root:
        register(args)
    else:
        parser.error("require complete registration inputs or --watch")


if __name__ == "__main__":
    main()
