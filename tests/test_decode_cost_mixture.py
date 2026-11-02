from collections import Counter
import hashlib
import json

import pytest

from scripts import audit_decode_cost_mixture as mixture
from scripts.audit_decode_cost_mixture import decode_strata, decompose


def cost(outputs, worker_ms, *, forwards=30):
    return Counter(output_tokens=outputs, worker_ms=worker_ms, forwards=forwards)


def test_equal_mixture_assigns_difference_to_within_stratum_cost():
    result = decompose(
        {(1, "le_32768"): cost(100, 200), (2, "le_32768"): cost(100, 100)},
        {(1, "le_32768"): cost(100, 150), (2, "le_32768"): cost(100, 80)},
    )
    assert result["observed_candidate_minus_reference_ms_per_token"] == pytest.approx(-.35)
    assert result["within_stratum_component_ms_per_token"] == pytest.approx(-.35)
    assert result["composition_component_ms_per_token"] == pytest.approx(0.)
    assert result["pooled_output_standardized_ms_per_token"] == {
        "reference": 1.5, "candidate": 1.15,
    }


def test_equal_stratum_costs_assign_difference_to_composition():
    result = decompose(
        {(1, "le_32768"): cost(150, 300), (2, "le_32768"): cost(50, 50)},
        {(1, "le_32768"): cost(50, 100), (2, "le_32768"): cost(150, 150)},
    )
    assert result["within_stratum_component_ms_per_token"] == 0.
    assert result["composition_component_ms_per_token"] == pytest.approx(-.5)
    assert result["pooled_output_standardized_ms_per_token"] == {
        "reference": 1.5, "candidate": 1.5,
    }


def test_absent_rare_and_zero_output_costs_are_not_dropped():
    result = decompose(
        {
            (1, "le_32768"): cost(100, 200),
            (2, "le_32768"): cost(0, 10),
            (3, "le_32768"): cost(1, 4, forwards=1),
        },
        {(1, "le_32768"): cost(100, 150), (4, "le_32768"): cost(10, 30)},
    )
    assert result["common_support_output_fraction"] == {
        "reference": pytest.approx(100 / 101), "candidate": pytest.approx(100 / 110),
    }
    assert len(result["strata"]) == 4
    assert sum(
        result[name] for name in (
            "within_stratum_component_ms_per_token",
            "composition_component_ms_per_token", "unsupported_strata_component_ms_per_token",
        )
    ) == pytest.approx(180 / 110 - 214 / 101)


def test_no_common_support_keeps_raw_difference_and_null_standardization():
    result = decompose(
        {(1, "le_32768"): cost(10, 20)},
        {(2, "le_32768"): cost(20, 30)},
    )
    assert result["unsupported_strata_component_ms_per_token"] == -.5
    assert result["pooled_output_standardized_ms_per_token"] is None


@pytest.mark.parametrize("reference,candidate", [({}, {}), ({}, {(1, "x"): cost(1, 1)})])
def test_empty_output_is_rejected(reference, candidate):
    with pytest.raises(ValueError, match="positive decode output"):
        decompose(reference, candidate)


def test_minimum_support_cannot_be_zero():
    with pytest.raises(ValueError, match="positive"):
        decompose({}, {}, minimum_forwards=0)


def batch(forward, end, *, width=2, contexts=(100, 200), deltas=(1, 1)):
    return {
        "event": "gpu_service_sample", "phase": "decode",
        "forward_iter": forward, "complete_monotonic_ms": end,
        "service_elapsed_ms": 5., "batch_size": width, "cuda_graph_replayed": True,
        "request_samples": [
            {"sequence_tokens_before": context, "output_token_delta": delta}
            for context, delta in zip(contexts, deltas)
        ],
    }


def test_window_uses_batch_completion_and_preserves_zero_output_cost():
    result = decode_strata([
        batch(1, 10.), batch(2, 15., deltas=(0, 0)), batch(3, 20.),
    ], (10., 20.), buckets=[1, 2, 3])
    assert result[(2, "le_8192")]["output_tokens"] == 2
    assert result[(2, "le_8192")]["worker_ms"] == 10.
    assert result[(2, "le_8192")]["zero_output_request_steps"] == 2


def test_context_and_width_are_jointly_stratified():
    result = decode_strata([
        batch(1, 10.), batch(2, 11., contexts=(100000, 100000)),
        batch(3, 12., width=1, contexts=(20000,), deltas=(1,)),
    ], (0., 20.), buckets=[1, 2, 3])
    assert set(result) == {(2, "le_8192"), (2, "le_131072"), (1, "le_32768")}


@pytest.mark.parametrize("change", ["duplicate", "width", "negative", "elapsed"])
def test_invalid_decode_geometry_is_rejected(change):
    first, second = batch(1, 10.), batch(2, 11.)
    if change == "duplicate":
        second["forward_iter"] = 1
    elif change == "width":
        second["batch_size"] = 3
    elif change == "negative":
        second["request_samples"][0]["output_token_delta"] = -1
    else:
        second["service_elapsed_ms"] = float("nan")
    with pytest.raises(ValueError, match="invalid or duplicate"):
        decode_strata([first, second], (0., 20.), buckets=[1, 2, 3])


def test_padding_geometry_uses_the_recorded_graph_buckets():
    result = decode_strata([
        batch(1, 10., width=3, contexts=(100, 200, 300), deltas=(1, 1, 1)),
    ], (0., 20.), buckets=[1, 2, 4])
    assert result[(3, "le_8192")]["graph_bucket_rows"] == 4
    assert result[(3, "le_8192")]["graph_real_rows"] == 3


@pytest.fixture
def completed_arm(tmp_path, monkeypatch):
    arm = tmp_path / "full"
    (arm / "replay").mkdir(parents=True)
    (arm / "server").mkdir()
    registration, cohort, report = (
        tmp_path / name for name in ("registration.json", "cohort.json", "cost.json")
    )
    registration.write_text(json.dumps({
        "run_root": str(tmp_path), "order": ["full"],
        "workflow_arrivals": {"arrival_schedule": [{"workflow_id": "m", "offset_seconds": 0.}]},
    }))
    cohort.write_text(json.dumps({"throughput_window_offset_seconds": [0., .02]}))
    events, audit = arm / "replay/runtime_events.replay.jsonl", arm / "server/runtime_audit.jsonl"
    events.write_text(json.dumps({"kind": "workflow_start", "workflow_id": "m", "ts_ms": 0.}) + "\n")
    audit.write_text(json.dumps(batch(1, 10.)) + "\n")
    payload = {
        "complete_measured_execution_verified": True,
        "inputs_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (registration, cohort)
        },
        "output_conservation": {"measured_requests": 1, "measured_output_tokens": 2},
        "common_window_offset_seconds": [0., .02],
        "input_jsonl_prefixes": {
            str(path): {
                "complete_prefix_bytes": path.stat().st_size,
                "complete_prefix_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "first_read_limit_bytes": path.stat().st_size,
            }
            for path in (events, audit)
        },
        "engine": {"cuda_graph_config": {"decode": {"bs": [1, 2, 3]}}},
        "phase_costs": {"window:decode": {
            "forwards": 1, "request_steps": 2, "output_tokens": 2,
            "context_tokens": 300, "worker_ms": 5.,
        }},
    }
    report.write_text(json.dumps(payload))
    monkeypatch.setattr(mixture, "verify_execution", lambda *_args: {
        "measured_requests": 1, "measured_output_tokens": 2,
    })
    return registration, cohort, report, payload


def test_complete_arm_reproduces_the_bound_cost_report(completed_arm):
    registration, cohort, report, _payload = completed_arm
    _plan, _execution, groups, evidence = mixture.inspect_arm(registration, cohort, report)
    assert groups[(2, "le_8192")]["output_tokens"] == 2
    assert evidence["method"] == "full"


@pytest.mark.parametrize("mismatch", [
    "unfinished", "different_cohort", "different_work", "window",
    "journal_hash", "journal_length", "counts", "worker_time",
])
def test_incomplete_changed_or_mismatched_evidence_is_rejected(completed_arm, mismatch):
    registration, cohort, report, payload = completed_arm
    if mismatch == "unfinished":
        payload["complete_measured_execution_verified"] = False
    elif mismatch == "different_cohort":
        payload["inputs_sha256"][str(cohort)] = "wrong"
    elif mismatch == "different_work":
        payload["output_conservation"]["measured_output_tokens"] = 3
    elif mismatch == "window":
        payload["common_window_offset_seconds"] = [0., .03]
    elif mismatch in ("journal_hash", "journal_length"):
        prefix = next(iter(payload["input_jsonl_prefixes"].values()))
        if mismatch == "journal_hash":
            prefix["complete_prefix_sha256"] = "wrong"
        else:
            prefix["complete_prefix_bytes"] += 1
    elif mismatch == "counts":
        payload["phase_costs"]["window:decode"]["output_tokens"] = 3
    else:
        payload["phase_costs"]["window:decode"]["worker_ms"] = 6.
    report.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        mixture.inspect_arm(registration, cohort, report)
