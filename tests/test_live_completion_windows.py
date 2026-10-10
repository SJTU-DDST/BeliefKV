import json
import math

from scripts.evaluate_live_completion_windows import (
    collect, countdown_work, forecast_features, roles, timing_summary,
)


def test_completion_window_labels_use_only_known_final_requests_before_eos(tmp_path):
    workflow = tmp_path / "client_156/workflows/pydata__xarray-1"
    server = tmp_path / "server"
    opportunities = tmp_path / "opportunities"
    for directory in (workflow, server, opportunities):
        directory.mkdir(parents=True)
    events = [
        {"kind": "llm_submit", "ts_ms": 100, "invocation_id": "child",
         "attributes": {"request_id": "final"}},
        {"kind": "llm_result", "ts_ms": 400, "invocation_id": "child",
         "attributes": {"request_id": "final"}},
        {"kind": "return", "ts_ms": 450, "invocation_id": "child",
         "attributes": {"source": "deepagents_task", "outcome": "completed"}},
        {"kind": "llm_submit", "ts_ms": 200, "invocation_id": "unresolved-child",
         "attributes": {"request_id": "unresolved"}},
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events)
    )
    native = [
        {"kind": "llm_result", "ts_ms": 1400, "attributes": {
            "request_id": rid, "output_tokens": 108,
        }} for rid in ("final", "unresolved")
    ]
    (server / "runtime_events.sglang.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in native)
    )
    forecast = {
        "event": "semantic_child_forecast", "ts_ms": 1300, "request_id": "final",
        "score": .9, "notice_active": True, "current_output_tokens": 100,
        "observed_output_tokens": 96, "observation_age_ms": 100, "last_service_age_ms": 20,
        "remaining_tokens": 12, "upper_tokens": 24, "lower_tokens": 3,
        "content_chars": 300, "estimated_report_tokens": 200,
        "prior_tool_calls": 3, "prior_model_rounds": 2,
    }
    samples = [
        {"event": "safe_point_census", "ts_ms": 1000, "monotonic_ms": 0},
        forecast, {**forecast, "ts_ms": 1500},
        {**forecast, "request_id": "unresolved"},
    ]
    (opportunities / "admission_opportunities.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in samples)
    )
    rows, coverage = collect(tmp_path, .6)
    assert len(rows) == 1
    assert rows[0]["remaining_tokens"] == 8
    assert rows[0]["remaining_client_wall_ms"] == 150
    assert rows[0]["lead_to_native_eos_ms"] == 100
    assert rows[0]["features"] == forecast_features(forecast)
    assert coverage["excluded"] == {"at_or_after_native_eos": 1, "unresolved_label": 1}
    assert countdown_work(forecast) == 8
    assert countdown_work({**forecast, "remaining_tokens": 2}) == 20
    assert math.isinf(countdown_work({**forecast, "remaining_tokens": 2, "upper_tokens": 3}))
    summary = timing_summary(rows * 2, [.8, .9], .7)
    assert summary["natural_trigger_count"] == 1
    assert summary["return_lead_0_to_500ms_count"] == 1


def test_completion_window_roles_keep_workflows_together_and_project_unseen():
    rows = [{"task": "pydata__xarray-1"}] * 3 + [
        {"task": f"django__django-{index}"} for index in range(20)
    ] * 2
    split = roles(rows, "pydata")
    assert split["evaluation"] == [0, 1, 2]
    identities = {
        role: {rows[index]["task"] for index in indices}
        for role, indices in split.items()
    }
    assert not identities["training"] & identities["selector"]
    assert not identities["training"] & identities["evaluation"]
    assert not identities["selector"] & identities["evaluation"]
