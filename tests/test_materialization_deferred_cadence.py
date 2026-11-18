import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_internal_comparison import observed
from tests.test_materialization_oracle_order import queued_children
from tests.test_subagent_materialization import cache, child, node, prepare
from tests.test_workflow_combination_runtime import configured


@pytest.fixture(params=("oracle", "r5"))
def deferred_children(request, monkeypatch, tmp_path):
    if request.param == "oracle":
        live, requests = queued_children(
            monkeypatch, tmp_path, ranking=True, tool_seconds=0.,
        )
        live._native_cache.token_to_kv_pool_allocator.available_size = lambda: 3000
    else:
        settings = configured(tmp_path)
        path = Path(settings["WORKFLOW_WORK_MODEL"])
        raw = json.loads(path.read_bytes())
        raw["free_dispatch_coalescing_window_ms"] = 2000
        path.write_text(json.dumps(raw))
        settings.update(
            WORKFLOW_WORK_MODEL_SHA256=hashlib.sha256(path.read_bytes()).hexdigest(),
            BELIEFKV_SUBAGENT_MATERIALIZATION="pressure_v1",
        )
        for name, value in arm_environment(settings, "full").items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
        live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "cadence.sock"))
        live.attach_native_cache(cache(node(1, 1000), device_free=3000))
        live._native_running_batch = NS(reqs=[])
        live._test_sequence = 10
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="parent-context"),
        ))
        requests = [child(live, name) for name in ("first", "second")]
    try:
        yield live, requests
    finally:
        live.close()


def test_permission_keeps_normal_window_and_changes_actual_service_only_at_its_boundary(
    deferred_children,
):
    live, requests = deferred_children
    admission, gate = live._agent_admission, live._subagent_materialization
    now = time.monotonic()
    admission.next_tick = now + 3.
    admission._deferred_dispatch.next_dispatch = now + 2.
    before = admission.counts["control_ticks"]

    prepare(live, requests, running=[object()], now=now)
    assert set(gate.last_decision["allowed"]) == {req.rid for req in requests}
    assert admission.next_tick == now + 3.
    assert admission._deferred_dispatch.next_dispatch == now + 2.
    admission.tick(now=now + .5)
    assert admission.counts["control_ticks"] == before
    assert not any(item.active for item in admission.policy.programs.values())

    admission.tick(now=now + 2.1)
    assert admission.counts["control_ticks"] == before + 1
    plan = live.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
    selected = select_native_prefill_candidates(
        requests, plan=plan, current_semantic_revision=live.semantic_revision,
    )
    assert {req.rid for req in selected.candidates} == {req.rid for req in requests}
    assert admission.policy.remaining_capacity() >= 0
    assert not live.physical_ledger.pending_count
    assert all(req.kv.kv_allocated_len == 0 for req in requests)


def test_capacity_revocation_still_wakes_control_and_blocks_native_materialization(
    deferred_children,
):
    live, requests = deferred_children
    admission, gate = live._agent_admission, live._subagent_materialization
    now = time.monotonic()
    prepare(live, requests, running=[object()], now=now)
    admission._deferred_dispatch.next_dispatch = now - 1.
    admission.tick(now=now)
    assert any(item.active for item in admission.policy.programs.values())

    admission.next_tick = now + 3.
    live._native_cache.token_to_kv_pool_allocator.available_size = lambda: 0
    gate.capacity_changed()
    prepare(live, requests, running=[object()], now=now + .1)
    assert not gate.last_decision["allowed"]
    assert admission.next_tick == 0.
    assert all(not admission.allows(live.visible[req.rid]) for req in requests)
    assert not any(item.active for item in admission.policy.programs.values())
    plan = live.plan_native_prefill(requests, running_batch=NS(reqs=[object()]), adder=None)
    selected = select_native_prefill_candidates(
        requests, plan=plan, current_semantic_revision=live.semantic_revision,
    )
    assert not selected.candidates
    assert admission.policy.remaining_capacity() >= 0
    assert not live.physical_ledger.pending_count
    assert all(req.kv.kv_allocated_len == 0 for req in requests)


def test_capacity_release_reindexes_held_children_without_restarting_the_normal_window(
    deferred_children,
):
    live, requests = deferred_children
    admission, gate = live._agent_admission, live._subagent_materialization
    now = time.monotonic()
    live._native_cache.token_to_kv_pool_allocator.available_size = lambda: 0
    admission._deferred_dispatch.next_dispatch = now - 1.
    prepare(live, requests, running=[object()], now=now)
    admission.tick(now=now)
    # The global dispatcher removes held entries; R5 may keep them indexed.
    if admission._combination is None:
        assert not any(
            live.visible[req.rid].invocation_id in admission._dispatch_pending for req in requests
        )
    assert not any(item.active for item in admission.policy.programs.values())

    admission.next_tick = now + 3.
    deadline = admission._deferred_dispatch.next_dispatch
    live._native_cache.token_to_kv_pool_allocator.available_size = lambda: 3000
    gate.capacity_changed()
    prepare(live, requests, running=[object()], now=now + .1)
    assert all(
        live.visible[req.rid].invocation_id in admission._dispatch_pending for req in requests
    )
    assert admission.next_tick == now + 3.
    assert admission._deferred_dispatch.next_dispatch == deadline

    before = admission.counts["control_ticks"]
    admission.tick(now=now + .5)
    assert admission.counts["control_ticks"] == before
    admission.tick(now=deadline + .1)
    plan = live.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
    selected = select_native_prefill_candidates(
        requests, plan=plan, current_semantic_revision=live.semantic_revision,
    )
    assert {req.rid for req in selected.candidates} == {req.rid for req in requests}
    assert admission.policy.remaining_capacity() >= 0
    assert not live.physical_ledger.pending_count
    assert all(req.kv.kv_allocated_len == 0 for req in requests)
