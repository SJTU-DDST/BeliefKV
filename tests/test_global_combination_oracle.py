from functools import partial
import json
from pathlib import Path
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.policy.paper_baselines import ThunderProgram
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from tests import test_global_workflow_oracle as global_oracle
from tests.test_workflow_combination_runtime import configured


def queued(monkeypatch, tmp_path, *, window_ms=2000, **kwargs):
    profile = configured(tmp_path)
    costs = json.loads(Path(profile["WORKFLOW_WORK_MODEL"]).read_bytes())["admission_cost_profile"]
    original_file, original_settings = global_oracle.oracle_file, global_oracle.settings

    def oracle_file(path, raw):
        return original_file(path, {**raw, "admission_cost_profile": costs})

    monkeypatch.setattr(global_oracle, "oracle_file", oracle_file)
    monkeypatch.setattr(global_oracle, "settings", lambda path: {
        **original_settings(path), "WORKFLOW_COMBINATION_ADMISSION": "1",
    })
    from beliefkv.runtime import workflow_combination as combination

    monkeypatch.setattr(
        combination, "choose_combination",
        partial(combination.choose_combination, clock=lambda: 0),
    )
    monkeypatch.setattr(combination, "observe_static_full_mamba_headroom", lambda cache: NS(
        observable=True, device_full_free_tokens=10000, device_mamba_free_slots=0,
    ))
    runtime, admission = global_oracle.queued(
        monkeypatch, tmp_path, global_order=True, activate_first=False,
        window_ms=window_ms, **kwargs,
    )
    runtime._native_cache.req_to_token_pool = NS(available_size=lambda: 24)
    runtime._native_running_batch = NS(reqs=[])
    runtime._native_max_running = 24
    runtime._native_input_reserve = runtime._native_chunk_input_reserve = 2048
    monkeypatch.setattr(runtime, "_read_request_reentry", lambda req, **kwargs: {
        "checkpoint_tokens": 0, "missing_full_tokens": 0, "missing_mamba_slots": 0,
    })
    return runtime, admission


@pytest.mark.parametrize("periodic", (False, True))
def test_combination_preserves_two_second_window_and_changes_actual_native_candidates(
    monkeypatch, tmp_path, periodic,
):
    runtime, admission = queued(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        before = time.monotonic()
        admission.tick(now=before)
        assert not any(item.active for item in admission.policy.programs.values())
        assert admission._deferred_dispatch is admission._combination
        assert admission._combination.window_ms == 2000
        deadline = admission._combination.next_dispatch
        if periodic:
            admission.next_tick = 0.
        monkeypatch.setattr(time, "monotonic", lambda: deadline + .001)
        admission.tick(now=deadline + .001)
        assert admission.policy.programs["short"].active
        assert not admission.policy.programs["long"].active
        requests = list(admission._live_requests.values())
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert tuple(req.rid for req in actual.candidates) == ("short-request",)
        assert any(row["event"] == "workflow_combination_selection" for row in records)
        assert admission.status()["workflow_oracle_coalescing_window_ms"] == 2000
        assert admission.policy.remaining_capacity() >= 0
    finally:
        runtime.close()


def test_tool_pair_ranking_survives_zero_pre_release_capacity_and_closed_window(
    monkeypatch, tmp_path,
):
    runtime, admission = queued(monkeypatch, tmp_path)
    try:
        admission.policy.programs["active"].active = True
        assert admission.policy.remaining_capacity() == 0
        keys = [admission.latest[name] for name in ("long", "short")]
        assert admission._order_dispatch_keys(keys, {}) == []
        assert admission.order_tool_replacement(keys) == keys[::-1]
        assert admission.policy.programs["active"].active
        assert not admission.policy.programs["short"].active
        assert not admission.counts["workflow_combination_passes"]
    finally:
        runtime.close()


@pytest.mark.parametrize("periodic", (False, True))
def test_ready_join_parent_stays_protected_during_global_combination(
    monkeypatch, tmp_path, periodic,
):
    runtime, admission = queued(monkeypatch, tmp_path)
    try:
        runtime._join_priority_pending["long"] = "observed-join"
        monkeypatch.setattr(
            runtime, "_ready_join_parent", lambda req, key: key.invocation_id == "long",
        )
        if periodic:
            admission.next_tick = 0.
        admission.tick(now=time.monotonic())
        assert admission.policy.programs["long"].active
        assert not admission.policy.programs["short"].active
        assert admission.policy.remaining_capacity() >= 0
    finally:
        runtime.close()


def test_periodic_combination_cannot_restore_a_tool_blocked_owner(monkeypatch, tmp_path):
    runtime, admission = queued(monkeypatch, tmp_path, window_ms=0)
    try:
        runtime.graph.invocations["short"].active_tool_calls["tool"] = "execute"
        admission.next_tick = 0.
        admission.tick()
        assert not admission.policy.programs["short"].active
        assert admission.policy.programs["long"].active
        assert "short" not in {
            row[0] for row in admission.policy.last_selection["ordered"]
        }
    finally:
        runtime.close()


def test_disabled_work_ranking_cannot_read_future_paths_for_set_utility(monkeypatch, tmp_path):
    runtime, admission = queued(monkeypatch, tmp_path, window_ms=0, ranking=False)
    try:
        monkeypatch.setattr(
            admission._combination, "_resources",
            lambda *_args: pytest.fail("disabled ranking built a work-weighted set"),
        )
        admission.tick()
        assert admission.policy.programs["long"].active
        assert not admission.policy.programs["short"].active
        assert admission.counts["workflow_combination_remaining_work_ranking_disabled_reference"] == 1
    finally:
        runtime.close()


def test_periodic_set_reference_preserves_join_right_before_context_order():
    seen = []

    def select(ordered, reference, budget):
        seen.append(([item.identity for item in ordered], [item.identity for item in reference]))
        return reference

    policy = WorkflowSizeWorkingSet(
        500, rank=lambda name, now: 0. if name == "parent" else 11.,
        sibling_group=lambda name: None, observe_ready=lambda item: True,
        rank_before_category=True, select_set=select,
    )
    policy.programs = {
        "parent": ThunderProgram("parent", 300, steps=2, queue_sequence=1),
        "small": ThunderProgram("small", 100, steps=2, queue_sequence=2),
    }
    policy.tick(now=1.)
    assert seen == [(["parent", "small"], ["parent"])]
    assert policy.programs["parent"].active and not policy.programs["small"].active
