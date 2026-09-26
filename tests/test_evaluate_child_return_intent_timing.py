import json

import pytest

from scripts.evaluate_child_return_intent_timing import (
    evaluate, evaluate_heldout, load_episodes,
)
from scripts.evaluate_child_intent_service_load import (
    _metrics_at_notice, evaluate as evaluate_asof_load,
)


def _workflow(root, project, *, lead=1000, later_tool=False, blocked=False):
    workflow = root / f"{project}_workloads" / "workflows" / f"{project}__issue"
    workflow.mkdir(parents=True)
    child = f"deepagents-invocation:{project}"
    events = [
        {"kind": "invocation_create", "invocation_id": child, "ts_ms": 0},
        {"kind": "spawn", "target_invocation_id": child, "ts_ms": 1},
        {"kind": "join_create", "join_id": "join",
         "member_invocation_ids": [child], "ts_ms": 2},
        {"kind": "tool_end", "invocation_id": child, "ts_ms": 100,
         "attributes": {"tool_name": "read_file"}},
        {"kind": "llm_result", "invocation_id": child, "ts_ms": 300,
         "attributes": {"request_id": f"earlier-{project}", "finish_reason": "tool_calls",
                        "output_chars": 1, "tool_call_count": 1}},
    ]
    if later_tool:
        events.append({"kind": "tool_start", "invocation_id": child, "ts_ms": 800,
                       "attributes": {"tool_name": "grep"}})
    if not blocked:
        events.extend([
            {"kind": "llm_submit", "invocation_id": child, "ts_ms": 700},
            {"kind": "llm_result", "invocation_id": child, "ts_ms": 500 + lead - 10,
             "attributes": {"request_id": f"final-{project}",
                            "finish_reason": "stop", "output_chars": 20,
                            "output_tokens": 100000}},
            {"kind": "return", "invocation_id": child, "ts_ms": 500 + lead},
            {"kind": "join_satisfied", "join_id": "join", "ts_ms": 500 + lead},
        ])
    else:
        (workflow / "child_reports.json").write_text(json.dumps([{
            "invocation_id": child, "semantic_completion": {"status": "blocked"},
        }]))
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
    )
    (workflow / "sandbox_audit.jsonl").write_text(json.dumps({
        "event": "child_return_intent_shadow",
        "invocation_id": child, "ts_ms": 500,
    }) + "\n")
    return workflow


def test_timing_uses_only_notice_time_and_fits_on_other_projects(tmp_path):
    first = _workflow(tmp_path, "alpha", lead=750)
    _workflow(tmp_path, "beta", lead=2800)
    _workflow(tmp_path, "gamma", lead=4300)
    before = evaluate(tmp_path)
    assert before["counts"]["valid_intents"] == 3
    assert before["pooled"]["train_median"]["join_last_child"]["count"] == 3
    assert before["causal_features"] == [
        "child_age_ms", "prior_llm_results", "prior_tool_ends",
    ]
    rows, _ = load_episodes(tmp_path)
    assert rows[0]["features"] == [500, 1, 1]
    # The final response is after the notice; its size and timing must not
    # become prediction features.
    path = first / "runtime_events.deepagents.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    final = next(event for event in events if event["kind"] == "llm_result"
                 and event["ts_ms"] > 500)
    final["attributes"]["output_tokens"] = 1
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    after = evaluate(tmp_path)
    assert after["folds"] == before["folds"]
    assert after["pooled"] == before["pooled"]
    revised_rows, _ = load_episodes(tmp_path)
    assert revised_rows[0]["post_notice"]["final_output_tokens"] == 1
    assert revised_rows[0]["post_notice"]["final_output_chars"] == 20
    assert before["post_notice_decomposition"]["valid"] == 3
    assert next(fold for fold in before["folds"]
                if fold["heldout_project"] == "alpha")["train_median"][
                    "return"]["median_absolute_error_ms"] == 2800


def test_revoked_and_blocked_intents_are_not_success_labels(tmp_path):
    _workflow(tmp_path, "alpha", later_tool=True)
    _workflow(tmp_path, "beta", blocked=True)
    _workflow(tmp_path, "gamma")
    rows, counts = load_episodes(tmp_path)
    assert len(rows) == 1
    assert counts["revoked_or_late"] == 1
    assert counts["nonterminal_or_blocked"] == 1
    assert counts["censored_without_terminal"] == 0
    with pytest.raises(ValueError, match="at least three projects"):
        evaluate(tmp_path)


def test_heldout_fit_does_not_consume_target_labels_or_overlap_projects(tmp_path):
    train = tmp_path / "fit"
    test = tmp_path / "heldout"
    _workflow(train, "alpha", lead=1000)
    _workflow(train, "beta", lead=3000)
    _workflow(train, "gamma", lead=4000)
    target = _workflow(test, "delta", lead=2000)
    before = evaluate_heldout(train, test)
    assert evaluate_heldout(train, test / "delta_workloads") == before
    assert before["train_projects"] == ["alpha", "beta", "gamma"]
    assert before["heldout_projects"] == ["delta"]
    assert before["results"]["train_median"]["return"]["mae_ms"] == 1000
    # Held-out response size is not an input.
    path = target / "runtime_events.deepagents.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events[-3]["attributes"]["output_tokens"] = 2
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    after = evaluate_heldout(train, test)
    assert after["results"] == before["results"]
    assert after["post_notice_decomposition"] != before["post_notice_decomposition"]
    _workflow(test, "alpha")
    with pytest.raises(ValueError, match="overlap"):
        evaluate_heldout(train, test)


def test_asof_load_ignores_future_metrics_and_rejects_stale_data(tmp_path):
    train = tmp_path / "fit"
    test = tmp_path / "heldout"
    for project, lead in (("alpha", 1000), ("beta", 3000), ("gamma", 4000)):
        workflow = _workflow(train, project, lead=lead)
        (workflow.parent.parent / "sglang_metrics.jsonl").write_text(json.dumps({
            "monotonic_ts_ms": 100, "num_running_reqs": 2,
            "num_queue_reqs": 0,
        }) + "\n")
    target = _workflow(test, "delta", lead=2500)
    path = target.parent.parent / "sglang_metrics.jsonl"
    path.write_text(json.dumps({
        "monotonic_ts_ms": 100, "num_running_reqs": 8,
        "num_queue_reqs": 1,
    }) + "\n")
    result = evaluate_asof_load(train, test)
    assert result["pre_notice_load"]["development"]["running_p50"] == 8
    assert result["results"]["train_median"]["return"]["count"] == 1
    path.write_text(path.read_text() + json.dumps({
        "monotonic_ts_ms": 600, "num_running_reqs": 1000,
        "num_queue_reqs": 1000,
    }) + "\n")
    assert evaluate_asof_load(train, test) == result
    with pytest.raises(ValueError, match="no recent metric"):
        _metrics_at_notice(path, 4000)
    path.unlink()
    with pytest.raises(FileNotFoundError, match="contemporaneous metrics"):
        evaluate_asof_load(train, test)
