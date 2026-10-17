from array import array
from io import StringIO
import json
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.checkpoint_path_probe import generation_prefix_compatibility
from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry


def path(prefixes):
    return {"status": "observed", "mamba_states": [
        {"node_id": index, "creation_time": float(index), "prefix_tokens": prefix,
         "role": "generated_output_tail"}
        for index, prefix in enumerate(prefixes, 1)
    ]}


@pytest.mark.parametrize("current,common,generated,expected", [
    ([1, 2, 3, 4, 5], 2, 2, ["exact_prefix_compatible", "exact_prefix_compatible"]),
    ([1, 2, 8, 9], 2, 0, ["token_prefix_diverges", "token_prefix_diverges"]),
    ([1, 2, 3, 9], 2, 1, ["exact_prefix_compatible", "token_prefix_diverges"]),
    ([1, 8, 9, 9], 1, 0, ["token_prefix_diverges", "token_prefix_diverges"]),
    ([1], 1, 0, ["new_input_ends_before_checkpoint", "new_input_ends_before_checkpoint"]),
    ([1, 2, 3], 2, 1, ["exact_prefix_compatible", "new_input_ends_before_checkpoint"]),
])
def test_exact_generated_prefix_and_checkpoint_boundary(current, common, generated, expected):
    result = generation_prefix_compatibility(
        array("q", [1, 2]), array("q", [3, 4]), array("q", current), common,
        NativeReactiveTelemetry._common_array_prefix, path([3, 4]),
    )
    assert result["common_generated_output_prefix_tokens"] == generated
    assert result["common_prior_transcript_prefix_tokens"] == common + generated
    assert [row["prefix_compatibility"] for row in result["checkpoint_compatibility"]] == expected


def test_unknown_path_and_out_of_transcript_checkpoint_are_not_imputed_misses():
    result = generation_prefix_compatibility(
        array("q", [1]), array("q", [2]), array("q", [1, 2]), 1,
        NativeReactiveTelemetry._common_array_prefix, {"status": "unavailable"},
    )
    assert result["checkpoint_path_observed"] is False
    assert result["checkpoint_compatibility"] == []
    result = generation_prefix_compatibility(
        array("q", [1]), array("q", [2]), array("q", [1, 2]), 1,
        NativeReactiveTelemetry._common_array_prefix, path([3]),
    )
    assert result["checkpoint_compatibility"][0]["prefix_compatibility"] == "outside_observed_transcript"


def record(kind, request, ids, **extra):
    return {
        "_internal_event": kind, "ts_ms": 1, "workflow_id": "workflow",
        "invocation_id": "agent", "context_id": "context", "context_epoch": 1,
        "session_id": "session", "session_generation": 1, "request_id": request,
        "input_ids": ids, "extra_key": None, "cache_salt": None,
        "cached_tokens_device": 0, "cached_tokens_host": 0, "mamba_host_hit_slots": 0,
        **extra,
    }


@pytest.mark.parametrize("change", ["same", "session", "generation", "namespace", "context", "epoch"])
def test_history_scope_prevents_cross_session_or_compaction_comparisons(tmp_path, monkeypatch, change):
    monkeypatch.setenv("BELIEFKV_CHECKPOINT_PATH_SHADOW", "1")
    telemetry = NativeReactiveTelemetry(tmp_path / "server")
    telemetry.close()
    output = StringIO()
    telemetry._write_context_prefix(record(
        "served_context_prefix", "prior", [1, 2],
        _generation_output_ids=[3, 4], _checkpoint_path_evidence=path([3, 4]),
    ), output)
    current = record("context_prefix_probe", "next", [1, 2, 3, 9])
    if change == "session":
        current["session_id"] = "fresh"
    elif change == "generation":
        current["session_generation"] = 2
    elif change == "namespace":
        current["cache_salt"] = "different"
    elif change == "context":
        current["context_id"] = "other"
    elif change == "epoch":
        current["context_epoch"] = 0
    telemetry._write_context_prefix(current, output)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    continuations = [row for row in rows if row["event"] == "generation_continuation_prefix_probe"]
    assert len(continuations) == int(change == "same")
    assert "token_ids" not in output.getvalue()
    if change == "same":
        assert continuations[0]["common_generated_output_prefix_tokens"] == 1
        assert telemetry._context_prefix_counts["previously_served_common_prefix_tokens"] == 2


@pytest.mark.parametrize("enabled", [False, True])
def test_only_enabled_served_output_is_snapshotted_on_scheduler(tmp_path, monkeypatch, enabled):
    monkeypatch.setenv("BELIEFKV_CHECKPOINT_PATH_SHADOW", str(int(enabled)))
    telemetry = NativeReactiveTelemetry(tmp_path / "server")
    telemetry.close()
    captured = []
    telemetry._emit = lambda stream, payload: captured.append(payload)
    req = NS(rid="request", origin_input_ids=[1], output_ids=[2])
    telemetry._queue_context_prefix(req, {
        "workflow_id": "workflow", "invocation_id": "agent",
        "context_id": "context", "context_epoch": 0,
    }, served=True, checkpoint_path=path([2]))
    req.output_ids.append(3)
    assert ("_generation_output_ids" in captured[0]) is enabled
    if enabled:
        assert captured[0]["_generation_output_ids"] == (2,)


def test_generation_history_obeys_existing_capacity_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("BELIEFKV_CHECKPOINT_PATH_SHADOW", "1")
    telemetry = NativeReactiveTelemetry(tmp_path / "server")
    telemetry.close()
    telemetry._context_prefix_history_limit = 1
    output = StringIO()
    for context in ("first", "second"):
        telemetry._write_context_prefix(record(
            "served_context_prefix", context, [1],
            context_id=context, _generation_output_ids=[2], _checkpoint_path_evidence=path([2]),
        ), output)
    assert len(telemetry._context_prefix_history) == 1
    assert telemetry._context_prefix_counts["history_capacity_expirations"] == 1
