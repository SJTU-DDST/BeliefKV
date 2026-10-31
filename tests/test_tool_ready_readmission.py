"""Exercise real tool returns before and after native successor registration."""

from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_reference_thunder_admission import full_runtime


@pytest.mark.parametrize("return_first", (False, True))
@pytest.mark.parametrize("tool_count", (1, 2))
def test_paused_tool_successor_reenters_dispatch_in_either_event_order(
    monkeypatch, tmp_path, return_first, tool_count,
):
    live = full_runtime(
        monkeypatch, tmp_path, capacity=500, tool_prefetch=False,
        free_continuation_admission=True, free_dispatch_interval_ms=500,
    )
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="a", context_id="a-context"),
        ))
        first = request("first", "a", tokens=200, workflow="wf")
        live.register_visible_request(first)
        live.on_batch_completed(NS(reqs=[first]))
        admission = live._agent_admission
        admission.next_tick = float("inf")
        admission.policy._pause(admission.policy.programs["a"], time.monotonic())
        for index in range(tool_count):
            live.on_events((observed(
                2 + index, Kind.TOOL_START, invocation_id="a",
                attributes={"tool_run_id": f"tool-{index}"},
            ),))
        following = request("following", "a", tokens=200, workflow="wf")
        following.output_ids.clear()
        if not return_first:
            live.register_visible_request(following)
            assert not admission.policy.programs["a"].active
            assert "a" not in admission._dispatch_pending
        for index in range(tool_count):
            live.on_events((observed(
                4 + index, Kind.TOOL_END, invocation_id="a",
                attributes={"tool_run_id": f"tool-{index}"},
            ),))
            if index + 1 < tool_count:
                assert live.graph.invocations["a"].state is InvocationState.WAIT_TOOL
                assert not admission.policy.programs["a"].active
        if return_first:
            live.register_visible_request(following)
        assert live.graph.invocations["a"].state is InvocationState.READY
        assert admission.policy.programs["a"].active
        assert not admission.counts["control_ticks"]
        plan = live.plan_native_prefill(
            [following], running_batch=NS(reqs=[]), adder=None,
        )
        actual = select_native_prefill_candidates(
            [following], plan=plan, current_semantic_revision=live.semantic_revision,
        )
        assert actual.candidates == (following,)
        assert not live.enable_tool_prefetch
    finally:
        live.close()
