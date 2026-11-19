from array import array
from dataclasses import replace
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime import ready_service_guard
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.sglang_v0520_admission import _request_key, select_native_prefill_candidates
from scripts.run_causal_comparison import preflight_runtime
from tests import test_workflow_combination_runtime as fixtures
from tests.test_full_only_native_lifecycle import full_native


@pytest.fixture
def queue(monkeypatch, tmp_path):
    config = fixtures.configured
    monkeypatch.setattr(fixtures, "configured", lambda path: {
        **config(path),
        "AGENT_READY_SERVICE_TIMEOUT_S": "1800",
        "AGENT_READY_SERVICE_FIT_AFTER_S": "120",
    })
    make_request = fixtures.request

    def bound(*args, **kwargs):
        req = make_request(*args, **kwargs)
        req.session_id, req.session_generation = "session-" + req.rid, 0
        req.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
        req.sampling_params = NS(max_new_tokens=100)
        req.is_retracted = req.retracted_stain = False
        req.finished = lambda: False
        return req

    monkeypatch.setattr(fixtures, "request", bound)
    runtime, admission, env = fixtures.runtime_with_queue(monkeypatch, tmp_path)
    control = admission._combination
    now = time.monotonic()
    keys = admission.latest
    for owner, key in keys.items():
        runtime.graph.invocations[owner].state = InvocationState.RUNNING_LLM
        runtime.context_sessions[key.context_id] = key
    runtime._capacity_waiting_queue = list(admission._live_requests.values())
    runtime._native_prefill_slots = 4
    runtime._native_cache.token_to_kv_pool_allocator.size_full = 10000
    headroom = NS(observable=True, device_full_free_tokens=5000, evictable=0)
    runtime._native_cache.evictable_size = lambda: headroom.evictable
    monkeypatch.setattr(
        ready_service_guard, "observe_static_full_mamba_headroom", lambda _cache: headroom,
    )
    prefix = {
        "checkpoint_tokens": 10, "device_checkpoint_tokens": 8,
        "missing_full_tokens": 2, "missing_mamba_slots": 0,
    }
    monkeypatch.setattr(runtime, "_read_request_reentry", lambda *args, **kw: prefix)
    monkeypatch.setattr(control, "select", lambda keys, reference, **kw: [
        key for key in keys if key.invocation_id == "b"
    ])
    # The ordinary selection has room for b + the running request. At most one
    # ordinary grant can yield, and the new total must still fit in raw budget.
    admission.policy.capacity_tokens = 660
    admission.policy.programs["c"].active = True
    runtime._native_running_batch = NS(reqs=[admission._live_requests["c-request"]])
    guard = admission._ready_service_guard
    guard.since = {"a": (keys["a"], now - 121.)}
    admission.next_tick = 0.
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    try:
        yield runtime, admission, guard, keys, env, now, headroom, prefix, records
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_one_overdue_request_uses_current_fit_and_reaches_native_admission(queue):
    runtime, a, guard, keys, env, now, headroom, _prefix, records = queue
    headroom.device_full_free_tokens, headroom.evictable = 128, 520
    a.tick(now=now)
    assert a.policy.last_selection["selected_in_priority_order"] == ("a",)
    assert {item.identity for item in a.policy.programs.values() if item.active} == {"a", "c"}
    assert a.policy.remaining_capacity() == 10
    assert not runtime.physical_ledger.pending_count
    assert not a.counts["public_timeout_force_resumed"]
    assert guard.fit_target == keys["a"]
    assert a.counts["ready_service_fit_native_checks"] == 1
    assert not a.counts["ready_service_fit_earlier_grant_native_checks"]
    assert a.counts["ready_service_fit_grants"] == a.counts["ready_service_fit_displaced_grants"] == 1
    grant = next(row for row in records if row["event"] == "ready_service_fit_granted")
    assert grant["displaced"]["request_id"] == "b-request"
    assert grant["selected_tokens"] == 400
    assert grant["first_chunk_and_restore_tokens"] > grant["device_free_tokens"]
    assert grant["device_prefix_pin_debit_tokens"] == 8
    assert grant["available_after_prefix_pin_tokens"] == 640
    assert grant["full_input_and_growth_tokens"] == 494

    requests = [a._live_requests[name + "-request"] for name in ("a", "b")]
    plan = runtime.plan_native_prefill(requests, running_batch=runtime._native_running_batch, adder=None)
    actual = select_native_prefill_candidates(
        requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
    )
    assert actual.candidates == (requests[0],)
    a.native_service(NS(reqs=[requests[0]]), ())
    a.native_service(NS(reqs=[requests[0]]), ())
    assert a.counts["ready_service_fit_first_native_service"] == 1
    assert guard.fit_target == keys["a"]
    requests[0].finished = lambda: True
    requests[0].output_ids.append(1)
    runtime.on_batch_completed(NS(reqs=[requests[0]]))
    assert guard.fit_target is None
    assert a.counts["ready_service_fit_native_completed"] == 1
    assert sum(row["event"] == "ready_service_fit_first_native_service" for row in records) == 1
    validate_reference_admission(
        a.status(), free_continuation_admission=True, free_dispatch_interval_ms=500,
        control_interval_ms=2000, workflow_size_resume=True,
        workflow_work_model_sha256=env["WORKFLOW_WORK_MODEL_SHA256"],
        workflow_combination_admission=True, ready_service_fit_after_s=120,
    )
    with pytest.raises(ValueError, match="fit differs"):
        validate_reference_admission(a.status(), workflow_combination_admission=True)


@pytest.mark.parametrize("change", (
    "recent", "raw_capacity", "two_replacements", "first_chunk", "unknown_prefix", "no_checkpoint",
    "prefix_pin", "full_input_growth", "native_slot", "request_rows",
    "unready", "stale_identity", "not_in_native_queue", "already_materialized", "join", "sibling",
    "existing_fit_target", "attempted", "materialization_hold",
))
def test_preserve_normal_short_selection_when_any_fit_condition_fails(queue, monkeypatch, change):
    runtime, a, guard, keys, _env, now, headroom, prefix, _records = queue
    expected = ("b",)
    if change == "recent":
        guard.since["a"] = keys["a"], now - 119.
    elif change == "raw_capacity":
        a.policy.capacity_tokens = 640
        # Decayed headroom can appear to fit the target. Only raw headroom counts.
        monkeypatch.setattr(a.policy, "remaining_capacity_with_decay", lambda **kw: 500)
    elif change == "two_replacements":
        a.policy.capacity_tokens = 800
        a.policy.programs["c"].active = False
        monkeypatch.setattr(a._combination, "select", lambda *args, **kw: [keys["b"], keys["c"]])
        a.policy.programs["a"].tokens = 455
        expected = ("b", "c")
    elif change == "first_chunk":
        runtime._native_input_reserve = 289
    elif change == "unknown_prefix":
        prefix.pop("device_checkpoint_tokens")
    elif change == "no_checkpoint":
        a.policy.programs["a"].steps = 2
        monkeypatch.setattr(runtime, "_read_request_reentry", lambda *args, **kw: None)
    elif change == "prefix_pin":
        headroom.device_full_free_tokens, headroom.evictable = 100, 400
    elif change == "full_input_growth":
        # The first chunk and restore fit, but native's full-input comparison
        # is strict at equality, even when chunked prefill is active.
        headroom.device_full_free_tokens, headroom.evictable = 100, 402
    elif change == "native_slot":
        runtime._native_max_running = 1
    elif change == "request_rows":
        runtime._native_cache.req_to_token_pool.available_size = lambda: 0
    elif change == "unready":
        runtime.graph.invocations["a"].active_tool_calls["tool"] = "execute"
    elif change == "stale_identity":
        guard.since["a"] = replace(keys["a"], context_epoch=4), now - 121.
    elif change == "not_in_native_queue":
        runtime._capacity_waiting_queue.remove(a._live_requests["a-request"])
    elif change == "already_materialized":
        a._live_requests["a-request"].kv.kv_committed_len = 1
    elif change == "join":
        monkeypatch.setattr(a, "_protected_join", lambda key: key == keys["b"])
    elif change == "sibling":
        monkeypatch.setattr(a._combination, "_group", lambda key, groups: (
            "join", 2 if key == keys["b"] else 1, 0,
        ))
    elif change == "existing_fit_target":
        guard.fit_target = keys["c"]
    elif change == "attempted":
        guard.fit_attempted.add(keys["a"])
    elif change == "materialization_hold":
        monkeypatch.setattr(a, "_materialization_allowed", lambda key: key != keys["a"])
    a.tick(now=now)
    assert a.policy.last_selection["selected_in_priority_order"] == expected
    assert not a.policy.programs["a"].active
    assert not a.counts["ready_service_fit_grants"]
    if change in ("raw_capacity", "two_replacements"):
        assert not a.counts["ready_service_fit_native_checks"]


@pytest.mark.parametrize(("change", "displaced", "probes"), (
    ("fits", "b", 1),
    ("two_qualifying", "d", 1),
    ("native_veto", None, 1),
    ("join_parent", None, 0),
    ("join_sibling", None, 0),
    ("last_join_child", None, 0),
    ("final_stage", None, 0),
    ("logical_alone", None, 0),
))
def test_fit_can_replace_an_earlier_ordinary_grant(queue, monkeypatch, change, displaced, probes):
    runtime, a, guard, keys, _env, _now, headroom, _prefix, records = queue
    for index, (identity, tokens) in enumerate((("d", 30), ("e", 20), ("f", 30)), start=3):
        runtime.on_events((
            replace(fixtures.observed(2 * index, Kind.WORKFLOW_START), workflow_id=identity),
            replace(fixtures.observed(
                2 * index + 1, Kind.INVOCATION_CREATE,
                invocation_id=identity, context_id=identity + "-context",
            ), workflow_id=identity),
        ))
        req = fixtures.request(identity + "-request", identity, tokens=tokens, workflow=identity)
        req.output_ids.clear()
        assert runtime.register_visible_request(req)
        runtime.graph.invocations[identity].state = InvocationState.RUNNING_LLM
        runtime.context_sessions[keys[identity].context_id] = keys[identity]
        runtime._capacity_waiting_queue.append(req)

    ordinary = ("b", "d", "e")
    monkeypatch.setattr(a._combination, "select", lambda *args, **kw: [keys[i] for i in ordinary])
    now = time.monotonic()
    guard.fit_after_s = 600
    guard.since["a"] = keys["a"], now - 601.
    # Raw headroom is 650: the 400-token aged grant is 250 over with all three
    # ordinary grants. The last costs 120, the preceding grants cost 130 and 250.
    a.policy.capacity_tokens = 900
    if change == "fits":
        monkeypatch.setattr(runtime, "_last_join_child_has_waiting_parent", lambda key: key == keys["d"])
    elif change == "two_qualifying":
        a.policy.capacity_tokens = 1025  # A 125-token deficit also allows replacing d.
    elif change == "native_veto":
        headroom.device_full_free_tokens = 502  # Strict full-input gate at equality.
        guard.since["f"] = keys["f"], now - 600.  # A second candidate could pass native fit.
    elif change == "join_parent":
        monkeypatch.setattr(a, "_protected_join", lambda key: key == keys["b"])
    elif change == "join_sibling":
        monkeypatch.setattr(a._combination, "_group", lambda key, groups: (
            "join", 2 if key == keys["b"] else 1, 0,
        ))
    elif change == "last_join_child":
        monkeypatch.setattr(runtime, "_last_join_child_has_waiting_parent", lambda key: key == keys["b"])
    elif change == "final_stage":
        stage = NS(semantic_only=False, child_id="b", child_epoch=keys["b"].context_epoch)
        runtime._final_request_stages[keys["b"].request_id] = stage
        monkeypatch.setattr(runtime, "_live_final_stage", lambda candidate: candidate is stage)
    elif change == "logical_alone":
        a.policy.programs["a"].tokens = 551

    a.tick(now=now)
    expected = tuple(i for i in ordinary if i != displaced) + (("a",) if displaced else ())
    assert a.policy.last_selection["selected_in_priority_order"] == expected
    assert {item.identity for item in a.policy.programs.values() if item.active} == {*expected, "c"}
    assert a.counts["ready_service_fit_native_checks"] == probes
    assert a.counts["ready_service_fit_earlier_grant_native_checks"] == probes
    assert a.counts["ready_service_fit_native_skips"] == (change == "native_veto")
    assert a.counts["ready_service_fit_displaced_grants"] == bool(displaced)
    assert guard.fit_target == (keys["a"] if displaced else None)
    assert guard.fit_attempted == ({keys["a"]} if displaced else set())
    assert not runtime.physical_ledger.pending_count
    grants = [row for row in records if row["event"] == "ready_service_fit_granted"]
    assert len(grants) == bool(displaced)
    if displaced:
        assert grants[0]["displaced"]["request_id"] == keys[displaced].request_id
        assert grants[0]["selected_tokens"] <= grants[0]["logical_capacity_tokens"]
        assert a.policy.remaining_capacity() >= 0


def test_fit_adds_to_existing_grants_when_raw_capacity_allows(queue):
    _runtime, a, guard, keys, _env, now, _headroom, _prefix, _records = queue
    a.policy.capacity_tokens = 900
    a.tick(now=now)
    assert a.policy.last_selection["selected_in_priority_order"] == ("b", "a")
    assert guard.fit_target == keys["a"]
    assert a.counts["ready_service_fit_grants"] == 1
    assert not a.counts["ready_service_fit_displaced_grants"]


def test_first_request_without_a_session_checkpoint_uses_full_input_bound(queue, monkeypatch):
    runtime, a, guard, keys, _env, now, _headroom, _prefix, records = queue
    monkeypatch.setattr(runtime, "_read_request_reentry", lambda *args, **kw: None)
    a.tick(now=now)
    assert guard.fit_target == keys["a"]
    grant = next(row for row in records if row["event"] == "ready_service_fit_granted")
    assert grant["host_restore_tokens"] is None
    assert grant["device_prefix_pin_debit_tokens"] == 0
    assert grant["first_chunk_and_restore_tokens"] == 302


@pytest.mark.parametrize("max_new_tokens, fits", [(4, True), (21, False)])
def test_fit_matches_real_full_only_prefill_after_prefix_lock(full_native, max_new_tokens, fits):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.managers.schedule_policy import AddReqResult, PrefillAdder
    from sglang.srt.sampling.sampling_params import SamplingParams
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _writes, loads, _commits = full_native
    _, generation = insert_session(cache, list(range(1, 9)), "tail")
    insert_session(cache, list(range(20, 40)), "other")
    assert allocator.alloc(32).numel() == 32
    assert (allocator.available_size(), cache.evictable_size()) == (4, 28)
    req = Req(
        rid="waiting", origin_input_text="", origin_input_ids=array("q", range(1, 11)),
        sampling_params=SamplingParams(max_new_tokens=max_new_tokens), session_id="tail",
        beliefkv_metadata={
            "root_workflow_id": "w", "invocation_id": "tail",
            "context_id": "c", "context_epoch": 0,
        },
    )
    req.session_generation = generation
    key = _request_key(req)
    running = NS(reqs=[])
    runtime = NS(
        _native_cache=cache, physical_disabled=False, _native_prefill_slots=4,
        _capacity_waiting_queue=(req,), _native_running_batch=running, _native_max_running=8,
        _native_page_size=1, _native_input_reserve=4, _native_chunk_input_reserve=4,
        _unallocated_waiting_full_request=lambda k, r: k == key and r is req,
        _live_critical_parent_demand=lambda: None,
        _read_request_reentry=lambda r, **_: cache.inspect_beliefkv_reentry(r),
    )
    guard = ready_service_guard.ReadyServiceGuard(
        NS(runtime=runtime, policy=NS(programs={"tail": NS(steps=2)})), fit_after_s=600,
    )
    fit = guard._native_fit(key, req)
    assert (fit is not None) is fits
    assert (allocator.available_size(), cache.evictable_size()) == (4, 28)
    assert not loads
    adder = PrefillAdder(
        page_size=1, tree_cache=cache, token_to_kv_pool_allocator=allocator,
        running_batch=running, new_token_ratio=1., rem_input_tokens=4, rem_chunk_tokens=4,
    )
    req.init_next_round_input(cache)
    assert len(req.prefix_indices) == 8
    assert 2 + max_new_tokens + 1 < adder.rem_total_tokens
    result = adder.add_one_req(req, has_chunked_req=False, truncation_align_size=None)
    assert (adder.can_run_list == [req]) is fits
    if not fits:
        assert result is AddReqResult.NO_TOKEN


def test_fit_is_attempted_once_for_same_request_even_after_blocking(queue):
    runtime, a, guard, keys, _env, now, _headroom, _prefix, _records = queue
    a.tick(now=now)
    assert guard.fit_target == keys["a"]
    runtime.graph.invocations["a"].active_tool_calls["tool"] = "execute"
    a.policy.programs["a"].active = False
    guard.begin(now + 1.)
    assert guard.fit_target is None
    assert keys["a"] not in {key for key, _ in guard.since.values()}
    runtime.graph.invocations["a"].active_tool_calls.clear()
    guard.begin(now + 2.)
    guard.begin(now + 123.)
    assert keys["a"] in guard.fit_attempted
    a.tick(now=now + 123.)
    assert guard.fit_target is None
    assert a.counts["ready_service_fit_grants"] == 1


def test_hard_guard_remains_independent_of_fit(queue):
    _runtime, a, guard, keys, _env, now, headroom, _prefix, _records = queue
    headroom.device_full_free_tokens = 0
    guard.since["a"] = keys["a"], now - 1800.
    a.requests["a-request"] = keys["a"], now - 1800.
    a.tick(now=now)
    assert guard.target == keys["a"]
    assert a.counts["public_timeout_force_resumed"] == 1
    assert not a.counts["ready_service_fit_grants"]


def test_optional_setting_uses_production_preflight_and_respects_paper_isolation(tmp_path):
    from beliefkv.experiments.paper_baselines import environment as paper_environment

    config = fixtures.configured(tmp_path)
    config.update(AGENT_READY_SERVICE_TIMEOUT_S="1800", AGENT_READY_SERVICE_FIT_AFTER_S="120")
    for mode in ("scheduler_only", "native_matched"):
        assert arm_environment(config, mode)["BELIEFKV_AGENT_READY_SERVICE_FIT_AFTER_S"] == "0"
    assert paper_environment(config, "thunderagent_acting_decay")[
        "BELIEFKV_AGENT_READY_SERVICE_FIT_AFTER_S"
    ] == "0"
    result = preflight_runtime(arm_environment(config, "full"), str(tmp_path))
    assert result["verified_configuration"]["agent_ready_service_fit_after_s"] == 120
    for changes in (
        {"AGENT_READY_SERVICE_FIT_AFTER_S": "1800"},
        {"AGENT_READY_SERVICE_TIMEOUT_S": "0"},
        {"WORKFLOW_COMBINATION_ADMISSION": "0"},
    ):
        with pytest.raises(ValueError, match="ready service fit"):
            arm_environment({**config, **changes}, "full")
