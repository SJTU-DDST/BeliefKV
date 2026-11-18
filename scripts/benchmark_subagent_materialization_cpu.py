"""Measure bounded pressure-census CPU cost; no GPU or JCT simulation."""

import argparse
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.subagent_materialization import observe_materialization_headroom
from scripts.train_workflow_work import write


def fixture(count):
    root = NS(id=0)
    nodes = {0: root}
    for index in range(1, count):
        nodes[index] = NS(
            id=index, write_through_pending_id=None, load_back_pending_id=None,
            children={}, parent=root,
            component_data={0: NS(
                value=(0,) * 128 if index % 4 == 0 else None,
                host_value=(0,) * 128, session_ref=1, host_lock_ref=0, lock_ref=0,
            )},
        )
    capacity = max(1, count - 1) * 128
    pool = NS(size=capacity, available_size=lambda: 0)
    return NS(
        tree_components=(0,), is_write_back=True, host_memory_mode="cache",
        tree_core=NS(
            _node_arena=nodes, root_node=root,
            full_host_duplicates={
                index: node for index, node in nodes.items() if index and index % 4 == 0
            },
            _can_reclaim_full_host_duplicate=lambda _: True,
        ),
        host_pool_group=NS(entry_map={"kv": NS(host_pool=pool)}),
        token_to_kv_pool_allocator=NS(available_size=lambda: 256),
    )


def benchmark(repetitions):
    if type(repetitions) is not int or repetitions < 10:
        raise ValueError("at least ten CPU measurements are required")
    rows = []
    for count in (64, 1024, 8192, 8193):
        native = fixture(count)
        for fast in (False, True):
            samples = []
            for _ in range(repetitions):
                started = time.thread_time_ns()
                result = observe_materialization_headroom(
                    native, required_free_tokens=128 if fast else None,
                )
                samples.append((time.thread_time_ns() - started) / 1000.)
            ordered = sorted(samples)
            expected_duplicates = (count - 1) // 4 * 128
            if not fast and count <= 8192:
                assert result.observable and result.census_complete
                assert result.reclaimable_host_tokens == expected_duplicates
                assert result.useful_host_only_tokens == (count - 1) * 128 - expected_duplicates
            elif fast:
                assert result.observable and not result.census_complete and result.inspected_nodes == 0
            else:
                assert not result.observable
            rows.append({
                "native_shape_standin_nodes": count, "raw_free_fast_path": fast,
                "observable": result.observable, "census_complete": result.census_complete,
                "thread_cpu_us_median": statistics.median(samples),
                "thread_cpu_us_p95": ordered[int(.95 * (len(ordered) - 1))],
            })
    return {
        "scope": "Python_scalar_census_CPU_microbenchmark",
        "node_fields_are_standins_not_native_allocator_or_DMA_execution": True,
        "samples_per_case": repetitions, "cases": rows,
        "source_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                Path(__file__), ROOT / "beliefkv/runtime/subagent_materialization.py",
            )
        },
        "GPU_action": False, "GPU_JCT_simulated": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=100)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous CPU measurements")
    report = benchmark(args.repetitions)
    write(args.output, report)
    print(json.dumps(report["cases"], indent=2))


if __name__ == "__main__":
    main()
