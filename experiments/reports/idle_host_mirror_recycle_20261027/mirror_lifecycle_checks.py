"""Check that lifecycle evidence cannot credit an unrelated native copy."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "mirror_lifecycle", Path(__file__).with_name("mirror_lifecycle.py"),
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def evidence():
    release = {
        "event": "idle_host_mirror_recycled", "ts_ms": 10.,
        "cold_node_id": 1, "cold_creation_time": 3.,
        "planned_backup_tokens": 4, "host_reserved_tokens": 4,
        "host_free_before": 0, "host_free_after": 8,
        "released_host_nodes": [[2, 2., 8]], "released_host_tokens": 8,
        "released_host_bytes": 8192, "released_device_tokens": 0,
        "evidence": "current_cold_owner;device_pinned_mirror;native_host_dma_guards",
    }
    prepare = {
        "event": "idle_cold_full_prepare", "ts_ms": 12.,
        "native_nodes": [[1, 3., 4]],
    }
    ack = {
        "event": "transfer_telemetry", "direction": "d2h", "status": "completed",
        "command_id": "copy", "node_ids": [1], "actual_bytes": 4096,
        "num_tokens_by_pool": {"kv": 4},
        "submit_ts_ms": 11., "complete_ts_ms": 13.,
    }
    return release, prepare, ack


def test_native_copy_credits_only_the_actual_cold_checkpoint(evidence):
    release, prepare, ack = evidence
    result = MODULE.inspect([release, prepare], [ack], 1024)
    assert result["summary"]["verified_native_cold_backup_bytes"] == 4096
    assert result["summary"]["released_host_bytes"] == 8192
    assert not result["releases"][0]["downstream_hbm_release_or_service_verified"]


@pytest.mark.parametrize("field,value", (
    ("direction", "h2d"), ("status", "failed"), ("node_ids", [2]),
    ("actual_bytes", 8192), ("submit_ts_ms", 9.), ("submit_ts_ms", 13.),
    ("complete_ts_ms", 10.), ("num_tokens_by_pool", {"kv": 8}),
))
def test_foreign_or_unfinished_copy_remains_unverified(evidence, field, value):
    release, prepare, ack = evidence
    ack[field] = value
    result = MODULE.inspect([release, prepare], [ack], 1024)
    assert result["summary"]["verified_native_cold_backup_bytes"] == 0


def test_future_generation_is_not_the_same_checkpoint(evidence):
    release, prepare, ack = evidence
    prepare["native_nodes"][0][1] += 1
    result = MODULE.inspect([release, prepare], [ack], 1024)
    assert result["summary"]["verified_native_cold_backup_bytes"] == 0
    assert result["releases"][0]["status"].startswith("no_later_same_generation")


def test_release_with_no_resulting_backup_is_preserved(evidence):
    release, _prepare, ack = evidence
    result = MODULE.inspect([release], [ack], 1024)
    assert result["summary"]["mirror_release_records"] == 1
    assert result["summary"]["verified_native_cold_backup_bytes"] == 0


def test_physical_release_claim_or_inconsistent_capacity_is_rejected(evidence):
    release, prepare, ack = evidence
    release["released_device_tokens"] = 1
    with pytest.raises(ValueError, match="geometry"):
        MODULE.inspect([release, prepare], [ack], 1024)
    release["released_device_tokens"] = 0
    release["host_free_after"] = 7
    with pytest.raises(ValueError, match="geometry"):
        MODULE.inspect([release, prepare], [ack], 1024)


def test_duplicate_ack_cannot_inflate_verified_bytes(evidence):
    release, prepare, ack = evidence
    with pytest.raises(ValueError, match="duplicate native"):
        MODULE.inspect([release, prepare], [ack, ack.copy()], 1024)


def test_ambiguous_distinct_native_copies_are_not_selected_by_convenience(evidence):
    release, prepare, ack = evidence
    duplicate = {**ack, "command_id": "other"}
    result = MODULE.inspect([release, prepare], [ack, duplicate], 1024)
    assert result["summary"]["verified_native_cold_backup_bytes"] == 0


def test_repeated_release_cannot_reuse_one_ack(evidence):
    release, prepare, ack = evidence
    with pytest.raises(ValueError, match="two mirror"):
        MODULE.inspect([release, release.copy(), prepare], [ack], 1024)
