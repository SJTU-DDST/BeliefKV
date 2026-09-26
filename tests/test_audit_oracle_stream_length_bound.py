import pytest

from scripts.audit_oracle_stream_length_bound import (
    _live_project_length, evaluate_rows,
)


def _row(project, *, chars=1900, lead=1300, label="true", join=False):
    return {
        "project": project, "task_id": f"{project}__task",
        "label": label, "join_last": join,
        "signal_ts_ms": 2000, "observed_first_content_ts_ms": 1040,
        "final_output_chars_oracle": chars if label == "true" else None,
        "lead_ms": lead if label == "true" else None,
        "return_ts_ms": 2000 + lead if label == "true" else None,
    }


def test_oracle_uses_future_length_only_in_upper_bound():
    rows = [
        _row("alpha", chars=1800, lead=1100),
        _row("beta", chars=2100, lead=1400),
        _row("gamma", chars=1900, lead=1200, join=True),
        _row("gamma", label="false"),
    ]
    result = evaluate_rows(rows)
    gamma = result["folds"]["gamma"]
    assert gamma["stage_candidates"] == 2
    assert gamma["stage_false_or_censored"] == 1
    assert gamma["evaluable"] == 1
    assert gamma["join_last_evaluable"] == 1
    assert gamma["final_length_prior_chars"] == 1950
    modified = [dict(row) for row in rows]
    modified[2]["final_output_chars_oracle"] = 3200
    changed = evaluate_rows(modified)["folds"]["gamma"]
    assert (
        changed["causal_length_prior"]["child_return"]
        == gamma["causal_length_prior"]["child_return"]
    )
    assert (
        changed["oracle_length"]["child_return"]
        != gamma["oracle_length"]["child_return"]
    )


def test_missing_live_rate_does_not_become_an_oracle_example():
    rows = [
        _row("alpha"),
        _row("beta"),
        _row("gamma"),
    ]
    rows[2]["observed_first_content_ts_ms"] = None
    result = evaluate_rows(rows)
    assert result["stage_outcomes"] == {"true": 3}
    assert result["evaluable"] == 2
    assert result["folds"]["gamma"]["evaluable"] == 0
    with pytest.raises(ValueError, match="three projects"):
        evaluate_rows(rows[:2])


def test_project_length_uses_only_completed_other_workflows():
    prior = []
    for task, chars in (("a1", 1800), ("a2", 2200)):
        for _ in range(2):
            prior.append({
                "task_id": task, "return_ts_ms": 4000,
                "signal_ts_ms": 2000, "final_output_chars_oracle": chars,
            })
    current = {
        "task_id": "a3", "signal_ts_ms": 5000,
        "return_ts_ms": 7000, "final_output_chars_oracle": 4000,
    }
    assert _live_project_length(current, prior, 1600) == (2000, True)
    too_late = [{**row, "return_ts_ms": 6000} for row in prior]
    assert _live_project_length(current, too_late, 1600) == (1600, False)
    same_task = [{**row, "task_id": "a3"} for row in prior]
    assert _live_project_length(current, same_task, 1600) == (1600, False)


def test_notice_length_hint_is_separate_from_oracle_and_reports_coverage():
    rows = [
        _row("alpha", chars=1800, lead=1100),
        _row("beta", chars=2100, lead=1400),
        _row("gamma", chars=1900, lead=1200),
    ]
    rows[2]["planned_final_report_chars_at_notice"] = 1850
    base = evaluate_rows(rows)
    assert base["folds"]["gamma"]["reported_length_hint_count"] == 1
    assert base["folds"]["gamma"]["reported_length_hint_char_mae"] == 50
    rows[2]["planned_final_report_chars_at_notice"] = 0
    absent = evaluate_rows(rows)
    assert absent["folds"]["gamma"]["reported_length_hint_count"] == 0
    assert (
        absent["folds"]["gamma"]["reported_length_hint"]["child_return"]
        == absent["folds"]["gamma"]["causal_length_prior"]["child_return"]
    )


def test_fixed_training_excludes_all_heldout_project_labels():
    train = [_row("alpha", chars=1800, lead=1100),
             _row("beta", chars=2100, lead=1400)]
    test = [_row("gamma", chars=1900, lead=1200),
            _row("delta", chars=2500, lead=1700)]
    result = evaluate_rows(test, train_rows=train)
    assert result["protocol"] == "fixed_project_disjoint_train_heldout"
    assert result["train_projects"] == ["alpha", "beta"]
    assert result["folds"]["gamma"]["train_workflows"] == 2
    assert result["folds"]["delta"]["train_workflows"] == 2
    assert (
        result["folds"]["gamma"]["final_length_prior_chars"]
        == result["folds"]["delta"]["final_length_prior_chars"]
    )
    with pytest.raises(ValueError, match="disjoint"):
        evaluate_rows(test, train_rows=[*train, test[0]])
