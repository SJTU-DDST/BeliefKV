from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from scripts.audit_r5_completion_path import inspect, totals
from scripts import prepare_r5_completion_path as training
from tests.test_workflow_work import event
from tests.test_workflow_work_distribution import start
from tests.test_workflow_shortest_frontier import fixture


def row(candidates, resources, selected, required=()):
    return {
        "monotonic_s": 100., "source": "periodic_decay_resume",
        "candidates": [asdict(value) for value in candidates],
        "resources": asdict(resources),
        "decision": {
            "selected": selected, "reference": selected, "required_reference_members": required,
            "reason": "shortest_work_reference",
        },
    }


def mappings(rows, actual):
    return {
        "current_weight": {value.workflow: value.weight for value in rows},
        "point_model_work": actual,
        "reforecast_point_model_work": actual,
        "true_model_path": actual,
        "true_model_tool_path": actual,
        "predicted_model_tool_path": actual,
    }


def test_audit_counts_feasible_misranking_without_mistaking_all_fit_for_competition():
    rows, resources, costs = fixture()
    old = [replace(value, weight=.1 if value.identity == "short" else .9) for value in rows]
    truth = {value.workflow: value.weight for value in rows}
    result = inspect(row(old, resources, ("long-a", "long-b")), costs, mappings(old, truth))
    assert result["capacity_competition"]
    assert len(result["feasible_true_path_priority_inversions"]) == 2
    assert result["choices"]["true_model_tool_path"]["selected"] == ["short"]
    counts = totals([result], {rows[0].workflow})["counts"]
    assert counts["measured_in_capacity_competition"] == 1
    assert counts["feasible_true_path_priority_inversion_boundaries"] == 1
    all_fit = inspect(
        row(rows, replace(resources, logical_tokens=500), ("short", "long-a", "long-b")),
        costs, mappings(rows, truth),
    )
    assert all_fit["all_fit"] and not all_fit["capacity_competition"]
    assert not all_fit["feasible_true_path_priority_inversions"]


def test_audit_separates_capacity_refusal_and_required_join_from_misranking():
    rows, resources, costs = fixture()
    truth = {value.workflow: value.weight for value in rows}
    required = inspect(
        row(rows, resources, ("long-a", "long-b"), required=("long-a",)),
        costs, mappings(rows, truth),
    )
    assert not required["feasible_true_path_priority_inversions"]
    too_large = [replace(rows[0], logical_tokens=1000), *rows[1:]]
    result = inspect(
        row(too_large, resources, ("long-a", "long-b")), costs, mappings(too_large, truth),
    )
    assert not result["feasible_true_path_priority_inversions"]
    assert rows[0].workflow not in result["feasible_workflows"]
    unknown = inspect(row(rows[:1], resources, ("missing",)), costs, mappings(rows[:1], truth))
    assert not unknown["metadata_complete"]


@pytest.mark.parametrize("invalid", (None, "missing_metadata", "wrong_progress_owner"))
def test_collection_preserves_observed_progress_when_background_is_censored(tmp_path, monkeypatch, invalid):
    def write(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        return path

    def submit(workflow, request):
        return replace(
            event(Kind.LLM_SUBMIT, invocation_id=workflow + "-root", context_id=workflow + "-context",
                  context_epoch=0, attributes={"request_id": request}),
            event_id="submit-" + workflow, workflow_id=workflow, ts_ms=120.,
        )

    started = [
        replace(start(), event_id="start-" + workflow, workflow_id=workflow, ts_ms=100.)
        for workflow in ("measured", "waiting")
        if not (invalid == "missing_metadata" and workflow == "measured")
    ]
    submitted = (submit("measured", "m"), submit("waiting", "w"))
    keys = ("workflow_id", "invocation_id", "context_id", "context_epoch")
    requests = {
        row.attributes["request_id"]: {
            **{key: getattr(row, key) for key in keys},
            "prompt_tokens": 16, "output_tokens": 256, "input_increment": 8,
        } for row in submitted
    }
    oracle = SimpleNamespace(
        requests=requests, graphs={
            row.workflow_id: [{"event_id": None, "request_id": row.attributes["request_id"], "node_id": "call"}]
            for row in submitted
        },
        validate_source=lambda *_: None, model_seconds_per_work_token=1.,
    )
    sample = {key: requests["w"][key] for key in keys}
    if invalid == "wrong_progress_owner":
        sample["context_epoch"] = 1
    records = {
        "replay/runtime_events.replay.jsonl": [row.to_dict() for row in (*started, *submitted)],
        "replay/native_requests.jsonl": [{
            "request_id": "m", "exact_tokens_verified": True, "prompt_tokens": 16, "output_tokens": 256,
            "native_complete_monotonic_ms": 500.,
        }],
        "server/runtime_audit.jsonl": [{
            "event": "gpu_service_sample", "phase": "prefill", "complete_monotonic_ms": 200.,
            "output_token_accounting": "observed_output_ids_delta_all_forward_modes_v1",
            "request_samples": [{**sample, "request_id": "w", "output_tokens_after": 140}],
        }],
        "opportunities/admission_opportunities.jsonl": [{
            "event": "workflow_combination_selection", "candidates": [{"workflow": "waiting"}],
            "source": "periodic_decay_resume", "monotonic_s": .25,
            **dict.fromkeys(("decision", "resources", "predictions", "keys"), {}),
        }],
    }
    for name, rows in records.items():
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("".join(json.dumps(record) + "\n" for record in rows))
    certificate = write("certificate.json", {"never_submitted_background": ["future"]})
    cohort = write("cohort.json", {"measured_workflow_ids": ["measured"]})
    base = write("model.json", {})
    config = write("config.json", {"common_environment": {"WORKFLOW_WORK_MODEL": str(base)}})
    registration = write("registration.json", {
        "experiment_configuration": str(config), "source_arm": str(tmp_path),
        "workflow_ids": ["measured", "waiting", "future"],
    })
    monkeypatch.setattr(training, "verify_execution", lambda *_: {
        "protocol": "all_fixed_measured_workflows_completed",
        "certificate": str(certificate), "completed_receipt_requests": ["m"],
        "completed_workflow_ids": ["measured"], "measured_requests": 1, "measured_output_tokens": 256,
    })
    monkeypatch.setattr(training, "source_plan", lambda _: (None, None, None))
    monkeypatch.setattr(training.WorkflowWorkOracleArtifact, "load", lambda *_: oracle)
    monkeypatch.setattr(training, "replay_arm", lambda _: tmp_path)
    monkeypatch.setattr(
        training.Labels, "at", lambda self, workflow, _: (256 - sum(self.active[workflow].values()),) * 3,
    )
    if invalid:
        with pytest.raises(ValueError, match="metadata" if invalid == "missing_metadata" else "owner"):
            training.collect(registration, cohort, write("oracle.json", {}), tmp_path / "analysis.json")
        return
    dataset = training.collect(registration, cohort, write("oracle.json", {}), tmp_path / "analysis.json")
    assert dataset["decisions"][0]["states"]["waiting"]["path"][0] == 116
    assert set(dataset["task_text"]) == {"measured", "waiting"}
    assert dataset["workload_observation"]["never_submitted_background"] == ["future"]
    assert dataset["workload_observation"]["submitted_requests_without_complete_receipt"] == 1
    assert dataset["workload_observation"]["incomplete_requests_with_native_progress"] == 1
