from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from tests.test_learned_coalescing_control import queued


def test_disabled_distribution_uses_context_order_and_records_the_actual_reference_window(
    monkeypatch, tmp_path,
):
    runtime, admission = queued(monkeypatch, tmp_path, ranking=False, window_ms=0)
    records = []
    runtime._opportunity_writer = NS(record=records.append)

    def forbidden(*args):
        raise AssertionError("reference ordering must not build native resource summaries")

    monkeypatch.setattr(admission._combination, "_resources", forbidden)
    try:
        admission.tick()
        assert {
            item.identity for item in admission.policy.programs.values() if item.active
        } == {"b", "c"}
        choice = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert choice["decision"]["selected"] == choice["decision"]["reference"] == ("b", "c")
        window = choice["logical_reference_window"]
        assert [row["key"]["invocation_id"] for row in window] == ["b", "c", "a"]
        assert [row["logical_tokens"] for row in window] == [250, 250, 400]
        assert not any(row["protected_join"] for row in window)
        assert choice["logical_reference_capacity_tokens"] == 500
        assert choice["logical_reference_maximum"] == 4
        assert choice["resources"] is None
        assert admission.status()["workflow_combination_free_reference_order"] == (
            "protected_join_category_context"
        )
        requests = [admission._live_requests[name + "-request"] for name in ("a", "b", "c")]
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        selected = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert {req.rid for req in selected.candidates} == {"b-request", "c-request"}
    finally:
        runtime._opportunity_writer = None
        runtime.close()


@pytest.mark.parametrize("priority", ("ready_join", "continuation"))
def test_reference_rights_and_resume_category_precede_small_context(
    monkeypatch, tmp_path, priority,
):
    runtime, admission = queued(monkeypatch, tmp_path, ranking=False, window_ms=0)
    try:
        if priority == "ready_join":
            runtime._join_priority_pending["a"] = "observed-join"
            monkeypatch.setattr(runtime, "_ready_join_parent", lambda req, key: key.invocation_id == "a")
        else:
            admission.policy.programs["a"].steps = 2
        admission.tick()
        assert admission.policy.programs["a"].active
        assert not admission.policy.programs["b"].active
        assert not admission.policy.programs["c"].active
        assert admission.policy.remaining_capacity() == 100
    finally:
        runtime.close()
