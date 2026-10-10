import json

import pytest

from scripts.audit_tool_transfer_windows import audit, history_forecast_audit


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_tool_transfer_audit_uses_tool_completion_not_first_gpu_service(tmp_path):
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "safe_point_census", "ts_ms": 1000., "monotonic_ms": 0.},
        {"event": "prefetch_native_issued", "source": "tool_wait",
         "command_id": "c1", "context_id": "ctx", "invocation_id": "child",
         "active_tool_ids": ["a", "b"]},
    ])
    write_rows(tmp_path / "client_64/workflows/task/runtime_events.deepagents.jsonl", [
        {"kind": "tool_end", "ts_ms": 300., "invocation_id": "child",
         "attributes": {"tool_run_id": "a"}},
        {"kind": "tool_end", "ts_ms": 500., "invocation_id": "child",
         "attributes": {"tool_run_id": "b"}},
    ])
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [
        {"submit_ts_ms": 1200., "tagged_child_commits": [{"command_id": "c1", "num_bytes": 100}]},
    ])
    write_rows(tmp_path / "server/physical_action_use.jsonl", [
        {"event": "beliefkv_prefetch_first_service", "command_id": "c1",
         "full_node_reused": True, "ts_ms": 2500.},
    ])
    result = audit(tmp_path)
    assert result["started_100_to_1000ms_before_tool_end_count"] == 1
    assert result["median_submit_lead_to_tool_end_ms"] == 300.
    assert result["rows"][0]["full_first_service_reused"] is True


@pytest.mark.parametrize("parallel_open_tool", (False, True))
def test_history_routing_audit_weights_each_tool_once_and_excludes_open_parallel_calls(
    tmp_path, parallel_open_tool,
):
    artifact = tmp_path / "predictor.json"
    artifact.write_text(json.dumps({"repeat_error_p90_ms": 100.}))
    events = [
        {
            "kind": "tool_start", "workflow_id": "wf", "invocation_id": "child",
            "ts_ms": 0., "attributes": {
                "tool_call_id": "command", "tool_name": "execute", "is_child": True,
                "previous_same_input_status": "success",
                "previous_same_input_duration_ms": 1000.,
            },
        },
        {
            "kind": "tool_end", "workflow_id": "wf", "invocation_id": "child",
            "ts_ms": 1100., "attributes": {"tool_call_id": "command"},
        },
    ]
    if parallel_open_tool:
        events.insert(1, {
            "kind": "tool_start", "workflow_id": "wf", "invocation_id": "child",
            "ts_ms": 750., "attributes": {"tool_call_id": "unclosed"},
        })
    write_rows(tmp_path / "client_64/workflows/task/runtime_events.deepagents.jsonl", events)
    forecasts = [{
        "event": "tool_wait_forecast", "workflow_id": "wf", "invocation_id": "child",
        "context_id": "ctx", "context_epoch": 0, "observed_monotonic_ms": ts,
        "p10_ms": max(1000. - 100. - ts, 0.), "p50_ms": 1000. - ts,
        "p90_ms": 1000. + 100. - ts, "release_cdf": [[1000., .1], [10000., .9]],
    } for ts in (775., 900.)]
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", forecasts)
    result = history_forecast_audit(tmp_path, artifact)
    if parallel_open_tool:
        assert result["matched_independent_tools"] == 0
        assert result["matched_forecasts"] == 0
    else:
        assert result["matched_independent_tools"] == 1
        assert result["matched_forecasts"] == 2
        assert result["history_tool_weighted_last_second_median_absolute_error_ms"] == 100.
        assert result["generic_tool_weighted_last_second_median_absolute_error_ms"] > 2000.
        assert result["history_first_windows_in_actual_0_to_500ms"] == 1
        assert result["generic_first_windows_in_actual_0_to_500ms"] == 0
        assert result["rows"][0]["history_support"] == "same_input_completed"
