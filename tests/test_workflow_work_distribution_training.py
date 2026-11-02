from dataclasses import replace
import hashlib

import pytest

from beliefkv.core.events import RelationType, RuntimeEventKind as Kind
from beliefkv.predictor.workflow_work import R5_FEATURES, R5_SCHEMA, WorkflowWorkModel
from scripts.train_workflow_work import fit
from scripts.train_workflow_work_distribution import distribution_metrics, label_sources, snapshots, split_rows
from tests.test_workflow_work import event
from tests.test_workflow_work_distribution import distribution_raw, root, start


def execution():
    def boundary(kind, ts, request, invocation, context):
        return replace(event(
            kind, invocation_id=invocation, context_id=context, attributes={
                "request_id": request, "expected_output_tokens": 10**9, "remaining_work": 0,
            },
        ), ts_ms=ts)

    events = [
        start(), replace(root(), ts_ms=1.),
        boundary(Kind.LLM_SUBMIT, 2., "r", "root", "c"),
        replace(event(
            Kind.INVOCATION_CREATE, invocation_id="child", parent_invocation_id="root",
            relation_type=RelationType.SPAWN, agent_definition_id="test-verifier",
        ), ts_ms=3.),
        boundary(Kind.LLM_RESULT, 6., "r", "root", "c"),
        boundary(Kind.LLM_SUBMIT, 7., "s", "child", "d"),
        boundary(Kind.LLM_RESULT, 8., "s", "child", "d"),
        replace(event(Kind.RETURN, invocation_id="child"), ts_ms=9.),
        replace(event(Kind.RETURN, invocation_id="root"), ts_ms=10.),
        replace(event(Kind.WORKFLOW_END), ts_ms=11.),
    ]
    requests = {
        request: {
            "workflow_id": "wf", "invocation_id": invocation, "context_id": context,
            "prompt_tokens": count, "input_increment": count, "output_tokens": output,
        }
        for request, invocation, context, count, output in (
            ("r", "root", "c", 3, 2), ("s", "child", "d", 4, 4),
        )
    }
    progress = [{"request_id": "r", "ts_ms": 4., "output_tokens": 1}]
    return events, requests, progress


def test_complete_work_labels_use_future_calls_only_as_labels_and_charge_observed_service_once():
    events, requests, progress = execution()
    rows = snapshots(events, requests, progress=progress)
    assert {row["total_work"] for row in rows} == {6.07}
    assert rows[0]["attained_work"] == 0.
    initial = dict(zip(R5_FEATURES, rows[0]["features"]))
    assert initial["children"] == initial["output_tokens"] == initial["input_tokens"] == 0.
    streamed = next(row for row in rows if row["event"] == "native_decode_progress")
    assert streamed["attained_work"] == pytest.approx(1.03)
    assert streamed["remaining_work"] == pytest.approx(5.04)
    assert rows[-1]["remaining_work"] == 0.
    final = dict(zip(R5_FEATURES, rows[-1]["features"]))
    assert final["root_input_tokens"] == 3.
    assert final["child_input_tokens"] == 4.
    assert final["root_output_tokens"] == 2.
    assert final["child_output_tokens"] == 4.
    assert final["role_verify"] == 1.
    assert {row["text"] for row in rows} == {"ab"}
    changed = [
        replace(value, attributes={
            **value.attributes, "future_children": 999, "remaining_work": 10**12,
        }) for value in events
    ]
    assert snapshots(changed, requests, progress=progress) == rows


@pytest.mark.parametrize("change", ("missing", "ownership", "outside_lifetime", "exceeds_label"))
def test_export_rejects_missing_or_mismatched_source_work(change):
    events, requests, progress = execution()
    if change == "missing":
        events = [value for value in events if not (
            value.kind is Kind.LLM_RESULT and value.attributes["request_id"] == "s"
        )]
    elif change == "ownership":
        requests["r"]["invocation_id"] = "other"
    elif change == "outside_lifetime":
        progress[0]["ts_ms"] = 1.
    else:
        progress[0]["output_tokens"] = 100.
    with pytest.raises(ValueError):
        snapshots(events, requests, progress=progress)


def row(identity, snapshot=0, remaining=20.):
    return {
        "workflow": identity, "logical_workflow_id": identity,
        "source_capture": "/capture/" + identity,
        "source_workflow_sha256": hashlib.sha256(identity.encode()).hexdigest(),
        "project": identity, "task_template": "task-" + identity,
        "text": "ab", "features": [0.] * len(R5_FEATURES),
        "snapshot": snapshot, "remaining_work": remaining, "total_work": 100.,
    }


def test_split_keeps_every_snapshot_of_the_fixed_measurement_workflow_held_out():
    rows = [row(str(index), snapshot) for index in range(8) for snapshot in range(3)]
    training, calibration, test, contract = split_rows(rows, ["0", "1"], calibration_workflows=2)
    assert {value["workflow"] for value in test} == {"0", "1"}
    assert (len(training), len(calibration), len(test)) == (12, 6, 6)
    assert not {value["workflow"] for value in training} & {value["workflow"] for value in calibration}
    assert contract["complete_workflows_disjoint"]
    assert split_rows(rows, ["0", "1"], calibration_workflows=2) == (
        training, calibration, test, contract,
    )
    with pytest.raises(ValueError, match="duplicate"):
        split_rows(rows, ["0", "0"], calibration_workflows=2)
    with pytest.raises(ValueError, match="executions overlap"):
        split_rows([
            {**value, "source_workflow_sha256": "1" * 64} for value in rows
        ], ["0", "1"], calibration_workflows=2)


def test_distribution_quality_uses_actual_support_without_the_legacy_point_six_threshold():
    import math

    raw = distribution_raw()
    raw["output_bias"] = [math.log(.4), math.log(.35), math.log(.25)]
    model = WorkflowWorkModel(raw)
    metrics = distribution_metrics(model, [row("a"), row("a", 1), row("b")])
    assert metrics["supported_distribution_coverage"] == pytest.approx(1.)
    assert metrics["long_to_short_rate"] == pytest.approx(1.)
    assert metrics["true_long_supported_coverage"] == pytest.approx(1.)
    assert metrics["mean_effective_probability_weight"] == pytest.approx(.6375)
    raw["metadata"]["decision_eligible"] = False
    rejected = distribution_metrics(WorkflowWorkModel(raw), [row("a"), row("b")])
    assert rejected["supported_distribution_coverage"] == 0.
    assert rejected["mean_effective_probability_weight"] == .5
    assert rejected["support_reason_equal_workflow_mass"] == {"calibration_not_eligible": 1.}


def test_progress_slices_without_initial_snapshots_report_undefined_initial_metrics_as_json_null():
    import json

    metrics = distribution_metrics(WorkflowWorkModel(distribution_raw()), [row("a", snapshot=10)])
    assert metrics["initial_accuracy"] is None
    assert metrics["initial_supported_distribution_coverage"] is None
    json.dumps(metrics, allow_nan=False)


def test_small_real_training_emits_a_separate_r5_contract_and_fixed_training_thresholds():
    training = [
        {**row(str(index)), "total_work": 10. * (index + 1), "remaining_work": 5. * (index + 1)}
        for index in range(6)
    ]
    calibration = [row("cal-" + str(index), remaining=10. * (index + 1)) for index in range(6)]
    raw = fit(
        training, calibration, epochs=2, split_mode="workflow",
        hidden_width=4, vocabulary_size=4, feature_schema=R5_SCHEMA,
    )
    model = WorkflowWorkModel(raw)
    assert model.schema == R5_SCHEMA
    assert model.thresholds == pytest.approx((26.6666666667, 43.3333333333))
    assert model.distribution_contract["minimum_refresh_s"] == 1.
    assert raw["metadata"]["task_training_encoding"] == "one_static_projection_per_workflow_per_epoch"
    assert raw["metadata"]["calibration_class_workflows"] == [2, 2, 2]


def test_label_provenance_rechecks_the_exact_token_and_overlay_bytes_before_export(tmp_path):
    import json

    tokens, tools = tmp_path / "tokens.json", tmp_path / "tools.json"
    tokens.write_text(json.dumps({"captured_logical_request": [1, 2, 3]}))
    tools.write_text("{}")
    binding = {
        "token_path": str(tokens), "token_sha256": hashlib.sha256(tokens.read_bytes()).hexdigest(),
        "tool_overlay_path": str(tools), "tool_overlay_sha256": hashlib.sha256(tools.read_bytes()).hexdigest(),
    }
    assert len(label_sources(binding)) == 2
    tools.write_text('{"changed": true}')
    with pytest.raises(ValueError, match="label source changed"):
        label_sources(binding)
