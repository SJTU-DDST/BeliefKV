"""Model signals are advisory and bound to live native request identities."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import time
from types import SimpleNamespace as NS

import pytest
from unittest.mock import Mock

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.sglang_v0520_admission import (
    select_native_prefill_candidates,
)
from beliefkv.runtime.sglang_v0520_prediction import (
    NativeDemandHint,
    NativeToolWaitHint,
    PREDICTION_ATTRIBUTE,
    parse_native_demand_hint,
    validate_admission_artifact,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime


MODEL_CONFIG = b"{}"
ARTIFACT = json.dumps(
    {
        "metadata": {
            "calibration_status": "calibrated",
            "online_eligible": True,
            "semantic_source_runtime_environment_contracts": [
                {
                    "model_revision_sha256": {
                        "config.json": hashlib.sha256(MODEL_CONFIG).hexdigest()
                    },
                    "server_identity": {"sglang_version": "0.5.20"},
                }
            ],
        }
    },
    sort_keys=True,
).encode()
SHA = hashlib.sha256(ARTIFACT).hexdigest()


def test_tool_wait_hint_is_immutable_and_bound_to_identity_and_lifetime():
    from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey

    identity = PrefillCandidateKey("a", "wf", "a", "ctx-a", 0, 0)
    other = PrefillCandidateKey("b", "wf", "a", "ctx-a", 0, 0)
    hint = NativeToolWaitHint(identity, 10.0, 20.0, 30.0, 100.0, 200.0, SHA, 7.0)
    assert hint.live(identity, now_ms=100.0)
    assert hint.live(identity, now_ms=199.0)
    assert not hint.live(identity, now_ms=200.0)
    assert not hint.live(identity, now_ms=99.0)
    assert not hint.live(other, now_ms=150.0)
    assert hint.invocation_revision_ts_ms == 7.0
    with pytest.raises(FrozenInstanceError):
        hint.wait_p50_ms = 99.0


def req(name: str):
    return NS(
        rid=name,
        beliefkv_metadata={
            "root_workflow_id": "wf",
            "invocation_id": name,
            "context_id": f"ctx-{name}",
            "context_epoch": 0,
        },
        cache_request_handle=NS(attempt_id=0),
        session_id=None,
        session_generation=None,
        finished=lambda: False,
    )


def event(seq, kind, invocation_id=None, **kwargs):
    return RuntimeEvent(
        event_id=f"e{seq}",
        ts_ms=float(seq),
        kind=kind,
        workflow_id="wf",
        invocation_id=invocation_id,
        **kwargs,
    )


def hint_event(seq, request, demand, *, model=SHA, now=None):
    now = time.monotonic() * 1000 if now is None else now
    metadata = request.beliefkv_metadata
    return event(
        seq,
        RuntimeEventKind.STRUCTURED_ACTION,
        invocation_id=metadata["invocation_id"],
        context_id=metadata["context_id"],
        context_epoch=metadata["context_epoch"],
        attributes={
            PREDICTION_ATTRIBUTE: {
                **metadata,
                "request_id": request.rid,
                "attempt_id": request.cache_request_handle.attempt_id,
                "session_id": request.session_id,
                "session_generation": request.session_generation,
                "predictor_sha256": model,
                "issued_monotonic_ms": now - 10,
                "expires_monotonic_ms": now + 5000,
                "next_output_tokens": demand,
            }
        },
    )


def select(runtime, requests):
    plan = runtime.plan_native_prefill(requests, running_batch=None, adder=None)
    return select_native_prefill_candidates(
        requests, plan=plan, current_semantic_revision=runtime.semantic_revision
    ).candidates


def init(tmp_path):
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "config.json").write_bytes(MODEL_CONFIG)
    artifact = tmp_path / "predictor.json"
    artifact.write_bytes(ARTIFACT)
    runtime = NativeAdmissionRuntime(
        event_socket_path=str(tmp_path / "events.sock"),
        predictor_sha256=SHA,
        predictor_artifact_path=str(artifact),
        model_path=str(model_path),
    )
    a, b = req("a"), req("b")
    runtime.register_visible_request(a)
    runtime.register_visible_request(b)
    runtime.on_events(
        (
            event(0, RuntimeEventKind.WORKFLOW_START),
            event(
                1, RuntimeEventKind.INVOCATION_CREATE,
                "a", context_id="ctx-a",
                agent_definition_id="a", agent_instance_id="a",
            ),
            event(
                2, RuntimeEventKind.INVOCATION_CREATE,
                "b", context_id="ctx-b",
                agent_definition_id="b", agent_instance_id="b",
            ),
        )
    )
    return runtime, a, b


def test_complete_fresh_prediction_reorders_only_equivalent_causal_class(tmp_path):
    runtime, a, b = init(tmp_path)
    try:
        runtime.on_events((hint_event(3, a, 100),))
        assert select(runtime, [a, b]) == (a, b)
        runtime.on_events((hint_event(4, b, 4),))
        assert select(runtime, [a, b]) == (b, a)
        runtime.on_prefill_candidate_result(b, admitted=True, result="CONTINUE")
        assert select(runtime, [a, b]) == (a, b)
    finally:
        runtime.close()


def test_wrong_model_does_not_mutate_graph_or_model_state(tmp_path):
    runtime, a, b = init(tmp_path)
    try:
        revision = runtime.semantic_revision
        with pytest.raises(ValueError, match="fingerprint"):
            runtime.on_events((hint_event(3, a, 5, model="b" * 64),))
        assert runtime.semantic_revision == revision
        assert runtime.demand_hints == {}
    finally:
        runtime.close()


def test_expired_changed_attempt_and_generation_fall_back(tmp_path):
    runtime, a, b = init(tmp_path)
    try:
        runtime.on_events((hint_event(3, a, 100), hint_event(4, b, 4)))
        now = time.monotonic() * 1000
        runtime.demand_hints[b.rid] = replace(
            runtime.demand_hints[b.rid], expires_monotonic_ms=now - 1
        )
        assert select(runtime, [a, b]) == (a, b)
        runtime.on_events((hint_event(5, b, 4),))
        b.cache_request_handle.attempt_id += 1
        assert select(runtime, [a, b]) == (a,)
        runtime.on_requests_requeued([b], is_retracted=True)
        assert select(runtime, [a, b]) == (a, b)
        b.session_id = "session"
        b.session_generation = 1
        runtime.on_requests_requeued([b], is_retracted=True)
        runtime.on_events((hint_event(6, b, 4),))
        b.session_generation = 2
        assert select(runtime, [a, b]) == (a,)
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("context_epoch", True, "identity"),
        ("next_output_tokens", 0, "demand"),
        ("next_output_tokens", 200_000, "demand"),
        ("expires_monotonic_ms", float("inf"), "clock"),
    ],
)
def test_invalid_payload_rejected(field, value, match):
    request = req("a")
    evt = hint_event(3, request, 8)
    raw = dict(evt.attributes[PREDICTION_ATTRIBUTE])
    raw[field] = value
    with pytest.raises(ValueError, match=match):
        parse_native_demand_hint(evt, raw, expected_sha256=SHA)


def test_old_model_or_unapproved_predictor_cannot_enable_admission(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_bytes(MODEL_CONFIG)
    artifact = tmp_path / "predictor.json"
    artifact.write_bytes(ARTIFACT)
    validate_admission_artifact(
        str(artifact), expected_sha256=SHA, model_path=str(model)
    )
    with pytest.raises(ValueError, match="SHA-256"):
        validate_admission_artifact(
            str(artifact), expected_sha256="a" * 64, model_path=str(model)
        )
    (model / "config.json").write_bytes(b'{"new": true}')
    with pytest.raises(ValueError, match="model file SHA-256"):
        validate_admission_artifact(
            str(artifact), expected_sha256=SHA, model_path=str(model)
        )
    (model / "config.json").write_bytes(MODEL_CONFIG)
    old = json.loads(ARTIFACT)
    old["metadata"]["semantic_source_runtime_environment_contracts"][0][
        "server_identity"
    ]["sglang_version"] = "0.5.2rc1"
    old["metadata"]["online_eligible"] = False
    artifact.write_text(json.dumps(old), encoding="utf-8")
    with pytest.raises(ValueError, match="not calibrated and online eligible"):
        validate_admission_artifact(
            str(artifact),
            expected_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            model_path=str(model),
        )
    old["metadata"]["online_eligible"] = True
    artifact.write_text(json.dumps(old), encoding="utf-8")
    with pytest.raises(ValueError, match="another model/runtime"):
        validate_admission_artifact(
            str(artifact),
            expected_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            model_path=str(model),
        )


def test_admission_artifact_validates_declared_model_files(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_bytes(MODEL_CONFIG)
    (model / "model.safetensors.index.json").write_bytes(b"weights-v1")
    raw = json.loads(ARTIFACT)
    contract = raw["metadata"]["semantic_source_runtime_environment_contracts"][0]
    contract["model_revision_sha256"]["model.safetensors.index.json"] = (
        hashlib.sha256(b"weights-v1").hexdigest()
    )
    artifact = tmp_path / "predictor.json"

    def check():
        artifact.write_text(json.dumps(raw), encoding="utf-8")
        validate_admission_artifact(
            str(artifact),
            expected_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            model_path=str(model),
        )

    check()
    (model / "model.safetensors.index.json").write_bytes(b"weights-v2")
    with pytest.raises(ValueError, match="model file SHA-256"):
        check()
    contract["model_revision_sha256"] = "not-a-manifest"
    with pytest.raises(ValueError, match="source contract|another model/runtime"):
        check()
    contract["model_revision_sha256"] = {
        "config.json": hashlib.sha256(MODEL_CONFIG).hexdigest(),
        "../other-model/config.json": hashlib.sha256(MODEL_CONFIG).hexdigest(),
    }
    with pytest.raises(ValueError, match="another model/runtime"):
        check()


def test_local_model_result_is_applied_only_to_still_visible_requests(tmp_path):
    runtime, a, b = init(tmp_path)
    submitted = []
    now = time.monotonic() * 1000
    model = NS(
        disabled=False,
        poll=Mock(return_value=()),
        submit=lambda items: submitted.append(items),
        close=lambda: None,
    )
    runtime._model_worker = model
    try:
        assert select(runtime, [a, b]) == (a, b)
        assert len(submitted) == 1
        assert len(submitted[0]) == 2
        assert select(runtime, [a, b]) == (a, b)
        assert len(submitted) == 1
        a_key, b_key = (item[0] for item in submitted[0])
        model.poll.return_value = (
            NativeDemandHint(a_key, 100, now, now + 5000, SHA, 1.0),
            NativeDemandHint(b_key, 4, now, now + 5000, SHA, 2.0),
        )
        runtime.scheduler_step()
        assert select(runtime, [a, b]) == (b, a)
        b.cache_request_handle.attempt_id = 1
        assert select(runtime, [a, b]) == (a,)
        b.cache_request_handle.attempt_id = 0
        runtime.graph.invocations["b"].updated_ts_ms = 9.0
        assert select(runtime, [a, b]) == (a, b)
    finally:
        runtime.close()
