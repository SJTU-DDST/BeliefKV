import pytest

from scripts.benchmark_decode_graph_cost import paired_summary, prepare


def test_equal_weight_blocks_and_slow_exact_choice():
    summary = paired_summary([
        {"mode": "sparse", "cuda_ms_per_forward": 10},
        {"mode": "sparse", "cuda_ms_per_forward": 12},
        {"mode": "exact", "cuda_ms_per_forward": 11},
        {"mode": "exact", "cuda_ms_per_forward": 13},
    ])
    assert summary["cuda_ms_per_forward_median_of_blocks"] == {"sparse": 11, "exact": 12}
    assert summary["exact_cuda_time_reduction_percent"] == pytest.approx(-100 / 11)


@pytest.mark.parametrize("blocks", [
    [], [{"mode": "sparse", "cuda_ms_per_forward": 10}],
    [{"mode": "sparse", "cuda_ms_per_forward": 10},
     {"mode": "exact", "cuda_ms_per_forward": 9},
     {"mode": "exact", "cuda_ms_per_forward": 8}],
])
def test_incomplete_pairs_are_rejected(blocks):
    with pytest.raises(ValueError, match="equal nonempty"):
        paired_summary(blocks)


@pytest.mark.parametrize("widths", [(), (6, 6), (0,), (25,), (True,)])
def test_invalid_shape_selection_fails_before_opening_captured_data(tmp_path, widths):
    with pytest.raises(ValueError, match="distinct captured widths"):
        prepare(tmp_path / "absent", tmp_path / "absent", tmp_path / "out", widths=widths)
