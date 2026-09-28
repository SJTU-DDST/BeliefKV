from __future__ import annotations

import json
import time
from array import array
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import pytest

import beliefkv.runtime.v0520_native_telemetry as telemetry_module
from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry


class _Mode:
    def __init__(self, phase: str):
        self.phase = phase

    def is_extend(self) -> bool:
        return self.phase == "prefill"

    def is_decode(self) -> bool:
        return self.phase == "decode"


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_admission_observation_has_distinct_provenance(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        NativeReactiveTelemetry(tmp_path / "invalid", collection_mode="unknown")
    audit = NativeReactiveTelemetry(
        tmp_path / "observation", collection_mode="admission_observation"
    )
    audit.close()
    ready = json.loads(
        (tmp_path / "observation/native_telemetry_ready.json").read_text()
    )
    assert ready["collection_mode"] == "admission_observation"


def test_confirmed_join_canary_has_distinct_provenance(tmp_path: Path) -> None:
    audit = NativeReactiveTelemetry(
        tmp_path / "canary", collection_mode="confirmed_join_canary"
    )
    audit.close()
    ready = json.loads(
        (tmp_path / "canary/native_telemetry_ready.json").read_text()
    )
    status = json.loads(
        (tmp_path / "canary/native_telemetry_status.json").read_text()
    )
    assert ready["collection_mode"] == "confirmed_join_canary"
    assert status["collection_mode"] == "confirmed_join_canary"
    assert status["writer_error"] is None


def test_host_hit_path_records_identity_without_claiming_component_reuse(
    tmp_path: Path,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "service")
    root = SimpleNamespace(id=0, creation_time=1, parent=None)
    ancestor = SimpleNamespace(id=52, creation_time=286, parent=root)
    leaf = SimpleNamespace(id=4721, creation_time=300, parent=ancestor)
    audit._cache = SimpleNamespace(
        tree_core=SimpleNamespace(
            node_by_id={node.id: node for node in (root, ancestor, leaf)}.__getitem__
        )
    )
    req = SimpleNamespace(
        rid="request", beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": "parent",
            "context_id": "ctx", "context_epoch": 9,
        },
        last_node=leaf.id, last_host_node=ancestor.id,
        best_match_node=leaf.id, origin_input_ids=[1, 2], output_ids=[],
        cached_tokens_device=1, cached_tokens_host=1,
        mamba_host_hit_length=1, extend_input_len=0,
        sampling_params=SimpleNamespace(max_new_tokens=2),
        finished=lambda: False,
    )
    audit.on_launch(SimpleNamespace(
        forward_mode=_Mode("prefill"), launch_ts=time.monotonic(),
        forward_iter=1, reqs=[req],
    ))
    audit.close()
    [event] = _read(tmp_path / "service/runtime_events.sglang.jsonl")
    path = event["attributes"]["native_host_hit_match_path"]
    assert path["last_device_path"] == [[0, 1], [52, 286], [4721, 300]]
    assert path["last_host_path"] == [[0, 1], [52, 286]]
    assert path["evidence"].endswith("not_proof_of_component_reuse")


def test_physical_action_evidence_flushes_without_idle_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoIdleFlushQueue(Queue):
        def get(self, block: bool = True, timeout: float | None = None):
            return super().get(block=block, timeout=None)

    monkeypatch.setattr(telemetry_module, "Queue", NoIdleFlushQueue)
    audit = NativeReactiveTelemetry(tmp_path / "service")
    try:
        audit._emit("action_ack", {"event": "ack"})
        audit._emit("action_use", {"event": "use"})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if all(
                (path := tmp_path / f"service/{name}").exists()
                and path.stat().st_size > 0
                for name in ("physical_action_ack.jsonl", "physical_action_use.jsonl")
            ):
                break
            time.sleep(0.01)
        assert _read(tmp_path / "service/physical_action_ack.jsonl") == [
            {"event": "ack"}
        ]
        assert _read(tmp_path / "service/physical_action_use.jsonl") == [
            {"event": "use"}
        ]
    finally:
        audit.close()


def test_verified_prefetch_records_node_match_at_first_gpu_service(
    tmp_path: Path,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "service")
    root = SimpleNamespace(id=0, creation_time=0, parent=None)
    loaded = SimpleNamespace(
        id=11, creation_time=7, parent=root, key=array("q", range(10)),
        component_data=(SimpleNamespace(value=object()),),
    )
    leaf = SimpleNamespace(
        id=12, creation_time=8, parent=loaded, key=array("q", range(5)),
    )
    nodes = {node.id: node for node in (root, loaded, leaf)}
    audit._cache = SimpleNamespace(
        tree_core=SimpleNamespace(node_by_id=nodes.__getitem__),
    )
    action = SimpleNamespace(
        command_id="prefetch-1", action="PREFETCH_GPU",
        context_id="ctx", context_epoch=2, node_ids=(11,),
        pool_bytes=(("kv", 2048), ("mamba", 64)), num_bytes=2112,
    )
    audit.on_verified_action_ack(action)
    request = SimpleNamespace(
        rid="parent-first-service",
        beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": "parent",
            "context_id": "ctx", "context_epoch": 3,
        },
        last_node=12, origin_input_ids=list(range(20)), output_ids=[],
        prefix_indices=list(range(12)),
        cached_tokens_device=12, cached_tokens_host=0,
        mamba_host_hit_length=0, extend_input_len=8,
        sampling_params=SimpleNamespace(max_new_tokens=10),
        finished=lambda: False,
    )
    audit.on_launch(SimpleNamespace(
        forward_mode=_Mode("prefill"), launch_ts=time.monotonic(),
        forward_iter=1, reqs=[request],
    ))
    audit.close()
    used = _read(tmp_path / "service/physical_action_use.jsonl")
    assert len(used) == 1
    assert used[0]["matched_node_ids"] == [11]
    assert used[0]["reused_full_node_ids"] == [11]
    assert used[0]["context_epoch"] == 2
    assert used[0]["service_context_epoch"] == 3
    assert used[0]["device_prefix_indices_len"] == 12
    assert used[0]["full_node_reused"] is True
    assert used[0]["mamba_reuse"] == "unverified"
    assert used[0]["request_id"] == request.rid


def test_verified_prefetch_unmatched_or_unserved_is_not_credited(
    tmp_path: Path,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "service")
    root = SimpleNamespace(id=0, creation_time=0, parent=None)
    loaded = SimpleNamespace(
        id=11, creation_time=7, parent=root, key=array("q", range(10)),
        component_data=(SimpleNamespace(value=object()),),
    )
    nodes = {node.id: node for node in (root, loaded)}
    audit._cache = SimpleNamespace(
        tree_core=SimpleNamespace(node_by_id=nodes.__getitem__),
    )
    for command_id, context in (("prefetch-miss", "ctx"), ("prefetch-censored", "other")):
        audit.on_verified_action_ack(SimpleNamespace(
            command_id=command_id, action="PREFETCH_GPU",
            context_id=context, context_epoch=2, node_ids=(11,),
            pool_bytes=(("kv", 2048),), num_bytes=2048,
        ))
    request = SimpleNamespace(
        rid="parent-miss",
        beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": "parent",
            "context_id": "ctx", "context_epoch": 2,
        },
        last_node=0, origin_input_ids=list(range(20)), output_ids=[],
        prefix_indices=[],
        cached_tokens_device=0, cached_tokens_host=0,
        mamba_host_hit_length=0, extend_input_len=20,
        sampling_params=SimpleNamespace(max_new_tokens=10),
        finished=lambda: False,
    )
    audit.on_launch(SimpleNamespace(
        forward_mode=_Mode("prefill"), launch_ts=time.monotonic(),
        forward_iter=1, reqs=[request],
    ))
    audit.close()
    outcomes = _read(tmp_path / "service/physical_action_use.jsonl")
    assert len(outcomes) == 2
    assert outcomes[0]["command_id"] == "prefetch-miss"
    assert outcomes[0]["full_node_reused"] is False
    assert outcomes[1]["command_id"] == "prefetch-censored"
    assert outcomes[1]["reason"] == "no_subsequent_service_before_shutdown"


@pytest.mark.parametrize("cached_device,device_prefix,replace_value,expected", [
    (5, 10, False, False),
    (10, 5, False, False),
    (10, 10, True, False),
    (10, 10, False, True),
])
def test_prefetch_ancestry_requires_same_full_value_and_entire_prefix(
    tmp_path: Path, cached_device: int, device_prefix: int,
    replace_value: bool, expected: bool,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "service")
    root = SimpleNamespace(id=0, creation_time=0, parent=None)
    loaded = SimpleNamespace(
        id=11, creation_time=7, parent=root, key=array("q", range(10)),
        component_data=(SimpleNamespace(value=object()),),
    )
    nodes = {0: root, 11: loaded}
    audit._cache = SimpleNamespace(
        tree_core=SimpleNamespace(node_by_id=nodes.__getitem__),
    )
    audit.on_verified_action_ack(SimpleNamespace(
        command_id="prefetch-1", action="PREFETCH_GPU",
        context_id="ctx", context_epoch=2, node_ids=(11,),
        pool_bytes=(("kv", 2048),), num_bytes=2048,
    ))
    if replace_value:
        loaded.component_data[0].value = object()
    request = SimpleNamespace(
        rid="parent-first-service",
        beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": "parent",
            "context_id": "ctx", "context_epoch": 2,
        },
        last_node=11, origin_input_ids=list(range(20)), output_ids=[],
        prefix_indices=list(range(device_prefix)),
        cached_tokens_device=cached_device, cached_tokens_host=0,
        mamba_host_hit_length=0, extend_input_len=20 - cached_device,
        sampling_params=SimpleNamespace(max_new_tokens=10),
        finished=lambda: False,
    )
    audit.on_launch(SimpleNamespace(
        forward_mode=_Mode("prefill"), launch_ts=time.monotonic(),
        forward_iter=1, reqs=[request],
    ))
    audit.close()
    [use] = _read(tmp_path / "service/physical_action_use.jsonl")
    assert use["matched_node_ids"] == [11]
    assert use["full_node_reused"] is expected
    assert use["reused_full_node_ids"] == ([11] if expected else [])


def test_prefetch_after_multiple_epoch_changes_is_censored(tmp_path: Path) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "service")
    root = SimpleNamespace(id=0, creation_time=0, parent=None)
    loaded = SimpleNamespace(
        id=11, creation_time=7, parent=root, key=array("q", range(10)),
        component_data=(SimpleNamespace(value=object()),),
    )
    audit._cache = SimpleNamespace(
        tree_core=SimpleNamespace(node_by_id={0: root, 11: loaded}.__getitem__),
    )
    audit.on_verified_action_ack(SimpleNamespace(
        command_id="prefetch-stale", action="PREFETCH_GPU",
        context_id="ctx", context_epoch=2, node_ids=(11,),
        pool_bytes=(("kv", 2048),), num_bytes=2048,
    ))
    audit._record_prefetch_first_service(
        SimpleNamespace(last_node=11, cached_tokens_device=10,
                        prefix_indices=list(range(10)), cached_tokens_host=0),
        {"context_id": "ctx", "context_epoch": 4},
    )
    audit.close()
    [use] = _read(tmp_path / "service/physical_action_use.jsonl")
    assert use["event"] == "beliefkv_prefetch_first_service_censored"
    assert use["reason"] == "epoch_advanced_without_first_service"


def test_capacity_census_is_scheduler_local_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from beliefkv.runtime import sglang_v0520_observer

    monkeypatch.setattr(
        sglang_v0520_observer, "observe_static_full_mamba",
        lambda cache: {
            "pool_layout": "static_separate_full_mamba",
            "device_total_bytes": 800,
            "host_full_tokens": 10,
            "host_mamba_slots": 4,
        },
    )
    monkeypatch.setattr(
        sglang_v0520_observer, "observe_static_full_mamba_host_usage",
        lambda cache: SimpleNamespace(observable=True, reason=None),
    )
    cache = SimpleNamespace(
        host_pool_group=SimpleNamespace(
            entry_map={
                "kv": SimpleNamespace(host_pool=SimpleNamespace(size_per_token=16)),
                "mamba": SimpleNamespace(host_pool=SimpleNamespace(size_per_token=32)),
            }
        ),
        tree_core=SimpleNamespace(
            set_beliefkv_host_eviction_observer=lambda observer: None
        ),
    )
    audit = NativeReactiveTelemetry(tmp_path / "server")
    audit.record_capacity(cache)
    path = tmp_path / "server/native_capacity_census.json"
    census = json.loads(path.read_text())
    assert census["capacity"]["device_total_bytes"] == 800
    with pytest.raises(FileExistsError):
        audit.record_capacity(object())
    audit.close()

    monkeypatch.setattr(
        sglang_v0520_observer, "observe_static_full_mamba",
        lambda cache: (_ for _ in ()).throw(ValueError("unknown pool")),
    )
    second = NativeReactiveTelemetry(tmp_path / "unavailable")
    with pytest.raises(RuntimeError, match="unknown pool"):
        second.record_capacity(object())
    second.close()
    assert not (tmp_path / "unavailable/native_capacity_census.json").exists()

    monkeypatch.setattr(
        sglang_v0520_observer, "observe_static_full_mamba",
        lambda _cache: {
            "pool_layout": "static_separate_full_mamba",
            "device_total_bytes": 800,
            "host_full_tokens": 10,
            "host_mamba_slots": 4,
        },
    )
    unsupported = NativeReactiveTelemetry(tmp_path / "unsupported")
    unsupported_cache = SimpleNamespace(
        host_pool_group=cache.host_pool_group,
        tree_core=SimpleNamespace(),
    )
    with pytest.raises(RuntimeError, match="block-level Host eviction"):
        unsupported.record_capacity(unsupported_cache)
    unsupported.close()
    assert not (
        tmp_path / "unsupported/native_capacity_census.json"
    ).exists()
    assert not hasattr(unsupported_cache, "on_hicache_host_eviction")


def test_native_transfer_stream_and_submit_ack_are_distinct_evidence(
    tmp_path: Path,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "server")
    audit.on_native_transfer_commit(SimpleNamespace(
        direction="d2h",
        status="completed",
        node_ids=(1,),
        num_tokens_by_pool=(("kv", 8), ("mamba", 1)),
        child_commits=(SimpleNamespace(
            command_id="beliefkv-shadow-1", anchor_node_id=1,
            published_node_ids=(1,), num_tokens_by_pool=(("kv", 8),),
            num_bytes=8192,
        ),),
        actual_bytes=8192,
        submit_ts_ms=1000.0,
        ack_ts_ms=1010.0,
        submit_to_ack_ms=10.0,
        transfer_stream_elapsed_ms=2.5,
        unacked_bytes_at_submit=4096,
    ))
    audit.on_verified_action_ack(SimpleNamespace(
        command_id="beliefkv-shadow-1", action="PREPARE_HOST",
        context_id="context-1", context_epoch=2, node_ids=(1,),
        pool_bytes=(("kv", 8192),), num_bytes=8192,
    ))
    audit.close()

    transfer = _read(tmp_path / "server/transfer_telemetry.jsonl")[0]
    assert transfer["actual_bytes"] == 8192
    assert transfer["submit_ts_ms"] == 1000.0
    assert transfer["complete_ts_ms"] == 1010.0
    assert transfer["submit_to_ack_ms"] == 10.0
    assert transfer["transfer_stream_elapsed_ms"] == 2.5
    assert transfer["native_unacked_bytes_at_submit"] == 4096
    assert transfer["start_ts_ms"] is None
    assert transfer["start_timestamp_semantics"] == "device_event_no_wall_anchor"
    assert transfer["tagged_child_commits"] == [{
        "command_id": "beliefkv-shadow-1",
        "anchor_node_id": 1,
        "published_node_ids": [1],
        "num_tokens_by_pool": {"kv": 8},
        "num_bytes": 8192,
    }]
    verified = _read(tmp_path / "server/physical_action_ack.jsonl")
    assert len(verified) == 1
    assert verified[0]["command_id"] == "beliefkv-shadow-1"
    assert verified[0]["pool_bytes"] == {"kv": 8192}
    assert verified[0]["evidence"] == "native_child_commit_reconciled_with_live_context"


def test_native_request_service_and_ack_are_evidence_not_invented_dma(
    tmp_path: Path,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "server")
    request = SimpleNamespace(
        rid="req-1",
        beliefkv_metadata={
            "root_workflow_id": "w-1",
            "invocation_id": "i-1",
            "context_id": "c-1",
            "context_epoch": 0,
        },
        origin_input_ids=list(range(24)),
        output_ids=[],
        cached_tokens_device=7,
        cached_tokens_host=3,
        mamba_host_hit_length=2,
        extend_input_len=14,
        sampling_params=SimpleNamespace(max_new_tokens=20),
        finished=lambda: False,
    )
    audit.on_enqueue(request)
    batch = SimpleNamespace(
        forward_mode=_Mode("prefill"),
        launch_ts=time.monotonic(),
        forward_iter=1,
        reqs=[request],
    )
    audit.on_launch(batch)
    audit.on_completed(batch)
    batch.forward_mode = _Mode("decode")
    batch.forward_iter = 2
    batch.launch_ts = time.monotonic()
    audit.on_launch(batch)
    request.output_ids.extend((11, 12))
    request.finished = lambda: True
    audit.on_completed(batch)
    batch.forward_iter = 3
    batch.launch_ts = time.monotonic()
    audit.on_launch(batch)
    audit.on_completed(batch)
    audit.on_native_transfer_commit(SimpleNamespace(
        direction="h2d", status="completed", node_ids=(10,),
        num_tokens_by_pool=(("full", 64), ("mamba", 2)),
    ))
    deadline = time.monotonic() + 2
    status_path = tmp_path / "server/native_telemetry_status.json"
    while not status_path.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert status_path.is_file(), "status must persist while the server is alive"
    audit.close()
    events = _read(tmp_path / "server/runtime_events.sglang.jsonl")
    assert [event["kind"] for event in events] == ["llm_submit", "llm_result"]
    assert events[0]["attributes"]["cache_hit_tokens"] == 10
    assert events[0]["attributes"]["uncached_prompt_tokens"] == 14
    assert events[0]["attributes"]["cached_tokens_host"] == 3
    assert events[0]["attributes"]["mamba_host_hit_slots"] == 2
    assert events[1]["attributes"]["output_tokens"] == 2
    assert events[0]["context_epoch"] == 0
    service = _read(tmp_path / "server/runtime_audit.jsonl")
    assert [event["phase"] for event in service] == ["prefill", "decode"]
    assert service[0]["request_samples"][0]["token_delta"] == 14
    assert service[1]["request_samples"][0]["token_delta"] == 2
    assert all(row["timing_semantics_version"] == "gpu_service_interval_v1" for row in service)
    transfer = _read(tmp_path / "server/transfer_telemetry.jsonl")[0]
    assert transfer["num_tokens_by_pool"] == {"full": 64, "mamba": 2}
    assert transfer["actual_bytes"] is None
    assert transfer["start_ts_ms"] is None
    assert transfer["training_eligible_service_curve"] is False
    status = json.loads((tmp_path / "server/native_telemetry_status.json").read_text())
    assert status["pending_request_count"] == 0
    assert status["pending_batch_count"] == 0
    assert status["writer_error"] is None
    assert status["failed_records"] == 0
    assert status["record_counts"] == {
        "audit": 2, "events": 2, "host_pool": 1, "transfer": 1
    }
    assert status["request_cache_evidence"]["all"] == {
        "cached_tokens_device": 7,
        "cached_tokens_host": 3,
        "mamba_host_hit_slots": 2,
        "prompt_tokens": 24,
        "request_count": 1,
        "requests_with_full_host_hit": 1,
        "requests_with_mamba_host_hit": 1,
        "uncached_prompt_tokens": 14,
    }


def test_unattributed_startup_prefill_does_not_pollute_native_telemetry(
    tmp_path: Path,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "server")
    audit._cache = object()
    warmup_request = SimpleNamespace(rid="startup-warmup", beliefkv_metadata=None)
    batch = SimpleNamespace(
        forward_mode=_Mode("prefill"),
        launch_ts=time.monotonic(),
        forward_iter=1,
        reqs=[warmup_request],
    )

    audit.on_launch(batch)
    audit.close()

    assert _read(tmp_path / "server/host_pool_telemetry.jsonl") == []
    status = json.loads(
        (tmp_path / "server/native_telemetry_status.json").read_text()
    )
    assert status["record_counts"] == {}


def test_host_pool_usage_and_evictions_are_persisted_by_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from beliefkv.runtime import sglang_v0520_observer

    full_pool = SimpleNamespace(size_per_token=16, available_size=lambda: 60)
    mamba_pool = SimpleNamespace(size_per_token=100, available_size=lambda: 8)
    cache = SimpleNamespace(
        host_pool_group=SimpleNamespace(
            entry_map={
                "kv": SimpleNamespace(host_pool=full_pool),
                "mamba": SimpleNamespace(host_pool=mamba_pool),
            }
        )
    )
    monkeypatch.setattr(
        sglang_v0520_observer,
        "observe_static_full_mamba_host_usage",
        lambda _: SimpleNamespace(
            observable=True,
            host_full_used_tokens=40,
            host_full_used_bytes=640,
            host_mamba_used_slots=2,
            host_mamba_used_bytes=200,
            reason=None,
        ),
    )
    audit = NativeReactiveTelemetry(tmp_path / "server")
    audit._host_pool_geometry = {
        "full": {"capacity_units": 100, "bytes_per_unit": 16},
        "mamba": {"capacity_units": 10, "bytes_per_unit": 100},
    }
    audit._host_pool_high_water = {
        pool: {"used_units": 0, "used_bytes": 0}
        for pool in ("full", "mamba")
    }
    audit.record_host_pool_usage(cache)

    from sglang.srt.mem_cache.unified_cache.component_type import ComponentType

    audit.on_native_host_eviction(
        ComponentType.FULL,
        {ComponentType.FULL: 12, ComponentType.MAMBA: 3},
    )
    audit.on_native_transfer_commit(SimpleNamespace(
        direction="h2d", status="completed", node_ids=(1,),
        num_tokens_by_pool=(("kv", 24), ("mamba", 2)),
    ))
    audit.close()

    rows = _read(tmp_path / "server/host_pool_telemetry.jsonl")
    usage = next(row for row in rows if row["event"] == "host_pool_usage")
    assert usage["pools"]["full"]["used_bytes"] == 640
    assert usage["pools"]["mamba"]["used_fraction"] == 0.2
    eviction = next(row for row in rows if row["event"] == "host_pool_eviction")
    assert eviction["pools"] == {
        "full": {"evicted_units": 12, "evicted_bytes": 192},
        "mamba": {"evicted_units": 3, "evicted_bytes": 300},
    }
    status = json.loads(
        (tmp_path / "server/native_telemetry_status.json").read_text()
    )
    assert status["host_pool_evidence"]["full"]["evicted_bytes"] == 192
    assert status["host_pool_evidence"]["mamba"]["h2d_ack_units"] == 2


def test_post_eviction_cache_hits_and_recompute_are_split_by_pool(
    tmp_path: Path,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "server")
    audit._host_evictions.update({"full": 1, "mamba": 1})
    request = SimpleNamespace(
        rid="req-after-eviction",
        beliefkv_metadata={
            "root_workflow_id": "w-1",
            "invocation_id": "i-2",
            "context_id": "c-2",
            "context_epoch": 0,
        },
        origin_input_ids=list(range(40)),
        output_ids=[],
        cached_tokens_device=20,
        cached_tokens_host=10,
        mamba_host_hit_length=2,
        extend_input_len=10,
        sampling_params=SimpleNamespace(max_new_tokens=16),
        finished=lambda: False,
    )
    audit.on_enqueue(request)
    batch = SimpleNamespace(
        forward_mode=_Mode("prefill"),
        launch_ts=time.monotonic(),
        forward_iter=1,
        reqs=[request],
    )
    audit.on_launch(batch)
    audit.on_completed(batch)
    audit.close()

    status = json.loads(
        (tmp_path / "server/native_telemetry_status.json").read_text()
    )
    evidence = status["request_cache_evidence"]
    assert evidence["after_first_host_eviction"]["uncached_prompt_tokens"] == 10
    assert (
        evidence["after_first_full_host_eviction"]["requests_with_full_host_hit"]
        == 1
    )
    assert (
        evidence["after_first_mamba_host_eviction"]["mamba_host_hit_slots"] == 2
    )
    assert "node-level FULL reaccess attribution" in (
        status["request_cache_evidence_semantics"]["after_eviction"]
    )


def test_host_block_eviction_is_attributed_to_later_full_and_mamba_access(
    tmp_path: Path,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "server")
    audit._host_pool_geometry = {
        "full": {"capacity_units": 100, "bytes_per_unit": 16},
        "mamba": {"capacity_units": 10, "bytes_per_unit": 64},
    }
    root = SimpleNamespace(parent=None, key=None)
    key = SimpleNamespace(
        raw_token_ids=lambda: array("q", range(1, 9)),
        extra_key="tenant-a",
        cache_salt="salt-a",
        is_bigram=False,
    )
    node = SimpleNamespace(id=8, parent=root, key=key)
    audit.on_native_host_block_eviction(node, 0, 4)
    audit.on_native_host_block_eviction(node, 2, 2)

    request = SimpleNamespace(
        rid="req-revisit",
        beliefkv_metadata={
            "root_workflow_id": "w-1",
            "invocation_id": "i-1",
            "context_id": "c-1",
            "context_epoch": 0,
        },
        origin_input_ids=array("q", range(1, 9)),
        extra_key="tenant-a",
        cache_salt="salt-a",
        output_ids=[],
        cached_tokens_device=5,
        cached_tokens_host=1,
        mamba_host_hit_length=3,
        extend_input_len=2,
        sampling_params=SimpleNamespace(max_new_tokens=8),
        finished=lambda: False,
    )
    audit.on_enqueue(request)
    batch = SimpleNamespace(
        forward_mode=_Mode("prefill"),
        launch_ts=time.monotonic(),
        forward_iter=1,
        reqs=[request],
    )
    audit.on_launch(batch)
    audit.on_completed(batch)
    audit.close()

    rows = _read(tmp_path / "server/eviction_attribution.jsonl")
    attributed = [
        row for row in rows if row["event"] == "host_block_reaccess_attributed"
    ]
    full = next(row for row in attributed if row["pool"] == "full")
    assert full["outcome"] == "full_partial_hit_and_recompute"
    assert full["full_device_hit_units"] == 1
    assert full["full_host_hit_units"] == 1
    assert full["full_recomputed_units"] == 2
    assert full["attribution_semantics"] == (
        "exact_radix_prefix_and_full_token_interval"
    )

    mamba = next(row for row in attributed if row["pool"] == "mamba")
    assert mamba["outcome"] == "mamba_prefix_revisited_hit_location_unknown"
    assert mamba["mamba_host_hit_slots_at_request"] == 3
    assert "not exposed" in mamba["attribution_semantics"]
    assert all("input_ids" not in row and "key_path" not in row for row in rows)
    assert all("raw_token_ids" not in json.dumps(row) for row in rows)

    status = json.loads(
        (tmp_path / "server/native_telemetry_status.json").read_text()
    )
    evidence = status["host_block_eviction_attribution"]
    assert evidence["available"] is False
    assert evidence["reused_full_units"] == 2
    assert evidence["recomputed_full_units"] == 2


def test_block_attribution_processing_error_does_not_stop_core_telemetry(
    tmp_path: Path,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "server")

    def fail_attribution(_record: dict, _output: object) -> None:
        raise ValueError("bad attribution payload")

    audit._write_block_eviction = fail_attribution
    audit._emit("eviction_attribution", {
        "_internal_event": "host_block_eviction",
        "pool": "full",
    })
    audit._emit("audit", {"event": "core_audit_survives"})
    audit.close()

    attribution = _read(tmp_path / "server/eviction_attribution.jsonl")
    assert attribution[0]["event"] == "host_block_attribution_error"
    assert attribution[0]["error_type"] == "ValueError"
    assert _read(tmp_path / "server/runtime_audit.jsonl") == [
        {"event": "core_audit_survives"}
    ]
    status = json.loads(
        (tmp_path / "server/native_telemetry_status.json").read_text()
    )
    assert status["writer_error"] is None
    assert status["failed_records"] == 0
    assert (
        status["host_block_eviction_attribution"]["counts"]["processing_errors"]
        == 1
    )


def test_decode_deltas_conserve_tokens_when_forward_completions_overlap(
    tmp_path: Path,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "server")
    request = SimpleNamespace(
        rid="req-overlap",
        beliefkv_metadata={
            "root_workflow_id": "w-overlap",
            "invocation_id": "i-overlap",
            "context_id": "c-overlap",
            "context_epoch": 0,
        },
        origin_input_ids=list(range(12)),
        output_ids=[],
        cached_tokens_device=0,
        cached_tokens_host=0,
        extend_input_len=12,
        sampling_params=SimpleNamespace(max_new_tokens=8),
        finished=lambda: False,
    )
    audit.on_enqueue(request)
    prefill = SimpleNamespace(
        forward_mode=_Mode("prefill"), launch_ts=time.monotonic(),
        forward_iter=1, reqs=[request],
    )
    audit.on_launch(prefill)
    audit.on_completed(prefill)

    older = SimpleNamespace(
        forward_mode=_Mode("decode"), launch_ts=time.monotonic(),
        forward_iter=2, reqs=[request],
    )
    audit.on_launch(older)
    request.output_ids.append(11)
    newer = SimpleNamespace(
        forward_mode=_Mode("decode"), launch_ts=time.monotonic(),
        forward_iter=3, reqs=[request],
    )
    audit.on_launch(newer)
    request.output_ids.extend((12, 13))
    request.finished = lambda: True
    audit.on_completed(older)
    audit.on_completed(newer)
    audit.close()

    records = _read(tmp_path / "server/runtime_audit.jsonl")
    decode_deltas = [
        sample["token_delta"]
        for record in records
        if record["phase"] == "decode"
        for sample in record["request_samples"]
    ]
    events = _read(tmp_path / "server/runtime_events.sglang.jsonl")
    result = next(event for event in events if event["kind"] == "llm_result")
    assert sum(decode_deltas) == result["attributes"]["output_tokens"] == 3


def test_aborted_waiting_and_started_requests_do_not_leave_pending_telemetry(
    tmp_path: Path,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "server")
    metadata = {
        "root_workflow_id": "workflow",
        "invocation_id": "root",
        "context_id": "context",
        "context_epoch": 0,
    }
    waiting = SimpleNamespace(rid="queued", beliefkv_metadata=metadata)
    started = SimpleNamespace(
        rid="running", beliefkv_metadata=metadata,
        origin_input_ids=[1, 2], output_ids=[], extend_input_len=2,
        sampling_params=SimpleNamespace(max_new_tokens=4),
        finished=lambda: True,
    )
    audit.on_enqueue(waiting)
    audit.on_enqueue(started)
    batch = SimpleNamespace(
        forward_mode=_Mode("prefill"), launch_ts=time.monotonic(),
        forward_iter=1, reqs=[started],
    )
    audit.on_launch(batch)
    audit.on_abort_request(SimpleNamespace(abort_all=False, rid="queued"))
    audit.on_abort_request(SimpleNamespace(abort_all=False, rid="running"))
    audit.on_completed(batch)
    audit.close()
    events = _read(tmp_path / "server/runtime_events.sglang.jsonl")
    assert [event["kind"] for event in events] == ["llm_submit"]
    records = _read(tmp_path / "server/runtime_audit.jsonl")
    assert [(row["request_id"], row["stage"]) for row in records[:2]] == [
        ("queued", "waiting"), ("running", "started")
    ]
    status = json.loads((tmp_path / "server/native_telemetry_status.json").read_text())
    assert status["pending_request_count"] == 0
    assert status["pending_batch_count"] == 0
