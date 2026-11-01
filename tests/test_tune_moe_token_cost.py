import pytest

from scripts.tune_moe_token_cost import BASE_LARGE, BASE_SMALL, SIZES, guarded_lookup_table, select_candidate


def test_native_nearest_lookup_keeps_every_untuned_prefill_size_unchanged():
    table = {int(key): val for key, val in guarded_lookup_table({
        size: {**BASE_SMALL, "BLOCK_SIZE_N": 128} for size in SIZES
    }).items()}
    for size in range(25, 4097):
        nearest = min(table, key=lambda key: abs(key - size))
        assert table[nearest] == (BASE_SMALL if size <= 128 else BASE_LARGE)


def row(index, uniform, skewed, close=True):
    return {
        "candidate_index": index, "numerically_close": close,
        "scenario_median_ms": {"uniform": uniform, "skewed": skewed},
    }


@pytest.mark.parametrize("candidate", [
    row(1, .5, .5, close=False),
    row(1, .5, 1.03),
    row(1, .99, .99),
])
def test_numerical_failure_route_regression_and_weak_improvement_keep_default(candidate):
    assert select_candidate([row(0, 1, 1), candidate])["candidate_index"] == 0


def test_improvement_in_both_routing_scenarios_can_be_selected():
    assert select_candidate([row(0, 1, 1), row(1, .8, .9)])["candidate_index"] == 1
