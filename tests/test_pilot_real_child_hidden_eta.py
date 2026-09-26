import json

import numpy as np

from scripts.pilot_real_child_hidden_eta import (
    classification_report,
    content_gated_report,
    load_records,
)


def test_real_child_features_never_use_future_round_as_first_snapshot(tmp_path):
    workflow = tmp_path / "workflows" / "astropy__task"
    workflow.mkdir(parents=True)
    events = [
        {"kind": "spawn", "target_invocation_id": "deepagents-invocation:child"},
        {"kind": "llm_result", "invocation_id": "deepagents-invocation:child",
         "ts_ms": 1100., "attributes": {
             "request_id": "tool-round", "finish_reason": "tool_calls",
             "tool_call_count": 1, "output_chars": 0,
         }},
        {"kind": "llm_result", "invocation_id": "deepagents-invocation:child",
         "ts_ms": 2000., "attributes": {
             "request_id": "final-round", "finish_reason": "stop",
             "tool_call_count": 0, "output_chars": 100,
         }},
        {"kind": "return", "invocation_id": "deepagents-invocation:child",
         "ts_ms": 2100.},
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in events)
    )
    traces = tmp_path / "traces"
    traces.mkdir()
    for rid, arrival in (("tool-round", 1000), ("final-round", 1800)):
        np.savez(
            traces / f"{rid}.npz",
            rid=np.asarray(rid),
            token_counts=np.asarray([32]),
            arrival_ns=np.asarray([arrival * 1_000_000]),
            hidden=np.zeros((1, 2048), dtype=np.float16),
            finish_reason=np.asarray("stop"),
        )
    records, counts = load_records(tmp_path / "workflows", traces)
    assert counts["terminal_rounds_with_hidden"] == 1
    assert counts["other_rounds_with_hidden"] == 1
    by_terminal = {record["terminal"]: record for record in records}
    assert by_terminal[True]["samples"][0][:3] == (1, 0., 300.)
    assert by_terminal[False]["samples"][0][2] is None


def test_classification_report_counts_false_early_terminal():
    report = classification_report(np.asarray([0., 1.]), np.asarray([0.8, 0.9]))
    assert report["true_positive"] == 1
    assert report["false_positive"] == 1


def test_frozen_content_gate_never_uses_later_tool_cue_to_accept():
    records = [
        {"rid": "final", "terminal": True, "return_ms": 2100.,
         "first_arrival_ms": 1000., "samples": [(16, 400., 700., None)]},
        {"rid": "tool-before", "terminal": False, "return_ms": None,
         "first_arrival_ms": 1000., "samples": [(16, 400., None, None)]},
        {"rid": "tool-after", "terminal": False, "return_ms": None,
         "first_arrival_ms": 1000., "samples": [(16, 400., None, None)]},
    ]
    report = content_gated_report(
        records, np.array([True, True, True]),
        {
            "final": {"content": 1500.},
            "tool-before": {"content": 1200., "tool": 1300.},
            "tool-after": {"content": 1200., "tool": 1600.},
        },
        all_terminal_count=1,
    )
    assert report["true_positive"] == 1
    assert report["false_positive"] == 1
    assert report["median_return_lead_ms"] == 600
