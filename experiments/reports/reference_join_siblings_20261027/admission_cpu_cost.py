"""Measure event-only admission checks with live synthetic streaming records."""

from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState, JoinMode
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.reference_thunder_admission import ReferenceThunderAdmission
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.v0520_opportunity_telemetry import NativeOpportunityTelemetry


def fixture(*, siblings, peer_tokens, writer):
    runtime = NS(
        internal_comparison_mode="full", _custom_kv_management=True,
        _paper_baseline=None, enable_frontier_restore=False, enable_execution_handoff=False,
        agent_join_free_admission=True, coadmit_join_children=siblings,
        _opportunity_writer=writer, visible={},
        graph=NS(invocations={}, joins={}, contexts={}),
    )
    adapter = ReferenceThunderAdmission(runtime)
    adapter.attach(NS(tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=805198)))
    parent = PrefillCandidateKey("parent-request", "workflow", "parent", "parent-context", 0, 0, "parent-session", 1)
    runtime.graph.invocations["parent"] = NS(
        invocation_id="parent", workflow_id="workflow", context_id="parent-context",
        state=InvocationState.WAIT_JOIN, join_id="join",
        active_tool_calls={}, blocking_child_ids=set(),
    )
    members = {"child-0", "child-1"} if siblings else {"child"}
    runtime.graph.joins["join"] = NS(
        join_id="join", workflow_id="workflow", satisfied=False, mode=JoinMode.ALL,
        waiter_invocation_ids={"parent"}, released_waiter_ids=set(),
        member_invocation_ids=members, completed_member_ids=set(),
    )
    adapter.latest["parent"] = parent
    adapter.policy.arrive("parent", 100000, now=0.)
    adapter.policy.completed("parent", 100000, now=.1)
    adapter.policy._pause(adapter.policy.programs["parent"], .2)

    def pending(identity, tokens, *, active):
        key = replace(
            parent, request_id=identity + "-request", invocation_id=identity,
            context_id=identity + "-context", session_id=identity + "-session",
        )
        adapter.policy.arrive(identity, tokens, now=.3)
        adapter.policy.programs[identity].active = active
        adapter.latest[identity] = key
        adapter.requests[key.request_id] = key, .3
        adapter._live_requests[key.request_id] = NS(
            origin_input_ids=range(tokens), output_ids=range(18),
        )
        runtime.visible[key.request_id] = key
        runtime.graph.invocations[identity] = NS(
            workflow_id="workflow", context_id=key.context_id,
            state=InvocationState.READY, active_tool_calls={}, blocking_child_ids=set(),
        )
        runtime.graph.contexts[key.context_id] = NS(workflow_id="workflow", epoch=0)

    for index in range(30 if siblings else 32):
        pending("peer-" + str(index), peer_tokens, active=True)
    if siblings:
        for identity in sorted(members):
            pending(identity, 100000, active=False)
    return adapter, parent


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve event-admission CPU cost evidence")
    results = {}
    for label, siblings, peer_tokens, already_active, expected in (
        ("parent_already_active", False, 20000, True, True),
        ("parent_new_grant", False, 20000, False, True),
        ("parent_insufficient", False, 22000, False, False),
        ("siblings_new_group", True, 20000, False, True),
        ("siblings_insufficient", True, 22000, False, False),
    ):
        directory = output.parent / "cpu_journal" / label
        writer = NativeOpportunityTelemetry(directory)
        adapter, key = fixture(siblings=siblings, peer_tokens=peer_tokens, writer=writer)
        targets = (
            [adapter.policy.programs[name] for name in ("child-0", "child-1")]
            if siblings else [adapter.policy.programs["parent"]]
        )
        samples = []
        try:
            for index in range(550):
                for item in targets:
                    item.active = already_active
                started = time.thread_time_ns()
                if siblings:
                    adapter._coadmit_join("join")
                    observed = all(item.active for item in targets)
                else:
                    observed = adapter.claim_restore(key, source="join_ticket")
                elapsed = (time.thread_time_ns() - started) / 1000.
                if observed is not expected:
                    raise ValueError("event-admission CPU fixture changed its outcome")
                if index >= 50:
                    samples.append(elapsed)
            results[label] = {
                "programs": len(adapter.policy.programs),
                "pending_live_records": len(adapter.requests), "samples": len(samples),
                "thread_cpu_us": {
                    "median": statistics.median(samples),
                    "p99": sorted(samples)[494], "max": max(samples),
                },
                "journal_enabled": True,
            }
        finally:
            adapter.close()
            writer.close()
        status = json.loads(writer.status_path.read_bytes())
        if not status["complete"] or status["error"] is not None:
            raise ValueError("CPU grant-journal writer did not drain successfully")
        results[label]["journal_enqueued"] = status["enqueued"]
        results[label]["journal_sha256"] = sha256_file(writer.path)
        results[label]["writer_status_sha256"] = sha256_file(writer.status_path)
    sources = (
        Path(__file__), ROOT / "beliefkv/runtime/reference_thunder_admission.py",
        ROOT / "beliefkv/runtime/paper_baseline.py", ROOT / "beliefkv/policy/paper_baselines.py",
        ROOT / "beliefkv/runtime/v0520_opportunity_telemetry.py",
    )
    report = {
        "scope": "Event-admission callback CPU only, synthetic 33-program state and real async journal.",
        "fixtures": results,
        "sources": {str(path.relative_to(ROOT)): sha256_file(path) for path in sources},
        "observed_lengths_only": True, "future_context_or_duration_used": False,
        "eligibility_reset_between_calls_for_timing_only": True,
        "background_writer_thread_cpu_included": False,
        "whole_controller_cost_measured": False,
        "gpu_or_end_to_end_performance_verified": False,
    }
    with output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
