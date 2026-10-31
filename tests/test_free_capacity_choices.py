from collections import OrderedDict
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from tests.test_paper_baseline_runtime import request
from tests.test_reference_thunder_admission import full_runtime, state


def waiting(monkeypatch, tmp_path, *, count=3, tokens=300, capacity=1000, same_workflow=False):
    live = full_runtime(
        monkeypatch, tmp_path, capacity=capacity,
        free_continuation_admission=True, free_dispatch_interval_ms=500,
    )
    adapter = live._agent_admission
    adapter.next_tick = float("inf")
    sequence = 0
    workflows = set()

    def register(invocation, workflow, size):
        nonlocal sequence
        events = []
        if workflow not in workflows:
            events.append(RuntimeEvent(
                f"choice-{sequence}", float(sequence), Kind.WORKFLOW_START, workflow,
            ))
            sequence += 1
            workflows.add(workflow)
        events.append(RuntimeEvent(
            f"choice-{sequence}", float(sequence), Kind.INVOCATION_CREATE, workflow,
            invocation_id=invocation, context_id=invocation + "-context",
        ))
        sequence += 1
        live.on_events(events)
        req = request(invocation + "-request", invocation, tokens=size, workflow=workflow)
        req.output_ids.clear()
        req.session_id, req.session_generation = invocation + "-session", 1
        live.register_visible_request(req)
        return req

    register("peer", "peer-workflow", capacity - 100)
    requests = [
        register(f"a{index}", "shared" if same_workflow else f"w{index}", tokens)
        for index in range(count)
    ]
    assert adapter.policy.programs["peer"].active
    assert all(not adapter.policy.programs[f"a{index}"].active for index in range(count))
    adapter.forget("peer")
    records = []
    monkeypatch.setattr(live, "_opportunity_writer", NS(record=records.append, close=lambda: None))
    return live, adapter, requests, records


def choices(records):
    return [row for row in records if row["event"] == "agent_free_capacity_choice"]


def test_actual_dispatch_observes_three_way_competition_without_changing_grants(monkeypatch, tmp_path):
    live, adapter, _requests, records = waiting(monkeypatch, tmp_path)
    try:
        assert adapter._admit_free_continuation()
        grants = [row for row in records if row["event"] == "agent_dispatch_free_capacity_admitted"]
        observed = choices(records)
        assert len(grants) == len(observed) == 2
        first = observed[0]
        assert first["remaining_before_tokens"] == 1000
        assert sum(row["complete_budget_tokens"] for row in first["candidates"]) == 1200
        assert max(row["complete_budget_tokens"] for row in first["candidates"]) * 2 <= 1000
        assert first["ready_unprotected_competition"]
        assert first["candidate_scope"] == "actual_dispatch_window"
        assert observed[0]["dispatch_sequence"] == observed[1]["dispatch_sequence"]
        assert first["grant_monotonic_s"] >= first["monotonic_s"]
        assert first["grant_published"] and not first["physical_feasibility_proven"]
        assert {item.identity for item in adapter.policy.programs.values() if item.active} == {"a0", "a1"}
        assert adapter.policy.remaining_capacity() == 200
    finally:
        live.close()


def test_multiple_workflows_that_all_fit_do_not_emit_competition(monkeypatch, tmp_path):
    live, adapter, _requests, records = waiting(monkeypatch, tmp_path, capacity=1500)
    try:
        assert adapter._admit_free_continuation()
        assert not choices(records)
        assert adapter.counts["free_choice_logical_competitions"] == 0
        assert all(item.active for item in adapter.policy.programs.values())
    finally:
        live.close()


@pytest.mark.parametrize("change", ("marked", "tool", "epoch", "visible"))
def test_unexecutable_or_stale_alternatives_do_not_create_a_choice(monkeypatch, tmp_path, change):
    live, adapter, _requests, records = waiting(monkeypatch, tmp_path)
    try:
        key = adapter.latest["a2"]
        if change == "marked":
            adapter.policy.programs["a2"].marked_for_pause = True
        elif change == "tool":
            live.graph.invocations["a2"].active_tool_calls["tool"] = "execute"
        elif change == "epoch":
            live.graph.contexts[key.context_id].epoch += 1
        else:
            live.visible[key.request_id] = replace(key, context_epoch=1)
        assert adapter._admit_free_continuation()
        assert not choices(records)
        assert not adapter.policy.programs["a2"].active
    finally:
        live.close()


def test_observation_uses_actual_bounded_dispatch_window_not_rotated_queue_tail(monkeypatch, tmp_path):
    live, adapter, _requests, records = waiting(monkeypatch, tmp_path, count=10, tokens=100)
    try:
        assert adapter._admit_free_continuation()
        first = choices(records)[0]
        assert len(first["candidates"]) == 8
        assert first["pending_index_members_outside_observation"] == 2
        assert {row["key"]["invocation_id"] for row in first["candidates"]} == {
            f"a{index}" for index in range(8)
        }
        assert sum(item.active for item in adapter.policy.programs.values()) == 4
    finally:
        live.close()


def test_observer_disabled_when_no_journal_exists(monkeypatch, tmp_path):
    live, adapter, _requests, _records = waiting(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(live, "_opportunity_writer", None)
        monkeypatch.setattr(adapter, "_observe_free_capacity_choice", lambda *args: pytest.fail("observer ran"))
        assert adapter._admit_free_continuation()
        assert not adapter.status()["free_capacity_choice_observation_enabled"]
    finally:
        live.close()


def test_same_workflow_fast_path_does_not_repeat_native_candidate_checks(monkeypatch, tmp_path):
    live, adapter, _requests, records = waiting(monkeypatch, tmp_path, same_workflow=True)
    try:
        checks = []
        original = adapter._candidate

        def candidate(*args, **kwargs):
            checks.append(args[0])
            return original(*args, **kwargs)

        monkeypatch.setattr(adapter, "_candidate", candidate)
        assert adapter._admit_free_continuation()
        assert len(checks) == 3
        assert not choices(records)
        assert adapter.counts["free_choice_single_workflow_key_windows"] == 2
    finally:
        live.close()


def test_group_grant_charges_each_selected_member_once(monkeypatch, tmp_path):
    live, adapter, _requests, records = waiting(monkeypatch, tmp_path)
    try:
        assert adapter._admit_from_free_capacity(
            (adapter.policy.programs["a0"], adapter.policy.programs["a1"]),
            event="agent_join_siblings_coadmitted", join_id="join",
        )
        row = choices(records)[0]
        assert row["candidate_scope"] == "bounded_pending_index"
        assert row["selected_tokens"] == 800
        assert sum(item["selected"] for item in row["candidates"]) == 2
        assert len(row["candidates"]) == 3
        assert adapter.policy.remaining_capacity() == 200
    finally:
        live.close()


def test_protected_join_choice_is_not_promoted_as_unprotected_ranking(monkeypatch, tmp_path):
    live, adapter, _requests, records = waiting(monkeypatch, tmp_path)
    try:
        live._join_priority_pending["a0"] = "join"
        monkeypatch.setattr(live, "_ready_join_parent", lambda req, key: key.invocation_id == "a0")
        assert adapter._admit_free_continuation()
        first = choices(records)[0]
        assert first["protected_join_present"]
        assert not first["ready_unprotected_competition"]
        assert next(row for row in first["candidates"] if row["selected"])["key"]["invocation_id"] == "a0"
    finally:
        live.close()


def test_warming_target_is_not_a_ready_execution_choice(monkeypatch, tmp_path):
    live, adapter, _requests, records = waiting(monkeypatch, tmp_path)
    try:
        adapter.policy.programs["a0"].pending = False
        assert adapter._admit_from_free_capacity(
            (adapter.policy.programs["a0"],), event="agent_join_free_capacity_admitted", join_id="join",
        )
        row = choices(records)[0]
        assert not row["all_selected_application_ready"]
        assert not row["ready_unprotected_competition"]
    finally:
        live.close()


def test_observer_leaves_policy_and_pending_order_unchanged(monkeypatch, tmp_path):
    live, adapter, _requests, _records = waiting(monkeypatch, tmp_path)
    try:
        before, pending = state(adapter), tuple(adapter._dispatch_pending.items())
        selected = (adapter.policy.programs["a0"],)
        row = adapter._observe_free_capacity_choice(selected, 1000, None, None)
        assert row["ready_unprotected_competition"]
        assert state(adapter) == before
        assert tuple(adapter._dispatch_pending.items()) == pending
    finally:
        live.close()


def test_omitted_candidate_count_does_not_scan_the_pending_index(monkeypatch, tmp_path):
    class PendingIndex(OrderedDict):
        def keys(self):
            pytest.fail("bounded observation enumerated the complete pending index")

    live, adapter, _requests, _records = waiting(monkeypatch, tmp_path, count=10, tokens=100)
    try:
        adapter._dispatch_pending = PendingIndex(adapter._dispatch_pending)
        row = adapter._observe_free_capacity_choice(
            (adapter.policy.programs["a0"],), 1000, None, None,
        )
        assert row["pending_index_members_outside_observation"] == 2
        assert len(row["candidates"]) == 8
    finally:
        live.close()
