import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.evaluate_join_group_notice import (
    _final_result_notice, collect, evaluate, main,
)


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


def test_parent_reentry_does_not_cross_context_epoch(tmp_path):
    root = tmp_path / "heldout"
    _write(root, "astropy__one", notices=(1000,))
    event_path = (
        root / "workflows" / "astropy__one" /
        "runtime_events.deepagents.jsonl"
    )
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    waiter = next(row for row in events if row["kind"] == "join_wait")
    submit = next(row for row in events if row["kind"] == "llm_submit")
    waiter["context_epoch"] = 2
    submit["context_epoch"] = 3
    event_path.write_text("".join(json.dumps(row) + "\n" for row in events))
    groups, counts = collect(root / "workflows")
    assert groups[0]["label"] == "natural"
    assert groups[0]["parent_reentry_lead_ms"] is None
    assert counts.get("candidate_parent_reentry_observed", 0) == 0

    submit["context_epoch"] = 2
    event_path.write_text("".join(json.dumps(row) + "\n" for row in events))
    groups, counts = collect(root / "workflows")
    assert groups[0]["parent_reentry_lead_ms"] == 900
    assert counts["candidate_parent_reentry_observed"] == 1


def test_parent_cancel_before_join_satisfied_invalidates_reentry(tmp_path):
    root = tmp_path / "heldout"
    _write(root, "astropy__one", notices=(1000,))
    event_path = (
        root / "workflows" / "astropy__one" /
        "runtime_events.deepagents.jsonl"
    )
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    events.append({
        "kind": "invocation_cancel", "invocation_id": "root:astropy__one",
        "ts_ms": 1600,
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


@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        ({"tool_call_count": 0, "output_chars": 8}, True),
        ({"tool_call_count": 1, "structured_action_names": [
            "ChildCompletion",
        ]}, True),
        ({"tool_call_count": 0, "output_chars": 7}, False),
        ({"tool_call_count": 0, "output_chars": 30,
          "finish_reason": "length"}, False),
        ({"tool_call_count": 0, "output_chars": 30,
          "invalid_tool_call_count": 1}, False),
        ({"tool_call_count": 0, "output_chars": 30,
          "runtime_internal": True}, False),
        ({"tool_call_count": 1, "output_chars": 30,
          "structured_action_names": ["execute"]}, False),
    ],
)
def test_final_result_notice_matches_runtime_hint(attrs, expected):
    assert _final_result_notice({"kind": "llm_result", "attributes": attrs}) == expected


def test_llm_result_replay_requires_all_live_members_and_no_shadow(tmp_path):
    root = tmp_path / "train"
    _write(root, "django__one", two_children=True, notices=())
    groups, counts = collect(root / "workflows", notice_source="llm_result")
    assert counts["all_mode_groups"] == 1
    assert counts["eligible_child_final_results"] == 2
    assert counts["candidate_natural"] == 1
    assert groups[0]["trigger_ts_ms"] == 1995
    assert groups[0]["pending_return_lead_ms"] == {
        "child:django__one:b": 5,
    }

    events_path = (
        root / "workflows" / "django__one" /
        "runtime_events.deepagents.jsonl"
    )
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    last_result = next(
        row for row in events if row["kind"] == "llm_result"
        and row["invocation_id"] == "child:django__one:b"
    )
    last_result["attributes"]["output_chars"] = 1
    events_path.write_text("".join(json.dumps(row) + "\n" for row in events))
    groups, counts = collect(root / "workflows", notice_source="llm_result")
    assert not groups
    assert counts["no_whole_group_candidate"] == 1


def test_llm_result_notice_is_revoked_on_model_reentry_without_tool(tmp_path):
    root = tmp_path / "train"
    _write(root, "django__one", notices=())
    path = root / "workflows" / "django__one" / "runtime_events.deepagents.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events += [
        {
            "kind": "llm_result", "invocation_id": "child:django__one:a",
            "ts_ms": 1000, "attributes": {
                "tool_call_count": 0, "output_chars": 40,
            },
        },
        {
            "kind": "llm_submit", "invocation_id": "child:django__one:a",
            "ts_ms": 1200,
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in events))
    groups, counts = collect(root / "workflows", notice_source="llm_result")
    assert counts["candidate_revoked"] == 1
    assert groups[0]["trigger_ts_ms"] == 1000
    assert groups[0]["lead_ms"] is None


def test_terminal_child_completion_tool_does_not_revoke_notice(tmp_path):
    root = tmp_path / "train"
    _write(root, "django__one", notices=())
    path = root / "workflows" / "django__one" / "runtime_events.deepagents.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    final = next(row for row in events if row["kind"] == "llm_result")
    final["attributes"] = {
        "tool_call_count": 1,
        "structured_action_names": ["ChildCompletion"],
        "output_chars": 0,
    }
    events.append({
        "kind": "tool_start", "invocation_id": "child:django__one:a",
        "ts_ms": 1796, "attributes": {"tool_name": "ChildCompletion"},
    })
    path.write_text("".join(json.dumps(row) + "\n" for row in events))
    groups, counts = collect(root / "workflows", notice_source="llm_result")
    assert counts["candidate_natural"] == 1
    assert groups[0]["lead_ms"] == 5


def test_llm_result_prior_uses_only_project_disjoint_training_groups(tmp_path):
    train, heldout = tmp_path / "train", tmp_path / "heldout"
    _write(train, "django__one", notices=())
    _write(heldout, "astropy__one", notices=())
    report = evaluate(
        train / "workflows", heldout / "workflows",
        notice_source="llm_result",
    )
    assert report["notice_source"] == "llm_result"
    assert report["train_counts"]["natural_pending_child_notices"] == 1
    assert report["train_task_balanced_child_notice_prior_ms"] == 5
    assert report["heldout_natural_group_point_error_ms"]["mae_ms"] == 0
    assert report["heldout_natural_group_lead_windows"]["at_least_500ms"] == 0
    gain = report["heldout_natural_group_paired_gain_vs_zero"]
    assert gain["tasks"] == 1
    assert gain["task_bootstrap_95pct_ci_ms"] == [5, 5]
    assert report["heldout_by_project"]["astropy"][
        "natural_candidate_gain_vs_zero"
    ]["paired_mae_improvement_ms"] == 5


def test_main_counts_frozen_workflows_even_when_runner_trace_is_missing(
    tmp_path, monkeypatch,
):
    train = tmp_path / "train"
    heldout = tmp_path / "heldout"
    _write(train, "sphinx-doc__one")
    _write(heldout, "astropy__one")
    for run, ids, runner_error in (
        (train, ["sphinx-doc__one"], None),
        (heldout, ["astropy__one", "astropy__two"], "astropy__two"),
    ):
        (run / "manifest.json").write_text(json.dumps({"instance_ids": ids}))
        (run / "summary.json").write_text(json.dumps({
            "workflow_count": len(ids),
            "workflows": [
                {"instance_id": task, "outcome": (
                    "runner_error" if task == runner_error else "completed"
                )}
                for task in ids
            ],
        }))
        for task in ids:
            if task != runner_error:
                (run / "workflows" / task / "result.json").write_text("{}")
    output = tmp_path / "join_report.json"
    monkeypatch.setattr(sys, "argv", [
        "evaluate_join_group_notice.py",
        "--train-workflows", str(train / "workflows"),
        "--heldout-workflows", str(heldout / "workflows"),
        "--output", str(output),
    ])
    main()
    result = json.loads(output.read_text())
    assert result["heldout_frozen_workflows"] == 2
    assert result["heldout_frozen_by_project"] == {"astropy": 2}
    assert result["heldout_missing_trace_workflows"] == ["astropy__two"]
    assert result["heldout_runner_errors"] == ["astropy__two"]
    assert result["heldout_counts"]["workflows"] == 1

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/evaluate_join_group_notice.py"
    )
    direct_output = tmp_path / "join_direct.json"
    subprocess.run([
        sys.executable, str(script),
        "--train-workflows", str(train / "workflows"),
        "--heldout-workflows", str(heldout / "workflows"),
        "--output", str(direct_output),
    ], cwd=tmp_path, check=True, capture_output=True, text=True)
    assert json.loads(direct_output.read_text()) == result
