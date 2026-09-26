import json

import pytest

from scripts.evaluate_join_group_notice import collect, evaluate


def _write(root, task, *, two_children=False, notices=(1000,), revoke=False,
           blocked=False):
    folder = root / "workflows" / task
    folder.mkdir(parents=True)
    children = [f"child:{task}:a"]
    if two_children:
        children.append(f"child:{task}:b")
    events = [
        {"kind": "spawn", "target_invocation_id": child, "ts_ms": 0}
        for child in children
    ]
    events += [
        {"kind": "invocation_create", "invocation_id": child, "ts_ms": 1}
        for child in children
    ]
    events += [
        {"kind": "join_create", "join_id": task,
         "member_invocation_ids": children, "ts_ms": 2,
         "attributes": {"mode": "all"}},
        {"kind": "join_wait", "join_id": task, "ts_ms": 3,
         "invocation_id": f"root:{task}", "context_id": f"ctx:{task}"},
    ]
    if revoke:
        events.append({
            "kind": "tool_start", "invocation_id": children[-1], "ts_ms": 1600,
            "attributes": {"tool_name": "execute"},
        })
    for index, child in enumerate(children):
        end = 1800 + 200 * index
        events += [
            {"kind": "llm_result", "invocation_id": child,
             "ts_ms": end - 5, "attributes": {
                 "request_id": f"final:{child}", "finish_reason": "stop",
                 "output_chars": 20, "tool_call_count": 0,
             }},
            {"kind": "return", "invocation_id": child, "ts_ms": end,
             "attributes": {
                 "outcome": "completed",
                 "child_report_status": "blocked" if blocked else "complete",
             }},
        ]
    events.append({
        "kind": "join_satisfied", "join_id": task, "ts_ms": end,
    })
    events.append({
        "kind": "llm_submit", "invocation_id": f"root:{task}",
        "context_id": f"ctx:{task}", "ts_ms": end + 100,
    })
    (folder / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8",
    )
    (folder / "sandbox_audit.jsonl").write_text(
        "".join(json.dumps({
            "event": "child_return_intent_shadow",
            "invocation_id": child, "join_id": task, "ts_ms": when,
        }) + "\n" for child, when in zip(children, notices)), encoding="utf-8",
    )


def test_first_candidate_requires_each_unfinished_child(tmp_path):
    root = tmp_path / "heldout"
    _write(root, "astropy__one", two_children=True, notices=(1000, 1500))
    groups, counts = collect(root / "workflows")
    assert counts["natural_groups"] == 1
    assert counts["candidate_natural"] == 1
    assert groups[0]["trigger_ts_ms"] == 1500
    assert groups[0]["pending_notices_ms"] == {
        "child:astropy__one:a": 1000,
        "child:astropy__one:b": 1500,
    }
    assert groups[0]["lead_ms"] == 500


def test_missing_sibling_notice_is_not_oracle_join_last_signal(tmp_path):
    root = tmp_path / "heldout"
    _write(root, "astropy__one", two_children=True, notices=(1000,))
    groups, counts = collect(root / "workflows")
    assert not groups
    assert counts["natural_groups"] == 1
    assert counts["no_whole_group_candidate"] == 1


def test_notice_before_parent_wait_remains_actionable(tmp_path):
    root = tmp_path / "heldout"
    _write(root, "astropy__one", notices=(1000,))
    event_path = (
        root / "workflows" / "astropy__one" /
        "runtime_events.deepagents.jsonl"
    )
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    next(row for row in events if row["kind"] == "join_wait")["ts_ms"] = 1500
    event_path.write_text("".join(json.dumps(row) + "\n" for row in events))
    groups, _ = collect(root / "workflows")
    assert groups[0]["trigger_ts_ms"] == 1500
    assert groups[0]["pending_notices_ms"]["child:astropy__one:a"] == 1000


def test_stale_context_notice_does_not_make_group_actionable(tmp_path):
    root = tmp_path / "heldout"
    _write(root, "astropy__one", notices=(1000,))
    event_path = (
        root / "workflows" / "astropy__one" /
        "runtime_events.deepagents.jsonl"
    )
    audit_path = event_path.parent / "sandbox_audit.jsonl"
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    events.append({
        "kind": "llm_result", "invocation_id": "child:astropy__one:a",
        "ts_ms": 900, "context_id": "ctx", "context_epoch": 2,
    })
    event_path.write_text("".join(json.dumps(row) + "\n" for row in events))
    audit_path.write_text(json.dumps({
        "event": "child_return_intent_shadow",
        "invocation_id": "child:astropy__one:a", "join_id": "astropy__one",
        "ts_ms": 1000, "context_id": "ctx", "context_epoch": 1,
    }) + "\n")
    groups, counts = collect(root / "workflows")
    assert not groups
    assert counts["invalid_notice_identity"] == 1


def test_duplicate_notice_timestamp_does_not_break_ordering(tmp_path):
    root = tmp_path / "heldout"
    _write(root, "astropy__one", notices=(1000,))
    audit_path = (
        root / "workflows" / "astropy__one" / "sandbox_audit.jsonl"
    )
    audit_path.write_text(audit_path.read_text() * 2)
    groups, counts = collect(root / "workflows")
    assert counts["candidate_natural"] == 1
    assert groups[0]["trigger_ts_ms"] == 1000


def test_revocation_and_blocked_child_are_not_clean_training_labels(tmp_path):
    root = tmp_path / "heldout"
    _write(root, "astropy__one", two_children=True,
           notices=(1000, 1500), revoke=True)
    _write(root, "astropy__two", notices=(1000,), blocked=True)
    groups, counts = collect(root / "workflows")
    assert counts["candidate_revoked"] == 1
    assert counts["candidate_censored"] == 1
    assert all(row["lead_ms"] is None for row in groups)
    assert counts["candidate_parent_reentry_observed"] == 2
    assert groups[1]["parent_reentry_lead_ms"] == 900


def test_cancelled_child_is_not_a_natural_join_even_if_return_says_completed(
    tmp_path,
):
    root = tmp_path / "heldout"
    _write(root, "astropy__one", notices=(1000,))
    event_path = (
        root / "workflows" / "astropy__one" /
        "runtime_events.deepagents.jsonl"
    )
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    events.append({
        "kind": "invocation_cancel", "invocation_id": "child:astropy__one:a",
        "ts_ms": 1700,
    })
    event_path.write_text("".join(json.dumps(row) + "\n" for row in events))
    groups, counts = collect(root / "workflows")
    assert groups[0]["label"] == "censored"
    assert counts.get("natural_groups", 0) == 0
    assert groups[0]["parent_reentry_lead_ms"] == 900


def test_parent_reentry_must_precede_parent_invalidating_transition(tmp_path):
    root = tmp_path / "heldout"
    _write(root, "astropy__one", notices=(1000,))
    event_path = (
        root / "workflows" / "astropy__one" /
        "runtime_events.deepagents.jsonl"
    )
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    events.append({
        "kind": "join_wait", "invocation_id": "root:astropy__one",
        "join_id": "next-join", "ts_ms": 1850,
    })
    event_path.write_text("".join(json.dumps(row) + "\n" for row in events))
    groups, counts = collect(root / "workflows")
    assert groups[0]["parent_reentry_lead_ms"] is None
    assert counts.get("candidate_parent_reentry_observed", 0) == 0


def test_project_disjoint_group_error_and_coverage(tmp_path):
    train, heldout = tmp_path / "train", tmp_path / "heldout"
    _write(train, "sphinx-doc__one", notices=(800,))
    _write(train, "sphinx-doc__two", notices=(1200,))
    _write(heldout, "astropy__one", two_children=True, notices=(1000, 1500))
    _write(heldout, "astropy__two", two_children=True, notices=(1000,))
    report = evaluate(train / "workflows", heldout / "workflows")
    assert report["train_task_balanced_child_notice_prior_ms"] == 800
    assert report["heldout_counts"]["natural_groups"] == 2
    assert report["heldout_counts"]["candidate_natural"] == 1
    assert report["heldout_natural_group_point_error_ms"]["count"] == 1
    assert report["heldout_natural_group_point_error_ms"]["mae_ms"] == 300
    assert report["train_task_balanced_parent_reentry_prior_ms"] == 900
    assert report["train_task_balanced_join_to_parent_submit_prior_ms"] == 100
    assert report["heldout_observed_parent_reentry_point_error_ms"]["count"] == 1
    assert report["heldout_observed_parent_reentry_composed_point_error_ms"][
        "mae_ms"
    ] == 300


def test_same_project_cannot_evaluate(tmp_path):
    train, heldout = tmp_path / "train", tmp_path / "heldout"
    _write(train, "astropy__one")
    _write(heldout, "astropy__two")
    with pytest.raises(ValueError, match="disjoint projects"):
        evaluate(train / "workflows", heldout / "workflows")
