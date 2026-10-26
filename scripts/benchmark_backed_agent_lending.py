#!/usr/bin/env python3
"""Measure added lending checks against actual native CPU radix paths."""

import argparse
from collections import Counter
from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.policy.thunder_agent_migration import ThunderAgentMigration
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_shared_path import distribution


def queued_policy():
    policy = ThunderAgentMigration(10000, retain_blocked=True)
    for index in range(8):
        name = f"blocked-{index}"
        policy.arrive(name, 900, now=index / 100.)
        policy.completed(name, 900, now=index / 100. + .001)
        policy.block(name, "join", now=index / 100. + .002)
    policy.arrive("running", 1500, now=.2)
    policy.begin("running")
    policy.arrive("waiting", 5000, now=.3)
    if policy.allowed("waiting") or not policy.agents["running"].in_flight:
        raise ValueError("fixture does not exercise blocked capacity competition")
    return policy


def measure(engine, *, depth, backing, iterations):
    import pytest

    from tests.test_full_only_native_lifecycle import full_native

    with ExitStack() as stack:
        monkeypatch = stack.enter_context(pytest.MonkeyPatch.context())
        monkeypatch.setenv("SGLANG_SOURCE_CHECKOUT", str(engine))
        cache, _, _, _, _ = full_native.__wrapped__(monkeypatch, NS(param=65536))
        from test_beliefkv_terminal_capacity import insert_session

        snapshots, leaves = {}, []
        for index in range(8):
            name = f"blocked-{index}"
            for offset in range(1, depth + 1):
                leaf, generation = insert_session(
                    cache, [index * 1000 + step for step in range(offset)], name,
                )
            leaves.append(leaf)
            for _ in range(depth + 2):
                result = cache.prepare_beliefkv_spawn_full(
                    session_id=name, session_generation=generation,
                )
                if result["issued"]:
                    cache.writing_check(finish_count=1)
                if result["complete"]:
                    break
            else:
                raise ValueError("native CPU fixture backup did not settle")
            key = PrefillCandidateKey(
                name, "workflow", name, name, 0, 1, name, generation,
            )
            snapshot = NativeAdmissionRuntime._snapshot_key_session_anchors(
                cache, key, reusable_input_tokens=depth,
            )
            if snapshot is None:
                raise ValueError("native fixture has no current session anchors")
            snapshots[name] = snapshot
        if backing == "pending":
            for leaf in leaves:
                leaf.write_through_pending_id = leaf.id
        elif backing == "incomplete":
            for name, snapshot in tuple(snapshots.items()):
                snapshots[name] = replace(snapshot, reusable_input_tokens=depth + 1)
        settings = arm_environment({
            "AGENT_ADMISSION": "thunderagent_retained",
            "AGENT_BLOCKED_LENDING": "backed_demand",
            "EXECUTION_FRONTIER_RESTORE": "0",
            "TOOL_RESTORE_MODE": "execution",
        }, "full")
        stack.enter_context(patch.dict(os.environ, settings))
        directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="bkv-lending-cpu-"))
        runtime = NativeAdmissionRuntime(event_socket_path=str(Path(directory) / "events.sock"))
        stack.callback(runtime.close)
        runtime._native_cache = cache
        # The benchmark supplies an already validated blocked-owner identity.
        # Full graph/epoch guards are exercised by the separate regression.
        runtime._live_parent_pressure_key = lambda key: key.context_id in snapshots
        runtime.snapshot_session_anchors = (
            lambda _cache, *, context_id, context_epoch: snapshots.get(context_id)
        )
        keys = {name: snapshot.key for name, snapshot in snapshots.items()}
        timings = {"thread_cpu": [], "wall": []}
        checks = Counter()
        for sample in range(iterations + 5):
            policy = queued_policy()
            before = runtime.counts["agent_blocked_backing_probe_nodes"]
            cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
            selected = policy.lend_backed_blocked(
                lambda identity: runtime._agent_blocked_prefix_backed(keys[identity]),
                now=1.,
            )
            cpu_ms = (time.thread_time_ns() - cpu) / 1e6
            wall_ms = (time.perf_counter_ns() - wall) / 1e6
            should_lend = backing == "complete" and depth < 64
            if (
                (selected == "waiting") is not should_lend
                or policy.counts["backed_wait_probes"] > 8
                or policy.counts["backed_wait_admitted"] > 1
                or not policy.agents["running"].in_flight
                or policy.used_tokens > policy.capacity
            ):
                raise ValueError("CPU lending changed its declared bounded behavior")
            if sample >= 5:
                timings["thread_cpu"].append(cpu_ms)
                timings["wall"].append(wall_ms)
                checks["probes"] += policy.counts["backed_wait_probes"]
                checks["captured_path_nodes"] += (
                    runtime.counts["agent_blocked_backing_probe_nodes"] - before
                )
                checks["admissions"] += policy.counts["backed_wait_admitted"]
        return {
            "native_nonroot_path_nodes": depth, "backing": backing,
            "iterations": iterations, "checks": dict(checks),
            "cost_ms": {clock: distribution(values) for clock, values in timings.items()},
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.iterations <= 10000:
        parser.error("use a fresh output and 1..10000 iterations")
    patch_path = ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"
    engine = args.engine.resolve()
    before = git_source_state(engine, canonical_patch=patch_path)["canonical_patch"]
    cases = [
        measure(engine, depth=depth, backing=backing, iterations=args.iterations)
        for depth, backing in (
            (8, "complete"), (32, "complete"), (63, "complete"),
            (63, "pending"), (63, "incomplete"), (64, "complete"),
        )
    ]
    after = git_source_state(engine, canonical_patch=patch_path)["canonical_patch"]
    if before != after:
        raise ValueError("native engine changed during CPU measurement")
    report = {
        "engine_tree": before["actual_tree"], "canonical_patch_sha256": before["sha256"],
        "analysis_source_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "scripts/benchmark_backed_agent_lending.py",
                "beliefkv/policy/thunder_agent_migration.py",
                "beliefkv/runtime/sglang_v0520_runtime.py",
                "beliefkv/runtime/sglang_v0520_physical.py",
                "beliefkv/runtime/sglang_v0520_observer.py",
                "tests/test_full_only_native_lifecycle.py",
            )
        },
        "cases": cases,
        "scope": [
            "Incremental lending decision CPU/wall cost, using actual native CPU radix trees and allocators.",
            "Complete-path Host backup uses synchronous copy/event stand-ins; graph blocking/identity classification is supplied as a prevalidated fixture.",
            "Policy construction, cache setup, copies and runtime startup are outside measurement; this is not total controller cost.",
            "Root is an additional path node, so 63 nonroot nodes fit the 64-node probe and 64 nonroot nodes must be rejected.",
            "Measured path does not enqueue copies or authorize physical allocation/eviction.",
            "CPU benchmark is not live GPU timing, numerical equivalence, throughput or JCT acceptance.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({
        "cases": [{
            "path_nodes": row["native_nonroot_path_nodes"] + 1,
            "backing": row["backing"], "checks": row["checks"],
            "thread_cpu_ms": row["cost_ms"]["thread_cpu"],
        } for row in cases],
    }, indent=2))


if __name__ == "__main__":
    main()
