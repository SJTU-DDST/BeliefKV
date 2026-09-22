"""JOIN timing is derived only from current, calibrated pending children."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from beliefkv.control.causal_graph import InvocationState, JoinMode
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.predictor.structured_frontier import EmpiricalDistribution
from beliefkv.runtime.sglang_v0520_join_projection import (
    CHILD_TIMING_SOURCE,
    MAX_JOIN_CHILDREN,
    MAX_JOIN_WAIT_MS,
    QUANTILE_CAVEAT,
    ChildReturnPrediction,
    JoinProjectionState,
    advance_join_state,
    join_state_from_create,
    project_join_reentry,
)


def event(kind, *, child=None, join_id="j", members=(), mode="all", workflow="wf"):
    return RuntimeEvent(
        event_id=f"{kind.value}-{child or join_id}",
        ts_ms=10.0,
        kind=kind,
        workflow_id=workflow,
        invocation_id=child,
        join_id=join_id,
        member_invocation_ids=members,
        attributes={"mode": mode} if kind is RuntimeEventKind.JOIN_CREATE else {},
    )


def state(mode="all", *, members=("a", "b", "c"), completed=()):
    created = join_state_from_create(
        event(RuntimeEventKind.JOIN_CREATE, members=members, mode=mode),
        parent_invocation_id="parent",
        completed_child_ids=completed,
    )
    return advance_join_state(
        created, event(RuntimeEventKind.JOIN_WAIT, child="parent")
    )


def child(name, quantiles=(10.0, 20.0, 30.0), *, support="exact"):
    return ChildReturnPrediction(
        child_id=name,
        remaining_to_return_ms=quantiles,
        support_level=support,
        calibration_coverage=0.9,
        state=InvocationState.RUNNING_LLM,
    )


def test_join_create_then_wait_all_uses_max_and_lowest_support():
    created = join_state_from_create(
        event(RuntimeEventKind.JOIN_CREATE, members=("a", "b")),
        parent_invocation_id="parent",
    )
    assert created.parent_state is InvocationState.CREATED
    assert not project_join_reentry(created, {}).available
    waiting = advance_join_state(
        created, event(RuntimeEventKind.JOIN_WAIT, child="parent")
    )
    hint = project_join_reentry(waiting, {
        "a": child("a", (10, 50, 60)),
        "b": child("b", (20, 30, 90), support="pooled"),
    })
    assert (hint.workflow_id, hint.parent_invocation_id, hint.join_id) == (
        "wf", "parent", "j"
    )
    assert (hint.mode, hint.pending_child_ids) == (JoinMode.ALL, ("a", "b"))
    assert (hint.p10_ms, hint.p50_ms, hint.p90_ms) == (20, 50, 90)
    assert hint.minimum_support == "pooled"
    assert hint.missing_reasons == ()
    assert [(p.child_id, p.source, p.support_level, p.calibration_coverage)
            for p in hint.provenance] == [
                ("a", CHILD_TIMING_SOURCE, "exact", 0.9),
                ("b", CHILD_TIMING_SOURCE, "pooled", 0.9),
            ]
    assert hint.quantile_caveat == QUANTILE_CAVEAT
    assert "dependence" in hint.quantile_caveat
    with pytest.raises(FrozenInstanceError):
        hint.p50_ms = 1


def test_any_uses_min_but_stops_after_first_return():
    waiting = state("any", members=("a", "b"))
    hint = project_join_reentry(waiting, {
        "a": child("a", (10, 80, 100)),
        "b": child("b", (20, 30, 90)),
    })
    assert (hint.p10_ms, hint.p50_ms, hint.p90_ms) == (10, 30, 90)
    returned = advance_join_state(
        waiting, event(RuntimeEventKind.RETURN, child="b", join_id=None)
    )
    assert returned.satisfied
    assert returned.parent_state is InvocationState.READY
    assert returned.pending_child_ids == ("a",)
    assert not project_join_reentry(returned, {"a": child("a")}).available
    assert project_join_reentry(returned, {}).missing_reasons == (
        "join_already_satisfied",
    )
    assert advance_join_state(
        returned, event(RuntimeEventKind.RETURN, child="b")
    ) == returned


def test_all_subset_return_reprojects_current_pending_children_only():
    waiting = state()
    before = project_join_reentry(waiting, {
        "a": child("a"), "b": child("b"), "c": child("c"),
    })
    assert before.available
    updated = advance_join_state(
        waiting, event(RuntimeEventKind.RETURN, child="b")
    )
    assert updated.pending_child_ids == ("a", "c")
    assert not updated.satisfied
    assert updated.parent_state is InvocationState.WAIT_JOIN
    partial = project_join_reentry(updated, {"a": child("a")})
    assert not partial.available
    assert (partial.p10_ms, partial.p50_ms, partial.p90_ms) == (None, None, None)
    assert partial.minimum_support == "unavailable"
    assert partial.missing_reasons == ("c:missing_prediction",)
    refreshed = project_join_reentry(updated, {
        "a": child("a", (5, 15, 25)), "c": child("c", (40, 50, 60)),
    })
    assert refreshed.available
    assert refreshed.pending_child_ids == ("a", "c")
    assert (refreshed.p10_ms, refreshed.p50_ms, refreshed.p90_ms) == (40, 50, 60)
    with pytest.raises(ValueError, match="outside pending"):
        project_join_reentry(updated, {
            "a": child("a"), "b": child("b"), "c": child("c"),
        })
    for name in ("a", "c"):
        updated = advance_join_state(
            updated, event(RuntimeEventKind.RETURN, child=name)
        )
    assert updated.satisfied and updated.pending_child_ids == ()
    assert not project_join_reentry(updated, {}).available


def test_create_with_completed_member_honors_join_algebra():
    all_wait = state("all", members=("a", "b"), completed=("a",))
    assert all_wait.pending_child_ids == ("b",)
    assert project_join_reentry(all_wait, {"b": child("b")}).available
    any_wait = state("any", members=("a", "b"), completed=("a",))
    assert any_wait.parent_state is InvocationState.READY
    assert not project_join_reentry(any_wait, {}).available


def test_empirical_child_distribution_requires_full_valid_support():
    waiting = state(members=("a",))
    empirical = EmpiricalDistribution(
        (10.0, 20.0, 40.0), (0.2, 0.4, 0.4), 10.0
    )
    hint = project_join_reentry(waiting, {
        "a": child("a", empirical, support="backoff")
    })
    assert hint.available
    assert (hint.p10_ms, hint.p50_ms, hint.p90_ms) == (10, 20, 40)
    assert hint.minimum_support == "backoff"


@pytest.mark.parametrize("prediction,reason", [
    (None, "missing_prediction"),
    (replace(child("a"), support_level="unavailable"), "unsupported"),
    (replace(child("a"), support_level="structural"), "unsupported"),
    (replace(child("a"), calibration_coverage=0), "uncalibrated"),
    (replace(child("a"), calibration_coverage=float("nan")), "uncalibrated"),
    (replace(child("a"), calibration_coverage=1.1), "uncalibrated"),
    (replace(child("a"), ood_reasons=("novel_family",)), "ood"),
    (replace(child("a"), state=InvocationState.DONE), "child_not_pending"),
    (replace(child("a"), state="ready"), "child_not_pending"),
    (child("a", (10, 5, 30)), "invalid_duration"),
    (child("a", [10, 20, 30]), "invalid_duration"),
    (child("a", (True, 5, 30)), "invalid_duration"),
    (child("a", (-1, 5, 30)), "invalid_duration"),
    (child("a", (1, float("inf"), 30)), "invalid_duration"),
    (child("a", (1, 5, MAX_JOIN_WAIT_MS + 1)), "invalid_duration"),
    (child("a", EmpiricalDistribution.empty()), "invalid_duration"),
    (child("a", EmpiricalDistribution(
        (10.0, MAX_JOIN_WAIT_MS + 1), (0.9, 0.1), 3,
    )), "invalid_duration"),
    (child("a", EmpiricalDistribution(
        (10.0, float("nan")), (0.9, 0.1), 3,
    )), "invalid_duration"),
    (child("a", EmpiricalDistribution(
        (10.0, 20.0), (1.1, -0.1), 3,
    )), "invalid_duration"),
])
def test_single_unusable_child_never_yields_partial_hint(prediction, reason):
    predictions = {"b": child("b")}
    if prediction is not None:
        predictions["a"] = prediction
    hint = project_join_reentry(state(members=("a", "b")), predictions)
    assert not hint.available
    assert hint.minimum_support == "unavailable"
    assert hint.missing_reasons == (f"a:{reason}",)


@pytest.mark.parametrize("mode", ["all", "any"])
def test_no_supported_child_is_not_a_zero_duration(mode):
    hint = project_join_reentry(state(mode), {})
    assert not hint.available
    assert hint.p50_ms is None
    assert hint.missing_reasons == tuple(
        f"{name}:missing_prediction" for name in ("a", "b", "c")
    )


def test_wrong_identity_wrong_event_and_algebra_fail_closed():
    waiting = state(members=("a",))
    with pytest.raises(ValueError, match="identity"):
        project_join_reentry(waiting, {"a": child("other")})
    with pytest.raises(ValueError, match="workflow"):
        advance_join_state(
            waiting, event(RuntimeEventKind.RETURN, child="a", workflow="other")
        )
    with pytest.raises(ValueError, match="not from"):
        advance_join_state(
            waiting, event(RuntimeEventKind.RETURN, child="other")
        )
    with pytest.raises(ValueError, match="identity/state"):
        advance_join_state(
            waiting, event(RuntimeEventKind.JOIN_WAIT, child="parent")
        )
    with pytest.raises(ValueError, match="unsupported"):
        advance_join_state(
            waiting, event(RuntimeEventKind.JOIN_SATISFIED)
        )
    with pytest.raises(ValueError, match="state/algebra"):
        replace(waiting, satisfied=True)
    with pytest.raises(ValueError, match="state/algebra"):
        replace(waiting, parent_state=InvocationState.READY)
    with pytest.raises(ValueError, match="state/algebra"):
        replace(waiting, mode=JoinMode.ANY, pending_child_ids=())
    with pytest.raises(ValueError, match="outside pending"):
        project_join_reentry(waiting, {"other": child("other")})


def test_bounded_members_and_create_mode_rejection():
    with pytest.raises(ValueError, match="member_child_ids"):
        state(members=tuple(f"child-{i}" for i in range(MAX_JOIN_CHILDREN + 1)))
    with pytest.raises(ValueError, match="duplicate"):
        state(members=("a", "a"))
    with pytest.raises(ValueError, match="join mode"):
        state("quorum")
    with pytest.raises(ValueError, match="completed child"):
        state(members=("a",), completed=("other",))
    with pytest.raises(ValueError, match="expected JOIN_CREATE"):
        join_state_from_create(
            event(RuntimeEventKind.JOIN_WAIT, child="parent"),
            parent_invocation_id="parent",
        )
    with pytest.raises(ValueError, match="state/algebra"):
        JoinProjectionState(
            "wf", "parent", "j", JoinMode.ALL,
            ("a",), ("other",), InvocationState.WAIT_JOIN, False,
        )
    with pytest.raises(ValueError, match="immutable"):
        replace(state(members=("a",)), pending_child_ids=["a"])
