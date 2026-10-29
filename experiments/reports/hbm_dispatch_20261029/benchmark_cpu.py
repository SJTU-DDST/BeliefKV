"""Measure bounded dispatch CPU cost on the actual runtime with GPU hidden."""

import argparse
from datetime import datetime, timezone
import gc
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pytest import MonkeyPatch
from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.layer_free_extent_20261029.run_bound_cpu import write
from tests.test_reference_free_dispatch import queue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("CPU benchmark must hide the GPU")
    rows = {}
    with tempfile.TemporaryDirectory(prefix="bkv-dispatch-cpu-") as directory:
        with MonkeyPatch.context() as patch:
            live, adapter, peer, _reqs = queue(patch, Path(directory), count=24, tokens=400)
            try:
                for scene in ("no_raw_capacity", "eight_unfitting_candidates"):
                    if scene == "eight_unfitting_candidates":
                        peer.origin_input_ids = peer.origin_input_ids[:600]
                        adapter.policy.programs["peer"].tokens = 600
                    for _ in range(100):
                        adapter._admit_free_continuation()
                    repeats = 10000
                    before = dict(adapter.counts)
                    gc.collect()
                    begin_cpu, begin_wall = time.thread_time_ns(), time.perf_counter_ns()
                    for _ in range(repeats):
                        adapter._admit_free_continuation()
                    cpu, wall = time.thread_time_ns() - begin_cpu, time.perf_counter_ns() - begin_wall
                    rows[scene] = {
                        "passes": repeats, "pending_agents": len(adapter._dispatch_pending),
                        "thread_cpu_us_per_pass": cpu / repeats / 1000.,
                        "wall_us_per_pass": wall / repeats / 1000.,
                        "candidate_checks": adapter.counts["free_dispatch_candidate_checks"]
                        - before.get("free_dispatch_candidate_checks", 0),
                        "logical_grants": adapter.counts["free_continuation_admitted"]
                        - before.get("free_continuation_admitted", 0),
                        "pressure_ticks": adapter.counts["control_ticks"] - before.get("control_ticks", 0),
                    }
            finally:
                live.close()
    write(args.output, {
        "observed_at": datetime.now(timezone.utc).isoformat(), "scenes": rows,
        "operator_sha256": sha256_file(Path(__file__)),
        "runtime_sha256": sha256_file(ROOT / "beliefkv/runtime/reference_free_continuation.py"),
        "semantics": [
            "Actual Full Python runtime and graph, CPU-only logical capacity and native request fixtures.",
            "Warm microbenchmark thread CPU excludes scheduling waits; wall includes them.",
            "No GPU kernels, physical admission, migration, complete-run control cost or JCT gain is established.",
        ],
    })
    print(rows)


if __name__ == "__main__":
    main()
