import json

import pytest

from scripts.audit_paired_realized_work import collect_arm, compare_pair


def fixture(tmp_path, name, counts):
    arm = tmp_path / name
    client, server = arm / "client_2", arm / "server"
    client.mkdir(parents=True)
    server.mkdir()
    workflows, events = [], []
    for instance, count in enumerate(counts):
        workflow = f"{name}-{instance}"
        workflows.append({
            "workflow_id": workflow, "instance_id": str(instance), "duration_seconds": 100.,
            "outcome": "completed", "native_agent_jct_eligible": True,
            "trace": {
                "llm_request_count": count, "tool_call_count": count,
                "event_counts": {"llm_submit": count, "llm_result": count},
            },
        })
        client_events = []
        for index in range(count):
            events.extend([
                {"kind": "llm_submit", "workflow_id": workflow,
                 "attributes": {"request_id": str(index), "prompt_tokens": 100}},
                {"kind": "llm_result", "workflow_id": workflow,
                 "attributes": {"request_id": str(index), "output_tokens": 10}},
            ])
            client_events.extend({
                "kind": kind, "workflow_id": workflow,
                "attributes": {"request_id": str(index), "runtime_internal": False},
            } for kind in ("llm_submit", "llm_result"))
        trace = client / "workflows" / str(instance) / "runtime_events.deepagents.jsonl"
        trace.parent.mkdir(parents=True)
        trace.write_text("".join(json.dumps(row) + "\n" for row in client_events))
    (client / "summary.json").write_text(json.dumps({
        "workflows": workflows, "workflow_count": len(counts),
        "completed_workflows": len(counts),
    }))
    (server / "runtime_events.sglang.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events),
    )
    return arm


def test_opposing_per_task_work_changes_do_not_look_matched_when_totals_equal(tmp_path):
    left = collect_arm(fixture(tmp_path, "left", (10, 30)))
    right = collect_arm(fixture(tmp_path, "right", (30, 10)))
    result = compare_pair(left, right)
    assert result["work_evidence_complete"]
    assert result["same_task_population"]
    assert result["joint_close_counts"] == {"0.05": 0, "0.1": 0, "0.2": 0}
    assert result["median_symmetric_work_gaps"]["output_tokens"] > .6
    assert len(result["rows"]) == 2
    assert result["performance_accepted"] is False


def test_native_request_ids_are_scoped_to_the_workflow(tmp_path):
    left = collect_arm(fixture(tmp_path, "left", (10, 10)))
    right = collect_arm(fixture(tmp_path, "right", (10, 10)))
    assert left["duplicate_events"] == {}
    assert compare_pair(left, right)["joint_close_counts"]["0.05"] == 2


def rewrite(path, update):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("".join(json.dumps(row) + "\n" for row in update(rows)))


def test_native_events_dropped_as_a_pair_cannot_pass_on_balanced_counts(tmp_path):
    arm = fixture(tmp_path, "left", (2,))
    rewrite(arm / "server/runtime_events.sglang.jsonl", lambda rows: rows[:2])
    result = collect_arm(arm)
    row = result["workflows"]["0"]
    assert row["native_submits"] == row["native_results"] == 1
    assert row["request_alignment"]["llm_submit_missing_native"] == ["1"]
    assert not result["work_evidence_complete"]


def test_same_counts_with_different_request_identities_are_incomplete(tmp_path):
    arm = fixture(tmp_path, "left", (1,))

    def swap(rows):
        rows[-1]["attributes"]["request_id"] = "another-request"
        return rows

    rewrite(arm / "server/runtime_events.sglang.jsonl", swap)
    result = collect_arm(arm)
    alignment = result["workflows"]["0"]["request_alignment"]
    assert alignment["llm_result_missing_native"] == ["0"]
    assert alignment["llm_result_missing_client"] == ["another-request"]
    assert not result["work_evidence_complete"]


def test_internal_compression_is_paired_without_dropping_its_token_work(tmp_path):
    arm = fixture(tmp_path, "left", (1,))
    client = arm / "client_2/workflows/0/runtime_events.deepagents.jsonl"
    rewrite(client, lambda rows: rows + [{
        "kind": kind, "workflow_id": "left-0",
        "attributes": {"request_id": "compression", "runtime_internal": True},
    } for kind in ("llm_submit", "llm_result")])
    rewrite(arm / "server/runtime_events.sglang.jsonl", lambda rows: rows + [
        {"kind": "llm_submit", "workflow_id": "left-0",
         "attributes": {"request_id": "compression", "prompt_tokens": 200}},
        {"kind": "llm_result", "workflow_id": "left-0",
         "attributes": {"request_id": "compression", "output_tokens": 20}},
    ])
    summary_path = arm / "client_2/summary.json"
    summary = json.loads(summary_path.read_text())
    summary["workflows"][0]["trace"]["event_counts"] = {"llm_submit": 2, "llm_result": 2}
    summary_path.write_text(json.dumps(summary))
    result = collect_arm(arm)
    row = result["workflows"]["0"]
    assert result["work_evidence_complete"]
    assert row["llm_requests"] == 1
    assert row["native_submits"] == row["native_results"] == 2
    assert (row["input_tokens"], row["output_tokens"]) == (300, 30)
    assert row["request_alignment"]["client_internal_request_ids"] == ["compression"]
    assert row["request_alignment"]["client_external_counts_match_summary"]
    assert row["request_alignment"]["client_total_counts_match_summary"]


@pytest.mark.parametrize("fault", (
    "client_missing", "client_pair_missing", "client_result_missing",
    "client_duplicate", "client_wrong_workflow", "client_invalid_request",
    "client_unknown_internal", "client_internal_disagrees", "summary_count",
    "summary_total_count", "summary_population",
    "native_duplicate", "native_unknown_workflow", "native_invalid_usage",
    "native_invalid_request", "native_empty_request",
))
def test_incomplete_or_inconsistent_evidence_never_qualifies(tmp_path, fault):
    arm = fixture(tmp_path, "left", (2,))
    client = arm / "client_2/workflows/0/runtime_events.deepagents.jsonl"
    native = arm / "server/runtime_events.sglang.jsonl"
    if fault == "client_missing":
        client.unlink()
    elif fault == "client_pair_missing":
        rewrite(client, lambda rows: rows[:2])
    elif fault == "client_result_missing":
        rewrite(client, lambda rows: rows[:-1])
    elif fault == "client_duplicate":
        rewrite(client, lambda rows: rows + rows[:1])
    elif fault.startswith("summary_"):
        path = arm / "client_2/summary.json"
        summary = json.loads(path.read_text())
        if fault == "summary_count":
            summary["workflows"][0]["trace"]["llm_request_count"] += 1
        elif fault == "summary_total_count":
            summary["workflows"][0]["trace"]["event_counts"]["llm_submit"] += 1
        else:
            summary["workflow_count"] += 1
        path.write_text(json.dumps(summary))
    else:
        target = client if fault.startswith("client_") else native

        def mutate(rows):
            if fault == "client_wrong_workflow":
                rows[0]["workflow_id"] = "other-workflow"
            elif fault == "client_invalid_request":
                rows[0]["attributes"]["request_id"] = None
            elif fault == "client_unknown_internal":
                del rows[0]["attributes"]["runtime_internal"]
            elif fault == "client_internal_disagrees":
                rows[0]["attributes"]["runtime_internal"] = True
            elif fault == "native_duplicate":
                return rows + rows[:1]
            elif fault == "native_unknown_workflow":
                rows[0]["workflow_id"] = "other-workflow"
            elif fault == "native_invalid_usage":
                rows[0]["attributes"]["prompt_tokens"] = None
            elif fault == "native_invalid_request":
                rows[0]["attributes"]["request_id"] = None
            elif fault == "native_empty_request":
                rows[0]["attributes"]["request_id"] = ""
            return rows

        rewrite(target, mutate)
    assert not collect_arm(arm)["work_evidence_complete"]


def test_empty_population_is_not_complete_evidence(tmp_path):
    result = collect_arm(fixture(tmp_path, "empty", ()))
    assert not result["complete_task_population"]
    assert not result["work_evidence_complete"]


def test_losing_internal_pairs_in_both_logs_still_fails_summary_reconciliation(tmp_path):
    arm = fixture(tmp_path, "left", (1,))
    path = arm / "client_2/summary.json"
    summary = json.loads(path.read_text())
    summary["workflows"][0]["trace"]["event_counts"] = {"llm_submit": 2, "llm_result": 2}
    path.write_text(json.dumps(summary))
    result = collect_arm(arm)
    alignment = result["workflows"]["0"]["request_alignment"]
    assert alignment["client_external_counts_match_summary"]
    assert not alignment["client_total_counts_match_summary"]
    assert not result["work_evidence_complete"]
