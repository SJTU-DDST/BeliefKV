"""Recorded EOS facts do not need invented transfer-duration estimates."""

import json
from unittest.mock import patch

import pytest

from scripts.audit_native_transfer_policy import audit


@pytest.mark.parametrize("trigger,h2d_ms", (
    ("observed_no_tool_eos", None), ("observed_no_tool_eos", 20.),
    ("estimated_work", None), ("estimated_work", 20.),
))
def test_eos_without_duration_estimate_keeps_labels_but_no_synthetic_window(tmp_path, trigger, h2d_ms):
    (tmp_path / "join_candidate_windows.json").write_text(json.dumps({
        "rows": [{"final_request_id": "child", "native_eos_ts_ms": 900.,
                  "child_return_ts_ms": 1100.}],
    }))
    record = {
        "event": "final_stage_latest_start", "child_request_id": "child",
        "join_id": "join", "ts_ms": 1000., "trigger_kind": trigger,
        "h2d_ms": h2d_ms, "enqueue_to_submit_p90_ms": None,
        "service_sample_count": 3 if h2d_ms is not None else None,
        "service_support": "measured" if h2d_ms is not None else "not_required_observed_eos",
        "prefetch_lead_ms": 2000., "remaining_ms": 0., "generated_tokens": 100,
    }
    with patch(
        "scripts.audit_native_transfer_policy.records",
        side_effect=lambda path: iter((record,)) if path.name == "admission_opportunities.jsonl" else iter(()),
    ):
        result = audit(tmp_path)
    assert result["join_trigger_count"] == 1
    assert result["work_trigger_count"] == int(trigger == "estimated_work")
    [row] = result["join_trigger_rows"]
    assert row["lead_to_eos_ms"] == -100.
    assert row["lead_to_child_return_ms"] == 100.
    assert row["recorded_h2d_p90_ms"] == h2d_ms
    assert (row["adaptive_start_window_ms"] is None) is (h2d_ms is None)
    if h2d_ms is None:
        assert row["adaptive_policy_would_wait_at_this_snapshot"] is None
