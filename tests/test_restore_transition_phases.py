import pytest

from scripts.audit_restore_transition_phases import restore_phase_groups


def sample(identity="r", *, size=1, extend=100):
    return {
        "request_ids": [identity], "batch_size": size, "extend_tokens": extend,
        "prefix_tokens": 20000, "forward_iter": 1, "phases": {}, "metrics": {},
        "gpu_span_status": "unknown", "restore_wait_status": "not_observed",
    }


def event(identity="r", *, full=0, mamba=None):
    return {
        "kind": "llm_submit", "attributes": {
            "request_id": identity, "cached_tokens_host": full,
            "mamba_host_hit_slots": mamba,
        },
    }


@pytest.mark.parametrize("full,mamba,status", [
    (100, None, "positive_full_host"),
    (0, 1, "positive_mamba_host_without_positive_full"),
    (0, 0, "known_zero_host_counters"),
    (0, None, "zero_full_host_mamba_unknown"),
    (None, None, "host_counters_unobserved"),
])
def test_full_and_mamba_counters_retain_unknown_states(full, mamba, status):
    result = restore_phase_groups([sample()], [event(full=full, mamba=mamba)])
    assert list(result["by_host_counter"]) == [status]
    assert result["joined_rows"][0]["status"] == status


def test_only_exact_request_identity_associates_and_nonshort_batches_are_excluded():
    result = restore_phase_groups(
        [sample(), sample("other"), sample("long", extend=512), sample("batch", size=2)],
        [event(full=100)],
    )
    assert result["short_prefill_samples"] == 2
    assert [row["status"] for row in result["joined_rows"]] == [
        "positive_full_host", "host_counters_unobserved",
    ]


def test_duplicate_or_invalid_counters_do_not_silently_change_attribution():
    with pytest.raises(ValueError, match="duplicate"):
        restore_phase_groups([], [event(), event()])
    with pytest.raises(ValueError, match="invalid Host"):
        restore_phase_groups([], [event(full=-1)])
    with pytest.raises(ValueError, match="invalid Host"):
        restore_phase_groups([], [event(mamba=True)])
