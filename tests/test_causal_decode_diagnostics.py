from copy import deepcopy

import pytest

from scripts.plot_causal_decode_diagnostics import (
    add_batch, finish_bins, make_bins, named_paths, require_complete_prefixes,
    require_matching, weighted_cdf,
)
from scripts.audit_sealed_wait_prefetch import SealedRecords


def batch(when, tokens, *, elapsed=100., size=2):
    return {
        "complete_monotonic_ms": when, "service_elapsed_ms": elapsed,
        "batch_size": size, "request_samples": [{"token_delta": tokens}],
    }


def test_full_wall_clock_includes_warmup_idle_bins_and_partial_tail():
    bins = make_bins(125., 60)
    add_batch(bins, batch(65000., 30), anchor_ms=0., bin_s=60)
    add_batch(bins, batch(125000., 10), anchor_ms=0., bin_s=60)
    result = finish_bins(bins, 60)
    assert [row["decode_wall_tokens_per_s"] for row in result] == [0., .5, 2.]
    assert [row["partial_bin"] for row in result] == [False, False, True]
    assert result[0]["decode_worker_tokens_per_s"] is None
    assert result[-1]["end_s"] == 125.
    assert sum(row["duration_s"] for row in result) == 125.
    assert sum(row["decode_tokens"] for row in result) == 40


def test_completion_boundary_and_real_last_bin_are_preserved():
    bins = make_bins(120., 60)
    add_batch(bins, batch(61000., 12), anchor_ms=1000., bin_s=60)
    add_batch(bins, batch(121000., 13), anchor_ms=1000., bin_s=60)
    assert [row["decode_tokens"] for row in bins] == [0, 25]
    assert not any(row["partial_bin"] for row in finish_bins(bins, 60))


def test_worker_rate_is_ratio_of_sums_not_mean_of_per_batch_rates():
    bins = make_bins(60., 60)
    add_batch(bins, batch(1000., 10, elapsed=100., size=1), anchor_ms=0., bin_s=60)
    add_batch(bins, batch(2000., 30, elapsed=300., size=3), anchor_ms=0., bin_s=60)
    result = finish_bins(bins, 60)[0]
    assert result["decode_worker_tokens_per_s"] == 100.
    assert result["worker_weighted_batch"] == 2.5


@pytest.mark.parametrize("when,elapsed,tokens", [
    (-1., 10., 1), (60001., 10., 1), (1., -1., 1),
    (float("nan"), 10., 1), (1., float("inf"), 1), (1., 0., 1),
])
def test_invalid_or_out_of_window_service_does_not_disappear(when, elapsed, tokens):
    with pytest.raises(ValueError):
        add_batch(make_bins(60., 60), batch(when, tokens, elapsed=elapsed),
                  anchor_ms=0., bin_s=60)


@pytest.mark.parametrize("duration,width", [
    (0., 60), (-1., 60), (float("inf"), 60), (1., 0), (1., 1.5), (True, 60),
])
def test_invalid_window_definition_is_rejected(duration, width):
    with pytest.raises(ValueError):
        make_bins(duration, width)


def test_time_weighted_cdf_keeps_zeros_and_does_not_overweight_partial_bins():
    x, y = weighted_cdf([(0., 60.), (100., 60.), (100., 5.)])
    assert x == [0., 100.]
    assert y == pytest.approx([60 / 125, 1.])
    assert weighted_cdf([(0., 0.)]) == ([], [])


@pytest.mark.parametrize("value,weight", [
    (-1., 1.), (1., -1.), (float("nan"), 1.), (1., float("inf")),
])
def test_invalid_distribution_observation_is_rejected(value, weight):
    with pytest.raises(ValueError):
        weighted_cdf([(value, weight)])


@pytest.mark.parametrize("field", [
    "tool_timing_overlay_sha256", "exact_receipts_sha256", "source_capacity",
    "engine_tree", "seeds", "source_plan", "free_page_h2d_fence",
])
def test_comparison_rejects_changed_work_timing_or_common_engine(field):
    contract = {name: "same" for name in (
        "tool_timing_overlay_sha256", "exact_receipts_sha256", "source_capacity",
        "engine_tree", "seeds", "source_plan", "free_page_h2d_fence",
    )}
    arms = {"full": {"match_contract": contract},
            "baseline": {"match_contract": deepcopy(contract)}}
    require_matching(arms)
    arms["baseline"]["match_contract"][field] = "changed"
    with pytest.raises(ValueError, match=field):
        require_matching(arms)


def test_named_input_paths_preserve_labels_and_reject_duplicates():
    assert str(named_paths(["Full V286=/tmp/full"])["Full V286"]) == "/tmp/full"
    for values in (["bad"], ["=/tmp/full"], ["full="], ["full=a", "full=b"]):
        with pytest.raises(ValueError):
            named_paths(values)


@pytest.mark.parametrize("change", (None, "partial", "append", "replace_same_size"))
def test_completed_figure_inputs_reject_partial_or_mutated_records(tmp_path, change):
    path = tmp_path / "records.jsonl"
    path.write_bytes(b'{"value":1}\n')
    if change == "partial":
        path.write_bytes(b'{"value":1}\n{"val')
    reader = SealedRecords()
    list(reader(path))
    if change == "append":
        path.write_bytes(b'{"value":1}\n{"value":2}\n')
    elif change == "replace_same_size":
        path.write_bytes(b'{"value":2}\n')
    if change is None:
        require_complete_prefixes(reader.prefixes)
    else:
        with pytest.raises(ValueError, match="completed figure input"):
            require_complete_prefixes(reader.prefixes)
