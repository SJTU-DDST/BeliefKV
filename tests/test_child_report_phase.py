from dataclasses import asdict, replace

import numpy as np
import pytest
import torch

from beliefkv.predictor.child_report_phase import (
    ChildReportPredictor, ReportObservation, ReportPhaseNetwork,
    fit_vocabulary, pinball_loss, tensors,
)
from scripts.evaluate_child_report_phase_work import (
    fit, observation_for, quality, select_work_snapshots,
)
from scripts.pilot_child_stream_service_progress import delivered_history


def observation(**kwargs):
    return ReportObservation(
        "rid", "child", "ctx", 2, 1000., "The fix is verified.", 64, 20,
        **kwargs,
    )


def test_observation_contains_no_wall_wait_or_future_labels():
    row = observation()
    fields = asdict(row)
    assert "remaining_tokens" not in fields
    assert "return_ts" not in fields
    assert "future_no_service_ms" not in fields
    assert len(row.features(with_events=False)) == 8
    notice = observation(notice_active=True, estimated_report_tokens=100)
    assert notice.features(with_events=True)[5] == 1
    assert notice.features(with_events=False)[5:] == [0., 0., 0.]
    with pytest.raises(ValueError):
        replace(row, context_epoch=-1)


def test_rolling_snapshots_cover_the_report_tail_without_future_finish_information():
    snapshots = [
        {"ts_ms": float(index * 100), "content_chars": index * 300}
        for index in range(16)
    ]
    fixed = select_work_snapshots(snapshots, "fixed_checkpoints")
    rolling = select_work_snapshots(snapshots, "rolling_250ms")
    assert max(fixed) == 400.
    assert list(rolling) == [100., 400., 700., 1000., 1300.]
    assert rolling[1300.][0]["content_chars"] == 3900
    rolling_fast = select_work_snapshots(snapshots, "rolling_100ms")
    assert list(rolling_fast) == [float(index * 100) for index in range(1, 16)]
    # Appending unseen future text must not change any prior selection.
    extra = snapshots + [{"ts_ms": 2000., "content_chars": 6000}]
    assert all(
        select_work_snapshots(extra, "rolling_250ms")[ts] == value
        for ts, value in rolling.items()
    )


def test_delivered_history_uses_only_new_delivered_text_and_marks_gaps():
    first, gap = delivered_history("", 0, {
        "content_chars": 4, "content_tail": "test",
    })
    assert first == "test"
    assert not gap
    second, gap = delivered_history(first, 4, {
        "content_chars": 9, "content_tail": "test more",
    })
    assert second == "test more"
    assert not gap
    missing, gap = delivered_history(second, 9, {
        "content_chars": 50, "content_tail": "end",
    })
    assert gap
    assert missing.endswith("[unobserved_text_gap] end")
    assert "invented" not in missing


def test_notices_are_causal_and_invalidated_by_tools_and_context_changes():
    row = {"rid": "rid", "invocation_id": "child", "context_id": "ctx",
           "context_epoch": 2}
    snap = {"ts_ms": 1000., "content_tail": "report", "content_chars": 64,
            "observed_output_tokens": 20}
    stage = {
        "ts_ms": 800., "kind": "structured_action", "context_id": "ctx",
        "context_epoch": 1, "attributes": {
            "child_completion_signal_kind": "stage",
            "beliefkv_child_completion_intent": True,
            "estimated_final_report_tokens": 200,
        },
    }
    submit = {"ts_ms": 900., "kind": "llm_submit", "context_id": "ctx",
              "attributes": {}}
    observed = observation_for(row, snap, [stage, submit])
    assert observed.notice_active
    assert observed.estimated_report_tokens == 200
    assert not observation_for(row, snap, [{**stage, "ts_ms": 1001.}]).notice_active
    tool = {"ts_ms": 950., "kind": "tool_start",
            "attributes": {"tool_name": "read_file"}}
    assert not observation_for(row, snap, [stage, submit, tool]).notice_active
    changed = {**submit, "context_id": "other"}
    assert not observation_for(row, snap, [stage, changed]).notice_active


def test_vocabulary_only_sees_training_tokens_and_keeps_word_order():
    training = [observation(), replace(observation(), request_id="second")]
    vocabulary = fit_vocabulary(training)
    assert "verified" in vocabulary
    assert "heldout_only" not in vocabulary
    held = replace(observation(), content_tail="heldout_only verified")
    ids, _ = tensors([held], vocabulary, with_events=False)
    assert ids[0, 0] == 1
    assert ids[0, 1] == vocabulary["verified"]


def test_quantiles_are_ordered_and_predictor_never_authorizes_actions(tmp_path):
    torch.set_num_threads(1)
    row = observation()
    vocabulary = fit_vocabulary([row, row])
    model = ChildReportPredictor(
        ReportPhaseNetwork(len(vocabulary) + 2), vocabulary,
        np.zeros(8, dtype=np.float32), np.ones(8, dtype=np.float32),
        with_events=True,
    )
    expected = model.predict([row])[0]
    low, middle, high = expected.conditional_remaining_tokens
    assert 0 <= low <= middle <= high
    assert expected.return_eta_ms is None
    assert expected.physical_action_authorized is False
    assert expected.score_status == "uncalibrated"
    assert 0 <= expected.final_report_score + expected.completion_notice_score <= 1.
    assert expected.predicted_phase in {
        "continue_work", "completion_notice", "final_report",
    }
    tool = model.predict([replace(row, tool_chunk_seen=True)])[0]
    assert tool.final_report_score == 0
    assert tool.conditional_remaining_tokens is None
    assert tool.observed_stage == "observed_tool"
    path = tmp_path / "head.pt"
    model.save(path, training_projects=["alpha"])
    loaded = ChildReportPredictor.load(path)
    assert loaded.predict([row])[0] == expected
    assert torch.load(path, weights_only=True)["online_eligible"] is False


def test_pinball_loss_and_report_keep_nonterminal_denominator():
    prediction = torch.tensor([[1., 2., 3.]], requires_grad=True)
    loss = pinball_loss(prediction, torch.tensor([2.]))
    assert loss.item() == pytest.approx(0.2 / 3)
    loss.sum().backward()
    records = [
        {
            "is_return": True, "scores": {"head": .8},
            "remaining_tokens": 20, "work": {"head": (10, 25, 30)},
            "future_decode_interval_ms": None,
        },
        {
            "is_return": False, "scores": {"head": .9},
            "remaining_tokens": None, "work": {"head": (10, 25, 30)},
        },
    ]
    result = quality(records, "head")
    assert result["precision"] == .5
    assert result["return_recall"] == 1.
    assert result["tool_false_positive_rate"] == 1.
    assert result["remaining_token_mae"] == 5.
    assert result["conditional_work_count"] == 1


def test_training_phase_balance_changes_only_loss_weights_not_runtime_policy():
    torch.set_num_threads(1)
    samples = [
        {
            "observation": replace(
                observation(), request_id=f"r{i}", invocation_id=f"c{i}",
                content_tail="Notice ready." if phase == 1 else "Report ready.",
            ),
            "task": f"task-{i}", "phase_label": phase,
            "remaining_tokens": 30 if phase == 2 else None,
        }
        for i, phase in enumerate((0, 0, 0, 1, 2, 2))
    ]
    model, info = fit(
        samples, with_events=True, epochs=1, seed=21, balance_phases=True,
    )
    assert info["training_phase_counts"] == [3, 1, 2]
    assert info["phase_loss_weights"][1] > info["phase_loss_weights"][0]
    predicted = model.predict([samples[0]["observation"]])[0]
    assert predicted.physical_action_authorized is False
    assert predicted.return_eta_ms is None
