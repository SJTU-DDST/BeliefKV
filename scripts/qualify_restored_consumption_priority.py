#!/usr/bin/env python3
"""Compare restored-checkpoint admission against the prior runtime on CPU."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests import test_tool_predictive_transfers as fixtures
from tests.test_sglang_v0520_runtime import req, select


def exercise(runtime_class, *, slots, source, observation, ordinary_admissions):
    with patch.object(fixtures, "NativeAdmissionRuntime", runtime_class):
        runtime, _, _, _, _, _ = fixtures.locked_runtime()
    restored = fixtures.submit_restored_request(runtime)
    ordinary = req("ordinary")
    runtime.register_visible_request(ordinary)
    runtime._visible_since[ordinary.rid] = time.monotonic() - 11.
    runtime._prefetch_priority_normal_admissions = ordinary_admissions
    try:
        with patch.object(runtime, "_causal_rank", side_effect=lambda req, index, ranks: (0, 0, index)), \
             patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
                 slots, 1024, source=source,
             )), \
             patch.object(runtime, "_read_request_reentry", return_value=observation) as inspect:
            selected = select(runtime, [ordinary, restored]).candidates[0]
            assert runtime.physical_ledger.pending_count == 0
            witness = {
                "selected": "restored" if selected is restored else "ordinary",
                "refreshed_checkpoint": inspect.call_count > 0,
                "ordinary_quota": getattr(runtime, "_prefetch_priority_complete_ready_quota", None),
                "physical_actions": 0,
            }
            if selected is restored:
                runtime.on_prefill_candidate_result(restored, admitted=True, result="ok")
                assert runtime._prefetch_priority_normal_admissions == 0
                assert select(runtime, [ordinary, restored]).candidates[0] is ordinary
                witness["ordinary_turn_after_restored_admission"] = True
            return witness
    finally:
        runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve existing qualification")
    source_path = "beliefkv/runtime/sglang_v0520_runtime.py"
    baseline_source = subprocess.run(
        ["git", "show", f"{args.baseline_revision}:{source_path}"],
        cwd=ROOT, check=True, stdout=subprocess.PIPE,
    ).stdout
    baseline = ModuleType("qualified_prior_consumption_runtime")
    baseline.__file__ = str(ROOT / source_path)
    sys.modules[baseline.__name__] = baseline
    exec(compile(baseline_source, baseline.__file__, "exec"), vars(baseline))
    complete = {"missing_full_tokens": 0, "missing_mamba_slots": 0,
                "device_checkpoint_tokens": 100}
    cases = []
    for label, changes, expected_current in (
        ("complete_native_one_slot", {}, "restored"),
        ("complete_native_four_slots", {"slots": 4, "ordinary_admissions": 3}, "restored"),
        ("partial_full", {"observation": {**complete, "missing_full_tokens": 1}}, "ordinary"),
        ("missing_mamba", {"observation": {**complete, "missing_mamba_slots": 1}}, "ordinary"),
        ("unknown_checkpoint", {"observation": None}, "ordinary"),
        ("no_frontier", {"slots": 0}, "ordinary"),
        ("unknown_capacity", {"source": "legacy_capacity_unavailable"}, "ordinary"),
    ):
        inputs = {"slots": 1, "source": "native_next_prefill",
                  "observation": complete, "ordinary_admissions": 1, **changes}
        old = exercise(baseline.NativeAdmissionRuntime, **inputs)
        current = exercise(NativeAdmissionRuntime, **inputs)
        assert old["selected"] == "ordinary"
        assert current["selected"] == expected_current
        if expected_current == "restored":
            assert current["refreshed_checkpoint"]
            assert current["ordinary_turn_after_restored_admission"]
        cases.append({"case": label, "inputs": inputs, "baseline": old, "current": current})
    prerequisite = args.qualification.read_bytes()
    report = {
        "schema_version": 1, "created_at": datetime.now().astimezone().isoformat(),
        "scope": "CPU causal queue/lease fixtures; no GPU throughput or JCT claim",
        "baseline_revision": args.baseline_revision,
        "baseline_runtime_sha256": hashlib.sha256(baseline_source).hexdigest(),
        "current_runtime_sha256": hashlib.sha256((ROOT / source_path).read_bytes()).hexdigest(),
        "prerequisite": str(args.qualification),
        "prerequisite_sha256": hashlib.sha256(prerequisite).hexdigest(),
        "cases": cases,
        "semantics": [
            "Actual prior/current runtime classes use the same graph, requests and settled-lease fixture.",
            "A fresh native checkpoint observation and a valid next-prefill frontier are both required.",
            "At least one ordinary admission remains between complete restored admissions.",
            "Partial/unknown dependencies and unavailable capacity retain the four-admission policy.",
            "Only queue order changes; native allocation, batch filling and execution dependencies remain.",
            "V50 remains frozen; these candidates require cold deployment and live outcome validation.",
        ],
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "cases": len(cases),
                      "changed_complete_frontiers": 2, "physical_actions": 0}))


if __name__ == "__main__":
    main()
