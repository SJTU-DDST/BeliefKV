from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.continuum_pins import ContinuumPins
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from tests.test_full_only_native_lifecycle import full_native


def completed(cache, *, invocation="a", tokens=(1, 2, 3, 4)):
    from test_beliefkv_terminal_capacity import insert_session

    node, generation = insert_session(cache, list(tokens), invocation)
    return node, PrefillCandidateKey(
        "request-" + invocation, "workflow", invocation, "context-" + invocation,
        0, 0, invocation, generation,
    )


@pytest.fixture
def retention(full_native):
    cache, _, writes, loads, _ = full_native
    pins = ContinuumPins()
    yield cache, pins
    pins.close()
    assert not writes and not loads


def test_multiple_programs_retain_real_native_device_state(retention):
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams

    cache, pins = retention
    # Exercise native drop eviction without the fixture's custom-only D2H stub.
    cache.is_write_back = False
    first, a = completed(cache)
    second, b = completed(cache, invocation="b", tokens=(5, 6, 7, 8))
    assert pins.retain(cache, a, ttl=2., now=0.)
    assert pins.retain(cache, b, ttl=2., now=0.)
    assert first.component_data[0].lock_ref == second.component_data[0].lock_ref == 1
    assert cache.evict(EvictParams(num_tokens=8)).num_tokens_evicted == 0
    assert pins.refresh({}, now=2.) == (a, b)
    assert first.component_data[0].lock_ref == second.component_data[0].lock_ref == 0
    assert cache.evict(EvictParams(num_tokens=8)).num_tokens_evicted == 8


def test_queued_successor_keeps_pin_past_deadline(retention):
    cache, pins = retention
    node, key = completed(cache)
    assert pins.retain(cache, key, ttl=2., now=0.)
    successor = replace(key, request_id="next", context_epoch=1)
    assert not pins.refresh({key.invocation_id: successor}, now=100.)
    assert node.component_data[0].lock_ref == 1
    assert pins.refresh({}, now=100.) == (key,)


def test_successor_native_lock_survives_ttl_release(retention):
    cache, pins = retention
    node, key = completed(cache)
    pins.retain(cache, key, ttl=2., now=0.)
    native = cache.inc_lock_ref(node.id)
    assert pins.refresh({}, now=2.) == (key,)
    assert node.component_data[0].lock_ref == 1
    cache.dec_lock_ref(node.id, native.to_dec_params())
    assert node.component_data[0].lock_ref == 0


@pytest.mark.parametrize("change", (
    {"session_generation": 2}, {"session_id": "changed"},
    {"context_id": "changed"}, {"root_workflow_id": "changed"},
))
def test_queued_reentry_cannot_extend_a_different_session(retention, change):
    cache, pins = retention
    node, key = completed(cache)
    pins.retain(cache, key, ttl=2., now=0.)
    successor = replace(key, **change)
    assert pins.refresh({key.invocation_id: successor}, now=1.) == (key,)
    assert node.component_data[0].lock_ref == 0


def test_shared_prefixes_have_independent_lock_receipts(retention):
    cache, pins = retention
    first, a = completed(cache)
    pins.retain(cache, a, ttl=2., now=0.)
    second, b = completed(cache, invocation="b", tokens=(1, 2, 5, 6))
    common = first.parent
    assert pins.retain(cache, b, ttl=3., now=0.)
    assert common.component_data[0].lock_ref == 2
    assert pins.release("a", "finished") == a
    assert first.component_data[0].lock_ref == 0
    assert second.component_data[0].lock_ref == common.component_data[0].lock_ref == 1
    pins.release("b", "finished")
    assert common.component_data[0].lock_ref == 0


def test_retain_same_shared_node_does_not_steal_another_program_lock(retention):
    cache, pins = retention
    node, a = completed(cache)
    pins.retain(cache, a, ttl=2., now=0.)
    same, b = completed(cache, invocation="b")
    assert same is node
    assert pins.retain(cache, b, ttl=3., now=0.)
    assert node.component_data[0].lock_ref == 2
    pins.release("a", "finished")
    assert node.component_data[0].lock_ref == 1


def test_next_completion_replaces_old_prefix_pin_after_growth(retention):
    cache, pins = retention
    old, key = completed(cache)
    pins.retain(cache, key, ttl=2., now=0.)
    tail, successor = completed(cache, tokens=tuple(range(1, 9)))
    assert pins.retain(cache, successor, ttl=2., now=1.)
    assert tail.parent is old
    assert old.component_data[0].lock_ref == tail.component_data[0].lock_ref == 1
    pins.release("a", "finished")
    assert old.component_data[0].lock_ref == tail.component_data[0].lock_ref == 0


@pytest.mark.parametrize("ttl", (0., .001, .01))
def test_zero_ttl_completion_releases_prior_retention(retention, ttl):
    cache, pins = retention
    node, key = completed(cache)
    pins.retain(cache, key, ttl=2., now=0.)
    assert not pins.retain(cache, key, ttl=ttl, now=1.)
    assert not pins.pins
    assert node.component_data[0].lock_ref == 0


def test_deadlock_breaker_selects_latest_deadline_including_queued_program(retention):
    cache, pins = retention
    first, a = completed(cache)
    second, b = completed(cache, invocation="b", tokens=(5, 6, 7, 8))
    pins.retain(cache, a, ttl=2., now=0.)
    pins.retain(cache, b, ttl=3., now=0.)
    assert not pins.refresh({"a": a, "b": b}, now=100.)
    assert pins.relieve_pressure(running_requests=0) == b
    assert first.component_data[0].lock_ref == 1
    assert second.component_data[0].lock_ref == 0


def test_running_contention_does_not_silently_replace_running_preemption(retention):
    cache, pins = retention
    _, key = completed(cache)
    pins.retain(cache, key, ttl=2., now=0.)
    assert pins.relieve_pressure(running_requests=2) is None
    assert key.invocation_id in pins.pins


@pytest.mark.parametrize("fault", ("generation", "layout", "missing_session", "pending"))
def test_unproven_native_state_cannot_be_pinned(retention, fault):
    cache, pins = retention
    node, key = completed(cache)
    if fault == "generation":
        key = replace(key, session_generation=key.session_generation + 1)
    elif fault == "layout":
        cache = NS(tree_components=(0, 2))
    elif fault == "missing_session":
        key = replace(key, session_id=None)
    else:
        node.load_back_pending_id = 7
    assert not pins.retain(cache, key, ttl=2., now=0.)
    assert node.component_data[0].lock_ref == 0


@pytest.mark.parametrize("value", (-1, True, "2", float("nan"), float("inf")))
def test_invalid_timing_never_changes_existing_pin(retention, value):
    cache, pins = retention
    node, key = completed(cache)
    pins.retain(cache, key, ttl=2., now=0.)
    with pytest.raises(ValueError):
        pins.retain(cache, key, ttl=value, now=1.)
    with pytest.raises(ValueError):
        pins.refresh({}, now=value)
    assert node.component_data[0].lock_ref == 1


def test_close_balances_all_receipts_and_is_idempotent(retention):
    cache, pins = retention
    node, key = completed(cache)
    pins.retain(cache, key, ttl=2., now=0.)
    pins.close()
    pins.close()
    assert not pins.pins and node.component_data[0].lock_ref == 0
