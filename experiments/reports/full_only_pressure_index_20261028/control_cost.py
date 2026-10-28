"""Compare unchanged publication results on real native FULL-only CPU trees."""

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace as NS
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
BASELINE = "e45a9c6b"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=200)
    args = parser.parse_args()
    if args.output.exists() or args.iterations < 100:
        raise ValueError("preserve prior evidence and measure at least 100 samples")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("CPU fixture requires hidden GPUs")
    import pytest
    from beliefkv.runtime import sglang_v0520_runtime as runtime_module
    from beliefkv.runtime.hotpath_timing import timed_runtime
    from tests.test_component_pressure_publication import full_only_publication_runtime
    from tests.test_full_only_native_lifecycle import full_native

    relative = "beliefkv/runtime/sglang_v0520_runtime.py"
    source = subprocess.check_output(
        ["git", "show", BASELINE + ":" + relative], cwd=ROOT,
    )
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "NativeAdmissionRuntime")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_publish_parent_pressure_candidates")
    method.decorator_list = []
    namespace = dict(vars(runtime_module))
    exec(compile(ast.Module(body=[method], type_ignores=[]), BASELINE + ":" + relative, "exec"), namespace)
    previous = timed_runtime("prepare_candidate_publication")(
        namespace["_publish_parent_pressure_candidates"],
    )
    current = runtime_module.NativeAdmissionRuntime._publish_parent_pressure_candidates
    files = (
        relative, "tests/test_component_pressure_publication.py",
        "tests/test_full_only_native_lifecycle.py", str(Path(__file__).resolve().relative_to(ROOT)),
    )
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}
    cases = {}
    for name, owners, components in (
        ("full_1", 1, (0,)), ("full_12", 12, (0,)), ("full_24", 24, (0,)),
        ("unknown_12", 12, ()), ("incomplete_hybrid_12", 12, (0, 2)),
    ):
        with pytest.MonkeyPatch.context() as monkeypatch, tempfile.TemporaryDirectory() as directory:
            fixture = full_native.__wrapped__(monkeypatch, NS(param=4096))
            cache, allocator, *_ = fixture
            runtime, _ = full_only_publication_runtime(fixture, owners=owners)
            runtime._hotpath_timing.enabled = False
            cache.tree_components = components
            try:
                before_free = allocator.available_size()
                candidates = dict(runtime._parent_pressure_candidates)
                observations, measured_reads = {}, {}
                samples = {label: [] for label in ("previous", "current")}
                wall = {label: [] for label in samples}
                callbacks = {"previous": previous, "current": current}
                for label, callback in callbacks.items():
                    with patch.object(
                        cache.tree_core, "node_by_id", wraps=cache.tree_core.node_by_id,
                    ) as lookup:
                        callback(runtime)
                    measured_reads[label] = lookup.call_count
                for iteration in range(args.iterations + 10):
                    order = ("current", "previous") if iteration % 2 else ("previous", "current")
                    for label in order:
                        start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
                        callbacks[label](runtime)
                        cpu = time.thread_time_ns() - cpu_start
                        elapsed = time.perf_counter_ns() - start
                        observations[label] = cache.beliefkv_join_pressure_candidates_by_component
                        if iteration >= 10:
                            samples[label].append(cpu / 1000.)
                            wall[label].append(elapsed / 1000.)
                    if observations["previous"] != observations["current"]:
                        raise ValueError("publication changed the actual native candidate prefix")
                if (
                    allocator.available_size() != before_free
                    or runtime._parent_pressure_candidates != candidates
                    or cache.cache_controller.write_queue or cache.cache_controller.load_queue
                ):
                    raise ValueError("read-only publication changed allocation, candidates or transfers")
                costs = {}
                for label, values in samples.items():
                    values.sort()
                    costs[label] = {
                        "thread_cpu_p50_us": statistics.median(values),
                        "thread_cpu_p99_us": values[math.ceil(.99 * len(values)) - 1],
                        "thread_cpu_max_us": values[-1],
                        "wall_p50_us": statistics.median(wall[label]),
                    }
                cases[name] = {
                    "owners": owners, "candidate_nodes": len(candidates),
                    "declared_components": components,
                    "single_call_native_node_lookups_outside_timing": measured_reads,
                    "samples_per_variant": args.iterations, "costs": costs,
                    "candidate_prefix": observations["current"],
                    "same_native_candidate_prefix_and_free_slots": True,
                }
            finally:
                runtime.close()
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest
           for name, digest in sources.items()):
        raise ValueError("source changed during measurement")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Read-only candidate publication on real native CPU trees; no GPU/JCT attribution.",
        "baseline_commit": BASELINE, "baseline_runtime_sha256": hashlib.sha256(source).hexdigest(),
        "sources_sha256": sources, "cases": cases,
        "gpu_used": False, "performance_accepted": False,
        "limitations": [
            "Known FULL-only layouts use actual native trees/allocators with Host index stand-ins.",
            "Unknown and incomplete-hybrid declarations test conservative fallback, not hybrid model execution.",
            "Lookup counting is excluded from timing; construction and equality checks are also excluded.",
            "This local publication cost does not quantify whole-controller cost or removable JCT.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({name: row["costs"] for name, row in cases.items()}, indent=2))


if __name__ == "__main__":
    main()
