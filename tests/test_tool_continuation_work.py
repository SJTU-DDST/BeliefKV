from copy import deepcopy
import hashlib
import json

import pytest

from beliefkv.runtime.replay_token_capture import _wire
from scripts.audit_tool_continuation_work import (
    audit_arm, continuation_rows, main, summarize, validated_requests,
)


def fixture():
    requests = []
    for name, epoch, submitted, completed, output in (
        ("origin", 1, 1., 10., 20), ("next", 2, 30., 60., 2048),
    ):
        receipt = {
            "request_id": name, "identity": ["w", "i", "ctx", epoch],
            "clock_domain": "clock", "complete_monotonic_ms": completed,
            "prompt_tokens": 100, "output_token_ids": [7] * output,
            "prompt_sha256": "a" * 64,
            "native_finish_reason": {"type": "stop"},
            "session_id": "session", "session_generation": 1,
            "sampling_parameters": {"max_new_tokens": 4096},
        }
        requests.append({
            **{key: receipt[key] for key in (
                "request_id", "prompt_tokens",
                "prompt_sha256", "native_finish_reason",
            )},
            "output_tokens": output,
            "output_sha256": hashlib.sha256(_wire(receipt["output_token_ids"])).hexdigest(),
            "exact_tokens_verified": True,
            "submitted_monotonic_ms": submitted,
            "native_complete_monotonic_ms": completed,
            "http_complete_monotonic_ms": completed + 1.,
            "native_receipt": receipt,
        })
    episode = {
        "workflow_id": "w", "invocation_id": "i",
        "origin_context_id": "ctx", "origin_context_epoch": 1,
        "context_id": "ctx", "context_epoch": 2,
        "origin_request_id": "origin", "request_id": "next",
        "tool_round_duration_ms": 8., "context_compactions_before_successor": 0,
    }
    native = {
        "kind": "llm_submit", "workflow_id": "w", "invocation_id": "i",
        "context_id": "ctx", "context_epoch": 2, "ts_ms": 100_000.,
        "attributes": {
            "request_id": "next", "cached_tokens_device": 60,
            "cached_tokens_host": 30, "mamba_host_hit_slots": 1,
            "uncached_prompt_tokens": 10,
        },
    }
    return requests, episode, native


def test_short_gap_retains_long_successor_work_and_separate_native_clock():
    requests, episode, native = fixture()
    rows = continuation_rows(
        [episode], validated_requests(requests), [native], short_gap_ms=1000.,
    )
    [row] = rows
    assert row["short_return_identity_candidate"] is True
    assert row["native_completion_to_next_submit_ms"] == 20.
    assert row["successor_output_tokens"] == 2048
    summary = summarize(rows)
    assert summary["successor_output_at_least"]["2048"] == 1
    assert summary["first_service_cache_counters"]["with_full_host_hit"] == 1
    assert "gpu_service_ms" not in row
    assert "fully_resident_at_tool_end" not in row


@pytest.mark.parametrize("mutation", (
    "exact", "duplicate", "receipt_identity", "token_work", "token_hash",
    "output_hash", "output_ids", "finish", "missing_finish",
    "mixed_clock", "early_http", "negative_clock", "boolean_epoch",
))
def test_invalid_exact_work_identity_clock_and_stopping_are_rejected(mutation):
    requests, _, _ = fixture()
    if mutation == "exact":
        requests[0]["exact_tokens_verified"] = False
    elif mutation == "duplicate":
        requests.append(deepcopy(requests[0]))
    elif mutation == "receipt_identity":
        requests[0]["native_receipt"]["request_id"] = "foreign"
    elif mutation == "token_work":
        requests[0]["output_tokens"] += 1
    elif mutation == "token_hash":
        requests[0]["prompt_sha256"] = "c" * 64
    elif mutation == "output_hash":
        requests[0]["output_sha256"] = "c" * 64
    elif mutation == "output_ids":
        requests[0]["native_receipt"]["output_token_ids"][0] = 7.
    elif mutation == "finish":
        requests[0]["native_finish_reason"] = {"type": "length"}
    elif mutation == "missing_finish":
        requests[0]["native_finish_reason"] = None
        requests[0]["native_receipt"]["native_finish_reason"] = None
    elif mutation == "mixed_clock":
        requests[0]["native_receipt"]["clock_domain"] = "other"
    elif mutation == "early_http":
        requests[0]["http_complete_monotonic_ms"] = 1.
    elif mutation == "negative_clock":
        requests[0]["submitted_monotonic_ms"] = -1.
    else:
        requests[0]["native_receipt"]["identity"][3] = True
    with pytest.raises(ValueError):
        validated_requests(requests)


@pytest.mark.parametrize("mutation,reason", (
    ("long_tool", "long_tool_round"),
    ("long_submit", "long_completion_to_submit_gap"),
    ("session", "context_or_session_changed_or_unknown"),
    ("unknown_session", "context_or_session_changed_or_unknown"),
    ("generation", "context_or_session_changed_or_unknown"),
    ("boolean_generation", "context_or_session_changed_or_unknown"),
    ("compaction", "context_compaction"),
))
def test_noncontinuations_remain_in_the_observed_population(mutation, reason):
    requests, episode, native = fixture()
    if mutation == "long_tool":
        episode["tool_round_duration_ms"] = 1001.
    elif mutation == "long_submit":
        requests[1]["submitted_monotonic_ms"] = 1020.
        requests[1]["native_complete_monotonic_ms"] = 1030.
        requests[1]["http_complete_monotonic_ms"] = 1031.
        requests[1]["native_receipt"]["complete_monotonic_ms"] = 1030.
    elif mutation in ("session", "unknown_session"):
        requests[1]["native_receipt"]["session_id"] = "other" if mutation == "session" else None
    elif mutation == "generation":
        requests[1]["native_receipt"]["session_generation"] = 2
    elif mutation == "boolean_generation":
        requests[1]["native_receipt"]["session_generation"] = True
    else:
        episode["context_compactions_before_successor"] = 1
    [row] = continuation_rows(
        [episode], validated_requests(requests), [native], short_gap_ms=1000.,
    )
    assert row["short_return_identity_candidate"] is False
    assert row["exclusion_reasons"] == [reason]
    assert row["successor_output_tokens"] == 2048


@pytest.mark.parametrize("mutation", (
    "native_identity", "native_missing", "work_missing", "duplicate_successor",
    "origin_identity", "cache_unknown", "cache_boolean",
))
def test_missing_or_misattributed_observations_are_not_reported_as_zero(mutation):
    requests, episode, native = fixture()
    indexed = validated_requests(requests)
    episodes, native_rows = [episode], [native]
    if mutation == "native_identity":
        native["workflow_id"] = "other"
    elif mutation == "native_missing":
        native_rows = []
    elif mutation == "work_missing":
        del indexed["origin"]
    elif mutation == "duplicate_successor":
        episodes *= 2
    elif mutation == "origin_identity":
        episode["origin_context_epoch"] = 0
    elif mutation == "cache_unknown":
        del native["attributes"]["cached_tokens_host"]
    else:
        native["attributes"]["mamba_host_hit_slots"] = False
    with pytest.raises(ValueError):
        continuation_rows(episodes, indexed, native_rows, short_gap_ms=1000.)


@pytest.mark.parametrize("value", (0, -1, True, float("nan"), float("inf")))
def test_invalid_descriptive_boundary_is_rejected(value):
    with pytest.raises(ValueError):
        continuation_rows([], {}, [], short_gap_ms=value)


def test_empty_population_is_unknown_and_not_an_invented_zero_latency():
    result = summarize([])
    assert result["requests"] == 0
    assert result["successor_output_tokens"]["count"] == 0
    assert "p50" not in result["successor_output_tokens"]


def write_arm(path):
    requests, _, native = fixture()

    def event(kind, when, epoch=1, **attrs):
        return {
            "kind": kind, "ts_ms": when, "workflow_id": "w",
            "invocation_id": "i", "context_id": "ctx", "context_epoch": epoch,
            "attributes": attrs,
        }

    events = [
        event("workflow_start", 0.),
        event("llm_submit", 1., request_id="origin"),
        event("llm_result", 11., request_id="origin", tool_call_count=1),
        event("tool_start", 12., tool_run_id="run", tool_call_id="call"),
        event("tool_end", 20., tool_run_id="run", tool_call_id="call"),
        event("llm_submit", 25., 2, request_id="next"),
        event("llm_result", 61., 2, request_id="next", tool_call_count=0),
        event("workflow_end", 62., 2),
    ]
    for relative, rows in (
        ("replay/runtime_events.replay.jsonl", events),
        ("replay/native_requests.jsonl", requests),
        ("server/runtime_events.sglang.jsonl", [native]),
    ):
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def test_full_audit_uses_native_output_ids_and_seals_every_input(tmp_path):
    result, work = audit_arm(write_arm(tmp_path / "arm"), short_gap_ms=1000.)
    assert result["exact_request_count"] == 2
    assert result["short_return_identity_candidates"]["requests"] == 1
    assert len(result["input_jsonl_prefixes"]) == 3
    assert set(work) == {"origin", "next"}
    assert work["next"][1] == 2048


def test_cli_compares_stopping_work_without_relabeling_single_arm_evidence(
    tmp_path, monkeypatch,
):
    arm = write_arm(tmp_path / "arm")
    result = tmp_path / "one.json"
    monkeypatch.setattr("sys.argv", [
        "audit", "--arm", f"full={arm}", "--output", str(result),
    ])
    main()
    assert json.loads(result.read_text())["same_request_work_verified"] is False
    other = write_arm(tmp_path / "other")
    native_path = other / "replay/native_requests.jsonl"
    rows = [json.loads(line) for line in native_path.read_text().splitlines()]
    rows[1]["native_receipt"]["sampling_parameters"]["max_new_tokens"] += 1
    native_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr("sys.argv", [
        "audit", "--arm", f"full={arm}", "--arm", f"native={other}",
        "--output", str(tmp_path / "two.json"),
    ])
    with pytest.raises(ValueError, match="different exact request work or stopping"):
        main()
