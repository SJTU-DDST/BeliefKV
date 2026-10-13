#!/usr/bin/env python3
"""Qualify free-list observations against native static allocator lifecycles."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.runtime.sglang_v0520_observer import observe_static_full_mamba_headroom
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_observer import _static_cache


def load_module(name, source, filename):
    module = ModuleType(name)
    module.__file__ = str(filename)
    sys.modules[name] = module
    exec(compile(source, str(filename), "exec"), vars(module))
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve existing qualification")
    allocator_root = args.engine_root / "python/sglang/srt/mem_cache/allocator"
    sources = {
        name: (allocator_root / f"{name}.py").read_bytes()
        for name in ("base", "token", "mamba")
    }
    base = load_module("qualified_native_base", sources["base"], allocator_root / "base.py")
    token_ast = ast.parse(sources["token"], filename=str(allocator_root / "token.py"))
    imports = [
        node for node in token_ast.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "sglang.srt.mem_cache.allocator.base"
    ]
    assert len(imports) == 1
    assert [(item.name, item.asname) for item in imports[0].names] == [
        ("BaseTokenToKVPoolAllocator", None),
    ]
    imports[0].module = base.__name__
    token = load_module("qualified_native_token", token_ast, allocator_root / "token.py")
    mamba = load_module("qualified_native_mamba", sources["mamba"], allocator_root / "mamba.py")
    observer_path = "beliefkv/runtime/sglang_v0520_observer.py"
    baseline_source = subprocess.run(
        ["git", "show", f"{args.baseline_revision}:{observer_path}"],
        cwd=ROOT, check=True, stdout=subprocess.PIPE,
    ).stdout
    baseline = load_module("qualified_baseline_observer", baseline_source, observer_path)
    import torch

    cases = []
    for deferred, reserved in ((False, False), (True, False), (False, True), (True, True)):
        cache = _static_cache()
        hybrid = cache.token_to_kv_pool_allocator._kvcache
        hybrid.full_kv_pool.device = cache.req_to_token_pool.mamba_pool.device = "cpu"
        full = token.TokenToKVPoolAllocator(12, torch.float16, "cpu", hybrid, True)
        state = mamba.MambaSlotAllocator(3, "cpu")
        cache.token_to_kv_pool_allocator = full
        cache.req_to_token_pool.mamba_allocator = state
        cache.req_to_token_pool.available_size = lambda: 3
        held = full.alloc(4)
        if deferred:
            full.free_group_begin()
            full.free(held[:2])
        if reserved:
            state.alloc_group_begin(2)
        pending = full.free_group
        iterator = state._alloc_iter
        old = baseline.observe_static_full_mamba_headroom(cache)
        current = observe_static_full_mamba_headroom(cache)
        assert old.observable is (not deferred and not reserved)
        assert current.observable and not current.physical_actions_supported
        assert current.device_full_free_tokens == 8
        assert current.device_mamba_free_slots == (1 if reserved else 3)
        assert full.free_group is pending and state._alloc_iter is iterator
        assert current.full_frees_deferred is deferred
        assert current.mamba_batch_reserved is reserved
        runtime = NativeAdmissionRuntime()
        runtime._native_cache = cache
        runtime._observe_native_admission_capacity(
            running_batch=SimpleNamespace(reqs=[object()]),
            adder=SimpleNamespace(max_running_requests=4, max_prefill_bs=2,
                                  page_size=1, rem_input_tokens=1),
        )
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            baseline.observe_static_full_mamba_headroom,
        ):
            old_budget = runtime._current_residency_budget()
        new_budget = runtime._current_residency_budget()
        assert new_budget.source == "native_next_prefill"
        assert new_budget.reserve_full_tokens == 4
        assert new_budget.reserve_mamba_slots == 2
        assert new_budget.byte_limit == 80 + (0 if reserved else 80)
        if deferred or reserved:
            assert old_budget.source == "legacy_capacity_unavailable"
        else:
            assert old_budget == new_budget
        assert runtime.physical_ledger.pending_count == 0
        assert not runtime.completed_physical_actions
        runtime.close()
        if reserved:
            # Observation must not consume the first native preallocated slot.
            assert state.alloc(1).tolist() == [1]
            state.alloc_group_end()
        if deferred:
            full.free_group_end()
        settled = observe_static_full_mamba_headroom(cache)
        assert settled.device_full_free_tokens == (10 if deferred else 8)
        assert settled.device_mamba_free_slots == (2 if reserved else 3)
        assert not settled.full_frees_deferred and not settled.mamba_batch_reserved
        cases.append({
            "full_deferred": deferred, "mamba_reserved": reserved,
            "baseline": asdict(old), "current": asdict(current),
            "baseline_budget": asdict(old_budget), "current_budget": asdict(new_budget),
            "after_native_group_end": asdict(settled),
            "physical_actions": 0,
        })
    junit = args.junit.read_bytes()
    suites = ET.fromstring(junit)
    assert all(int(suite.get(key, "0")) == 0 for suite in suites
               for key in ("failures", "errors"))
    report = {
        "schema_version": 1, "created_at": datetime.now().astimezone().isoformat(),
        "scope": "CPU native allocator lifecycle; no GPU transfer or JCT gain",
        "engine_root": str(args.engine_root.resolve()),
        "baseline_revision": args.baseline_revision,
        "baseline_observer_sha256": hashlib.sha256(baseline_source).hexdigest(),
        "native_allocator_sources": {
            name: {"path": str(allocator_root / f"{name}.py"),
                   "sha256": hashlib.sha256(source).hexdigest()}
            for name, source in sources.items()
        },
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                observer_path, "beliefkv/runtime/sglang_v0520_runtime.py",
                "tests/test_sglang_v0520_observer.py",
                "tests/test_native_transfer_policy.py",
            )
        },
        "junit": str(args.junit), "junit_sha256": hashlib.sha256(junit).hexdigest(),
        "test_suites": [dict(suite.attrib) for suite in suites],
        "cases": cases,
        "semantics": [
            "Production allocator classes run on CPU; surrounding pool geometry is a named fixture.",
            "Only the token allocator import is redirected to the inspected native base class.",
            "Pending FULL frees and Mamba preallocation are excluded from reported free capacity.",
            "Read-only observations do not close groups, consume iterators or authorize allocation.",
            "Same-call budget reuse does not reuse a snapshot across native mutations.",
            "Unknown layouts and full native page/state dependencies remain authoritative.",
            "V50 remains frozen; this candidate is not deployed during its collection.",
        ],
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "cases": len(cases),
                      "baseline_group_rejections": 3, "physical_actions": 0}))


if __name__ == "__main__":
    main()
