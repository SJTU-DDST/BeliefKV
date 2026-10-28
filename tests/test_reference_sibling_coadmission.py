from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import EventConfidence, RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.paper_baseline import ThunderAgentDecayAdapter
from beliefkv.runtime.reference_thunder_admission import (
    ReferenceThunderAdmission, validate_reference_admission,
)
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import run_causal_comparison as comparison
from tests.test_full_only_native_lifecycle import full_native
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_reference_thunder_admission import full_runtime


def group(live, *, waiting=True, mode="all", confidence=EventConfidence.OBSERVED_EXACT, size=2):
    members = tuple("child-" + str(index) for index in range(size))
    events = [
        observed(0, Kind.WORKFLOW_START),
        observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="parent-context"),
    ]
    events.extend(
        observed(
            index + 2, Kind.INVOCATION_CREATE, invocation_id=name, context_id=name + "-context",
            parent_invocation_id="parent", relation_type=RelationType.SPAWN,
        ) for index, name in enumerate(members)
    )
    events.append(observed(
        size + 2, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=members,
        attributes={"mode": mode}, confidence=confidence,
    ))
    if waiting:
        events.append(observed(
            size + 3, Kind.JOIN_WAIT, invocation_id="parent", join_id="join",
            confidence=confidence,
        ))
    live.on_events(tuple(events))
    return members


def arrivals(live):
    first = request("first", "child-0", tokens=300, workflow="wf")
    peer = request("large", "unrelated", tokens=800, workflow="other")
    second = request("second", "child-1", tokens=300, workflow="wf")
    for req in (first, peer, second):
        live.register_visible_request(req)
    return first, peer, second


@pytest.mark.parametrize("enabled", (False, True))
@pytest.mark.parametrize("capacity", (700, 1000))
def test_observed_sibling_admission_uses_only_complete_free_budget(monkeypatch, tmp_path, enabled, capacity):
    live = full_runtime(monkeypatch, tmp_path, capacity=capacity, coadmit_join_children=enabled)
    try:
        group(live)
        first, peer, second = arrivals(live)
        adapter = live._agent_admission
        assert adapter.allows(live.visible[first.rid])
        assert not adapter.allows(live.visible[peer.rid])
        expected = enabled and capacity == 1000
        assert adapter.allows(live.visible[second.rid]) is expected
        assert adapter.counts["sibling_free_programs_admitted"] == int(expected)
        assert not adapter.policy.programs["unrelated"].active
        assert adapter.policy.remaining_capacity() >= 0
        adapter.next_tick = float("inf")
        plan = live.plan_native_prefill([peer, second, first], running_batch=NS(reqs=[]), adder=None)
        selection = select_native_prefill_candidates(
            [peer, second, first], plan=plan, current_semantic_revision=live.semantic_revision,
        )
        assert {req.rid for req in selection.candidates} == (
            {"first", "second"} if expected else {"first"}
        )
    finally:
        live.close()


@pytest.mark.parametrize("scenario", (
    "spawn_only", "any", "inferred", "timeout", "satisfied", "foreign_child", "tool_waiting_child",
))
def test_group_requires_current_actual_blocked_all_join_and_ready_child(monkeypatch, tmp_path, scenario):
    live = full_runtime(monkeypatch, tmp_path, coadmit_join_children=True)
    try:
        group(
            live, waiting=scenario != "spawn_only", mode="any" if scenario == "any" else "all",
            confidence=(
                EventConfidence.INFERRED if scenario == "inferred" else EventConfidence.OBSERVED_EXACT
            ),
        )
        if scenario in ("timeout", "satisfied"):
            live.on_events((observed(
                10, Kind.JOIN_TIMEOUT if scenario == "timeout" else Kind.JOIN_SATISFIED,
                join_id="join",
            ),))
        if scenario == "tool_waiting_child":
            live.on_events((observed(
                10, Kind.TOOL_START, invocation_id="child-1", context_id="child-1-context",
                attributes={"tool_call_id": "tool"},
            ),))
        first = request("first", "child-0", tokens=300, workflow="wf")
        peer = request("large", "unrelated", tokens=800, workflow="other")
        second = request(
            "second", "child-1", tokens=300, workflow="foreign" if scenario == "foreign_child" else "wf",
        )
        for req in (first, peer, second):
            live.register_visible_request(req)
        adapter = live._agent_admission
        assert adapter.policy.programs["child-0"].active
        assert not adapter.policy.programs["child-1"].active
        assert not adapter.counts["sibling_free_programs_admitted"]
    finally:
        live.close()


@pytest.mark.parametrize("capacity", (1000, 1200, 1202))
def test_join_event_admits_the_complete_submitted_group_atomically(monkeypatch, tmp_path, capacity):
    live = full_runtime(monkeypatch, tmp_path, capacity=capacity, coadmit_join_children=True)
    try:
        members = group(live, waiting=False, size=3)
        for index, name in enumerate(members):
            live.register_visible_request(request(str(index), name, tokens=300, workflow="wf"))
            if index == 0:
                live.register_visible_request(request(
                    "large", "unrelated", tokens=capacity + 100, workflow="other",
                ))
        adapter = live._agent_admission
        assert [adapter.policy.programs[name].active for name in members] == [True, False, False]
        before = deepcopy(adapter.policy.programs[members[0]])
        live.on_events((observed(10, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
        assert adapter.policy.programs[members[0]] == before
        assert [adapter.policy.programs[name].active for name in members] == (
            [True, True, True] if capacity == 1202 else [True, True, False]
        )
        assert adapter.counts["sibling_free_programs_admitted"] == (2 if capacity == 1202 else 1)
        live.on_events((observed(11, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
        assert adapter.counts["sibling_free_programs_admitted"] == (2 if capacity == 1202 else 1)
        assert adapter.policy.remaining_capacity() >= 0
    finally:
        live.close()


def test_group_budget_is_refreshed_from_already_generated_native_outputs(monkeypatch, tmp_path):
    live = full_runtime(monkeypatch, tmp_path, capacity=1202, coadmit_join_children=True)
    try:
        members = group(live, waiting=False, size=3)
        first = request("first", members[0], tokens=300, workflow="wf")
        live.register_visible_request(first)
        live.register_visible_request(request("large", "unrelated", tokens=1300, workflow="other"))
        for index, name in enumerate(members[1:], 1):
            live.register_visible_request(request(str(index), name, tokens=300, workflow="wf"))
        adapter = live._agent_admission
        assert adapter.policy.programs[members[0]].tokens == 302
        assert adapter.policy.remaining_capacity() == 800
        first.output_ids.extend([1] * 18)
        live.on_events((observed(10, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
        assert adapter.policy.programs[members[1]].active
        assert not adapter.policy.programs[members[2]].active
        assert adapter.policy.programs[members[0]].tokens == 320
        assert adapter.policy.remaining_capacity() == 382
    finally:
        live.close()


@pytest.mark.parametrize("capacity", (1000, 1202))
def test_group_journal_records_actual_atomic_grant_once_with_each_current_identity(
    monkeypatch, tmp_path, capacity,
):
    live = full_runtime(monkeypatch, tmp_path, capacity=capacity, coadmit_join_children=True)
    try:
        members = group(live, waiting=False, size=3)
        for index, name in enumerate(members):
            live.register_visible_request(request(str(index), name, tokens=300, workflow="wf"))
            if index == 0:
                live.register_visible_request(request(
                    "large", "unrelated", tokens=capacity + 100, workflow="other",
                ))
        records = []
        with monkeypatch.context() as patch:
            patch.setattr(live, "_opportunity_writer", NS(record=records.append))
            live.on_events((observed(10, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
            live._agent_admission._coadmit_join("join")
        grants = [row for row in records if row["event"] == "agent_join_siblings_coadmitted"]
        assert len(grants) == 1
        row = grants[0]
        added = 1 if capacity == 1000 else 2
        assert row["join_id"] == "join"
        assert row["required_tokens"] == 400 * added
        assert row["remaining_before_tokens"] == capacity - 402
        assert row["remaining_after_tokens"] == capacity - 402 - 400 * added
        assert {item["key"]["invocation_id"] for item in row["programs"]} == set(members[1:1 + added])
        assert {item["key"]["request_id"] for item in row["programs"]} == {
            str(index) for index in range(1, 1 + added)
        }
        assert {item["complete_budget_tokens"] for item in row["programs"]} == {400}
        assert row["native_capacity_authority_preserved"]
    finally:
        live.close()


@pytest.mark.parametrize("scenario", ("marked", "stale_visible", "stale_epoch", "missing_request"))
def test_free_sibling_grant_rejects_outdated_or_protected_request(monkeypatch, tmp_path, scenario):
    live = full_runtime(monkeypatch, tmp_path, coadmit_join_children=True)
    try:
        group(live, waiting=False)
        _, _, second = arrivals(live)
        adapter = live._agent_admission
        key = adapter.latest["child-1"]
        if scenario == "marked":
            adapter.policy.programs["child-1"].marked_for_pause = True
        elif scenario == "stale_visible":
            live.visible.pop(second.rid)
        elif scenario == "stale_epoch":
            live.graph.contexts[key.context_id].epoch += 1
        else:
            adapter.requests.pop(second.rid)
        live.on_events((observed(10, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
        assert not adapter.policy.programs["child-1"].active
        assert not adapter.counts["sibling_free_programs_admitted"]
    finally:
        live.close()


def test_unsubmitted_sibling_never_holds_ready_child_or_reserves_its_future_context(monkeypatch, tmp_path):
    live = full_runtime(monkeypatch, tmp_path, coadmit_join_children=True)
    try:
        group(live)
        first = request("first", "child-0", tokens=300, workflow="wf")
        live.register_visible_request(first)
        adapter = live._agent_admission
        assert adapter.allows(live.visible["first"])
        assert "child-1" not in adapter.policy.programs
        assert adapter.policy.remaining_capacity() == 600
        assert not adapter.counts["sibling_free_programs_admitted"]
    finally:
        live.close()


@pytest.mark.parametrize("size", (24, 25))
def test_event_scan_limit_keeps_larger_join_on_normal_reference_path(monkeypatch, tmp_path, size):
    live = full_runtime(monkeypatch, tmp_path, coadmit_join_children=True)
    try:
        group(live, size=size)
        _, _, second = arrivals(live)
        assert live._agent_admission.allows(live.visible[second.rid]) is (size == 24)
    finally:
        live.close()


@pytest.mark.parametrize("join_free", (False, True))
def test_actual_constructor_and_declared_combined_policy_are_isolated(monkeypatch, tmp_path, join_free):
    live = full_runtime(
        monkeypatch, tmp_path, coadmit_join_children=True, join_free_admission=join_free,
    )
    try:
        adapter = live._agent_admission
        validate_reference_admission(
            adapter.status(), join_free_admission=join_free, coadmit_join_children=True,
        )
        with pytest.raises(ValueError):
            validate_reference_admission(adapter.status(), join_free_admission=join_free)
        for method in ("attach", "tick", "completed", "aborted", "_refresh_streaming_progress"):
            assert getattr(ReferenceThunderAdmission, method) is getattr(ThunderAgentDecayAdapter, method)
    finally:
        live.close()
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference", "AGENT_COADMIT_JOIN_CHILDREN": "1",
        "AGENT_JOIN_FREE_ADMISSION": str(int(join_free)),
    }, "full")
    proof = comparison.preflight_runtime(settings, str(tmp_path))
    assert proof["verified_configuration"]["coadmit_join_children"] is True
    control = paper_environment({
        "AGENT_ADMISSION": "thunderagent_reference", "AGENT_COADMIT_JOIN_CHILDREN": "1",
    }, "thunderagent_acting_decay")
    assert control["BELIEFKV_AGENT_COADMIT_JOIN_CHILDREN"] == "0"


def test_sibling_candidate_keeps_same_work_timing_and_changes_only_explicit_sibling_option():
    root = Path(__file__).resolve().parents[1]
    base = json.loads((root / "configs/migration/predictive_reference_join_free_20261027.json").read_bytes())
    candidate = json.loads((root / "configs/migration/predictive_reference_join_siblings_20261027.json").read_bytes())
    settings = dict(candidate["common_environment"])
    assert settings.pop("BELIEFKV_AGENT_COADMIT_JOIN_CHILDREN") == "1"
    assert settings["AGENT_COADMIT_JOIN_CHILDREN"] == "1"
    settings["AGENT_COADMIT_JOIN_CHILDREN"] = "0"
    assert settings == base["common_environment"]
    for name in (
        "source_profile", "source_registration", "tool_timing_overlay",
        "control_reuse", "native_reference", "order",
    ):
        assert candidate[name] == base[name]


@pytest.mark.parametrize("full_native", (512,), indirect=True)
@pytest.mark.parametrize("physical_slots", (8, 16))
def test_shared_logical_admission_does_not_override_native_host_restore_capacity(
    full_native, monkeypatch, tmp_path, physical_slots,
):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _, loads, _ = full_native
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference", "AGENT_COADMIT_JOIN_CHILDREN": "1",
        "TOOL_RESTORE_MODE": "speculative", "EXECUTION_FRONTIER_RESTORE": "0",
    }, "full")
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "siblings.sock"))
    live.attach_native_cache(cache)
    try:
        group(live)
        native = []
        for index in range(2):
            node, generation = insert_session(cache, list(range(100 * index, 100 * index + 8)), f"s{index}")
            assert cache.prepare_beliefkv_spawn_full(
                session_id=f"s{index}", session_generation=generation,
            )["issued"]
            cache.writing_check(finish_count=1)
            cache._demote(node.id, {})
            native.append((node, generation))
        first, second = (
            request("first", "child-0", tokens=8, workflow="wf"),
            request("second", "child-1", tokens=8, workflow="wf"),
        )
        for index, req in enumerate((first, second)):
            req.session_id, req.session_generation = f"s{index}", native[index][1]
        live.register_visible_request(first)
        live.register_visible_request(request("large", "unrelated", tokens=405, workflow="other"))
        live.register_visible_request(second)
        adapter = live._agent_admission
        assert adapter.allows(live.visible["first"]) and adapter.allows(live.visible["second"])
        held = allocator.alloc(allocator.available_size() - physical_slots)
        assert held is not None
        for index, (req, (node, generation)) in enumerate(zip((first, second), native)):
            before = allocator.available_size()
            result = cache.prefetch_gpu_session_nodes(
                session_id=req.session_id, session_generation=generation,
                leaf_node_id=node.id, leaf_creation_time=float(node.creation_time),
                nodes=((node.id, float(node.creation_time), False, f"sibling-{index}"),),
                beliefkv_before_enqueue=lambda _op, req=req: adapter.allows(live.visible[req.rid]),
            )
            should_load = before >= 8
            assert sum(item.issued for item in result) == int(should_load)
            assert allocator.available_size() == before - 8 * int(should_load)
            if should_load:
                cache.loading_check(finish_count=1)
                assert node.component_data[0].value is not None
            assert node.component_data[0].host_value is not None
            assert node.component_data[0].host_lock_ref == 0
        assert len(loads) == physical_slots // 8
    finally:
        live.close()
