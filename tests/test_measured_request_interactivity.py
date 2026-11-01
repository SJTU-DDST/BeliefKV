import pytest

from scripts.audit_measured_request_interactivity import normalized_interactivity


def request(seconds, tokens=1):
    return {
        "submitted_monotonic_ms": 1000., "http_complete_monotonic_ms": 1000 + seconds * 1000,
        "output_tokens": tokens,
    }


def test_inverse_P90_of_seconds_per_output_is_not_P90_of_token_rates():
    result = normalized_interactivity([request(1), request(10)])
    assert result["complete_request_seconds_per_output_token"]["p90"] == pytest.approx(9.1)
    assert result["E2E_normalized_interactivity_tokens_per_second"] == pytest.approx(1 / 9.1)


def test_complete_request_time_including_first_token_is_normalized_by_all_outputs():
    result = normalized_interactivity([request(20, 10)])
    assert result["E2E_normalized_interactivity_tokens_per_second"] == .5


def test_zero_output_is_explicitly_undefined_and_not_a_fast_request():
    result = normalized_interactivity([request(1, 0)])
    assert result["undefined_zero_output_requests"] == 1
    assert result["E2E_normalized_interactivity_tokens_per_second"] is None


@pytest.mark.parametrize("row", [
    request(-1), request(0), request(1, -1), request(1, True), request(float("nan")),
])
def test_invalid_request_timings_or_token_counts_are_rejected(row):
    with pytest.raises(ValueError):
        normalized_interactivity([row])
