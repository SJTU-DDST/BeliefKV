import pytest

from scripts.audit_short_workflow_decode_budget import local_decode_burst, summary


def test_full_decode_burst_and_added_prefill_are_charged_before_early_return():
    result = local_decode_burst(
        100., 1000., 4, added_prefill_ms=20.,
        decode_step_ms=10., decode_cost_multiplier=2.,
    )
    assert result["extra_decode_steps"] == 3
    assert result["extra_decode_worker_cost_ms"] == 60.
    assert result["extra_worker_cost_before_original_prefill_frontier_ms"] == 80.
    assert result["hypothetical_return_ms"] == 180.
    assert result["local_request_advance_ms"] == 820.
    assert result["local_advance_exceeds_twice_extra_cost"]


def test_expensive_or_single_token_returns_do_not_get_free_compute():
    result = local_decode_burst(
        100., 150., 1, added_prefill_ms=60.,
        decode_step_ms=10., decode_cost_multiplier=1.,
    )
    assert result["hypothetical_return_ms"] == 160.
    assert result["local_request_advance_ms"] == 0.
    assert result["extra_worker_cost_before_original_prefill_frontier_ms"] == 60.


@pytest.mark.parametrize(
    "kwargs",
    [
        {"output_tokens": 0}, {"output_tokens": True},
        {"added_prefill_ms": -1.}, {"decode_step_ms": 0.},
        {"decode_cost_multiplier": .5}, {"decode_cost_multiplier": float("nan")},
    ],
)
def test_invalid_cost_assumptions_are_rejected(kwargs):
    arguments = {
        "prefill_complete_ms": 100., "original_complete_ms": 1000., "output_tokens": 10,
        "added_prefill_ms": 10., "decode_step_ms": 20., "decode_cost_multiplier": 1.,
    }
    arguments.update(kwargs)
    with pytest.raises(ValueError):
        local_decode_burst(**arguments)


def test_summary_uses_first_observed_choice_instead_of_best_future_advance():
    rows = [
        {
            "request_id": "a", "boundary_ms": when, "local_request_advance_ms": gain,
            "extra_worker_cost_before_original_prefill_frontier_ms": 10.,
            "local_advance_exceeds_twice_extra_cost": gain > 20.,
            "last_model_call": True,
        }
        for when, gain in ((200., 200.), (100., 50.))
    ]
    result = summary(rows, {"a"})
    assert result["distinct_requests"] == 1
    assert result["measured_critical_advance_ms"]["sum"] == 50.
