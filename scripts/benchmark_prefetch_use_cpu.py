#!/usr/bin/env python3
"""Compare first-service prefetch evidence on identical CPU prefix fixtures."""

from __future__ import annotations

import argparse
from array import array
import ast
from collections import Counter, OrderedDict
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.runtime import v0520_native_telemetry as telemetry

SOURCE = Path("beliefkv/runtime/v0520_native_telemetry.py")


def load_method(source: str, label: str):
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "NativeReactiveTelemetry")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_record_prefetch_first_service")
    namespace = dict(vars(telemetry))
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        method,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), label, "exec"), namespace)
    return namespace[method.name]


def fixture(tokens: int, extents: int, mismatch: str, native_array: bool):
    audit = telemetry.NativeReactiveTelemetry.__new__(telemetry.NativeReactiveTelemetry)
    emitted = []
    audit._emit_prefetch_use = emitted.append
    audit._pending_prefetch_use = OrderedDict()
    nodes = {0: NS(id=0, creation_time=0, parent=None)}
    for index in range(1, extents + 1):
        length = tokens * index // extents
        node = NS(
            id=index, creation_time=index, parent=nodes[index - 1],
            component_data=(NS(value=object()), NS(), NS(value=None)),
        )
        nodes[index] = node
        command = f"restore-{index}"
        audit._pending_prefetch_use[command] = (
            NS(
                command_id=command, context_id="ctx", context_epoch=2,
                node_ids=(index,), pool_bytes=(("kv", tokens // extents * 16),),
            ),
            ((index, index, node, length, node.component_data[0].value, None),),
            100., {index: (array("q", range(length)), None, None)},
        )
    audit._cache = NS(tree_core=NS(node_by_id=nodes.__getitem__))
    inputs = list(range(tokens + 1))
    if mismatch != "none":
        inputs[{"first": 0, "middle": tokens // 2, "last": tokens - 1}[mismatch]] = -1
    if native_array:
        inputs = array("q", inputs)
    request = NS(
        rid="next", last_node=extents, origin_input_ids=inputs,
        prefix_indices=range(tokens), cached_tokens_device=tokens,
    )
    return audit, request, emitted


def distribution(values):
    ordered = sorted(values)
    return {
        "samples": len(values), "mean_ms": statistics.mean(values),
        "p50_ms": statistics.median(values),
        "p90_ms": ordered[min(len(ordered) - 1, int(.9 * len(ordered)))],
        "max_ms": max(values),
    }


def measure(methods, *, tokens: int, extents: int, mismatch: str,
            native_array: bool, iterations: int):
    samples = {name: {"wall": [], "thread_cpu": []} for name in methods}
    canonical = None
    with patch.object(telemetry.time, "time", return_value=.2):
        for iteration in range(iterations + 10):
            outputs = {}
            names = list(methods)
            if iteration % 2:
                names.reverse()
            for name in names:
                audit, request, emitted = fixture(tokens, extents, mismatch, native_array)
                wall_start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
                candidates = methods[name](
                    audit, request, {"context_id": "ctx", "context_epoch": 3},
                )
                elapsed = {
                    "wall": (time.perf_counter_ns() - wall_start) / 1e6,
                    "thread_cpu": (time.thread_time_ns() - cpu_start) / 1e6,
                }
                outputs[name] = (emitted, candidates, list(audit._pending_prefetch_use))
                if iteration >= 10:
                    for clock, value in elapsed.items():
                        samples[name][clock].append(value)
            if outputs["baseline"] != outputs["optimized"]:
                raise AssertionError("first-service evidence or consumed commands changed")
            if len(outputs["optimized"][0]) != extents:
                raise AssertionError("first-service records were lost")
            canonical = json.dumps(outputs["optimized"], sort_keys=True)
    return {
        "input_tokens": tokens, "restore_extents": extents,
        "input_mismatch": mismatch != "none", "mismatch_position": mismatch,
        "input_storage": "native_array" if native_array else "list",
        "records_and_consumed_commands_equal": True,
        "canonical_output_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "costs": {
            name: {clock: distribution(values) for clock, values in clocks.items()}
            for name, clocks in samples.items()
        },
    }


def observed_prefix_work(path: Path) -> dict:
    groups = {}
    prefix_hash, bytes_read, rows = hashlib.sha256(), 0, 0
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                raise ValueError("incomplete first-service evidence line")
            prefix_hash.update(line)
            bytes_read += len(line)
            row = json.loads(line)
            if row.get("event") != "beliefkv_prefetch_first_service":
                continue
            rows += 1
            key = (row["context_id"], row["service_context_epoch"], row["request_id"])
            group = groups.setdefault(key, {
                "commands": 0, "targets": 0, "common_tokens": 0, "max_common_tokens": 0,
            })
            group["commands"] += 1
            for target in row.get("target_prefixes", ()):
                common = target["common_input_prefix_tokens"]
                group["targets"] += 1
                group["common_tokens"] += common
                group["max_common_tokens"] = max(group["max_common_tokens"], common)
    repeated = sum(group["common_tokens"] - group["max_common_tokens"]
                   for group in groups.values())
    return {
        "source": str(path.resolve()), "read_bytes": bytes_read,
        "source_sha256": prefix_hash.hexdigest(), "first_service_command_records": rows,
        "first_service_requests": len(groups),
        "requests_with_multiple_target_prefixes": sum(
            group["targets"] > 1 for group in groups.values()
        ),
        "target_prefixes_per_request_histogram": dict(sorted(Counter(
            group["targets"] for group in groups.values()
        ).items())),
        "reported_common_prefix_tokens": sum(group["common_tokens"] for group in groups.values()),
        "repeated_common_prefix_tokens_within_request": repeated,
        "maximum_common_prefix_tokens": max(
            (group["max_common_tokens"] for group in groups.values()), default=0,
        ),
        "semantics": (
            "Groups use the same context/epoch/first-service request. Common-prefix "
            "tokens were compared by the old Python loop; repeated tokens count "
            "shared matching positions, not avoided bytes, elapsed time or "
            "additional proven KV reuse. No token payload is saved."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observed-action-use", type=Path)
    args = parser.parse_args()
    if args.iterations <= 0:
        parser.error("iterations must be positive")
    if Path(telemetry.__file__).resolve() != ROOT / SOURCE:
        raise RuntimeError("benchmark imported a different worktree")
    baseline = subprocess.check_output(
        ["git", "show", f"{args.baseline_revision}:{SOURCE.as_posix()}"],
        cwd=ROOT, text=True,
    )
    current = (ROOT / SOURCE).read_text()
    methods = {
        "baseline": load_method(baseline, args.baseline_revision),
        "optimized": load_method(current, str(ROOT / SOURCE)),
    }
    report = {
        "scope": (
            "CPU first-service evidence with native-array prefix snapshots and "
            "materialized request-prefix identity fixtures; no writer thread, "
            "allocator, DMA, CUDA or end-to-end throughput measurement."
        ),
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": hashlib.sha256(baseline.encode()).hexdigest(),
        "optimized_source_sha256": hashlib.sha256(current.encode()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "iterations_per_case": args.iterations, "warmup_iterations": 10, "deployed": False,
        "cases": [
            measure(methods, tokens=tokens, extents=extents, mismatch=mismatch,
                    native_array=native_array, iterations=args.iterations)
            for tokens in (1024, 8192, 32768)
            for extents in (1, 16) for mismatch in ("none", "first", "middle", "last")
            for native_array in (False, True)
        ],
    }
    if args.observed_action_use is not None:
        report["observed_prefix_work"] = observed_prefix_work(args.observed_action_use)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for case in report["cases"]:
        before = case["costs"]["baseline"]["wall"]["mean_ms"]
        after = case["costs"]["optimized"]["wall"]["mean_ms"]
        print(f"tokens={case['input_tokens']} extents={case['restore_extents']} "
              f"mismatch={case['mismatch_position']} storage={case['input_storage']}: "
              f"{before:.4f} -> {after:.4f} ms")
    if "observed_prefix_work" in report:
        print(json.dumps(report["observed_prefix_work"], indent=2))


if __name__ == "__main__":
    main()
