from copy import deepcopy
import json
import sys

import pytest

from beliefkv.predictor.workflow_work import R5_FEATURES, R5_SCHEMA, WorkflowWorkModel
from beliefkv.experiments.source_provenance import sha256_file
from scripts import train_workflow_task_diversity as study
from scripts.train_workflow_task_diversity import (
    CANDIDATES, candidate_model, choose_candidate, initial_training_rows, training_population,
)
from scripts.train_workflow_work import capture_split
from scripts.train_workflow_work_distribution import split_rows
from tests.test_workflow_work_distribution import distribution_raw
from tests.test_workflow_work_distribution_training import row


def populations():
    dataset = {
        "schema": R5_SCHEMA, "prefill_token_weight": .01,
        "progress_observation_contract": "observed_native_decode_progress_128_v1",
        "rows": [row(str(index)) for index in range(16)],
    }
    cohort = {"measured_workflow_ids": ["0"]}
    training, calibration, development, original_split = split_rows(dataset["rows"], ["0"])
    previous = {
        **deepcopy(dataset), "rows": [row("previous")],
        "training_extension": {"original_dataset_sha256": "f" * 64},
    }
    expanded_split = capture_split(
        training + previous["rows"], calibration, development, require_capture_disjoint=False,
    )
    previous["training_extension"]["expanded_split"] = expanded_split
    additional = {
        **deepcopy(dataset),
        "rows": [{**row("new-task"), "project": training[0]["project"]}],
    }
    evaluation = {"original_split": original_split, "expanded_split": expanded_split}
    return dataset, cohort, previous, additional, evaluation


def test_new_task_study_preserves_both_holdout_groups_and_independent_project_count():
    values = populations()
    dataset, cohort, _previous, _additional, _evaluation = values
    before = deepcopy(values)
    old_training, old_calibration, old_development, _ = split_rows(
        dataset["rows"], cohort["measured_workflow_ids"],
    )
    training, calibration, development, contract = training_population(*values, "f" * 64)
    assert values == before
    assert calibration == old_calibration and development == old_development
    assert training[:len(old_training)] == old_training
    assert contract["previous_training_executions"] == 4
    assert contract["expanded_training_executions"] == 5
    assert contract["training_projects"] == 4 and contract["new_independent_projects"] == 0


@pytest.mark.parametrize("change", ("heldout", "source", "previous_split", "features", "duplicate"))
def test_new_task_study_rejects_leakage_or_changed_source_contract(change):
    dataset, cohort, previous, additional, evaluation = populations()
    if change == "heldout":
        additional["rows"][0]["project"] = "0"
    elif change == "source":
        previous["training_extension"]["original_dataset_sha256"] = "e" * 64
    elif change == "previous_split":
        evaluation["expanded_split"] = {}
    elif change == "features":
        additional["progress_observation_contract"] = "different_progress_clock"
    else:
        duplicate = {**additional["rows"][0], "workflow": "other-new-task"}
        additional["rows"].append(duplicate)
    with pytest.raises(ValueError):
        training_population(dataset, cohort, previous, additional, evaluation, "f" * 64)


def component(logits):
    value = distribution_raw()
    value.update(
        admission_cost_profile={"test_preserved_policy": True},
        free_dispatch_coalescing_window_ms=2000,
        output_bias=logits,
    )
    value["metadata"].update(
        progress_observation_contract="observed_native_decode_progress_128_v1",
        training_dataset_sha256="f" * 64,
    )
    return value


def predict(raw, *, serviced=False, child=False):
    model = WorkflowWorkModel(raw)
    features = [0.] * len(R5_FEATURES)
    if serviced:
        features[R5_FEATURES.index("output_tokens")] = 128.
    if child:
        features[R5_FEATURES.index("children")] = 1.
    return model.predict(model.task_projection("ab"), features)


def test_component_updates_keep_the_other_branch_probabilities_and_serving_policy_exact():
    original = component([5., 0., 0.])
    old_initial = component([0., 5., 0.])
    original["initial_state_head"] = {
        "kind": "before_observed_work_and_children_v1", "blend": 1., "model": old_initial,
    }
    progress, initial = component([0., 0., 5.]), component([3., 2., 1.])
    before = deepcopy((original, progress, initial))
    for name in CANDIDATES:
        raw = candidate_model(name, original, progress, initial)
        initial_reference = initial if name in ("new_initial", "new_progress_and_initial") else original
        progress_reference = progress if name in ("new_progress", "new_progress_and_initial") else original
        assert predict(raw) == predict(initial_reference)
        assert predict(raw, serviced=True) == predict(progress_reference, serviced=True)
        assert predict(raw, child=True) == predict(progress_reference, child=True)
        assert raw["admission_cost_profile"] == original["admission_cost_profile"]
        assert raw["free_dispatch_coalescing_window_ms"] == 2000
    assert (original, progress, initial) == before


def test_selection_retains_control_on_ties_and_never_uses_an_unsupported_candidate():
    summaries = {name: {"decision_eligible": True, "selection_score": .8} for name in CANDIDATES}
    assert choose_candidate(summaries) == "retained"
    summaries["new_progress"].update(decision_eligible=False, selection_score=1.)
    summaries["new_initial"]["selection_score"] = .81
    assert choose_candidate(summaries) == "new_initial"
    for value in summaries.values():
        value["decision_eligible"] = False
    with pytest.raises(ValueError, match="support"):
        choose_candidate(summaries)


def test_untrained_or_nested_components_are_rejected():
    baseline = component([5., 0., 0.])
    with pytest.raises(ValueError, match="not been trained"):
        candidate_model("new_progress", baseline)
    nested = candidate_model("new_initial", baseline, initial=baseline)
    with pytest.raises(ValueError, match="nested"):
        candidate_model("new_initial", baseline, initial=nested)


def test_conditional_initial_training_uses_observed_activation_state_and_requires_all_tasks():
    initial = {**row("a"), "attained_work": 0.}
    served = {**row("a", snapshot=1), "attained_work": 1.}
    served["features"][R5_FEATURES.index("output_tokens")] = 1.
    child = {**row("a", snapshot=2), "attained_work": 0.}
    child["features"][R5_FEATURES.index("children")] = 1.
    assert initial_training_rows([initial, served, child]) == [initial]
    with pytest.raises(ValueError, match="every complete task"):
        initial_training_rows([initial, {**served, "workflow": "unseen-initial"}])
    with pytest.raises(ValueError, match="unserved"):
        initial_training_rows([{**initial, "attained_work": 1.}])


@pytest.mark.parametrize("family", ("whole_task", "conditional_initial"))
def test_actual_training_components_are_loadable_and_selection_precedes_development(tmp_path, monkeypatch, family):
    dataset, cohort, previous, additional, evaluation = populations()
    sources = tmp_path / "source_fixture.json"
    sources.write_text("{}")

    def complete_rows(identity, project, total):
        result = []
        for snapshot in range(2):
            value = {
                **row(identity, snapshot=snapshot, remaining=total - snapshot),
                "project": project, "total_work": total, "attained_work": float(snapshot),
                "event": "workflow_start" if not snapshot else "llm_submit",
            }
            value["features"][R5_FEATURES.index("ready")] = float(snapshot)
            value["features"][R5_FEATURES.index("output_tokens")] = float(snapshot)
            result.append(value)
        return result

    dataset["rows"] = [
        value for index in range(16)
        for value in complete_rows(str(index), str(index), (5., 15., 30.)[index % 3])
    ]
    training, calibration, development, original_split = split_rows(dataset["rows"], ["0"])
    previous["rows"] = complete_rows("previous", "previous", 20.)
    additional["rows"] = complete_rows("new-task", training[0]["project"], 10.)
    evaluation.update(
        original_split=original_split,
        expanded_split=capture_split(
            training + previous["rows"], calibration, development, require_capture_disjoint=False,
        ),
    )
    previous["training_extension"]["expanded_split"] = evaluation["expanded_split"]
    for value in (dataset, previous, additional):
        value["sources_sha256"] = {str(sources): sha256_file(sources)}
    paths = {}
    for name, value in (("dataset", dataset), ("cohort", cohort), ("baseline-model", component([5., 0., 0.]))):
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_text(json.dumps(value))
    previous["training_extension"]["original_dataset_sha256"] = sha256_file(paths["dataset"])
    evaluation.update(
        model_sha256=sha256_file(paths["baseline-model"]),
        inputs_sha256={str(paths["dataset"]): sha256_file(paths["dataset"])},
    )
    for name, value in (
        ("previous-training-data", previous), ("additional-training-data", additional),
        ("baseline-evaluation", evaluation),
    ):
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_text(json.dumps(value))
    output = tmp_path / "study"
    real_fit, real_metrics = study.fit, study.metrics

    def small_fit(*args, **kwargs):
        kwargs.update(epochs=2, hidden_width=4, vocabulary_size=4)
        return real_fit(*args, **kwargs)

    def ordered_metrics(model, rows):
        if {value["logical_workflow_id"] for value in rows} == {"0"}:
            assert (output / "selection.json").is_file()
        return real_metrics(model, rows)

    monkeypatch.setattr(study, "fit", small_fit)
    monkeypatch.setattr(study, "metrics", ordered_metrics)
    # Component plumbing is exercised here; separate tests cover production support gates.
    monkeypatch.setattr(study, "calibration_rejections", lambda values, raw: [])
    monkeypatch.setattr(sys, "argv", [
        "train_workflow_task_diversity.py",
        *(item for key, value in paths.items() for item in ("--" + key, str(value))),
        "--output-dir", str(output), "--family", family,
    ])
    study.main()
    result = json.loads((output / "evaluation.json").read_text())
    assert result["expanded_training_executions"] == 5
    assert result["training_projects"] == 4
    assert set(result["held_out"]) == {"retained", result["selected"]}
    assert result["candidate_selection_used_test_labels"] is False
