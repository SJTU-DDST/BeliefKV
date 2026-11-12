from copy import deepcopy

import pytest

from scripts.audit_bound_capacity_release_service import Observations, paired_paths, summarize
from scripts.audit_periodic_admission_decisions import inspect
from tests.test_free_dispatch_service_audit import evidence


def fixture():
    grants, _submits, batches, _receipts = evidence()
    grant = grants[0]
    grant["event"] = "agent_dispatch_free_capacity_admitted"
    consumer = grant["programs"][0]["key"]
    donor = {
        **consumer, "request_id": "donor", "root_workflow_id": "other",
        "invocation_id": "donor-agent", "context_id": "donor-context",
        "session_id": "donor-session",
    }
    release = {
        "event": "predicted_tool_kv_evicted", "ts_ms": 990.,
        "workflow_id": donor["root_workflow_id"],
        **{key: donor[key] for key in (
            "invocation_id", "context_id", "context_epoch", "session_id", "session_generation",
        )},
        "freed_units": {"0": 56}, "freed_bytes": 56 * 98304,
        "beneficiary_request_id": consumer["request_id"],
        "beneficiary_context_id": consumer["context_id"],
        "dispatch_stage": "event_tool_replacement",
    }
    marker = {
        "event": "agent_predicted_tool_replacement", "ts_ms": 1000.1,
        "monotonic_s": 1.0001, "consumer": consumer, "donor": donor,
        "remaining_before_tokens": grant["remaining_before_tokens"],
        "remaining_after_tokens": grant["remaining_after_tokens"],
        "coalescing_window_bypassed": True, "ordinary_restore_lease": False,
        "physical_capacity_authority": "settled_private_release_then_fresh_native_budget",
    }
    close = {
        "event": "agent_periodic_admission_decision", "ts_ms": 2000., "monotonic_s": 2.,
        "programs": [], "resumed_program_ids": [], "resume_capacity_tokens": 0,
        "physical_capacity_credit": False,
    }
    batches[0]["launch_monotonic_ms"] = 1005.
    batches[0]["request_samples"][0].update(token_delta=10, output_token_delta=0)
    return [release, grant, marker, close], batches


def analyze(records, batches):
    observed = Observations()
    progress = inspect(observed.records(records), batches, include_free_grants=True)
    return paired_paths(progress, observed), progress, observed


def test_last_matching_release_exact_grant_and_bounded_service_are_linked():
    rows, progress, observed = analyze(*fixture())
    assert len(progress["published_grant_interventions"]["links"]) == 1
    assert rows[0]["last_release_record_to_grant_ms"] == 10.
    assert rows[0]["grant_to_new_service_ms"] == 15.
    assert rows[0]["last_release_record_to_new_service_ms"] == 25.
    assert observed.counts["predicted_tool_kv_evicted_FULL_tokens"] == 56
    assert summarize(rows)["bounded_release_to_service_ms"]["p50"] == 25.


def test_periodic_start_to_publication_span_does_not_contaminate_free_grant_clock():
    records, batches = fixture()
    records[-1]["ts_ms"] += 58.
    rows, _progress, observed = analyze(records, batches)
    assert observed.offsets == [0.]
    assert observed.periodic_publication_offsets == [58.]
    assert rows[0]["last_release_record_to_new_service_ms"] == 25.


@pytest.mark.parametrize("field", (
    "workflow_id", "invocation_id", "context_id", "context_epoch", "session_id",
    "session_generation", "beneficiary_request_id", "beneficiary_context_id", "dispatch_stage",
))
def test_a_different_donor_epoch_or_consumer_cannot_prove_capacity_handoff(field):
    records, batches = fixture()
    records[0][field] = 99 if type(records[0][field]) is int else "foreign"
    rows, _progress, _observed = analyze(records, batches)
    assert not rows[0]["release_record_matched"]
    assert summarize(rows)["bounded_release_to_service_ms"]["count"] == 0


def test_equal_looking_bool_donor_epoch_is_not_an_integer_epoch():
    records, batches = fixture()
    records[0]["context_epoch"] = False
    records[2]["donor"]["context_epoch"] = 0
    rows, _progress, _observed = analyze(records, batches)
    assert not rows[0]["release_record_matched"]


def test_future_release_wall_time_is_rejected_even_when_journal_order_looks_valid():
    records, batches = fixture()
    records[0]["ts_ms"] = 1001.
    with pytest.raises(ValueError, match="publication"):
        analyze(records, batches)


def test_multiple_node_releases_use_last_publication_without_summing_waits():
    records, batches = fixture()
    another = {**deepcopy(records[0]), "ts_ms": 995., "freed_units": {"0": 100}}
    records.insert(1, another)
    rows, _progress, observed = analyze(records, batches)
    assert rows[0]["last_release_record_to_grant_ms"] == 5.
    assert rows[0]["last_record_freed_FULL_tokens"] == 100
    assert observed.counts["predicted_tool_kv_evicted_FULL_tokens"] == 156
    assert summarize(rows)["bounded_release_to_service_ms"]["sum"] == 20.


def test_one_physical_release_cannot_validate_two_replacement_episodes():
    records, batches = fixture()
    regrant, remarker = deepcopy(records[1]), deepcopy(records[2])
    regrant.update(monotonic_s=1.1, ts_ms=1100.)
    remarker.update(monotonic_s=1.1001, ts_ms=1100.1)
    records[-1:-1] = [regrant, remarker]
    rows, _progress, _observed = analyze(records, batches)
    assert len(rows) == 2
    assert rows[0]["release_record_matched"] and not rows[1]["release_record_matched"]


def test_service_after_next_control_is_kept_but_not_counted_as_bounded_consumption():
    records, batches = fixture()
    records[-1].update(ts_ms=1010., monotonic_s=1.01)
    rows, _progress, _observed = analyze(records, batches)
    assert rows[0]["grant_to_new_service_ms"] == 15.
    assert rows[0]["last_release_record_to_new_service_ms"] is None
    assert summarize(rows)["service_not_verified_in_grant_window"] == 1


@pytest.mark.parametrize("change", ("foreign_attempt", "old_launch", "zero_progress", "no_service"))
def test_unrelated_or_old_or_zero_progress_work_does_not_validate_the_release(change):
    records, batches = fixture()
    if change == "foreign_attempt":
        batches[0]["request_samples"][0]["attempt_id"] += 1
    elif change == "old_launch":
        batches[0]["launch_monotonic_ms"] = 999.
    elif change == "zero_progress":
        batches[0]["request_samples"][0]["token_delta"] = 0
    else:
        batches = []
    rows, _progress, _observed = analyze(records, batches)
    assert rows[0]["release_record_matched"]
    assert summarize(rows)["bounded_release_to_service_ms"]["count"] == 0


def test_native_only_releases_remain_counts_without_an_invented_consumer():
    records, batches = fixture()
    records[0]["event"] = "parent_pressure_demoted"
    rows, _progress, observed = analyze(records, batches)
    assert not rows[0]["release_record_matched"]
    assert observed.counts["parent_pressure_demoted_FULL_tokens"] == 56


def test_zero_release_is_not_a_capacity_event():
    records, batches = fixture()
    records[0]["freed_units"] = {"0": 0}
    rows, _progress, _observed = analyze(records, batches)
    assert not rows[0]["release_record_matched"]


def test_ambiguous_consumer_attempt_cannot_borrow_an_untagged_release_identity():
    records, batches = fixture()
    observed = Observations()
    progress = inspect(observed.records(records), batches, include_free_grants=True)
    foreign = deepcopy(progress["ready_grant_episodes"][0])
    foreign["key"] = list(foreign["key"])
    foreign["key"][5] += 1
    progress["ready_grant_episodes"].append(foreign)
    rows = paired_paths(progress, observed)
    assert rows[0]["status"] == "ambiguous_consumer_attempt_or_session_identity"
    assert not rows[0]["release_record_matched"]
