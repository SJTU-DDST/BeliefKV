from copy import deepcopy
import hashlib
import json

import pytest

from scripts.analyze_prefill_overlap_neighbors import analyze as analyze_neighbors
from scripts.analyze_prefill_transition_gaps import analyze, load_window, partition
from tests.test_analyze_prefill_overlap_neighbors import sample_and_index


def phase(start, end, cpu=1.):
    return {
        "start_monotonic_ms": start, "end_monotonic_ms": end,
        "wall_ms": end - start, "thread_cpu_ms": cpu,
    }


def evidence():
    sample, index = sample_and_index()
    neighbor = analyze_neighbors([sample], index)["samples"][0]
    return sample, neighbor


def test_partition_clips_and_unions_intervals_without_inventing_cpu_allocation():
    result = partition(10., 20., {
        "earlier": phase(8., 15., 6.), "later": phase(12., 18., 4.),
    })
    assert result["measured_wall_union_ms"] == 8.
    assert result["unmeasured_wall_ms"] == 2.
    assert result["whole_phase_thread_cpu_ms"] == {"later": 4.}
    assert result["phase_wall_ms"] == {"earlier": 5., "later": 6.}
    assert sum(row["wall_ms"] for row in result["segments"]) == 10.
    assert any(row["phases"] == ["earlier", "later"] for row in result["segments"])


def test_each_sample_has_exact_additive_wall_accounting():
    sample, neighbor = evidence()
    sample["phases"]["post_launch_physical"] = phase(125., 128., 2.)
    neighbor["previous"]["complete_monotonic_ms"] = 123.
    result = analyze([sample], [neighbor])
    row = result["samples"][0]
    assert row["metrics"] == {
        "gap_ms": 11., "measured_wall_union_ms": 4., "unmeasured_wall_ms": 7.,
        "before_previous_observer_entry_ms": 4.,
        "after_previous_observer_entry_ms": 7.,
        "late_measured_wall_union_ms": 3., "late_unmeasured_wall_ms": 4.,
    }
    assert row["partition"]["whole_phase_thread_cpu_ms"]["post_launch_physical"] == 2.
    assert result["decode_short_prefill_decode"]["sample_count"] == 1


@pytest.mark.parametrize("observer,relation", ((None, "missing"), (118., "outside"), (131., "outside")))
def test_unknown_observer_retains_gap_but_never_fabricates_late_accounting(observer, relation):
    sample, neighbor = evidence()
    neighbor["previous"]["complete_monotonic_ms"] = observer
    result = analyze([sample], [neighbor])
    row = result["samples"][0]
    assert row["previous_observer_relation"] == relation
    assert row["late_partition"] is None
    assert row["metrics"]["gap_ms"] == 11.
    assert "after_previous_observer_entry_ms" not in row["metrics"]


@pytest.mark.parametrize("field", ("request_ids", "prefix_tokens", "extend_tokens", "loop", "graph"))
def test_neighbor_and_phase_identity_must_agree(field):
    sample, neighbor = evidence()
    neighbor[field] = ["different"] if field == "request_ids" else "different"
    result = analyze([sample], [neighbor])
    assert result["samples"] == []
    assert result["rejected"] == {"sample_identity_or_geometry_disagreement": 1}


@pytest.mark.parametrize("kind", ("phase_duplicate", "neighbor_duplicate", "clock_domain"))
def test_ambiguous_or_cross_clock_samples_are_not_paired(kind):
    sample, neighbor = evidence()
    samples, neighbors = [sample], [neighbor]
    if kind == "phase_duplicate":
        samples.append(deepcopy(sample))
    elif kind == "neighbor_duplicate":
        neighbors.append(deepcopy(neighbor))
    else:
        sample["monotonic_clock_domain"] = "other-clock"
    result = analyze(samples, neighbors)
    assert not result["samples"]
    assert result["rejected"]["missing_or_ambiguous_identity"] == len(neighbors)


def test_slot_matched_cohort_and_missing_measured_phase_keep_explicit_coverage():
    sample, neighbor = evidence()
    neighbor["previous"]["batch_size"] = 47
    neighbor["following"]["batch_size"] = 48
    del sample["phases"]["post_launch_physical"]
    result = analyze([sample], [neighbor])
    cohort = result["decode47_prefill1_decode48"]
    assert cohort["sample_count"] == 1
    assert cohort["phase_observation_coverage"]["post_launch_physical"] == 0
    assert cohort["phase_wall_ms"]["post_launch_physical"]["count"] == 0
    assert cohort["whole_phase_thread_cpu_ms"]["post_launch_physical"]["count"] == 0
    assert cohort["metrics"]["unmeasured_wall_ms"]["mean_ms"] == 10.


def test_physical_phase_after_successor_entry_is_not_charged_to_gap():
    sample, neighbor = evidence()
    sample["phases"]["post_launch_physical"] = phase(135., 140., 4.)
    result = analyze([sample], [neighbor])
    row = result["samples"][0]
    assert row["metrics"]["measured_wall_union_ms"] == 1.
    assert "post_launch_physical" not in row["partition"]["whole_phase_thread_cpu_ms"]


@pytest.mark.parametrize("bad_end", (118., 144., float("inf")))
def test_invalid_successor_order_is_rejected(bad_end):
    sample, neighbor = evidence()
    neighbor["following"]["launch_monotonic_ms"] = bad_end
    result = analyze([sample], [neighbor])
    assert result["samples"] == []
    assert result["rejected"] == {"invalid_successor_chronology": 1}


def test_report_hash_chain_is_verified_without_reopening_raw_trace(tmp_path):
    sample, neighbor = evidence()
    busy = tmp_path / "busy.json"
    busy.write_text(json.dumps({"arms": {"predictive": {
        "source": "raw-not-present.jsonl", "read_prefix_bytes": 5,
        "read_prefix_sha256": "previously-sealed", "monitor_window": {},
    }}}))
    busy_digest = hashlib.sha256(busy.read_bytes()).hexdigest()
    phases = tmp_path / "phases.json"
    phases.write_text(json.dumps({
        "busy_report": str(busy), "busy_report_sha256": busy_digest,
        "same_version": False, "arms": {"predictive": {"samples": [sample]}},
    }))
    neighbors = tmp_path / "neighbors.json"
    neighbors.write_text(json.dumps({
        "phase_report": str(phases),
        "phase_report_sha256": hashlib.sha256(phases.read_bytes()).hexdigest(),
        "busy_report": str(busy), "busy_report_sha256": busy_digest,
        "same_version": False, "arms": {"predictive": {"samples": [neighbor]}},
    }))
    result = load_window(neighbors)
    assert result["arms"]["predictive"]["decode_short_prefill_decode"]["sample_count"] == 1
    busy.write_text("{}")
    with pytest.raises(ValueError, match="busy report hash changed"):
        load_window(neighbors)
