"""Register the bounded parallel pilot only after the old comparison has exited."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.workflow_mix_20261029.review_burst import complete_work
from experiments.reports.workflow_work_training_20261029 import register_capture
from scripts import run_causal_source_capture as capture


def predecessor_complete(control, oracle, review, reviewer):
    work = complete_work(control)
    if work is None or complete_work(oracle) != work:
        raise ValueError("finish the complete equal-work comparison before new collection")
    result = capture.load(review)
    worker = capture.load(reviewer)
    if (
        result["complete_equal_work_fresh_drain_and_actual_exit"] is not True
        or result["inputs_sha256"].get(str(control)) != sha256_file(control)
        or result["inputs_sha256"].get(str(oracle)) != sha256_file(oracle)
        or not worker.get("process") or capture.reservation.alive(worker["process"])
    ):
        raise ValueError("wait for the completed bound CPU reviewer and its actual exit")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    predecessor_complete(
        OPS / "workflow_mix_burst_full_20261030_r1.registration.json",
        OPS / "workflow_mix_burst_oracle_20261030_r1.registration.json",
        MAIN / "experiments/reports/workflow_mix_20261029/burst_oracle_review_r1/review.json",
        MAIN / "experiments/reports/workflow_mix_20261029/burst_oracle_review_r1.registration.json",
    )
    sys.argv = [
        str(Path(register_capture.__file__)),
        "--cpu-evidence", str(args.cpu_evidence), "--output-dir", str(args.output_dir),
        "--workload", str(ROOT / "configs/workloads/locobench_parallel_pilot6_20261030.json"),
        "--configuration", str(ROOT / "configs/migration/workflow_parallel_pilot6_native_20261030.json"),
        "--registration", str(OPS / "workflow_parallel_pilot6_source_20261030_r1.registration.json"),
        "--run-root", str(MAIN / "experiments/raw/workflow_parallel_pilot6_source_20261030_pilot6"),
        "--port", "18906", "--purpose", "derived_deliverable_parallel_shape_pilot",
    ]
    register_capture.main()


if __name__ == "__main__":
    main()
