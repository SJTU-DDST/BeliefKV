from scripts.pilot_tool_peer_survival import replay


def _row(
    duration: int, peers: int, *, workflow: str = "wf",
    prior_status: str | None = None, support: int = 0,
) -> dict:
    return {
        "project": "project",
        "workflow": workflow,
        "is_child": True,
        "previous": (2000, 100, prior_status) if prior_status else None,
        "project_class_completed_support": support,
        "other_workflow_2s_peers": peers,
        "duration_ms": duration,
    }


def test_peer_lower_bound_reports_false_positive_and_separate_eta_error():
    report = replay([
        _row(3000, 4), _row(100, 4, workflow="other"),
        _row(4000, 0),
        _row(3000, 8, prior_status="success"),
        _row(3000, 4, support=16),
    ])
    assert report["eligible_completed_cold_child"] == 3
    assert report["eligible_true_at_least_2s"] == 2
    assert report["predicted_at_least_2s"] == 2
    assert report["precision"] == .5
    assert report["recall"] == .5
    assert report["constant_2s_point_error_true_positive"][
        "p50_error_ms"
    ] == 1000


def test_peer_lower_bound_rejects_invalid_support():
    assert replay([_row(4000, 4, support=0.5)])[
        "eligible_completed_cold_child"
    ] == 0
