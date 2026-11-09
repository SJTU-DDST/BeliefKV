import pytest

from scripts.audit_recovery_wait_paths import pending_phases


def episode(when, key):
    return {
        "staged_monotonic_ms": when, "native_submit_ts_ms": key,
        "native_node_ids": [key], "native_h2d_bytes": 100,
        "monotonic_clock_domain": "clock",
    }


def test_staging_phase_unions_overlapping_copies_and_keeps_remaining_service_wait():
    episodes = [episode(10, 1), episode(20, 2), episode(110, 3)]
    ready = {
        (1, (1,), 100): {"monotonic_ms": 50, "monotonic_clock_domain": "clock"},
        (2, (2,), 100): {"monotonic_ms": 80, "monotonic_clock_domain": "clock"},
    }
    result = pending_phases(0, 100, episodes, ready)
    assert result["staging_episodes"] == 2
    assert result["staging_to_observed_ACK_union_ms"] == 70
    assert result["submit_to_first_staging_ms"] == 10
    assert result["last_observed_staged_ACK_to_service_ms"] == 20


def test_missing_ready_observation_cannot_be_called_copy_completion():
    result = pending_phases(0, 100, [episode(10, 1)], {})
    assert result["staging_episodes_without_ready_observation"] == 1
    assert not result["all_staged_ACKs_observed_before_service"]
    assert result["last_observed_staged_ACK_to_service_ms"] is None


def test_initial_wait_excludes_copies_staged_after_first_model_service():
    assert pending_phases(0, 100, [episode(110, 1)], {}) == {
        "staging_before_first_service": False,
    }


@pytest.mark.parametrize("clock,finish", (("other", 50), ("clock", 5)))
def test_phase_clock_and_chronology_require_exact_evidence(clock, finish):
    with pytest.raises(ValueError):
        pending_phases(0, 100, [episode(10, 1)], {
            (1, (1,), 100): {"monotonic_ms": finish, "monotonic_clock_domain": clock},
        })
