import json

import pytest

from scripts.audit_child_notice_gpu_service import audit


def _events(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_final_request_gpu_stages_use_server_clock_and_request_identity(tmp_path):
    server = tmp_path / "server.jsonl"
    samples = tmp_path / "samples.jsonl"
    episodes = [
        {"project": "alpha", "post_notice": {"final_request_id": "final"}},
        {"project": "beta", "post_notice": {"final_request_id": "missing"}},
    ]
    _events(server, [
        {"kind": "llm_submit", "ts_ms": 100,
         "attributes": {"request_id": "final"}},
        {"kind": "llm_result", "ts_ms": 200,
         "attributes": {"request_id": "final"}},
        {"kind": "llm_submit", "ts_ms": 100,
         "attributes": {"request_id": "missing"}},
        {"kind": "llm_result", "ts_ms": 200,
         "attributes": {"request_id": "missing"}},
    ])
    _events(samples, [
        {"event": "gpu_service_sample", "ts_ms": 120,
         "request_samples": [{"request_id": "final", "phase": "prefill"}]},
        {"event": "gpu_service_sample", "ts_ms": 140,
         "request_samples": [{"request_id": "final", "phase": "decode"},
                             {"request_id": "other", "phase": "decode"}]},
        {"event": "gpu_service_sample", "ts_ms": 180,
         "request_samples": [{"request_id": "final", "phase": "decode"}]},
    ])
    result = audit(episodes, server, samples)
    assert result["pooled"]["requests"] == 1
    assert result["excluded"] == {"missing_gpu_service": 1}
    assert result["pooled"]["stages"] == {
        "submit_to_first_service_ms": {"p50_ms": 20, "p90_ms": 20},
        "first_service_to_first_decode_ms": {"p50_ms": 20, "p90_ms": 20},
        "first_decode_to_result_ms": {"p50_ms": 60, "p90_ms": 60},
        "submit_to_result_ms": {"p50_ms": 100, "p90_ms": 100},
    }
    assert result["by_project"]["beta"]["requests"] == 0


def test_duplicate_final_request_id_is_not_silently_overwritten(tmp_path):
    episodes = [
        {"project": project, "post_notice": {"final_request_id": "same"}}
        for project in ("alpha", "beta")
    ]
    with pytest.raises(ValueError, match="duplicate final request ID"):
        audit(episodes, tmp_path / "unused", tmp_path / "unused")


def test_late_sample_callback_does_not_censor_first_service(tmp_path):
    server = tmp_path / "server.jsonl"
    samples = tmp_path / "samples.jsonl"
    _events(server, [
        {"kind": "llm_submit", "ts_ms": 100,
         "attributes": {"request_id": "final"}},
        {"kind": "llm_result", "ts_ms": 200,
         "attributes": {"request_id": "final"}},
    ])
    _events(samples, [
        {"event": "gpu_service_sample", "ts_ms": 120,
         "request_samples": [{"request_id": "final", "phase": "prefill"}]},
        {"event": "gpu_service_sample", "ts_ms": 140,
         "request_samples": [{"request_id": "final", "phase": "decode"}]},
        {"event": "gpu_service_sample", "ts_ms": 205,
         "request_samples": [{"request_id": "final", "phase": "decode"}]},
    ])
    result = audit([
        {"project": "alpha", "post_notice": {"final_request_id": "final"}},
    ], server, samples)
    assert result["excluded"] == {}
    assert result["pooled"]["requests"] == 1
    assert result["post_result_service_lag_ms"]["p50"] == 5
