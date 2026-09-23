from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

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
        )
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
    assert "not node-level identity matching" in (
        status["request_cache_evidence_semantics"]["after_eviction"]
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
