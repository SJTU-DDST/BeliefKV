from copy import deepcopy

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import CausalReplayPlan, ReplayNode
from scripts.audit_causal_jct_paths import account, first_launches


def fixture():
    def event(name, kind, when, invocation=None, request=None):
        return RuntimeEvent(
            name, when, kind, "w", invocation_id=invocation,
            context_id="context-" + invocation if invocation else None,
            context_epoch=0 if invocation else None,
            attributes={"request_id": request} if request else {},
        )

    events = [
        event("start", Kind.WORKFLOW_START, 0.),
        event("submit-a", Kind.LLM_SUBMIT, 1., "a", "a"),
        event("submit-b", Kind.LLM_SUBMIT, 2., "b", "b"),
        event("join", Kind.JOIN_SATISFIED, 32., "parent"),
        event("submit-p", Kind.LLM_SUBMIT, 34., "parent", "p"),
        event("end", Kind.WORKFLOW_END, 41.),
    ]
    predecessors = {
        "start": (), "submit-a": ("start",), "submit-b": ("start",),
        "join": ("request-a", "request-b"), "submit-p": ("join",),
        "end": ("request-p",),
    }
    nodes = [ReplayNode(row.event_id, "w", predecessors[row.event_id], 0., event=row)
             for row in events]
    nodes.extend(ReplayNode(
        "request-" + request, "w", ("submit-" + request,), 0., request_id=request,
    ) for request in ("a", "b", "p"))
    plan = CausalReplayPlan(tuple(nodes), {request: (2, 1) for request in ("a", "b", "p")}, True, 0)
    requests = []
    for request, invocation, submitted, complete in (
        ("a", "a", 1., 20.), ("b", "b", 3., 30.), ("p", "parent", 34.5, 40.),
    ):
        requests.append({
            "request_id": request, "submitted_monotonic_ms": submitted,
            "native_complete_monotonic_ms": complete,
            "http_complete_monotonic_ms": complete + .1,
            "exact_tokens_verified": True, "prompt_tokens": 2, "output_tokens": 1,
            "native_receipt": {
                "request_id": request, "complete_monotonic_ms": complete,
                "clock_domain": "clock", "identity": ["w", invocation, "context-" + invocation, 0],
            },
        })
    return plan, [row.to_dict() for row in events], requests, {"a": 2., "b": 10., "p": 36.}


def test_parallel_siblings_count_only_the_last_completing_dependency_path():
    plan, events, requests, launches = fixture()
    result = account(
        plan, events, requests, launches, expected_jct_ms={"w": 41.},
        cohorts={"b": "post_tool", "p": "post_join"},
    )
    row = result["workflows"]["w"]
    assert row["critical_path_requests"] == ["b", "p"]
    assert sum(row["components_ms"].values()) == row["jct_ms"] == 41.
    assert row["components_ms"]["request_dispatch"] == 1.5
    assert row["components_ms"]["request_submit_to_first_launch"] == 8.5
    assert row["components_ms"]["request_first_launch_to_native_complete"] == 24.
    assert row["critical_path_request_counts_by_cohort"] == {"post_join": 1, "post_tool": 1}
    assert sum(row["request_components_ms_by_cohort"]["post_tool"].values()) == 28.


def test_each_arm_can_have_a_different_observed_blocking_branch():
    plan, events, requests, launches = fixture()
    requests[0]["native_complete_monotonic_ms"] = 31.
    requests[0]["http_complete_monotonic_ms"] = 31.1
    requests[0]["native_receipt"]["complete_monotonic_ms"] = 31.
    row = account(
        plan, events, requests, launches, expected_jct_ms={"w": 41.},
    )["workflows"]["w"]
    assert row["critical_path_requests"] == ["a", "p"]
    assert row["jct_ms"] == 41.


@pytest.mark.parametrize("change", (
    "duplicate_event", "foreign_identity", "mixed_clock", "missing_launch",
    "early_join", "late_launch", "wrong_jct", "wrong_work",
))
def test_invalid_population_clock_or_causal_evidence_is_rejected(change):
    plan, events, requests, launches = fixture()
    expected = {"w": 41.}
    if change == "duplicate_event":
        events.append(deepcopy(events[0]))
    elif change == "foreign_identity":
        requests[0]["native_receipt"]["identity"][0] = "other"
    elif change == "mixed_clock":
        requests[0]["native_receipt"]["clock_domain"] = "other-clock"
    elif change == "missing_launch":
        del launches["a"]
    elif change == "early_join":
        events[3]["ts_ms"] = 29.
    elif change == "late_launch":
        launches["a"] = 21.
    elif change == "wrong_jct":
        expected["w"] = 42.
    else:
        requests[0]["output_tokens"] = 2
    with pytest.raises(ValueError):
        account(plan, events, requests, launches, expected_jct_ms=expected)


def launch_fixture():
    _, _, requests, launches = fixture()
    rows = []
    for index, request in enumerate(requests, 1):
        workflow, invocation, context, epoch = request["native_receipt"]["identity"]
        rows.append({
            "event": "gpu_service_sample", "sample_id": f"native-service-{index:09d}",
            "monotonic_clock_domain": "clock",
            "launch_monotonic_ms": launches[request["request_id"]],
            "complete_monotonic_ms": launches[request["request_id"]] + .1,
            "request_samples": [{
                "request_id": request["request_id"], "workflow_id": workflow,
                "invocation_id": invocation, "context_id": context, "context_epoch": epoch,
            }],
        })
    return rows, {row["request_id"]: row for row in requests}, launches


def test_first_launch_requires_complete_sequential_native_samples_and_exact_identity():
    rows, requests, expected = launch_fixture()
    assert first_launches(rows, requests, expected_records=3) == expected


@pytest.mark.parametrize("change", ("clock", "identity", "sample_gap", "count", "request_missing"))
def test_incomplete_launch_evidence_cannot_be_called_queue_time(change):
    rows, requests, _ = launch_fixture()
    count = 3
    if change == "clock":
        rows[0]["monotonic_clock_domain"] = "foreign"
    elif change == "identity":
        rows[0]["request_samples"][0]["context_epoch"] = 1
    elif change == "sample_gap":
        rows[1]["sample_id"] = "native-service-000000004"
    elif change == "count":
        count = 4
    else:
        rows[0]["request_samples"] = []
    with pytest.raises(ValueError):
        first_launches(rows, requests, expected_records=count)
