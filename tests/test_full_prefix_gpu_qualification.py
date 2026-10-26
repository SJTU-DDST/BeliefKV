from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
import json

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import qualify_full_prefix_migration as qualifier
from tests.test_full_only_native_lifecycle import full_native


@pytest.fixture
def fixture(monkeypatch, tmp_path, full_native):
    """Real native CPU requests/tree/allocator; copies and events are stand-ins."""
    import torch
    from sglang.srt.mem_cache.memory_pool_host import LogicalHostPool
    from sglang.srt.server_args import ServerArgs, set_global_server_args_for_scheduler

    set_global_server_args_for_scheduler(ServerArgs(model_path="dummy"))
    cache, allocator, _, _, _ = full_native
    cache.load_back_threshold = 1
    with qualifier.full_environment():
        runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "p.sock"))
    runtime.attach_native_cache(cache)
    runtime._agent_admission.policy.buffer_tokens = 1
    runtime._native_plain_output_template = True
    result = qualifier.NativeFullFixture.__new__(qualifier.NativeFullFixture)
    result.torch, result.cache, result.allocator = torch, cache, allocator
    result.pool, result.rows = allocator._kvcache, cache.req_to_token_pool
    result.runtime, result.actor = runtime, runtime._full_prefix_migration
    result.sequence, result.live, result.registered = 0, {}, {}
    result.commits, result.issued_batches, result.poisoned_tokens = [], [], 0
    result.restore_consumers = 0
    result.host_blocker = result.device_blocker = None
    result.sync = lambda: None
    slots = LogicalHostPool(size=257, page_size=1)
    host = cache.host_pool_group.anchor_entry.host_pool
    host.size, host.alloc, host.free = 257, slots.alloc, slots.free
    host.available_size = slots.available_size
    cache.host_pool_group.available_size = slots.available_size
    cache.host_pool_group.free = lambda indices, **_kwargs: slots.free(indices)
    storage = [
        (torch.zeros((257, *key.shape[1:]), dtype=key.dtype),
         torch.zeros((257, *value.shape[1:]), dtype=value.dtype))
        for key, value in zip(result.pool.k_buffer, result.pool.v_buffer, strict=True)
    ]

    def write(indices, **kwargs):
        host_indices = slots.alloc(len(indices))
        if host_indices is None:
            return None
        for (key, value), (host_key, host_value) in zip(
            zip(result.pool.k_buffer, result.pool.v_buffer, strict=True), storage, strict=True,
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
            zip(result.pool.k_buffer, result.pool.v_buffer, strict=True), storage, strict=True,
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
    write_check, load_check = cache.writing_check, cache.loading_check

    def writing_check(*args, **kwargs):
        kwargs.setdefault("finish_count", 1)
        return write_check(*args, **kwargs)

    def loading_check(*args, **kwargs):
        kwargs.setdefault("finish_count", 1)
        return load_check(*args, **kwargs)

    cache.writing_check, cache.loading_check = writing_check, loading_check
    cache.on_hicache_transfer_commit = result.acknowledged

    @contextmanager
    def busy():
        counter = cache.cache_controller.layer_done_counter
        event = counter.events[(counter.producer_index + 2) % counter.num_counters].finish_event
        previous = event.query
        event.query = lambda: False
        try:
            yield lambda: setattr(event, "query", lambda: True)
        finally:
            event.query = previous

    result.busy_producer = busy
    result.send(qualifier.Kind.WORKFLOW_START)
    yield result
    for req in tuple(result.registered.values()):
        result.abort(req)
    result.settle_writes()
    result.settle_loads()
    runtime.close()


@pytest.mark.parametrize("full_native", (128,), indirect=True)
@pytest.mark.parametrize("name", qualifier.SCENARIOS)
def test_actual_qualifier_scenarios_use_native_requests_and_check_their_own_bytes(fixture, name):
    result = qualifier.scenario(fixture, name)
    assert result["status"] == "complete"
    assert result["final_device_free_tokens"] == 128
    assert result["native_ack_tokens"] == dict(zip(("d2h", "h2d"), qualifier.EXPECTED_ACKS[name]))
    if qualifier.EXPECTED_ACKS[name][1]:
        assert result["poisoned_device_tokens"] > 0
        assert fixture.runtime._agent_admission.counts["native_restore_consumers"] == 1
    else:
        assert fixture.runtime._agent_admission.counts["native_restore_consumers"] == 0


@pytest.mark.parametrize("full_native", (128,), indirect=True)
def test_corrupted_restored_bytes_fail_the_same_qualifier_comparison(fixture):
    expected = fixture.offload_parent()
    child = fixture.request("child", range(30, 34))
    fixture.admit(child)
    fixture.finish(child)
    fixture.send(qualifier.Kind.RETURN, invocation="child", attributes={"context_retired": False})
    fixture.dispatch()
    fixture.settle_loads()
    indices = fixture.match(range(8)).device_indices
    fixture.pool.k_buffer[0][indices[0]] = -999
    with pytest.raises(ValueError, match="original per-layer bytes"):
        fixture.require_bytes(range(8), expected)


def report():
    return {
        "status": "full_native_prefix_components_qualified",
        "native_engine": {"actual_tree": "native"}, "native_patch_sha256": "patch",
        "device_tokens": 128, "host_tokens": 257, "layers": 2,
        "fixture_only_agent_buffer_tokens": 1,
        **dict.fromkeys(qualifier.PROPERTIES, True),
        "scenarios": {
            name: {
                "status": "complete", "final_device_free_tokens": 128,
                "native_ack_tokens": dict(zip(("d2h", "h2d"), qualifier.EXPECTED_ACKS[name])),
                "poisoned_device_tokens": 128 if qualifier.EXPECTED_ACKS[name][1] else 0,
                "d2h_batches": [4, 4, 4] if name == "incremental" else [],
                "native_restore_consumers": int(qualifier.EXPECTED_ACKS[name][1] > 0),
            } for name in qualifier.SCENARIOS
        },
        "whole_model_qualified": False, "model_numerical_equivalence_verified": False,
        "performance_accepted": False,
        "source_sha256": {name: sha256_file(qualifier.ROOT / name) for name in qualifier.SOURCES},
    }


def test_complete_component_evidence_preserves_unqualified_model_and_performance_scope():
    qualifier.validate_report(report(), engine_tree="native", patch_sha256="patch")


@pytest.mark.parametrize("field", qualifier.PROPERTIES)
def test_missing_full_runtime_component_evidence_is_rejected(field):
    evidence = report()
    evidence[field] = False
    with pytest.raises(ValueError, match="incomplete, stale or overclaimed"):
        qualifier.validate_report(evidence, engine_tree="native", patch_sha256="patch")


@pytest.mark.parametrize("change", (
    "engine", "patch", "source", "model", "numerical", "performance", "layer",
    "buffer", "missing_case", "failed_case", "leaked_slots", "missing_ack", "unpoisoned", "unbounded",
    "consumer", "host",
))
def test_incorrect_bytes_capacity_or_scope_cannot_be_qualified_by_flags(change):
    evidence = deepcopy(report())
    if change == "engine":
        evidence["native_engine"]["actual_tree"] = "other"
    elif change == "patch":
        evidence["native_patch_sha256"] = "other"
    elif change == "source":
        evidence["source_sha256"][qualifier.SOURCES[1]] = "old"
    elif change in ("model", "numerical", "performance"):
        evidence[{
            "model": "whole_model_qualified", "numerical": "model_numerical_equivalence_verified",
            "performance": "performance_accepted",
        }[change]] = True
    elif change == "layer":
        evidence["layers"] = 1
    elif change == "host":
        evidence["host_tokens"] = 2000000
    elif change == "buffer":
        evidence["fixture_only_agent_buffer_tokens"] = 100
    elif change == "missing_case":
        evidence["scenarios"].pop("shared_ready")
    elif change == "failed_case":
        evidence["scenarios"]["confirmed_join"]["status"] = "failed"
    elif change == "leaked_slots":
        evidence["scenarios"]["host_pressure"]["final_device_free_tokens"] = 127
    elif change == "missing_ack":
        evidence["scenarios"]["confirmed_join"]["native_ack_tokens"]["h2d"] = 0
    elif change == "unpoisoned":
        evidence["scenarios"]["controlled_eos"]["poisoned_device_tokens"] = 0
    elif change == "consumer":
        evidence["scenarios"]["confirmed_join"]["native_restore_consumers"] = 0
    else:
        evidence["scenarios"]["incremental"]["d2h_batches"] = [12]
    with pytest.raises(ValueError, match="incomplete, stale or overclaimed"):
        qualifier.validate_report(evidence, engine_tree="native", patch_sha256="patch")


def test_queued_full_check_does_not_probe_resources_or_launch_while_predecessor_lives(
    tmp_path, monkeypatch,
):
    class StopObservation(Exception):
        pass

    root = tmp_path / "run"
    root.mkdir()
    path = root / "registration.json"
    path.write_text(json.dumps({
        "run_root": str(root), "port": 18640, "after_run_root": "previous",
    }))
    monkeypatch.setattr(qualifier, "check_registration", lambda _row: None)
    monkeypatch.setattr(qualifier, "predecessor_finished", lambda _root: False)
    monkeypatch.setattr(qualifier.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(qualifier.controls.fresh, "check_hold", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(qualifier.controls, "owned_processes", lambda rows: rows)
    monkeypatch.setattr(qualifier.controls, "cleanup", lambda *_args: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Full GPU check must wait for actual predecessor exit and cleanup")

    def stop(_seconds):
        raise StopObservation()

    monkeypatch.setattr(qualifier.controls.qualified, "resources", forbidden)
    monkeypatch.setattr(qualifier.subprocess, "Popen", forbidden)
    monkeypatch.setattr(qualifier.time, "sleep", stop)
    qualifier.watch(path)
    result = json.loads((root / "summary.json").read_bytes())
    assert result["error_type"] == "StopObservation"
    assert result["performance_accepted"] is False


@pytest.mark.parametrize("change", (None, "source", "registration", "supervisor"))
def test_full_qualification_pins_sources_and_exact_predecessor_identity(tmp_path, change):
    prior = tmp_path / "predecessor"
    prior.mkdir()
    path = prior / "registration.json"
    path.write_text(json.dumps({"fixture": True}))
    identity = {"pid": 123, "start_ticks": 456}
    (prior / "supervisor.json").write_text(json.dumps(identity))
    registration = {
        "after_run_root": str(prior), "predecessor_supervisor": identity,
        "predecessor_registration_sha256": sha256_file(path),
        "source_sha256": {name: sha256_file(qualifier.ROOT / name) for name in qualifier.SOURCES},
    }
    if change == "source":
        registration["source_sha256"][qualifier.SOURCES[1]] = "old"
    elif change == "registration":
        path.write_text(json.dumps({"fixture": "changed"}))
    elif change == "supervisor":
        (prior / "supervisor.json").write_text(json.dumps({"pid": 123, "start_ticks": 999}))
    if change is None:
        qualifier.check_registration(registration)
    else:
        with pytest.raises(ValueError):
            qualifier.check_registration(registration)
