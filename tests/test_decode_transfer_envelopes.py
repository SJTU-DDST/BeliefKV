import pytest

from scripts.audit_decode_transfer_envelopes import (
    EnvelopeIndex, compare, exact_singletons, steady_decode, transfer_bounds,
)


def sample(forward=2, *, requests=("r",), sequence=100, ordinal=2, cost=4):
    return {
        "forward": forward, "phase": "decode", "requests": requests,
        "batch_size": len(requests), "sequence_sum": sequence, "sequence_max": sequence,
        "graph": True, "steady": True, "worker_ms": cost, "copy_bound_ms": 0,
        "transfer_class": "outside_envelopes", "singleton_ordinal": ordinal, "output_tokens": 1,
        "neighbors": (("decode", len(requests)), ("decode", len(requests))),
    }


def test_union_intersections_do_not_double_count_overlapping_transfers():
    index = EnvelopeIndex([(1, 5), (3, 8), (10, 12), (12, 13), (6, 6)])
    assert index.spans == [(1, 8), (10, 13)]
    assert index.overlap_ms(0, 14) == 10
    assert index.overlap_ms(4, 11) == 5
    assert index.overlap_ms(8, 10) == 0
    assert index.overlap_ms(5, 5) == 0
    assert EnvelopeIndex([]).overlap_ms(1, 2) == 0


@pytest.mark.parametrize("begin,end", [(3, 2), (-1, 2), (1, float("nan"))])
def test_unordered_or_unknown_envelopes_cannot_prove_no_copy(begin, end):
    with pytest.raises(ValueError):
        EnvelopeIndex([(begin, end)])


def test_unknown_positive_byte_bounds_are_distinct_from_zero_byte_operations():
    common = {"status": "completed", "direction": "h2d"}
    spans, counts = transfer_bounds([
        {**common, "actual_bytes": 4, "submit_ts_ms": 1, "complete_ts_ms": 3},
        {**common, "actual_bytes": 4, "submit_ts_ms": None, "complete_ts_ms": 5},
        {**common, "actual_bytes": 0},
    ])
    assert dict(spans) == {"h2d": [(1, 3)]}
    assert counts["unbounded_positive_byte_operations"] == 1
    assert counts["zero_byte_operations"] == 1


def test_steady_requires_exact_adjacent_forwards_and_ordered_request_identity():
    current = sample(requests=("a", "b"))
    previous, following = sample(1, requests=("a", "b")), sample(3, requests=("a", "b"))
    assert steady_decode(current, {1: previous, 3: following})
    assert not steady_decode(current, {0: previous, 3: following})
    assert not steady_decode(current, {1: previous, 3: {**following, "phase": "prefill"}})
    assert not steady_decode(current, {1: previous, 3: {**following, "requests": ("b", "a")}})


def test_sparse_geometry_stays_unknown_and_never_fills_missing_timings_with_zero():
    result = compare([sample()], [sample()], minimum=2)
    assert result["all"]["ratio"] is None
    assert result["all"]["common_weight"] == 0


def test_neighbor_matching_does_not_compare_prefill_transition_with_steady_decode():
    left = [{**sample(), "neighbors": (("decode", 1), ("prefill", 1))}]
    right = [sample()]
    assert compare(left, right, minimum=1)["all"]["common_weight"] == 1
    assert compare(left, right, minimum=1, neighbors=True)["all"]["common_weight"] == 0


def test_singleton_match_rejects_duplicates_context_changes_and_copy_intersections():
    left = [sample(ordinal=1), sample(ordinal=1), sample(ordinal=2), sample(ordinal=3)]
    right = [sample(ordinal=1), sample(ordinal=2, sequence=101), {
        **sample(ordinal=3), "transfer_class": "intersects_envelope",
    }]
    result = exact_singletons(left, right)
    assert result["matched_ordinals"] == 0
    assert result["ambiguous_common_ordinals"] == 1
    assert result["incompatible_common_ordinals"] == 1
    assert result["left"]["worker_interval_mean_ms"] is None


def test_exact_singleton_keeps_equal_work_with_different_service_time():
    result = exact_singletons([sample(cost=8)], [sample(cost=4)])
    assert result["matched_ordinals"] == result["matched_requests"] == 1
    assert result["left"]["worker_interval_sum_ms"] == 8
    assert result["right"]["worker_interval_sum_ms"] == 4
