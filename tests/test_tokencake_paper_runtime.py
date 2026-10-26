from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.paper_baselines import (
    DISABLED_POLICIES, RUNTIME_ARMS, environment,
    validate_state, validate_tokencake_parameters,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.tokencake_baseline import TokenCakePaperAdapter
from tests.test_full_only_native_lifecycle import full_native
from tests.test_kvflow_runtime import selection
from tests.test_tokencake_public_runtime import finished_prefix, active_and_waiting, send, start
from tests.test_tokencake_runtime import submit, stage, materialize, complete


@pytest.fixture
def live(monkeypatch, full_native):
    for name, value in environment({}, "thunderagent").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime, clock = NativeAdmissionRuntime(), NS(now=10., sequence=0)
    adapter = TokenCakePaperAdapter(
        runtime, generation_budget=8, context_limit=64, running_limit=4,
        prefill_token_budget=32, chunked_prefill_tokens=8, policy_block_tokens=1,
        eviction_window_tokens=16, clock=lambda: clock.now,
    )
    runtime._paper_baseline = adapter
    cache, allocator, _, _, commits = full_native
    cache.load_back_threshold = 1
    runtime.attach_native_cache(cache)

    def acknowledged(commit):
        commits.append(commit)
        runtime.on_native_transfer_commit(commit)

    cache.on_hicache_transfer_commit = acknowledged
    result = runtime, adapter, cache, allocator, clock
    send(result, Kind.WORKFLOW_START, invocation=None)
    for agent in ("agent", "peer", "queued"):
        send(result, Kind.INVOCATION_CREATE, invocation=agent)
    yield result
    runtime.close()


def decode_rate(live, req):
    _, adapter, _, _, clock = live
    mode = NS(is_decode=lambda: True)
    adapter.completed(NS(reqs=[req], forward_mode=mode), ())
    clock.now += .01
    req.output_ids.append(1)
    adapter.completed(NS(reqs=[req], forward_mode=mode), ())
    assert adapter.temporal.decode_rate == pytest.approx(100.)


def cold_checkpoint(live):
    runtime, adapter, cache, allocator, _ = live
    _, leaf, record = finished_prefix(live)
    peer, queued = active_and_waiting(live)
    decode_rate(live, peer)
    start(live)
    before = allocator.available_size()
    adapter.tick()
    assert record.pending_retention
    cache.writing_check(finish_count=1)
    assert leaf.component_data[0].value is None
    assert leaf.component_data[0].host_value is not None and record.retained
    assert allocator.available_size() == before + 8
    assert adapter.temporal.last_first_fit == queued.rid
    assert not runtime.physical_disabled
    return peer, queued, leaf, record


def test_paper_first_fit_requires_observed_work_rate_and_ignores_recorded_generation_length(live, full_native):
    _, adapter, cache, _, _ = live
    finished_prefix(live)
    peer, queued = active_and_waiting(live)
    start(live)
    queued.sampling_params.max_new_tokens = 1
    adapter.tick()
    assert not full_native[2] and adapter.temporal.decode_rate is None
    assert adapter.temporal.counts["paper_first_fit_rejected"]
    decode_rate(live, peer)
    queued.sampling_params.max_new_tokens = 1000000
    adapter.tick()
    assert len(full_native[2]) == 1
    cache.writing_check(finish_count=1)
    complete(live, peer)


def test_paper_upload_waits_for_predicted_deadline_and_reserves_each_actual_half_grant(live, full_native):
    _, adapter, cache, _, clock = live
    peer, _, leaf, record = cold_checkpoint(live)
    checkpoint = adapter.temporal.checkpoints["first"]
    clock.now = checkpoint.finish_at - .2
    adapter.tick()
    assert not full_native[3]
    clock.now = checkpoint.finish_at - .05
    copied = []
    while len(copied) < 4:
        before = len(full_native[3])
        adapter.tick()
        assert len(full_native[3]) > before
        copied.extend(len(op.host_indices) for op in full_native[3][before:])
        assert record.retained
        assert leaf.component_data[0].host_lock_ref > 0
        cache.loading_check(finish_count=1)
    assert copied == [4, 2, 1, 1]
    assert sum(adapter.capacity.window.used.values()) == 47
    assert len(adapter.capacity.reservations) == 1
    send(live, Kind.TOOL_END, attributes={"tool_run_id": "external"})
    assert record.state == "finished" and not record.retained
    assert checkpoint.host_leases and leaf.component_data[0].host_lock_ref > 0
    send(live, Kind.LLM_SUBMIT, attributes={"request_id": "next"})
    req = submit(live, "next", list(range(9)), context="agent")
    selection(live[0], [req])
    stage(live, req)
    assert not adapter.capacity.reservations and not adapter.temporal.checkpoints
    assert leaf.component_data[0].host_lock_ref == 0
    materialize(live, [req])
    assert sum(adapter.capacity.window.used.values()) == 48
    complete(live, req)
    complete(live, peer)


def test_early_finish_during_backup_never_starts_a_late_predictive_upload(live, full_native):
    _, adapter, cache, _, clock = live
    finished_prefix(live)
    peer, _ = active_and_waiting(live)
    decode_rate(live, peer)
    start(live)
    adapter.tick()
    send(live, Kind.TOOL_END, attributes={"tool_run_id": "external"})
    assert not adapter.temporal.checkpoints
    cache.writing_check(finish_count=1)
    clock.now += 20.
    adapter.tick()
    assert not full_native[3] and not adapter.capacity.reservations
    complete(live, peer)


def test_tool_returns_during_upload_host_source_survives_until_real_consumer_admission(live):
    runtime, adapter, cache, _, clock = live
    peer, _, leaf, _ = cold_checkpoint(live)
    checkpoint = adapter.temporal.checkpoints["first"]
    clock.now = checkpoint.finish_at - .05
    adapter.tick()
    assert cache.ongoing_load_back
    send(live, Kind.TOOL_END, attributes={"tool_run_id": "external"})
    assert checkpoint.host_leases and leaf.component_data[0].host_lock_ref > 0
    req = submit(live, "next", list(range(9)), context="agent")
    selection(runtime, [req])
    assert not runtime.allow_native_prefill_candidate(req, running_batch=None, adder=None)
    cache.loading_check(finish_count=1)
    # Native demand recovery owns the rest; the adapter cannot claim unready KV.
    complete(live, peer)


@pytest.mark.parametrize("event", ("expiry", "retire", "abort", "pressure"))
def test_unused_upload_reservations_release_without_dropping_native_host_state(live, event):
    runtime, adapter, cache, _, clock = live
    peer, _, leaf, _ = cold_checkpoint(live)
    checkpoint = adapter.temporal.checkpoints["first"]
    clock.now = checkpoint.finish_at - .05
    adapter.tick()
    cache.loading_check(finish_count=1)
    assert adapter.capacity.reservations
    if event == "expiry":
        clock.now = checkpoint.expires_at + 1.
        adapter.tick()
    elif event == "retire":
        send(live, Kind.RETURN, attributes={"context_retired": True})
    elif event == "abort":
        adapter.aborted("first")
    else:
        assert runtime.on_native_decode_capacity_pressure(NS(reqs=[peer]))
    assert not adapter.capacity.reservations
    assert leaf.component_data[0].host_value is not None
    if event != "pressure":
        assert not adapter.temporal.checkpoints
    complete(live, peer)


def test_registered_paper_policy_is_distinct_from_public_and_spatial_components(live):
    _, adapter, *_ = live
    state = adapter.status()
    assert state["predictive_upload_enabled"] is True
    assert state["future_information_used"] is False
    assert state["generation_budget"] == 8
    assert state["complete_baseline"] is True
    assert RUNTIME_ARMS["tokencake_paper_observed"] == adapter.policy_name
    assert "tokencake_public_observed" not in RUNTIME_ARMS
    validate_state("tokencake_paper_observed", {
        **dict.fromkeys(DISABLED_POLICIES, False),
        "internal_comparison_mode": "scheduler_only", "paper_baseline": state,
    })
    parameters = {
        "generation_budget": 8, "context_limit": 64, "running_limit": 4,
        "prefill_token_budget": 32, "chunked_prefill_tokens": 8,
        "policy_block_tokens": 1, "eviction_window_tokens": 16,
    }
    validate_tokencake_parameters(state, parameters)
    state["generation_budget"] = 9
    with pytest.raises(ValueError, match="effective serving parameters"):
        validate_tokencake_parameters(state, parameters)
