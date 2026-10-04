from dataclasses import replace

import numpy as np
import pytest

from beliefkv.predictor.child_report_phase import ReportObservation
from beliefkv.predictor.child_semantic_work import (
    SemanticHead, SemanticReportPredictor, calibrate_scores_and_bias, calibrate_work_bounds,
    choose_request_threshold, fit_head,
)
from scripts.train_child_semantic_work import split_roles
from beliefkv.predictor.conditional_work import NeuralConditionalWork


def rows(project="train", count=18):
    return [
        {
            "observation": ReportObservation(
                f"{project}-r{i}", f"{project}-child{i}", "ctx", 1, 1000.,
                "Report complete." if i % 3 == 2 else "More checking needed.",
                64 + i, 10 + i, notice_active=i % 3 == 2,
                estimated_report_tokens=200 if i % 3 == 2 else 0,
            ),
            "task": f"{project}__task{i // 3}", "project": project,
            "phase_label": i % 3, "is_return": i % 3 == 2,
            "remaining_tokens": 40 + i if i % 3 == 2 else None,
        }
        for i in range(count)
    ]


def test_roles_and_selector_interval_workflows_are_disjoint():
    samples = rows() + rows("cal") + rows("test")
    plan = {
        "training_projects": ["train"], "calibration_projects": ["cal"],
        "evaluation_projects": ["test"],
    }
    roles = split_roles(samples, plan)
    selector = {samples[i]["task"] for i in roles["selector"]}
    interval = {samples[i]["task"] for i in roles["interval"]}
    assert selector.isdisjoint(interval)
    assert selector | interval == {row["task"] for row in rows("cal")}
    assert all(samples[i]["project"] == "train" for i in roles["training"])
    with pytest.raises(ValueError, match="overlap"):
        split_roles(samples, {**plan, "calibration_projects": ["train"]})


def test_neural_work_changes_only_remaining_work_and_preserves_tool_invalidation():
    training = rows()
    embeddings = np.zeros((len(training), 8))
    head = fit_head(
        training, embeddings, dimensions=0,
        phase_regularization=.1, work_regularization=.2,
    )
    width = head.design([training[0]["observation"]], embeddings[:1]).shape[1] + 8
    work = NeuralConditionalWork({
        "kind": "neural_conditional_work", "schema_version": 1,
        "center": [0.] * width, "scale": [1.] * width,
        "layers": [
            {"weight": [[0.] * width], "bias": [0.]},
            {"weight": [[0.]], "bias": [0.]},
            {"weight": [[0.]], "bias": [np.log1p(300.)]},
        ],
        "target": "total", "token_bias": 0., "interval_margin_tokens": 20.,
    })
    before = SemanticReportPredictor(head, None)
    after = SemanticReportPredictor(head, None, neural_work=work)
    observation = replace(training[0]["observation"], observed_output_tokens=250)
    old = before.predict([observation])[0]
    new = after.predict([observation])[0]
    assert new.final_report_score == old.final_report_score
    assert new.completion_notice_score == old.completion_notice_score
    assert new.conditional_remaining_tokens == pytest.approx((30., 50., 70.))
    assert after.predict([replace(observation, tool_chunk_seen=True)])[0].conditional_remaining_tokens is None


@pytest.mark.parametrize("dimensions", [0, 4])
def test_head_roundtrip_and_predictions_do_not_accept_future_labels(tmp_path, dimensions):
    training = rows()
    rng = np.random.default_rng(42)
    embeddings = rng.normal(size=(len(training), 8))
    head = fit_head(
        training, embeddings, dimensions=dimensions,
        phase_regularization=.1, work_regularization=.2,
    )
    observations = [row["observation"] for row in training]
    before = head.predictions(observations, embeddings)
    training[0]["remaining_tokens"] = 999999
    training[0]["phase_label"] = 2
    assert head.predictions(observations, embeddings) == before
    assert all(row.return_eta_ms is None for row in before)
    assert all(row.physical_action_authorized is False for row in before)
    path = tmp_path / "head.json"
    head.save(path, metadata={"training_projects": ["train"]})
    loaded = SemanticHead.load(path)
    assert loaded.predictions(observations, embeddings) == before
    tool = replace(observations[0], tool_chunk_seen=True)
    assert loaded.predictions([tool], embeddings[:1])[0].conditional_remaining_tokens is None


def test_workflow_calibration_does_not_count_repeated_snapshots_as_workflows():
    class Head:
        interval_margin = 0.
        work_bounds_calibrated = False

        def arrays(self, observations, embeddings):
            return np.zeros((len(observations), 3)), np.tile(
                [0., 1., 2.], (len(observations), 1),
            )

    data = [
        {"task": f"task{i}", "observation": rows(count=1)[0]["observation"],
         "remaining_tokens": 3 + i}
        for i in range(5) for _ in range(10)
    ]
    head = Head()
    calibrated = calibrate_work_bounds(
        head, data, np.zeros((len(data), 8)), coverage=.8,
    )
    assert calibrated["workflow_count"] == 5
    assert calibrated["rank"] == 5
    assert head.interval_margin == 5.
    assert head.work_bounds_calibrated
    with pytest.raises(ValueError, match="insufficient"):
        calibrate_work_bounds(
            Head(), data[:10], np.zeros((10, 8)), coverage=.8,
        )


def test_score_and_bias_calibration_does_not_claim_work_bound_calibration():
    training = rows()
    rng = np.random.default_rng(21)
    embeddings = rng.normal(size=(len(training), 8))
    head = fit_head(
        training, embeddings, dimensions=4,
        phase_regularization=.1, work_regularization=.2,
    )
    result = calibrate_scores_and_bias(head, training, embeddings)
    assert .25 <= result["temperature"] <= 8.
    assert head.calibration_status != "uncalibrated"
    assert not head.work_bounds_calibrated
    predictions = head.predictions(
        [row["observation"] for row in training], embeddings,
    )
    assert predictions[0].work_interval_status == "training_residual_interval_uncalibrated"
    for value in predictions:
        low, middle, high = value.conditional_remaining_tokens
        assert 0 <= low <= middle <= high


def test_request_threshold_counts_requests_not_snapshots():
    samples = rows(count=6)
    samples = [row for row in samples if row["is_return"]] + [samples[0]]
    scores = np.asarray([.95, .92, .9])
    operating = choose_request_threshold(
        samples * 10, np.tile(scores, 10), precision=.9, minimum=2,
    )
    assert operating["selected"] == 2
    assert operating["threshold"] == .92
    absent = choose_request_threshold(
        samples, scores, precision=1., minimum=3,
    )
    assert absent["threshold"] is None


def test_semantic_cache_reuses_text_but_keeps_new_request_and_epoch():
    training = rows()
    embeddings = np.random.default_rng(4).normal(size=(len(training), 8))
    head = fit_head(
        training, embeddings, dimensions=4,
        phase_regularization=.1, work_regularization=.2,
    )

    class Encoder:
        calls = []

        def encode(self, texts):
            self.calls.append(list(texts))
            return np.asarray([
                np.full(8, len(text), dtype=float) for text in texts
            ]).reshape(-1, 8)

    encoder = Encoder()
    predictor = SemanticReportPredictor(head, encoder, cache_size=1)
    observed = [row["observation"] for row in training[:3]]
    predictor.predict(observed)
    assert len(encoder.calls[0]) == 2
    changed = replace(observed[-1], request_id="new-request", context_epoch=3)
    result = predictor.predict([changed])[0]
    assert encoder.calls[-1] == []
    assert result.request_id == "new-request"
    assert result.context_epoch == 3
    assert result.physical_action_authorized is False
    assert len(predictor._text_cache) == 1


def test_work_only_update_preserves_every_phase_score():
    training = rows()
    embeddings = np.zeros((len(training), 8))
    phase = fit_head(
        training, embeddings, dimensions=0,
        phase_regularization=.1, work_regularization=.2,
    )
    work = replace(phase, work_coefficients=phase.work_coefficients.copy())
    work.work_coefficients[0] += 1
    observations = [row["observation"] for row in training]
    original = SemanticReportPredictor(phase, None).predict(observations)
    changed = SemanticReportPredictor(phase, None, work_head=work).predict(observations)
    assert [row.final_report_score for row in changed] == [
        row.final_report_score for row in original
    ]
    assert [row.completion_notice_score for row in changed] == [
        row.completion_notice_score for row in original
    ]
    assert changed[0].conditional_remaining_tokens != original[0].conditional_remaining_tokens


def test_total_length_work_subtracts_observed_progress_before_bias_clipping():
    training = rows()
    head = fit_head(
        training, np.zeros((len(training), 8)), dimensions=0,
        phase_regularization=.1, work_regularization=.2, work_target="total",
    )
    head.work_coefficients[:] = 0
    head.work_coefficients[0] = np.log1p(100)
    head.work_residual_quantiles[:] = 0
    head.token_bias = 10
    observation = training[0]["observation"]
    _, first = head.arrays([replace(observation, observed_output_tokens=80)], np.zeros((1, 8)))
    _, last = head.arrays([replace(observation, observed_output_tokens=120)], np.zeros((1, 8)))
    assert np.allclose(first, [[30, 30, 30]])
    assert np.allclose(last, [[0, 0, 0]])
