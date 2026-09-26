import json

import pytest

from scripts.evaluate_child_return_intent_timing import (
    evaluate, evaluate_heldout, load_episodes,
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
    assert evaluate(tmp_path) == before
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
    assert counts["nonterminal_or_censored"] == 1
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
    assert evaluate_heldout(train, test) == before
    _workflow(test, "alpha")
    with pytest.raises(ValueError, match="overlap"):
        evaluate_heldout(train, test)
