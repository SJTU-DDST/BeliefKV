"""Measure added continuation checks with synthetic state and a real async journal."""

from dataclasses import replace
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
from beliefkv.runtime.reference_free_continuation import ReferenceFreeContinuationAdmission
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.v0520_opportunity_telemetry import NativeOpportunityTelemetry


def fixture(*, peer_tokens, visible_output_tokens, writer):
    runtime = NS(
        internal_comparison_mode="full", _custom_kv_management=True,
        _paper_baseline=None, enable_frontier_restore=False, enable_execution_handoff=False,
        agent_join_free_admission=True, coadmit_join_children=True,
        agent_free_continuation_admission=True,
        _opportunity_writer=writer, visible={}, _join_priority_pending={},
        graph=NS(invocations={}, joins={}, contexts={}),
    )
    adapter = ReferenceFreeContinuationAdmission(runtime)
    adapter.attach(NS(tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=805198)))
    base = PrefillCandidateKey(
        "target-request", "workflow", "target", "target-context", 0, 0, "target-session", 1,
    )

    def pending(name, tokens, *, active):
        key = replace(
            base, request_id=name + "-request", invocation_id=name,
            context_id=name + "-context", session_id=name + "-session",
        )
        adapter.policy.arrive(name, tokens, now=0.)
        adapter.policy.completed(name, tokens, now=.1)
        adapter.policy.arrive(name, tokens, now=.2)
        adapter.policy.programs[name].active = active
        adapter.latest[name] = key
        adapter.requests[key.request_id] = key, .2
        adapter._live_requests[key.request_id] = NS(
            origin_input_ids=range(tokens),
            output_ids=range(visible_output_tokens if active else 0),
        )
        runtime.visible[key.request_id] = key
        runtime.graph.invocations[name] = NS(
            workflow_id="workflow", context_id=key.context_id, state=InvocationState.READY,
            active_tool_calls={}, blocking_child_ids=set(),
        )
        runtime.graph.contexts[key.context_id] = NS(workflow_id="workflow", epoch=0)
        return key

    targets = [pending("candidate-" + str(index), 100000, active=False) for index in range(9)]
    peers = [pending("peer-" + str(index), peer_tokens, active=True) for index in range(24)]
    return adapter, targets[-1], peers


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve continuation CPU cost evidence")
    registration = Path("/home/longhao/experiment/ops/reference_free_continuation_20261027_r1.registration.json")
    plan = json.loads(registration.read_bytes())
    paths = (
        "beliefkv/runtime/reference_free_continuation.py",
        "beliefkv/runtime/reference_thunder_admission.py",
        "beliefkv/runtime/paper_baseline.py", "beliefkv/policy/paper_baselines.py",
        "beliefkv/runtime/v0520_opportunity_telemetry.py",
    )
    sources = {name: sha256_file(ROOT / name) for name in paths}
    if any(plan["source_sha256"][name] != digest for name, digest in sources.items()):
        raise ValueError("measured callbacks differ from the frozen running sources")
    results = {}
    for label, peer_tokens, visible, expected, empty in (
        ("new_grant", 25000, 18, True, None),
        ("full_budget_insufficient", 33000, 18, False, None),
        ("live_growth_refuses_grant", 25000, 5000, False, None),
        ("empty_decode_completion", 33000, 18, None, "completion"),
        ("empty_runtime_events", 33000, 18, None, "events"),
    ):
        writer = NativeOpportunityTelemetry(output.parent / "cpu_journal_verified" / label)
        adapter, preferred, peers = fixture(
            peer_tokens=peer_tokens, visible_output_tokens=visible, writer=writer,
        )
        samples = []
        before_checks = adapter.counts["free_continuation_event_checks"]
        try:
            for index in range(550):
                adapter.policy.programs[preferred.invocation_id].active = False
                if label == "live_growth_refuses_grant":
                    for key in peers:
                        adapter.policy.programs[key.invocation_id].tokens = peer_tokens
                started = time.thread_time_ns()
                if empty == "completion":
                    observed = adapter.completed(NS(reqs=[]), ())
                elif empty == "events":
                    observed = adapter.events(())
                else:
                    observed = adapter._admit_free_continuation(preferred=preferred)
                elapsed = (time.thread_time_ns() - started) / 1000.
                if observed is not expected:
                    raise ValueError("CPU fixture did not preserve its expected admission outcome")
                if index >= 50:
                    samples.append(elapsed)
            if empty and adapter.counts["free_continuation_event_checks"] != before_checks:
                raise ValueError("empty decode callbacks scanned continuation candidates")
            results[label] = {
                "programs": len(adapter.policy.programs), "pending_records": len(adapter.requests),
                "ordinary_candidates_limit": adapter.max_continuation_checks,
                "known_join_parents": 0, "samples": len(samples),
                "thread_cpu_us": {
                    "median": statistics.median(samples),
                    "p99": statistics.quantiles(samples, n=100, method="inclusive")[98],
                    "max": max(samples),
                },
                "journal_enabled": True,
            }
        finally:
            adapter.close()
            writer.close()
        status = json.loads(writer.status_path.read_bytes())
        if not status["complete"] or status["error"] is not None:
            raise ValueError("CPU admission journal did not drain")
        expected_counts = {"agent_continuation_free_capacity_admitted": 550} if expected is True else {}
        if status["enqueued"] != expected_counts:
            raise ValueError("CPU admission journal did not retain every successful grant")
        results[label].update(
            journal_enqueued=status["enqueued"], journal_sha256=sha256_file(writer.path),
            writer_status_sha256=sha256_file(writer.status_path),
        )
    if any(sha256_file(ROOT / name) != digest for name, digest in sources.items()):
        raise ValueError("measured callback sources changed during execution")
    result = {
        "scope": "Added Python callback thread CPU only; synthetic 33-program state and real async journal.",
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "running_code_commit": plan["code_commit"], "fixtures": results,
        "sources_sha256": sources, "operator_sha256": sha256_file(Path(__file__)),
        "timing_fixture_resets_eligibility_or_cached_size_between_calls": True,
        "background_writer_thread_cpu_included": False,
        "whole_controller_cost_measured": False, "gpu_or_end_to_end_performance_verified": False,
        "preserved_initial_validation_failure": {
            "operator_sha256": "318e3390f847659cb228c738e2d149c1532648def9a1476782d1a59e24c2e599",
            "journal_directory": str(output.parent / "cpu_journal/new_grant"),
            "reason": "The initial operator compared the writer's event-count dictionary with an integer.",
            "observed_complete_new_grant_records": 550,
        },
        "limitations": [
            "Synthetic ranges preserve constant-time length observations, not actual KV or DMA.",
            "Only the ordinary continuation window is timed; the 24-parent JOIN scan is not benchmarked here.",
            "Empty completion timing includes construction of its small batch stand-in.",
            "Sub-millisecond callback cost does not establish lower JCT, higher throughput or harmless GPU interference.",
        ],
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
