from copy import deepcopy

import pytest

from scripts.audit_continuation_service_competition import service_competition


def submit(request, context, queued, *, host=0, epoch=0):
    return {
        "kind": "llm_submit", "ts_ms": queued, "workflow_id": "workflow",
        "invocation_id": context, "context_id": context, "context_epoch": epoch,
        "attributes": {
            "request_id": request, "prompt_tokens": 8192,
            "cached_tokens_host": host, "cached_tokens_device": 8000,
        },
    }


def complete(request, timestamp):
    return {
        "kind": "llm_result", "ts_ms": timestamp,
        "attributes": {"request_id": request, "output_tokens": 10},
    }


def observations():
    return [
        submit("a1", "a", 0), submit("b1", "b", 1), complete("a1", 10),
        # This request queued before a1 completed, but starts service after it.
        submit("c1", "c", 3), complete("c1", 12),
        submit("c2", "c", 13), submit("a2", "a", 11, host=128),
        complete("a2", 25), complete("b1", 26), complete("c2", 27),
    ]


def test_first_service_order_and_repeated_contexts_are_not_queue_order():
    result = service_competition(observations(), 11, 12)
    row = result["rows"][0]
    assert row["request_id"] == "a2"
    assert row["distinct_known_other_contexts"] == 1
    assert row["other_request_starts"] == 2
    assert row["completion_to_queue_ms"] == 1
    assert result["rapid_same_epoch"]["positive_full_host_fraction_of_known"] == 1
    assert result["rapid_same_context_by_competition"]["0_to_15"]["requests"] == 1


def test_unknown_predecessor_is_not_skipped_in_favor_of_an_older_completed_one():
    rows = observations()
    rows.insert(5, submit("a-pending", "a", 10.5))
    result = service_competition(rows, 11, 12)
    assert result["rows"][0]["preceding_status"] == "previous_completion_unobserved"
    assert result["rows"][0]["distinct_known_other_contexts"] is None
    assert result["rapid_same_epoch"]["requests"] == 0


def test_epoch_change_is_observed_but_not_in_same_epoch_subcohort():
    rows = observations()
    rows[6]["context_epoch"] = 1
    result = service_competition(rows, 11, 12)
    assert result["rows"][0]["same_context_epoch"] is False
    assert result["rows"][0]["distinct_known_other_contexts"] == 1
    assert result["rapid_same_epoch"]["requests"] == 0
    assert result["rapid_same_context"]["requests"] == 1
    assert result["rows"][0]["previous_context_epoch"] == 0
    assert result["context_epoch_transition_counts"] == {"advanced": 1}


@pytest.mark.parametrize(
    ("previous_epoch", "epoch", "transition"),
    [(0, None, "unknown"), (2, 1, "regressed")],
)
def test_epoch_transition_never_drops_a_logical_continuation(
    previous_epoch, epoch, transition
):
    rows = observations()
    rows[0]["context_epoch"] = previous_epoch
    rows[6]["context_epoch"] = epoch
    result = service_competition(rows, 11, 12)
    assert result["rapid_same_context"]["requests"] == 1
    assert result["rapid_same_epoch"]["requests"] == 0
    assert result["context_epoch_transition_counts"] == {transition: 1}


def test_unknown_competing_identity_is_not_a_known_zero():
    rows = observations()
    rows[3]["context_id"] = None
    result = service_competition(rows, 11, 12)
    assert result["rows"][0]["unknown_context_request_starts"] == 1
    assert result["rapid_same_epoch"]["requests"] == 1
    assert result["rapid_same_context_known_competition"]["requests"] == 0


def test_completion_timestamp_cannot_be_future_information_at_queue_time():
    rows = observations()
    rows[6]["ts_ms"] = 9
    result = service_competition(rows, 9, 10)
    assert result["rows"][0]["preceding_status"] == "completion_after_queue_registration"
    assert result["rows"][0]["completion_to_queue_ms"] is None


@pytest.mark.parametrize("kind", ["duplicate_start", "duplicate_completion", "missing_start", "bad_clock", "bad_tokens"])
def test_malformed_native_evidence_cannot_be_counted_as_a_hit_or_miss(kind):
    rows = deepcopy(observations())
    if kind == "duplicate_start":
        rows.insert(1, deepcopy(rows[0]))
    elif kind == "duplicate_completion":
        rows.append(deepcopy(rows[-1]))
    elif kind == "missing_start":
        rows.insert(0, complete("unseen", 1))
    elif kind == "bad_clock":
        rows[0]["ts_ms"] = float("nan")
    else:
        rows[0]["attributes"]["cached_tokens_host"] = True
    with pytest.raises(ValueError):
        service_competition(rows, 11, 12)


def test_missing_completion_coverage_is_not_an_empty_window():
    with pytest.raises(ValueError, match="do not cover"):
        service_competition(observations(), 30, 40)
    with pytest.raises(ValueError, match="invalid competition window"):
        service_competition(observations(), 10, 10)
