#!/usr/bin/env python3
"""Compare complete CPU prefetch evidence with optional token diagnostics."""

from __future__ import annotations

from array import array
import argparse
import ast
from collections import OrderedDict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from types import MethodType, SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime import v0520_native_telemetry as telemetry
from beliefkv.runtime.sglang_v0520_physical import PhysicalActionCompleted

SOURCE = Path("beliefkv/runtime/v0520_native_telemetry.py")


def load_methods(source, label):
    cls = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "NativeReactiveTelemetry"
    )
    names = {
        "on_prefetch_issued", "_track_prefetch_use", "on_verified_action_ack",
        "_record_prefetch_first_service",
    }
    namespace = dict(vars(telemetry))
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names),
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), label, "exec"), namespace)
    return {name: namespace[name] for name in names}


def native_key_class(engine):
    path = engine / "python/sglang/srt/mem_cache/radix_cache.py"
    source = path.read_bytes()
    cls = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "RadixKey"
    )
    namespace = dict(vars(telemetry))
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        cls,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["RadixKey"], {"path": str(path), "sha256": hashlib.sha256(source).hexdigest()}


def fixture(methods, key_class, *, tokens, extents, diagnostics):
    nodes = {0: NS(id=0, parent=None, key=key_class(array("q")), creation_time=0.)}
    actions = []
    for index in range(1, extents + 1):
        start, end = tokens * (index - 1) // extents, tokens * index // extents
        nodes[index] = NS(
            id=index, parent=nodes[index - 1], creation_time=float(index),
            key=key_class(array("q", range(start, end)), extra_key="ns", cache_salt="salt"),
            component_data=(NS(value=object()), NS(), NS(value=None)),
        )
        amount = (end - start) * 16
        actions.append(PhysicalActionCompleted(
            f"restore-{index}", "PREFETCH_GPU", "ctx", 2, (index,),
            (("kv", amount),), amount, "join_ticket",
        ))
    audit = telemetry.NativeReactiveTelemetry.__new__(telemetry.NativeReactiveTelemetry)
    audit._cache = NS(tree_core=NS(node_by_id=nodes.__getitem__))
    audit._prefetch_prefix_diagnostics = diagnostics
    records = []
    audit._emit = lambda kind, record: records.append((kind, record))
    for name, method in methods.items():
        setattr(audit, name, MethodType(method, audit))
    request = NS(
        rid="next", last_node=extents, best_match_node=extents,
        origin_input_ids=list(range(tokens + 1)), prefix_indices=range(tokens),
        cached_tokens_device=tokens, cached_tokens_host=0, mamba_host_hit_length=0,
        extra_key="ns", cache_salt="salt",
    )
    return audit, request, actions, records


def distribution(values):
    ordered = sorted(values)
    return {
        "p50_ms": statistics.median(values), "mean_ms": statistics.mean(values),
        "p90_ms": ordered[min(len(ordered) - 1, int(len(ordered) * .9))],
        "raw_ms": values,
    }


def measure(methods, key_class, *, tokens, extents, diagnostics, samples):
    fixtures = {
        name: fixture(variant, key_class, tokens=tokens, extents=extents, diagnostics=diagnostics)
        for name, variant in methods.items()
    }
    common = next(iter(fixtures.values()))
    for name, variant in tuple(fixtures.items()):
        variant[0]._cache = common[0]._cache
        fixtures[name] = (variant[0], common[1], common[2], variant[3])
    del variant
    costs = {
        name: {phase: {"thread_cpu": [], "wall": []} for phase in ("issued", "ack", "first_service")}
        for name in fixtures
    }
    snapshots = {}
    canonical = None
    with patch.object(telemetry.time, "time", lambda: .2):
        for sample in range(samples + 10):
            outputs = {}
            order = list(fixtures)
            if sample % 2:
                order.reverse()
            for name in order:
                audit, request, actions, records = fixtures[name]
                audit._pending_prefetch_use = OrderedDict()
                audit._unacked_prefetch_use = {}
                audit._prefetch_use_before_ack = {}
                audit._prefetch_ack_times = OrderedDict()
                records.clear()
                phases = (
                    ("issued", lambda: [audit.on_prefetch_issued(action) for action in actions]),
                    ("ack", lambda: [audit.on_verified_action_ack(action) for action in actions]),
                    ("first_service", lambda: audit._record_prefetch_first_service(
                        request, {"context_id": "ctx", "context_epoch": 3},
                    )),
                )
                for phase, run in phases:
                    cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                    result = run()
                    elapsed_cpu = (time.thread_time_ns() - cpu) / 1e6
                    elapsed_wall = (time.perf_counter_ns() - wall) / 1e6
                    if sample >= 10:
                        costs[name][phase]["thread_cpu"].append(elapsed_cpu)
                        costs[name][phase]["wall"].append(elapsed_wall)
                    if phase == "issued":
                        assert len(audit._unacked_prefetch_use) == extents
                        assert not audit._prefetch_ack_times and not records
                        snapshots[name] = {
                            "arrays": sum(
                                len(state[3]) for state in audit._pending_prefetch_use.values()
                            ),
                            "token_bytes": sum(
                                len(ids) * ids.itemsize
                                for state in audit._pending_prefetch_use.values()
                                for ids, _, _ in state[3].values()
                            ),
                        }
                    elif phase == "ack":
                        assert not audit._unacked_prefetch_use
                        assert len(records) == extents
                        assert all(kind == "action_ack" for kind, _ in records)
                    else:
                        assert not result and not audit._pending_prefetch_use
                assert len(records) == 2 * extents
                normalized = []
                for kind, record in records:
                    record = dict(record)
                    if kind == "action_use":
                        assert record["full_node_reused"] is True
                        record.pop("target_prefix_diagnostics", None)
                        if not diagnostics:
                            record.pop("target_prefixes")
                    else:
                        assert kind == "action_ack"
                    normalized.append((kind, record))
                outputs[name] = {
                    "records": normalized,
                    "ack_times": dict(audit._prefetch_ack_times),
                    "pending": len(audit._pending_prefetch_use),
                    "unacked": len(audit._unacked_prefetch_use),
                }
            if outputs["baseline"] != outputs["candidate"]:
                raise AssertionError("native reuse proof, ACK lifecycle or non-diagnostic record changed")
            canonical = json.dumps(outputs["candidate"], sort_keys=True)
    return {
        "tokens": tokens, "extents": extents, "candidate_prefix_diagnostics": diagnostics,
        "native_reuse_records_and_ack_lifecycle_equal": True,
        "ignored_record_fields": (
            ["target_prefix_diagnostics"] if diagnostics
            else ["target_prefix_diagnostics", "target_prefixes"]
        ),
        "canonical_proof_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "snapshots_by_arm": snapshots,
        "costs": {
            name: {
                phase: {clock: distribution(values) for clock, values in clocks.items()}
                for phase, clocks in phases.items()
            }
            for name, phases in costs.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--tokens", type=int, nargs="+", default=[8192, 32768, 131072, 262144])
    parser.add_argument("--extents", type=int, nargs="+", default=[1, 16, 42])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or min([args.samples, *args.tokens, *args.extents]) <= 0:
        parser.error("positive dimensions and a fresh output are required")
    baseline = subprocess.check_output(
        ["git", "show", f"{args.baseline}:{SOURCE.as_posix()}"], cwd=ROOT, text=True,
    )
    current = (ROOT / SOURCE).read_text()
    paths = [
        SOURCE, Path("beliefkv/runtime/sglang_v0520_physical.py"),
        Path("scripts/benchmark_prefetch_capture_cpu.py"),
        Path("scripts/run_qwen35_long_repository_ab.sh"),
        Path("scripts/run_qwen35_native_regime_probe.sh"),
    ]
    sources = {str(path): hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    key_class, key_source = native_key_class(args.engine)
    variants = {
        "baseline": load_methods(baseline, args.baseline),
        "candidate": load_methods(current, str(ROOT / SOURCE)),
    }
    cases = [
        measure(variants, key_class, tokens=tokens, extents=extents, diagnostics=diagnostics,
                samples=args.samples)
        for tokens in args.tokens for extents in args.extents for diagnostics in (False, True)
    ]
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest
           for path, digest in sources.items()):
        raise RuntimeError("measured source changed")
    report = {
        "status": "complete_cpu_comparison_not_gpu_or_jct_gain",
        "scope": "Real synchronous issuance, simulated verified ACK, and first-service evidence on CPU",
        "at": datetime.now().astimezone().isoformat(), "baseline_revision": args.baseline,
        "baseline_source_sha256": hashlib.sha256(baseline.encode()).hexdigest(),
        "candidate_source_sha256": sources, "native_key_source": key_source,
        "samples": args.samples, "warmup_samples": 10, "cases": cases,
        "limitations": [
            "Native RadixKey is AST-loaded from the qualified engine and uses array-backed token IDs.",
            "Both variants read the same cache, keys, input and action objects; only telemetry state is separate.",
            "Snapshot statistics retain scalar counts only; they do not extend array lifetime past first service.",
            "No allocator, GPU, DMA, writer thread, file I/O or concurrent key mutation is simulated.",
            "ACKs are explicitly simulated for lifecycle comparison; no real transfer or reuse credit is claimed.",
            "Disabled diagnostics omit token comparison only, preserving native allocation/prefix proof fields.",
            "Independent prefix snapshots and identical diagnostic records remain in opt-in controls.",
            "Controlled token/extent populations are not measured online frequency or a JCT saving.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps([
        {
            "tokens": case["tokens"], "extents": case["extents"],
            "diagnostics": case["candidate_prefix_diagnostics"],
            "issued_p50_ms": {
                name: data["issued"]["thread_cpu"]["p50_ms"]
                for name, data in case["costs"].items()
            },
            "snapshot_bytes": {
                name: data["token_bytes"] for name, data in case["snapshots_by_arm"].items()
            },
        } for case in cases
    ], indent=2))


if __name__ == "__main__":
    main()
