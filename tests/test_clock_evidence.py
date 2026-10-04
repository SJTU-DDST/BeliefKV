from types import SimpleNamespace

import pytest

from beliefkv.runtime import clock_evidence
from scripts.child_stream_service_index import snapshot_status


@pytest.fixture(autouse=True)
def clear_clock_cache():
    clock_evidence.local_monotonic_clock_domain.cache_clear()
    yield
    clock_evidence.local_monotonic_clock_domain.cache_clear()


def test_clock_domain_requires_same_boot_and_time_namespace(monkeypatch):
    monkeypatch.setattr(clock_evidence.time, "get_clock_info", lambda _: SimpleNamespace(
        implementation="clock_gettime(CLOCK_MONOTONIC)",
    ))
    monkeypatch.setattr(clock_evidence.Path, "read_text", lambda _: "00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(clock_evidence.os, "readlink", lambda _: "time:[1]")
    first = clock_evidence.local_monotonic_clock_domain()
    assert len(first) == 64
    clock_evidence.local_monotonic_clock_domain.cache_clear()
    monkeypatch.setattr(clock_evidence.os, "readlink", lambda _: "time:[2]")
    assert clock_evidence.local_monotonic_clock_domain() != first
    clock_evidence.local_monotonic_clock_domain.cache_clear()
    monkeypatch.setattr(clock_evidence.time, "get_clock_info", lambda _: SimpleNamespace(
        implementation="unknown_clock",
    ))
    assert clock_evidence.local_monotonic_clock_domain() is None


def test_pre_eos_status_prefers_monotonic_evidence_only_when_domain_matches():
    state = {
        "server_end_ms": 1200., "offset_lower_ms": 0., "offset_upper_ms": 0.,
        "decode_sample_times_ms": [890.], "server_end_monotonic_ms": 950.,
        "monotonic_clock_domain": "kernel-A", "decode_sample_monotonic_times_ms": [925.],
    }
    assert snapshot_status(state, 1000.) == "unfinished_with_recent_decode"
    assert snapshot_status(state, 1000., clock_domain="kernel-B") == "unfinished_with_recent_decode"
    assert snapshot_status(state, 1000., clock_domain="kernel-A") == "server_finished_before_trigger"
    assert snapshot_status(state, 940., clock_domain="kernel-A") == "unfinished_with_recent_decode"
