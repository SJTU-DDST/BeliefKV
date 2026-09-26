import json

from scripts.audit_join_intent_lower_bound import collect, evaluate


def _workflow(
    root, project, *, sibling_return=100, child_return=2200,
    cancelled=False, later_tool=False, join_satisfied=True,
    sibling_final=True, sibling_report_status=None,
    child_report_status=None, parent_wait=False, parent_submit=None,
    parent_cancel=None, parent_next_join=None, future_waiter_at=None,
    parent_submit_epoch=0, parent_submit_context="root-context",
    child_outcome="completed", final_chunk_at=None, tool_after_chunk_at=None,
):
    folder = root / f"{project}__task"
    folder.mkdir(parents=True)
    first = f"{project}:first"
    last = f"{project}:last"
    parent = f"{project}:root"
    join_id = f"{project}:join"
    events = [
        {"kind": "spawn", "target_invocation_id": first, "ts_ms": 0},
        {"kind": "spawn", "target_invocation_id": last, "ts_ms": 0},
        {"kind": "join_create", "join_id": join_id,
         "member_invocation_ids": [first, last],
         "attributes": {"mode": "all"}, "ts_ms": 1},
        {"kind": "return", "invocation_id": first, "ts_ms": sibling_return,
         "attributes": {"outcome": "completed", **(
             {"child_report_status": sibling_report_status}
             if sibling_report_status is not None else {}
         )}},
        {"kind": "llm_result", "invocation_id": last, "ts_ms": child_return - 1,
         "context_id": "child-context", "context_epoch": 1,
         "attributes": {"finish_reason": "stop", "output_chars": 32,
                        "request_id": f"{project}:rid", **(
                            {"stream_final_chunk_ts_ms": final_chunk_at}
                            if final_chunk_at is not None else {}
                        )}},
        {"kind": "return", "invocation_id": last, "ts_ms": child_return,
         "attributes": {"outcome": child_outcome, **(
             {"child_report_status": child_report_status}
             if child_report_status is not None else {}
         )}},
    ]
    if parent_wait:
        events.append({"kind": "join_wait", "join_id": join_id,
                       "invocation_id": parent, "context_id": "root-context",
                       "context_epoch": 0, "ts_ms": 2})
    if final_chunk_at is not None:
        events.append({
            "kind": "structured_action", "invocation_id": last,
            "join_id": join_id, "context_id": "child-context",
            "context_epoch": 1, "ts_ms": final_chunk_at,
            "attributes": {"beliefkv_child_final_chunk_shadow": True,
                           "request_id": f"{project}:rid"},
        })
    if tool_after_chunk_at is not None:
        events.append({"kind": "tool_start", "invocation_id": last,
                       "attributes": {"tool_name": "execute"},
                       "ts_ms": tool_after_chunk_at})
    if future_waiter_at is not None:
        events.append({"kind": "join_wait", "join_id": join_id,
                       "invocation_id": f"{project}:other",
                       "ts_ms": future_waiter_at})
    if parent_submit is not None:
        events.append({"kind": "llm_submit", "invocation_id": parent,
                       "context_id": parent_submit_context,
                       "context_epoch": parent_submit_epoch,
                       "ts_ms": parent_submit})
    if parent_cancel is not None:
        events.append({"kind": "invocation_cancel", "invocation_id": parent,
                       "ts_ms": parent_cancel})
    if parent_next_join is not None:
        events.append({"kind": "join_wait", "join_id": f"{project}:next",
                       "invocation_id": parent, "ts_ms": parent_next_join})
    if sibling_final:
        events.append({"kind": "llm_result", "invocation_id": first,
                       "ts_ms": sibling_return - 1,
                       "attributes": {"finish_reason": "stop",
                                      "output_chars": 24, "tool_call_count": 0,
                                      "request_id": f"{project}:first-rid"}})
    if join_satisfied:
        events.append({"kind": "join_satisfied", "join_id": join_id,
                       "ts_ms": child_return})
    if cancelled:
        events.append({"kind": "invocation_cancel", "invocation_id": first,
                       "ts_ms": 150})
    if later_tool:
        events.append({"kind": "tool_start", "invocation_id": last,
                       "attributes": {"tool_name": "read_file"}, "ts_ms": 300})
    (folder / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    (folder / "sandbox_audit.jsonl").write_text(
        json.dumps({"event": "child_return_intent_shadow",
                    "invocation_id": last, "join_id": join_id, "ts_ms": 200})
        + "\n"
    )


def test_sole_pending_selection_uses_only_past_sibling_returns(tmp_path):
    _workflow(tmp_path, "alpha")
    _workflow(tmp_path, "beta", sibling_return=250)
    _workflow(tmp_path, "gamma", cancelled=True)
    _workflow(tmp_path, "delta", later_tool=True)
    _workflow(tmp_path, "epsilon", join_satisfied=False)
    _workflow(tmp_path, "zeta", sibling_final=False)
    _workflow(tmp_path, "eta", sibling_report_status="blocked")
    _workflow(tmp_path, "theta", sibling_final=False,
              sibling_report_status="complete")
    items = collect([tmp_path])
    assert {row["project"] for row in items} == {
        "alpha", "delta", "epsilon", "zeta", "eta", "theta",
    }
    assert next(row for row in items if row["project"] == "alpha")["lead_ms"] == 2000
    assert next(row for row in items if row["project"] == "delta")["revoked_by_tool"]
    assert next(row for row in items if row["project"] == "epsilon")[
        "censored_or_nonterminal"
    ]
    assert {row["project"] for row in collect(
        [tmp_path], require_observed_sibling_final=True,
    )} == {"alpha", "delta", "epsilon", "theta"}


def test_project_heldout_lower_bound_exposes_violation(tmp_path):
    _workflow(tmp_path, "alpha", child_return=2200)
    _workflow(tmp_path, "beta", child_return=3200)
    _workflow(tmp_path, "gamma", child_return=850)
    report = evaluate(collect([tmp_path]))
    assert report["folds"]["gamma"]["train_min_minus_200ms"] == 1800
    assert report["folds"]["gamma"]["natural_join_before_floor_count"] == 1
    assert report["folds"]["gamma"]["window_success_counts"] == {
        "500": 1, "1000": 0, "2000": 0,
    }
    assert report["folds"]["alpha"]["natural_join_before_floor_count"] == 0


def test_blocked_child_still_can_wake_parent_without_natural_join(tmp_path):
    _workflow(tmp_path, "alpha", parent_wait=True, parent_submit=2300,
              child_report_status="blocked")
    _workflow(tmp_path, "beta", parent_wait=True, parent_submit=2600,
              later_tool=True)
    _workflow(tmp_path, "gamma", parent_wait=True, parent_submit=2400)
    rows = {item["project"]: item for item in collect([tmp_path])}
    assert not rows["alpha"]["natural_join"]
    assert rows["alpha"]["child_report_status"] == "blocked"
    assert rows["alpha"]["parent_reentry_lead_ms"] == 2100
    assert rows["alpha"]["parent_submit_after_join_ms"] == 100
    assert rows["beta"]["revoked_by_tool"]
    assert rows["beta"]["parent_reentry_observed"]
    report = evaluate(list(rows.values()))["folds"]
    assert report["alpha"]["parent_reentry_without_natural_label_count"] == 1
    assert report["alpha"]["natural_join_count"] == 0
    assert report["beta"]["parent_reentry_after_notice_revocation_count"] == 1
    assert report["alpha"]["parent_reentry_train_count"] == 1
    assert report["alpha"]["parent_reentry_train_median_ms"] == 2200
    assert report["alpha"]["parent_reentry_abs_error_p50_ms"] == 100
    assert report["alpha"]["parent_reentry_within_500ms_count"] == 1
    assert report["beta"]["parent_reentry_observed_count"] == 1
    assert report["beta"]["parent_reentry_count"] == 0


def test_reentry_requires_an_existing_unique_waiter_and_current_parent(tmp_path):
    _workflow(tmp_path, "alpha", parent_submit=2300)
    _workflow(tmp_path, "beta", parent_wait=True, future_waiter_at=300,
              parent_submit=2300)
    _workflow(tmp_path, "gamma", parent_wait=True, parent_cancel=2250,
              parent_submit=2300)
    _workflow(tmp_path, "delta", parent_wait=True, parent_next_join=2250,
              parent_submit=2300)
    _workflow(tmp_path, "epsilon", parent_wait=True, parent_submit=2300,
              join_satisfied=False)
    _workflow(tmp_path, "zeta", parent_wait=True, future_waiter_at=150,
              parent_submit=2300)
    _workflow(tmp_path, "eta", parent_wait=True, parent_submit=2300,
              parent_submit_context="other-context")
    _workflow(tmp_path, "theta", parent_wait=True, parent_submit=2300,
              parent_submit_epoch=1)
    rows = {item["project"]: item for item in collect([tmp_path])}
    assert set(rows) == {
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    }
    assert rows["beta"]["parent_reentry_observed"]
    assert rows["theta"]["parent_reentry_observed"]
    assert all(
        not row["parent_reentry_observed"]
        for project, row in rows.items() if project not in {"beta", "theta"}
    )


def test_non_completed_return_does_not_become_natural_label(tmp_path):
    _workflow(tmp_path, "alpha", child_outcome="cancelled",
              parent_wait=True, parent_submit=2300)
    row, = collect([tmp_path])
    assert not row["natural_join"]
    assert row["failure"] == "no_natural_child_return"
    assert row["parent_reentry_observed"]


def test_first_live_final_chunk_is_bound_to_terminal_child_request(tmp_path):
    _workflow(tmp_path, "alpha", parent_wait=True, parent_submit=2300,
              final_chunk_at=2000)
    _workflow(tmp_path, "beta", parent_wait=True, parent_submit=2300,
              final_chunk_at=2000, tool_after_chunk_at=2100)
    _workflow(tmp_path, "gamma", parent_wait=True, parent_submit=2300,
              final_chunk_at=2200)
    rows = {item["project"]: item for item in collect([tmp_path])}
    assert rows["alpha"]["first_final_chunk_seen"]
    assert rows["alpha"]["first_final_chunk_parent_reentry"]
    assert rows["alpha"]["first_final_chunk_parent_lead_ms"] == 300
    assert rows["beta"]["first_final_chunk_seen"]
    assert not rows["beta"]["first_final_chunk_parent_reentry"]
    assert not rows["gamma"]["first_final_chunk_seen"]
    report = evaluate(list(rows.values()))["folds"]
    assert report["beta"]["first_final_chunk_candidate_count"] == 1
    assert report["beta"]["first_final_chunk_true_parent_count"] == 0
    assert report["alpha"]["first_final_chunk_zero_baseline_within_500ms_count"] == 1


def test_first_chunk_does_not_select_later_correct_signal(tmp_path):
    _workflow(tmp_path, "alpha", parent_wait=True, parent_submit=2300,
              final_chunk_at=2000)
    event_path = (
        tmp_path / "alpha__task" / "runtime_events.deepagents.jsonl"
    )
    events = [
        json.loads(line) for line in event_path.read_text().splitlines()
    ]
    events.extend([
        {"kind": "structured_action", "invocation_id": "alpha:last",
         "join_id": "alpha:join", "context_id": "child-context",
         "context_epoch": 0, "ts_ms": 1000,
         "attributes": {"beliefkv_child_final_chunk_shadow": True,
                        "request_id": "alpha:early"}},
        {"kind": "llm_result", "invocation_id": "alpha:last",
         "context_id": "child-context", "context_epoch": 0, "ts_ms": 1010,
         "attributes": {"finish_reason": "stop", "output_chars": 12,
                        "stream_final_chunk_ts_ms": 1000,
                        "request_id": "alpha:early"}},
        {"kind": "llm_submit", "invocation_id": "alpha:last",
         "ts_ms": 1500},
    ])
    event_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    row, = collect([tmp_path])
    assert row["first_final_chunk_seen"]
    assert not row["first_final_chunk_parent_reentry"]
