"""Measure the JOIN eligibility check only, using a fixed synthetic CPU fixture."""

import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.reference_thunder_admission import ReferenceThunderAdmission
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey


def adapter(peer_tokens):
    runtime = NS(
        internal_comparison_mode="full", _custom_kv_management=True,
        _paper_baseline=None, enable_frontier_restore=False, enable_execution_handoff=False,
        agent_join_free_admission=True,
        graph=NS(invocations={}, joins={}),
    )
    live = ReferenceThunderAdmission(runtime)
    live.attach(NS(tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=805198)))
    key = PrefillCandidateKey("request", "workflow", "parent", "context", 0, 0, "session", 1)
    runtime.graph.invocations["parent"] = NS(
        workflow_id="workflow", context_id="context", state=InvocationState.WAIT_JOIN,
        join_id="join", active_tool_calls={}, blocking_child_ids=set(),
    )
    runtime.graph.joins["join"] = NS(
        workflow_id="workflow", satisfied=False,
        waiter_invocation_ids={"parent"}, released_waiter_ids=set(),
    )
    live.latest["parent"] = key
    live.policy.arrive("parent", 100000, now=0.)
    live.policy.completed("parent", 100000, now=.1)
    live.policy._pause(live.policy.programs["parent"], .2)
    for index in range(32):
        name = "other-agent-" + str(index)
        live.policy.arrive(name, peer_tokens, now=.3)
        live.policy.programs[name].active = True
    return live, key


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve JOIN CPU cost evidence")
    fixtures = {}
    for label, peer_tokens, already_active, expected in (
        ("already_active", 20000, True, True),
        ("new_free_join_grant", 20000, False, True),
        ("insufficient_complete_budget", 22000, False, False),
    ):
        live, key = adapter(peer_tokens)
        samples = []
        for index in range(2100):
            live.policy.programs["parent"].active = already_active
            before = time.thread_time_ns()
            observed = live.claim_restore(key, source="join_ticket")
            elapsed = (time.thread_time_ns() - before) / 1000.
            if observed is not expected:
                raise ValueError("CPU cost fixture changed its eligibility outcome")
            if index >= 100:
                samples.append(elapsed)
        fixtures[label] = {
            "programs": len(live.policy.programs), "samples": len(samples),
            "thread_cpu_us": {
                "median": statistics.median(samples), "p99": sorted(samples)[1979],
                "max": max(samples),
            },
        }
        live.close()
    result = {
        "scope": "claim_restore CPU call only; synthetic fixed33-program state",
        "fixtures": fixtures,
        "eligibility_reset_between_calls_for_timing_only": True,
        "sources": {
            str(path.relative_to(ROOT)): sha256_file(path) for path in (
                Path(__file__), ROOT / "beliefkv/runtime/reference_thunder_admission.py",
                ROOT / "beliefkv/runtime/paper_baseline.py",
                ROOT / "beliefkv/policy/paper_baselines.py",
            )
        },
        "whole_controller_cost_measured": False,
        "gpu_or_end_to_end_performance_verified": False,
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(fixtures, indent=2))


if __name__ == "__main__":
    main()
