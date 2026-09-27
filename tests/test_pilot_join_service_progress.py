from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.pilot_join_service_progress import (
    attach_progress, clock_bracket, evaluate_rows,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in records),
        encoding="utf-8",
    )


def test_clock_bounds_use_submit_and_result_pairs_not_submit_median(
    tmp_path: Path,
):
    workflows = tmp_path / "workflows"
    server = tmp_path / "server.jsonl"
    _write_jsonl(server, [
        {
            "kind": kind, "ts_ms": 1000. + index * 10. + (
                1020. if kind == "llm_submit" else 995.
            ),
            "attributes": {"request_id": f"rid-{index}"},
        }
        for index in range(100)
        for kind in ("llm_submit", "llm_result")
    ])
    _write_jsonl(
        workflows / "alpha__one" / "runtime_events.deepagents.jsonl",
        [
            {
                "kind": kind, "ts_ms": 1000. + index * 10.,
                "attributes": {"request_id": f"rid-{index}"},
            }
            for index in range(100)
            for kind in ("llm_submit", "llm_result")
        ],
    )
    lower, upper, audit = clock_bracket(workflows, server)
    assert (lower, upper) == (995., 1020.)
    assert audit["width_ms"] == 25.
    _write_jsonl(server, [
        {
            "kind": kind, "ts_ms": 1000. + index * 10. + (
                1020. if kind == "llm_submit" else 1030.
            ),
            "attributes": {"request_id": f"rid-{index}"},
        }
        for index in range(100)
        for kind in ("llm_submit", "llm_result")
    ])
    with pytest.raises(ValueError, match="wide or inverted"):
        clock_bracket(workflows, server)


def test_progress_uses_only_completed_decode_samples_before_guarded_trigger(
    tmp_path: Path,
):
    audit = tmp_path / "audit.jsonl"
    _write_jsonl(audit, [
        {
            "event": "gpu_service_sample",
            "phase": "decode",
            "ts_ms": ts,
            "request_samples": [{
                "request_id": rid,
                "output_tokens_before": tokens,
                "token_delta": 1,
                "token_delta_semantics": "observed_output_ids_delta",
            }],
        }
        for ts, rid, tokens in (
            (1000., "rid", 4),
            (1600., "rid", 14),
            (1910., "rid", 1000),
            (1700., "other", 900),
        )
    ])
    rows = [{
        "request_id": "rid", "trigger_ms": 1000.,
        "features": [1., 2., 3., 4., 5.],
        "lead_ms": 3000.,
    }]
    selected, coverage = attach_progress(rows, audit, lower_offset_ms=1000.)
    assert coverage["matched_service_events"] == 2
    assert coverage["supported"] == 1
    assert selected[0]["service_sample_count"] == 2
    assert selected[0]["service_age_ms"] == 300.
    assert selected[0]["service_features"][5] == pytest.approx(
        2.772588722239781,
    )
    assert selected[0]["service_features"][6] == pytest.approx(
        2.871679624884012,
    )
    missing, counts = attach_progress(rows, audit, lower_offset_ms=500.)
    assert missing == []
    assert counts["excluded"] == {"insufficient_prior_decode_samples": 1}


def test_progress_comparison_uses_same_heldout_workflows_for_both_heads():
    rows = [
        {
            "trace_path": (
                f"/workflows/{project}__{i}/runtime_events.deepagents.jsonl"
            ),
            "lead_ms": float(2000 + 30 * i),
            "features": [1., 2., 3., 4., 5.],
            "service_features": [1., 2., 3., 4., 5., 6., 7., 8.],
        }
        for project in ("alpha", "beta", "gamma")
        for i in range(12)
    ]
    report = evaluate_rows(rows)
    assert report["training_projects"] == ["alpha", "beta", "gamma"]
    for project in report["training_projects"]:
        fold = report["folds"][project]
        assert fold["workflows"] == 12
        assert fold["stream_only"]["natural_returns"] == 12
        assert fold["stream_plus_service"]["natural_returns"] == 12
        assert fold["paired_vs_stream_only"]["workflows"] == 12
    with pytest.raises(ValueError, match="three training projects"):
        evaluate_rows(rows[:20])
