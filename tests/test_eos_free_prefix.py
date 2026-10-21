"""An EOS can start a fitting complete prefix before cold-capacity recovery."""

from contextlib import ExitStack, contextmanager
from dataclasses import fields, replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import time

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget, TransferStartWindow
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_wait_prefetch_batch import batch_fixture, provisional_join_batch_fixture


@contextmanager
def pressured_prefix(
    *, free_full=9, source="observed_eos", cls=NativeAdmissionRuntime, free_mamba=4,
    node_count=4,
):
    with patch("tests.test_sglang_v0520_runtime.NativeAdmissionRuntime", cls), patch(
        "tests.test_tool_predictive_transfers.NativeAdmissionRuntime", cls,
    ):
        fixture = (
            provisional_join_batch_fixture(node_count=node_count) if source == "observed_eos"
            else batch_fixture(
                "tool_wait" if source == "tool_wait" else "join_ticket",
                node_count=node_count,
            )
        )
    runtime, key, original, cache, _ = fixture
    # Each pinned baseline owns its physical record classes and strict receipt checks.
    physical = cls._wait_prefetch_opportunity.__globals__
    anchors = physical["ContextSessionAnchors"](**vars(original.anchors))
    steps = tuple(physical["PrefetchLoadStep"](**{
        field.name: getattr(step, field.name) for field in fields(step)
    }) for step in original.steps)
    original = physical["SessionH2DOpportunity"](**{
        **vars(original), "anchors": anchors,
        "candidate": replace(original.candidate, anchors=anchors),
        "step": steps[0], "steps": steps,
    })
    headroom = [replace(
        original.headroom, device_full_free_tokens=free_full,
        device_mamba_free_slots=free_mamba,
    )]
    timeline, rows = [], []
    runtime._opportunity_writer = NS(record=rows.append, close=lambda: None)
    budget = PrefetchResidencyBudget(
        1, max(1024, node_count * 40 + 20),
        reserve_full_tokens=1, reserve_mamba_slots=1,
        evictable_full_tokens=max(32, node_count * 4), evictable_mamba_slots=8,
    )
    enqueue = cache.prefetch_gpu_session_nodes.side_effect

    def native_enqueue(**kwargs):
        timeline.append({
            "kind": "enqueue", "cpu_ns": time.thread_time_ns(),
            "nodes": tuple(node for node, *_ in kwargs["nodes"]),
            "full_tokens": 4 * len(kwargs["nodes"]),
        })
        outcomes = enqueue(**kwargs)
        headroom[0] = replace(
            headroom[0],
            device_full_free_tokens=headroom[0].device_full_free_tokens - 4 * len(outcomes),
            device_mamba_free_slots=headroom[0].device_mamba_free_slots - sum(
                int(item[2]) for item in kwargs["nodes"][:len(outcomes)]
            ),
        )
        return outcomes

    def reclaim(**kwargs):
        timeline.append({
            "kind": "reclaim", "cpu_ns": time.thread_time_ns(),
            "pending": runtime.physical_ledger.pending_count, **kwargs,
        })
        full = max(0, kwargs["full_tokens"] - headroom[0].device_full_free_tokens)
        state = max(0, kwargs["mamba_slots"] - headroom[0].device_mamba_free_slots)
        headroom[0] = replace(
            headroom[0],
            device_full_free_tokens=headroom[0].device_full_free_tokens + full,
            device_mamba_free_slots=headroom[0].device_mamba_free_slots + state,
        )
        return {pool: value for pool, value in ((0, full), (2, state)) if value}

    cache.prefetch_gpu_session_nodes.side_effect = native_enqueue
    cache.reclaim_beliefkv_handoff_capacity = Mock(side_effect=reclaim)
    cache.split_beliefkv_prefetch_full_prefix = Mock(
        side_effect=AssertionError("the immediate path may submit only complete extents"),
    )
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(
                runtime, "inspect_context_h2d_opportunity",
                side_effect=lambda **kwargs: replace(original, headroom=headroom[0]),
            ))
            stack.enter_context(patch.object(runtime, "_current_residency_budget", return_value=budget))
            stack.enter_context(patch.object(runtime, "_publish_parent_pressure_candidates"))
            stack.enter_context(patch.object(
                runtime, "_h2d_start_window", return_value=TransferStartWindow(200., 0., 500.),
            ))
            stack.enter_context(patch.dict(physical, {
                "observe_static_full_mamba_headroom": lambda *args: headroom[0],
            }))
            yield NS(
                runtime=runtime, key=key, original=original, cache=cache,
                headroom=headroom, timeline=timeline, rows=rows,
                dispatch=(
                    runtime.dispatch_tool_prefetch if source == "tool_wait"
                    else runtime.dispatch_join_prefetch
                ),
            )
    finally:
        runtime.close()


@pytest.mark.parametrize("free_full", (9, 11))
def test_eos_submits_complete_free_prefix_before_reclaiming_its_safe_tail(free_full):
    with pressured_prefix(free_full=free_full) as case:
        case.dispatch()
        assert [row["kind"] for row in case.timeline] == ["enqueue", "reclaim"]
        assert case.timeline[0]["nodes"] == (1, 2)
        assert case.timeline[1]["pending"] == 2
        assert case.timeline[1]["full_tokens"] == 9
        assert case.timeline[1]["mamba_slots"] == 1
        assert case.timeline[1]["exclude_node_ids"] == (0, 1, 2, 3, 4)
        assert case.runtime.counts["observed_eos_free_prefix_before_reclaim"] == 1
        assert case.runtime.counts["native_admitted"] == 0
        assert case.runtime.graph.invocations[case.key.invocation_id].state is InvocationState.WAIT_JOIN
        assert case.runtime.physical_ledger.pending_count == 2
        assert not case.runtime.completed_physical_actions
        assert not case.runtime.physical_disabled
        case.cache.split_beliefkv_prefetch_full_prefix.assert_not_called()
        submitted = next(
            row for row in case.rows
            if row["event"] == "wait_prefetch_plan" and row["reason"] == "submitted"
        )
        assert submitted["planned_full_tokens"] == 8
        assert submitted["planned_mamba_slots"] == 0
        assert submitted["partial_full_tokens"] == 0
        assert submitted["capacity_budget"]["partial_full_tokens"] == 0
        assert submitted["capacity_budget"]["capacity_refresh_source"] == (
            "free_eos_prefix_before_reclaim"
        )


@pytest.mark.parametrize("source", ("tool_wait", "confirmed_join"))
def test_other_restore_paths_keep_whole_burst_reclaim(source):
    with pressured_prefix(source=source) as case:
        case.dispatch()
        assert [row["kind"] for row in case.timeline] == ["reclaim", "enqueue"]
        assert case.timeline[0]["pending"] == 0
        assert case.timeline[1]["nodes"] == (1, 2, 3, 4)
        assert case.runtime.physical_ledger.pending_count == 4
        assert not case.runtime.counts["observed_eos_free_prefix_before_reclaim"]
        assert not case.runtime.physical_disabled


@pytest.mark.parametrize("free_full", (1, 3))
def test_eos_without_a_complete_fitting_extent_keeps_initial_reclaim(free_full):
    with pressured_prefix(free_full=free_full) as case:
        case.dispatch()
        assert [row["kind"] for row in case.timeline] == ["reclaim", "enqueue"]
        assert case.timeline[0]["pending"] == 0
        assert case.timeline[1]["nodes"] == (1, 2, 3, 4)
        assert not case.runtime.counts["observed_eos_free_prefix_before_reclaim"]
        case.cache.split_beliefkv_prefetch_full_prefix.assert_not_called()


def test_whole_fitting_burst_skips_cold_reclaim():
    with pressured_prefix(free_full=17) as case:
        case.dispatch()
        assert [row["kind"] for row in case.timeline] == ["enqueue"]
        assert case.timeline[0]["nodes"] == (1, 2, 3, 4)
        assert not case.runtime.counts["observed_eos_free_prefix_before_reclaim"]


def test_state_only_shortfall_does_not_fragment_a_fitting_full_burst():
    with pressured_prefix(free_full=17, free_mamba=1) as case:
        case.dispatch()
        assert [row["kind"] for row in case.timeline] == ["reclaim", "enqueue"]
        assert case.timeline[0]["full_tokens"] == 17
        assert case.timeline[0]["mamba_slots"] == 2
        assert case.timeline[1]["nodes"] == (1, 2, 3, 4)
        assert not case.runtime.counts["observed_eos_free_prefix_before_reclaim"]


def test_later_burst_keeps_normal_reclaim():
    with pressured_prefix() as case:
        case.runtime._roll_final_stage()
        case.runtime._join_ticket.issued_nodes = 1
        case.dispatch()
        assert [row["kind"] for row in case.timeline] == ["reclaim", "enqueue"]
        assert not case.runtime.counts["observed_eos_free_prefix_before_reclaim"]


def test_lost_eos_evidence_cannot_start_the_free_prefix():
    with pressured_prefix() as case:
        case.runtime._roll_final_stage()
        case.runtime._semantic_eos_proofs.clear()
        case.runtime._semantic_finished.clear()
        case.dispatch()
        assert not case.timeline
        assert not case.runtime.physical_ledger.pending_count
        assert not case.runtime.counts["observed_eos_free_prefix_before_reclaim"]
        assert not case.runtime.physical_disabled


def test_prefix_ack_keeps_the_join_blocked_and_does_not_claim_complete_state():
    with pressured_prefix() as case:
        case.dispatch()
        transfers = tuple(case.runtime._prefetch_steps.items())
        assert len(transfers) == 2
        case.runtime.on_native_transfer_commit(NS(
            direction="h2d", status="completed", node_ids=(1, 2),
            num_tokens_by_pool=(("kv", 8),),
            child_commits=tuple(NS(
                command_id=command, anchor_node_id=step.node_id,
                published_node_ids=(step.node_id,),
                num_tokens_by_pool=(("kv", 4),), num_bytes=40,
            ) for command, (step, _, _) in transfers),
        ))
        assert not case.runtime.physical_ledger.pending_count
        assert len(case.runtime.completed_physical_actions) == 2
        assert case.runtime.graph.invocations[case.key.invocation_id].state is InvocationState.WAIT_JOIN
        assert not any(lease.demand_ready for lease in case.runtime._prefetch_service_leases.values())
        assert case.runtime.counts["native_admitted"] == 0
        assert not case.runtime.physical_disabled


@pytest.mark.parametrize("change", ("epoch", "session", "capacity"))
def test_final_causal_drain_revalidates_the_immediate_prefix(change):
    with pressured_prefix() as case:
        def drain(**kwargs):
            if change == "epoch":
                case.runtime.graph.contexts[case.key.context_id].epoch += 1
            elif change == "session":
                case.runtime.context_sessions[case.key.context_id] = replace(
                    case.key, session_generation=case.key.session_generation + 1,
                )
            else:
                case.headroom[0] = replace(case.headroom[0], device_full_free_tokens=1)
            return (NS(),)

        case.runtime.event_server = NS(drain=drain, close=lambda: None)
        case.dispatch()
        assert not case.timeline
        assert not case.runtime.physical_ledger.pending_count
        assert not case.runtime.physical_disabled
