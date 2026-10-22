import json

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
            "trace": {"llm_request_count": count, "tool_call_count": count},
        })
        for index in range(count):
            events.extend([
                {"kind": "llm_submit", "workflow_id": workflow,
                 "attributes": {"request_id": str(index), "prompt_tokens": 100}},
                {"kind": "llm_result", "workflow_id": workflow,
                 "attributes": {"request_id": str(index), "output_tokens": 10}},
            ])
    (client / "summary.json").write_text(json.dumps({"workflows": workflows}))
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
