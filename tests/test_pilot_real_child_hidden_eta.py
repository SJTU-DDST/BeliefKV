import json

import numpy as np
import pytest

from scripts.pilot_real_child_hidden_eta import (
    classification_report,
    content_gated_report,
    first_eta_trigger_report,
    load_records,
    load_batch_records,
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
    assert by_terminal[True]["join_last"] is False
    with pytest.raises(ValueError, match="duplicate request IDs"):
        load_batch_records([tmp_path / "workflows"] * 2, traces)
    with pytest.raises(ValueError, match="one hidden-state trace root"):
        load_batch_records([tmp_path / "workflows"] * 2, [traces])
    with pytest.raises(ValueError, match="duplicate request IDs"):
        load_batch_records([tmp_path / "workflows"] * 2, [traces, traces])
    with pytest.raises(FileNotFoundError, match="workflow events"):
        load_batch_records([tmp_path / "missing-workflows"], traces)
    with pytest.raises(FileNotFoundError, match="trace directory"):
        load_batch_records([tmp_path / "workflows"], tmp_path / "missing-traces")


def test_classification_report_counts_false_early_terminal():
    report = classification_report(np.asarray([0., 1.]), np.asarray([0.8, 0.9]))
    assert report["true_positive"] == 1
    assert report["false_positive"] == 1


def test_frozen_content_gate_never_uses_later_tool_cue_to_accept():
    records = [
        {"rid": "final", "terminal": True, "return_ms": 2100.,
         "join_last": True, "first_arrival_ms": 1000.,
         "samples": [(16, 400., 700., None)]},
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
    assert report["actionable_precision"] == 0.5
    assert report["eligible_join_last_terminal_rounds"] == 1
    assert report["join_last_true_positive"] == 1
    assert report["join_last_at_least_500ms_early"] == 1


def test_first_eta_trigger_counts_nonterminal_and_gates_late_tool(monkeypatch):
    monkeypatch.setattr(
        "scripts.pilot_real_child_hidden_eta.predict",
        lambda model, samples: np.asarray([900.0] * len(samples)),
    )
    def record(rid, terminal, return_ms=None, join_last=False):
        return {
            "rid": rid, "terminal": terminal, "return_ms": return_ms,
            "join_last": join_last, "first_arrival_ms": 1000.,
            "samples": [(16, 300., 0. if terminal else None, None)],
        }
    records = [
        record("join", True, 2300., True),
        record("nonterminal", False),
        record("tool-before", False),
        record("tool-after", False),
    ]
    report = first_eta_trigger_report(
        (), records, np.ones(len(records), dtype=bool),
        {
            "join": {"content": 1400.},
            "nonterminal": {"content": 1200.},
            "tool-before": {"content": 1400., "tool": 1350.},
            "tool-after": {"content": 1400., "tool": 1450.},
        },
        hidden=False,
    )
    assert report["first_triggered_rounds"] == 3
    assert report["nonterminal_false_triggers"] == 2
    assert report["join_last_trigger_500_to_3000ms"] == 1
    assert report["first_trigger_actionable_precision"] == 0.3333
