from pathlib import Path

import pytest

from experiments.reports.mixed_chunk_20261029.complete_after_pair import (
    ORDER, commands, pair_ready, validate_physical_scope,
)
from scripts import run_causal_comparison as causal


def queue():
    return {"entries": {
        label: {"comparison_registration": "/registrations/" + label,
                "comparison_run": "/runs/" + label}
        for label in ORDER
    }}


@pytest.mark.parametrize("stage", (
    "live", "review_live", "review_exited", "failed", "missing_handle", "incomplete", "different_work",
))
def test_cpu_mechanisms_wait_for_full_pair_review_and_actual_exit(monkeypatch, stage):
    state = {
        "status": "waiting_position_then_complete_formal_work",
        "watcher": {"pid": 42},
    }
    if stage in ("review_live", "review_exited", "incomplete", "different_work"):
        state["status"] = "complete_pair_reviewed"
    elif stage == "failed":
        state["status"] = "stopped_no_next_gpu_action"
    monkeypatch.setattr(causal.reservation, "alive", lambda _: stage in ("live", "review_live"))
    import experiments.reports.mixed_chunk_20261029.complete_after_pair as review
    checked = []

    def work(path):
        checked.append(path)
        if stage == "incomplete":
            return None
        return {"work": str(path) if stage == "different_work" else "same"}

    monkeypatch.setattr(review, "complete_work", work)
    if stage in ("failed", "missing_handle", "incomplete", "different_work"):
        with pytest.raises(ValueError):
            pair_ready(queue(), state)
    else:
        assert pair_ready(queue(), state) is (stage == "review_exited")
    assert bool(checked) is (stage in ("review_exited", "incomplete", "different_work"))


def test_cpu_actions_audit_both_mixed_outputs_and_only_declared_full_mechanisms():
    actions = commands(queue(), Path("/output"))
    for label in ("thunder", "full"):
        assert "--complete" in actions[label + "_output"]
        assert "--complete" in actions[label + "_decode_gaps"]
    assert "--expected-receipts" in actions["full_eos"]
    assert all(command[0].startswith("scripts/audit_") for command in actions.values())
    assert not any("launch" in argument or "run_causal_comparison" in argument
                   for command in actions.values() for argument in command)


@pytest.mark.parametrize("source,handoffs", (
    ("join_ticket", 0), ("tool_ticket", 0), ("execution_handoff", 0), ("join_ticket", 1),
))
def test_join_tickets_do_not_reenable_user_disabled_physical_features(source, handoffs):
    physical = {"handoff_events": handoffs, "prefetch_journal_commands_by_source": {source: 1}}
    if source == "join_ticket" and handoffs == 0:
        validate_physical_scope(physical)
    else:
        with pytest.raises(ValueError, match="user-disabled"):
            validate_physical_scope(physical)
