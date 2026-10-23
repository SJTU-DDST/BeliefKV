import json
from pathlib import Path

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_position_replay_calibration as calibration


@pytest.fixture
def source(tmp_path):
    registration = tmp_path / "source.json"
    registration.write_text(json.dumps({"run_root": "/source"}))
    state = {
        "run_root": "/source", "status": "recording_native",
        "watcher": {"pid": 10}, "driver": {"pid": 11},
    }
    path = registration.with_suffix(".state.json")
    path.write_text(json.dumps(state))
    return {"source_registration": str(registration)}, state, path


@pytest.mark.parametrize("status,live_pid,expected", (
    ("recording_native", 10, False),
    ("waiting_gpu_capture", 10, False),
    ("capture_preflight", 10, False),
    ("source_captured_gpu_replay_pending", 10, False),
    ("source_captured_gpu_replay_pending", 11, False),
    ("source_captured_gpu_replay_pending", None, True),
))
def test_calibration_waits_for_source_quality_and_actual_process_release(
    source, monkeypatch, status, live_pid, expected,
):
    plan, state, path = source
    state.update(
        status=status, original_exit_code=0, source_selection={"accepted": True},
    )
    path.write_text(json.dumps(state))
    monkeypatch.setattr(calibration.reservation, "alive", lambda row: row["pid"] == live_pid)
    assert calibration.source_ready(plan) is expected


@pytest.mark.parametrize("change", ("rejected", "failed", "dead", "nonzero", "other_source", "unselected"))
def test_failed_or_unqualified_source_cannot_start_gpu_replay(source, monkeypatch, change):
    plan, state, path = source
    state.update(
        status="source_captured_gpu_replay_pending", original_exit_code=0,
        source_selection={"accepted": True},
    )
    if change == "rejected":
        state["status"] = "source_captured_selection_rejected"
    elif change == "failed":
        state["status"] = "capture_failed"
    elif change == "dead":
        state["status"] = "recording_native"
    elif change == "nonzero":
        state["original_exit_code"] = 1
    elif change == "other_source":
        state["run_root"] = "/another"
    else:
        state["source_selection"]["accepted"] = False
    path.write_text(json.dumps(state))
    monkeypatch.setattr(calibration.reservation, "alive", lambda _row: False)
    with pytest.raises(ValueError):
        calibration.source_ready(plan)


@pytest.mark.parametrize("change", (
    None, "wrong_sampler", "missing_request", "different_stop_count", "changed_receipts",
    "unverified_tokens", "missing_server",
))
def test_gpu_evidence_requires_actual_position_receipts_and_all_native_stops(tmp_path, change):
    arm = tmp_path / "gpu"
    (arm / "smoke").mkdir(parents=True)
    requests = arm / "smoke/native_requests.jsonl"
    rows = [{
        "request_id": str(index), "prompt_tokens": 12, "output_tokens": 3,
        "prompt_sha256": "input", "output_sha256": "output",
        "native_finish_reason": {"type": "stop", "matched": 9},
        "exact_tokens_verified": True, "native_receipt": {"sampling_path": "position"},
    } for index in range(24)]
    if change == "wrong_sampler":
        rows[0]["native_receipt"]["sampling_path"] = "legacy"
    elif change == "missing_request":
        rows.pop()
    elif change == "unverified_tokens":
        rows[0]["exact_tokens_verified"] = False
    requests.write_text("".join(json.dumps(row) + "\n" for row in rows))
    summary = {
        "verified_requests": 24, "native_data_plane_sample_verified": True,
        "native_stop_requests": 24, "native_requests_sha256": sha256_file(requests),
    }
    if change == "different_stop_count":
        summary["native_stop_requests"] = 23
    elif change == "changed_receipts":
        summary["native_requests_sha256"] = "changed"
    plan = {
        "source_sha256": {name: "qualified" for name in calibration.SAMPLING_MODULES},
        "source_arm": "/source/native_capture", "source_registration": "/source.json",
    }
    server = None if change == "missing_server" else {"pid": 10}
    if change is None:
        result = calibration.smoke_result(plan, summary, arm, {}, server, {"pid": 11})
        assert result["native_position_sampling_verified"] and result["gpu_process_verified"]
        assert result["requests"] == result["native_stop_requests"] == 24
        assert not result["performance_accepted"]
        client = json.loads(Path(result["client_registration"]).read_bytes())
        assert client["unchanged_server_modules"] == plan["source_sha256"]
    else:
        with pytest.raises(ValueError):
            calibration.smoke_result(plan, summary, arm, {}, server, {"pid": 11})
