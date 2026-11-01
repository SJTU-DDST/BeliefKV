import pytest

from scripts.audit_tool_stage_alternatives import matching_choice, next_stage
from scripts.audit_free_initial_admission_delay import first_following, merged_duration


def event(kind, when, *, rid=None, join=None):
    return {
        "kind": kind, "ts_ms": when, "workflow_id": "w", "invocation_id": "i",
        "attributes": {"request_id": rid} if rid else {}, "join_id": join,
    }


def source(*requests):
    return {rid: {"input_increment": 100, "output_tokens": 20} for rid in requests}


def tool(origin, start, end):
    return origin, {"tool_wait_start_ts_ms": start, "client_ready_ts_ms": end}


def test_short_tools_are_charged_before_a_real_long_tool_release_boundary():
    rows = [
        event("llm_submit", 0, rid="a"), event("tool_start", 1),
        event("tool_start", 2), event("tool_end", 100),
        event("llm_submit", 100, rid="b"), event("tool_start", 101),
        event("return", 10000),
    ]
    result = next_stage(rows, "a", source("a", "b"), dict((
        tool("a", 1, 101), tool("b", 101, 5101),
    )), {}, .02, 2000.)
    assert result["boundary_kind"] == "long_tool" and result["observed_boundary_ms"] == 101
    assert result["requests"] == ["a", "b"] and result["observed_short_tool_wait_ms"] == 100
    assert result["phase_cost_proxy_ms"] == pytest.approx(940.)
    assert not result["boundary_is_actual_physical_release"]


def test_already_satisfied_join_does_not_become_a_cold_phase_boundary():
    rows = [
        event("llm_submit", 0, rid="a"), event("join_wait", 10, join="j"),
        event("llm_submit", 11, rid="b"), event("return", 20),
    ]
    result = next_stage(rows, "a", source("a", "b"), {}, {("w", "j"): 10.5}, .02, 2000.)
    assert result["boundary_kind"] == "return" and result["requests"] == ["a", "b"]
    result = next_stage(rows, "a", source("a", "b"), {}, {("w", "j"): 30.}, .02, 2000.)
    assert result["boundary_kind"] == "blocked_join" and result["requests"] == ["a"]


@pytest.mark.parametrize("barrier,reason", (
    ("call", "foreground_child_path_not_accounted"),
    ("invocation_cancel", "cancelled_not_normal_return"),
    ("tool_start", "tool_round_not_resolved"),
    ("join_wait", "join_completion_unobserved"),
))
def test_unresolved_or_cancelled_work_does_not_get_an_optimistic_zero_cost(barrier, reason):
    result = next_stage(
        [event("llm_submit", 0, rid="a"), event(barrier, 1, join="j")],
        "a", source("a"), {}, {}, .02, 2000.,
    )
    assert result == {"supported": False, "reason": reason}


def test_an_incomplete_phase_is_censored_even_when_current_model_work_is_known():
    assert next_stage(
        [event("llm_submit", 0, rid="a")], "a", source("a"), {}, {}, .02, 2000.,
    ) == {"supported": False, "reason": "stage_boundary_censored"}


def key(generation=1):
    return {
        "request_id": "a", "root_workflow_id": "w", "invocation_id": "i", "context_id": "c",
        "context_epoch": 0, "attempt_id": 0, "session_id": "s", "session_generation": generation,
    }


def test_choice_match_keeps_attempt_session_and_time_bounded():
    from scripts.audit_free_dispatch_service import identity

    exact = identity(key(), grant=True)
    choices = {exact: [{"grant_monotonic_s": 1.}]}
    assert matching_choice(choices, {"consumer": key(), "monotonic_s": 1.01}) is choices[exact][0]
    assert matching_choice(choices, {"consumer": key(2), "monotonic_s": 1.01}) is None
    assert matching_choice(choices, {"consumer": key(), "monotonic_s": 1.1}) is None
    with pytest.raises(ValueError, match="ambiguous"):
        matching_choice({exact: choices[exact] * 2}, {"consumer": key(), "monotonic_s": 1.01})


def test_initial_waits_for_parallel_siblings_are_unioned_before_workflow_aggregation():
    assert merged_duration([(1., 4.), (2., 5.), (7., 9.)]) == 6.
    assert merged_duration([]) == 0.


def test_initial_grant_matching_never_uses_an_earlier_grant_for_the_same_exact_key():
    assert first_following([1., 2., 4.], 2.) == 2.
    assert first_following([1., 2., 4.], 3.) == 4.
    assert first_following([1., 2., 4.], 5.) is None
