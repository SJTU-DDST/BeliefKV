from copy import deepcopy
import json

import pytest

from scripts.audit_join_reentry_service import identity
from scripts.audit_reference_join_grants import audit, inspect, validate_grant
from scripts.audit_sealed_wait_prefetch import SealedRecords


def grant(*, names=("child",), parent=False, when=100.):
    programs = [{
        "key": {
            "request_id": name + "-request", "root_workflow_id": "workflow",
            "invocation_id": name, "context_id": name + "-context",
            "context_epoch": 2, "attempt_id": 0,
            "session_id": name + "-session", "session_generation": 1,
        },
        "observed_context_tokens": 100, "complete_budget_tokens": 200,
    } for name in names]
    return {
        "event": "agent_join_free_capacity_admitted" if parent else "agent_join_siblings_coadmitted",
        "ts_ms": when, "monotonic_s": when / 1000., "join_id": "join",
        "programs": programs, "required_tokens": len(programs) * 200,
        "remaining_before_tokens": 1000, "remaining_after_tokens": 1000 - len(programs) * 200,
        "native_capacity_authority_preserved": True,
    }


def submit(key, *, rid=None, epoch=None):
    return {
        "kind": "llm_submit", "ts_ms": 60., "workflow_id": key["root_workflow_id"],
        "invocation_id": key["invocation_id"], "context_id": key["context_id"],
        "context_epoch": key["context_epoch"] if epoch is None else epoch,
        "attributes": {"request_id": rid or key["request_id"], "cached_tokens_host": 80},
    }


def receipt(event):
    rid = event["attributes"]["request_id"]
    return {
        "request_id": rid, "exact_tokens_verified": True,
        "native_receipt": {
            "request_id": rid, "identity": list(identity(event)), "clock_domain": "native",
            "session_id": event["invocation_id"] + "-session", "session_generation": 1,
        },
    }


def service(index, event, *, start):
    return {
        "event": "gpu_service_sample", "sample_id": f"native-service-{index:09d}",
        "service_start_ts_ms": start, "complete_ts_ms": start + 10.,
        "monotonic_clock_domain": "native",
        "request_samples": [{
            **{name: event[name] for name in ("workflow_id", "invocation_id", "context_id", "context_epoch")},
            "request_id": event["attributes"]["request_id"],
        }],
    }


def replay():
    return [{"kind": "workflow_start", "ts_ms": 0., "workflow_id": "workflow"}]


def parent_work():
    row = grant(names=("parent",), parent=True)
    event = submit(row["programs"][0]["key"], rid="parent-next", epoch=3)
    events = replay() + [
        {
            "kind": "join_wait", "ts_ms": 10000., "workflow_id": "workflow",
            "join_id": "join", "invocation_id": "parent",
        },
        {"kind": "join_satisfied", "ts_ms": 10001., "workflow_id": "workflow", "join_id": "join"},
        {**event, "ts_ms": 10002.},
    ]
    copy = {
        "source": "join_ticket", "workflow_id": "workflow", "invocation_id": "parent",
        "context_id": "parent-context", "join_id": "join", "submit_ts_ms": 110.,
        "first_service_request_id": "parent-next", "command_id": "copy",
        "full_first_service_reused": True, "pool_bytes": {"kv": 1234},
        "full_reuse_proof_version": 2,
        "full_use_witness": {"full_reuse_proof": "acknowledged_allocation_in_materialized_service_prefix"},
    }
    return row, event, events, copy


def test_group_service_requires_each_completed_current_native_session():
    row = grant(names=("first", "second"))
    events = [submit(item["key"]) for item in row["programs"]]
    observed = inspect(
        [row], replay(), events, [receipt(item) for item in events],
        [service(1, events[0], start=130.), service(2, events[1], start=160.)],
    )
    assert observed["summary"]["journal_grants"] == 1
    assert observed["summary"]["program_statuses"] == {"service_after_grant_verified": 2}
    assert observed["groups"][0]["newly_granted_service_start_spread_ms"] == 30.
    assert [item["grant_to_next_native_service_ms"] for item in observed["programs"]] == [30., 60.]
    assert all(item["attempt_identity_verified"] is False for item in observed["programs"])
    assert observed["summary"]["verified_reused_copy_bytes_after_grant"] is None


@pytest.mark.parametrize("change", ("session", "generation", "clock", "epoch", "no_receipt", "request_id"))
def test_unproven_native_identity_is_never_credited_as_verified_service(change):
    row = grant()
    event = submit(row["programs"][0]["key"])
    completed = receipt(event)
    sampled = service(1, event, start=130.)
    if change == "session":
        completed["native_receipt"]["session_id"] = "other"
    elif change == "generation":
        completed["native_receipt"]["session_generation"] = 2
    elif change == "clock":
        completed["native_receipt"]["clock_domain"] = "other"
    elif change == "epoch":
        sampled["request_samples"][0]["context_epoch"] = 3
    elif change == "request_id":
        completed["native_receipt"]["request_id"] = "other"
    observed = inspect(
        [row], replay(), [event], [] if change == "no_receipt" else [completed], [sampled],
    )
    assert "service_after_grant_verified" not in observed["summary"]["program_statuses"]
    assert len(observed["programs"]) == 1


def test_parent_association_uses_actual_join_successor_and_server_clock():
    row, event, events, copy = parent_work()
    earlier = {**event, "attributes": {"request_id": "unrelated"}, "ts_ms": 9999.}
    events.insert(2, earlier)
    observed = inspect(
        [row], events, [event], [receipt(event)], [service(1, event, start=130.)], copies=[copy],
    )
    program = observed["programs"][0]
    assert program["service_request_id"] == "parent-next"
    assert program["grant_to_next_native_service_ms"] == 30.
    assert program["verified_reused_copy_bytes_after_grant"] == 1234
    assert program["associated_copy_command_ids"] == ["copy"]
    assert observed["groups"][0]["newly_granted_service_start_spread_ms"] is None


def test_later_grant_bounds_earlier_service_and_copy_credit():
    first, event, events, copy = parent_work()
    second = deepcopy(first)
    second["ts_ms"], second["monotonic_s"] = 120., .12
    copy["submit_ts_ms"] = 125.
    observed = inspect(
        [first, second], events, [event], [receipt(event)],
        [service(1, event, start=130.)], copies=[copy],
    )
    assert observed["programs"][0]["status"] == "service_after_grant_not_observed"
    assert observed["programs"][0]["verified_reused_copy_bytes_after_grant"] == 0
    assert observed["programs"][1]["grant_to_next_native_service_ms"] == 10.
    assert observed["summary"]["verified_reused_copy_bytes_after_grant"] == 1234


def test_request_served_before_grant_does_not_claim_an_initial_service_improvement():
    row = grant()
    event = submit(row["programs"][0]["key"])
    observed = inspect(
        [row], replay(), [event], [receipt(event)],
        [service(1, event, start=90.), service(2, event, start=130.)],
    )
    program = observed["programs"][0]
    assert program["request_had_service_before_grant"] is True
    assert program["first_service_ts_ms"] == 90.
    assert program["grant_to_next_native_service_ms"] == 30.


def test_completed_request_without_service_after_grant_remains_uncredited():
    row = grant()
    event = submit(row["programs"][0]["key"])
    observed = inspect([row], replay(), [event], [receipt(event)], [service(1, event, start=90.)])
    program = observed["programs"][0]
    assert program["request_had_service_before_grant"] is True
    assert program["status"] == "service_after_grant_not_observed"
    assert "grant_to_next_native_service_ms" not in program


def test_partial_service_coverage_retains_pending_members_and_unknown_group_spread():
    row = grant(names=("first", "second"))
    events = [submit(item["key"]) for item in row["programs"]]
    observed = inspect([row], replay(), events, [receipt(events[0])], [service(1, events[0], start=130.)])
    assert observed["groups"][0]["verified_served_programs"] == 1
    assert observed["groups"][0]["newly_granted_programs"] == 2
    assert observed["groups"][0]["newly_granted_service_start_spread_ms"] is None


@pytest.mark.parametrize("change", ("legacy_proof", "unverified", "foreign_join", "other_request", "before_grant"))
def test_unverified_or_unrelated_copies_receive_no_grant_credit(change):
    row, event, events, copy = parent_work()
    if change == "legacy_proof":
        copy["full_reuse_proof_version"] = 1
    elif change == "unverified":
        copy["full_first_service_reused"] = False
    elif change == "foreign_join":
        copy["join_id"] = "another"
    elif change == "other_request":
        copy["first_service_request_id"] = "other"
    else:
        copy["submit_ts_ms"] = 99.
    observed = inspect([row], events, [event], [receipt(event)], [service(1, event, start=130.)], copies=[copy])
    assert observed["summary"]["verified_reused_copy_bytes_after_grant"] == 0


@pytest.mark.parametrize("change", ("negative", "sum", "duplicate", "workflow", "buffer", "boolean"))
def test_invalid_complete_budget_journals_are_rejected(change):
    row = grant()
    if change == "negative":
        row["remaining_after_tokens"] = -1
    elif change == "sum":
        row["required_tokens"] += 1
    elif change == "duplicate":
        row["programs"].append(deepcopy(row["programs"][0]))
    elif change == "workflow":
        other = deepcopy(row["programs"][0])
        other["key"].update(root_workflow_id="other", invocation_id="other", context_id="other")
        row["programs"].append(other)
    elif change == "buffer":
        row["programs"][0]["complete_budget_tokens"] += 1
    else:
        row["programs"][0]["key"]["context_epoch"] = False
    with pytest.raises(ValueError):
        validate_grant(row)


def test_copy_commands_cannot_be_credited_twice():
    row, event, events, copy = parent_work()
    with pytest.raises(ValueError, match="credited twice"):
        inspect([row], events, [event], [receipt(event)], [service(1, event, start=130.)], copies=[copy, copy])


@pytest.mark.parametrize("change", ("sequence", "clock", "duplicate_request"))
def test_corrupted_native_service_prefix_cannot_silently_supply_evidence(change):
    row = grant()
    event = submit(row["programs"][0]["key"])
    sampled = service(1, event, start=130.)
    if change == "sequence":
        sampled["sample_id"] = "native-service-000000002"
    elif change == "clock":
        sampled["monotonic_clock_domain"] = None
    else:
        sampled["request_samples"].append(deepcopy(sampled["request_samples"][0]))
    with pytest.raises(ValueError):
        inspect([row], replay(), [event], [receipt(event)], [sampled])


def test_audit_seals_five_collecting_inputs_and_keeps_incomplete_tail_out(tmp_path):
    row = grant()
    event = submit(row["programs"][0]["key"])
    inputs = {
        "opportunities/admission_opportunities.jsonl": [row],
        "replay/runtime_events.replay.jsonl": replay(),
        "server/runtime_events.sglang.jsonl": [event],
        "replay/native_requests.jsonl": [receipt(event)],
        "server/runtime_audit.jsonl": [service(1, event, start=130.)],
    }
    for name, records in inputs.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(item) + "\n" for item in records))
    path = tmp_path / "opportunities/admission_opportunities.jsonl"
    prefix = path.stat().st_size
    with path.open("a") as stream:
        stream.write('{"event":')
    observed = audit(tmp_path)
    assert len(observed["input_jsonl_prefixes"]) == 5
    assert observed["input_jsonl_prefixes"][str(path)]["complete_prefix_bytes"] == prefix
    assert observed["complete_run_verified"] is False
    assert observed["performance_accepted"] is False


def test_missing_grant_journal_is_not_reported_as_evidence_of_zero_opportunities():
    observed = inspect([], replay(), [], [], [])
    assert observed["summary"]["grant_refresh_declaration_snapshots"] == 0
    assert observed["summary"]["verified_reused_copy_bytes_after_grant"] is None


def test_imported_prefetch_snapshot_keeps_original_limits_for_relative_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path.parent)
    row = grant()
    event = submit(row["programs"][0]["key"])
    inputs = {
        "opportunities/admission_opportunities.jsonl": [row],
        "replay/runtime_events.replay.jsonl": replay(),
        "server/runtime_events.sglang.jsonl": [event],
        "replay/native_requests.jsonl": [receipt(event)],
        "server/runtime_audit.jsonl": [service(1, event, start=130.)],
    }
    reader = SealedRecords()
    for name, records in inputs.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(item) + "\n" for item in records))
        list(reader(path.relative_to(tmp_path.parent)))
    evidence = tmp_path / "prefetch.json"
    evidence.write_text(json.dumps({
        "arm": str(tmp_path.relative_to(tmp_path.parent)),
        "rows": [], "input_jsonl_prefixes": reader.prefixes,
    }))
    path = tmp_path / "opportunities/admission_opportunities.jsonl"
    later = grant(when=200.)
    with path.open("a") as stream:
        stream.write(json.dumps(later) + "\n")
    observed = audit(tmp_path, prefetch_report=evidence)
    assert observed["summary"]["journal_grants"] == 1
    assert observed["summary"]["verified_reused_copy_bytes_after_grant"] == 0
    assert len(observed["input_jsonl_prefixes"]) == 5
    expected = reader.prefixes[str(path.relative_to(tmp_path.parent))]
    assert observed["input_jsonl_prefixes"][str(path)] == expected
