import json

from scripts.audit_join_intent_lower_bound import collect, evaluate


def _workflow(
    root, project, *, sibling_return=100, child_return=2200,
    cancelled=False, later_tool=False, join_satisfied=True,
    sibling_final=True, sibling_report_status=None,
):
    folder = root / f"{project}__task"
    folder.mkdir(parents=True)
    first = f"{project}:first"
    last = f"{project}:last"
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
         "attributes": {"finish_reason": "stop", "output_chars": 32,
                        "request_id": f"{project}:rid"}},
        {"kind": "return", "invocation_id": last, "ts_ms": child_return,
         "attributes": {"outcome": "completed"}},
    ]
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
