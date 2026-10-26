from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import EventConfidence, RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request


def runtime(monkeypatch, tmp_path, *, capacity=1000):
    for name, value in arm_environment({
        "AGENT_ADMISSION": "thunderagent_workflow", "TOOL_RESTORE_MODE": "execution",
        "PREFILL_SCHEDULING_POLICY": "join_priority",
    }, "full").items():
        monkeypatch.setenv(name, value)
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "runtime.sock"))
    live.attach_native_cache(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=capacity),
    ))
    return live


def test_workflow_candidate_observes_real_selected_batches_and_deduplicates_children(
    monkeypatch, tmp_path,
):
    live = runtime(monkeypatch, tmp_path)
    try:
        first = request("one", "child-a", tokens=300, workflow="root")
        second = request("two", "child-b", tokens=300, workflow="root")
        live.register_visible_request(first)
        live.register_visible_request(second)
        policy = live._agent_admission.policy
        policy.observe_join("join", {"child-a", "child-b"}, now=policy._control_now)
        assert policy.service["root"].first_service is None
        for req in (first, second):
            live.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
        live.on_batch_selected(NS(reqs=[first, second]))
        assert policy.service["root"].service_batches == 1
        assert policy.service["root"].first_service is not None
        live.on_batch_completed(NS(reqs=[first, second]))
        assert not policy.service["root"].ready
        status = live._agent_admission.status()
        assert status["ordinary_restore_reservation_ms"] == 0
        assert status["workflow_service"]["first_service_target_s"] == 30.
        assert status["eligibility_release"] == (
            "observed_growth_terminal_idle_or_workflow_service_handover"
        )
        assert not live.enable_frontier_restore
    finally:
        live.close()


def test_overdue_workflow_can_enter_native_admission_without_ordinary_restore_binding(
    monkeypatch, tmp_path,
):
    live = runtime(monkeypatch, tmp_path, capacity=500)
    try:
        now = [0.]
        monkeypatch.setattr("beliefkv.runtime.agent_working_set.time.monotonic", lambda: now[0])
        first = request("one", "a", tokens=300, workflow="root-a")
        second = request("two", "b", tokens=300, workflow="root-b")
        live.register_visible_request(first)
        now[0] = .1
        live.register_visible_request(second)
        live.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
        live.on_batch_selected(NS(reqs=[first]))
        now[0] = 31.
        live.on_batch_completed(NS(reqs=[first]))
        plan = live.plan_native_prefill(
            [second], running_batch=NS(reqs=[]), adder=None,
        )
        selection = select_native_prefill_candidates(
            [second], plan=plan, current_semantic_revision=live.semantic_revision,
        )
        assert selection.candidates == (second,)
        assert not live._agent_admission.claim_restore(
            live.visible["two"], source="frontier",
        )
        assert live._agent_admission.policy.restore_owner is None
    finally:
        live.close()


@pytest.mark.parametrize("mode", ("all", "any"))
@pytest.mark.parametrize("confidence", (EventConfidence.OBSERVED_EXACT, EventConfidence.INFERRED))
def test_observed_all_join_coadmits_ready_siblings_but_any_join_does_not(
    monkeypatch, tmp_path, mode, confidence,
):
    live = runtime(monkeypatch, tmp_path)
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="p"),
            observed(2, Kind.INVOCATION_CREATE, invocation_id="a", context_id="a-context",
                     parent_invocation_id="parent", relation_type=RelationType.SPAWN),
            observed(3, Kind.INVOCATION_CREATE, invocation_id="b", context_id="b-context",
                     parent_invocation_id="parent", relation_type=RelationType.SPAWN),
            observed(4, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("a", "b"),
                     attributes={"mode": mode}, confidence=confidence),
            observed(5, Kind.JOIN_WAIT, invocation_id="parent", join_id="join",
                     confidence=confidence),
        ))
        live.register_visible_request(request("a-one", "a", tokens=300, workflow="wf"))
        live.register_visible_request(request(
            "large", "other", tokens=800, workflow="other-workflow",
        ))
        live.register_visible_request(request("b-one", "b", tokens=300, workflow="wf"))
        policy = live._agent_admission.policy
        assert policy.allowed("a")
        observed_all = mode == "all" and confidence is EventConfidence.OBSERVED_EXACT
        assert policy.allowed("b") is observed_all
        assert policy.counts["sibling_coadmitted"] == int(observed_all)
        if confidence is EventConfidence.INFERRED:
            assert not policy.join_members
            assert not live.graph.joins
    finally:
        live.close()


def test_workflow_promotion_keeps_the_current_join_head(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path, capacity=2000)
    try:
        now = [0.]
        monkeypatch.setattr("beliefkv.runtime.agent_working_set.time.monotonic", lambda: now[0])
        join = request("join", "p", workflow="join-root")
        hot = request("hot", "a", workflow="hot-root")
        old = request("old", "b", workflow="old-root")
        for req in (join, hot, old):
            live.register_visible_request(req)
        agents = live._agent_admission
        agents.policy.rebalance(now=5.1)
        ordered = [(0, join), (1, hot), (2, old)]
        keys = {index: live.visible[req.rid] for index, req in ordered}
        agents.policy.service["join-root"].last_service = 39.
        agents.policy.service["hot-root"].last_service = 39.
        now[0] = 40.
        agents.order_ready(ordered, keys, now=40., protected_head=True)
        assert [req.rid for _, req in ordered] == ["join", "old", "hot"]
    finally:
        live.close()


@pytest.mark.parametrize("mode", ["join_priority_only", "scheduler_only", "kv_only", "native_matched"])
def test_paper_and_internal_controls_never_inherit_workflow_opportunities(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_workflow",
        "PREFILL_SCHEDULING_POLICY": "join_priority", "TOOL_RESTORE_MODE": "execution",
    }, mode)
    assert settings["BELIEFKV_AGENT_ADMISSION"] == "off"


def test_workflow_candidate_requires_compatible_explicit_configuration():
    with pytest.raises(ValueError, match="JOIN priority"):
        arm_environment({
            "AGENT_ADMISSION": "thunderagent_workflow", "TOOL_RESTORE_MODE": "execution",
        }, "full")
    with pytest.raises(ValueError, match="native ordinary"):
        arm_environment({
            "AGENT_ADMISSION": "thunderagent_workflow", "EXECUTION_FRONTIER_RESTORE": "1",
            "PREFILL_SCHEDULING_POLICY": "join_priority", "TOOL_RESTORE_MODE": "execution",
        }, "full")
