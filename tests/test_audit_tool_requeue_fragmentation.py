from copy import deepcopy
import math

import pytest

from scripts.audit_tool_requeue_fragmentation import summarize


def episode(origin, successor, queued, served, *, tool=10., invocation="child"):
    return {
        "status": "first_service_observed", "workflow_id": "root",
        "invocation_id": invocation, "origin_request_id": origin,
        "request_id": successor, "native_queue_ts_ms": queued,
        "service_start_ts_ms": served, "native_queue_to_service_ms": served - queued,
        "tool_round_duration_ms": tool,
    }


def test_only_exact_serial_links_form_a_short_tool_chain():
    rows = [
        episode("a", "b", 0, 10), episode("b", "c", 20, 30),
        episode("missing", "d", 40, 50), episode("d", "e", 60, 70, tool=201),
        episode("e", "f", 80, 90),
    ]
    result = summarize(list(reversed(rows)))
    agent = result["agents"][0]
    assert result["summary"]["served_successor_requests"] == 5
    assert result["summary"]["served_tool_rounds"]["count"] == 1
    assert agent["longest_linked_short_tool_chain"] == 2
    assert agent["linked_pairs"] == 3
    assert agent["queue_union_ms"] == agent["queue_sum_ms"] == 50
    assert agent["short_queue_union_ms"] == 40
    assert agent["unknown_sole_blocker_snapshots"] == 5


def test_overlapping_intervals_are_retained_and_never_silently_summed():
    result = summarize([
        episode("a", "b", 0, 30), episode("b", "c", 10, 20),
        episode("c", "d", 25, 40),
    ])
    agent = result["agents"][0]
    assert agent["overlapping_queue_intervals"] == 2
    assert agent["queue_sum_ms"] == 55
    assert agent["queue_union_ms"] == 40


def test_zero_duration_queue_inside_another_wait_does_not_count_as_an_overlap():
    result = summarize([episode("a", "b", 0, 30), episode("b", "c", 10, 10)])
    assert result["agents"][0]["overlapping_queue_intervals"] == 0
    assert result["agents"][0]["queue_union_ms"] == 30


def test_siblings_and_workflows_never_form_a_serial_chain_or_global_wait_saving():
    sibling = episode("b", "c", 0, 20, invocation="sibling")
    another = {**episode("b", "d", 0, 20), "workflow_id": "another-root"}
    result = summarize([episode("a", "b", 0, 20), sibling, another])
    assert result["summary"]["agents"] == 3
    assert all(row["longest_linked_short_tool_chain"] == 1 for row in result["agents"])
    assert "jct_saved_ms" not in result


def test_unserved_and_unknown_blocker_evidence_is_preserved():
    rows = [episode("a", "b", 0, 10), {"status": "submitted_no_native_service_in_prefix"}]
    rows[0]["sole_observed_blocker_depth"] = 1
    result = summarize(rows)
    assert result["summary"]["episode_status_counts"]["submitted_no_native_service_in_prefix"] == 1
    assert result["agents"][0]["sole_blocker_snapshot_rounds"] == 1
    assert result["agents"][0]["unknown_sole_blocker_snapshots"] == 0


@pytest.mark.parametrize("value", (-1, math.nan, math.inf, True, None))
@pytest.mark.parametrize("field", ("native_queue_to_service_ms", "tool_round_duration_ms"))
def test_invalid_duration_cannot_become_a_short_tool_or_queue_observation(field, value):
    row = episode("a", "b", 0, 10)
    row[field] = value
    with pytest.raises(ValueError):
        summarize([row])


def test_ambiguous_successor_or_inconsistent_clock_is_rejected():
    row = episode("a", "b", 0, 10)
    with pytest.raises(ValueError, match="duplicate"):
        summarize([row, deepcopy(row)])
    with pytest.raises(ValueError, match="disagree"):
        summarize([{**row, "service_start_ts_ms": 11}])
    with pytest.raises(ValueError, match="missing"):
        summarize([{**row, "origin_request_id": ""}])


def test_threshold_is_explicit_and_inclusive():
    assert summarize([episode("a", "b", 0, 10, tool=200)])["agents"][0]["short_tool_rounds"] == 1
    for threshold in (-1, True, math.nan):
        with pytest.raises(ValueError):
            summarize([], short_tool_ms=threshold)
