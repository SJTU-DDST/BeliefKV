import hashlib
import json
from pathlib import Path
import pytest

from scripts.export_delivered_semantic_work_samples import collect, normalized_seals, reconstruct
from scripts.replay_semantic_work_prefix import prefix_records


def sample_inputs():
    forecast = {
        "ts_ms": 2100., "request_id": "request", "context_id": "context",
        "context_epoch": 2, "content_chars": 10, "observed_output_tokens": 20,
        "notice_active": True, "estimated_report_tokens": 200,
        "prior_tool_calls": 3, "prior_model_rounds": 4,
        "sampled_tokens_per_second": 40.,
    }
    text = {
        "ts_ms": 1000., "request_id": "request", "context_id": "context",
        "context_epoch": 2, "content_chars": 10, "invocation_id": "child",
        "semantic_content_tail": "Finished.",
    }
    native = {"ts_ms": 2200., "attributes": {"output_tokens": 27}}
    return forecast, text, native


def test_reconstruct_uses_native_token_label_and_delivered_features(tmp_path):
    forecast, text, native = sample_inputs()
    result = reconstruct(
        forecast, text, native, 1250., 1000., run=tmp_path, task="psf__requests-1",
    )
    assert result["remaining_tokens"] == 7
    assert result["remaining_client_wall_ms"] == 150.
    assert result["lead_to_native_eos_ms_label"] == 100.
    assert result["project"] == "psf"
    assert result["observation"]["estimated_report_tokens"] == 200
    assert result["observation"]["ts_ms"] == 1000.
    assert "native_output_tokens_label" not in result["observation"]


@pytest.mark.parametrize("change", ["future_text", "after_eos", "counter_overrun", "tool_chunk"])
def test_reject_noncausal_or_invalid_work_labels(tmp_path, change):
    forecast, text, native = sample_inputs()
    if change == "future_text":
        text["ts_ms"] = 1101.
    elif change == "after_eos":
        forecast["ts_ms"] = 2300.
    elif change == "counter_overrun":
        forecast["observed_output_tokens"] = 28
    else:
        text["tool_chunk"] = True
    assert reconstruct(
        forecast, text, native, 1400., 1000., run=tmp_path, task="psf__requests-1",
    ) is None


def test_reject_foreign_content_identity(tmp_path):
    forecast, text, native = sample_inputs()
    text["context_epoch"] = 1
    with pytest.raises(ValueError, match="identity"):
        reconstruct(
            forecast, text, native, 1250., 1000., run=tmp_path, task="psf__requests-1",
        )


@pytest.mark.parametrize("key", [
    "verified_input_prefixes", "source_prefix_seals", "input_jsonl_prefixes",
])
def test_seal_schemas_exclude_appends_and_detect_rewritten_bytes(tmp_path, key):
    path = tmp_path / "trace.jsonl"
    original = b'{"original":true}\n'
    path.write_bytes(original + b'{"future":true}\n')
    metadata = (
        {"read_complete_prefix_bytes": len(original), "sha256": hashlib.sha256(original).hexdigest()}
        if key == "source_prefix_seals" else
        {"complete_prefix_bytes": len(original), "complete_prefix_sha256": hashlib.sha256(original).hexdigest()}
    )
    seals = normalized_seals({key: {str(path): metadata}})
    assert list(prefix_records(path, seals)) == [{"original": True}]
    path.write_bytes(b'{"original":null}\n')
    with pytest.raises(ValueError, match="changed"):
        list(prefix_records(path, seals))


def test_missing_seals_are_rejected():
    with pytest.raises(ValueError, match="sealed source"):
        normalized_seals({})


@pytest.mark.parametrize("clock_kind", ["safe_point_census", "admission_runtime_state"])
@pytest.mark.parametrize("relative_seals", [False, True])
def test_collect_keeps_sealed_text_and_excludes_intervened_workflow(
    tmp_path, clock_kind, relative_seals,
):
    arm = tmp_path / "arm"
    workflow = arm / "client_1/workflows/psf__requests-1"
    forecast, text, native = sample_inputs()
    sources = {
        arm / "opportunities/admission_opportunities.jsonl": [
            {"event": clock_kind, "ts_ms": 2000., "monotonic_ms": 1000.},
            {"event": "semantic_child_forecast", **forecast},
        ],
        arm / "server/runtime_events.sglang.jsonl": [
            {"kind": "llm_result", **native, "attributes": {
                **native["attributes"], "request_id": "request",
            }},
        ],
        workflow / "runtime_events.deepagents.jsonl": [
            {"kind": "llm_result", "invocation_id": "child", "attributes": {
                "request_id": "request", "tool_call_count": 0,
            }},
            {"kind": "return", "invocation_id": "child", "ts_ms": 1250., "attributes": {
                "source": "deepagents_task", "outcome": "completed",
            }},
        ],
        workflow / "child_stream_content.jsonl": [
            {"event": "child_stream_content", **text},
        ],
    }
    seals = {}
    for path, rows in sources.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(json.dumps(row) + "\n" for row in rows).encode()
        path.write_bytes(payload)
        seals[str(path)] = {
            "complete_prefix_bytes": len(payload),
            "complete_prefix_sha256": hashlib.sha256(payload).hexdigest(),
        }
    audit = workflow / "sandbox_audit.jsonl"
    audit.write_text("")
    report = tmp_path / "sealed.json"
    report.write_text(json.dumps({"verified_input_prefixes": {
        str(Path(name).relative_to(tmp_path)) if relative_seals else name: seal
        for name, seal in seals.items()
    }}))
    content = workflow / "child_stream_content.jsonl"
    with content.open("a") as stream:
        stream.write(json.dumps({"event": "child_stream_content", **text, "ts_ms": 3000.}) + "\n")
    rows, coverage, verified, excluded = collect(report, arm, source_root=tmp_path)
    assert len(rows) == 1
    assert coverage["notice_active_snapshot_count"] == 1
    assert excluded == []
    assert verified[str(content)]["read_complete_prefix_bytes"] == seals[str(content)]["complete_prefix_bytes"]
    assert verified[str(audit)]["sha256"] == hashlib.sha256(b"").hexdigest()
    audit.write_text(json.dumps({"event": "agent_protocol_repair_attempt"}) + "\n")
    rows, coverage, _, excluded = collect(report, arm, source_root=tmp_path)
    assert rows == []
    assert excluded == ["psf__requests-1"]
    assert coverage["omitted_snapshot_counts"]["intervened_or_missing_intervention_audit"] == 1


def test_relative_seals_require_an_explicit_root_and_cannot_escape_it(tmp_path):
    seal = {"complete_prefix_bytes": 0, "complete_prefix_sha256": hashlib.sha256(b"").hexdigest()}
    report = {"input_jsonl_prefixes": {"trace.jsonl": seal}}
    with pytest.raises(ValueError, match="explicit source root"):
        normalized_seals(report)
    assert str(tmp_path / "trace.jsonl") in normalized_seals(report, source_root=tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        normalized_seals(
            {"input_jsonl_prefixes": {"../trace.jsonl": seal}}, source_root=tmp_path,
        )
