from array import array
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.paper_baselines import environment, validate_server_arguments
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_continuum_pins import completed
from tests.test_full_only_native_lifecycle import full_native
from tests.test_paper_baseline_runtime import request


@pytest.fixture
def continuum(full_native, monkeypatch):
    for name, value in environment({}, "continuum_public").items():
        monkeypatch.setenv(name, value)
    live = NativeAdmissionRuntime()
    cache = full_native[0]
    live.attach_native_cache(cache)
    yield live, cache, live._paper_baseline
    live.close()


def bound_request(cache, *, invocation="a", identity="one", tokens=(1, 2, 3, 4)):
    node, key = completed(cache, invocation=invocation, tokens=tokens)
    req = request(identity, invocation, tokens=len(tokens))
    req.origin_input_ids = array("q", tokens)
    req.session_id, req.session_generation = key.session_id, key.session_generation
    return req, node


def test_continuum_preserves_program_fcfs_across_tool_reentry(continuum, monkeypatch):
    live, cache, paper = continuum
    clock = [1.]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    first, _ = bound_request(cache)
    live.register_visible_request(first)
    paper.tools.identify = lambda req: None
    live.on_batch_completed(NS(reqs=[first]))
    clock[0] = 2.
    newer = request("new", "b")
    live.register_visible_request(newer)
    clock[0] = 3.
    again = request("again", "a")
    live.register_visible_request(again)
    plan = live.plan_native_prefill([newer, again], running_batch=NS(reqs=[]), adder=None)
    assert [key.request_id for key in plan.prioritized] == ["again", "new"]
    assert again.priority == first.priority < newer.priority
    assert not live.enable_join_parent_priority and not live.enable_resident_first


def test_completed_tool_physically_pins_then_queued_successor_inherits_ttl(continuum, monkeypatch):
    live, cache, paper = continuum
    clock = [1.]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    first, leaf = bound_request(cache)
    live.register_visible_request(first)
    paper.tools.identify = lambda req: "shell:grep"
    clock[0] = 2.
    live.on_batch_completed(NS(reqs=[first]))
    assert leaf.component_data[0].lock_ref == 1
    clock[0] = 2.1
    successor, _ = bound_request(cache, identity="next")
    live.register_visible_request(successor)
    clock[0] = 10.
    paper.tick()
    assert leaf.component_data[0].lock_ref == 1
    newer = request("b", "b")
    live.register_visible_request(newer)
    plan = live.plan_native_prefill([newer, successor], running_batch=NS(reqs=[]), adder=None)
    selected = select_native_prefill_candidates(
        [newer, successor], plan=plan, current_semantic_revision=live.semantic_revision,
    )
    assert selected.candidates == (successor, newer)
    receipt = cache.inc_lock_ref(leaf.id)
    live.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
    paper.tick()
    assert "a" not in paper.pins.pins
    assert leaf.component_data[0].lock_ref == 1
    cache.dec_lock_ref(leaf.id, receipt.to_dec_params())


def test_idle_allocation_pressure_releases_latest_pin_without_admission_bypass(continuum):
    live, cache, paper = continuum
    req, leaf = bound_request(cache)
    live.register_visible_request(req)
    paper.tools.identify = lambda req: "tool:read_file"
    live.on_batch_completed(NS(reqs=[req]))
    other = request("other", "b")
    live.register_visible_request(other)
    live.plan_native_prefill([other], running_batch=NS(reqs=[]), adder=None)
    assert live.on_prefill_candidate_result(other, admitted=False, result="NO_TOKEN")
    assert leaf.component_data[0].lock_ref == 0
    assert not live.allow_prefill_capacity_bypass(
        other, native_budget_available=True, bypassed=0,
    )


def test_pin_release_does_not_replace_preemption_when_other_work_is_running(continuum):
    live, cache, paper = continuum
    req, leaf = bound_request(cache)
    live.register_visible_request(req)
    paper.tools.identify = lambda req: "tool:read_file"
    live.on_batch_completed(NS(reqs=[req]))
    other = request("other", "b")
    live.register_visible_request(other)
    live.plan_native_prefill([other], running_batch=NS(reqs=[NS()]), adder=None)
    assert not live.on_prefill_candidate_result(other, admitted=False, result="NO_TOKEN")
    assert not live.on_native_decode_capacity_pressure(NS(reqs=[NS(), NS()]))
    assert leaf.component_data[0].lock_ref == 1
    assert live.on_native_decode_capacity_pressure(NS(reqs=[NS()]))
    assert leaf.component_data[0].lock_ref == 0


def test_native_retraction_updates_attempt_without_new_arrival_or_gap(continuum):
    live, _, paper = continuum
    req = request("one", "a")
    live.register_visible_request(req)
    original = paper.requests[req.rid]
    live.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    req.cache_request_handle.attempt_id = 1
    live.on_requests_requeued([req], is_retracted=True)
    assert paper.requests[req.rid] == replace(original, attempt_id=1)
    assert paper.queued["a"] == paper.requests[req.rid]
    assert paper.counts["registered"] == 1
    assert paper.counts["native_retractions"] == 1
    assert not paper.history.tool_gap_counts


@pytest.mark.parametrize("terminal", ("workflow", "abort", "shutdown"))
def test_terminal_cleanup_balances_the_physical_ttl_lock(continuum, terminal):
    live, cache, paper = continuum
    req, leaf = bound_request(cache)
    live.register_visible_request(req)
    paper.tools.identify = lambda req: "tool:read_file"
    live.on_batch_completed(NS(reqs=[req]))
    if terminal == "workflow":
        paper.events((RuntimeEvent("end", 1., Kind.WORKFLOW_END, "workflow"),))
    elif terminal == "abort":
        # Pending successors are still associated with their retained program.
        successor = request("next", "a")
        live.register_visible_request(successor)
        live.on_abort_request(NS(rid="next", abort_all=False))
    else:
        paper.close()
    assert leaf.component_data[0].lock_ref == 0
    assert not paper.pins.pins


def test_finished_native_request_is_accounted_once(continuum):
    live, _, paper = continuum
    req = request("one", "a")
    live.register_visible_request(req)
    paper.tools.identify = lambda req: None
    batch = NS(reqs=[req])
    live.on_batch_completed(batch)
    live.on_batch_completed(batch)
    assert paper.counts["completed"] == 1


def test_abort_all_releases_tool_wait_pins_without_pending_requests(continuum):
    live, cache, paper = continuum
    req, leaf = bound_request(cache)
    live.register_visible_request(req)
    paper.tools.identify = lambda req: "tool:read_file"
    live.on_batch_completed(NS(reqs=[req]))
    assert not paper.requests and leaf.component_data[0].lock_ref == 1
    live.on_abort_request(NS(rid="", abort_all=True))
    assert not paper.pins.pins and leaf.component_data[0].lock_ref == 0


def test_continuum_rejects_missing_native_pressure_policy():
    settings = {
        "enable_priority_scheduling": True, "disable_priority_preemption": True,
        "schedule_low_priority_values_first": True, "retraction_policy": "priority",
    }
    validate_server_arguments("continuum_public", settings)
    for name in settings:
        with pytest.raises(ValueError, match="native program-order"):
            validate_server_arguments(
                "continuum_public", {key: value for key, value in settings.items() if key != name},
            )
