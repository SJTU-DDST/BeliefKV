import pytest

from scripts.audit_workflow_startup import summarize


def event(kind, stamp, workflow="wf"):
    return {"kind": kind, "workflow_id": workflow, "ts_ms": stamp}


def test_complete_jct_keeps_pre_model_preparation_and_every_workflow():
    result = summarize([
        event("workflow_start", 0.), event("llm_submit", 30_000.),
        event("llm_submit", 40_000.), event("workflow_end", 100_000.),
        event("workflow_start", 10_000., "wf2"),
        event("llm_submit", 50_000., "wf2"), event("workflow_end", 300_000., "wf2"),
    ])
    assert result["mean_startup_s"] == 35.
    assert result["mean_end_to_end_jct_s"] == 195.
    assert result["mean_after_first_submit_s"] == 160.
    assert result["workflow_event_makespan_s"] == 300.
    assert result["hypothetical_startup_removal_performance_measured"] is False


@pytest.mark.parametrize("change", ("missing_end", "missing_submit", "duplicate", "reversed"))
def test_incomplete_and_invalid_populations_are_rejected(change):
    rows = [
        event("workflow_start", 100.), event("llm_submit", 200.),
        event("workflow_end", 500.),
    ]
    if change == "missing_end":
        rows.pop()
    elif change == "missing_submit":
        rows.pop(1)
    elif change == "duplicate":
        rows.append(rows[0])
    else:
        rows[1]["ts_ms"] = 50.
    with pytest.raises(ValueError):
        summarize(rows)
