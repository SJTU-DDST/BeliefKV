from types import SimpleNamespace as NS
import json

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.agentix_capacity import AgentixHostBacking, AgentixNativeCapacity
from beliefkv.runtime.agentix_swap import AgentixNativeSwap
from scripts import qualify_agentix_pressure as qualifier
from tests.test_full_only_native_lifecycle import full_native


@pytest.fixture
def pressure(full_native):
    """Real CPU native tree/pools; synchronous copy and event stand-ins."""
    import torch
    from sglang.srt.mem_cache.memory_pool_host import LogicalHostPool
    from sglang.srt.server_args import ServerArgs, set_global_server_args_for_scheduler

    set_global_server_args_for_scheduler(ServerArgs(model_path="dummy"))
    cache, allocator, _writes, _loads, _commits = full_native
    fixture = qualifier.NativePressureFixture.__new__(qualifier.NativePressureFixture)
    fixture.torch, fixture.cache, fixture.allocator = torch, cache, allocator
    fixture.pool, fixture.rows = allocator._kvcache, cache.req_to_token_pool
    fixture.host = cache.host_pool_group.anchor_entry.host_pool
    slots = LogicalHostPool(size=257, page_size=1)
    fixture.host.size = 257
    fixture.host.alloc, fixture.host.free = slots.alloc, slots.free
    fixture.host.available_size = slots.available_size
    cache.host_pool_group.available_size = slots.available_size
    cache.host_pool_group.free = lambda indices, **_kwargs: slots.free(indices)
    fixture.sync = lambda: None
    cache.load_back_threshold = 1
    fixture.actor = AgentixNativeSwap(cache, drained=lambda: True)
    fixture.capacity = AgentixNativeCapacity(
        fixture.actor.transfers, context_limit=64, decode_growth_tokens=4,
    )
    fixture.backing = AgentixHostBacking(fixture.capacity, fixture.actor)
    fixture.active, fixture.expected, fixture.original = {}, {}, {}
    fixture.host_blocker = None
    storage = [
        (torch.zeros((257, *key.shape[1:]), dtype=key.dtype),
         torch.zeros((257, *value.shape[1:]), dtype=value.dtype))
        for key, value in zip(fixture.pool.k_buffer, fixture.pool.v_buffer, strict=True)
    ]

    def write(indices, **kwargs):
        host_indices = slots.alloc(len(indices))
        if host_indices is None:
            return None
        for (key, value), (host_key, host_value) in zip(
            zip(fixture.pool.k_buffer, fixture.pool.v_buffer, strict=True),
            storage, strict=True,
        ):
            host_key[host_indices], host_value[host_indices] = key[indices], value[indices]
        operation = NS(
            node_id=kwargs["node_id"], node_ids=[kwargs["node_id"]],
            host_indices=host_indices, device_indices=indices.clone(),
            beliefkv_command_id=kwargs.get("beliefkv_command_id"),
        )
        before = kwargs.get("beliefkv_before_enqueue")
        if before is not None and not before(operation):
            slots.free(host_indices)
            return None
        cache.cache_controller.write_queue.append(operation)
        if not kwargs.get("beliefkv_defer_writing", False):
            cache.cache_controller.start_writing()
        return host_indices

    def load(host_indices, **kwargs):
        indices = allocator.alloc(len(host_indices))
        assert indices is not None
        for (key, value), (host_key, host_value) in zip(
            zip(fixture.pool.k_buffer, fixture.pool.v_buffer, strict=True),
            storage, strict=True,
        ):
            key[indices], value[indices] = host_key[host_indices], host_value[host_indices]
        operation = NS(
            node_id=kwargs["node_id"], node_ids=[kwargs["node_id"]],
            host_indices=host_indices.clone(), device_indices=indices,
            beliefkv_command_id=kwargs.get("beliefkv_command_id"),
        )
        before = kwargs.get("beliefkv_before_enqueue")
        if before is not None and not before(operation):
            allocator.free(indices)
            return None
        cache.cache_controller.load_queue.append(operation)
        return indices

    cache.cache_controller.write, cache.cache_controller.load = write, load
    native_write_check, native_load_check = cache.writing_check, cache.loading_check

    def writing_check(*args, **kwargs):
        kwargs.setdefault("finish_count", 1)
        return native_write_check(*args, **kwargs)

    def loading_check(*args, **kwargs):
        kwargs.setdefault("finish_count", 1)
        return native_load_check(*args, **kwargs)

    cache.writing_check, cache.loading_check = writing_check, loading_check
    yield fixture
    fixture.actor.close()


@pytest.mark.parametrize("full_native", (128,), indirect=True)
def test_pressure_scenarios_use_native_allocations_and_preserve_each_request(pressure):
    growth = qualifier.backing_growth(pressure)
    assert growth["initial"]["normal_live_budget_tokens"] == 193
    assert growth["selected_growth_tokens_after_drain"] == 34
    assert growth["remaining_paused"] == 4
    refusal = qualifier.host_refusal(pressure)
    assert refusal["refused"]["host_capacity_refused"] is True
    assert refusal["actual_host_blocker_slots_released"] > 0
    assert pressure.actor.counts["backup_declined"] == 1
    assert pressure.actor.counts["resumed_requests"] == 2
    for req in tuple(pressure.active.values()):
        pressure.finish(req)
    for pin in tuple(pressure.actor.paused.values()):
        req = pin.proof.request
        pressure.restore(req)
        pressure.finish(req)
    assert not pressure.actor.paused and not pressure.actor.pending
    assert pressure.actor.counts["resumed_requests"] == 6


@pytest.mark.parametrize("full_native", (128,), indirect=True)
def test_growth_cannot_replace_expected_prefix_with_already_corrupted_bytes(pressure):
    req = pressure.request("corrupt")
    pressure.pool.k_buffer[0][pressure._indices(req)[0]] = -999
    with pytest.raises(ValueError, match="lost KV before"):
        pressure.grow(req, 1)
    assert req.seqlen == 28


def report():
    return {
        "status": "agentix_native_pressure_components_qualified",
        "native_engine": {"actual_tree": "tree", "sha256": "patch"},
        **dict.fromkeys(qualifier.PROPERTIES, True),
        "device_tokens": 128, "host_tokens": 257, "layers": 2,
        "backing_growth": {
            "initial": {"normal_live_budget_tokens": 193, "draining": False},
            "after_completion": {"draining": False},
            "selected_growth_tokens_after_drain": 34,
        },
        "host_refusal": {
            "refused": {"status": "backup_declined", "host_capacity_refused": True},
            "actual_host_blocker_slots_released": 122,
        },
        "swap": {
            "pending_requests": 0, "paused_host_pins": 0,
            "counts": {"backup_declined": 1, "paused_requests": 6, "resumed_requests": 6},
        },
        "remaining_paused_requests_restored": ["a", "b", "c", "d"],
        "whole_llm_pressure_progress_qualified": False,
        "model_numerical_equivalence_verified": False,
        "complete_baseline_qualified": False, "performance_accepted": False,
        "source_sha256": {name: sha256_file(qualifier.ROOT / name) for name in qualifier.SOURCES},
    }


def test_pressure_report_requires_current_sources_and_retains_component_scope():
    qualifier.validate_report(report(), engine_tree="tree", patch_sha256="patch")


@pytest.mark.parametrize("field", (*qualifier.PROPERTIES, "whole_llm_pressure_progress_qualified"))
def test_missing_evidence_and_whole_model_overclaim_are_rejected(field):
    evidence = report()
    evidence[field] = field == "whole_llm_pressure_progress_qualified"
    with pytest.raises(ValueError, match="missing, stale or overclaimed"):
        qualifier.validate_report(evidence, engine_tree="tree", patch_sha256="patch")


def test_pressure_source_change_invalidates_gpu_evidence():
    evidence = report()
    evidence["source_sha256"]["beliefkv/runtime/agentix_capacity.py"] = "old"
    with pytest.raises(ValueError, match="missing, stale or overclaimed"):
        qualifier.validate_report(evidence, engine_tree="tree", patch_sha256="patch")


@pytest.mark.parametrize("field,value", (
    ("host_tokens", 2000000), ("device_tokens", 805198), ("layers", 1),
    ("remaining_paused_requests_restored", []),
    ("swap", {"pending_requests": 1, "paused_host_pins": 1}),
    ("host_refusal", {
        "refused": {"status": "backup_declined", "host_capacity_refused": True},
        "actual_host_blocker_slots_released": 0,
    }),
))
def test_roomy_pool_or_incomplete_pressure_lifecycle_cannot_qualify(field, value):
    evidence = report()
    evidence[field] = value
    with pytest.raises(ValueError, match="missing, stale or overclaimed"):
        qualifier.validate_report(evidence, engine_tree="tree", patch_sha256="patch")


def test_queued_pressure_check_cannot_allocate_gpu_while_predecessor_is_live(tmp_path, monkeypatch):
    class StopObservation(Exception):
        pass

    root = tmp_path / "run"
    root.mkdir()
    registration = {"run_root": str(root), "port": 18635, "after_run_root": "previous"}
    path = root / "registration.json"
    path.write_text(json.dumps(registration))
    monkeypatch.setattr(qualifier, "check_registration", lambda _row: None)
    monkeypatch.setattr(qualifier, "predecessor_finished", lambda _root: False)
    monkeypatch.setattr(qualifier.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(qualifier.controls.fresh, "check_hold", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(qualifier.controls, "owned_processes", lambda rows: rows)
    monkeypatch.setattr(qualifier.controls, "cleanup", lambda *_args: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GPU allocation must wait for actual predecessor exit and cleanup")

    def end_observation(_duration):
        raise StopObservation()

    monkeypatch.setattr(qualifier.controls.qualified, "resources", forbidden)
    monkeypatch.setattr(qualifier.subprocess, "Popen", forbidden)
    monkeypatch.setattr(qualifier.time, "sleep", end_observation)
    qualifier.watch(path)
    result = json.loads((root / "summary.json").read_bytes())
    assert result["error_type"] == "StopObservation"
    assert result["performance_accepted"] is False
