from copy import deepcopy
import gzip
import hashlib
import json

import pytest

from scripts.analyze_prefill_turnover import (
    analyze_samples, distribution, read_lifecycle_prefix, read_sealed,
)


def phase(start, end, cpu):
    return {
        "start_monotonic_ms": start, "end_monotonic_ms": end,
        "wall_ms": end - start, "thread_cpu_ms": cpu,
    }


def evidence():
    service = {
        "event": "gpu_service_sample", "phase": "prefill", "sample_id": "service-a",
        "forward_iter": 17, "monotonic_clock_domain": "boot/time-ns",
        "batch_size": 1, "request_samples": [{
            "request_id": "request-a", "prefill_geometry_source": "native_batch",
            "token_delta": 80, "sequence_tokens_before": 256,
        }],
        "cuda_graph_replayed": True, "launch_monotonic_ms": 105.,
        "complete_monotonic_ms": 149., "complete_ts_ms": 1049.,
    }
    probe = {
        "event": "prefill_turnover_sample", "forward_iter": 17,
        "monotonic_clock_domain": "boot/time-ns", "request_ids": ["request-a"],
        "batch_size": 1, "extend_tokens": 80, "prefix_tokens": 256,
        "loop": "overlap", "sampling_interval": 16, "result_processed": True,
        "launch_monotonic_ms": 105., "ts_ms": 5000.,
        "gpu_span_status": "observed", "gpu_run_batch_span_ms": 12.5,
        "phases": {
            "selection": phase(100, 102, 1),
            "run_batch": phase(105, 119, 8),
            "launch_telemetry": phase(105, 109, 2),
            "war_barrier_and_queue_publish": phase(119, 120, .5),
            "post_launch_physical": phase(120, 123, 2),
            "result_processing": phase(143, 150, 3),
        },
    }
    wait = {
        "event": "gpu_restore_dependency_wait", "forward_iter": 17,
        "request_ids": ["request-a"], "batch_size": 1, "ts_ms": 1010.,
        "gpu_layer_dependency_wait_ms": .09,
    }
    return probe, service, wait


def analyze(probe, service, wait=None):
    return analyze_samples([probe], [service], [wait] if wait else [], begin=1000., end=1100.)


def test_pairs_late_published_probe_by_forward_and_preserves_distinct_boundaries():
    probe, service, wait = evidence()
    result = analyze(probe, service, wait)
    row = result["samples"][0]
    assert row["metrics"] == {
        "gpu_run_batch_span_ms": 12.5,
        "launch_to_processed_completion_ms": 44.,
        "run_start_to_result_end_ms": 45.,
        "selection_end_to_run_start_ms": 3,
        "run_return_to_result_start_ms": 24,
        "gpu_layer_dependency_wait_ms": .09,
    }
    assert row["phases"]["run_batch"]["wall_minus_thread_cpu_ms"] == 6
    assert result["short_single_request"]["sample_count"] == 1
    assert result["unknown_counts"] == {}
    assert result["short_single_request"]["phases"]["launch_telemetry"]["thread_cpu_ms"]["mean_ms"] == 2


@pytest.mark.parametrize("field,value,reason", [
    ("forward_iter", None, "missing_clock_domain_or_forward_iter"),
    ("monotonic_clock_domain", "another-boot", "missing_service"),
    ("request_ids", ["another-request"], "request_identity_disagreement"),
    ("result_processed", False, "result_not_processed"),
    ("extend_tokens", 81, "native_geometry_disagreement"),
])
def test_identity_or_work_disagreement_never_falls_back_to_sample_ordinal(field, value, reason):
    probe, service, _ = evidence()
    probe[field] = value
    result = analyze(probe, service)
    assert not result["available"]
    assert result["unknown_counts"] == {reason: 1}
    assert result["all_paired"]["metrics"]["gpu_run_batch_span_ms"]["sum_ms"] is None


@pytest.mark.parametrize("duplicate", ["probe", "service"])
def test_duplicate_forward_identity_is_ambiguous_even_with_matching_requests(duplicate):
    probe, service, _ = evidence()
    result = analyze_samples(
        [probe, deepcopy(probe)] if duplicate == "probe" else [probe],
        [service, deepcopy(service)] if duplicate == "service" else [service],
        [], begin=1000., end=1100.,
    )
    assert result["samples"] == []
    reason = f"ambiguous_{duplicate}_identity" if duplicate == "probe" else "ambiguous_service"
    assert result["unknown_counts"][reason] == (2 if duplicate == "probe" else 1)


def test_shutdown_sample_can_retain_processed_cpu_but_not_unobserved_gpu_or_missing_phases():
    probe, service, _ = evidence()
    probe.update(event="prefill_turnover_incomplete", gpu_span_status="pending",
                 gpu_run_batch_span_ms=None)
    del probe["phases"]["post_launch_physical"]
    result = analyze(probe, service)
    summary = result["short_single_request"]
    assert summary["phases"]["run_batch"]["wall_ms"]["mean_ms"] == 14.
    assert summary["phases"]["post_launch_physical"]["wall_ms"]["mean_ms"] is None
    assert summary["metrics"]["gpu_run_batch_span_ms"]["mean_ms"] is None
    assert summary["metrics"]["gpu_layer_dependency_wait_ms"]["sum_ms"] is None
    assert summary["gpu_span_status"] == {"pending": 1}
    assert summary["restore_wait_status"] == {"not_observed": 1}


@pytest.mark.parametrize("change", ["identity", "outside", "duplicate"])
def test_restore_wait_requires_exact_identity_and_time_containment(change):
    probe, service, wait = evidence()
    if change == "identity":
        wait["request_ids"] = ["other"]
    elif change == "outside":
        wait["ts_ms"] = 999.
    result = (
        analyze_samples([probe], [service], [wait, deepcopy(wait)], begin=1000., end=1100.)
        if change == "duplicate" else analyze(probe, service, wait)
    )
    assert "gpu_layer_dependency_wait_ms" not in result["samples"][0]["metrics"]


def test_invalid_phase_and_gpu_duration_stay_unknown_while_valid_phases_survive():
    probe, service, _ = evidence()
    probe["phases"]["selection"]["wall_ms"] = 7
    probe["gpu_run_batch_span_ms"] = float("nan")
    result = analyze(probe, service)
    assert result["unknown_counts"] == {"invalid_phase/selection": 1}
    assert result["samples"][0]["gpu_span_status"] == "invalid"
    assert "selection" not in result["samples"][0]["phases"]
    assert result["short_single_request"]["phases"]["run_batch"]["wall_ms"]["mean_ms"] == 14.


def test_partial_batch_geometry_and_legacy_guesses_are_not_native_work_evidence():
    probe, service, _ = evidence()
    service["request_samples"][0]["prefill_geometry_source"] = "legacy_request"
    assert analyze(probe, service)["unknown_counts"] == {"missing_native_geometry": 1}
    service["batch_size"] = 2
    assert analyze(probe, service)["unknown_counts"] == {"request_identity_disagreement": 1}


def test_sealed_input_hash_excludes_appended_live_records_and_rejects_changed_prefix(tmp_path):
    probe, service, wait = evidence()
    raw = b"".join((json.dumps(row) + "\n").encode() for row in (service, probe, wait))
    path = tmp_path / "audit.jsonl"
    path.write_bytes(raw + b'{"event":"append-only-live-growth"}\n')
    metadata = {
        "source": str(path), "read_prefix_bytes": len(raw),
        "read_prefix_sha256": hashlib.sha256(raw).hexdigest(),
        "monitor_window": {"begin_ts_ms": 1000., "end_ts_ms": 1100.},
    }
    assert read_sealed(metadata)["all_paired"]["sample_count"] == 1
    path.write_bytes(raw.replace(b"service-a", b"service-b"))
    with pytest.raises(ValueError, match="hash changed"):
        read_sealed(metadata)


def test_window_end_is_exclusive_and_absent_old_native_fields_are_unknown():
    probe, service, _ = evidence()
    service["complete_ts_ms"] = 1100.
    assert analyze(probe, service)["samples"] == []
    del service["forward_iter"]
    service["complete_ts_ms"] = 1049.
    assert analyze(probe, service)["unknown_counts"] == {"missing_service": 1}
    assert distribution([]) == {
        "count": 0, "mean_ms": None, "p50_ms": None, "p90_ms": None,
        "max_ms": None, "sum_ms": None,
    }


def lifecycle_fixture(tmp_path):
    probe, service, wait = evidence()
    audit = tmp_path / "arm/server/runtime_audit.jsonl"
    audit.parent.mkdir(parents=True)
    raw = b"".join((json.dumps(row) + "\n").encode() for row in (probe, service, wait))
    audit.write_bytes(raw + b'{"event":"unsealed-live-suffix"}\n')
    lifecycle = {
        "arm": str(audit.parent.parent),
        "collection_code_commit": "frozen-collection",
        "input_jsonl_prefixes": {str(audit): {
            "complete_prefix_bytes": len(raw),
            "complete_prefix_sha256": hashlib.sha256(raw).hexdigest(),
        }},
    }
    path = tmp_path / "lifecycle.json.gz"
    path.write_bytes(gzip.compress(json.dumps(lifecycle).encode(), mtime=0))
    return path, audit, lifecycle


def test_lifecycle_analysis_reuses_the_original_complete_prefix_and_collection(tmp_path):
    path, audit, _ = lifecycle_fixture(tmp_path)
    result = read_lifecycle_prefix(path, begin=1000., end=1100.)
    assert result["analysis"]["short_single_request"]["sample_count"] == 1
    assert result["collection_code_commit"] == "frozen-collection"
    assert result["lifecycle_report_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["source_prefix"]["read_prefix_bytes"] < audit.stat().st_size
    assert result["source_prefix"]["monitor_window"] == {
        "begin_ts_ms": 1000., "end_ts_ms": 1100.,
    }


@pytest.mark.parametrize("mutation", ("missing_prefix", "wrong_hash", "wrong_size", "no_commit"))
def test_lifecycle_analysis_rejects_unbound_or_changed_evidence(tmp_path, mutation):
    path, audit, lifecycle = lifecycle_fixture(tmp_path)
    prefix = lifecycle["input_jsonl_prefixes"][str(audit)]
    if mutation == "missing_prefix":
        lifecycle["input_jsonl_prefixes"].clear()
    elif mutation == "wrong_hash":
        prefix["complete_prefix_sha256"] = "0" * 64
    elif mutation == "wrong_size":
        prefix["complete_prefix_bytes"] = True
    else:
        del lifecycle["collection_code_commit"]
    path.write_bytes(gzip.compress(json.dumps(lifecycle).encode(), mtime=0))
    with pytest.raises(ValueError):
        read_lifecycle_prefix(path, begin=1000., end=1100.)


@pytest.mark.parametrize("begin,end", (
    (None, 1100.), (1000., None), (1000., 1000.), (1100., 1000.),
    (-1., 1100.), (float("nan"), 1100.), (1000., float("inf")), (True, 1100.),
))
def test_lifecycle_analysis_rejects_an_invalid_diagnostic_window(tmp_path, begin, end):
    with pytest.raises(ValueError, match="finite, nonempty"):
        read_lifecycle_prefix(tmp_path / "unread.json.gz", begin=begin, end=end)
