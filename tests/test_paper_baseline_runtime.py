from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.paper_baselines import (
    DISABLED_POLICIES, RUNTIME_ARMS, environment, validate_state,
)
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime


def runtime(monkeypatch, *, capacity=1000):
    settings = environment({"TOOL_CONTINUITY": "1"}, "thunderagent")
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    result = NativeAdmissionRuntime()
    cache = NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=capacity),
    )
    result.attach_native_cache(cache)
    return result


def request(identity, invocation, *, tokens=100, workflow="workflow"):
    return NS(
        rid=identity, origin_input_ids=list(range(tokens)), output_ids=[1, 2],
        beliefkv_metadata={
            "root_workflow_id": workflow, "invocation_id": invocation,
            "context_id": invocation + "-context", "context_epoch": 0,
        },
        cache_request_handle=NS(attempt_id=0),
        session_id=None, session_generation=None, finished=lambda: True,
    )


def test_paper_environment_disables_our_policies_and_preserves_shared_limits():
    settings = environment({
        "SERVER_RANDOM_SEED": "21", "MAX_RUNNING_REQUESTS": "24",
        "CONTEXT_LENGTH": "262144", "TOOL_CONTINUITY": "1",
        "EXECUTION_FRONTIER_RESTORE": "1",
    }, "thunderagent")
    assert settings["SERVER_RANDOM_SEED"] == "21"
    assert settings["MAX_RUNNING_REQUESTS"] == "24"
    assert settings["BELIEFKV_PAPER_BASELINE"] == RUNTIME_ARMS["thunderagent"]
    for field in (
        "BELIEFKV_ENABLE_RESIDENT_FIRST", "BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY",
        "BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY", "BELIEFKV_ENABLE_TOOL_CONTINUITY",
        "BELIEFKV_ENABLE_FRONTIER_RESTORE", "BELIEFKV_ENABLE_PREPARE_HOST",
        "BELIEFKV_ENABLE_TERMINAL_CHILD_RECLAIM",
    ):
        assert settings[field] == "0"
    with pytest.raises(ValueError, match="runtime adapter"):
        environment({}, "continuum_paper")


def test_native_selection_defers_a_program_without_mutating_its_request(monkeypatch):
    live = runtime(monkeypatch, capacity=500)
    first = request("one", "a", tokens=300)
    second = request("two", "b", tokens=300)
    assert live.register_visible_request(first)
    assert live.register_visible_request(second)
    live._paper_baseline.next_tick = float("inf")
    plan = live.plan_native_prefill([first, second], running_batch=NS(reqs=[]), adder=None)
    selected = select_native_prefill_candidates(
        [first, second], plan=plan, current_semantic_revision=live.semantic_revision,
    )
    assert selected.candidates == (first,)
    assert second.origin_input_ids == list(range(300))
    assert "two" in live.visible
    assert live._paper_baseline.policy.programs["b"].pending
    assert not live._custom_kv_management
    live.close()


def test_program_tokens_survive_tool_boundaries_without_join_or_resident_boosts(monkeypatch):
    live = runtime(monkeypatch)
    first = request("one", "a", tokens=100)
    live.register_visible_request(first)
    assert live.on_batch_completed(NS(reqs=[first])) == ("one",)
    program = live._paper_baseline.policy.programs["a"]
    assert program.tokens == 102 and program.active and program.acting
    second = request("two", "a", tokens=120)
    live.register_visible_request(second)
    assert program.steps == 2 and program.pending and not program.acting
    assert live.enable_join_parent_priority is False
    assert live.enable_resident_first is False
    assert live._tool_continuity is None
    live.close()


def test_pausing_program_does_not_change_native_running_request(monkeypatch):
    live = runtime(monkeypatch, capacity=500)
    first = request("one", "a", tokens=300)
    live.register_visible_request(first)
    first.output_ids = list(range(300))
    live._paper_baseline.tick(now=0.)
    program = live._paper_baseline.policy.programs["a"]
    assert program.active and program.marked_for_pause
    assert live.plan_running_batch_retraction(NS(reqs=[first])) is None
    assert live.on_batch_completed(NS(reqs=[first])) == ("one",)
    assert not program.active and not program.marked_for_pause
    live.close()


def test_paper_window_does_not_authorize_changed_request_identity(monkeypatch):
    live = runtime(monkeypatch)
    req = request("one", "a")
    live.register_visible_request(req)
    plan = live.plan_native_prefill([req], running_batch=None, adder=None)
    req.cache_request_handle.attempt_id = 1
    selection = select_native_prefill_candidates(
        [req], plan=plan, current_semantic_revision=live.semantic_revision,
    )
    assert not selection.candidates
    assert selection.rejected == (("one", "identity_changed"),)
    live.close()


def test_workflow_end_retires_pending_program_metadata(monkeypatch):
    live = runtime(monkeypatch)
    live.register_visible_request(request("one", "a"))
    live._paper_baseline.events((
        RuntimeEvent("done", 1., Kind.WORKFLOW_END, "workflow"),
    ))
    assert not live._paper_baseline.policy.programs
    assert not live._paper_baseline.requests
    live.close()


def test_paper_adapter_rejects_hybrid_capacity_and_policy_leak(monkeypatch):
    settings = environment({}, "thunderagent")
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY", "1")
    with pytest.raises(ValueError, match="inherit"):
        NativeAdmissionRuntime()
    monkeypatch.setenv("BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY", "0")
    live = NativeAdmissionRuntime()
    with pytest.raises(ValueError, match="FULL-only"):
        live.attach_native_cache(NS(
            tree_components=(0, 2), token_to_kv_pool_allocator=NS(size_full=1000),
        ))
    live.close()


def test_unknown_paper_name_is_a_preflight_error(monkeypatch):
    for name, value in environment({}, "thunderagent").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "ordinary_fifo_called_agentix")
    with pytest.raises(ValueError, match="runtime adapter"):
        NativeAdmissionRuntime()


def state():
    return {
        **{name: False for name in DISABLED_POLICIES},
        "internal_comparison_mode": "scheduler_only",
        "paper_baseline": {
            "policy": RUNTIME_ARMS["thunderagent"], "program_identity": "invocation_id",
            "future_information_used": False, "native_capacity_authority_preserved": True,
            "streaming_context_accounting": "observed_native_outputs_at_arrival_and_five_second_control",
        },
    }


@pytest.mark.parametrize("field", DISABLED_POLICIES)
def test_observed_paper_arm_rejects_any_our_policy_enabled(field):
    observed = state()
    validate_state("thunderagent", observed)
    observed[field] = True
    with pytest.raises(ValueError, match="leaked"):
        validate_state("thunderagent", observed)


def test_finished_native_request_is_charged_once(monkeypatch):
    live = runtime(monkeypatch)
    req = request("one", "a")
    live.register_visible_request(req)
    batch = NS(reqs=[req])
    assert live.on_batch_completed(batch) == ("one",)
    # Native may still present a completed object during overlapping publication.
    live.on_batch_completed(batch)
    assert live._paper_baseline.counts["completed"] == 1
    live.close()


def test_running_context_growth_is_charged_at_existing_control_cadence(monkeypatch):
    live = runtime(monkeypatch, capacity=500)
    req = request("streaming", "a", tokens=300)
    live.register_visible_request(req)
    adapter = live._paper_baseline
    adapter.next_tick = 0.
    adapter.tick(now=0.)
    program = adapter.policy.programs["a"]
    assert program.tokens == 302 and not program.marked_for_pause
    req.output_ids.extend(range(298))
    adapter.tick(now=1.)
    assert program.tokens == 302
    adapter.tick(now=5.)
    assert program.tokens == 600 and program.active and program.marked_for_pause
    assert adapter.counts["streaming_progress_checks"] == 2
    live.on_batch_completed(NS(reqs=[req]))
    assert not program.active and not adapter._live_requests
    live.close()


def test_native_retraction_keeps_request_arrival_and_continues_streaming_accounting(monkeypatch):
    live = runtime(monkeypatch, capacity=1000)
    req = request("one", "a", tokens=100)
    live.register_visible_request(req)
    adapter = live._paper_baseline
    _, original_arrival = adapter.requests[req.rid]
    req.cache_request_handle.attempt_id = 1
    live.on_requests_requeued([req], is_retracted=True)
    key, arrived = adapter.requests[req.rid]
    assert key == live.visible[req.rid] and arrived == original_arrival
    req.output_ids.extend(range(50))
    adapter.next_tick = 0.
    adapter.tick(now=0.)
    assert adapter.policy.programs["a"].tokens == 152
    assert adapter.counts["registered"] == 1
    live.close()


def test_a_port_without_streaming_context_accounting_cannot_qualify_as_thunderagent():
    observed = state()
    observed["paper_baseline"].pop("streaming_context_accounting")
    with pytest.raises(ValueError, match="streaming context growth"):
        validate_state("thunderagent", observed)


def test_new_program_cannot_use_stale_decode_occupancy_between_control_ticks(monkeypatch):
    live = runtime(monkeypatch, capacity=800)
    first = request("first", "a", tokens=200)
    live.register_visible_request(first)
    adapter = live._paper_baseline
    adapter.next_tick = float("inf")
    first.output_ids.extend(range(398))
    second = request("second", "b", tokens=100)
    live.register_visible_request(second)
    assert adapter.policy.programs["a"].tokens == 600
    assert not adapter.policy.programs["b"].active
    live.close()
