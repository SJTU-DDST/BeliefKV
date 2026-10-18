import json

import pytest

from scripts.audit_tool_transfer_windows import (
    audit, history_forecast_audit, history_physical_opportunity, history_physical_summary,
)


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.mark.parametrize("clock_event", ("safe_point_census", "admission_runtime_state"))
def test_tool_transfer_audit_uses_tool_completion_not_first_gpu_service(tmp_path, clock_event):
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": clock_event, "ts_ms": 1000., "monotonic_ms": 0.},
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
        "ts_ms": ts + 1000.,
        "p10_ms": max(1000. - 100. - ts, 0.), "p50_ms": 1000. - ts,
        "p90_ms": 1000. + 100. - ts, "release_cdf": [[1000., .1], [10000., .9]],
    } for ts in (775., 900.)]
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "safe_point_census", "ts_ms": 1000., "monotonic_ms": 0.},
        *forecasts,
        {"event": "wait_prefetch_plan", "source": "tool_wait",
         "workflow_id": "wf", "invocation_id": "child", "context_id": "ctx",
         "context_epoch": 0, "ts_ms": 1950., "active_tool_ids": ["command"],
         "start_window_ms": 300., "planned_pool_bytes": {"kv": 200},
         "fits_current_free_lists": True},
    ])
    result = history_forecast_audit(tmp_path, artifact)
    if parallel_open_tool:
        assert result["matched_independent_tools"] == 0
        assert result["matched_forecasts"] == 0
        assert result["physical_opportunity_intersection"]["matched_positive_full_plans"]["snapshots"] == 0
    else:
        assert result["matched_independent_tools"] == 1
        assert result["matched_forecasts"] == 2
        assert result["history_tool_weighted_last_second_median_absolute_error_ms"] == 100.
        assert result["generic_tool_weighted_last_second_median_absolute_error_ms"] > 2000.
        assert result["history_first_windows_in_actual_0_to_500ms"] == 1
        assert result["generic_first_windows_in_actual_0_to_500ms"] == 0
        assert result["rows"][0]["history_support"] == "same_input_completed"
        physical = result["physical_opportunity_intersection"]
        assert physical["history_within_recorded_start_window"]["independent_tools"] == 1
        assert physical["generic_within_recorded_start_window"]["independent_tools"] == 0
        assert physical["history_within_recorded_start_window"]["with_observed_free_capacity"][
            "sum_per_tool_max_observed_full_bytes"
        ] == 200


def test_history_opportunity_requires_live_accepted_forecast_and_exact_active_tool():
    from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
    from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint

    key = PrefillCandidateKey("r", "wf", "child", "ctx", 0, 0)
    generic = NativeToolWaitHint(
        key, 1000., 2000., 3000., 500., 1500., "frozen",
    )
    history = NativeToolWaitHint(
        key, 100., 300., 500., 500., 1500., "frozen",
        residual_duration_mass=((100., .1), (300., .5), (500., .4)),
    )
    snapshot = {
        "ts_ms": 1750., "active_tool_ids": ["command"], "start_window_ms": 200.,
        "planned_pool_bytes": {"kv": 100}, "fits_current_free_lists": True,
    }
    candidate = {
        "window": {"tool_id": "command", "workflow_id": "wf", "end_ms": 1100.},
        "generic": generic, "history": history, "accepted_ts_ms": 1600.,
    }
    linked = history_physical_opportunity(snapshot, candidate, 1000.)
    assert linked["history_within_recorded_start_window"] is True
    assert linked["generic_within_recorded_start_window"] is False
    assert linked["actual_lead_to_tool_end_ms"] == 350.
    assert history_physical_opportunity(
        snapshot, {**candidate, "accepted_ts_ms": 1800.}, 1000.,
    ) is None
    assert history_physical_opportunity(
        {**snapshot, "ts_ms": 2600.}, candidate, 1000.,
    ) is None
    assert history_physical_opportunity(
        {**snapshot, "active_tool_ids": ["command", "parallel"]}, candidate, 1000.,
    ) is None
    after_end = history_physical_opportunity(
        snapshot, {**candidate, "window": {**candidate["window"], "end_ms": 700.}}, 1000.,
    )
    assert after_end["history_within_recorded_start_window"] is True
    assert after_end["actual_lead_to_tool_end_ms"] == -50.


def test_history_physical_summary_keeps_unknown_horizons_and_capacity_separate():
    row = {
        "workflow_id": "wf", "invocation_id": "child", "tool_id": "command",
        "planned_full_bytes": 100, "history_within_500ms": True,
        "generic_within_500ms": False, "history_within_recorded_start_window": True,
        "generic_within_recorded_start_window": False, "fits_current_free_lists": True,
        "actual_lead_to_tool_end_ms": 200.,
    }
    result = history_physical_summary([
        row, {**row, "planned_full_bytes": 150},
        {**row, "tool_id": "blocked", "fits_current_free_lists": False},
        {**row, "tool_id": "unknown", "history_within_recorded_start_window": None},
    ])
    selected = result["history_within_recorded_start_window"]
    assert selected["independent_tools"] == 2
    assert selected["sum_per_tool_max_observed_full_bytes"] == 250
    assert selected["with_observed_free_capacity"]["independent_tools"] == 1
    assert result["history_within_500ms"]["with_observed_free_capacity"]["independent_tools"] == 2
