from collections import Counter
from pathlib import Path

import pytest

from scripts import audit_join_two_stage_windows as windows
from scripts.audit_join_two_stage_windows import paired, summarize


def _row(task, join, trigger, lead, *, parent=None, label="natural"):
    return {
        "task_id": task, "join_id": join, "trigger_ts_ms": trigger,
        "label": label, "lead_ms": lead, "parent_reentry_lead_ms": parent,
    }


def test_summary_separates_join_and_parent_submitted_windows():
    rows = [
        _row("a", "j1", 100, 1200, parent=1600),
        _row("b", "j2", 300, 200, parent=800),
        _row("c", "j3", 500, 180, parent=None),
        _row("d", "j4", 800, None, label="revoked"),
    ]
    counts = Counter(
        all_mode_groups=5, candidate_revoked=1, candidate_censored=0,
        no_whole_group_candidate=1,
    )
    result = summarize(rows, counts)
    assert result["candidate_natural"] == 3
    assert result["join_lead_ge_500ms"] == 1
    assert result["parent_first_submit_observed"] == 2
    assert result["parent_first_submit_lead_ge_500ms"] == 2
    assert result["parent_first_submit_lead_ge_1000ms"] == 1
    assert result["candidate_revoked"] == 1
    assert result["no_candidate"] == 1


def test_paired_counts_only_common_natural_join_groups():
    early = [
        _row("a", "j1", 100, 1200, parent=1500),
        _row("b", "j2", 200, 1200, parent=None),
        _row("c", "j3", 300, None, label="revoked"),
    ]
    final = [
        _row("a", "j1", 1100, 200, parent=500),
        _row("b", "j2", 1300, None, label="censored"),
        _row("c", "j3", 1350, 250, parent=750),
    ]
    result = paired(early, final)
    assert result == {
        "both_natural_groups": 1,
        "early_only_natural_groups": 1,
        "final_only_natural_groups": 1,
        "both_with_parent_first_submit": 1,
        "early_join_lead_ge_500ms": 1,
        "final_join_lead_ge_500ms": 0,
        "early_parent_first_submit_lead_ge_500ms": 1,
        "final_parent_first_submit_lead_ge_500ms": 1,
    }


def test_paired_rejects_noncausal_or_duplicate_terminal_group():
    early = [_row("a", "j1", 150, 300, parent=400)]
    final = [_row("a", "j1", 140, 310, parent=410)]
    with pytest.raises(ValueError, match="preceded"):
        paired(early, final)
    with pytest.raises(ValueError, match="duplicate"):
        paired(early, final * 2)


def test_summary_accepts_absent_zero_count_categories():
    result = summarize(
        [_row("a", "j", 0, 600, parent=900)],
        {"all_mode_groups": 1},
    )
    assert result["candidate_censored"] == 0
    assert result["candidate_revoked"] == 0
    assert result["no_candidate"] == 0


def test_audit_rejects_shared_project_before_loading_events(monkeypatch):
    def batch(path):
        name = Path(path).name
        return ([f"shared__{name}"], [])

    monkeypatch.setattr(windows, "require_complete_batch", batch)
    monkeypatch.setattr(
        windows, "collect",
        lambda *_args, **_kwargs: pytest.fail("loaded overlapping events"),
    )
    with pytest.raises(ValueError, match="project-disjoint"):
        windows.audit(Path("train"), Path("heldout"))
