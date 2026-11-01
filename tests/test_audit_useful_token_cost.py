from copy import deepcopy
import json

import pytest

from scripts import audit_useful_token_cost as cost
from scripts.audit_useful_token_cost import analyze


def sample(forward, phase, start, end, *, before=0, after=0, rid="r", workflow="m"):
    member = {
        "request_id": rid, "workflow_id": workflow, "phase": phase,
        "sequence_tokens_before": 0 if phase == "prefill" else 100,
        "output_tokens_reported_before": before, "output_tokens_after": after,
        "output_token_delta": after - before,
        "token_delta": 100 if phase == "prefill" else after - before,
    }
    return {
        "event": "gpu_service_sample", "forward_iter": forward, "phase": phase,
        "service_start_monotonic_ms": start, "complete_monotonic_ms": end,
        "service_elapsed_ms": end - start, "batch_size": 1,
        "request_samples": [member], "cuda_graph_replayed": True,
        "monotonic_clock_domain": "clock",
        "output_token_accounting": "observed_output_ids_delta_all_forward_modes_v1",
    }


def audit(rows, receipts=None, measured=None):
    return analyze(
        rows, buckets=[1, 2, 4], receipts=receipts or {
            "r": {"output_tokens": 2, "native_receipt": {"identity": ["m"]}},
        }, measured=measured or {"m"}, window=(5., 25.),
    )


def test_first_prefill_output_and_zero_terminal_are_counted_once():
    rows = [
        sample(1, "prefill", 0, 10, after=1),
        sample(2, "decode", 12, 20, before=1, after=2),
        sample(3, "decode", 20, 28, before=2, after=2),
    ]
    result = audit(rows)
    assert result["scopes"]["whole_observed_execution"]["output_tokens"] == 2
    assert result["scopes"]["common_window"]["worker_ms_per_output_token"] == 9
    assert result["phase_costs"]["whole:decode"]["zero_output_request_steps"] == 1
    assert result["common_window_worker_coverage_ms"] == 18
    assert result["common_window_outside_worker_intervals_ms"] == 2
    assert result["output_conservation"]["measured_requests_complete"]


def test_nonmeasured_unfinished_output_is_retained():
    result = audit([
        sample(1, "prefill", 0, 10, after=1),
        sample(2, "decode", 10, 15, before=1, after=2),
        sample(3, "prefill", 16, 20, after=1, rid="b", workflow="background"),
    ])
    assert result["scopes"]["common_window"]["output_tokens"] == 3
    assert result["output_conservation"]["incomplete_background_requests_with_observed_output"] == ["b"]


def test_unreceipted_measured_output_is_rejected():
    with pytest.raises(ValueError, match="measured request"):
        audit([
            sample(1, "prefill", 0, 10, after=2),
            sample(2, "prefill", 10, 12, after=1, rid="other"),
        ])


@pytest.mark.parametrize("change", ["duplicate", "overlap", "ordinal", "receipt"])
def test_corrupted_accounting_is_rejected(change):
    rows = [
        sample(1, "prefill", 0, 10, after=1),
        sample(2, "decode", 12, 20, before=1, after=2),
    ]
    rows = deepcopy(rows)
    if change == "duplicate":
        rows[1]["forward_iter"] = 1
    elif change == "overlap":
        rows[1]["service_start_monotonic_ms"] = 9
    elif change == "ordinal":
        rows[1]["request_samples"][0]["output_tokens_reported_before"] = 0
    else:
        rows[1]["request_samples"][0].update(output_tokens_after=3, output_token_delta=2)
    with pytest.raises(ValueError):
        audit(rows)


@pytest.mark.parametrize("method", ["full", "thunderagent_acting_decay"])
def test_completed_arm_uses_its_own_worker_and_receipt_evidence(tmp_path, monkeypatch, method):
    arm = tmp_path / method
    (arm / "server").mkdir(parents=True)
    (arm / "replay").mkdir()

    def save(path, value):
        path.write_text(json.dumps(value))

    def save_rows(path, rows):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    registration, cohort = tmp_path / "registration.json", tmp_path / "cohort.json"
    save(registration, {
        "run_root": str(tmp_path), "order": [method],
        "workflow_arrivals": {"arrival_schedule": [{"workflow_id": "m", "offset_seconds": 0.}]},
    })
    save(cohort, {
        "measured_workflow_ids": ["m"], "throughput_window_offset_seconds": [0., .025],
    })
    save(arm / "server/server_info.json", {
        "enable_mixed_chunk": False, "cuda_graph_config": {"decode": {"bs": [1]}},
    })
    save(arm / "server/native_telemetry_status.json", {
        "writer_queue_depth": 0,
        "background_writer_timing": {"categories": {"audit:gpu_service_sample": {"records_seen": 2}}},
    })
    save_rows(arm / "replay/runtime_events.replay.jsonl", [
        {"kind": "workflow_start", "workflow_id": "m", "ts_ms": 0.},
    ])
    save_rows(arm / "replay/native_requests.jsonl", [
        {"request_id": "r", "output_tokens": 2, "native_receipt": {"identity": ["m"]}},
    ])
    save_rows(arm / "server/runtime_audit.jsonl", [
        sample(1, "prefill", 0., 10., after=1),
        sample(2, "decode", 12., 20., before=1, after=2),
    ])
    verified = []

    def verify(registration_path, cohort_path):
        verified.append((registration_path, cohort_path))
        return {"protocol": "all_fixed_measured_workflows_completed"}

    monkeypatch.setattr(cost, "verify_execution", verify)
    result = cost.inspect(registration, cohort)
    assert verified == [(registration, cohort)]
    assert result["method"] == method
    assert result["complete_measured_execution_verified"]
    assert result["scopes"]["common_window"]["output_tokens"] == 2
    assert all(str(arm) in path for path in result["input_jsonl_prefixes"])


def test_cost_report_rejects_unfinished_execution_before_reading_worker_data(tmp_path, monkeypatch):
    def incomplete(*args):
        raise ValueError("no fixed measured completion certificate")

    monkeypatch.setattr(cost, "verify_execution", incomplete)
    with pytest.raises(ValueError, match="completion certificate"):
        cost.inspect(tmp_path / "registration.json", tmp_path / "cohort.json")
