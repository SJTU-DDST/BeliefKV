from copy import deepcopy

import pytest

from scripts.audit_selected_service_identity_coverage import inspect


def fixture():
    key = ("request", "workflow", "agent", "context", 0, 0, "session", 1)
    selected = {"decisions": [{
        "raw_journal_sequence": 3, "all_workflows_bounded_served": True,
        "shortest_delay_after_first_ms": 0.,
        "workflows": [{"first_bounded_wait_ms": 100., "members": [{"key": key}]}],
    }]}
    labelled = {"decisions": [{"raw_journal_sequence": 3, "monotonic_s": 1.}]}
    batch = dict(
        event="gpu_service_sample", phase="prefill",
        launch_monotonic_ms=1020., service_start_monotonic_ms=1030.,
        request_samples=[{
            "request_id": "request", "workflow_id": "workflow", "invocation_id": "agent",
            "context_id": "context", "context_epoch": 0,
            "attempt_id": 0, "session_id": "session",
            "token_delta": 8, "output_token_delta": 0,
        }],
    )
    return selected, labelled, batch


def test_earlier_partial_positive_progress_excludes_first_service_order_claim():
    selected, labelled, batch = fixture()
    before = deepcopy((selected, labelled, batch))
    result = inspect(selected, labelled, [batch])
    assert result["counts"]["windows_with_earlier_uncertain_or_uncredited_service"] == 1
    assert not result["counts"].get("clean_shortest_in_first_wave")
    assert (selected, labelled, batch) == before


@pytest.mark.parametrize("kind", ("foreign_attempt", "zero_progress", "earlier_launch", "later_service"))
def test_irrelevant_native_samples_do_not_invalidate_exact_bounded_service(kind):
    selected, labelled, batch = fixture()
    sample = batch["request_samples"][0]
    if kind == "foreign_attempt":
        sample["attempt_id"] = 99
    elif kind == "zero_progress":
        sample["token_delta"] = 0
    elif kind == "earlier_launch":
        batch["launch_monotonic_ms"] = 999.
    else:
        batch["service_start_monotonic_ms"] = 1100.
    result = inspect(selected, labelled, [batch])
    assert result["counts"]["windows_without_earlier_uncertain_or_uncredited_service"] == 1
    assert result["counts"]["clean_shortest_in_first_wave"] == 1


@pytest.mark.parametrize("kind", ("unknown_progress", "exact_uncredited"))
def test_unknown_progress_and_earlier_exact_work_remain_separate_uncertainty_reasons(kind):
    selected, labelled, batch = fixture()
    sample = batch["request_samples"][0]
    if kind == "unknown_progress":
        del sample["output_token_delta"]
    else:
        sample["session_generation"] = 1
    result = inspect(selected, labelled, [batch])
    reason = next(iter(result["ambiguous_windows"][0]["earlier_evidence"]))
    assert reason.startswith("unknown_progress" if kind == "unknown_progress" else "exact_positive_progress")
