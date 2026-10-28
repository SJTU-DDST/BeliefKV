"""Observed JOIN budget handover preserves ready peers and native KV authority."""

from dataclasses import replace
from array import array
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from scripts.audit_reference_join_grants import validate_grant
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_reference_join_free_admission import join_wait
from tests.test_reference_thunder_admission import full_runtime, state
from tests.test_full_only_native_lifecycle import full_native


def bind(live, identity, session=None, generation=1):
    adapter = live._agent_admission
    key = replace(adapter.latest[identity], session_id=session or identity,
                  session_generation=generation)
    adapter.latest[identity] = key
    live.context_sessions[key.context_id] = key
    live._context_session_changed(key.context_id)
    return key


def blocked(live, identity="donor", *, tokens=400):
    sequence = 20 + 100 * len(live._agent_admission.policy.programs)
    live.on_events((observed(
        sequence, Kind.INVOCATION_CREATE, invocation_id=identity, context_id=identity + "-context",
    ),))
    req = request(identity, identity, tokens=tokens, workflow="wf")
    req.session_id, req.session_generation = identity, 1
    live.register_visible_request(req)
    # Explicit starting working set, without running the periodic policy.
    live._agent_admission.policy.programs[identity].active = True
    live.on_batch_completed(NS(reqs=[req]))
    live.on_events((
        observed(sequence + 1, Kind.INVOCATION_CREATE, invocation_id=identity + "-child",
                 context_id=identity + "-child-context", parent_invocation_id=identity,
                 relation_type=RelationType.SPAWN),
        observed(sequence + 2, Kind.JOIN_CREATE, join_id=identity + "-join",
                 member_invocation_ids=(identity + "-child",)),
        observed(sequence + 3, Kind.JOIN_WAIT, invocation_id=identity, join_id=identity + "-join"),
    ))
    return live._agent_admission.policy.programs[identity]


def setup(monkeypatch, tmp_path, *, capacity=700, tokens=200, enabled=True):
    live = full_runtime(
        monkeypatch, tmp_path, capacity=capacity, join_free_admission=True,
        join_blocked_handoff=enabled,
    )
    adapter, _ = join_wait(live, tokens=tokens)
    key = bind(live, "parent")
    return live, adapter, key


def test_join_restoration_can_trade_only_blocked_complete_budget(monkeypatch, tmp_path):
    live, adapter, key = setup(monkeypatch, tmp_path)
    try:
        donor = blocked(live)
        assert adapter.policy.remaining_capacity() == 198
        assert adapter.claim_restore(key, source="join_ticket")
        assert not donor.active and adapter.policy.programs["parent"].active
        assert adapter.policy.remaining_capacity() == 398
        assert adapter.holds_restore(key)
        before = state(adapter)
        assert adapter.claim_restore(key, source="join_ticket")
        assert state(adapter) == before
        assert adapter.counts["join_blocked_handoff_grants"] == 1
        validate_reference_admission(
            adapter.status(), join_free_admission=True, join_blocked_handoff=True,
        )
    finally:
        live.close()


@pytest.mark.parametrize("cause", (
    "disabled", "ready", "pending", "marked", "protected", "stale",
    "short_tool", "tool_source", "insufficient",
))
def test_handoff_refusal_never_pauses_a_ready_or_unproven_owner(monkeypatch, tmp_path, cause):
    live, adapter, key = setup(
        monkeypatch, tmp_path, enabled=cause != "disabled",
        capacity=1000 if cause == "insufficient" else 700,
        tokens=900 if cause == "insufficient" else 200,
    )
    try:
        donor = blocked(live)
        if cause == "ready":
            live.graph.invocations["donor"].state = InvocationState.READY
        elif cause == "pending":
            donor.pending, donor.acting = True, False
        elif cause == "marked":
            donor.marked_for_pause = True
        elif cause == "protected":
            live._prefetch_service_leases["test"] = NS(key=adapter.latest["donor"])
        elif cause == "stale":
            live.context_sessions["donor-context"] = replace(
                adapter.latest["donor"], context_epoch=1,
            )
        elif cause == "short_tool":
            live.graph.invocations["donor"].state = InvocationState.WAIT_TOOL
            live.graph.invocations["donor"].active_tool_calls["short"] = {}
        before = state(adapter)
        assert not adapter.claim_restore(
            key, source="tool_wait" if cause == "tool_source" else "join_ticket",
        )
        assert state(adapter) == before
    finally:
        live._prefetch_service_leases.clear()
        live.close()


def test_multiple_donors_are_committed_only_when_the_whole_parent_fits(monkeypatch, tmp_path):
    live, adapter, key = setup(monkeypatch, tmp_path, capacity=1000, tokens=600)
    try:
        a, b = blocked(live, "a", tokens=250), blocked(live, "b", tokens=250)
        assert adapter.policy.remaining_capacity() == 296
        assert adapter.claim_restore(key, source="join_ticket")
        assert not a.active and not b.active
        assert adapter.policy.remaining_capacity() == 298
        assert adapter.counts["join_blocked_handoff_paused_agents"] == 2
    finally:
        live.close()


def test_a_return_during_revalidation_cancels_the_entire_handoff(monkeypatch, tmp_path):
    live, adapter, key = setup(monkeypatch, tmp_path)
    try:
        donor = blocked(live)
        controller, original = adapter._join_handoff, adapter._join_handoff._donor
        checks = []

        def recheck(item, target):
            checks.append(item.identity)
            if checks.count("donor") == 2:
                live.graph.invocations["donor"].state = InvocationState.READY
            return original(item, target)

        monkeypatch.setattr(controller, "_donor", recheck)
        assert not adapter.claim_restore(key, source="join_ticket")
        assert donor.active and not adapter.policy.programs["parent"].active
    finally:
        live.close()


def test_repeated_capacity_refusal_uses_the_bounded_backoff(monkeypatch, tmp_path):
    live, adapter, key = setup(monkeypatch, tmp_path)
    try:
        donor = blocked(live)
        live.graph.invocations["donor"].state = InvocationState.READY
        assert not adapter.claim_restore(key, source="join_ticket")
        scanned = adapter.counts["join_blocked_handoff_donor_checks"]
        assert not adapter.claim_restore(key, source="join_ticket")
        assert adapter.counts["join_blocked_handoff_donor_checks"] == scanned
        assert adapter.counts["join_blocked_handoff_backoff"] == 1
        assert donor.active
    finally:
        live.close()


def test_periodic_control_cannot_readmit_a_still_blocked_donor_and_undo_the_join(
    monkeypatch, tmp_path,
):
    live, adapter, key = setup(monkeypatch, tmp_path)
    try:
        donor = blocked(live)
        assert adapter.claim_restore(key, source="join_ticket")
        adapter.tick(now=time.monotonic() + 10.)
        assert not donor.active
        assert adapter.policy.programs["parent"].active
        assert adapter._join_handoff.can_resume(donor) is False
        live.on_events((observed(1000, Kind.RETURN, invocation_id="donor-child"),))
        assert adapter._join_handoff.can_resume(donor) is True
    finally:
        live.close()


def test_waiting_parent_does_not_claim_budget_before_an_eos_or_ready_request(monkeypatch, tmp_path):
    live, adapter, key = setup(monkeypatch, tmp_path)
    try:
        donor = blocked(live)
        adapter.events((observed(30, Kind.TOOL_START, invocation_id="donor"),))
        assert donor.active and not adapter.policy.programs["parent"].active
        assert not adapter.counts["join_blocked_handoff_grants"]
    finally:
        live.close()


def test_actual_submitted_join_successor_gets_budget_on_registration(monkeypatch, tmp_path):
    live, adapter, key = setup(monkeypatch, tmp_path)
    try:
        donor = blocked(live)
        live.on_events((observed(1000, Kind.RETURN, invocation_id="child"),))
        req = request("parent-following", "parent", tokens=220, workflow="wf")
        req.session_id, req.session_generation, req.finished = "parent", 1, lambda: False
        live.register_visible_request(req)
        assert adapter.policy.programs["parent"].active
        assert not donor.active
        assert adapter.allows(live.visible[req.rid])
        assert adapter.counts["join_blocked_handoff_grants"] == 1
    finally:
        live.close()


def test_success_journal_reconciles_full_budgets_without_claiming_physical_free_pages(
    monkeypatch, tmp_path,
):
    live, adapter, key = setup(monkeypatch, tmp_path)
    try:
        blocked(live)
        rows = []
        with monkeypatch.context() as patch:
            patch.setattr(live, "_opportunity_writer", NS(record=rows.append))
            assert adapter.claim_restore(key, source="join_ticket")
        assert len(rows) == 1
        validate_grant(rows[0])
        assert rows[0]["released_device_tokens"] == rows[0]["released_host_tokens"] == 0
        assert rows[0]["donors"][0]["key"]["invocation_id"] == "donor"
    finally:
        live.close()


@pytest.mark.parametrize("full_native", (512,), indirect=True)
def test_join_budget_handoff_requires_real_native_reclaim_and_restore(
    full_native, monkeypatch, tmp_path,
):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, _ = full_native
    for name, value in arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference", "AGENT_JOIN_FREE_ADMISSION": "1",
        "AGENT_JOIN_BLOCKED_HANDOFF": "1", "EXECUTION_FRONTIER_RESTORE": "0",
    }, "full").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "native-handoff.sock"))
    live.attach_native_cache(cache)
    try:
        adapter, _ = join_wait(live, tokens=80)
        parent, generation = insert_session(cache, list(range(80)), "parent")
        key = bind(live, "parent", generation=generation)
        assert cache.prepare_beliefkv_spawn_full(
            session_id="parent", session_generation=generation,
        )["issued"]
        cache.writing_check(finish_count=1)
        cache.beliefkv_cold_full_owner_validator = lambda _owners: True
        assert cache.reclaim_beliefkv_handoff_capacity(
            full_tokens=allocator.available_size() + 80, mamba_slots=0,
        ) == {0: 80}
        cache.beliefkv_cold_full_owner_validator = live._live_cold_full_owners
        donor = blocked(live)
        donor_node, donor_generation = insert_session(cache, list(range(1000, 1400)), "donor")
        bind(live, "donor", generation=donor_generation)
        assert cache.prepare_beliefkv_spawn_full(
            session_id="donor", session_generation=donor_generation,
        )["issued"]
        cache.writing_check(finish_count=1)
        held = allocator.alloc(allocator.available_size() - 16)
        before = allocator.available_size()
        assert adapter.claim_restore(key, source="join_ticket")
        assert not donor.active and allocator.available_size() == before == 16
        arguments = dict(
            session_id="parent", session_generation=generation,
            leaf_node_id=parent.id, leaf_creation_time=float(parent.creation_time),
            nodes=((parent.id, float(parent.creation_time), False, "join-restored"),),
            beliefkv_before_enqueue=lambda _operation: adapter.holds_restore(key),
        )
        assert not any(row.issued for row in cache.prefetch_gpu_session_nodes(**arguments))
        assert not loads and donor_node.component_data[0].value is not None
        freed = cache.reclaim_beliefkv_handoff_capacity(
            full_tokens=80, mamba_slots=0, exclude_node_ids=(parent.id,),
        )
        assert freed == {0: 400}
        assert any(row.issued for row in cache.prefetch_gpu_session_nodes(**arguments))
        cache.loading_check(finish_count=1)
        assert parent.component_data[0].value is not None
        assert parent.component_data[0].host_lock_ref == 0
        assert donor_node.component_data[0].value is None
        assert donor_node.component_data[0].host_value is not None
        live.on_events((observed(1000, Kind.RETURN, invocation_id="child"),))
        following = request("parent-next", "parent", tokens=80, workflow="wf")
        following.origin_input_ids = array("q", following.origin_input_ids)
        following.session_id, following.session_generation = "parent", generation
        following.finished = lambda: False
        live.register_visible_request(following)
        plan = live.plan_native_prefill([following], running_batch=NS(reqs=[]), adder=None)
        selection = select_native_prefill_candidates(
            [following], plan=plan, current_semantic_revision=live.semantic_revision,
        )
        assert selection.candidates == (following,)
        allocator.free(held)
        cache.sanity_check()
    finally:
        live.close()


@pytest.mark.parametrize("mode", ("scheduler_only", "kv_only", "native_matched"))
def test_internal_controls_strip_join_handoff_and_group_sizes(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference", "AGENT_JOIN_FREE_ADMISSION": "1",
        "AGENT_JOIN_BLOCKED_HANDOFF": "1", "BELIEFKV_LAYER_HANDOFF_D2H_LAYERS": "8",
        "BELIEFKV_LAYER_HANDOFF_H2D_LAYERS": "4",
    }, mode)
    assert settings["BELIEFKV_AGENT_JOIN_BLOCKED_HANDOFF"] == "0"
    assert settings["BELIEFKV_LAYER_HANDOFF_D2H_LAYERS"] == "1"
    assert settings["BELIEFKV_LAYER_HANDOFF_H2D_LAYERS"] == "1"


def test_thunderagent_decay_does_not_inherit_join_handoff():
    settings = paper_environment({
        "AGENT_ADMISSION": "thunderagent_reference", "AGENT_JOIN_FREE_ADMISSION": "1",
        "AGENT_JOIN_BLOCKED_HANDOFF": "1",
    }, "thunderagent_acting_decay")
    assert settings["BELIEFKV_AGENT_JOIN_BLOCKED_HANDOFF"] == "0"
