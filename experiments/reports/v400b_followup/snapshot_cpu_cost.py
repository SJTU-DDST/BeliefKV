"""Bounded CPU cost check for the once-per-second reference status snapshot."""

from dataclasses import asdict
import json
from pathlib import Path
import random
import statistics
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.paper_baseline import ThunderAgentDecayAdapter
from beliefkv.runtime.reference_thunder_admission import ReferenceThunderAdmission


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve snapshot cost evidence")
    runtime = NS(
        internal_comparison_mode="full", _custom_kv_management=True,
        _paper_baseline=None, enable_frontier_restore=False, enable_execution_handoff=False,
    )
    adapter = ReferenceThunderAdmission(runtime)
    adapter.attach(NS(tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=805198)))
    rng = random.Random(21)
    for index in range(32):
        name = "synthetic-workflow:project:context:" + str(index) + ":root"
        adapter.policy.arrive(name, rng.randrange(8000, 120001), now=index / 100.)
        if index % 3 == 0:
            adapter.policy.completed(name, adapter.policy.programs[name].tokens, now=1.)
    before = {name: asdict(item) for name, item in adapter.policy.programs.items()}
    calls = {
        "parent_aggregate_status": lambda: ThunderAgentDecayAdapter.status(adapter),
        "full_identity_status": adapter.status,
    }
    for _ in range(100):
        for call in calls.values():
            call()
    samples = {name: [] for name in calls}
    for _ in range(2000):
        for name, call in calls.items():
            begin = time.thread_time_ns()
            call()
            samples[name].append((time.thread_time_ns() - begin) / 1000.)
    if before != {name: asdict(item) for name, item in adapter.policy.programs.items()}:
        raise ValueError("status snapshot mutated policy state")
    result = {
        "programs": len(before), "samples_per_variant": 2000,
        "thread_cpu_us": {
            name: {
                "median": statistics.median(values), "p99": sorted(values)[1979],
                "max": max(values),
            } for name, values in samples.items()
        },
        "source_sha256": {
            str(path.relative_to(ROOT)): sha256_file(path) for path in (
                Path(__file__), ROOT / "beliefkv/runtime/reference_thunder_admission.py",
                ROOT / "beliefkv/runtime/paper_baseline.py",
            )
        },
        "policy_state_unchanged": True,
        "scope": "CPU snapshot construction only; synthetic fixture; not whole-controller or GPU performance.",
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result, indent=2))
    adapter.close()


if __name__ == "__main__":
    main()
