#!/usr/bin/env python3
"""Compare exact prefix and eviction evidence on identical CPU writer inputs."""

from __future__ import annotations

import argparse
from array import array
import ast
from collections import Counter, OrderedDict
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock
import time
from types import SimpleNamespace as NS
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.runtime import v0520_native_telemetry as telemetry

SOURCE = Path("beliefkv/runtime/v0520_native_telemetry.py")
METHODS = (
    "_write_context_prefix", "_write_block_request_probe",
    "_write_block_eviction", "_remove_block_eviction_index",
    "_remove_pending_block_totals", "_write_status",
    "_match_path", "_match_paths", "on_launch",
)


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
    audit._pending_block_counts = Counter()
    audit._pending_block_latest_units = Counter()
    audit._block_eviction_index = {}
    audit._block_eviction_routes = {}
    audit._block_eviction_route_lengths = {}
    audit._block_eviction_lengths = {}
    audit._block_eviction_sorted_lengths = {}
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
    if scenario.startswith("block_pressure_"):
        return pressure_fixture(audit, prior, scenario, record)
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
    if scenario == "block_eviction":
        return audit, [{
            "key_path": (array("q", prior),), "pool": "full", "node_id": 1,
            "units": tokens // 16, "ts_ms": 1., "extra_key": None, "cache_salt": None,
        }], "_write_block_eviction"
    if scenario in ("block_many_branches", "block_many_unrelated", "block_many_matches"):
        for branch in range(32):
            branch_tokens = array("q", prior)
            if scenario != "block_many_matches":
                branch_tokens[tokens // 4] = -branch - 1
            for index in range(16):
                length = tokens // 2 + branch * 16 + index + 1
                audit._write_block_eviction({
                    "key_path": (branch_tokens[:length],), "pool": "full",
                    "node_id": branch * 16 + index, "units": tokens // 16, "ts_ms": 1.,
                    "extra_key": None, "cache_salt": None,
                }, StringIO())
        if scenario == "block_many_branches":
            current[tokens // 4] = -1
    else:
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


def pressure_fixture(audit, prior, scenario, record):
    """Seed a large namespaced index outside the timed query or eviction."""
    token_ids = array("q", prior)
    unrelated = array("q", prior)
    unrelated[0] = -1
    lengths = list(range(len(prior) // 2, len(prior)))
    namespace = audit._namespace_digest(None, None)
    unrelated_digests = audit._prefix_digests(None, None, unrelated, lengths)
    matching_digests = audit._prefix_digests(None, None, token_ids, lengths)
    boundary = lengths[0]
    for position, length in enumerate(lengths[::2] + lengths[1::2], 1):
        matched = scenario == "block_pressure_partial" and length % 1024 == 0
        digests = matching_digests if matched else unrelated_digests
        digest = digests[length]
        identity = ("full", namespace, length, digest)
        index_key = (namespace, length, digest)
        route = (namespace, boundary, digests[boundary])
        audit._pending_block_evictions[identity] = {
            "first_eviction_id": position, "last_eviction_id": position,
            "first_eviction_ts_ms": 1., "last_eviction_ts_ms": 1.,
            "last_evicted_units": 1, "eviction_count": 1,
            "evicted_units_total": 1, "evicted_bytes_total": 20480,
        }
        audit._block_eviction_index[index_key] = {identity}
        audit._block_eviction_routes[index_key] = route
        audit._block_eviction_route_lengths.setdefault(route, Counter())[length] += 1
        audit._block_eviction_length_refs[(namespace, length)] += 1
        audit._block_eviction_lengths.setdefault(namespace, OrderedDict())[length] = None
    audit._block_eviction_sorted_lengths[namespace] = lengths
    audit._next_block_eviction_id = audit._host_block_eviction_count = len(lengths)
    audit._block_attribution_counts["full_evictions"] = len(lengths)
    audit._block_attribution_evicted_units["full"] = len(lengths)
    audit._pending_block_counts["full"] = len(lengths)
    audit._pending_block_latest_units["full"] = len(lengths)
    if scenario == "block_pressure_eviction":
        audit._block_eviction_index_limit = len(lengths)
        return audit, [{
            "key_path": (token_ids,), "pool": "full", "node_id": len(prior),
            "units": 1, "ts_ms": 2., "extra_key": None, "cache_salt": None,
        }], "_write_block_eviction"
    return audit, [{**record, "input_ids": token_ids}], "_write_block_request_probe"


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
            if name == "optimized" and audit._block_eviction_sorted_lengths != {
                namespace: sorted(lengths)
                for namespace, lengths in audit._block_eviction_lengths.items()
            }:
                raise AssertionError("sorted prefix index diverged from live lengths")
            if name == "optimized":
                blocks, units = Counter(), Counter()
                for identity, pending in audit._pending_block_evictions.items():
                    blocks[identity[0]] += 1
                    units[identity[0]] += int(pending["last_evicted_units"])
                if (
                    audit._pending_block_counts != blocks
                    or audit._pending_block_latest_units != units
                ):
                    raise AssertionError("pending status totals diverged from live records")
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


class StatusSink:
    """Keep the real status serialization in memory for the CPU comparison."""

    def __init__(self):
        self.text = ""

    def __truediv__(self, filename):
        return self

    def with_suffix(self, suffix):
        return self

    def write_text(self, text, *, encoding):
        self.text = text

    def replace(self, destination):
        return self


def status_publication_case(writers: dict, *, entries: int, iterations: int) -> dict:
    variants = {}
    with TemporaryDirectory(prefix="beliefkv-status-cpu-") as directory:
        for name, writer in writers.items():
            audit = writer(Path(directory) / name)
            audit.close()
            sink = StatusSink()
            audit.directory = sink
            for index in range(entries):
                pool = "mamba" if index % 4 == 0 else "full"
                units = 1 if pool == "mamba" else 256 + index % 1024
                audit._pending_block_evictions[pool, "namespace", index, str(index)] = {
                    "last_evicted_units": units,
                }
                audit._pending_block_counts[pool] += 1
                audit._pending_block_latest_units[pool] += units
            variants[name] = audit, sink
        samples = {
            name: {"wall": [], "thread_cpu": []} for name in variants
        }
        for iteration in range(iterations + 10):
            order = list(variants)
            if iteration % 2:
                order.reverse()
            outputs = {}
            for name in order:
                audit, sink = variants[name]
                wall_start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
                audit._write_status()
                elapsed = {
                    "wall": (time.perf_counter_ns() - wall_start) / 1e6,
                    "thread_cpu": (time.thread_time_ns() - cpu_start) / 1e6,
                }
                if iteration >= 10:
                    for clock, value in elapsed.items():
                        samples[name][clock].append(value)
                outputs[name] = json.loads(sink.text)
                outputs[name].pop("snapshot_ts_ms")
                outputs[name]["background_writer_timing"].pop("snapshot_ts_ms")
            if outputs["baseline"] != outputs["optimized"]:
                raise AssertionError("status evidence changed beyond snapshot timestamps")
    return {
        "scenario": "status_publication", "pending_entries": entries,
        "parsed_status_equal_except_snapshot_timestamps": True,
        "costs": {
            name: {clock: distribution(values) for clock, values in clocks.items()}
            for name, clocks in samples.items()
        },
        "scope": (
            "Whole status construction and JSON serialization with identical pending "
            "records and real telemetry state; in-memory publication excludes disk, "
            "concurrent scheduler/GIL contention and GPU/JCT performance."
        ),
    }


def first_service_match_path_case(writers, *, depth, scenario, iterations):
    states = {}
    for name, writer in writers.items():
        nodes = {0: NS(id=0, creation_time=0., parent=None)}
        for index in range(1, depth):
            nodes[index] = NS(id=index, creation_time=float(index), parent=nodes[index - 1])
        leaf = depth - 1
        anchors = {
            "same_anchor": (leaf, leaf, leaf),
            "two_anchors": (leaf, max(0, leaf - 1), leaf),
            "distinct_anchors": (leaf, max(0, leaf - 1), max(0, leaf - 2)),
            "missing_anchor": (depth, depth, depth),
            "no_host_hit": (leaf, leaf, leaf),
        }[scenario]
        audit = writer.__new__(writer)
        audit._cache = NS(tree_core=NS(node_by_id=nodes.__getitem__))
        audit._state_lock = Lock()
        audit._cache_request_evidence = {"all": Counter()}
        audit._host_evictions = Counter()
        audit._poll_restore_wait = lambda: None
        audit._record_prefetch_first_service = lambda *args: ()
        audit._queue_block_reaccess_probe = lambda *args: None
        audit._queue_context_prefix = lambda *args, **kwargs: None
        audit.record_host_pool_usage = lambda *args: None
        records = []
        audit._emit = lambda stream, record, records=records: records.append((stream, record))
        request = NS(
            rid="request", beliefkv_metadata={
                "root_workflow_id": "workflow", "invocation_id": "parent",
                "context_id": "context", "context_epoch": 1,
            },
            last_node=anchors[0], last_host_node=anchors[1], best_match_node=anchors[2],
            origin_input_ids=array("q", range(1024)), output_ids=[],
            cached_tokens_device=1020,
            cached_tokens_host=0 if scenario == "no_host_hit" else 4,
            mamba_host_hit_length=0, extend_input_len=4,
            sampling_params=NS(max_new_tokens=128),
        )
        batch = NS(
            forward_mode=NS(is_extend=lambda: True, is_decode=lambda: False),
            launch_ts=1., forward_iter=1, reqs=[request],
            prefix_lens=[1020], extend_lens=[4],
        )
        states[name] = audit, batch, records
    samples = {
        name: {"wall": [], "thread_cpu": []} for name in writers
    }
    # Only the record timestamps are fixed; both measured clocks remain live.
    with patch.object(telemetry.time, "time", return_value=1.):
        for iteration in range(iterations + 10):
            order = list(writers)
            if iteration % 2:
                order.reverse()
            signatures = {}
            for name in order:
                audit, batch, records = states[name]
                audit._pending = {"request": 1000.}
                audit._active = set()
                audit._completed = set()
                audit._reported_output_tokens = {}
                audit._launched = {}
                audit._sequence = 0
                audit._cache_request_evidence["all"].clear()
                records.clear()
                wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                audit.on_launch(batch)
                elapsed_cpu = (time.thread_time_ns() - cpu) / 1e6
                elapsed_wall = (time.perf_counter_ns() - wall) / 1e6
                signatures[name] = (
                    records[:], dict(audit._launched),
                    dict(audit._cache_request_evidence["all"]),
                )
                if iteration >= 10:
                    samples[name]["wall"].append(elapsed_wall)
                    samples[name]["thread_cpu"].append(elapsed_cpu)
            if signatures["baseline"] != signatures["optimized"]:
                raise AssertionError("first-service path evidence or launch state changed")
    return {
        "scenario": scenario, "path_depth": depth,
        "costs": {
            name: {clock: distribution(values) for clock, values in clocks.items()}
            for name, clocks in samples.items()
        },
        "records_and_service_state_equal": True,
        "scope": (
            "Complete real on_launch for one first prefill with CPU node ancestry. "
            "Transfer proof, prefix-probe queueing, usage publication and CUDA probes "
            "are excluded identically; no concurrent writer, allocator, GPU or JCT measurement."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observed-prefix-evidence", type=Path)
    parser.add_argument("--pressure-cases", action="store_true")
    parser.add_argument("--status-publication-only", action="store_true")
    parser.add_argument("--first-service-match-paths", action="store_true")
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
        "benchmark_started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "CPU status publication and pending-counter mutation with unchanged evidence; "
            "no disk, concurrent writer/scheduler, GIL contention or GPU/JCT measurement."
            if args.status_publication_only else
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
    }
    if args.observed_prefix_evidence is not None:
        report["observed_prefix_work"] = observed_prefix_work(args.observed_prefix_evidence)
    if args.first_service_match_paths:
        report["scope"] = (
            "Complete first-prefill on_launch CPU comparison on identical native "
            "ancestry and request records; fixed record timestamps, alternating "
            "baseline/current order, no CUDA or end-to-end benefit measurement."
        )
        report["cases"] = [
            first_service_match_path_case(
                writers, depth=depth, scenario=scenario, iterations=args.iterations,
            )
            for depth in (24, 64, 185, 257)
            for scenario in (
                "same_anchor", "two_anchors", "distinct_anchors",
                "missing_anchor", "no_host_hit",
            )
        ]
    elif args.status_publication_only:
        report["cases"] = [
            status_publication_case(writers, entries=entries, iterations=args.iterations)
            for entries in (0, 1024, 32768)
        ] + [
            measure(writers, tokens=1024, scenario=scenario, iterations=args.iterations)
            for scenario in ("block_eviction", "block_many_matches")
        ]
    else:
        report["cases"] = [
            measure(writers, tokens=tokens, scenario=scenario, iterations=args.iterations)
            for tokens in (1024, 8192, 32768)
            for scenario in (
                "context_append", "context_middle_diverge", "context_late_diverge",
                "context_shorter", "context_array", "block_list", "block_array",
                "block_other_namespace", "block_many_branches", "block_many_unrelated",
                "block_many_matches", "block_eviction",
            )
        ]
    if args.pressure_cases and not args.status_publication_only and not args.first_service_match_paths:
        report["cases"].extend(
            measure(writers, tokens=tokens, scenario=scenario, iterations=args.iterations)
            for tokens in (8192, 32768)
            for scenario in (
                "block_pressure_unrelated", "block_pressure_partial", "block_pressure_eviction",
            )
        )
        report["pressure_fixture_scope"] = (
            "Directly seeded 4096/16384-entry namespaced CPU indexes. "
            "Setup and derived-index checks are outside measurement. "
            "Query and bounded-expiry mutation outputs and evidence states are compared."
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report["benchmark_finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for case in report["cases"]:
        before = case["costs"]["baseline"]["wall"]["mean_ms"]
        after = case["costs"]["optimized"]["wall"]["mean_ms"]
        label = (
            f"depth={case['path_depth']}" if "path_depth" in case else
            f"pending={case['pending_entries']}" if "pending_entries" in case
            else f"tokens={case['prior_input_tokens']}"
        )
        print(f"{label} {case['scenario']}: "
              f"{before:.4f} -> {after:.4f} ms")


if __name__ == "__main__":
    main()
