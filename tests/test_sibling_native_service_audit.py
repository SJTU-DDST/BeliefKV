from copy import deepcopy
import json
import sys

import pytest

from scripts.audit_sibling_native_service import inspect


def fixture():
    keys = [
        {
            "request_id": request, "root_workflow_id": "wf", "invocation_id": child,
            "context_id": "context-" + child, "context_epoch": 0, "attempt_id": 0,
            "session_id": "session-" + child, "session_generation": 1,
        } for request, child in (("r1", "a"), ("r2", "b"))
    ]
    journal = {
        "event": "agent_join_siblings_native_service", "join_id": "join",
        "ts_ms": 100019., "monotonic_s": .019, "keys": keys,
        "request_ids": ["r1", "r2"],
        "ready_to_selected_ms": [1., 2.], "ready_to_forward_return_ms": [10., 11.],
    }
    events = [
        {
            "kind": "join_create", "workflow_id": "wf", "join_id": "join",
            "ts_ms": 1., "attributes": {"mode": "all"},
            "member_invocation_ids": ["a", "b"], "confidence": "declared_runtime",
        },
        {
            "kind": "join_wait", "workflow_id": "wf", "join_id": "join",
            "ts_ms": 2., "invocation_id": "parent", "confidence": "declared_runtime",
        },
    ]
    members = [
        {
            "request_id": key["request_id"], "workflow_id": "wf",
            "invocation_id": key["invocation_id"], "context_id": key["context_id"],
            "context_epoch": 0,
        } for key in keys
    ]
    services = [{
        "event": "gpu_service_sample", "sample_id": "native-service-000000001",
        "forward_iter": 1, "phase": "prefill", "batch_size": 2,
        "request_samples": members, "monotonic_clock_domain": "clock",
        "launch_monotonic_ms": 11., "service_start_monotonic_ms": 11.,
        "complete_monotonic_ms": 20., "complete_ts_ms": 100020.,
    }]
    receipts = [
        {
            "request_id": key["request_id"], "exact_tokens_verified": True,
            "submitted_monotonic_ms": 9., "native_complete_monotonic_ms": 30.,
            "native_receipt": {
                "request_id": key["request_id"],
                "identity": ["wf", key["invocation_id"], key["context_id"], 0],
                "clock_domain": "clock", "session_id": key["session_id"],
                "session_generation": 1,
            },
        } for key in keys
    ]
    return [journal], events, receipts, services


def test_same_real_batch_and_completed_sessions_verify_explicit_blocking_join():
    result = inspect(*fixture())
    assert result["summary"]["verified_multi_member_batches"] == 1
    assert result["summary"]["verified_request_attempts"] == 2
    assert result["summary"]["positive_service_evidence"]
    assert result["records"][0]["request_had_earlier_native_launch"] == [False, False]
    assert result["records"][0]["attempt_identity_independently_verified"] is False


@pytest.mark.parametrize("missing", ("native", "join", "completion"))
def test_missing_collecting_evidence_remains_unproven(missing):
    args = list(fixture())
    args[{"native": 3, "join": 1, "completion": 2}[missing]] = []
    result = inspect(*args)
    assert not result["summary"]["positive_service_evidence"]
    assert not result["summary"]["all_journal_records_verified"]


@pytest.mark.parametrize("change", (
    "session", "generation", "worker_identity", "clock", "not_member",
    "inferred_join", "not_all", "not_parallel", "wait_after_selection",
    "already_satisfied", "selected_after_launch", "clock_offset",
    "missing_worker_identity", "boolean_receipt_generation", "stale_completed_member",
))
def test_indirect_or_contradictory_evidence_never_proves_common_service(change):
    journals, events, receipts, services = fixture()
    if change == "session":
        receipts[0]["native_receipt"]["session_id"] = "different"
    elif change == "generation":
        receipts[0]["native_receipt"]["session_generation"] = 2
    elif change == "worker_identity":
        services[0]["request_samples"][0]["context_epoch"] = 1
    elif change == "missing_worker_identity":
        services[0]["request_samples"][0].pop("invocation_id")
    elif change == "boolean_receipt_generation":
        receipts[0]["native_receipt"]["session_generation"] = True
    elif change == "stale_completed_member":
        receipts[0]["native_complete_monotonic_ms"] = 15.
    elif change == "clock":
        receipts[0]["native_receipt"]["clock_domain"] = "other"
    elif change == "not_member":
        events[0]["member_invocation_ids"] = ["a", "other"]
    elif change == "inferred_join":
        events[0]["confidence"] = "inferred"
    elif change == "not_all":
        events[0]["attributes"]["mode"] = "any"
    elif change == "not_parallel":
        events[0]["member_invocation_ids"] = ["a"]
    elif change == "wait_after_selection":
        events[1]["ts_ms"] = 12.
    elif change == "already_satisfied":
        events.append({**events[1], "kind": "join_satisfied", "ts_ms": 5.})
    elif change == "selected_after_launch":
        journals[0]["ready_to_selected_ms"] = [4., 5.]
    else:
        journals[0]["ts_ms"] += 10.
    result = inspect(journals, events, receipts, services)
    assert result["summary"]["verified_records"] == 0


@pytest.mark.parametrize("change", (
    "duplicate_request", "duplicate_journal", "bad_wait", "duplicate_worker",
    "duplicate_forward", "boolean_generation",
))
def test_malformed_or_duplicated_evidence_fails(change):
    args = list(fixture())
    if change == "duplicate_request":
        args[0][0]["request_ids"][1] = "r1"
    elif change == "duplicate_journal":
        args[0].append(deepcopy(args[0][0]))
    elif change == "bad_wait":
        args[0][0]["ready_to_selected_ms"][0] = 12.
    elif change == "duplicate_forward":
        duplicate = deepcopy(args[3][0])
        duplicate.update(sample_id="native-service-000000002",
                         service_start_monotonic_ms=20., complete_monotonic_ms=30.)
        args[3].append(duplicate)
    elif change == "boolean_generation":
        args[0][0]["keys"][0]["session_generation"] = True
    else:
        args[3].append(deepcopy(args[3][0]))
    with pytest.raises(ValueError):
        inspect(*args)


def test_empty_journal_is_not_positive_evidence():
    _, events, receipts, services = fixture()
    result = inspect([], events, receipts, services)
    assert result["summary"]["journal_records"] == 0
    assert not result["summary"]["positive_service_evidence"]


def test_previous_request_attempt_service_is_retained_as_a_diagnostic():
    journals, events, receipts, services = fixture()
    journals[0]["keys"][0]["attempt_id"] = 1
    previous = deepcopy(services[0])
    previous.update(launch_monotonic_ms=9., service_start_monotonic_ms=9.,
                    complete_monotonic_ms=10., complete_ts_ms=100010.)
    services[0]["sample_id"] = "native-service-000000002"
    services[0]["forward_iter"] = 2
    result = inspect(journals, events, receipts, [previous, *services])
    assert result["summary"]["verified_records"] == 1
    assert result["records"][0]["request_had_earlier_native_launch"] == [True, True]
    assert result["records"][0]["attempt_identity_independently_verified"] is False


@pytest.mark.parametrize("change", ("append", "tamper", "foreign_arm"))
def test_cli_sealed_recheck_preserves_the_exact_original_prefix(tmp_path, monkeypatch, change):
    from scripts import audit_sibling_native_service as audit

    arm = tmp_path / "arm"
    paths = (
        "opportunities/admission_opportunities.jsonl", "replay/runtime_events.replay.jsonl",
        "replay/native_requests.jsonl", "server/runtime_audit.jsonl",
    )
    for name, rows in zip(paths, fixture(), strict=True):
        path = arm / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    monkeypatch.setattr(sys, "argv", ["audit", "--arm", str(arm), "--output", str(first)])
    audit.main()
    basis = json.loads(first.read_text())
    assert basis["summary"]["verified_multi_member_batches"] == 1
    if change == "append":
        for name in paths:
            with (arm / name).open("a") as stream:
                stream.write('{"event":"later_unrelated_record"}\n')
    elif change == "tamper":
        path = arm / paths[0]
        path.write_text(path.read_text().replace('"join"', '"oops"'))
    else:
        basis["arm"] = str(tmp_path / "foreign")
        first.write_text(json.dumps(basis))
    monkeypatch.setattr(sys, "argv", [
        "audit", "--arm", str(arm), "--output", str(second), "--sealed-audit", str(first),
    ])
    if change == "append":
        audit.main()
        actual = json.loads(second.read_text())
        assert actual["summary"] == basis["summary"]
        assert actual["input_jsonl_prefixes"] == basis["input_jsonl_prefixes"]
    else:
        with pytest.raises(ValueError if change == "tamper" else SystemExit):
            audit.main()
        assert not second.exists()
