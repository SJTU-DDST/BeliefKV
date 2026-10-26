from copy import deepcopy
import math

import pytest

from beliefkv.experiments.agentx_trace import (
    child_request_time, explicit_join_groups, residual_ready_gap_seconds,
)


def request(start, duration=1.):
    return {"type": "s", "t": start, "api_time": duration}


def child(identity, start, end, **updates):
    return {
        "type": "subagent", "agent_id": identity, "t": start,
        "duration_ms": (end - start) * 1000., "status": "completed",
        "requests": [request(start, end - start)], **updates,
    }


def trace(*rows):
    return {"hash_id_scope": "local", "requests": list(rows)}


def test_explicit_parallel_and_tiered_joins_do_not_wait_for_background_children():
    raw = trace(
        request(0), child("a", 1, 4), child("b", 1, 5),
        child("c", 1, 9), child("background", 1, 20),
        request(6), request(12),
    )
    saved = deepcopy(raw)
    groups, background = explicit_join_groups(raw)
    assert raw == saved
    assert [group["successful_served_members"] for group in groups] == [2, 1]
    assert [group["join_before_outer_index"] for group in groups] == [5, 6]
    assert background[0]["agent_id"] == "background"
    assert background[0]["reason"] == "background_no_later_parent_request"
    assert all("inferred_join" in group["evidence"] for group in groups)


def test_failed_and_unserved_markers_do_not_qualify_as_two_successful_children():
    groups, _ = explicit_join_groups(trace(
        request(0), child("a", 1, 3), child("failed", 1, 3, status="failed"),
        child("empty", 1, 3, requests=[]), request(4),
    ))
    assert groups[0]["successful_served_members"] == 1


def test_inferred_sidecar_is_not_used_as_parent_when_author_main_indices_are_supplied():
    groups, _ = explicit_join_groups(
        trace(request(0), request(1), child("a", 2, 3), request(4), request(5)),
        main_outer_indices={0, 4},
    )
    assert groups[0]["spawn_after_outer_index"] == 0
    assert groups[0]["join_before_outer_index"] == 4


def test_legacy_relative_inner_timestamps_and_missing_duration_use_observed_end():
    row = child("a", 10, 20, duration_ms=None, requests=[request(2, 3)])
    assert child_request_time(row, row["requests"][0]) == 12
    groups, _ = explicit_join_groups(trace(request(0), row, request(16)))
    assert groups[0]["members"][0]["end_seconds"] == 15


def test_inconsistent_recorded_child_terminal_is_retained_for_rejection():
    groups, _ = explicit_join_groups(trace(
        request(0), child("a", 1, 2, requests=[request(1, 10)]), request(3),
    ))
    assert groups[0]["members"][0]["recorded_calls_exceed_declared_end"] is True


def test_replay_gap_subtracts_old_model_service_and_actual_join_dependency_once():
    previous, current = request(0, 3), request(10)
    assert residual_ready_gap_seconds(previous, current) == 7
    assert residual_ready_gap_seconds(previous, current, dependency_end_seconds=(8., 5.)) == 2
    assert residual_ready_gap_seconds(previous, current, dependency_end_seconds=(12.,)) == 0


@pytest.mark.parametrize("change", ("scope", "duplicate", "nan", "negative"))
def test_unsupported_or_corrupt_metadata_does_not_get_silently_repaired(change):
    raw = trace(request(0), child("a", 1, 2), request(3))
    if change == "scope":
        raw["hash_id_scope"] = "global"
    elif change == "duplicate":
        raw["requests"].insert(2, child("a", 1, 2))
    elif change == "nan":
        raw["requests"][1]["duration_ms"] = math.nan
    else:
        raw["requests"][0]["api_time"] = -1
    with pytest.raises(ValueError):
        explicit_join_groups(raw)
