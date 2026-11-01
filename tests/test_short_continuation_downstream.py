import pytest

from scripts.audit_short_continuation_downstream import earliest_completion
from scripts.audit_short_continuation_joint_cadence import local_return


def rows():
    return [
        {"phase": "prefill", "complete_ms": 10.},
        {"phase": "prefill", "complete_ms": 20.},
        {"phase": "prefill", "complete_ms": 30.},
        {"phase": "decode", "complete_ms": 31.},
        {"phase": "prefill", "complete_ms": 40.},
        {"phase": "decode", "complete_ms": 41.},
    ]


def test_prefill_first_token_does_not_imply_request_return_before_long_chunk():
    assert earliest_completion(0, 1, rows()) == (10., 0)
    assert earliest_completion(0, 2, rows()) == (31., 1)
    assert earliest_completion(0, 3, rows()) == (41., 2)


def test_missing_decode_progress_is_unknown_not_a_zero_latency_completion():
    assert earliest_completion(0, 4, rows()) == (None, 2)


def test_combined_cadence_pays_for_each_inserted_decode_before_following_prefill():
    prefills = [{"complete_monotonic_ms": when} for when in (10., 20., 30.)]
    assert local_return(prefills, 1, 2.) == (10., 0.)
    assert local_return(prefills, 3, 2.) == (24., 4.)
    assert local_return(prefills, 5, 2.) is None


@pytest.mark.parametrize("outputs", [0, -1, True, 2.5])
def test_future_length_screen_rejects_invalid_output_work(outputs):
    with pytest.raises(ValueError):
        earliest_completion(0, outputs, rows())
