from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RelationType, RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.paper_baselines import environment
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request


def runtime(monkeypatch, *, initial=1):
    for name, value in environment({"ROOT_COUNT": str(initial)}, "concur_observed_join").items():
        monkeypatch.setenv(name, value)
    live = NativeAdmissionRuntime()
    live.attach_native_cache(NS(
        tree_components=(0,),
        token_to_kv_pool_allocator=NS(size_full=1000, available_size=lambda: 1000),
        evictable_size=lambda: 0,
    ))
    return live


def test_native_queue_filters_agent_permits_and_preserves_shared_batch_limits(monkeypatch):
    live = runtime(monkeypatch)
    try:
        first, second = request("a", "one"), request("b", "two")
        live.register_visible_request(first)
        live.register_visible_request(second)
        plan = live.plan_native_prefill([first, second], running_batch=NS(reqs=[]), adder=None)
        assert [key.request_id for key in plan.prioritized] == ["a"]
        assert not live._custom_kv_management and not live.enable_resident_first
        assert live._tool_continuity is None
        assert live._paper_baseline.status()["policy"] == "concur_paper_observed_join"
    finally:
        live.close()


def test_real_selected_prefill_feedback_is_gpu_only_and_charged_once(monkeypatch):
    live = runtime(monkeypatch)
    try:
        req = request("a", "one")
        req._cache_breakdown_computed = True
        req.retracted_stain = False
        req.cached_tokens_device, req.cached_tokens_host = 10, 80
        live.register_visible_request(req)
        live.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
        selected = NS(reqs=[req], forward_mode=NS(is_extend=lambda: True))
        live.on_batch_selected(selected)
        live.on_batch_selected(selected)
        sample = live._paper_baseline.feedback.sample(1)
        assert sample.gpu_hit_rate == .1 and sample.observed_requests == 1
        live.on_batch_completed(NS(reqs=[req]))
        assert live._paper_baseline.counts["completed"] == 1
    finally:
        live.close()


def test_a_real_join_frees_logical_parent_capacity_without_join_priority(monkeypatch):
    live = runtime(monkeypatch)
    paper = live._paper_baseline
    paper.next_tick = float("inf")
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="parent-context"),
            observed(
                2, Kind.INVOCATION_CREATE, invocation_id="child", context_id="child-context",
                parent_invocation_id="parent", relation_type=RelationType.SPAWN,
            ),
            observed(3, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",)),
        ))
        first = request("parent-one", "parent", workflow="wf")
        child = request("child-one", "child", workflow="wf")
        live.register_visible_request(first)
        live.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
        live.on_batch_completed(NS(reqs=[first]))
        live.register_visible_request(child)
        assert not paper.policy.agents["child"].active
        live.on_events((observed(4, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
        assert live.graph.invocations["parent"].state.value == "wait_join"
        assert not paper.policy.agents["parent"].active and paper.policy.agents["child"].active
        assert paper.counts["observed_join_permits_yielded"] == 1
        plan = live.plan_native_prefill([child], running_batch=NS(reqs=[]), adder=None)
        assert [key.request_id for key in plan.prioritized] == ["child-one"]
        live.on_prefill_candidate_result(child, admitted=True, result="CONTINUE")
        live.on_batch_completed(NS(reqs=[child]))
        live.on_events((observed(5, Kind.RETURN, invocation_id="child"),))
        successor = request("parent-two", "parent", workflow="wf")
        live.register_visible_request(successor)
        assert paper.policy.agents["parent"].active
        assert not live.enable_join_parent_priority
    finally:
        live.close()


def test_workflow_end_retires_all_active_and_paused_permits(monkeypatch):
    live = runtime(monkeypatch)
    try:
        live.register_visible_request(request("a", "one"))
        live.register_visible_request(request("b", "two"))
        live._paper_baseline.events((RuntimeEvent("end", 1., Kind.WORKFLOW_END, "workflow"),))
        assert not live._paper_baseline.policy.agents
        assert not live._paper_baseline.requests
    finally:
        live.close()
