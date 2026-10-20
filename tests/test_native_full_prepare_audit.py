"""Offline attribution for untagged native FULL backup batches."""

import json

import pytest

from scripts.audit_prefetch_lifecycle import audit, native_full_prepare_attribution


def issue(when=1., nodes=((10, 17., 4),), source="idle"):
    return {
        "event": "idle_cold_full_prepare" if source == "idle" else "critical_parent_cold_full_prepare",
        "ts_ms": when, "native_nodes": nodes, "include_mamba": False,
    }


def transfer(direction, when, nodes=(10,), tokens=4, size=40):
    return {
        "command_id": f"native-{direction}-{when}", "direction": direction,
        "status": "completed", "node_ids": list(nodes),
        "num_tokens_by_pool": {"kv": tokens}, "actual_bytes": size,
        "submit_ts_ms": when if direction == "h2d" else when - 1.5,
        "complete_ts_ms": when if direction == "d2h" else when + .5,
    }


def run(issues, transfers, reclaims=(), uses=None, evictions=(), full_unit=10):
    return native_full_prepare_attribution(
        issues, transfers, reclaims, uses or {}, evictions, full_unit=full_unit,
    )


def test_native_full_prepare_reconciles_ack_then_same_generation_release_and_restore():
    release = {
        "event": "critical_parent_headroom", "ts_ms": 3.,
        "victims": [
            {"node_id": 10, "creation_time": 17., "freed_units": {"0": 4}},
            {"node_id": 11, "creation_time": 99., "freed_units": {"0": 2}},
        ],
    }
    result = run(
        [issue(nodes=((10, 17., 4), (11, 18., 2)), source="critical")],
        [transfer("d2h", 2., nodes=(10, 11), tokens=6, size=60),
         transfer("h2d", 4., nodes=(10, 11), tokens=6, size=60)],
        [release],
    )
    summary = result["summary"]
    assert summary["reconciled_ack_batches"] == 1
    assert summary["acknowledged_full_bytes"] == 60
    assert summary["observed_released_full_tokens"] == 4
    assert summary["restore_node_associations"] == 2
    assert summary["restore_associations_with_verified_controlled_first_service"] == 0
    assert summary["by_source"]["critical"]["reconciled_ack_batches"] == 1
    [row] = result["rows"]
    assert row["ack_ts_ms"] == 2.
    assert row["capacity_releases"][0]["full_bytes_known"] == 40
    assert row["capacity_releases"][0]["node_id"] == 10


@pytest.mark.parametrize("change", ("nodes", "units", "mamba", "bytes", "tagged", "split"))
def test_ambiguous_native_ack_never_grants_full_byte_or_restore_credit(change):
    ack = transfer("d2h", 2.)
    if change in ("nodes", "split"):
        ack["node_ids"].append(11)
    elif change == "units":
        ack["num_tokens_by_pool"]["kv"] = 3
    elif change == "mamba":
        ack["num_tokens_by_pool"]["mamba"] = 1
    elif change == "bytes":
        ack["actual_bytes"] = 41
    elif change == "tagged":
        ack["tagged_child_commits"] = [{
            "command_id": "semantic-prepare", "anchor_node_id": 10,
            "published_node_ids": [10], "num_tokens_by_pool": {"kv": 4},
            "num_bytes": 40,
        }]
    result = run([issue()], [ack, transfer("h2d", 4.)])
    assert result["summary"]["ambiguous_ack_batches"] == 1
    assert result["summary"]["acknowledged_full_bytes"] == 0
    assert result["summary"]["restore_node_associations"] == 0


@pytest.mark.parametrize("when", (2., 3.))
def test_full_host_eviction_at_or_after_ack_ends_original_copy_association(when):
    result = run(
        [issue()], [transfer("d2h", 2.), transfer("h2d", 4.)],
        evictions=[{"pool": "full", "node_id": 10, "ts_ms": when}],
    )
    assert result["summary"]["reconciled_ack_batches"] == 1
    assert result["summary"]["restore_node_associations"] == 0
    assert result["rows"][0]["copy_end_observations"] == [{
        "node_id": 10, "ts_ms": when, "reason": "host_eviction",
    }]


def test_mamba_only_write_preserves_full_but_later_full_write_ends_it():
    mamba = transfer("d2h", 3., tokens=0, size=64)
    mamba["num_tokens_by_pool"] = {"mamba": 1}
    result = run([issue()], [
        transfer("d2h", 2.), mamba, transfer("h2d", 4.),
        transfer("d2h", 5.), transfer("h2d", 6.),
    ])
    [row] = result["rows"]
    assert [restore["submit_ts_ms"] for restore in row["restore_associations"]] == [4.]
    assert row["copy_end_observations"] == [{
        "node_id": 10, "ts_ms": 5., "reason": "later_full_d2h",
    }]


def test_new_node_generation_and_overlapping_unacked_issues_cannot_inherit_credit():
    newer = issue(3., nodes=((10, 18., 4),))
    result = run([issue(), newer], [transfer("d2h", 2.), transfer("h2d", 4.)])
    assert result["summary"]["reconciled_ack_batches"] == 1
    assert result["summary"]["ack_unobserved_batches"] == 1
    assert result["summary"]["restore_node_associations"] == 0
    assert result["rows"][0]["copy_end_observations"][0]["reason"] == "new_node_generation"
    duplicate = run([issue(), issue(1.5)], [transfer("d2h", 2.), transfer("h2d", 4.)])
    assert duplicate["summary"]["ambiguous_ack_batches"] == 2
    assert duplicate["summary"]["acknowledged_full_bytes"] == 0


@pytest.mark.parametrize("proof", ("valid", "legacy", "other_node", "unknown_time", "early_time"))
def test_first_service_requires_the_controlled_full_allocation_proof(proof):
    restore = transfer("h2d", 4.)
    restore["tagged_child_commits"] = [{
        "command_id": "prefetch", "anchor_node_id": 10,
        "published_node_ids": [10], "num_tokens_by_pool": {"kv": 4},
        "num_bytes": 40,
    }]
    use = {
        "full_node_reused": True,
        "full_reuse_proof_version": 1 if proof == "legacy" else 2,
        "reused_full_node_ids": [11] if proof == "other_node" else [10],
        "first_service_ts_ms": None if proof == "unknown_time" else 3. if proof == "early_time" else 5.,
        "request_id": "parent-next",
    }
    result = run([issue()], [transfer("d2h", 2.), restore], uses={"prefetch": use})
    summary = result["summary"]
    assert summary["restore_node_associations"] == 1
    assert summary["restore_associations_with_verified_controlled_first_service"] == (proof == "valid")
    [association] = result["rows"][0]["restore_associations"]
    assert association["first_service_request_id"] == ("parent-next" if proof == "valid" else None)


def test_missing_eviction_and_unit_evidence_stays_unknown():
    result = run(
        [issue()], [transfer("d2h", 2.), transfer("h2d", 4.)],
        evictions=None, full_unit=None,
    )
    summary = result["summary"]
    assert summary["issued_full_bytes_known"] == 0
    assert summary["issued_full_bytes_unknown_batches"] == 1
    assert summary["acknowledged_full_bytes"] == 40
    assert summary["host_eviction_evidence_available"] is False
    assert result["rows"][0]["restore_associations"][0]["host_eviction_evidence_available"] is False


def test_audit_keeps_native_background_separate_from_semantic_prepare_and_prefetch(tmp_path):
    for relative, rows in (
        ("opportunities/admission_opportunities.jsonl", [issue()]),
        ("server/transfer_telemetry.jsonl", [transfer("d2h", 2.), transfer("h2d", 4.)]),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = audit(tmp_path)
    assert report["summary"]["prepare_issued_commands"] == 0
    assert report["summary"]["join_bytes"] == report["summary"]["tool_bytes"] == 0
    native = report["native_full_prepare_attribution"]["summary"]
    assert native["reconciled_ack_batches"] == 1
    assert native["batches_with_restore_association"] == 1
