from copy import deepcopy

import pytest

from scripts.audit_demand_prefill_pipeline import reconcile
from scripts.audit_staged_native_restore import inspect, service_evidence
from tests.test_staged_native_restore_audit import observations


def fixture():
    journals, transfers = observations()
    journals[0].update(monotonic_ms=10., monotonic_clock_domain="clock")
    member = {name: journals[0][name] for name in (
        "request_id", "workflow_id", "invocation_id", "context_id", "context_epoch",
        "attempt_id", "session_id", "session_generation",
    )}
    workers = [{
        "event": "gpu_service_sample", "phase": "prefill", "forward_iter": 5,
        "monotonic_clock_domain": "clock", "launch_monotonic_ms": 20.,
        "complete_monotonic_ms": 25., "request_samples": [member],
    }]
    receipts = [{
        "request_id": "r", "native_receipt": {
            "request_id": "r", "identity": ["w", "i", "c", 1],
            "session_id": "s", "session_generation": 2, "clock_domain": "clock",
            "complete_monotonic_ms": 30.,
        },
    }]
    stage = inspect(journals, transfers, bytes_per_token=100)
    return stage, journals, workers, receipts


def test_staged_demand_requires_native_forward_identity_and_completed_receipt():
    stage, journals, workers, receipts = fixture()
    result = service_evidence(stage["episodes"], journals, workers, receipts=receipts)
    assert result["counts"] == {"members_with_subsequent_native_service": 1}
    assert result["members"][0]["staged_to_first_native_launch_ms"] == 10.
    assert result["members"][0]["native_completion_receipt_verified"]
    assert not result["native_kv_reuse_or_hidden_dma_proven"]


@pytest.mark.parametrize("fault", (
    "attempt", "session", "epoch", "clock", "preexisting_service", "missing_service",
    "missing_receipt", "receipt_session", "receipt_clock", "early_completion", "duplicate_forward",
))
def test_inexact_service_or_terminal_evidence_is_refused(fault):
    stage, journals, workers, receipts = fixture()
    if fault in ("attempt", "session", "epoch"):
        field = {"attempt": "attempt_id", "session": "session_generation", "epoch": "context_epoch"}[fault]
        workers[0]["request_samples"][0][field] += 1
    elif fault == "clock":
        workers[0]["monotonic_clock_domain"] = "other"
    elif fault == "preexisting_service":
        workers[0]["launch_monotonic_ms"] = 9.
    elif fault == "missing_service":
        workers.clear()
    elif fault == "missing_receipt":
        receipts.clear()
    elif fault == "receipt_session":
        receipts[0]["native_receipt"]["session_generation"] += 1
    elif fault == "receipt_clock":
        receipts[0]["native_receipt"]["clock_domain"] = "other"
    elif fault == "early_completion":
        receipts[0]["native_receipt"]["complete_monotonic_ms"] = 24.
    else:
        workers.append(deepcopy(workers[0]))
    with pytest.raises(ValueError, match="native|staged"):
        service_evidence(stage["episodes"], journals, workers, receipts=receipts)


def test_explicit_cancellation_preserves_copy_accounting_without_inventing_service():
    stage, journals, workers, _ = fixture()
    journals.append({
        **journals[0], "event": "native_demand_restore_cancelled", "monotonic_ms": 15.,
        "requests": stage["episodes"][0]["requests"],
    })
    result = service_evidence(stage["episodes"], journals, [])
    assert result["counts"] == {"cancelled_members_without_service": 1}
    assert stage["counts"]["native_h2d_bytes"] == 800
    assert not result["native_kv_reuse_or_hidden_dma_proven"]


def balance_fixture():
    stage, *_ = fixture()
    native = {
        "transfers": {"h2d_bytes": 1600, "h2d_tagged_bytes": 100},
        "native_request_host_hit_bytes": 500, "handoff_consumer_h2d_bytes": 200,
        "untagged_h2d_unexplained_after_request_hits_and_handoff_bytes": 800,
        "handoffs": [{
            "native_pair_submit_ts_ms": 1., "published_consumer_anchor": [10],
            "consumer_bytes": 200,
        }],
    }
    return native, stage


def test_exact_staging_ack_fills_only_its_own_physical_native_accounting_term():
    native, stage = balance_fixture()
    result = reconcile(native, stage)
    assert result["exact_physical_bytes_reconciled"]
    assert result["staged_native_demand_bytes"] == 800
    assert not result["native_cache_hit_counters_changed"]


@pytest.mark.parametrize("fault", ("missing_bytes", "wrong_legacy_gap", "double_credit"))
def test_unknown_or_double_credited_bytes_do_not_pass_complete_balance(fault):
    native, stage = balance_fixture()
    if fault == "missing_bytes":
        stage["counts"]["native_h2d_bytes"] = 799
    elif fault == "wrong_legacy_gap":
        native["untagged_h2d_unexplained_after_request_hits_and_handoff_bytes"] = 799
    else:
        native["handoffs"][0] = {
            "native_pair_submit_ts_ms": 100., "published_consumer_anchor": [3, 7],
            "consumer_bytes": 800,
        }
    with pytest.raises(ValueError, match="native|H2D"):
        reconcile(native, stage)
