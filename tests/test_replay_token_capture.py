from array import array
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.runtime.replay_token_capture import (
    capture_record, decode_tokens, encode_tokens,
    native_request_expectations, verify_capture,
)
from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry
from scripts.audit_replay_token_capture import audit
from scripts.audit_replay_token_capture import main as audit_main
from tests.test_v0520_native_telemetry import _Mode


def request(request_id, tokens, *, epoch=0, session="s", generation=1, salt=None):
    return NS(
        rid=request_id, beliefkv_metadata={
            "root_workflow_id": "w", "invocation_id": "i",
            "context_id": "c", "context_epoch": epoch,
        },
        origin_input_ids=array("q", tokens), output_ids=[7, 8],
        cached_tokens_device=0, cached_tokens_host=0, mamba_host_hit_length=0,
        extend_input_len=len(tokens), sampling_params=NS(max_new_tokens=32),
        session_id=session, session_generation=generation,
        extra_key=None, cache_salt=salt, finished=lambda: True,
    )


def complete(telemetry, req, iteration):
    telemetry.on_enqueue(req)
    batch = NS(
        forward_mode=_Mode("prefill"), launch_ts=time.monotonic(),
        forward_iter=iteration, reqs=[req],
    )
    telemetry.on_launch(batch)
    telemetry.on_completed(batch)


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


@pytest.mark.parametrize("count", (0, 1, 32, 100_000))
@pytest.mark.parametrize("with_base", (False, True))
def test_token_codec_keeps_exact_paths_at_long_context_lengths(count, with_base):
    tokens = array("q", range(count))
    common = count // 2 if with_base else 0
    record = encode_tokens(
        tokens, base_request_id="prior" if common else None,
        common_prefix_tokens=common,
    )
    restored = decode_tokens(record, base_tokens=tokens if common else None)
    assert list(restored) == list(tokens)
    assert len(record["token_suffix_b64"]) <= 4 * ((4 * (count - common) + 2) // 3)


@pytest.mark.parametrize("tokens", ([True], [1.0], [-1], [1 << 32]))
def test_capture_rejects_non_token_values(tokens):
    with pytest.raises((ValueError, TypeError)):
        encode_tokens(tokens)


@pytest.mark.parametrize("change", ("missing_base", "short_base", "wrong_base", "suffix", "count"))
def test_decode_refuses_missing_or_changed_identity(change):
    record = encode_tokens([1, 2, 3], base_request_id="prior", common_prefix_tokens=2)
    base = [1, 2]
    if change == "missing_base":
        base = None
    elif change == "short_base":
        base = [1]
    elif change == "wrong_base":
        base = [9, 2]
    elif change == "suffix":
        record["token_suffix_b64"] = "invalid!"
    else:
        record["token_count"] = 10
    with pytest.raises(ValueError):
        decode_tokens(record, base_tokens=base)


@pytest.mark.parametrize("change", (
    "session", "generation", "namespace", "history_expired", "invocation", None,
))
def test_native_capture_uses_only_a_served_same_scope_prefix(tmp_path, monkeypatch, change):
    monkeypatch.setenv("BELIEFKV_REPLAY_TOKEN_CAPTURE", "1")
    telemetry = NativeReactiveTelemetry(tmp_path / "arm/server")
    if change == "history_expired":
        telemetry._context_prefix_history_limit = 0
    first = request("first", range(100_000))
    complete(telemetry, first, 1)
    first.origin_input_ids[0] = 999999
    first.output_ids[0] = 999999
    second = request(
        "next", range(100_001), epoch=1,
        session="other" if change == "session" else "s",
        generation=2 if change == "generation" else 1,
        salt="other" if change == "namespace" else None,
    )
    if change == "invocation":
        second.beliefkv_metadata["invocation_id"] = "resumed"
    complete(telemetry, second, 2)
    second.origin_input_ids[0] = 888888
    second.output_ids[0] = 888888
    telemetry.close()
    capture = rows(tmp_path / "arm/server/request_tokens.jsonl")
    assert len(capture) == 4
    prompts = [row for row in capture if row["event"] == "request_prompt"]
    assert prompts[0]["base_request_id"] is None
    shared = change in (None, "invocation")
    assert prompts[1]["base_request_id"] == ("first" if shared else None)
    assert prompts[1]["common_prefix_tokens"] == (100_000 if shared else 0)
    native = native_request_expectations(
        rows(tmp_path / "arm/server/runtime_events.sglang.jsonl"),
    )
    verified = verify_capture(capture, native)
    assert verified["token_evidence_complete"]
    assert verified["prompt_tokens"] == 200001
    assert verified["output_tokens"] == 4
    assert not verified["gpu_replay_qualified"]
    assert not verified["performance_accepted"]
    assert list(decode_tokens(capture[1])) == [7, 8]
    report = audit(tmp_path / "arm")
    assert report["token_evidence_complete"]
    assert report["native_requests"] == 2
    assert report["input_jsonl_prefixes"]
    if change is None:
        output = tmp_path / "verified.json"
        monkeypatch.setattr(sys, "argv", [
            "audit_replay_token_capture", "--arm", str(tmp_path / "arm"),
            "--output", str(output), "--require-complete",
        ])
        audit_main()
        assert json.loads(output.read_bytes())["token_evidence_complete"]


def synthetic_capture():
    identity = {
        "workflow_id": "w", "invocation_id": "i", "context_id": "c",
        "context_epoch": 0, "session_id": "s", "session_generation": 1,
        "ts_ms": 1., "_replay_request_parameters": {"max_new_tokens": 32},
    }
    captured, events = [], []
    for index in range(2):
        request_id = f"r{index}"
        current = {**identity, "request_id": request_id, "context_epoch": index}
        captured.extend((
            capture_record(
                current, [1, 2, index], event="request_prompt",
                base_request_id="r0" if index else None,
                common_prefix_tokens=2 if index else 0,
            ),
            capture_record(current, [7], event="request_output"),
        ))
        for kind, field, count in (
            ("llm_submit", "prompt_tokens", 3), ("llm_result", "output_tokens", 1),
        ):
            events.append({**current, "kind": kind,
                           "attributes": {"request_id": request_id, field: count}})
    return captured, events


@pytest.mark.parametrize("change", (
    "missing_prompt", "missing_output", "duplicate_prompt", "duplicate_output",
    "foreign_base", "wrong_native_count", "wrong_context", "wrong_budget",
    "truncated_suffix", "schema",
))
def test_capture_verifier_rejects_incomplete_or_mismatched_work(change):
    capture, events = synthetic_capture()
    if change == "missing_prompt":
        del capture[0]
    elif change == "missing_output":
        capture.pop()
    elif change == "duplicate_prompt":
        capture.insert(0, deepcopy(capture[0]))
    elif change == "duplicate_output":
        capture.append(deepcopy(capture[-1]))
    elif change == "foreign_base":
        capture[2]["session_id"] = "other"
    elif change == "wrong_native_count":
        events[-1]["attributes"]["output_tokens"] = 2
    elif change == "wrong_context":
        capture[2]["context_id"] = "other"
    elif change == "wrong_budget":
        capture[0]["max_new_tokens"] = 0
    elif change == "truncated_suffix":
        capture[2]["token_suffix_b64"] = ""
    else:
        capture[0]["schema_version"] = True
    with pytest.raises(ValueError):
        verify_capture(capture, native_request_expectations(events))


def test_missing_capture_remains_unknown_instead_of_using_lengths(tmp_path):
    _, events = synthetic_capture()
    server = tmp_path / "arm/server"
    server.mkdir(parents=True)
    (server / "runtime_events.sglang.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events),
    )
    report = audit(tmp_path / "arm")
    assert report["reason"] == "exact_prompt_and_output_capture_unavailable"
    assert not report["token_evidence_complete"]
    assert report["native_prompt_tokens"] == 6


def test_required_capture_rejects_missing_data_and_preserves_the_report(tmp_path, monkeypatch):
    _, events = synthetic_capture()
    server = tmp_path / "arm/server"
    server.mkdir(parents=True)
    (server / "runtime_events.sglang.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events),
    )
    output = tmp_path / "incomplete.json"
    monkeypatch.setattr(sys, "argv", [
        "audit_replay_token_capture", "--arm", str(tmp_path / "arm"),
        "--output", str(output), "--require-complete",
    ])
    with pytest.raises(SystemExit) as error:
        audit_main()
    assert error.value.code == 2
    assert not json.loads(output.read_bytes())["token_evidence_complete"]


@pytest.mark.parametrize("setting", ("true", "2", ""))
def test_invalid_capture_flag_fails_before_creating_evidence(tmp_path, monkeypatch, setting):
    monkeypatch.setenv("BELIEFKV_REPLAY_TOKEN_CAPTURE", setting)
    with pytest.raises(ValueError, match="must be 0 or 1"):
        NativeReactiveTelemetry(tmp_path / "server")
    assert not (tmp_path / "server").exists()


def test_disabled_capture_does_not_copy_input_or_output_tokens(tmp_path, monkeypatch):
    monkeypatch.delenv("BELIEFKV_REPLAY_TOKEN_CAPTURE", raising=False)
    telemetry = NativeReactiveTelemetry(tmp_path / "server")
    telemetry.close()
    emitted = []
    telemetry._emit = lambda stream, record: emitted.append(record)
    req = request("off", [1, 2, 3])
    telemetry._request_token_array = lambda *args, **kwargs: pytest.fail("disabled copy")
    telemetry._replay_stopping_parameters = lambda *args: pytest.fail("disabled stopping snapshot")
    telemetry._replay_finish_reason = lambda *args: pytest.fail("disabled finish snapshot")
    telemetry._queue_context_prefix(req, telemetry._identity(req), served=False)
    telemetry._queue_context_prefix(req, telemetry._identity(req), served=True)
    assert all(row["input_ids"] is req.origin_input_ids for row in emitted)
    assert all("_replay_output_ids" not in row for row in emitted)
    assert not (tmp_path / "server/request_tokens.jsonl").exists()


@pytest.mark.parametrize("finish", (
    {"type": "stop", "matched": 8},
    {"type": "stop", "matched": [7, 8]},
    {"type": "stop", "matched": "</tool_call>"},
    {"type": "length", "length": 2},
    {"type": "abort", "message": "cancelled"},
    None,
))
def test_capture_snapshots_original_stopping_rules_and_native_finish_reason(
    tmp_path, monkeypatch, finish,
):
    from copy import deepcopy

    monkeypatch.setenv("BELIEFKV_REPLAY_TOKEN_CAPTURE", "1")
    telemetry = NativeReactiveTelemetry(tmp_path / "server")
    req = request("native-stop", [1, 2, 3])
    req.sampling_params = NS(
        max_new_tokens=32, min_new_tokens=1, ignore_eos=False,
        stop_strs=["done"], stop_regex_strs=["END[.]"], stop_token_ids={8},
    )
    original = deepcopy(finish)
    req.finished_reason = NS(to_json=lambda: finish) if finish is not None else None
    emitted = []
    telemetry._emit = lambda stream, record: emitted.append(record)
    try:
        telemetry._queue_context_prefix(req, telemetry._identity(req), served=True)
        req.sampling_params.stop_strs.append("changed")
        req.sampling_params.stop_token_ids.add(999)
        if finish is not None:
            finish["type"] = "changed"
        record = emitted[0]
        captured = capture_record(record, record["_replay_output_ids"], event="request_output")
        assert captured["native_finish_reason"] == original
        assert captured["sampling_parameters"] == {
            "max_new_tokens": 32, "min_new_tokens": 1, "ignore_eos": False,
            "stop": ("done",), "stop_regex": ("END[.]",), "stop_token_ids": (8,),
        }
    finally:
        telemetry.close()


def test_capture_verifier_rejects_stopping_rule_changes_between_boundaries():
    capture, events = synthetic_capture()
    capture[1]["sampling_parameters"] = {"max_new_tokens": 32, "ignore_eos": True}
    with pytest.raises(ValueError, match="stopping parameters"):
        verify_capture(capture, native_request_expectations(events))
