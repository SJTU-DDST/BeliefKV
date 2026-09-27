from __future__ import annotations

from pathlib import Path

import pytest

from scripts.evaluate_join_stream_eta import align, evaluate


def _join(**overrides):
    return {
        "task_id": "alpha__task", "invocation_id": "child",
        "request_id": "rid", "join_id": "join-1", "signal_ts_ms": 1000.,
        "join_lead_ms": 5260., "group_label": "natural", "label": "true",
        **overrides,
    }


def _stream(**overrides):
    return {
        "trace_path": "/workflows/alpha__task/runtime_events.deepagents.jsonl",
        "child": "child", "request_id": "rid", "join_id": "join-1",
        "trigger_ms": 1250., "lead_ms": 5000., "final": True,
        "features": [1.] * 5,
        **overrides,
    }


def test_join_alignment_uses_sole_pending_identity_and_true_join_time():
    aligned, counts = align([_join()], [_stream()])
    assert counts["matched_natural_joins"] == 1
    assert aligned[0]["lead_ms"] == 5010.
    assert aligned[0]["return_to_join_ms"] == 10.
    missing, counts = align(
        [_join(), _join(task_id="alpha__other", request_id="other")],
        [_stream(), _stream(request_id="rid-other", trigger_ms=1252.)],
    )
    assert len(missing) == 1
    assert counts["no_live_trigger_after_delay"] == 1


def test_join_alignment_rejects_nonfinal_and_reversed_clocks():
    with pytest.raises(ValueError, match="final stream"):
        align([_join()], [_stream(final=False)])
    with pytest.raises(ValueError, match="before final child"):
        align([_join(join_lead_ms=5000.)], [_stream()])
    late, counts = align([_join(join_lead_ms=200.)], [_stream(lead_ms=-60.)])
    assert late == []
    assert counts["join_before_delayed_trigger"] == 1


def test_join_alignment_excludes_wrong_join_and_unmatched_delay():
    missing, counts = align(
        [_join(), _join(task_id="alpha__second", request_id="rid2")],
        [
            _stream(join_id="join-other"),
            _stream(
                trace_path="/workflows/alpha__second/runtime_events.deepagents.jsonl",
                request_id="rid2", trigger_ms=1400.,
            ),
        ],
    )
    assert missing == []
    assert counts["natural_join_candidates"] == 2
    assert counts["no_live_trigger_after_delay"] == 2


def test_join_evaluator_keeps_full_project_and_candidate_denominators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    train = tmp_path / "train"
    heldout = tmp_path / "heldout"
    train_ids = [f"alpha__{i}" for i in range(25)]
    heldout_ids = [f"beta__{i}" for i in range(6)]
    monkeypatch.setattr(
        "scripts.evaluate_join_stream_eta.require_complete_batch",
        lambda root: (train_ids if root == train else heldout_ids, []),
    )

    def joins(root, stage):
        ids = train_ids if root == train else heldout_ids
        return [
            _join(
                task_id=item, request_id=item, join_id=item,
                join_lead_ms=1260. + 10 * index,
            )
            for index, item in enumerate(ids)
        ], {"groups": len(ids)}

    def streams(root, *, stage_chars):
        ids = train_ids if root == train else heldout_ids
        return [
            _stream(
                trace_path=f"/workflows/{item}/runtime_events.deepagents.jsonl",
                request_id=item, join_id=item,
                lead_ms=1000. + 10 * index,
                features=[float(index % 3)] * 5,
            )
            for index, item in enumerate(ids)
        ], 0

    monkeypatch.setattr("scripts.evaluate_join_stream_eta.collect_joins", joins)
    monkeypatch.setattr("scripts.evaluate_join_stream_eta.samples", streams)
    result = evaluate(train, heldout)
    assert result["heldout_alignment"]["natural_join_candidates"] == 6
    assert result["heldout"]["natural_joins"] == 6
    assert result["heldout"]["return_to_join_p50_ms"] == 10.
    assert result["heldout_paired_gain"]["workflows"] == 6
    assert result["heldout_by_project"]["beta"]["frozen_tasks"] == 6

    heldout_ids[:] = ["alpha__collision"]
    with pytest.raises(ValueError, match="disjoint"):
        evaluate(train, heldout)
