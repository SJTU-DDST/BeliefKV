from dataclasses import replace

import numpy as np
import pytest

from beliefkv.predictor.child_report_phase import ReportObservation
from beliefkv.predictor.child_semantic_work import (
    SemanticHead, SemanticReportPredictor, calibrate_scores_and_bias, calibrate_work_bounds,
    choose_request_threshold, encoder_snapshot, fit_head,
)
from scripts.train_child_semantic_work import split_roles
from beliefkv.predictor.conditional_work import (
    LEGACY_WORK_PROJECTION, SIGNED_WORK_PROJECTION, NeuralConditionalWork,
    project_work_bounds, structural_work_features,
)


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


def test_signed_work_margin_has_no_clipping_floor_and_legacy_is_unchanged():
    raw = np.asarray([[-40., -30., -20.], [-15., -10., -5.], [10., 20., 30.]])
    signed = project_work_bounds(
        raw, bias=0., margin=10., projection=SIGNED_WORK_PROJECTION,
    )
    legacy = project_work_bounds(
        raw, bias=0., margin=10., projection=LEGACY_WORK_PROJECTION,
    )
    assert np.allclose(signed, [[0., 0., 0.], [0., 0., 5.], [0., 20., 40.]])
    assert np.allclose(legacy, [[0., 0., 10.], [0., 0., 10.], [0., 20., 40.]])
    assert np.all(np.diff(signed, axis=1) >= 0)
    with pytest.raises(ValueError, match="unsupported"):
        project_work_bounds(raw, bias=0., margin=10., projection="unversioned")


def test_loading_old_head_preserves_legacy_calibrated_margin(tmp_path):
    import json

    data = rows()
    head = fit_head(
        data, np.zeros((len(data), 8)), dimensions=0,
        phase_regularization=.1, work_regularization=.2, work_target="total",
    )
    head.work_coefficients[:] = 0.
    head.work_coefficients[0] = np.log1p(10.)
    head.work_residual_quantiles[:] = 0.
    head.interval_margin = 15.
    path = tmp_path / "old.json"
    head.save(path, metadata={})
    raw = json.loads(path.read_text())
    raw["head"].pop("work_interval_projection")
    path.write_text(json.dumps(raw))
    old = SemanticHead.load(path)
    assert old.work_interval_projection == LEGACY_WORK_PROJECTION
    observation = replace(data[0]["observation"], observed_output_tokens=30)
    _, bounds = old.arrays([observation], np.zeros((1, 8)))
    assert bounds[0, 2] == 15.
    raw["head"]["work_interval_projection"] = SIGNED_WORK_PROJECTION
    path.write_text(json.dumps(raw))
    _, new_bounds = SemanticHead.load(path).arrays(
        [observation], np.zeros((1, 8)),
    )
    assert new_bounds[0, 2] == 0.


def test_signed_calibration_uses_unclipped_residuals_for_positive_work():
    data = rows()
    head = fit_head(
        data, np.zeros((len(data), 8)), dimensions=0,
        phase_regularization=.1, work_regularization=.2, work_target="total",
    )
    head.work_coefficients[:] = 0.
    head.work_coefficients[0] = np.log1p(10.)
    head.work_residual_quantiles[:] = 0.
    head.token_bias = 0.
    head.interval_margin = 10000.
    samples = [
        {"task": f"task{i}", "observation": replace(
            data[0]["observation"], observed_output_tokens=20,
        ), "remaining_tokens": 5.} for i in range(5)
    ]
    result = calibrate_work_bounds(head, samples, np.zeros((5, 8)), coverage=.8)
    assert result["added_token_margin"] == pytest.approx(15.)
    _, work = head.arrays(
        [row["observation"] for row in samples], np.zeros((5, 8)),
    )
    assert work[:, 2] == pytest.approx(np.full(5, 5.))
    for row in samples:
        row["remaining_tokens"] = 0.
    result = calibrate_work_bounds(head, samples, np.zeros((5, 8)), coverage=.8)
    assert result["added_token_margin"] == 0.


@pytest.mark.parametrize("projection, expected", [
    (LEGACY_WORK_PROJECTION, 30.),
    (SIGNED_WORK_PROJECTION, 0.),
])
def test_neural_total_work_uses_versioned_interval_projection(projection, expected):
    class Phase:
        def design(self, observations, embeddings):
            return np.zeros((len(observations), 2))

    work = NeuralConditionalWork({
        "kind": "neural_conditional_work", "schema_version": 1,
        "center": [0.] * 10, "scale": [1.] * 10,
        "layers": [
            {"weight": [[0.] * 10], "bias": [0.]},
            {"weight": [[0.]], "bias": [0.]},
            {"weight": [[0.]], "bias": [np.log1p(100.)]},
        ],
        "target": "total", "token_bias": 0., "interval_margin_tokens": 30.,
        "work_interval_projection": projection,
    })
    observation = replace(rows()[0]["observation"], observed_output_tokens=140)
    bounds = work.arrays([observation], np.zeros((1, 8)), Phase())
    assert bounds[0, 2] == expected


def test_work_trigger_reports_first_early_crossing_and_false_tool_round():
    from scripts.compare_semantic_rolling_work import work_triggers

    row = {
        "request_id": "return", "snapshot_ts_ms": 100.,
        "notice_active": True, "observed_output_tokens": 100,
        "scores": {"candidate": .99}, "candidate": 0.,
        "candidate_bounds": [0., 0., 0.], "sampled_tokens_per_second": 50.,
        "actual_remaining_tokens": 100, "remaining_client_wall_ms": 3000.,
    }
    later = {
        **row, "snapshot_ts_ms": 2900., "actual_remaining_tokens": 5,
        "remaining_client_wall_ms": 200.,
    }
    tool = {**row, "request_id": "tool", "actual_remaining_tokens": None}
    report = work_triggers([row, later, tool], "candidate", .9, time_horizon_ms=1000.)
    assert report["first_trigger_count"] == 2
    assert report["tool_round_false_trigger_count"] == 1
    assert report["lead_over_2000ms_count"] == 1
    assert report["lead_0_to_1000ms_count"] == 0
    assert report["upper_underestimated_at_trigger_count"] == 1


def test_composite_refit_keeps_the_actual_frozen_encoder_location(tmp_path):
    original = tmp_path / "original" / "adapted_encoder"
    original.mkdir(parents=True)
    refit = tmp_path / "work_refit" / "semantic.json"
    raw = {"metadata": {"adapted_encoder": {"snapshot": str(original)}}}
    assert encoder_snapshot(raw, refit) == original
    raw["metadata"]["adapted_encoder"]["snapshot"] = "adapted_encoder"
    with pytest.raises(FileNotFoundError):
        encoder_snapshot(raw, refit)
    local = refit.parent / "adapted_encoder"
    local.mkdir(parents=True)
    assert encoder_snapshot(raw, refit) == local


def test_log_quantile_work_keeps_order_and_does_not_reinterpret_scalar_schema():
    class Phase:
        def design(self, observations, embeddings):
            return np.zeros((len(observations), 2))

    raw = {
        "kind": "neural_conditional_work", "schema_version": 2,
        "center": [0.] * 10, "scale": [1.] * 10,
        "layers": [
            {"weight": [[0.] * 10], "bias": [0.]},
            {"weight": [[0.]], "bias": [0.]},
            {"weight": [[0.], [0.], [0.]], "bias": [3., 1., 2.]},
        ],
        "target": "remaining", "output_space": "ordered_log1p_quantiles",
        "log1p_bias": 0., "interval_margin_log1p": .5,
    }
    work = NeuralConditionalWork(raw)
    bounds = work.arrays([rows()[0]["observation"]], np.zeros((1, 8)), Phase())
    assert bounds[0] == pytest.approx(np.expm1([.5, 2., 3.5]))
    old = {**raw, "schema_version": 1, "token_bias": 0., "interval_margin_tokens": 10.}
    with pytest.raises(ValueError, match="width"):
        NeuralConditionalWork(old)
    with pytest.raises(ValueError, match="geometry"):
        NeuralConditionalWork({**raw, "target": "total"})


def test_log_work_calibration_clusters_snapshots_and_covers_positive_signed_work():
    from scripts.fit_semantic_work_quantiles import interval_margin

    actual = np.tile(np.asarray([0., 1.]), 5)
    raw = np.tile(np.asarray([[-10., -9., -8.], [-4., -3., -2.]]), (5, 1))
    tasks = [f"task{i}" for i in range(5) for _ in range(2)]
    margin, calibration = interval_margin(raw, actual, tasks, .8)
    assert margin == 3.
    assert calibration == {"workflow_count": 5, "rank": 5}
    with pytest.raises(ValueError, match="insufficient"):
        interval_margin(raw[:2], actual[:2], tasks[:2], .8)


def test_body_progress_separates_visible_report_from_total_decode_without_future_input():
    row = replace(
        rows()[0]["observation"], notice_active=True, estimated_report_tokens=500,
        content_chars=400, observed_output_tokens=800,
    )
    legacy = structural_work_features([row])
    body = structural_work_features([row], version="body_progress_v2")
    assert legacy.shape == (1, 8)
    assert body.shape == (1, 12)
    assert legacy[0, 7] == 0.
    assert body[0, 7] == pytest.approx(np.log1p(400))
    assert body[0, 9] == pytest.approx(.2)
    assert body[0, 10] == pytest.approx(np.log1p(700))


def test_suffix_work_features_preserve_recent_order_without_future_or_old_prefix():
    row = replace(rows()[0]["observation"], content_tail="tests passed; report complete.")
    changed = replace(row, content_tail="report complete; tests passed.")
    suffix = structural_work_features([row], version="suffix_sequence_v3")
    reordered = structural_work_features([changed], version="suffix_sequence_v3")
    assert suffix.shape == (1, 72)
    assert suffix[0, :8] == pytest.approx(structural_work_features([row])[0])
    assert not np.allclose(suffix[0, 8:], reordered[0, 8:])
    assert np.linalg.norm(suffix[0, 8:]) == pytest.approx(1.)
    tail = " ".join(f"token{i}" for i in range(40))
    recent = structural_work_features(
        [replace(row, content_tail=tail)], version="suffix_sequence_v3",
    )
    prefixed = structural_work_features(
        [replace(row, content_tail="old text " * 200 + tail)], version="suffix_sequence_v3",
    )
    assert recent[0, 8:] == pytest.approx(prefixed[0, 8:])
    empty = structural_work_features(
        [replace(row, content_tail="")], version="suffix_sequence_v3",
    )
    assert np.isfinite(empty).all()
    assert not empty[0, 8:].any()


def test_cached_work_samples_bind_exclusions_to_the_original_run(tmp_path):
    import gzip
    import hashlib
    import json
    from dataclasses import asdict

    from scripts.fit_semantic_work_quantiles import cached_work_samples

    plan = {
        "training_runs": ["run"], "calibration_evaluation_runs": [],
        "snapshot_policy": "rolling_100ms",
    }
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "plan": plan, "excluded_intervened_tasks_by_run": {"run": ["train__task0"]},
    }))
    cached = tmp_path / "samples.json.gz"
    data = {
        "coverage": {"snapshot_policy": "rolling_100ms"},
        "rows": [{
            **row, "observation": asdict(row["observation"]), "run": str(tmp_path / "run"),
        } for row in rows(count=6)],
    }
    manifest = tmp_path / "manifest.json"

    def save():
        with gzip.open(cached, "wt", encoding="utf-8") as stream:
            json.dump(data, stream)
        manifest.write_text(json.dumps({
            "source_root": str(tmp_path), "source_report": str(report),
            "source_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            "samples": [{
                "run": "run", "path": str(cached),
                "sha256": hashlib.sha256(cached.read_bytes()).hexdigest(),
            }],
        }))

    save()
    samples, _, _ = cached_work_samples(manifest, plan)
    assert [row["observation"].request_id for row in samples] == ["train-r5"]
    data["rows"][0]["run"] = str(tmp_path / "different-run")
    save()
    with pytest.raises(ValueError, match="different run"):
        cached_work_samples(manifest, plan)
