from collections import Counter

import pytest

from scripts.audit_decode_batch_efficiency import (
    accumulate, eligibility_at, padding_bucket, summarize,
)


BUCKETS = [1, 2, 4, 8, 12, 16, 24]


def sample(size, elapsed, *, output=None, graph=True):
    return {
        "batch_size": size, "service_elapsed_ms": elapsed,
        "cuda_graph_replayed": graph,
        "request_samples": [
            {
                "request_id": str(index), "sequence_tokens_before": 1000,
                "token_delta": 1 if output is None else output[index],
                "token_delta_semantics": "observed_output_ids_delta",
            }
            for index in range(size)
        ],
    }


def test_actual_work_is_not_padded_batch_and_terminal_steps_generate_no_output():
    counts = Counter()
    accumulate(counts, sample(3, 10., output=[1, 0, 1]), BUCKETS)
    result = summarize(counts)
    assert result["request_steps"] == 3
    assert result["output_tokens"] == 2
    assert result["configured_bucket_rows"] == 4
    assert result["output_tokens_per_worker_interval_second"] == 200.
    assert result["configured_padding_rows_per_real_graph_row"] == pytest.approx(1. / 3.)


def test_batch_average_and_worker_weighted_average_have_distinct_denominators():
    counts = Counter()
    accumulate(counts, sample(1, 10.), BUCKETS)
    accumulate(counts, sample(8, 30.), BUCKETS)
    result = summarize(counts)
    assert result["mean_batch"] == 4.5
    assert result["worker_weighted_mean_batch"] == 6.25
    assert result["output_tokens_per_worker_interval_second"] == 225.


@pytest.mark.parametrize("size,padded", [(1, 1), (3, 4), (5, 8), (7, 8), (9, 12), (24, 24)])
def test_configured_bucket_mapping(size, padded):
    assert padding_bucket(size, BUCKETS, graph=True) == padded


def test_non_graph_work_does_not_infer_padding():
    counts = Counter()
    accumulate(counts, sample(3, 10., graph=False), BUCKETS)
    result = summarize(counts)
    assert result["graph_batch_fraction"] == 0.
    assert result["configured_padding_rows_per_real_graph_row"] is None
    assert padding_bucket(100, BUCKETS, graph=False) is None


@pytest.mark.parametrize("buckets", [[], [2, 1], [1, 1], [1, True], [0, 1]])
def test_invalid_graph_configuration_is_rejected(buckets):
    with pytest.raises(ValueError):
        padding_bucket(1, buckets, graph=True)


def test_uncovered_graph_size_is_rejected():
    with pytest.raises(ValueError, match="exceeds"):
        padding_bucket(25, BUCKETS, graph=True)


def test_no_padding_configuration_requires_exact_graph_bucket():
    with pytest.raises(ValueError, match="no exact bucket"):
        accumulate(Counter(), sample(3, 10.), BUCKETS, disable_padding=True)


@pytest.mark.parametrize("field,value", [
    ("token_delta", -1), ("token_delta", True),
    ("sequence_tokens_before", None), ("token_delta_semantics", "prefill_extend_input_len"),
])
def test_invalid_or_prefill_work_cannot_be_counted_as_decode(field, value):
    row = sample(1, 10.)
    row["request_samples"][0][field] = value
    with pytest.raises(ValueError, match="observed output"):
        accumulate(Counter(), row, BUCKETS)


@pytest.mark.parametrize("elapsed", [-1., float("nan"), float("inf"), True])
def test_invalid_worker_duration_is_rejected(elapsed):
    with pytest.raises(ValueError):
        accumulate(Counter(), sample(1, elapsed), BUCKETS)


def test_missing_future_and_stale_snapshots_do_not_imply_inactive_eligibility():
    states = [{"ts_ms": 100., "agent_admission": {"active_invocation_ids": ["active"]}}]
    assert eligibility_at(50., states, [100.]) is None
    assert eligibility_at(1700., states, [100.]) is None
    assert eligibility_at(150., states, [100.]) == {"active"}
    assert eligibility_at(150., [{"ts_ms": 100., "agent_admission": None}], [100.]) is None
