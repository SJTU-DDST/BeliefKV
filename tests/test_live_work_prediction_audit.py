from copy import deepcopy
from dataclasses import replace

import pytest

from beliefkv.core.events import RelationType, RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel
from scripts.audit_live_work_prediction import (
    audit, competitions, observation_timeline, rank_summary,
)
from tests.test_workflow_work_distribution import distribution_raw


def event(kind, *, workflow_id, **kwargs):
    identity = "event-" + workflow_id + "-" + kind.value + "-" + kwargs.get("invocation_id", "")
    return RuntimeEvent(identity, 0., kind, workflow_id, **kwargs)


def metadata(owner):
    return {
        "request_id": owner + "-req", "root_workflow_id": owner,
        "invocation_id": owner + "-root", "context_id": owner + "-context",
        "context_epoch": 0, "attempt_id": 0, "session_id": owner + "-session",
        "session_generation": 1,
    }


def fixture():
    from beliefkv.predictor.workflow_rank_input import WorkflowRankInput

    events, requests = [], {}
    for owner, output in (("a", 256), ("b", 220)):
        key = metadata(owner)
        common = {"workflow_id": owner, "invocation_id": key["invocation_id"], "context_id": key["context_id"]}
        values = [
            event(Kind.WORKFLOW_START, attributes={"workflow_ranking_input":
                WorkflowRankInput.from_instructions(task_instructions="ab").to_dict()}, **common),
            event(Kind.INVOCATION_CREATE, relation_type=RelationType.ROOT, **common),
            event(Kind.LLM_SUBMIT, attributes={"request_id": key["request_id"]}, **common),
            event(Kind.LLM_RESULT, attributes={"request_id": key["request_id"]}, **common),
        ]
        for value, when in zip(values, (0., 1., 2000., 4000. if owner == "a" else 7000.)):
            events.append(replace(value, ts_ms=when).to_dict())
        requests[key["request_id"]] = {
            **common, "input_increment": 2, "prompt_tokens": 2, "output_tokens": output,
        }
    events.append(replace(event(
        Kind.INVOCATION_CREATE, workflow_id="a", invocation_id="child", context_id="child-context",
        parent_invocation_id="a-root", relation_type=RelationType.SPAWN,
    ), ts_ms=5000.).to_dict())
    native = [{
        "complete_monotonic_ms": 3500., "monotonic_clock_domain": "domain", "phase": "decode",
        "request_samples": [{
            "request_id": "a-req", "workflow_id": "a", "invocation_id": "a-root", "context_id": "a-context",
            "token_delta_semantics": "observed_output_ids_delta", "output_tokens_before": 0,
            "token_delta": 128,
        }],
    }]
    decisions = [
        {"monotonic_s": now, "candidates": [{
            "key": metadata(owner), "protected_JOIN": False,
            "recorded_remaining_path_s": 100. if owner == "a" else 200.,
        } for owner in ("a", "b")]} for now in (3.6, 6.)
    ]
    return events, requests, native, decisions


def test_features_use_published_prefix_and_future_work_is_only_a_label():
    events, requests, native, decisions = fixture()
    models = {"model": WorkflowWorkModel(distribution_raw())}
    values = audit(
        deepcopy(decisions), observation_timeline(events, native, requests),
        requests, {"a": 300., "b": 400.}, models,
    )
    first = values[0]["candidates"][0]
    second = values[1]["candidates"][0]
    assert first["features"][R5_FEATURES.index("children")] == 0.
    assert second["features"][R5_FEATURES.index("children")] == 1.
    assert first["features"][R5_FEATURES.index("output_tokens")] == 128.
    assert first["observed_remaining_model_work"] == pytest.approx(171.98)
    changed = audit(
        deepcopy(decisions), observation_timeline(events, native, requests),
        requests, {"a": 3000., "b": 4000.}, models,
    )
    assert changed[0]["candidates"][0]["predictions"] == first["predictions"]
    assert changed[0]["candidates"][0]["features"] == first["features"]


def test_logical_competition_requires_actual_ready_window_and_exact_selection_identity():
    programs = [{
        "key": metadata(owner), "invocation_id": owner + "-root", "application_ready": True,
        "pending_before": True, "active_before": False, "marked_before": False,
        "resume_window_eligible": True, "complete_budget_tokens": 60,
    } for owner in ("a", "b")]
    control = {
        "event": "agent_periodic_admission_decision", "monotonic_s": 3., "resume_capacity_tokens": 100,
        "programs": programs,
    }
    choice = {
        "event": "workflow_size_resume_selection", "monotonic_s": 3.,
        "rank_before_category": True,
        "rank_kind": "reference_category_context_with_matched_oracle_bookkeeping",
        "ordered": [[owner + "-root", 1, 1.] for owner in ("a", "b")],
        "candidates": [{
            "key": metadata(owner), "prediction": {
                "evidence": "oracle", "remaining_path_s": 100., "remaining_work": 200.,
            },
        } for owner in ("a", "b")],
    }
    result, count = competitions([control, choice])
    assert count == 1 and len(result) == 1
    control["programs"][0]["resume_window_eligible"] = False
    assert competitions([control, choice])[0] == []
    control["programs"][0]["resume_window_eligible"] = True
    bad = deepcopy(choice)
    bad["candidates"][0]["key"]["attempt_id"] = 99
    changed = competitions([control, bad])[0]
    assert changed[0]["candidates"][0]["unavailable_reason"] == "request_identity_changed_within_boundary"
    assert not changed[0]["candidates"][0]["selector_metadata_valid"]


def test_reasoning_category_zero_is_not_protected_join_rank_zero():
    programs = [{
        "key": metadata(owner), "invocation_id": owner + "-root", "application_ready": True,
        "pending_before": True, "active_before": False, "marked_before": False,
        "resume_window_eligible": True, "complete_budget_tokens": 60,
    } for owner in ("a", "b")]
    control = {
        "event": "agent_periodic_admission_decision", "monotonic_s": 3.,
        "resume_capacity_tokens": 100, "programs": programs,
    }
    choice = {
        "event": "workflow_size_resume_selection", "monotonic_s": 3.,
        "rank_before_category": True,
        "rank_kind": "reference_category_context_with_matched_oracle_bookkeeping",
        "ordered": [["a-root", 0, 1.], ["b-root", 1, 0.]],
        "candidates": [{
            "key": metadata(owner), "prediction": {
                "evidence": "oracle", "remaining_path_s": 100., "remaining_work": 200.,
            },
        } for owner in ("a", "b")],
    }
    values, _ = competitions([control, choice])
    assert not values[0]["candidates"][0]["protected_JOIN"]
    assert values[0]["candidates"][1]["protected_JOIN"]
    choice["rank_before_category"] = False
    with pytest.raises(ValueError, match="global rank"):
        competitions([control, choice])


@pytest.mark.parametrize("entry", ("rescore_workflow_predictions", "rescore_live_work_prediction"))
def test_rescoring_refuses_the_legacy_protected_join_population(tmp_path, monkeypatch, entry):
    import importlib
    import json
    import sys
    from beliefkv.experiments.source_provenance import sha256_file

    main = importlib.import_module("scripts." + entry).main

    dataset, cohort, model, old = (tmp_path / (name + ".json")
                                   for name in ("dataset", "cohort", "model", "old"))
    for path in (dataset, cohort, model):
        path.write_text("{}")
    old.write_text(json.dumps({
        "inputs_sha256": {str(path): sha256_file(path) for path in (dataset, cohort)},
        "scope": {
            "protected_JOIN_pairs_excluded_from_predictor_rank_quality": True,
            "client_published_events_and_observed_native_progress_only_for_features": True,
        },
    }))
    monkeypatch.setattr(sys, "argv", [
        "rescore", "--frozen-audit" if entry == "rescore_workflow_predictions" else "--sealed-audit",
        str(old), "--dataset", str(dataset),
        "--cohort", str(cohort), "--model", str(model), "--output", str(tmp_path / "out.json"),
    ])
    with pytest.raises(ValueError, match="causal"):
        main()


def test_rank_metrics_exclude_protected_join_and_keep_held_out_pairs_separate():
    candidates = [{
        "key": metadata(owner), "protected_JOIN": owner == "c",
        "observed_remaining_model_work": model_work, "recorded_remaining_path_s": path,
        "predictions": {"model": {"effective_weight": score}},
    } for owner, model_work, path, score in (
        ("a", 100., 300., .8), ("b", 200., 200., .2), ("c", 10., 10., .9),
    )]
    result = rank_summary([{"candidates": candidates}], "model", measured={"a", "b"}, held_out_only=True)
    assert result["counts"]["evaluated_pair_observations"] == 1
    assert result["metrics"]["model"]["boundary_equal_weight_accuracy"] == 1.
    assert result["metrics"]["model_tool_path"]["boundary_equal_weight_accuracy"] == 0.
    all_values = rank_summary([{"candidates": candidates}], "model", measured={"a", "b"})
    assert all_values["counts"]["excluded_protected_JOIN_pairs"] == 2


def test_foreign_native_ownership_cannot_change_the_predictor_input():
    events, requests, native, _decisions = fixture()
    native[0]["request_samples"][0]["workflow_id"] = "foreign"
    with pytest.raises(ValueError, match="ownership"):
        observation_timeline(events, native, requests)


def test_overlap_progress_uses_reported_cumulative_output_instead_of_launch_ordinal():
    events, requests, native, _decisions = fixture()
    sample = native[0]["request_samples"][0]
    sample.update(
        output_tokens_before=126, output_tokens_reported_before=127,
        output_tokens_after=128, token_delta=1,
    )
    timeline = observation_timeline(events, native, requests)
    assert [item for _when, _priority, _index, item in timeline if isinstance(item, tuple)] == [
        ("a-req", 128),
    ]
    sample["output_tokens_after"] = 129
    with pytest.raises(ValueError, match="cumulative native output"):
        observation_timeline(events, native, requests)


def test_missing_selector_metadata_is_unknown_rather_than_a_prediction_error():
    values = [{
        "candidates": [
            {"key": metadata("a"), "selector_metadata_valid": False,
             "unavailable_reason": "not_in_recorded_resume_selection"},
            {"key": metadata("b"), "selector_metadata_valid": True,
             "protected_JOIN": False},
        ],
    }]
    result = rank_summary(values, "model", measured={"a", "b"})
    assert result["counts"]["missing_exact_selector_metadata_pairs"] == 1
    assert result["metrics"]["model"]["boundary_equal_weight_accuracy"] is None


def test_rescoring_reuses_only_sealed_features_and_initial_task_text():
    from scripts.rescore_live_work_prediction import rescore

    _events, _requests, _native, decisions = fixture()
    for decision in decisions:
        for candidate in decision["candidates"]:
            candidate["features"] = [0.] * len(R5_FEATURES)
            candidate["observed_remaining_model_work"] = 12345.
    model = WorkflowWorkModel(distribution_raw())
    values = rescore(decisions, {"a": "ab", "b": "ab"}, {"new": model})
    first = values[0]["candidates"][0]
    changed = deepcopy(decisions)
    changed[0]["candidates"][0]["observed_remaining_model_work"] = 999999.
    other = rescore(changed, {"a": "ab", "b": "ab"}, {"new": model})
    assert other[0]["candidates"][0]["predictions"] == first["predictions"]
    assert "predictions" not in decisions[0]["candidates"][0]
    assert first["features"] == decisions[0]["candidates"][0]["features"]
