from dataclasses import asdict
from types import SimpleNamespace as NS

import pytest

from tests.test_workflow_combination_runtime import runtime_with_queue


@pytest.mark.parametrize(
    "reason,observable,nodes,expected_match",
    (
        ("node ancestry exceeds bound", False, (), None),
        ("pending_load_transfer", False, (), None),
        ("pending_write_transfer", False, (), None),
        (None, True, (NS(creation_time=3.),), False),
    ),
)
def test_rejected_prefix_records_native_reason_without_relaxing_reference_fallback(
    monkeypatch, tmp_path, reason, observable, nodes, expected_match,
):
    from beliefkv.runtime import workflow_combination as combination

    runtime, admission, _env = runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    monkeypatch.setattr(runtime, "_read_request_reentry", lambda req, **kwargs: {
        "checkpoint_tokens": 10, "missing_full_tokens": 10,
        "missing_mamba_slots": 0, "component_leaves": ((0, ((77, 2.),)),),
    })
    calls = []

    def observe(cache, identity, **kwargs):
        calls.append((identity, kwargs))
        return NS(observable=observable, nodes=nodes, reason=reason)

    monkeypatch.setattr(combination, "observe_unified_node_closure", observe)
    try:
        admission.tick()
        row = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert row["decision"]["reason"] == "reference_candidate_summary_unavailable"
        assert admission.policy.programs["a"].active
        assert not admission.policy.programs["b"].active
        assert not admission.policy.programs["c"].active
        assert calls == [(77, {"max_nodes": 64, "reject_pending": True})]
        assert row["unavailable_summaries"] == [{
            "key": asdict(admission.latest["a"]),
            "reason": "unobservable_missing_closure",
            "detail": {
                "closure_reason": reason, "closure_observable": observable,
                "closure_node_count": len(nodes), "maximum_closure_nodes": 64,
                "anchor_id": 77, "anchor_creation_matches": expected_match,
                "checkpoint_tokens": 10, "missing_full_tokens": 10,
            },
        }]
        assert row["physical_capacity_credit"] is False
    finally:
        runtime.close()
