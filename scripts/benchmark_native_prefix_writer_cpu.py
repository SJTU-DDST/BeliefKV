#!/usr/bin/env python3
"""Compare exact prefix and eviction evidence on identical CPU writer inputs."""

from __future__ import annotations

import argparse
from array import array
import ast
from collections import Counter, OrderedDict
import hashlib
from io import StringIO
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.runtime import v0520_native_telemetry as telemetry

SOURCE = Path("beliefkv/runtime/v0520_native_telemetry.py")
METHODS = ("_write_context_prefix", "_write_block_request_probe")


def load_writer(source: str, label: str) -> type:
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "NativeReactiveTelemetry")
    namespace = dict(vars(telemetry))
    snapshot = ast.ClassDef(
        name="PrefixWriterSnapshot",
        bases=[ast.Name(id="NativeReactiveTelemetry", ctx=ast.Load())],
        keywords=[],
        body=[node for node in cls.body if isinstance(node, ast.FunctionDef)
              and node.name in (*METHODS, "_request_token_array")],
        decorator_list=[],
    )
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        snapshot,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), label, "exec"), namespace)
    return namespace[snapshot.name]


def fixture(tokens: int, scenario: str, writer: type):
    audit = writer.__new__(writer)
    audit._block_hash_key = b"x" * 32
    audit._context_prefix_history = OrderedDict()
    audit._context_prefix_history_limit = 1024
    audit._context_prefix_counts = Counter()
    audit._host_pool_geometry = {"full": {"bytes_per_unit": 20480}}
    audit._pending_block_evictions = OrderedDict()
    audit._block_eviction_index = {}
    audit._block_eviction_lengths = {}
    audit._block_eviction_length_refs = Counter()
    audit._block_eviction_index_limit = 32768
    audit._max_prefix_lengths_per_probe = 32768
    audit._block_attribution_counts = Counter()
    audit._block_attribution_evicted_units = Counter()
    audit._block_attribution_reused_units = Counter()
    audit._block_attribution_recomputed_units = Counter()
    audit._block_attribution_probe_overflow = 0
    audit._host_block_eviction_count = 0
    audit._next_block_eviction_id = 0
    prior = list(range(tokens))
    current = prior + [tokens, tokens + 1]
    record = {
        "ts_ms": 2., "workflow_id": "w", "invocation_id": "i",
        "context_id": "ctx", "context_epoch": 1, "request_id": "next",
        "session_id": "s", "session_generation": 1,
        "extra_key": None, "cache_salt": None,
        "cached_tokens_device": tokens // 2, "cached_tokens_host": tokens // 4,
        "mamba_host_hit_slots": 0,
    }
    if scenario.startswith("context_"):
        if scenario == "context_middle_diverge":
            current[tokens // 2] = -1
        elif scenario == "context_late_diverge":
            current[tokens - 1] = -1
        elif scenario == "context_shorter":
            current = current[:tokens // 2]
        elif scenario == "context_array":
            prior, current = array("q", prior), array("q", current)
        records = [
            {**record, "_internal_event": "served_context_prefix",
             "request_id": "prior", "context_epoch": 0, "input_ids": prior, "ts_ms": 1.},
            {**record, "_internal_event": "context_prefix_probe", "input_ids": current},
        ]
        return audit, records, "_write_context_prefix"
    for index in range(1, 17):
        length = tokens * index // 16
        audit._write_block_eviction({
            "key_path": (array("q", prior[:length]),), "pool": "full",
            "node_id": index, "units": tokens // 16, "ts_ms": 1.,
            "extra_key": None, "cache_salt": None,
        }, StringIO())
    if scenario == "block_array":
        current = array("q", current)
    elif scenario == "block_other_namespace":
        record["extra_key"] = "other"
    return audit, [{**record, "input_ids": current}], "_write_block_request_probe"


def state(audit) -> dict:
    return {
        "history": [
            [scope, previous["request_id"], previous["context_epoch"],
             hashlib.sha256(previous["tokens"].tobytes()).hexdigest()]
            for scope, previous in audit._context_prefix_history.items()
        ],
        "context_counts": dict(audit._context_prefix_counts),
        "block_counts": dict(audit._block_attribution_counts),
        "reused_units": dict(audit._block_attribution_reused_units),
        "recomputed_units": dict(audit._block_attribution_recomputed_units),
        "pending": list(audit._pending_block_evictions.items()),
        "index": sorted((key, sorted(values))
                        for key, values in audit._block_eviction_index.items()),
        "length_refs": sorted(audit._block_eviction_length_refs.items()),
        "lengths": [(namespace, list(lengths))
                    for namespace, lengths in audit._block_eviction_lengths.items()],
        "probe_overflow": audit._block_attribution_probe_overflow,
    }


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "samples": len(values), "mean_ms": statistics.mean(values),
        "p50_ms": statistics.median(values),
        "p90_ms": ordered[min(len(ordered) - 1, int(.9 * len(ordered)))],
        "max_ms": max(values),
    }


def measure(writers: dict, *, tokens: int, scenario: str, iterations: int) -> dict:
    samples = {name: {"wall": [], "thread_cpu": []} for name in writers}
    canonical = None
    for iteration in range(iterations + 10):
        names = list(writers)
        if iteration % 2:
            names.reverse()
        outputs = {}
        for name in names:
            audit, records, method_name = fixture(tokens, scenario, writers[name])
            output = StringIO()
            method = getattr(audit, method_name)
            wall_start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
            for record in records:
                method(record, output)
            elapsed = {
                "wall": (time.perf_counter_ns() - wall_start) / 1e6,
                "thread_cpu": (time.thread_time_ns() - cpu_start) / 1e6,
            }
            outputs[name] = (output.getvalue(), state(audit))
            if iteration >= 10:
                for clock, value in elapsed.items():
                    samples[name][clock].append(value)
        if outputs["baseline"] != outputs["optimized"]:
            raise AssertionError(f"{scenario}: writer records or evidence state changed")
        canonical = json.dumps(outputs["optimized"], sort_keys=True)
    return {
        "prior_input_tokens": tokens, "scenario": scenario,
        "records_hashes_counters_history_and_pending_equal": True,
        "canonical_output_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "costs": {
            name: {clock: distribution(values) for clock, values in clocks.items()}
            for name, clocks in samples.items()
        },
    }


def observed_prefix_work(path: Path) -> dict:
    digest, bytes_read, probes, common, changed = hashlib.sha256(), 0, 0, [], 0
    events = Counter()
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                raise ValueError("incomplete writer evidence line")
            digest.update(line)
            bytes_read += len(line)
            row = json.loads(line)
            events[row.get("event")] += 1
            if row.get("event") != "context_prefix_reuse_probe":
                continue
            probes += 1
            common.append(row["common_previously_served_input_tokens"])
            changed += row["new_or_changed_prompt_tokens"]
    return {
        "source": str(path.resolve()), "source_sha256": digest.hexdigest(),
        "read_bytes": bytes_read, "event_counts": dict(events),
        "context_probes_with_prior_served_prompt": probes,
        "common_previously_served_input_tokens": sum(common),
        "common_prefix_p50_tokens": statistics.median(common) if common else None,
        "new_or_changed_prompt_tokens": changed,
        "semantics": (
            "Closed-arm writer output describes exact common input-prefix work, "
            "not new KV reuse, avoided recomputation, CPU time or JCT savings."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observed-prefix-evidence", type=Path)
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
    writers = {
        "baseline": load_writer(baseline, args.baseline_revision),
        "optimized": load_writer(current, str(ROOT / SOURCE)),
    }
    report = {
        "scope": (
            "CPU writer processing with exact array prefixes and namespaced eviction "
            "identities; no concurrent writer/scheduler, GIL contention, allocator, "
            "DMA, CUDA or end-to-end throughput measurement."
        ),
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": hashlib.sha256(baseline.encode()).hexdigest(),
        "optimized_source_sha256": hashlib.sha256(current.encode()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "iterations_per_case": args.iterations, "warmup_iterations": 10,
        "deployed": False,
        "cases": [
            measure(writers, tokens=tokens, scenario=scenario, iterations=args.iterations)
            for tokens in (1024, 8192, 32768)
            for scenario in (
                "context_append", "context_middle_diverge", "context_late_diverge",
                "context_shorter", "context_array", "block_list", "block_array",
                "block_other_namespace",
            )
        ],
    }
    if args.observed_prefix_evidence is not None:
        report["observed_prefix_work"] = observed_prefix_work(args.observed_prefix_evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for case in report["cases"]:
        before = case["costs"]["baseline"]["wall"]["mean_ms"]
        after = case["costs"]["optimized"]["wall"]["mean_ms"]
        print(f"tokens={case['prior_input_tokens']} {case['scenario']}: "
              f"{before:.4f} -> {after:.4f} ms")


if __name__ == "__main__":
    main()
