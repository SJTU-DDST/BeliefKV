"""Register one mixed-scope natural source using the qualified native collector."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from experiments.reports.workflow_work_training_20261029 import register_capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-evidence", type=Path, required=True)
    args = parser.parse_args()
    sys.argv = [
        str(Path(register_capture.__file__)),
        "--cpu-evidence", str(args.cpu_evidence),
        "--output-dir", "/home/longhao/experiment/BeliefKV/experiments/reports/workflow_mix_20261029/gpu_entry_r2",
        "--workload", str(ROOT / "configs/workloads/locobench_mixed_scope18_20261029.json"),
        "--configuration", str(ROOT / "configs/migration/workflow_mix_scope18_native_20261029.json"),
        "--registration", "/home/longhao/experiment/ops/workflow_mix_scope18_source_20261029_r1.registration.json",
        "--run-root", "/home/longhao/experiment/BeliefKV/experiments/raw/workflow_mix_scope18_source_20261029_scope18",
        "--port", "18903", "--purpose", "mixed_scope_natural_candidates_reuse_capture_as_native",
    ]
    register_capture.main()


if __name__ == "__main__":
    main()
