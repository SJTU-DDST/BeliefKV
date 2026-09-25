from scripts.compare_cross_workflow_input_timing import _cross_workflow_priors


def _wait(workflow, start, end, *, status="success"):
    return {
        "workflow_id": workflow, "tool_call_id": f"{workflow}-{start}",
        "project": "project", "tool_name": "glob",
        "parameter_signature": "a" * 64,
        "start_ts_ms": start, "terminal_ts_ms": end, "status": status,
    }


def test_cross_workflow_history_requires_completed_prior_other_workflows():
    waits = [
        _wait("a", 0, 30),
        _wait("b", 20, 45),
        _wait("a", 35, 40),
        _wait("c", 46, 65),
    ]
    history = _cross_workflow_priors(waits, minimum_support=1)
    assert ("b", "b-20") not in history
    assert ("a", "a-35") not in history
    assert history["c", "c-46"] == (25, 3)


def test_cross_workflow_history_rejects_failed_and_same_workflow():
    waits = [
        _wait("a", 0, 20, status="error"),
        _wait("a", 25, 40),
        _wait("a", 45, 80),
        _wait("b", 90, 120),
    ]
    history = _cross_workflow_priors(waits, minimum_support=1)
    assert ("a", "a-45") not in history
    assert history["b", "b-90"] == (25, 2)
