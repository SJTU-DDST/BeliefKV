import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.evaluate_child_final_chunk_forecast import collect, evaluate


def test_direct_cli_entrypoint_loads_audit_module():
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/evaluate_child_final_chunk_forecast.py"
    )
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True, text=True, check=True,
    )
    assert "--heldout-workflows" in result.stdout


def _write_workflow(root, task_id, *, lead_ms, next_round=False, returned=True):
    path = root / task_id
    path.mkdir(parents=True)
    invocation_id = f"deepagents-invocation:{task_id}"
    events = [
        {"kind": "spawn", "target_invocation_id": invocation_id},
        {"kind": "join_create", "join_id": task_id,
         "member_invocation_ids": [invocation_id]},
        {"kind": "structured_action", "invocation_id": invocation_id,
         "ts_ms": 1000., "attributes": {
             "beliefkv_child_final_chunk_shadow": True, "request_id": "first",
         }},
        {"kind": "llm_result", "invocation_id": invocation_id,
         "ts_ms": 1100., "attributes": {
             "request_id": "first", "finish_reason": "stop",
             "stream_content_counted_chars": 40,
             "stream_final_chunk_ts_ms": 1000.,
             "output_chars": 40, "tool_call_count": 0,
         }},
    ]
    if next_round:
        events.extend([
            {"kind": "llm_result", "invocation_id": invocation_id,
             "ts_ms": 1200., "attributes": {
                 "request_id": "last", "finish_reason": "stop",
                 "stream_content_counted_chars": 0,
                 "output_chars": 40, "tool_call_count": 0,
             }},
        ])
    if returned:
        events.extend([
            {"kind": "return", "invocation_id": invocation_id,
             "ts_ms": 1000. + lead_ms},
            {"kind": "join_satisfied", "join_id": task_id,
             "ts_ms": 1000. + lead_ms},
        ])
    (path / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in events)
    )


def test_heldout_prior_ignores_repeated_task_and_censors_last_round(tmp_path):
    train_a = tmp_path / "train_a"
    train_b = tmp_path / "train_b"
    test = tmp_path / "heldout"
    _write_workflow(train_a, "django__one", lead_ms=800)
    _write_workflow(train_b, "django__one", lead_ms=800)
    _write_workflow(train_a, "django__two", lead_ms=1200)
    _write_workflow(test, "sympy__one", lead_ms=800)
    _write_workflow(test, "sympy__two", lead_ms=900, next_round=True)
    _write_workflow(test, "sympy__three", lead_ms=800, returned=False)
    result = evaluate(
        collect([train_a, train_b], require_signal=False),
        collect([test], require_signal=True),
    )
    assert result["train_positive_count"] == 3
    assert result["train_unique_positive_tasks"] == 2
    assert result["frozen_train_median_lead_ms"] == 1000
    assert result["heldout_true_candidates"] == 1
    assert result["heldout_false_candidates"] == 1
    assert result["heldout_censored_candidates"] == 1
    assert result["heldout_join_last_true_candidates"] == 1
    assert result["heldout_eta_error_p50_ms"] == 200
    assert result["heldout_control_saved_p50_ms"] == 100


def test_project_overlap_rejected_before_reporting(tmp_path):
    train = tmp_path / "train"
    heldout = tmp_path / "heldout"
    _write_workflow(train, "sympy__one", lead_ms=700)
    _write_workflow(heldout, "sympy__two", lead_ms=800)
    with pytest.raises(ValueError, match="projects overlap"):
        evaluate(
            collect([train], require_signal=False),
            collect([heldout], require_signal=True),
        )
    with pytest.raises(FileNotFoundError, match="workflow events"):
        collect([tmp_path / "nonexistent"], require_signal=True)
