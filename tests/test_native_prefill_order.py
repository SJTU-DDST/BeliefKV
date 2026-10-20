from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime


def request(name, *, tagged=True):
    return NS(
        rid=name,
        beliefkv_metadata={
            "root_workflow_id": "wf",
            "invocation_id": name,
            "context_id": f"ctx-{name}",
            "context_epoch": 0,
        } if tagged else None,
        cache_request_handle=NS(attempt_id=0),
        session_id=None,
        session_generation=None,
        finished=lambda: False,
    )


def select(runtime, requests, *, running=None, adder=None):
    plan = runtime.plan_native_prefill(
        requests, running_batch=running, adder=adder,
    )
    return select_native_prefill_candidates(
        requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
    )


@pytest.fixture
def native_runtime(monkeypatch):
    monkeypatch.setenv("BELIEFKV_NATIVE_PREFILL_ORDER", "1")
    monkeypatch.setenv("BELIEFKV_WORKFLOW_ATTAINED_WORK", "1")
    monkeypatch.setenv("BELIEFKV_ENABLE_RESIDENT_FIRST", "1")
    monkeypatch.setenv("BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY", "1")
    runtime = NativeAdmissionRuntime()
    try:
        yield runtime
    finally:
        runtime.close()


@pytest.mark.parametrize("setting", [None, "0", "1"])
def test_native_order_is_opt_in_and_preserves_default_ranking(monkeypatch, setting):
    if setting is None:
        monkeypatch.delenv("BELIEFKV_NATIVE_PREFILL_ORDER", raising=False)
    else:
        monkeypatch.setenv("BELIEFKV_NATIVE_PREFILL_ORDER", setting)
    monkeypatch.setenv("BELIEFKV_ENABLE_RESIDENT_FIRST", "0")
    monkeypatch.setenv("BELIEFKV_WORKFLOW_ATTAINED_WORK", "0")
    runtime = NativeAdmissionRuntime()
    first, plain, second = request("a"), request("plain", tagged=False), request("b")
    runtime.register_visible_request(first)
    runtime.register_visible_request(second)
    runtime._prefill_causal_ranks = Mock(return_value={0: (2, 0, 0), 2: (0, 0, 2)})
    try:
        chosen = select(runtime, [first, plain, second])
        assert chosen.candidates == (
            (first, plain, second) if setting == "1" else (second, plain, first)
        )
        assert not chosen.rejected
        assert runtime._prefill_causal_ranks.call_count == int(setting != "1")
        assert runtime.counts["native_prefill_order_plans"] == int(setting == "1")
    finally:
        runtime.close()


@pytest.mark.parametrize("setting", ["true", "", "2"])
def test_invalid_native_order_setting_is_rejected(monkeypatch, setting):
    monkeypatch.setenv("BELIEFKV_NATIVE_PREFILL_ORDER", setting)
    with pytest.raises(ValueError, match="native prefill order"):
        NativeAdmissionRuntime()


def test_native_order_is_fixed_at_startup_and_skips_priority_work(native_runtime, monkeypatch):
    runtime = native_runtime
    requests = [request("cold"), request("hot")]
    for item in requests:
        runtime.register_visible_request(item)
    monkeypatch.setenv("BELIEFKV_NATIVE_PREFILL_ORDER", "0")
    for name in (
        "_prefill_causal_ranks", "_read_request_residency_hint",
        "_read_request_reentry", "_resident_aged_bypass", "_final_admission_work",
    ):
        monkeypatch.setattr(runtime, name, Mock(side_effect=AssertionError(name)))
    runtime._visible_since["hot"] = 0.
    assert select(runtime, requests).candidates == tuple(requests)
    assert runtime.enable_resident_first
    assert runtime.enable_final_stage_priority
    assert runtime._workflow_attained_work_enabled
    assert not runtime.physical_ledger.pending_count


def test_native_order_preserves_capacity_observation(native_runtime):
    runtime = native_runtime
    queued = request("queued")
    runtime.register_visible_request(queued)
    running = NS(reqs=[request("running")])
    adder = NS(
        max_running_requests=48, max_prefill_bs=8, page_size=16,
        rem_input_tokens=8192, rem_chunk_tokens=4096,
    )
    assert select(runtime, [queued], running=running, adder=adder).candidates == (queued,)
    assert runtime._native_running_batch is running
    assert runtime._native_max_running == 48
    assert runtime._native_prefill_slots == 8
    assert runtime._native_input_reserve == 4096
    assert runtime._native_page_size == 16


@pytest.mark.parametrize("tagged_count", [0, 1])
def test_tool_only_noncompeting_queue_skips_ordering_but_keeps_capacity(
    monkeypatch, tagged_count,
):
    monkeypatch.setenv("BELIEFKV_NATIVE_PREFILL_ORDER", "0")
    runtime = NativeAdmissionRuntime()
    runtime._tool_timing_only = True
    runtime.enable_resident_first = True
    runtime.enable_final_stage_priority = True
    plain = request("plain", tagged=False)
    tagged = [request("single")] if tagged_count else []
    for item in tagged:
        runtime.register_visible_request(item)
    running = NS(reqs=[request("running")])
    adder = NS(
        max_running_requests=48, max_prefill_bs=8, page_size=16,
        rem_input_tokens=8192, rem_chunk_tokens=4096,
    )
    for name in (
        "_prefill_causal_ranks", "_submit_local_predictions", "_resident_prefill_scan",
        "_current_residency_budget", "_read_request_residency_hint",
        "_read_request_reentry", "_resident_aged_bypass", "_final_admission_work",
    ):
        monkeypatch.setattr(runtime, name, Mock(side_effect=AssertionError(name)))
    try:
        queued = [plain, *tagged]
        assert select(runtime, queued, running=running, adder=adder).candidates == tuple(queued)
        assert runtime.counts["noncompeting_prefill_plans"] == 1
        assert runtime.counts["native_prefill_order_plans"] == 0
        assert runtime._native_max_running == 48
        assert runtime._native_input_reserve == 4096
        assert runtime._native_running_batch is running
        assert runtime.enable_resident_first
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_tool_only_noncompeting_queue_rechecks_identity_and_resumes_competition(monkeypatch):
    monkeypatch.setenv("BELIEFKV_NATIVE_PREFILL_ORDER", "0")
    runtime = NativeAdmissionRuntime()
    runtime._tool_timing_only = True
    runtime.enable_resident_first = False
    first, second = request("first"), request("second")
    runtime.register_visible_request(first)
    runtime.register_visible_request(second)
    causal = Mock(return_value={0: (3, 0, 0), 1: (0, 0, 1)})
    monkeypatch.setattr(runtime, "_prefill_causal_ranks", causal)
    try:
        plan = runtime.plan_native_prefill([first], running_batch=None, adder=None)
        first.cache_request_handle.attempt_id += 1
        changed = select_native_prefill_candidates(
            [first], plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert changed.candidates == ()
        assert changed.rejected == (("first", "identity_changed"),)
        causal.assert_not_called()
        first.cache_request_handle.attempt_id -= 1
        assert select(runtime, [first, second]).candidates == (second, first)
        causal.assert_called_once()
        assert runtime.counts["noncompeting_prefill_plans"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["attempt", "session", "epoch", "abort", "terminal"])
def test_native_order_keeps_identity_and_terminal_checks(native_runtime, change):
    runtime = native_runtime
    tagged, plain = request("tagged"), request("plain", tagged=False)
    tagged.session_id, tagged.session_generation = "session", 1
    runtime.register_visible_request(tagged)
    if change == "attempt":
        tagged.cache_request_handle.attempt_id += 1
    elif change == "session":
        tagged.session_generation += 1
    elif change == "epoch":
        tagged.beliefkv_metadata["context_epoch"] += 1
    elif change == "abort":
        runtime.on_abort_request(NS(rid=tagged.rid, abort_all=False))
    else:
        runtime.on_events((
            RuntimeEvent("start", 0., RuntimeEventKind.WORKFLOW_START, "wf"),
            RuntimeEvent("end", 1., RuntimeEventKind.WORKFLOW_END, "wf"),
        ))
    chosen = select(runtime, [tagged, plain])
    assert chosen.candidates == (plain,)
    assert chosen.rejected == (("tagged", "no_authorization"),)


def test_native_order_selection_rechecks_identity_after_planning(native_runtime):
    runtime = native_runtime
    tagged, plain = request("tagged"), request("plain", tagged=False)
    runtime.register_visible_request(tagged)
    plan = runtime.plan_native_prefill([tagged, plain], running_batch=None, adder=None)
    tagged.cache_request_handle.attempt_id += 1
    chosen = select_native_prefill_candidates(
        [tagged, plain], plan=plan, current_semantic_revision=runtime.semantic_revision,
    )
    assert chosen.candidates == (plain,)
    assert chosen.rejected == (("tagged", "identity_changed"),)


def test_native_order_retains_candidate_bound_and_untagged_traffic(native_runtime):
    runtime = native_runtime
    tagged = [request(f"r{index}") for index in range(513)]
    for item in tagged:
        runtime.register_visible_request(item)
    plain = request("plain", tagged=False)
    chosen = select(runtime, [*tagged, plain])
    assert chosen.candidates == (*tagged[:512], plain)
    assert chosen.rejected == (("r512", "no_authorization"),)
    assert runtime.counts["candidate_bound"] == 1


def test_native_order_clears_promotion_feedback_without_disabling_admission(native_runtime):
    runtime = native_runtime
    queued = request("queued")
    runtime.register_visible_request(queued)
    for name in (
        "_workflow_work_promoted", "_final_priority_promoted", "_prefetch_priority_promoted",
        "_parent_handoff_priority_promoted", "_resident_priority_promoted",
    ):
        setattr(runtime, name, queued.rid)
    runtime._residency_ordering_hints[runtime.visible[queued.rid]] = (
        float("inf"), {"device_checkpoint_tokens": 128},
    )
    assert select(runtime, [queued]).candidates == (queued,)
    runtime.on_prefill_candidate_result(queued, admitted=True, result="CONTINUE")
    assert runtime.counts["native_admitted"] == 1
    assert not runtime._residency_hint_admissions
    for counter in (
        "workflow_work_head_admitted", "final_priority_admitted",
        "prefetch_priority_admitted", "parent_handoff_priority_admitted",
        "resident_aged_priority_admitted", "residency_ordering_hint_admitted",
    ):
        assert runtime.counts[counter] == 0


@pytest.mark.parametrize("native_order", [False, True])
def test_native_order_disables_only_custom_capacity_bypass(monkeypatch, native_order):
    monkeypatch.setenv("BELIEFKV_NATIVE_PREFILL_ORDER", str(int(native_order)))
    runtime = NativeAdmissionRuntime()
    runtime.enable_resident_first = True
    queued = request("queued")
    runtime.register_visible_request(queued)
    runtime._executable_waiting_key = Mock(return_value=runtime.visible[queued.rid])
    try:
        assert runtime.allow_prefill_capacity_bypass(
            queued, native_budget_available=True, bypassed=0,
        ) is not native_order
        assert runtime._executable_waiting_key.call_count == int(not native_order)
    finally:
        runtime.close()


def test_native_order_keeps_prepare_lifecycle_and_completion_callbacks(native_runtime):
    runtime = native_runtime
    runtime.dispatch_join_prepare = Mock()
    runtime.dispatch_tool_prepare = Mock()
    runtime.dispatch_join_prefetch = Mock()
    runtime.dispatch_tool_prefetch = Mock()
    waiting, running = [], NS(reqs=[])
    runtime.dispatch_after_forward(waiting, running_batch=running)
    runtime.dispatch_join_prepare.assert_called_once_with(
        waiting, running_batch=running, _defer_pressure_publication=True,
    )
    runtime.dispatch_tool_prepare.assert_called_once_with(
        waiting, running_batch=running, _defer_pressure_publication=True,
    )
    runtime.dispatch_join_prefetch.assert_not_called()
    runtime.dispatch_tool_prefetch.assert_not_called()
    runtime.on_events((
        RuntimeEvent("start", 0., RuntimeEventKind.WORKFLOW_START, "wf"),
        RuntimeEvent(
            "invocation", 1., RuntimeEventKind.INVOCATION_CREATE, "wf",
            invocation_id="child", context_id="ctx-child", context_epoch=0,
        ),
    ))
    queued = request("child")
    queued.origin_input_ids, queued.output_ids = list(range(16)), [1, 2, 3]
    queued.finished = lambda: True
    runtime.register_visible_request(queued)
    runtime.on_batch_completed(NS(reqs=[queued]))
    assert runtime._workflow_attained_output_tokens == {"wf": 3}
    assert queued.rid not in runtime.visible
    runtime.on_events((
        RuntimeEvent("end", 2., RuntimeEventKind.WORKFLOW_END, "wf"),
    ))
    assert runtime.graph.workflows["wf"].end_ts_ms == 2.
    assert "wf" not in runtime._workflow_attained_output_tokens
    assert runtime.enable_prepare_host


def test_runtime_state_distinguishes_configured_priorities_from_native_order(native_runtime):
    runtime = native_runtime
    writer = Mock()
    runtime._opportunity_writer = writer
    try:
        runtime._record_runtime_state()
        state = writer.record.call_args.args[0]
        assert state["prefill_ordering_mode"] == "native"
        assert state["resident_first"] is True
        assert state["workflow_attained_work"] is True
        assert state["final_stage_priority"] is True
        assert state["prepare_host"] is True
    finally:
        runtime._opportunity_writer = None
