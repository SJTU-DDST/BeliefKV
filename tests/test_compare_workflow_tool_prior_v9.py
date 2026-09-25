import pytest

from scripts.compare_workflow_tool_prior_v9 import _local_priors


def test_local_prior_requires_strictly_completed_successes_in_same_workflow():
    rows = [
        ("first", "wf", 0, 100, "success"),
        ("second", "wf", 100, 300, "success"),
        ("failed", "wf", 400, 600, "error"),
        ("third", "wf", 700, 900, "success"),
        ("other", "other_wf", 1_000, 1_200, "success"),
        ("target", "wf", 1_300, 1_400, "success"),
    ]
    waits = [{
        "tool_call_id": call, "workflow_id": workflow,
        "observed_command_class": "test_suite",
        "start_ts_ms": start, "terminal_ts_ms": end, "status": status,
    } for call, workflow, start, end, status in rows]
    assert _local_priors(waits, minimum_support=3) == {
        ("wf", "target"): (200, 3)
    }
    with pytest.raises(ValueError):
        _local_priors(waits, minimum_support=1)
