from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

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
    assert status["record_counts"] == {"audit": 2, "events": 2, "transfer": 1}


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
