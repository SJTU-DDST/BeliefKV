from copy import deepcopy

import pytest

from scripts.audit_resident_decode_prefill_frontiers import (
    closed_bursts,
    completion_proxy,
    join_completion_skew,
    last_outstanding_child,
    retained_members,
)


def member(**changes):
    return {
        "request_id": "request", "workflow_id": "workflow",
        "invocation_id": "child", "context_id": "context", "context_epoch": 0,
        "attempt_id": 0, "session_id": "session", "session_generation": 2,
        "phase": "decode", "output_tokens_before": 9, "output_tokens_after": 10,
        **changes,
    }


def test_bursts_require_observed_decode_at_both_boundaries():
    rows = [{"phase": phase} for phase in (
        "prefill", "decode", "prefill", "prefill", "decode", "prefill",
    )]
    assert list(closed_bursts(rows)) == [(rows[1], tuple(rows[2:4]), rows[4])]


def test_retained_member_requires_identity_and_unchanged_output():
    before = {"request_samples": [member()]}
    after = {"request_samples": [member(output_tokens_before=10, output_tokens_after=11)]}
    retained = list(retained_members(before, [], after))
    assert retained[0]["request_id"] == "request"
    assert retained[0]["full_state_identity_observed"] is True
    changed = deepcopy(after)
    changed["request_samples"][0]["session_generation"] += 1
    assert not list(retained_members(before, [], changed))
    changed = deepcopy(after)
    changed["request_samples"][0]["attempt_id"] += 1
    assert not list(retained_members(before, [], changed))
    changed = deepcopy(after)
    changed["request_samples"][0]["output_tokens_before"] += 1
    with pytest.raises(ValueError, match="unrecorded"):
        list(retained_members(before, [], changed))


def test_member_needing_its_own_prefill_is_not_resident_opportunity():
    before = {"request_samples": [member()]}
    after = {"request_samples": [member(output_tokens_before=10)]}
    prefill = {"request_samples": [member(phase="prefill")]}
    assert not list(retained_members(before, [prefill], after))


def test_incomplete_identity_does_not_prove_continuous_residency():
    before = {"request_samples": [member(context_id=None)]}
    assert not list(retained_members(before, [], {"request_samples": [member()]}))


def test_omitted_extended_identity_remains_explicitly_unknown():
    before = {"request_samples": [member(session_id=None, attempt_id=None)]}
    after = {"request_samples": [member(output_tokens_before=10)]}
    retained = list(retained_members(before, [], after))
    assert retained[0]["full_state_identity_observed"] is False


def test_overlap_uses_reported_output_position_instead_of_stale_launch_count():
    before = {"request_samples": [member()]}
    after = {"request_samples": [member(
        output_tokens_before=9, output_tokens_reported_before=10,
    )]}
    assert list(retained_members(before, [], after))


def test_completion_exposure_pays_for_extra_decode_and_cannot_be_negative():
    assert completion_proxy(100., 200., 3, 20.) == {
        "extra_decode_worker_proxy_ms": 60.,
        "local_burst_exposure_removed_proxy_ms": 40.,
    }
    assert completion_proxy(100., 200., 6, 20.)["local_burst_exposure_removed_proxy_ms"] == 0.
    with pytest.raises(ValueError):
        completion_proxy(100., 200., 0, 20.)


def test_last_child_uses_only_observed_wait_and_already_returned_peers():
    joins = {"join": {
        "mode": "all", "members": ("child", "peer"),
        "wait_ms": 10., "satisfied_ms": 100.,
    }}
    returned = {"peer": 30., "child": 100.}
    assert not last_outstanding_child("child", 9., joins, returned)
    assert not last_outstanding_child("child", 20., joins, returned)
    assert last_outstanding_child("child", 40., joins, returned)
    assert not last_outstanding_child("child", 100., joins, returned)


def test_unknown_peer_return_never_creates_last_child_hint():
    joins = {"join": {
        "mode": "all", "members": ("child", "peer"),
        "wait_ms": 10., "satisfied_ms": None,
    }}
    assert not last_outstanding_child("child", 40., joins, {})


def test_join_skew_is_separate_from_intrinsic_tool_and_worker_exposure():
    joins = {"join": {
        "mode": "all", "members": ("child", "peer"), "wait_ms": 10.,
        "satisfied_ms": 101., "workflow_id": "workflow", "parent_invocation_id": "parent",
    }}
    returned = {"peer": 30., "child": 100.}
    events = [
        {"kind": kind, "invocation_id": "child", "ts_ms": when, "attributes": {"tool_run_id": "tool"}}
        for kind, when in (("tool_start", 20.), ("tool_end", 60.))
    ]
    forwards = [{
        "request_samples": [member()], "complete_monotonic_ms": 80.,
        "service_elapsed_ms": 10.,
    }]
    result = join_completion_skew(events, forwards, joins, returned, {"workflow"})
    row = result["groups"][0]
    assert row["first_to_last_return_ms"] == 70.
    assert row["last_child_own_worker_overlap_ms"] == 10.
    assert row["last_child_tool_wait_overlap_ms"] == 30.
    assert result["summaries"]["measured_multi_member"]["groups"] == 1
