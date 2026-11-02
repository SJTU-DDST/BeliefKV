from copy import deepcopy

import pytest

from beliefkv.predictor.workflow_task_encoder import (
    DIMENSION, FILES, KIND, TASK_FEATURES, WorkflowTaskEncoder, task_features,
    validate_encoder,
)
from beliefkv.predictor.workflow_work import R5_SCHEMA, WORD_ENCODING, WorkflowWorkModel, ngrams, task_terms
from beliefkv.predictor.workflow_work_training import phase_weights, ready_weights
from beliefkv.predictor.workflow_work_prior import KIND as PRIOR_KIND, TotalWorkPrior
from tests.test_workflow_work_distribution import distribution_raw


def encoder_raw():
    return {
        "kind": KIND, "directory": "/unused/pinned-encoder", "revision": "1" * 40,
        "files_sha256": {name: "1" * 64 for name in FILES},
        "embedding_dimension": DIMENSION, "task_features": list(TASK_FEATURES),
        "maximum_task_characters": 4096, "chunk_tokens": 256,
        "pooling": "token_mean_then_length_weighted_chunk_mean_l2",
        "device": "cpu", "remote_code": False,
    }


def test_training_phases_keep_initial_workflow_weight_despite_dense_late_snapshots():
    rows = []
    for name, count in (("a", 1), ("b", 100)):
        rows.append({"workflow": name, "snapshot": 0, "attained_work": 0., "total_work": 100.})
        for phase in range(3):
            rows.extend({
                "workflow": name, "snapshot": len(rows) + index,
                "attained_work": 10. + phase * 33., "total_work": 100.,
            } for index in range(count))
    weights = phase_weights(rows)
    for name in ("a", "b"):
        assert sum(weight for row, weight in zip(rows, weights) if row["workflow"] == name) == pytest.approx(.5)
        assert next(weight for row, weight in zip(rows, weights) if (
            row["workflow"] == name and row["snapshot"] == 0
        )) == pytest.approx(.125)
    assert weights.sum() == pytest.approx(1.)


@pytest.mark.parametrize("change", ("missing_initial", "negative_work", "duplicate_initial"))
def test_phase_weighting_rejects_partial_or_invalid_execution_samples(change):
    rows = [{"workflow": "a", "snapshot": 0, "attained_work": 0., "total_work": 10.}]
    if change == "missing_initial":
        rows[0]["snapshot"] = 1
    elif change == "negative_work":
        rows[0]["attained_work"] = -1
    else:
        rows.append(dict(rows[0]))
    with pytest.raises(ValueError):
        phase_weights(rows)


def test_ready_training_focus_uses_observed_readiness_and_keeps_equal_workflow_mass():
    import numpy as np
    from beliefkv.predictor.workflow_work import R5_FEATURES

    rows = []
    for workflow in ("a", "b"):
        for ready in (0., 1.):
            values = [0.] * len(R5_FEATURES)
            values[R5_FEATURES.index("ready")] = ready
            rows.append({"workflow": workflow, "features": values})
    base = np.asarray([.25] * 4)
    weighted = ready_weights(rows, base)
    assert weighted.tolist() == pytest.approx([.0625, .4375, .0625, .4375])
    assert base.tolist() == [.25] * 4
    assert weighted[:2].sum() == weighted[2:].sum() == .5


def test_word_encoding_retains_task_intent_and_structure_with_unchanged_legacy_encoding():
    text = "Implement a feature\n1. Add src/core.py and test it."
    assert task_terms(text) == ngrams(text)
    terms = task_terms(text, WORD_ENCODING)
    assert terms["word:implement"] == 3
    assert terms["structure:numbered_requirements"] == pytest.approx(1.)
    assert terms["structure:implement"] == pytest.approx(1.)
    assert "word:the" not in terms
    with pytest.raises(ValueError, match="unknown"):
        task_terms(text, "unknown")


@pytest.mark.parametrize("field,value", (
    ("device", "cuda"), ("remote_code", True), ("maximum_task_characters", 200000),
    ("revision", "main"), ("files_sha256", {}), ("chunk_tokens", 2048),
    ("directory", "relative/model"),
))
def test_semantic_encoder_cannot_change_device_input_scope_or_unpin_weights(field, value):
    contract = encoder_raw()
    contract[field] = value
    with pytest.raises(ValueError):
        validate_encoder(contract)


def test_task_structure_features_are_fixed_and_use_only_explicit_instruction_text():
    values = task_features("Implement a feature.\n1. Add src/core.py.\n2. Test the integration.")
    assert len(values) == len(TASK_FEATURES)
    features = dict(zip(TASK_FEATURES, values))
    assert features["implement"] > 0
    assert features["numbered_requirements"] > features["file_references"] > 0
    assert features["explain"] == features["read_only"] == 0


def test_encoder_keeps_later_instruction_chunks_and_never_uses_gpu_or_full_prompt():
    import numpy as np
    import torch
    from types import SimpleNamespace

    class Tokenizer:
        unk_token_id = 0

        def num_special_tokens_to_add(self, *, pair):
            assert not pair
            return 2

        def __call__(
            self, text, *, max_length, truncation, return_overflowing_tokens, padding, return_tensors,
        ):
            assert padding and return_tensors == "pt"
            assert truncation and return_overflowing_tokens and max_length == 256
            tokens = [ord(character) for character in text]
            rows = [
                {"input_ids": [1, *tokens[index:index + 254], 2]}
                for index in range(0, len(tokens), 254)
            ]
            width = max(len(row["input_ids"]) for row in rows)
            return {
                "input_ids": torch.tensor([
                    row["input_ids"] + [0] * (width - len(row["input_ids"])) for row in rows
                ]),
                "attention_mask": torch.tensor([
                    [1] * len(row["input_ids"]) + [0] * (width - len(row["input_ids"])) for row in rows
                ]),
            }

    seen = []

    class Model:
        def __call__(self, input_ids, attention_mask):
            assert input_ids.device.type == "cpu"
            assert not torch.is_grad_enabled()
            seen.append(input_ids.clone())
            states = torch.zeros((*input_ids.shape, DIMENSION))
            states[:, :, 0] = input_ids / 100.
            states[:, :, 1] = 1.
            return SimpleNamespace(last_hidden_state=states)

    encoder = WorkflowTaskEncoder(encoder_raw())
    encoder._tokenizer, encoder._model = Tokenizer(), Model()
    vector, coverage = encoder.encode("a" * 254 + "z" * 146)
    assert vector.shape == (DIMENSION + len(TASK_FEATURES),)
    assert coverage == 1.
    assert seen[0].shape == (2, 256)
    assert 122 in seen[0][1]
    assert np.linalg.norm(vector[:DIMENSION]) == pytest.approx(1.)
    assert encoder.encode("")[0] is None
    with pytest.raises(ValueError, match="bounded explicit"):
        encoder.encode("x" * 4097)


def test_changed_encoder_file_is_rejected_before_loading_transformer(tmp_path):
    raw = encoder_raw()
    raw["directory"] = str(tmp_path)
    for name in FILES:
        (tmp_path / name).write_bytes(b"changed")
    with pytest.raises(ValueError, match="encoder file changed"):
        WorkflowTaskEncoder(raw).encode("explicit task")


def test_semantic_model_has_separate_static_dimensions_and_preserves_cached_numeric_inference(monkeypatch):
    import numpy as np

    raw = distribution_raw()
    raw.update(
        task_encoder=encoder_raw(), vocabulary=[], idf=[],
        static_weight=[[0.]] * (DIMENSION + len(TASK_FEATURES)),
    )
    monkeypatch.setattr(WorkflowTaskEncoder, "encode", lambda _self, _text: (
        np.ones(DIMENSION + len(TASK_FEATURES)), 1.,
    ))
    model = WorkflowWorkModel(raw)
    assert model.schema == R5_SCHEMA
    assert model.predict(model.task_projection("task"), [0.] * len(model.feature_names)) == pytest.approx(
        WorkflowWorkModel(distribution_raw()).predict(
            np.zeros(1), [0.] * len(model.feature_names),
        ),
    )
    bad = deepcopy(raw)
    bad["static_weight"] = [[0.]]
    with pytest.raises(ValueError, match="weight dimensions"):
        WorkflowWorkModel(bad)
    bad = deepcopy(raw)
    bad["vocabulary"], bad["idf"] = ["ab"], [1.]
    with pytest.raises(ValueError, match="separate R5"):
        WorkflowWorkModel(bad)


def prior_raw():
    return {
        "kind": PRIOR_KIND, "encoding": "task_structure_v1", "task_encoder": None,
        "center": [0.] * len(TASK_FEATURES), "coefficient": [0.] * len(TASK_FEATURES),
        "bias": 10., "log_total_sigma": .5, "initial_blend": .5, "blend_work": 100.,
        "target": "log1p_complete_logical_model_work",
    }


def test_total_prior_conditions_on_observed_service_and_preserves_finite_normalized_tails():
    import math

    prior = TotalWorkPrior(prior_raw())
    initial = prior.distribution(10., 0., (1000., 10000.))
    conditioned = prior.distribution(10., 20000., (1000., 10000.))
    assert sum(initial) == pytest.approx(1.)
    assert sum(conditioned) == pytest.approx(1.)
    assert initial != conditioned
    for mean, attained, thresholds in (
        (1., 10**20, (10**19, 10**20)), (100., 1., (1000., 10000.)),
    ):
        values = prior.distribution(mean, attained, thresholds)
        assert sum(values) == pytest.approx(1.)
        assert all(math.isfinite(value) and 0 <= value <= 1 for value in values)
    features = [0., 0.]
    mixed = prior.combine((.2, .3, .5), 10., features, (1000., 10000.))
    assert mixed == pytest.approx(tuple(.5 * a + .5 * b for a, b in zip(initial, (.2, .3, .5))))
    # Long observed service hands authority back to the progress model.
    assert prior.combine((.2, .3, .5), 10., [0., 100000.], (1000., 10000.)) == pytest.approx((.2, .3, .5))


def test_initial_task_prior_stops_affecting_classification_after_any_actual_model_service():
    raw = prior_raw()
    raw.update(activation="before_first_model_service", initial_blend=1.)
    prior = TotalWorkPrior(raw)
    initial = prior.combine((.2, .3, .5), 10., [0., 0.], (1000., 10000.))
    assert initial == prior.distribution(10., 0., (1000., 10000.))
    assert prior.combine((.2, .3, .5), 10., [1., 0.], (1000., 10000.)) == (.2, .3, .5)
    assert prior.combine((.2, .3, .5), 10., [0., 1.], (1000., 10000.)) == (.2, .3, .5)


def test_prior_leave_one_workflow_out_error_matches_actual_held_out_refits():
    import numpy as np
    from scripts.train_workflow_work_prior import fit_prior

    x = np.asarray([[1., 0.], [0., 1.], [1., 1.], [2., 1.], [1., 2.]])
    y = np.asarray([100., 200., 300., 500., 900.])
    _prior, search = fit_prior(x, y)
    log_y = np.log1p(y)
    for item in search:
        errors = []
        for index in range(len(x)):
            keep = np.arange(len(x)) != index
            center, bias = x[keep].mean(0), log_y[keep].mean()
            values, target = x[keep] - center, log_y[keep] - bias
            coefficient = values.T @ np.linalg.solve(
                values @ values.T + item["alpha"] * np.eye(sum(keep)), target,
            )
            prediction = bias + (x[index] - center) @ coefficient
            errors.append(log_y[index] - prediction)
        assert item["LOO_log_RMSE"] == pytest.approx(np.mean(np.asarray(errors) ** 2) ** .5)


def test_structured_prior_reaches_real_worker_without_changing_event_or_numeric_inputs():
    from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker
    from tests.test_workflow_work_distribution import root, start

    raw = distribution_raw()
    raw["total_work_prior"] = prior_raw()
    model = WorkflowWorkModel(raw)
    worker = WorkflowWorkWorker("", "", model=model, autostart=False)
    try:
        worker.submit("wf", "event", start())
        worker.submit("wf", "event", root())
        worker.process_pending()
        prediction = worker.prediction("wf")
        assert prediction is not None and prediction.evidence == "model"
        assert sum(prediction.probabilities) == pytest.approx(1.)
        assert worker.counts["task_encodings"] == 1
        with pytest.raises(ValueError, match="cached task prior"):
            model.predict([0.], [0.] * len(model.feature_names))
    finally:
        worker.close()


def test_merged_seed_models_equal_the_geometric_mean_without_new_runtime_interfaces():
    import numpy as np
    from scripts.train_workflow_work_ensemble import merge

    left, right = distribution_raw(), distribution_raw()
    left["metadata"]["seed"], right["metadata"]["seed"] = 21, 7
    left["temperature"], right["temperature"] = .5, 2.
    right["output_bias"] = [1., 2., 3.]
    features = [0.] * len(left["features"])
    individual = []
    for raw in (left, right):
        model = WorkflowWorkModel(raw)
        individual.append(model.predict(model.task_projection("ab"), features))
    expected = np.sqrt(np.prod(individual, axis=0))
    expected /= expected.sum()
    combined = WorkflowWorkModel(merge([left, right]))
    assert combined.predict(combined.task_projection("ab"), features) == pytest.approx(expected)
    right["work_thresholds"] = [1., 100.]
    with pytest.raises(ValueError, match="matching feature"):
        merge([left, right])


def test_cache_lifetime_starts_after_expensive_task_encoding(monkeypatch):
    from beliefkv.runtime import workflow_work_worker as module
    from tests.test_workflow_work_distribution import root, start

    clock = [100.]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    model = WorkflowWorkModel(distribution_raw())
    original = model.task_encoding

    def slow_encoding(text):
        clock[0] += 5.
        return original(text)

    monkeypatch.setattr(model, "task_encoding", slow_encoding)
    worker = module.WorkflowWorkWorker("", "", model=model, autostart=False)
    try:
        worker.submit("wf", "event", start())
        worker.submit("wf", "event", root())
        worker.process_pending()
        prediction = worker.prediction("wf")
        assert prediction is not None
        assert prediction.updated_at == 105.
        assert prediction.expires_at == 108.
        assert worker._next_inference["wf"] == 106.
    finally:
        worker.close()


def test_semantic_preparation_runs_on_worker_thread_and_failure_abstains(monkeypatch):
    import threading
    import time
    from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker

    model = WorkflowWorkModel(distribution_raw())
    observed = threading.Event()
    caller = threading.get_ident()

    def warmup():
        assert threading.get_ident() != caller
        observed.set()
        raise ValueError("changed local encoder weights")

    monkeypatch.setattr(model, "warm_task_encoder", warmup)
    worker = WorkflowWorkWorker("", "", model=model, autostart=True)
    try:
        assert observed.wait(timeout=2.)
        deadline = time.monotonic() + 2.
        while not worker.error and time.monotonic() < deadline:
            time.sleep(.01)
        assert "changed local encoder weights" in worker.error
        assert worker.prediction("wf") is None
    finally:
        worker.close()


def test_delegation_export_exposes_assignment_only_after_spawn_and_removes_returned_child():
    from dataclasses import replace
    from beliefkv.core.events import RuntimeEventKind as Kind
    from beliefkv.predictor.workflow_rank_input import WorkflowRankInput
    from scripts.train_workflow_work_distribution import snapshots
    from tests.test_workflow_work_distribution_training import execution

    events, requests, progress = execution()
    updated = []
    for value in events:
        if value.kind is Kind.INVOCATION_CREATE and value.invocation_id == "child":
            value = replace(value, attributes={
                **value.attributes, "workflow_ranking_input": WorkflowRankInput.from_instructions(
                    delegation_instructions="Verify the gateway retry logic.",
                    agent_role="test-verifier",
                ).to_dict(),
            })
        updated.append(value)
    rows = snapshots(updated, requests, progress=progress, observed_delegations=True)
    first = next(index for index, row in enumerate(rows) if "gateway retry" in row["text"])
    assert all(row["text"] == "ab" for row in rows[:first])
    assert rows[first]["event"] == "invocation_create"
    returned = next(row for row in rows if row["event"] == "return")
    assert returned["text"] == "ab"
    assert all(row["task_text"] == "ab" for row in rows)


def test_worker_reencodes_only_changed_observed_assignments_and_retains_static_model_behavior(monkeypatch):
    from beliefkv.core.events import RelationType, RuntimeEventKind as Kind
    from beliefkv.predictor.workflow_rank_input import WorkflowRankInput
    from beliefkv.runtime import workflow_work_worker as module
    from tests.test_workflow_work import event
    from tests.test_workflow_work_distribution import root, start

    clock = [100.]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    raw = distribution_raw()
    raw["observed_delegation_inputs"] = True
    model = WorkflowWorkModel(raw)
    worker = module.WorkflowWorkWorker("", "", model=model, autostart=False)
    try:
        worker.submit("wf", "event", start())
        worker.submit("wf", "event", root())
        worker.process_pending()
        for when, value in (
            (101., event(
                Kind.INVOCATION_CREATE, invocation_id="child", parent_invocation_id="root",
                relation_type=RelationType.SPAWN, attributes={
                    "workflow_ranking_input": WorkflowRankInput.from_instructions(
                        delegation_instructions="Inspect ab.", agent_role="repository-explorer",
                    ).to_dict(),
                },
            )),
            (102., event(Kind.RETURN, invocation_id="child")),
        ):
            clock[0] = when
            worker.submit("wf", "event", value)
            worker.process_pending()
        assert worker.counts["task_encodings"] == 3
        assert worker._projection_texts["wf"] == "ab"
    finally:
        worker.close()


def test_baseline_predictions_do_not_gain_delegation_inputs_from_the_new_evaluation_export():
    from scripts.train_workflow_work import probabilities

    model = WorkflowWorkModel(distribution_raw())
    row = {
        "workflow": "wf", "text": "a future-looking child assignment ab",
        "task_text": "ab", "features": [0.] * len(model.feature_names),
    }
    actual = probabilities(model, [row])[0]
    assert actual == pytest.approx(model.predict(model.task_projection("ab"), row["features"]))


def test_parent_export_accepts_serialization_types_and_bound_additional_hashes_only(tmp_path):
    import json
    from scripts.train_workflow_work_distribution import bind_parent_dataset

    original = {
        "sources_sha256": {"original": "1" * 64},
        "rows": [{"features": [0., 1.], "text": "task", "remaining_work": 20.}],
    }
    path = tmp_path / "parent.json"
    path.write_text(json.dumps(original))
    derived = {
        "sources_sha256": {"original": "1" * 64, "tokens": "2" * 64},
        "rows": [{
            "features": (0., 1.), "text": "task\nKnown assignment", "task_text": "task",
            "remaining_work": 20.,
        }],
    }
    with pytest.raises(ValueError):
        bind_parent_dataset(deepcopy(derived), path)
    accepted = bind_parent_dataset(deepcopy(derived), path, additional_sources={"tokens": "2" * 64})
    assert accepted["parent_dataset"]["original_fields_except_text_equal"]
    changed = deepcopy(derived)
    changed["rows"][0]["remaining_work"] = 1.
    with pytest.raises(ValueError):
        bind_parent_dataset(changed, path, additional_sources={"tokens": "2" * 64})
    with pytest.raises(ValueError):
        bind_parent_dataset(deepcopy(derived), path, additional_sources={"tokens": "3" * 64})


def test_probability_cache_keeps_distinct_causal_text_versions(monkeypatch):
    import numpy as np
    from scripts.train_workflow_work import probabilities

    raw = distribution_raw()
    raw["observed_delegation_inputs"] = True
    model = WorkflowWorkModel(raw)
    encoded = []

    def projection(text):
        encoded.append(text)
        return np.zeros(1)

    monkeypatch.setattr(model, "task_projection", projection)
    base = {"workflow": "a", "features": [0.] * len(model.feature_names), "task_text": "ab"}
    probabilities(model, [
        {**base, "text": "ab"}, {**base, "text": "ab\nObserved child"}, {**base, "text": "ab"},
    ])
    assert encoded == ["ab", "ab\nObserved child"]


def test_real_training_does_not_replace_early_text_with_the_final_delegation_version():
    from scripts.train_workflow_work import fit
    from tests.test_workflow_work_distribution_training import row

    training = [
        {
            **row("train-" + str(index), snapshot=snapshot),
            "text": "ab" if snapshot == 0 else "ab observed child assignment",
            "task_text": "ab", "total_work": 10. * (index + 1),
            "remaining_work": (10. if snapshot == 0 else 5.) * (index + 1),
        }
        for index in range(6) for snapshot in (0, 1)
    ]
    calibration = [row("cal-" + str(index), remaining=10. * (index + 1)) for index in range(6)]
    raw = fit(
        training, calibration, epochs=2, split_mode="workflow", hidden_width=4,
        vocabulary_size=16, feature_schema=R5_SCHEMA, observed_delegations=True,
    )
    model = WorkflowWorkModel(raw)
    assert model.observed_delegations
    assert raw["metadata"]["task_training_encoding"] == "one_projection_per_observed_text_version_per_epoch"
