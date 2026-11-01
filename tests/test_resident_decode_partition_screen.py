import numpy as np
import pytest

from scripts.audit_resident_decode_partition import (
    geometry, membership_split, segment_ms, simulate_frontier, two_class_savings,
)
from scripts.audit_resident_phase_sensitivity import classify_frontier
from scripts.audit_tool_window_backfill import interval_union_ms


BUCKETS = [1, 2, 4, 8, 12, 16, 24]


def member(rid, workflow, remaining, *, short, context=10000):
    return {
        "request_id": rid, "workflow_id": workflow, "remaining": remaining,
        "short": short, "context": context,
    }


def test_two_cohort_condition_accounts_for_delayed_long_work():
    assert two_class_savings(10, 2, 40, 20, 38, 10) == 1640
    assert two_class_savings(1, 10, 40, 20, 38, 10) == -1600


def test_linear_segment_includes_context_growth_instead_of_freezing_initial_cost():
    coefficients = np.array([1., 0., 0., 1., 0.])
    assert segment_ms([100000], 3, coefficients, BUCKETS) == pytest.approx(6.00003)


def test_work_is_conserved_and_short_first_pays_the_second_batch_cost():
    members = [member("s", "short", 2, short=True), member("l", "long", 10, short=False)]
    coefficients = np.array([1., 0., 0., 0., 0.])
    base = simulate_frontier(members, coefficients, BUCKETS)
    split = simulate_frontier(members, coefficients, BUCKETS, short_first=True)
    assert base["request_finish_ms"] == {"s": 2., "l": 10.}
    assert split["request_finish_ms"] == {"s": 2., "l": 12.}
    assert split["frontier_workflow_mean_ms"] > base["frontier_workflow_mean_ms"]
    assert base["output_tokens"] == split["output_tokens"] == 12
    assert members[0]["remaining"] == 2


def test_workflow_aggregation_uses_the_last_current_member_and_equal_workflow_weights():
    members = [
        member("s1", "short", 1, short=True),
        member("s2", "short", 4, short=True),
        member("l", "long", 10, short=False),
    ]
    result = simulate_frontier(members, np.array([1., 0., 0., 0., 0.]), BUCKETS)
    assert result["frontier_workflow_finish_ms"] == {"short": 4., "long": 10.}
    assert result["frontier_workflow_mean_ms"] == 7.


@pytest.mark.parametrize("cadence", [1, 8, 32])
def test_bounded_extra_short_progress_preserves_every_token_and_long_progress(cadence):
    members = [member("s", "short", 45, short=True), member("l", "long", 60, short=False)]
    result = simulate_frontier(
        members, np.array([1., 0., 0., 0., 0.]), BUCKETS, extra_short_every=cadence,
    )
    assert result["output_tokens"] == 105
    assert result["request_finish_ms"]["l"] >= 60.
    assert result["request_finish_ms"]["s"] == 45.


def test_extra_short_steps_accelerate_short_work_only_when_the_smaller_batch_is_faster():
    members = [member("s", "short", 45, short=True), member("l", "long", 60, short=False)]
    coefficients = np.array([0., 24., 0., 0., 0.])
    base = simulate_frontier(members, coefficients, BUCKETS)
    result = simulate_frontier(members, coefficients, BUCKETS, extra_short_every=8)
    assert result["request_finish_ms"]["s"] < base["request_finish_ms"]["s"]
    assert result["request_finish_ms"]["l"] >= base["request_finish_ms"]["l"]
    assert result["output_tokens"] == base["output_tokens"]


def test_graph_padding_cost_uses_the_observed_bucket():
    assert geometry([1000] * 3, BUCKETS)[2] == 4 / 24
    assert geometry([1000] * 5, BUCKETS)[2] == 8 / 24


def test_membership_holdout_does_not_split_adjacent_steps_of_the_same_request_set():
    rows = [member("a", "w1", 8, short=True), member("b", "w2", 20, short=False)]
    assert membership_split(rows) == membership_split(list(reversed(rows)))
    rows[0]["remaining"] = 2
    assert membership_split(rows) == membership_split(list(reversed(rows)))


def test_phase_classification_keeps_all_current_workflow_members_in_one_class():
    rows = [
        member("a1", "a", 1, short=False), member("a2", "a", 100, short=False),
        member("b", "b", 10, short=False), member("c", "c", 40, short=False),
    ]
    assert [row["short"] for row in classify_frontier(rows)] == [False, False, True, True]
    assert all(not row["short"] for row in rows)


def test_tool_cost_counts_parallel_calls_once_and_keeps_separate_rounds():
    assert interval_union_ms([(10., 20.), (12., 18.), (15., 25.), (30., 35.)]) == 20.
    assert interval_union_ms([]) == 0.
    with pytest.raises(ValueError):
        interval_union_ms([(10., 5.)])


@pytest.mark.parametrize("bad", [[], [member("a", "w", 0, short=True)]])
def test_no_zero_work_or_missing_frontier_can_be_called_a_success(bad):
    with pytest.raises(ValueError):
        simulate_frontier(bad, np.ones(5), BUCKETS)
