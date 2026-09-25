import json

from scripts.pilot_inflight_tool_survival import audit


def test_only_prior_inflight_peers_count_without_target_leak(tmp_path):
    waits = [
        ("peer", 0, 10_000, True),
        ("old", 0, 2_000, True),
        ("long", 3_000, 8_000, True),
        ("short", 3_500, 3_600, True),
        ("excluded", 3_500, 9_000, False),
    ]
    (tmp_path / "external_waits.jsonl").write_text("".join(
        json.dumps({
            "workflow_id": "wf", "tool_call_id": name,
            "invocation_id": name, "tool_name": "execute",
            "is_child": True, "project": "p", "observed_command_class": "test",
            "start_ts_ms": start, "terminal_ts_ms": end,
            "training_eligible_survival": eligible, "censored": False,
        }) + "\n"
        for name, start, end, eligible in waits
    ))
    (tmp_path / "frontier_decision_points.jsonl").write_text("".join(
        json.dumps({
            "workflow_id": "wf", "trigger_kind": "tool_start",
            "trigger_invocation_id": name,
            "trigger_attributes": {"tool_call_id": name, "is_child": True},
        }) + "\n"
        for name, _, _, _ in waits
    ))
    result = audit(tmp_path)
    starts = result["groups"]["elapsed_0ms"]["all:any:peers_1"]
    assert starts["samples"] == 4
    assert starts["true_long"] == 3
    assert starts["predicted_long"] == 2
    assert starts["correct_long"] == 1
    assert starts["precision"] == 0.5
    assert result["groups"]["elapsed_0ms"][
        "all:other_workflow:peers_1"
    ]["predicted_long"] == 0
