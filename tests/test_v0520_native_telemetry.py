from __future__ import annotations

import json
from io import StringIO
import time
from array import array
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from numpy import float64

import beliefkv.runtime.v0520_native_telemetry as telemetry_module
from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry


class _Mode:
    def __init__(self, phase: str, *, target_verify: bool = False):
        self.phase = phase
        self.target_verify = target_verify

    def is_extend(self) -> bool:
        return self.phase == "prefill"

    def is_decode(self) -> bool:
        return self.phase == "decode"

    def is_target_verify(self) -> bool:
        return self.target_verify

    def is_draft_extend_v2(self) -> bool:
        return self.phase == "draft_extend_v2"

def test_context_prefix_loss_excludes_new_input_and_fresh_sessions(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "prefix")
    audit.close()
    output = StringIO()
    common = {
        "ts_ms": 1., "workflow_id": "w", "invocation_id": "i",
        "context_id": "ctx", "context_epoch": 0, "request_id": "old",
        "session_id": "s", "session_generation": 1,
        "extra_key": None, "cache_salt": None, "cached_tokens_device": 0,
        "cached_tokens_host": 0, "mamba_host_hit_slots": 0,
    }
    audit._write_context_prefix({
        **common, "_internal_event": "served_context_prefix",
        "input_ids": [1, 2, 3, 4, 5, 6],
    }, output)
    audit._write_context_prefix({
        **common, "request_id": "next", "context_epoch": 1,
        "_internal_event": "context_prefix_probe", "input_ids": [1, 2, 3, 4, 5, 6, 7, 8],
        "cached_tokens_device": 2, "cached_tokens_host": 1,
    }, output)
    [result] = [json.loads(line) for line in output.getvalue().splitlines()]
    assert result["common_previously_served_input_tokens"] == 6
    assert result["previously_served_prefix_recompute_proxy_tokens"] == 3
    assert result["new_or_changed_prompt_tokens"] == 2
    audit._write_context_prefix({
        **common, "session_id": "fresh", "request_id": "compact",
        "_internal_event": "context_prefix_probe", "input_ids": [1, 2, 3, 4, 5, 6],
    }, output)
    assert audit._context_prefix_counts["without_prior_served_prompt"] == 1


@pytest.mark.parametrize("mismatch", (None, 0, 1, 1023, 1024, 2047, 2048, 4095))
def test_context_prefix_loss_preserves_exact_divergence_across_ranges(tmp_path, mismatch):
    audit = NativeReactiveTelemetry(tmp_path / "prefix")
    audit.close()
    prior = [(1 << 35) + index for index in range(4096)]
    current = prior + [7, 8]
    if mismatch is not None:
        current[mismatch] = -1
    common = len(prior) if mismatch is None else mismatch
    base = {
        "ts_ms": 1., "workflow_id": "w", "invocation_id": "i",
        "context_id": "ctx", "context_epoch": 0, "request_id": "old",
        "session_id": "s", "session_generation": 1,
        "cached_tokens_device": 1000, "cached_tokens_host": 100,
        "mamba_host_hit_slots": 0,
    }
    output = StringIO()
    audit._write_context_prefix({
        **base, "_internal_event": "served_context_prefix", "input_ids": prior,
    }, output)
    audit._write_context_prefix({
        **base, "_internal_event": "context_prefix_probe", "input_ids": current,
        "request_id": "next", "context_epoch": 1,
    }, output)
    [result] = [json.loads(line) for line in output.getvalue().splitlines()]
    assert result["common_previously_served_input_tokens"] == common
    assert result["previously_served_prefix_recompute_proxy_tokens"] == max(0, common - 1100)
    assert result["new_or_changed_prompt_tokens"] == len(current) - common
    assert audit._context_prefix_counts["previously_served_common_prefix_tokens"] == common


@pytest.mark.parametrize("input_ids", (
    [1, 2, 3], ("1", "2", "3"), [1., 2., 3.], array("q", [1, 2, 3]),
))
def test_served_prefix_is_an_independent_normalized_snapshot(tmp_path, input_ids):
    audit = NativeReactiveTelemetry(tmp_path / "prefix")
    audit.close()
    base = {
        "ts_ms": 1., "workflow_id": "w", "invocation_id": "i",
        "context_id": "ctx", "context_epoch": 0, "request_id": "old",
        "session_id": "s", "session_generation": 1,
        "cached_tokens_device": 0, "cached_tokens_host": 0,
        "mamba_host_hit_slots": 0,
    }
    output = StringIO()
    audit._write_context_prefix({
        **base, "_internal_event": "served_context_prefix", "input_ids": input_ids,
    }, output)
    if isinstance(input_ids, (list, array)):
        input_ids[0] = 9
    audit._write_context_prefix({
        **base, "_internal_event": "context_prefix_probe", "input_ids": [1, 2],
        "request_id": "next", "context_epoch": 1,
    }, output)
    [result] = [json.loads(line) for line in output.getvalue().splitlines()]
    assert result["common_previously_served_input_tokens"] == 2
    assert result["previously_served_prefix_recompute_proxy_tokens"] == 2
    assert result["new_or_changed_prompt_tokens"] == 0


@pytest.mark.parametrize("input_ids", (
    [1, 2, 3], ("1", "2", "3"), [1., 2., 3.], array("q", [1, 2, 3]),
))
def test_block_reaccess_keeps_normalized_prefix_identity(tmp_path, input_ids):
    audit = NativeReactiveTelemetry(tmp_path / "blocks")
    audit.close()
    audit._host_pool_geometry["full"] = {"bytes_per_unit": 2}
    audit._write_block_eviction({
        "key_path": (array("q", [1, 2]),), "pool": "full",
        "node_id": 1, "units": 2, "ts_ms": 1.,
        "extra_key": None, "cache_salt": None,
    }, StringIO())
    output = StringIO()
    audit._write_block_request_probe({
        "input_ids": input_ids, "ts_ms": 2.,
        "extra_key": None, "cache_salt": None,
        "cached_tokens_device": 1, "cached_tokens_host": 1,
    }, output)
    result, summary = [json.loads(line) for line in output.getvalue().splitlines()]
    assert result["outcome"] == "full_mixed_tier_hit"
    assert summary["matched_evicted_blocks"] == 1
    assert audit._block_attribution_reused_units["full"] == 2
    assert not audit._pending_block_evictions


def test_block_probe_covers_more_than_the_old_1024_length_limit(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "blocks")
    audit.close()
    audit._host_pool_geometry["full"] = {"bytes_per_unit": 2}
    output = StringIO()
    for length in range(1, 1030):
        audit._write_block_eviction({
            "key_path": (array("q", range(length)),), "pool": "full",
            "node_id": length, "units": 1, "ts_ms": 1.,
            "extra_key": None, "cache_salt": None,
        }, output)
    output = StringIO()
    audit._write_block_request_probe({
        "input_ids": array("q", range(1030)), "ts_ms": 2.,
        "extra_key": None, "cache_salt": None,
        "cached_tokens_device": 1030, "cached_tokens_host": 0,
    }, output)
    assert audit._block_attribution_probe_overflow == 0
    assert audit._block_attribution_counts["full_device_hit"] == 1029


@pytest.mark.parametrize("length", (1, 2, 31, 32, 33, 63, 64, 65))
def test_eviction_routes_keep_exact_prefix_matches_across_power_boundaries(tmp_path, length):
    audit = NativeReactiveTelemetry(tmp_path / "blocks")
    audit.close()
    tokens = array("q", range(length))
    audit._write_block_eviction({
        "key_path": (tokens,), "pool": "full", "node_id": 1, "units": 1,
        "ts_ms": 1., "extra_key": "key", "cache_salt": "salt",
    }, StringIO())
    output = StringIO()
    audit._write_block_request_probe({
        "input_ids": [*tokens, 100], "ts_ms": 2., "extra_key": "key", "cache_salt": "salt",
        "cached_tokens_device": length, "cached_tokens_host": 0,
    }, output)
    result, summary = [json.loads(line) for line in output.getvalue().splitlines()]
    assert result["prefix_tokens"] == length
    assert result["outcome"] == "full_device_hit"
    assert summary["candidate_prefix_lengths"] == 1
    assert summary["matched_evicted_blocks"] == 1
    assert not audit._block_eviction_routes
    assert not audit._block_eviction_route_lengths


def test_eviction_routes_retain_same_route_length_and_cross_pool_references(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "blocks")
    audit.close()
    first = array("q", range(40))
    second = array("q", first)
    second[35] = -1
    for index, (tokens, pool) in enumerate(
        ((first, "full"), (first, "mamba"), (second, "full")), 1,
    ):
        audit._write_block_eviction({
            "key_path": (tokens,), "pool": pool, "node_id": index, "units": 1,
            "ts_ms": 1., "extra_key": None, "cache_salt": None,
        }, StringIO())
    assert len(audit._block_eviction_route_lengths) == 1
    assert list(audit._block_eviction_route_lengths.values())[0][40] == 2
    record = {
        "input_ids": first, "ts_ms": 2., "extra_key": None, "cache_salt": None,
        "cached_tokens_device": 40, "cached_tokens_host": 0,
    }
    output = StringIO()
    audit._write_block_request_probe(record, output)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert {row["pool"] for row in rows[:-1]} == {"full", "mamba"}
    assert rows[-1]["matched_evicted_blocks"] == 2
    assert len(audit._pending_block_evictions) == 1
    assert list(audit._block_eviction_route_lengths.values())[0][40] == 1
    audit._write_block_request_probe({**record, "input_ids": second}, StringIO())
    assert not audit._pending_block_evictions
    assert not audit._block_eviction_routes
    assert not audit._block_eviction_route_lengths


def test_many_eviction_routes_use_exact_terminals_after_matching_boundaries(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "blocks")
    audit.close()
    first = array("q", range(128))
    second = array("q", first)
    second[40] = -1
    for length in range(1, 129):
        audit._write_block_eviction({
            "key_path": (first[:length],), "pool": "full", "node_id": length, "units": 1,
            "ts_ms": 1., "extra_key": None, "cache_salt": None,
        }, StringIO())
    output = StringIO()
    audit._write_block_request_probe({
        "input_ids": second, "ts_ms": 2., "extra_key": None, "cache_salt": None,
        "cached_tokens_device": 128, "cached_tokens_host": 0,
    }, output)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [row["prefix_tokens"] for row in rows[:-1]] == list(range(1, 41))
    assert rows[-1]["candidate_prefix_lengths"] == 128
    assert rows[-1]["matched_evicted_blocks"] == 40
    assert len(audit._pending_block_evictions) == 88
    audit._write_block_request_probe({
        "input_ids": first, "ts_ms": 3., "extra_key": None, "cache_salt": None,
        "cached_tokens_device": 128, "cached_tokens_host": 0,
    }, StringIO())
    assert not audit._pending_block_evictions
    assert not audit._block_eviction_routes
    assert not audit._block_eviction_route_lengths


def test_eviction_route_expiry_removes_only_the_expired_prefix(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "blocks")
    audit.close()
    audit._block_eviction_index_limit = 2
    for branch in range(3):
        audit._write_block_eviction({
            "key_path": (array("q", [branch, 2, 3]),), "pool": "full",
            "node_id": branch, "units": 1, "ts_ms": 1.,
            "extra_key": None, "cache_salt": None,
        }, StringIO())
    assert len(audit._pending_block_evictions) == 2
    assert len(audit._block_eviction_routes) == 2
    assert len(audit._block_eviction_route_lengths) == 2
    record = {
        "input_ids": [0, 2, 3, 4], "ts_ms": 2., "cached_tokens_device": 4,
        "cached_tokens_host": 0, "extra_key": None, "cache_salt": None,
    }
    output = StringIO()
    audit._write_block_request_probe(record, output)
    assert output.getvalue() == ""
    assert audit._block_attribution_counts["expired_without_reaccess"] == 1
    audit._write_block_request_probe({**record, "input_ids": [1, 2, 3, 4]}, output)
    assert len(audit._pending_block_evictions) == 1
    assert len(audit._block_eviction_routes) == 1
    assert len(audit._block_eviction_route_lengths) == 1


def test_pending_status_tracks_replacements_reaccess_and_capacity_expiry(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "blocks")
    audit.close()
    audit._block_eviction_index_limit = 3
    status_path = tmp_path / "blocks/native_telemetry_status.json"

    def evict(tokens, pool, units):
        audit._write_block_eviction({
            "key_path": (array("q", tokens),), "pool": pool, "node_id": 1,
            "units": units, "ts_ms": 1., "extra_key": None, "cache_salt": None,
        }, StringIO())

    def check(blocks, units):
        audit._write_status()
        result = json.loads(status_path.read_text())["host_block_eviction_attribution"]
        assert result["pending_blocks_by_pool"] == blocks
        assert result["pending_latest_evicted_units_by_pool"] == units

    evict([1, 2, 3], "full", 3)
    check({"full": 1}, {"full": 3})
    evict([1, 2, 3], "full", 1)
    check({"full": 1}, {"full": 1})
    evict([1, 2, 3], "mamba", 1)
    evict([9, 2, 3], "full", 2)
    check({"full": 2, "mamba": 1}, {"full": 3, "mamba": 1})
    evict([8, 2, 3], "full", 3)
    check({"full": 2, "mamba": 1}, {"full": 5, "mamba": 1})
    for tokens, blocks, units in (
        ([1, 2, 3, 4], {"full": 2}, {"full": 5}),
        ([9, 2, 3, 4], {"full": 1}, {"full": 3}),
        ([8, 2, 3, 4], {}, {}),
    ):
        audit._write_block_request_probe({
            "input_ids": tokens, "ts_ms": 2., "extra_key": None, "cache_salt": None,
            "cached_tokens_device": 4, "cached_tokens_host": 0,
        }, StringIO())
        check(blocks, units)


def test_status_publication_does_not_traverse_pending_evictions(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "blocks")
    audit.close()
    audit._write_block_eviction({
        "key_path": (array("q", [1, 2, 3]),), "pool": "full", "node_id": 1,
        "units": 3, "ts_ms": 1., "extra_key": None, "cache_salt": None,
    }, StringIO())

    class NoScan(dict):
        def items(self):
            raise AssertionError("status publication scanned pending records")

    audit._pending_block_evictions = NoScan(audit._pending_block_evictions)
    audit._write_status()
    result = json.loads(
        (tmp_path / "blocks/native_telemetry_status.json").read_text()
    )["host_block_eviction_attribution"]
    assert result["pending_blocks_by_pool"] == {"full": 1}
    assert result["pending_latest_evicted_units_by_pool"] == {"full": 3}


def test_matching_namespace_preserves_other_namespace_pending_indexes(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "blocks")
    audit.close()
    for extra_key in ("first", "second"):
        for length in range(1, 41):
            audit._write_block_eviction({
                "key_path": (array("q", range(length)),), "pool": "full",
                "node_id": length, "units": 1, "ts_ms": 1.,
                "extra_key": extra_key, "cache_salt": None,
            }, StringIO())
    probe = {
        "input_ids": array("q", range(40)), "ts_ms": 2., "cache_salt": None,
        "cached_tokens_device": 40, "cached_tokens_host": 0,
    }
    audit._write_block_request_probe({**probe, "extra_key": "first"}, StringIO())
    assert len(audit._pending_block_evictions) == 40
    assert len(audit._block_eviction_index) == 40
    assert len(audit._block_eviction_routes) == 40
    assert len(audit._block_eviction_lengths) == 1
    audit._write_block_request_probe({**probe, "extra_key": "second"}, StringIO())
    assert audit._block_attribution_counts["full_device_hit"] == 80
    assert not audit._pending_block_evictions
    assert not audit._block_eviction_index
    assert not audit._block_eviction_routes
    assert not audit._block_eviction_route_lengths
    assert not audit._block_eviction_lengths
    assert not audit._block_eviction_sorted_lengths
    assert not audit._block_eviction_length_refs


def test_eviction_routes_preserve_existing_overflow_and_candidate_count(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "blocks")
    audit.close()
    audit._max_prefix_lengths_per_probe = 2
    for branch, tokens in enumerate(([0, 1, 2], [9, 1, 2, 3], [8, 1, 2, 3, 4])):
        audit._write_block_eviction({
            "key_path": (array("q", tokens),), "pool": "full", "node_id": branch,
            "units": 1, "ts_ms": 1., "extra_key": None, "cache_salt": None,
        }, StringIO())
    output = StringIO()
    audit._write_block_request_probe({
        "input_ids": list(range(10)), "ts_ms": 2., "cached_tokens_device": 10,
        "cached_tokens_host": 0, "extra_key": None, "cache_salt": None,
    }, output)
    [summary] = [json.loads(line) for line in output.getvalue().splitlines()]
    assert summary["candidate_prefix_lengths"] == 2
    assert summary["omitted_prefix_lengths"] == 1
    assert summary["matched_evicted_blocks"] == 0
    assert audit._block_attribution_probe_overflow == 1
    assert len(audit._pending_block_evictions) == 3


def test_short_overflow_probe_selects_recent_lengths_then_tracks_remaining_matches(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "blocks")
    audit.close()
    audit._max_prefix_lengths_per_probe = 2
    for length in (5, 2, 4, 1, 3):
        audit._write_block_eviction({
            "key_path": (array("q", range(length)),), "pool": "full",
            "node_id": length, "units": 1, "ts_ms": 1.,
            "extra_key": None, "cache_salt": None,
        }, StringIO())
    output = StringIO()
    record = {
        "input_ids": list(range(4)), "ts_ms": 2.,
        "cached_tokens_device": 4, "cached_tokens_host": 0,
        "extra_key": None, "cache_salt": None,
    }
    audit._write_block_request_probe(record, output)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [row["prefix_tokens"] for row in rows[:-1]] == [1, 3]
    assert rows[-1]["omitted_prefix_lengths"] == 2
    output = StringIO()
    audit._write_block_request_probe({**record, "input_ids": list(range(3))}, output)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [row["prefix_tokens"] for row in rows[:-1]] == [2]
    assert rows[-1]["omitted_prefix_lengths"] == 0
    audit._write_block_request_probe(
        {**record, "input_ids": list(range(5)), "cached_tokens_device": 5}, StringIO(),
    )
    assert audit._block_attribution_counts["full_device_hit"] == 5
    assert not audit._pending_block_evictions
    assert not audit._block_eviction_sorted_lengths


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


def test_targeted_pair_first_crossing_has_decode_ordinal(tmp_path: Path) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "pair")
    emitted: list[dict] = []
    audit._emit = lambda stream, record: emitted.append(record)
    sample = {
        "request_id": "r1", "workflow_id": "w1", "invocation_id": "i1",
        "context_id": "c1", "context_epoch": 0,
        "output_tokens_before": 1,
    }
    req = SimpleNamespace(
        rid="r1", output_ids=[90, 91], finished=lambda: False,
        logprob=SimpleNamespace(
            token_ids_logprob=[10, 11],
            output_token_ids_logprobs_val=[[-3.1, -3.2]],
            output_token_ids_logprobs_idx=[[10, 11]],
            output_token_logprobs_idx=[91],
        ),
    )
    audit._observe_targeted_pair(req, sample, 1000., 1)
    req.output_ids.append(92)
    req.logprob.output_token_ids_logprobs_val.append([-2.9, -5.])
    req.logprob.output_token_ids_logprobs_idx.append([10, 11])
    req.logprob.output_token_logprobs_idx.append(92)
    audit._observe_targeted_pair(req, sample, 1010., 2)
    audit._observe_targeted_pair(req, sample, 1020., 3)
    crossing = [
        row for row in emitted
        if row["event"] == "targeted_pair_first_crossing"
        and row["threshold"] == .05
    ]
    assert len(crossing) == 1
    assert crossing[0]["output_token_ordinal"] == 3
    assert crossing[0]["ts_ms"] == 1010.
    assert crossing[0]["context_epoch"] == 0
    assert crossing[0]["sample_id"] == 2
    audit.close()


def test_targeted_pair_excludes_sampled_probe_and_invalid_alignment(
    tmp_path: Path,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "pair")
    emitted: list[dict] = []
    audit._emit = lambda stream, record: emitted.append(record)
    sample = {
        "request_id": "r1", "workflow_id": "w1", "invocation_id": "i1",
        "context_id": "c1", "context_epoch": 0,
        "output_tokens_before": 1,
    }
    req = SimpleNamespace(
        rid="r1", output_ids=[90, 10], finished=lambda: False,
        logprob=SimpleNamespace(
            token_ids_logprob=[10, 11],
            output_token_ids_logprobs_val=[[-.1, -1.]],
            output_token_ids_logprobs_idx=[[10, 11]],
            output_token_logprobs_idx=[10],
        ),
    )
    audit._observe_targeted_pair(req, sample, 1000., 1)
    assert not emitted
    req.output_ids.append(92)
    req.logprob.output_token_ids_logprobs_val.append([-.1, -1.])
    req.logprob.output_token_ids_logprobs_idx.append([11, 10])
    req.logprob.output_token_logprobs_idx.append(92)
    audit._observe_targeted_pair(req, sample, 1010., 2)
    assert [row["event"] for row in emitted] == ["targeted_pair_ordinal_invalid"]
    audit._observe_targeted_pair(req, sample, 1020., 3)
    assert len(emitted) == 1
    audit.close()


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


@pytest.mark.parametrize("identical", (False, True))
def test_host_hit_path_records_identity_without_claiming_component_reuse(
    tmp_path: Path, identical: bool,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "service")
    root = SimpleNamespace(id=0, creation_time=1, parent=None)
    ancestor = SimpleNamespace(id=52, creation_time=float64(286), parent=root)
    leaf = SimpleNamespace(id=4721, creation_time=300, parent=ancestor)
    lookup = Mock(side_effect={
        node.id: node for node in (root, ancestor, leaf)
    }.__getitem__)
    audit._cache = SimpleNamespace(tree_core=SimpleNamespace(node_by_id=lookup))
    req = SimpleNamespace(
        rid="request", beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": "parent",
            "context_id": "ctx", "context_epoch": 9,
        },
        last_node=leaf.id, last_host_node=leaf.id if identical else ancestor.id,
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
    assert path["last_host_path"] == (
        path["last_device_path"] if identical else [[0, 1], [52, 286]]
    )
    assert path["best_match_path"] == path["last_device_path"]
    assert lookup.call_count == (1 if identical else 2)
    assert path["evidence"].endswith("not_proof_of_component_reuse")


def test_match_path_reuse_does_not_survive_the_next_observation():
    audit = NativeReactiveTelemetry.__new__(NativeReactiveTelemetry)
    node = SimpleNamespace(id=7, creation_time=1, parent=None)
    audit._cache = SimpleNamespace(tree_core=SimpleNamespace(node_by_id=lambda _: node))
    assert audit._match_paths((7, 7, 7)) == ([[7, 1]],) * 3
    node.creation_time = 2
    assert audit._match_paths((7, 7, 7)) == ([[7, 2]],) * 3


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


def test_status_refreshes_while_writer_queue_never_reaches_idle(tmp_path, monkeypatch):
    class BusyQueue(Queue):
        def get(self, block=True, timeout=None):
            item = super().get(block=block, timeout=None)
            time.sleep(0.002)
            return item

    monkeypatch.setattr(telemetry_module, "Queue", BusyQueue)
    audit = NativeReactiveTelemetry(tmp_path / "busy")
    try:
        for sequence in range(700):
            audit._emit("audit", {"event": "busy", "sequence": sequence})
        path = tmp_path / "busy/native_telemetry_status.json"
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert path.exists()
        status = json.loads(path.read_text())
        assert status["record_counts"]["audit"] > 0
        assert status["snapshot_ts_ms"] > 0
        assert status["writer_queue_depth"] > 0
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


@pytest.mark.parametrize("acknowledged", (False, True))
def test_prefetch_service_before_ack_is_credited_only_after_verified_ack(tmp_path, acknowledged):
    audit = NativeReactiveTelemetry(tmp_path / "early-service")
    root = SimpleNamespace(id=0, parent=None)
    node = SimpleNamespace(
        id=11, creation_time=7, parent=root, key=array("q", range(10)),
        component_data=(SimpleNamespace(value=object()),),
    )
    audit._cache = SimpleNamespace(tree_core=SimpleNamespace(node_by_id=lambda _: node))
    action = SimpleNamespace(
        command_id="early", action="PREFETCH_GPU", source="execution_handoff",
        context_id="ctx", context_epoch=2, node_ids=(11,),
        pool_bytes=(("kv", 2048),), num_bytes=2048,
    )
    audit.on_prefetch_issued(action)
    request = SimpleNamespace(
        rid="parent", last_node=11, origin_input_ids=list(range(20)),
        prefix_indices=list(range(12)), cached_tokens_device=12, cached_tokens_host=0,
    )
    audit._record_prefetch_first_service(request, {"context_id": "ctx", "context_epoch": 2})
    assert len(audit._prefetch_use_before_ack["early"]) == 1
    if acknowledged:
        audit.on_verified_action_ack(action)
    audit.close()
    used = _read(tmp_path / "early-service/physical_action_use.jsonl")
    if acknowledged:
        assert len(used) == 1
        assert used[0]["full_node_reused"] is True
        assert used[0]["first_service_before_ack"] is True
        assert used[0]["ack_ts_ms"] >= used[0]["first_service_ts_ms"]
    else:
        assert used == []
        assert any(
            item["event"] == "unacknowledged_prefetch_use_discarded"
            for item in _read(tmp_path / "early-service/runtime_audit.jsonl")
        )


def test_failed_prefetch_discards_provisional_reuse_before_shutdown(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "discarded-service")
    action = SimpleNamespace(
        command_id="expired", action="PREFETCH_GPU", source="execution_handoff",
        context_id="ctx", context_epoch=2, node_ids=(),
        pool_bytes=(("kv", 2048),), num_bytes=2048,
    )
    audit.on_prefetch_issued(action)
    audit._emit_prefetch_use({
        "event": "beliefkv_prefetch_first_service", "command_id": action.command_id,
        "first_service_ts_ms": 1.,
    })
    audit.on_prefetch_discarded(action.command_id, "physical_expired")
    audit._emit_prefetch_use({
        "event": "beliefkv_prefetch_mamba_forward_completed",
        "command_id": action.command_id, "ack_ts_ms": None,
    })
    assert not audit._unacked_prefetch_use
    assert not audit._pending_prefetch_use
    assert not audit._prefetch_use_before_ack
    audit.close()
    assert _read(tmp_path / "discarded-service/physical_action_use.jsonl") == []
    assert any(
        item.get("reason") == "physical_expired"
        for item in _read(tmp_path / "discarded-service/runtime_audit.jsonl")
    )


@pytest.mark.parametrize("changed_prefix", [False, True])
def test_prefetch_diagnostic_separates_input_divergence_from_mamba_boundary(
    tmp_path: Path, changed_prefix: bool,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "restore")
    root = SimpleNamespace(id=0, creation_time=0, parent=None)
    loaded = SimpleNamespace(
        id=11, creation_time=7, parent=root, key=array("q", range(8)),
        component_data=(SimpleNamespace(value=object()),),
    )
    audit._cache = SimpleNamespace(
        tree_core=SimpleNamespace(node_by_id={0: root, 11: loaded}.__getitem__),
    )
    audit.on_verified_action_ack(SimpleNamespace(
        command_id="restore", action="PREFETCH_GPU",
        context_id="ctx", context_epoch=2, node_ids=(11,),
        pool_bytes=(("kv", 80),), num_bytes=80,
    ))
    # The ACK snapshot must survive subsequent radix topology/key changes.
    loaded.key[0] = 99
    inputs = list(range(12))
    if changed_prefix:
        inputs[4] = 90
    request = SimpleNamespace(
        rid="next", last_node=0, best_match_node=0,
        origin_input_ids=inputs, prefix_indices=[], cached_tokens_device=0,
        cached_tokens_host=0, mamba_host_hit_length=0,
        beliefkv_full_kv_hit_length=8, mamba_branching_seqlen=8,
    )
    audit._record_prefetch_first_service(
        request, {"context_id": "ctx", "context_epoch": 3},
    )
    audit.close()
    use = _read(tmp_path / "restore/physical_action_use.jsonl")[0]
    target = use["target_prefixes"][0]
    assert target["common_input_prefix_tokens"] == (4 if changed_prefix else 8)
    assert target["complete_input_prefix"] is not changed_prefix
    assert use["native_full_kv_hit_length"] == 8
    assert use["native_mamba_branching_seqlen"] == 8


@pytest.mark.parametrize("native_array", [False, True])
def test_prefetch_prefix_evidence_reuse_handles_shorter_and_divergent_targets(tmp_path, native_array):
    audit = NativeReactiveTelemetry(tmp_path / "prefixes")
    targets = [
        list(range(8)),
        list(range(4)),
        [0, 1, 99, 3, 4],
        list(range(12)),
        list(range(5)),
        list(range(12)),
    ]
    for index, target in enumerate(targets):
        command = f"restore-{index}"
        action = SimpleNamespace(
            command_id=command, context_id="ctx", context_epoch=2,
            node_ids=(index,), pool_bytes=(),
        )
        audit._pending_prefetch_use[command] = (
            action, (), 100., {
                index: (array("q", target), "other" if index == 5 else "ns", None),
            },
        )
    inputs = list(range(12))
    inputs[10] = 90
    if native_array:
        inputs = array("q", inputs)
    audit._record_prefetch_first_service(SimpleNamespace(
        rid="next", origin_input_ids=inputs, prefix_indices=[],
        last_node=0, cached_tokens_device=0, extra_key="ns",
    ), {"context_id": "ctx", "context_epoch": 3})
    audit.close()
    records = _read(tmp_path / "prefixes/physical_action_use.jsonl")
    prefixes = [row["target_prefixes"][0] for row in records]
    assert [row["common_input_prefix_tokens"] for row in prefixes] == [8, 4, 2, 10, 5, 10]
    assert [row["complete_input_prefix"] for row in prefixes] == [
        True, True, False, False, True, False,
    ]
    assert [row["same_cache_namespace"] for row in prefixes] == [
        True, True, True, True, True, False,
    ]


@pytest.mark.parametrize("tokens", (2047, 2048, 32768))
@pytest.mark.parametrize("mismatch", (None, 0, 1, 1023, -1))
@pytest.mark.parametrize("input_format", ("array", "list", "tuple"))
def test_prefetch_long_array_evidence_preserves_exact_token_boundary(
    tmp_path, tokens, mismatch, input_format,
):
    audit = NativeReactiveTelemetry(tmp_path / "prefixes")
    original = array("q", ((1 << 60) + index for index in range(tokens)))
    inputs = array("q", original)
    inputs.append(-1)
    if mismatch is not None:
        mismatch %= tokens
        inputs[mismatch] ^= 1 << 40
    if input_format == "list":
        inputs = list(inputs)
    elif input_format == "tuple":
        inputs = tuple(inputs)
    action = SimpleNamespace(
        command_id="restore", context_id="ctx", context_epoch=2,
        node_ids=(1,), pool_bytes=(),
    )
    audit._pending_prefetch_use["restore"] = (
        action, (), 100., {1: (original, None, None)},
    )
    audit._record_prefetch_first_service(SimpleNamespace(
        rid="next", origin_input_ids=inputs, prefix_indices=[],
        last_node=0, cached_tokens_device=0,
    ), {"context_id": "ctx", "context_epoch": 3})
    audit.close()
    [record] = _read(tmp_path / "prefixes/physical_action_use.jsonl")
    [prefix] = record["target_prefixes"]
    assert prefix["common_input_prefix_tokens"] == (
        tokens if mismatch is None else mismatch
    )
    assert prefix["complete_input_prefix"] is (mismatch is None)
    assert prefix["same_cache_namespace"] is True


@pytest.mark.parametrize("changed", (2.5, 1 << 80))
def test_prefetch_list_conversion_failure_preserves_original_numeric_boundary(tmp_path, changed):
    audit = NativeReactiveTelemetry(tmp_path / "prefixes")
    action = SimpleNamespace(
        command_id="restore", context_id="ctx", context_epoch=2,
        node_ids=(1,), pool_bytes=(),
    )
    audit._pending_prefetch_use["restore"] = (
        action, (), 100., {1: (array("q", range(4)), None, None)},
    )
    audit._record_prefetch_first_service(SimpleNamespace(
        rid="next", origin_input_ids=[0, 1, changed, 3],
        prefix_indices=[], last_node=0, cached_tokens_device=0,
    ), {"context_id": "ctx", "context_epoch": 3})
    audit.close()
    [record] = _read(tmp_path / "prefixes/physical_action_use.jsonl")
    [prefix] = record["target_prefixes"]
    assert prefix["common_input_prefix_tokens"] == 2
    assert prefix["complete_input_prefix"] is False


def test_prefetch_diagnostic_does_not_convert_new_input_beyond_ack_prefix(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "prefixes")
    action = SimpleNamespace(
        command_id="restore", context_id="ctx", context_epoch=2,
        node_ids=(1,), pool_bytes=(),
    )
    audit._pending_prefetch_use["restore"] = (
        action, (), 100., {1: (array("q", range(2048)), None, None)},
    )
    class Input(list):
        def __getitem__(self, item):
            if isinstance(item, slice):
                assert item.stop <= 2048
            return super().__getitem__(item)
    inputs = Input([*range(2048), 1 << 80])
    audit._record_prefetch_first_service(SimpleNamespace(
        rid="next", origin_input_ids=inputs, prefix_indices=[],
        last_node=0, cached_tokens_device=0,
    ), {"context_id": "ctx", "context_epoch": 3})
    audit.close()
    [record] = _read(tmp_path / "prefixes/physical_action_use.jsonl")
    [prefix] = record["target_prefixes"]
    assert prefix["common_input_prefix_tokens"] == 2048
    assert prefix["complete_input_prefix"] is True
    assert inputs[-1] == 1 << 80


def test_sampled_launch_telemetry_is_nested_inside_forward_submission(tmp_path, monkeypatch):
    from beliefkv.runtime import prefill_turnover_probe as probe_module
    clock = [0, 0]
    monkeypatch.setattr(probe_module, "_clock", lambda: tuple(clock))
    audit = NativeReactiveTelemetry(tmp_path / "launch")
    records = []
    probe = audit.prefill_turnover_probe
    probe.every = 1
    probe.emit = lambda _stream, record: records.append(record)
    audit._identity = lambda _req: {
        "workflow_id": "w", "invocation_id": "i", "context_id": "ctx", "context_epoch": 0,
    }
    def first_service(*_args):
        clock[0] += 8_000_000
        clock[1] += 3_000_000
        return []
    audit._record_prefetch_first_service = first_service
    req = SimpleNamespace(
        rid="r", origin_input_ids=list(range(6)), output_ids=[],
        sampling_params=SimpleNamespace(max_new_tokens=32),
    )
    batch = SimpleNamespace(
        forward_mode=_Mode("prefill"), forward_iter=0, launch_ts=0.,
        reqs=[req], prefix_lens=[4], extend_lens=[2], extend_num_tokens=2,
    )
    probe.on_selected(batch, probe.begin_selection(), loop="normal")
    forward = probe.begin_forward(batch, None, None)
    batch.forward_iter = 1
    audit.on_launch(batch)
    probe.end_forward(forward)
    probe.on_processed(
        probe.begin_phase(batch, "result_processing"),
        SimpleNamespace(can_run_cuda_graph=True),
    )
    audit.close()
    [record] = records
    assert record["phases"]["run_batch"]["wall_ms"] == 8
    assert record["phases"]["launch_telemetry"]["wall_ms"] == 8
    assert record["phases"]["launch_telemetry"]["thread_cpu_ms"] == 3


@pytest.mark.parametrize("typecode", ("q", "i", "d"))
def test_prefetch_array_evidence_keeps_numeric_comparison_and_short_input(tmp_path, typecode):
    audit = NativeReactiveTelemetry(tmp_path / "prefixes")
    action = SimpleNamespace(
        command_id="restore", context_id="ctx", context_epoch=2,
        node_ids=(1,), pool_bytes=(),
    )
    audit._pending_prefetch_use["restore"] = (
        action, (), 100., {1: (array("q", range(4096)), None, None)},
    )
    audit._record_prefetch_first_service(SimpleNamespace(
        rid="next", origin_input_ids=array(typecode, range(3072)),
        prefix_indices=[], last_node=0, cached_tokens_device=0,
    ), {"context_id": "ctx", "context_epoch": 3})
    audit.close()
    [record] = _read(tmp_path / "prefixes/physical_action_use.jsonl")
    [prefix] = record["target_prefixes"]
    assert prefix["common_input_prefix_tokens"] == 3072
    assert prefix["complete_input_prefix"] is False


def test_prefetch_reuse_observes_full_ancestor_beyond_256_nodes(tmp_path):
    audit = NativeReactiveTelemetry(tmp_path / "deep")
    nodes = {0: SimpleNamespace(id=0, creation_time=0, parent=None)}
    for index in range(1, 301):
        nodes[index] = SimpleNamespace(
            id=index, creation_time=index, parent=nodes[index - 1],
            key=array("q", (index - 1,)),
            component_data=(SimpleNamespace(value=object()),),
        )
    audit._cache = SimpleNamespace(
        tree_core=SimpleNamespace(node_by_id=nodes.__getitem__),
    )
    audit.on_verified_action_ack(SimpleNamespace(
        command_id="restore", action="PREFETCH_GPU", context_id="ctx",
        context_epoch=2, node_ids=(1,), pool_bytes=(("kv", 8),), num_bytes=8,
    ))
    audit._record_prefetch_first_service(SimpleNamespace(
        rid="next", last_node=300, origin_input_ids=list(range(301)),
        prefix_indices=list(range(300)), cached_tokens_device=300,
    ), {"context_id": "ctx", "context_epoch": 3})
    audit.close()
    [record] = _read(tmp_path / "deep/physical_action_use.jsonl")
    assert record["matched_node_ids"] == [1]
    assert record["reused_full_node_ids"] == [1]
    assert record["full_node_reused"] is True


@pytest.mark.parametrize("mismatch", [
    None, "other_node", "replaced_state", "no_cow", "multi_request",
    "no_completed_forward", "speculative_verify",
    "multi_witness", "wrong_request", "wrong_source", "wrong_destination",
    "duplicate_witness", "empty_witness", "witness_uncompleted",
])
def test_mamba_prefetch_requires_single_request_cow_and_completed_forward(
    tmp_path: Path, mismatch: str | None,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "service")
    root = SimpleNamespace(id=0, creation_time=0, parent=None)
    mamba_value = object()
    loaded = SimpleNamespace(
        id=11, creation_time=7, parent=root, key=array("q", range(10)),
        component_data=(
            SimpleNamespace(value=object()), SimpleNamespace(value=None),
            SimpleNamespace(value=mamba_value),
        ),
    )
    audit._cache = SimpleNamespace(
        tree_core=SimpleNamespace(node_by_id={0: root, 11: loaded}.__getitem__),
    )
    audit.on_verified_action_ack(SimpleNamespace(
        command_id="mamba-prefetch", action="PREFETCH_GPU",
        context_id="ctx", context_epoch=2, node_ids=(11,),
        pool_bytes=(("mamba", 64),), num_bytes=64,
    ))
    request = SimpleNamespace(
        rid="parent-first-service", beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": "parent",
            "context_id": "ctx", "context_epoch": 3,
        },
        last_node=11, best_match_node=0 if mismatch == "other_node" else 11,
        origin_input_ids=list(range(20)), output_ids=[],
        prefix_indices=list(range(10)), cached_tokens_device=10,
        cached_tokens_host=0, mamba_host_hit_length=0,
        extend_input_len=10, sampling_params=SimpleNamespace(max_new_tokens=10),
        kv=SimpleNamespace(mamba_pool_idx=object()),
        finished=lambda: False,
    )
    if mismatch == "replaced_state":
        loaded.component_data[2].value = object()
    batch = SimpleNamespace(
        forward_mode=_Mode(
            "prefill", target_verify=mismatch == "speculative_verify"
        ), launch_ts=time.monotonic(), forward_iter=1,
        reqs=[request] + (
            [SimpleNamespace(rid="untracked", beliefkv_metadata=None)]
            if mismatch in (
                "multi_request", "multi_witness", "wrong_request", "wrong_source",
                "wrong_destination", "duplicate_witness", "empty_witness",
                "witness_uncompleted",
            ) else []
        ),
        mamba_cow_src_indices=[] if mismatch == "no_cow" else [3],
        mamba_cow_dst_indices=[] if mismatch == "no_cow" else [7],
    )
    if mismatch in (
        "multi_witness", "wrong_request", "wrong_source", "wrong_destination",
        "duplicate_witness", "empty_witness", "witness_uncompleted",
    ):
        witness = (
            "other" if mismatch == "wrong_request" else request.rid,
            object() if mismatch == "wrong_source" else mamba_value,
            object() if mismatch == "wrong_destination" else request.kv.mamba_pool_idx,
        )
        batch.beliefkv_mamba_cow_witnesses = (
            [] if mismatch == "empty_witness" else [witness]
        )
        if mismatch == "duplicate_witness":
            batch.beliefkv_mamba_cow_witnesses.append(witness)
    audit.on_launch(batch)
    if mismatch not in ("no_completed_forward", "witness_uncompleted"):
        audit.on_completed(batch)
    audit.close()
    use = _read(tmp_path / "service/physical_action_use.jsonl")
    assert use[0]["mamba_reuse"] == "unverified"
    verified = [
        row for row in use
        if row["event"] == "beliefkv_prefetch_mamba_forward_completed"
    ]
    assert len(verified) == (1 if mismatch in (None, "multi_witness") else 0)
    if verified:
        assert verified[0]["node_id"] == 11
        assert verified[0]["request_id"] == request.rid
        assert verified[0]["service_context_epoch"] == 3
        assert verified[0]["mamba_reuse"] == (
            "verified_per_request_cow_forward_completed"
            if mismatch == "multi_witness"
            else "verified_single_request_cow_forward_completed"
        )


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
    (0, 10, False, True),
    (5, 10, False, True),
    (0, 5, False, False),
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
        cached_tokens_device=cached_device,
        cached_tokens_host=max(0, device_prefix - cached_device),
        mamba_host_hit_length=0, extend_input_len=20 - device_prefix,
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
    assert use["full_reuse_proof_version"] == 2
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


def test_no_host_native_census_and_writer_do_not_require_host_observers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from beliefkv.runtime import sglang_v0520_observer

    calls = []
    capacity = {
        "pool_layout": "static_separate_full_mamba",
        "device_full_tokens": 12, "device_mamba_slots": 3,
        "device_full_bytes": 260, "device_mamba_bytes": 240,
        "device_total_bytes": 500,
        "host_full_tokens": 0, "host_mamba_slots": 0,
        "host_full_bytes": 0, "host_mamba_bytes": 0, "host_total_bytes": 0,
    }

    def observe(cache, **kwargs):
        calls.append(kwargs)
        return capacity

    def unexpected(*args):
        raise AssertionError("no Host attachment may be inspected")

    monkeypatch.setattr(sglang_v0520_observer, "observe_static_full_mamba", observe)
    monkeypatch.setattr(
        sglang_v0520_observer, "observe_static_full_mamba_host_usage", unexpected,
    )
    cache = SimpleNamespace(cache_controller=None, host_pool_group=None)
    audit = NativeReactiveTelemetry(tmp_path / "no-host")
    audit.record_capacity(cache)
    audit.record_host_pool_usage(cache)
    audit.close()
    assert calls == [{"allow_without_host": True}]
    assert json.loads((tmp_path / "no-host/native_capacity_census.json").read_text())[
        "capacity"
    ] == capacity
    status = json.loads((tmp_path / "no-host/native_telemetry_status.json").read_text())
    assert status["host_tier_enabled"] is False
    assert status["writer_error"] is None
    assert not status["host_block_eviction_attribution"]["available"]
    assert not (tmp_path / "no-host/host_pool_telemetry.jsonl").read_text()
    assert not hasattr(cache, "on_hicache_host_eviction")


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
        node_commits=(SimpleNamespace(
            command_id=None, anchor_node_id=1, published_node_ids=(1,),
            num_tokens_by_pool=(("kv", 8), ("mamba", 1)), num_bytes=8192,
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
    assert transfer["node_commits"] == [{
        "command_id": None, "anchor_node_id": 1, "published_node_ids": [1],
        "num_tokens_by_pool": {"kv": 8, "mamba": 1}, "num_bytes": 8192,
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
        time_stats=SimpleNamespace(beliefkv_ingress_timing_ms={
            "api_dispatch_started_ts_ms": time.time() * 1000.0,
        }),
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
    audit.on_completed(batch, SimpleNamespace(can_run_cuda_graph=False))
    batch.forward_mode = _Mode("decode")
    batch.forward_iter = 2
    batch.launch_ts = time.monotonic()
    audit.on_launch(batch)
    request.output_ids.extend((11, 12))
    request.finished = lambda: True
    audit.on_completed(batch, SimpleNamespace(can_run_cuda_graph=True))
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
    ingress = events[0]["attributes"]["native_ingress_timing_ms"]
    assert ingress["native_queue_registered_ts_ms"] == events[0]["ts_ms"]
    assert ingress["api_dispatch_started_ts_ms"] <= ingress["native_queue_registered_ts_ms"]
    assert events[1]["attributes"]["output_tokens"] == 2
    assert events[0]["context_epoch"] == 0
    service = _read(tmp_path / "server/runtime_audit.jsonl")
    assert [event["phase"] for event in service] == ["prefill", "decode"]
    assert [event["cuda_graph_replayed"] for event in service] == [False, True]
    assert service[0]["request_samples"][0]["token_delta"] == 14
    assert service[1]["request_samples"][0]["token_delta"] == 2
    assert all(row["timing_semantics_version"] == "gpu_service_interval_v1" for row in service)
    for row in service:
        assert row["launch_monotonic_ms"] <= row["service_start_monotonic_ms"]
        assert row["launch_timestamp_semantics"] == (
            "scheduler.run_batch entry; not CUDA submission or kernel start"
        )
        assert row["complete_monotonic_ms"] >= row["service_start_monotonic_ms"]
        assert row["complete_monotonic_ms"] - row["service_start_monotonic_ms"] == pytest.approx(
            row["service_elapsed_ms"], abs=1e-5,
        )
        assert row["monotonic_clock_domain"] == events[1]["attributes"]["monotonic_clock_domain"]
    assert events[1]["attributes"]["complete_monotonic_ms"] == service[-1]["complete_monotonic_ms"]
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
        "audit": 2, "events": 2, "host_pool": 1, "transfer": 1,
        "eviction_attribution": 2,
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


@pytest.mark.parametrize("collection_mode", ("native_reactive", "admission_observation"))
@pytest.mark.parametrize("token_count", (0, 3, 8192))
def test_completed_output_token_evidence_is_opt_in_immutable_and_once(
    tmp_path, monkeypatch, collection_mode, token_count,
):
    monkeypatch.setenv("BELIEFKV_RECORD_OUTPUT_TOKEN_IDS", "1")
    audit = NativeReactiveTelemetry(
        tmp_path / "server", collection_mode=collection_mode,
    )
    request = SimpleNamespace(
        rid="tokens", beliefkv_metadata={
            "root_workflow_id": "w", "invocation_id": "i",
            "context_id": "c", "context_epoch": 7,
        },
        origin_input_ids=[90000, 90001], output_ids=[],
        cached_tokens_device=0, cached_tokens_host=0,
        mamba_host_hit_length=0, extend_input_len=2,
        sampling_params=SimpleNamespace(max_new_tokens=8192),
        finished=lambda: False,
    )
    audit.on_enqueue(request)
    batch = SimpleNamespace(
        forward_mode=_Mode("prefill"), launch_ts=time.monotonic(),
        forward_iter=1, reqs=[request],
    )
    audit.on_launch(batch)
    audit.on_completed(batch)
    request.output_ids.extend(range(token_count))
    request.finished = lambda: True
    batch.forward_mode = _Mode("decode")
    batch.forward_iter = 2
    batch.launch_ts = time.monotonic()
    audit.on_launch(batch)
    audit.on_completed(batch)
    request.output_ids.append(99999)
    batch.forward_iter = 3
    batch.launch_ts = time.monotonic()
    audit.on_launch(batch)
    audit.on_completed(batch)
    audit.close()
    events = _read(tmp_path / "server/runtime_events.sglang.jsonl")
    completed = [event for event in events if event["kind"] == "llm_result"]
    assert len(completed) == 1
    result = completed[0]
    assert result["context_epoch"] == 7
    attrs = result["attributes"]
    evidence = attrs["output_token_evidence"]
    assert evidence["token_ids"] == list(range(token_count))
    assert len(evidence["token_ids"]) == attrs["output_tokens"] == token_count
    assert evidence["availability"] == "observed_request_completion"
    assert evidence["prompt_token_ids_included"] is False
    assert evidence["representation"] == "engine_retained_generated_output_ids"
    assert "output_token_evidence" not in events[0]["attributes"]
    for filename in ("native_telemetry_ready.json", "native_telemetry_status.json"):
        state = json.loads((tmp_path / "server" / filename).read_text())
        assert state["record_output_token_ids"] is True


@pytest.mark.parametrize("setting", (None, "0"))
def test_disabled_output_token_evidence_does_not_copy_generated_ids(
    tmp_path, monkeypatch, setting,
):
    if setting is None:
        monkeypatch.delenv("BELIEFKV_RECORD_OUTPUT_TOKEN_IDS", raising=False)
    else:
        monkeypatch.setenv("BELIEFKV_RECORD_OUTPUT_TOKEN_IDS", setting)

    class NoIteration(list):
        def __iter__(self):
            raise AssertionError("disabled output-token collection copied generated IDs")

    audit = NativeReactiveTelemetry(tmp_path / "server")
    request = SimpleNamespace(
        rid="disabled", beliefkv_metadata={
            "root_workflow_id": "w", "invocation_id": "i",
            "context_id": "c", "context_epoch": 0,
        },
        origin_input_ids=[90000], output_ids=NoIteration([1, 2]),
        cached_tokens_device=0, cached_tokens_host=0,
        mamba_host_hit_length=0, extend_input_len=1,
        sampling_params=SimpleNamespace(max_new_tokens=8192),
        finished=lambda: True,
    )
    audit.on_enqueue(request)
    batch = SimpleNamespace(
        forward_mode=_Mode("prefill"), launch_ts=time.monotonic(),
        forward_iter=1, reqs=[request],
    )
    audit.on_launch(batch)
    audit.on_completed(batch)
    audit.close()
    events = _read(tmp_path / "server/runtime_events.sglang.jsonl")
    result = next(event for event in events if event["kind"] == "llm_result")
    assert "output_token_evidence" not in result["attributes"]
    status = json.loads((tmp_path / "server/native_telemetry_status.json").read_text())
    assert status["record_output_token_ids"] is False


@pytest.mark.parametrize("setting", ("true", "2", ""))
def test_invalid_output_token_evidence_setting_fails_before_collection(
    tmp_path, monkeypatch, setting,
):
    monkeypatch.setenv("BELIEFKV_RECORD_OUTPUT_TOKEN_IDS", setting)
    with pytest.raises(ValueError, match="must be 0 or 1"):
        NativeReactiveTelemetry(tmp_path / "server")
    assert not (tmp_path / "server").exists()


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


@pytest.mark.parametrize("length_source", ["batch", "range", "unavailable"])
def test_prefill_uses_native_chunk_lengths_without_reading_device_tensors(
    tmp_path: Path, length_source: str,
) -> None:
    audit = NativeReactiveTelemetry(tmp_path / "server")
    request = SimpleNamespace(
        rid="chunked",
        beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": "child",
            "context_id": "context", "context_epoch": 0,
        },
        origin_input_ids=list(range(12000)), output_ids=[],
        sampling_params=SimpleNamespace(max_new_tokens=8),
        finished=lambda: False,
    )
    warmup = SimpleNamespace(rid="unattributed", beliefkv_metadata=None)
    batch = SimpleNamespace(
        forward_mode=_Mode("prefill"), launch_ts=time.monotonic(),
        forward_iter=1, reqs=[warmup, request],
    )
    if length_source == "batch":
        batch.prefix_lens, batch.extend_lens = [0, 4096], [1, 4096]
        request.extend_range = SimpleNamespace(start=0, length=12000)
        request.extend_input_len = 0
    elif length_source == "range":
        request.extend_range = SimpleNamespace(start=4096, length=4096)

    audit.on_launch(batch)
    if length_source != "unavailable":
        request.extend_range = SimpleNamespace(start=8192, length=3808)
    audit.on_completed(batch)
    audit.close()

    [record] = _read(tmp_path / "server/runtime_audit.jsonl")
    [sample] = record["request_samples"]
    assert record["batch_size"] == 2
    assert sample["request_id"] == "chunked"
    if length_source == "unavailable":
        assert sample["sequence_tokens_before"] is None
        assert sample["token_delta"] is None
        assert sample["prefill_geometry_source"] == "unavailable"
    else:
        assert sample["sequence_tokens_before"] == sample["token_delta"] == 4096
        assert sample["prefill_geometry_source"] == (
            "native_batch" if length_source == "batch" else "native_request_range"
        )


@pytest.mark.parametrize("with_scan_counts", (False, True))
def test_host_pool_usage_and_evictions_are_persisted_by_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_scan_counts: bool,
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
    scan_counts = {
        "calls": 5, "already_satisfied": 3, "heap_builds": 2, "heap_candidates": 24,
    }
    close_counts = {"close_calls": 3, "freed_device_full_tokens": 7}
    if with_scan_counts:
        cache.components = {0: SimpleNamespace(host_eviction_counts=scan_counts)}
        cache.write_back_batch_nodes = 32
        cache.write_back_batch_counts = {"attempts": 6, "batches": 2, "nodes": 7}
        cache.native_session_close_reclaim_stats = lambda: {
            "enabled": True, "pending_sessions": 1, "counts": close_counts,
        }
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
    scan_counts["calls"] = 6
    if with_scan_counts:
        cache.write_back_batch_counts["batches"] = 3
        close_counts["freed_device_full_tokens"] = 999

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
    assert usage["full_host_eviction_scans"] == ({
        **scan_counts, "calls": 5,
    } if with_scan_counts else {})
    assert usage["write_back_batch"] == ({
        "max_nodes": 32, "counts": {"attempts": 6, "batches": 2, "nodes": 7},
    } if with_scan_counts else {"max_nodes": None, "counts": {}})
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
    assert status["write_back_batch"] == usage["write_back_batch"]
    if with_scan_counts:
        assert usage["native_session_close_reclaim"] == {
            "enabled": True, "pending_sessions": 1,
            "counts": {"close_calls": 3, "freed_device_full_tokens": 7},
        }
        assert status["native_session_close_reclaim"] == usage["native_session_close_reclaim"]
    else:
        assert "native_session_close_reclaim" not in usage
        assert "native_session_close_reclaim" not in status


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
    decode_records = [record for record in records if record["phase"] == "decode"]
    assert decode_records[0]["launch_monotonic_ms"] == older.launch_ts * 1000
    assert decode_records[1]["launch_monotonic_ms"] == newer.launch_ts * 1000
    assert decode_records[1]["service_start_monotonic_ms"] == (
        decode_records[0]["complete_monotonic_ms"]
    )
    assert decode_records[1]["launch_monotonic_ms"] < (
        decode_records[1]["service_start_monotonic_ms"]
    )


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
