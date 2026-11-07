from copy import deepcopy
from dataclasses import replace
import json

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from scripts.audit_cohort_jct_paths import audit_arm, compare_paths, measured_plan, request_details
from scripts.audit_causal_jct_paths import account
from test_causal_jct_paths import fixture, launch_fixture


def result():
    plan, events, rows, launches = fixture()
    observed = account(plan, events, rows, launches, expected_jct_ms={"w": 41.})
    requests = {row["request_id"]: row for row in rows}
    observed["requests"] = request_details(events, requests, launches, {})
    observed["cohort"] = {"measured_workflow_ids": ["w"]}
    observed["workflows"]["w"]["workflow_to_first_native_launch_s"] = .002
    return observed


def test_whole_measured_graph_omits_background_without_dropping_dependencies():
    plan, _, _, _ = fixture()
    background = tuple(replace(node, node_id="bg-" + node.node_id, workflow_id="bg") for node in plan.nodes)
    selected = measured_plan(replace(plan, nodes=plan.nodes + background), ["w"])
    assert selected == plan
    assert selected.native_completion_boundaries_verified


@pytest.mark.parametrize("workflows", ([], ["w", "w"], ["unknown"], [None]))
def test_measurement_population_must_be_distinct_and_complete(workflows):
    plan, _, _, _ = fixture()
    with pytest.raises(ValueError):
        measured_plan(plan, workflows)


@pytest.mark.parametrize("change", ("missing_end", "missing_dependency", "cross_dependency"))
def test_partial_or_cross_workflow_graphs_are_rejected(change):
    plan, _, _, _ = fixture()
    if change == "missing_end":
        nodes = tuple(node for node in plan.nodes if not node.event or node.event.kind is not Kind.WORKFLOW_END)
    elif change == "missing_dependency":
        nodes = tuple(node for node in plan.nodes if node.node_id != "request-b")
    else:
        nodes = tuple(
            replace(node, dependencies=("background",)) if node.node_id == "submit-a" else node
            for node in plan.nodes
        )
    with pytest.raises(ValueError):
        measured_plan(replace(plan, nodes=nodes), ["w"])


def test_same_completed_workflows_compare_exactly_with_zero_difference():
    before = result()
    compared = compare_paths(before, deepcopy(before))
    assert compared["right_minus_left_mean_jct_s"] == 0.
    assert compared["workflows_by_descending_jct_delta"][0]["right_minus_left_jct_s"] == 0.


def test_path_differences_reconstruct_jct_without_summing_parallel_waits():
    before, after = result(), result()
    after["mean_jct_s"] += .010
    after["workflows"]["w"]["jct_ms"] += 10.
    after["workflows"]["w"]["components_ms"]["request_submit_to_first_launch"] += 10.
    compared = compare_paths(before, after)
    assert compared["right_minus_left_mean_jct_s"] == pytest.approx(.010)
    assert compared["right_minus_left_mean_components_s"]["request_submit_to_first_launch"] == .010


@pytest.mark.parametrize("change", ("cohort", "requests", "identity", "work", "jct"))
def test_changed_cohorts_work_identity_or_unreconciled_paths_are_rejected(change):
    before, after = result(), result()
    if change == "cohort":
        after["cohort"]["measured_workflow_ids"] = ["other"]
    elif change == "requests":
        del after["requests"]["a"]
    elif change == "identity":
        after["requests"]["a"]["context_id"] = "foreign"
    elif change == "work":
        after["requests"]["a"]["output_tokens"] = 2
    else:
        after["workflows"]["w"]["jct_ms"] += 10.
    with pytest.raises(ValueError):
        compare_paths(before, after)


@pytest.mark.parametrize("missing", (None, "measured_end", "measured_receipt", "service_sample"))
def test_complete_cohort_accounting_preserves_partial_background_and_rejects_measured_gaps(
    tmp_path, missing,
):
    plan, events, rows, _ = fixture()
    plan = replace(plan, nodes=tuple(
        replace(node, event=replace(node.event, join_id="join-w"))
        if node.event and node.event.kind is Kind.JOIN_SATISFIED else node
        for node in plan.nodes
    ))
    events[3]["join_id"] = "join-w"
    samples, _, _ = launch_fixture()
    events.append({"workflow_id": "unfinished-background", "kind": "workflow_start"})
    rows.append({
        "request_id": "background-request",
        "native_receipt": {"identity": ["unfinished-background", "bg", "bg", 0]},
    })
    if missing == "measured_end":
        events = [row for row in events if row.get("event_id") != "end"]
    elif missing == "measured_receipt":
        rows = [row for row in rows if row["request_id"] != "a"]
    elif missing == "service_sample":
        samples = samples[:-1]
    files = {
        "replay/runtime_events.replay.jsonl": events,
        "replay/native_requests.jsonl": rows,
        "server/runtime_audit.jsonl": samples,
    }
    for name, records in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in records))
    status = {
        "writer_error": None, "dropped_records": 0, "failed_records": 0,
        "writer_queue_depth": 0, "pending_batch_count": 0,
        "background_writer_timing": {"categories": {
            "audit:gpu_service_sample": {"records_seen": 3},
        }},
    }
    (tmp_path / "server/native_telemetry_status.json").write_text(json.dumps(status))
    report = {
        "cohort": {"measured_workflow_ids": ["w"]}, "all_measured_completed": True,
        "normal_tails_removed": False, "measured_workflow_JCT_ms": {"w": 41.},
    }
    report_path = tmp_path / "cohort.json"
    report_path.write_text(json.dumps(report))
    if missing:
        with pytest.raises(ValueError):
            audit_arm(plan, tmp_path, report_path)
    else:
        observed = audit_arm(plan, tmp_path, report_path)
        assert observed["measured_receipts"] == 3
        assert observed["measured_outputs"] == 3
        assert observed["mean_jct_s"] == .041
        assert set(observed["workflows"]) == {"w"}
