import json

import pytest

from scripts.audit_request_agent_eligibility import audit, first_launch, snapshot_rows
from scripts.audit_join_reentry_service import identity


def receipt():
    sample = {
        "workflow_id": "workflow", "invocation_id": "agent", "context_id": "context",
        "context_epoch": 4, "session_id": "session", "session_generation": 2,
        "request_id": "request",
    }
    return sample, {
        "request_id": "request",
        "native_receipt": {"identity": list(identity(sample)), "clock_domain": "native"},
    }


def service(index, sample, *, start):
    return {
        "event": "gpu_service_sample", "sample_id": f"native-service-{index:09d}",
        "launch_monotonic_ms": start, "complete_monotonic_ms": start + 20.,
        "monotonic_clock_domain": "native", "request_samples": [sample],
    }


def test_first_launch_does_not_use_later_decode_or_worker_completion():
    sample, request = receipt()
    first = service(1, sample, start=100.)
    selected, count = first_launch([first, service(2, sample, start=120.)], request)
    assert selected is first and count == 2


@pytest.mark.parametrize("change", ("identity", "clock", "missing_sample", "duplicate"))
def test_incomplete_or_wrong_native_identity_is_not_a_request_launch(change):
    sample, request = receipt()
    row = service(1, sample, start=100.)
    if change == "identity":
        sample["context_epoch"] += 1
    elif change == "clock":
        row["monotonic_clock_domain"] = "other"
    elif change == "missing_sample":
        row["sample_id"] = "native-service-000000002"
    else:
        row["request_samples"].append(dict(sample))
    with pytest.raises(ValueError):
        first_launch([row], request)


def test_snapshot_reports_use_nested_control_budget_and_preserve_observed_transition_bracket():
    def row(when, active, paused):
        return {
            "ts_ms": when, "counts": {"native_NO_TOKEN": 4},
            "agent_admission": {
                "active_invocation_ids": active,
                "paused_pending_invocation_ids": paused,
                "marked_for_pause_invocation_ids": [],
                "logical_active_tokens": 691642,
                "control_capacity_snapshot": {"remaining_capacity_tokens": 113556},
            },
        }
    observed = snapshot_rows(
        [row(0., ["agent"], []), row(1000., ["other"], []),
         row(2000., ["other"], ["agent"]), row(3000., ["other", "agent"], [])],
        "agent", begin=1500., end=2500., lookback_ms=2000.,
    )
    assert [item["active"] for item in observed] == [True, False, False, True]
    assert observed[1]["active_removed_since_previous_sample"] == ["agent"]
    assert observed[1]["active_added_since_previous_sample"] == ["other"]
    assert observed[1]["from_native_queue_ms"] == -500.
    assert observed[1]["control_capacity_snapshot"]["remaining_capacity_tokens"] == 113556
    assert observed[2]["paused_pending"] is True


def test_missing_snapshot_identity_fields_remain_unknown():
    observed = snapshot_rows(
        [{"ts_ms": 10., "agent_admission": {}}],
        "agent", begin=0., end=20., lookback_ms=0.,
    )
    assert observed[0]["active"] is None
    assert observed[0]["paused_pending"] is None
    assert observed[0]["marked_for_pause"] is None


@pytest.mark.parametrize("complete", (True, False))
def test_request_audit_binds_completed_lifecycle_and_all_five_sealed_inputs(tmp_path, complete):
    sample, request = receipt()
    request.update(
        exact_tokens_verified=True, submitted_monotonic_ms=90.,
        native_complete_monotonic_ms=200., prompt_tokens=100, output_tokens=10,
    )
    request["native_receipt"]["complete_monotonic_ms"] = 200.
    submit = {
        **sample, "kind": "llm_submit", "ts_ms": 1100.,
        "attributes": {
            "request_id": "request",
            "native_ingress_timing_ms": {"native_queue_registered_ts_ms": 1100.},
            "cached_tokens_host": 90, "cached_tokens_device": 0,
            "uncached_prompt_tokens": 10,
        },
    }
    result = {
        **sample, "kind": "llm_result", "ts_ms": 1200.,
        "attributes": {"request_id": "request"},
    }
    inputs = {
        "replay/native_requests.jsonl": [request],
        "server/runtime_events.sglang.jsonl": [submit, result] if complete else [submit],
        "server/runtime_audit.jsonl": [service(1, sample, start=110.)],
        "opportunities/admission_opportunities.jsonl": [{
            "event": "admission_runtime_state", "ts_ms": 1100.,
            "physical_disabled": False,
            "agent_admission": {"active_invocation_ids": []},
        }],
        "replay/runtime_events.replay.jsonl": [{
            "kind": "workflow_start", "ts_ms": 10., "workflow_id": "workflow",
        }],
    }
    for name, rows in inputs.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    if not complete:
        with pytest.raises(ValueError, match="native result"):
            audit(tmp_path, "request")
        return
    observed = audit(tmp_path, "request")
    assert observed["native_queue_to_first_launch_ms"] == 10.
    assert observed["submit_to_first_launch_ms"] == 20.
    assert observed["first_launch_to_native_complete_ms"] == 90.
    assert observed["sample_hold_partition"] == {
        "active_ms": 0., "inactive_ms": 10., "unknown_ms": 0.,
    }
    assert len(observed["input_jsonl_prefixes"]) == 5
    assert all(
        observed["input_jsonl_prefixes"][str(tmp_path / name)]["complete_prefix_bytes"]
        == (tmp_path / name).stat().st_size
        for name in inputs
    )
    assert observed["complete_run_verified"] is False
    assert observed["performance_accepted"] is False
