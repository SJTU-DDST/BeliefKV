from copy import deepcopy

import pytest

from scripts.audit_tool_eos_guard_cases import inspect


def case(name="child"):
    return {
        "child_request_id": name + "-old",
        "next_observed_request_id": name + "-next",
        "identity": ["workflow", name, name + "-context", 0],
        "output_sha256": name + "-hash", "output_tokens": 2,
    }


def receipt(target, *, following=False):
    request = target["next_observed_request_id"] if following else target["child_request_id"]
    identity = list(target["identity"])
    identity[3] += int(following)
    return {
        "request_id": request, "exact_tokens_verified": True,
        "output_sha256": target["output_sha256"], "output_tokens": 2,
        "native_receipt": {
            "request_id": request, "identity": identity, "clock_domain": "native",
            "complete_monotonic_ms": 2000. if following else 1000.,
            "native_finish_reason": {"type": "stop"},
            "output_token_ids": [151658, 151645],
        },
    }


def snapshot(when=4000., window=1000.):
    return {
        "event": "admission_runtime_state", "physical_disabled": False,
        "monotonic_ms": when, "eos_prefetch_evidence_window_ms": window,
    }


def observed(targets, receipts, states, *, clock="native"):
    return inspect(
        targets, receipts, states,
        opener_ids={151657}, closer_ids={151658}, clock_domain=clock,
    )


def test_completed_counterexample_requires_completed_successor_and_later_fifo_frontier():
    target = case()
    result = observed(
        [target], [receipt(target), receipt(target, following=True)], [snapshot()],
    )
    assert result["summary"]["all_cases_verified"] is True
    assert result["cases"][0]["status"] == "completed_tool_response_without_final_eos_trigger"
    assert result["cases"][0]["following_native_complete_monotonic_ms"] == 2000.


@pytest.mark.parametrize("stage", ("not_reached", "no_successor", "no_frontier", "frontier_too_early", "at_deadline"))
def test_absent_trigger_alone_is_never_a_pass(stage):
    target = case()
    receipts = [] if stage == "not_reached" else [receipt(target)]
    if stage not in ("not_reached", "no_successor"):
        receipts.append(receipt(target, following=True))
    states = (
        [] if stage == "no_frontier" else
        [snapshot(2500. if stage == "frontier_too_early" else 3000. if stage == "at_deadline" else 4000.)]
    )
    result = observed([target], receipts, states)
    assert result["summary"]["all_cases_verified"] is False
    assert result["cases"][0]["observed_final_eos_trigger_count"] == 0


def test_old_false_trigger_is_retained_as_failure_with_matching_output():
    target = case()
    trigger = {
        "event": "final_stage_latest_start", "trigger_kind": "observed_no_tool_eos",
        "child_request_id": target["child_request_id"],
    }
    result = observed(
        [target], [receipt(target), receipt(target, following=True)], [trigger, snapshot()],
    )
    assert result["summary"]["all_cases_verified"] is False
    assert result["cases"][0]["status"] == "false_final_eos_trigger_recorded"
    assert result["cases"][0]["trigger_records"] == [trigger]


@pytest.mark.parametrize("where", ("current", "following", "reader"))
def test_unknown_or_changed_monotonic_domain_cannot_settle_absence(where):
    target = case()
    current, following = receipt(target), receipt(target, following=True)
    if where in ("current", "following"):
        (current if where == "current" else following)["native_receipt"]["clock_domain"] = "other-host"
    result = observed(
        [target], [current, following], [snapshot()], clock=None if where == "reader" else "native",
    )
    assert result["summary"]["all_cases_verified"] is False
    assert result["cases"][0]["status"] == "native_clock_domain_unproven"


@pytest.mark.parametrize("change", ("hash", "token_count", "identity", "opener", "no_closer", "stop", "unverified"))
def test_changed_work_cannot_make_a_known_tool_case_disappear(change):
    target = case()
    current = receipt(target)
    if change == "hash":
        current["output_sha256"] = "changed"
    elif change == "token_count":
        current["output_tokens"] += 1
    elif change == "identity":
        current["native_receipt"]["identity"][2] = "other-context"
    elif change == "opener":
        current["native_receipt"]["output_token_ids"][0] = 151657
    elif change == "no_closer":
        current["native_receipt"]["output_token_ids"][0] = 42
    elif change == "stop":
        current["native_receipt"]["native_finish_reason"]["type"] = "length"
    else:
        current["exact_tokens_verified"] = False
    with pytest.raises(ValueError, match="counterexample"):
        observed([target], [current], [snapshot()])


@pytest.mark.parametrize("change", ("context", "epoch", "request", "clock_order"))
def test_following_completion_must_prove_the_same_causal_agent_progressed(change):
    target = case()
    following = receipt(target, following=True)
    if change == "context":
        following["native_receipt"]["identity"][2] = "other"
    elif change == "epoch":
        following["native_receipt"]["identity"][3] = 0
    elif change == "request":
        following["native_receipt"]["request_id"] = "other"
    else:
        following["native_receipt"]["complete_monotonic_ms"] = 900.
    with pytest.raises(ValueError, match="following"):
        observed([target], [receipt(target), following], [snapshot()])


@pytest.mark.parametrize("window", (None, 0., -1.))
def test_missing_eos_window_remains_unknown(window):
    target = case()
    result = observed(
        [target], [receipt(target), receipt(target, following=True)], [snapshot(window=window)],
    )
    assert result["summary"]["all_cases_verified"] is False


def test_partial_case_coverage_is_not_full_guard_verification():
    targets = [case("first"), case("second")]
    result = observed(
        targets, [receipt(targets[0]), receipt(targets[0], following=True)], [snapshot()],
    )
    assert result["summary"]["known_cases"] == 2
    assert result["summary"]["all_cases_verified"] is False
    assert result["summary"]["statuses"] == {
        "completed_tool_response_without_final_eos_trigger": 1,
        "request_not_completed_in_prefix": 1,
    }


@pytest.mark.parametrize("change", ("duplicate_case", "duplicate_receipt", "backward_frontier", "unhealthy"))
def test_corrupt_or_unhealthy_observations_are_not_success(change):
    target = case()
    targets, receipts, states = [target], [receipt(target)], [snapshot()]
    if change == "duplicate_case":
        targets.append(deepcopy(target))
    elif change == "duplicate_receipt":
        receipts.append(deepcopy(receipts[0]))
    elif change == "backward_frontier":
        states.append(snapshot(3000.))
    else:
        states[0]["physical_disabled"] = True
    with pytest.raises(ValueError):
        observed(targets, receipts, states)
