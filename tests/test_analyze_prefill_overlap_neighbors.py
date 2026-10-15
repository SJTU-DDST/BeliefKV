from copy import deepcopy
import hashlib
import json

import pytest

from scripts.analyze_prefill_overlap_neighbors import analyze, read_neighbors
from scripts.analyze_prefill_turnover import analyze_samples, key
from tests.test_analyze_prefill_turnover import evidence


def sample_and_index():
    probe, service, _ = evidence()
    sample = analyze_samples(
        [probe], [service], [], begin=1000., end=1100.,
    )["samples"][0]
    previous = deepcopy(service)
    previous.update(forward_iter=16, phase="decode", launch_monotonic_ms=90.,
                    complete_monotonic_ms=115.)
    following = deepcopy(service)
    following.update(forward_iter=18, phase="decode", launch_monotonic_ms=130.,
                     complete_monotonic_ms=165.)
    return sample, {key(row): [row] for row in (previous, service, following)}


def test_next_decode_launch_splits_delivery_gap_without_adding_overlapping_phases():
    sample, index = sample_and_index()
    result = analyze([sample], index)
    assert not result["rejected"]
    row = result["samples"][0]
    assert row["next_launch_relation"] == "next_launch_inside_delivery_gap"
    assert row["previous"]["phase"] == row["following"]["phase"] == "decode"
    assert row["metrics"] == {
        "run_return_to_result_start_ms": 24.,
        "run_return_to_next_launch_ms": 11.,
        "next_launch_to_result_start_ms": 13.,
        "current_post_launch_physical_overlap_ms": 3.,
        "current_war_barrier_overlap_ms": 1.,
    }


def test_consecutive_prefill_can_process_current_result_before_next_launch():
    sample, index = sample_and_index()
    following = index[("boot/time-ns", 18)][0]
    following.update(phase="prefill", launch_monotonic_ms=151.)
    result = analyze([sample], index)
    row = result["samples"][0]
    assert row["next_launch_relation"] == "result_started_before_next_launch"
    assert "run_return_to_next_launch_ms" not in row["metrics"]
    assert result["short_single_request"]["metrics"]["next_launch_to_result_start_ms"]["count"] == 0


@pytest.mark.parametrize("change,expected", (
    ("different_clock", "missing"),
    ("nonadjacent", "missing"),
    ("duplicate", "ambiguous"),
    ("early", "invalid_next_launch_chronology"),
))
def test_unknown_or_unordered_successor_does_not_use_a_nearby_forward(change, expected):
    sample, index = sample_and_index()
    following = index.pop(("boot/time-ns", 18))
    if change == "different_clock":
        index[("another-clock", 18)] = following
    elif change == "nonadjacent":
        index[("boot/time-ns", 19)] = following
    elif change == "duplicate":
        index[("boot/time-ns", 18)] = following * 2
    else:
        following[0]["launch_monotonic_ms"] = 110.
        index[("boot/time-ns", 18)] = following
    row = analyze([sample], index)["samples"][0]
    assert row["next_launch_relation"] == expected
    assert "next_launch_to_result_start_ms" not in row["metrics"]


def test_current_request_identity_mismatch_is_rejected():
    sample, index = sample_and_index()
    index[("boot/time-ns", 17)][0]["request_samples"][0]["request_id"] = "another"
    assert analyze([sample], index)["samples"] == []


def test_sealed_prefix_keeps_only_requested_rows_and_ignores_appended_neighbors(tmp_path):
    _, index = sample_and_index()
    rows = [row[0] for row in index.values()]
    raw = b"".join((json.dumps(row) + "\n").encode() for row in rows[:2])
    path = tmp_path / "audit.jsonl"
    path.write_bytes(raw + (json.dumps(rows[2]) + "\n").encode())
    metadata = {
        "source": str(path), "read_prefix_bytes": len(raw),
        "read_prefix_sha256": hashlib.sha256(raw).hexdigest(),
    }
    wanted = set(index)
    assert set(read_neighbors(metadata, wanted)) == {("boot/time-ns", 16), ("boot/time-ns", 17)}
    path.write_bytes(raw.replace(b"service-a", b"service-b"))
    with pytest.raises(ValueError, match="hash changed"):
        read_neighbors(metadata, wanted)
