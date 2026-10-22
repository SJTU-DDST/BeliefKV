"""Offline backup accounting must retain unknowns and separate DMA from queue delay."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.audit_prepare_background import audit_data
from tests.test_native_full_prepare_audit import issue, run, transfer


def usage(when, *, capacity=1_000_000_000, used=0):
    return {
        "event": "host_pool_usage", "ts_ms": when,
        "pools": {"full": {"capacity_units": capacity, "used_units": used}},
    }


def pressure(when, *, unbacked=1, shortfall=True):
    return {
        "event": "critical_parent_headroom", "ts_ms": when,
        "capacity": {
            "full_target_satisfied": not shortfall,
            "native_full_rejections": {"unbacked": unbacked, "session_referenced": 4},
        },
    }


def test_small_batches_use_issued_round_denominator_and_do_not_imply_a_scan_limit():
    issues = [issue(1.), issue(5., nodes=((11, 18., 2),))]
    transfers = [
        transfer("d2h", 2., size=40),
        transfer("d2h", 6., nodes=(11,), tokens=2, size=20),
    ]
    transfers[0].update(transfer_stream_elapsed_ms=.1, submit_to_ack_ms=5000.)
    transfers[1].update(transfer_stream_elapsed_ms=.2, submit_to_ack_ms=7000.)
    report = audit_data(issues, transfers, [], full_unit=10)
    background = report["rounds"]["idle"]
    assert background["issued_batches"] == 2
    assert background["bytes_per_issued_batch"]["mean"] == 30.
    assert background["issue_gap_ms"]["p50"] == 4.
    assert background["batches_reaching_extent_limit"] == 0
    assert report["runtime_checks"] is None
    assert report["native_round_diagnostics"] is None
    assert report["directions"]["d2h"]["stream_ms_sum"] == pytest.approx(.3)
    assert report["directions"]["d2h"]["stream_ms_per_wall_ms"] == pytest.approx(.3 / 5.5)


def test_background_urgent_and_tagged_transfers_remain_separate():
    issues = [issue(1.), issue(5., nodes=((11, 18., 2),), source="critical")]
    transfers = [
        transfer("d2h", 2.),
        transfer("d2h", 6., nodes=(11,), tokens=2, size=20),
        transfer("h2d", 8., size=40),
        transfer("d2h", 10., nodes=(90,), tokens=3, size=30),
    ]
    transfers[-1]["tagged_child_commits"] = [{
        "command_id": "semantic-prepare", "anchor_node_id": 90,
        "published_node_ids": [90], "num_tokens_by_pool": {"kv": 3},
        "num_bytes": 30,
    }]
    issues.append({
        "event": "prepare_native_issued", "ts_ms": 9.,
        "command_id": "semantic-prepare", "source": "join_prepare",
    })
    report = audit_data(issues, transfers, [], full_unit=10)
    assert report["rounds"]["idle"]["issued_full_bytes_known"] == 40
    assert report["rounds"]["critical"]["issued_full_bytes_known"] == 20
    assert report["semantic_prepare_by_source"]["join_prepare"]["acknowledged_child_receipts"] == 1
    assert report["semantic_prepare_by_source"]["join_prepare"]["full_ack_bytes_known"] == 30
    assert report["directions"]["d2h"]["bytes"] == 90
    assert report["directions"]["h2d"]["bytes"] == 40


def test_spawn_backups_have_their_own_source_and_never_count_as_predictive_h2d():
    spawned = {**issue(), "event": "spawn_full_prepare", "max_merged_nodes": 64}
    report = audit_data([spawned], [transfer("d2h", 2.)], [], full_unit=10)
    assert report["rounds"]["spawn"]["issued_batches"] == 1
    assert report["rounds"]["spawn"]["acknowledged_full_bytes"] == 40
    assert report["rounds"]["idle"]["issued_batches"] == 0
    assert report["rounds"]["critical"]["issued_batches"] == 0
    assert report["native_backup_lifecycle"]["by_source"]["spawn"]["reconciled_ack_batches"] == 1
    assert report["directions"]["h2d"]["bytes"] == 0


def test_ambiguous_ack_cannot_supply_background_stream_or_ack_bytes():
    ack = transfer("d2h", 2., nodes=(10, 11), tokens=4, size=40)
    ack["transfer_stream_elapsed_ms"] = 2.
    report = audit_data([issue()], [ack], [], full_unit=10)
    assert report["rounds"]["idle"]["reconciled_ack_batches"] == 0
    assert report["rounds"]["idle"]["stream_ms_per_reconciled_batch"]["count"] == 0
    assert report["directions"]["d2h"]["stream_ms_sum"] == 2.


def test_duplicate_transfer_cannot_double_bandwidth():
    ack = transfer("d2h", 2.)
    with pytest.raises(ValueError, match="duplicate"):
        audit_data([issue()], [ack, ack.copy()], [], full_unit=10)


def test_unknown_stream_time_is_not_pretended_to_be_zero_measurement():
    report = audit_data([issue()], [transfer("d2h", 2.)], [], full_unit=10)
    assert report["directions"]["d2h"]["unknown_stream_batches"] == 1
    assert report["rounds"]["idle"]["stream_ms_per_reconciled_batch"]["count"] == 0


def test_cold_rejections_are_repeated_observations_and_only_use_prior_usage_samples():
    rows = [pressure(10.), pressure(20., unbacked=2), pressure(30., unbacked=0)]
    host = [
        usage(9.), {"event": "host_pool_transfer_ack", "ts_ms": 9.5},
        usage(11., used=999_999_999),
    ]
    report = audit_data(rows, [], host, full_unit=10)
    cold = report["cold_capacity"]
    assert cold["checks"] == 3
    assert cold["checks_with_cold_unbacked_rejection"] == 2
    assert cold["cold_unbacked_rejection_observations"] == 3
    assert cold["unbacked_checks_with_at_least_one_batch_host_free"] == 1
    assert cold["host_full_free_bytes_at_unbacked_check"]["min"] == 10
    assert cold["prior_host_sample_age_ms_at_unbacked_check"]["count"] == 2
    assert cold["prior_host_sample_age_ms_at_unbacked_check"]["max"] == 9.


def test_missing_capacity_and_future_only_sample_leave_headroom_unknown():
    report = audit_data([pressure(10.)], [], [usage(11.)], full_unit=10)
    assert report["cold_capacity"]["unbacked_checks_without_prior_host_sample"] == 1
    assert report["cold_capacity"]["host_full_free_bytes_at_unbacked_check"]["count"] == 0


def test_existing_lifecycle_proof_is_preserved_without_reassigning_consumed_bytes():
    native = run([issue()], [transfer("d2h", 2.), transfer("h2d", 4.)])
    report = audit_data(
        [issue()], [transfer("d2h", 2.), transfer("h2d", 4.)], [],
        full_unit=10, native=native,
    )
    assert report["native_backup_lifecycle"] == native["summary"]
    assert report["native_backup_lifecycle"]["restore_node_associations"] == 1


@pytest.mark.parametrize("pool,expected", (("full", 0), ("mamba", 1)))
def test_native_backup_fallback_respects_observed_host_eviction(pool, expected):
    report = audit_data(
        [issue()], [transfer("d2h", 2.), transfer("h2d", 4.)], [],
        full_unit=10,
        host_evictions=[{
            "event": "host_block_evicted", "pool": pool, "node_id": 10, "ts_ms": 3.,
        }],
    )
    assert report["native_backup_lifecycle"]["host_eviction_evidence_available"] is True
    assert report["native_backup_lifecycle"]["restore_node_associations"] == expected
    assert report["rounds"]["idle"]["acknowledged_full_bytes"] == 40
    assert report["directions"]["h2d"]["bytes"] == 40


@pytest.mark.parametrize("evictions,available", ((None, False), ([], True)))
def test_native_backup_fallback_keeps_missing_and_empty_eviction_evidence_distinct(
    evictions, available,
):
    report = audit_data(
        [issue()], [transfer("d2h", 2.), transfer("h2d", 4.)], [],
        full_unit=10, host_evictions=evictions,
    )
    assert report["native_backup_lifecycle"]["host_eviction_evidence_available"] is available
    assert report["native_backup_lifecycle"]["restore_node_associations"] == 1


def test_existing_lifecycle_remains_authoritative_when_fallback_evictions_are_supplied():
    native = run([issue()], [transfer("d2h", 2.), transfer("h2d", 4.)])
    report = audit_data(
        [issue()], [transfer("d2h", 2.), transfer("h2d", 4.)], [],
        native=native, full_unit=10,
        host_evictions=[{"pool": "full", "node_id": 10, "ts_ms": 3.}],
    )
    assert report["native_backup_lifecycle"] == native["summary"]


def test_runtime_configuration_and_skip_counts_are_read_from_existing_snapshots():
    state = {
        "event": "admission_runtime_state", "ts_ms": 30.,
        "counts": {
            "idle_full_prepare_checks": 4, "idle_full_prepare_native_calls": 3,
            "idle_full_prepare_issued_batches": 2, "idle_cold_full_backup_tokens": 12,
            "other": 999,
        },
        "idle_full_prepare": {
            "interval_ms": 800., "native": {"max_batch_bytes": 2**31, "checks": 3},
        },
    }
    report = audit_data([state], [], [], full_unit=10)
    assert report["runtime_checks"] == {
        "idle_full_prepare_checks": 4, "idle_full_prepare_native_calls": 3,
        "idle_full_prepare_issued_batches": 2,
    }
    assert report["native_round_diagnostics"]["checks"] == 3
    assert report["configuration"]["background_interval_ms"] == 800.
    assert report["configuration"]["background_max_batch_bytes"] == 2**31
    assert report["configuration"]["source"] == "runtime_snapshot"
    assert report["runtime_snapshot_cumulative_full_bytes_per_round"] == {
        "snapshot_ts_ms": 30., "issued_full_bytes_known": 120,
        "runtime_check": 30., "native_call": 40., "issued_batch": 60.,
    }


def test_window_assignment_is_by_submit_cohort_and_never_uses_ack_as_dma_duration():
    ack = transfer("d2h", 100., size=40)
    ack.update(submit_ts_ms=5., transfer_stream_elapsed_ms=2., submit_to_ack_ms=95.)
    rows = [issue(1.), pressure(90.)]
    report = audit_data(rows, [ack], [], full_unit=10, window_ms=50.)
    windows = report["windows_by_submit_cohort"]
    assert windows[0]["d2h_bytes"] == 40
    assert windows[0]["d2h_stream_ms"] == 2.
    assert windows[1]["d2h_bytes"] == 0
    assert windows[1]["checks_with_cold_unbacked_rejection"] == 1


def test_empty_trace_has_no_bandwidth_or_coverage_claim():
    report = audit_data([], [], [], full_unit=None)
    assert report["rounds"]["idle"]["issued_batches"] == 0
    assert report["rounds"]["idle"]["bytes_per_issued_batch"]["mean"] is None
    assert report["directions"]["d2h"]["stream_ms_per_wall_ms"] is None
    assert report["windows_by_submit_cohort"] == []


def test_cli_reuses_sealed_prefix_when_arm_path_changes_from_relative_to_absolute(tmp_path, monkeypatch):
    from scripts.audit_sealed_wait_prefetch import SealedRecords

    monkeypatch.chdir(tmp_path)
    arm = Path("arm")
    (arm / "server").mkdir(parents=True)
    (arm / "opportunities").mkdir()
    paths = (
        arm / "server/transfer_telemetry.jsonl",
        arm / "opportunities/admission_opportunities.jsonl",
    )
    values = ([transfer("d2h", 2.)], [issue()])
    for path, rows in zip(paths, values):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    sealed = SealedRecords()
    for path in paths:
        list(sealed(path))
    lifecycle = {
        "input_jsonl_prefixes": sealed.prefixes,
        "native_full_prepare_attribution": run(values[1], values[0]),
    }
    report = tmp_path / "lifecycle.json"
    report.write_text(json.dumps(lifecycle))
    with paths[0].open("a") as stream:
        stream.write(json.dumps(transfer("d2h", 20., nodes=(99,))) + "\n")
    eviction_path = arm / "server/eviction_attribution.jsonl"
    eviction_path.write_text(json.dumps({
        "event": "host_block_evicted", "pool": "full", "node_id": 10, "ts_ms": 3.,
    }) + "\n")
    output = tmp_path / "result.json"
    script = Path(__file__).resolve().parents[1] / "scripts/audit_prepare_background.py"
    result = subprocess.run(
        [sys.executable, str(script), "--arm", str(arm.resolve()),
         "--lifecycle", str(report), "--output", str(output)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_bytes())
    assert data["directions"]["d2h"]["completed_batches"] == 1
    assert data["native_backup_lifecycle"] == lifecycle["native_full_prepare_attribution"]["summary"]
    assert str(eviction_path.resolve()) not in data["input_jsonl_prefixes"]


@pytest.mark.parametrize("eviction_file", ("missing", "empty", "full", "mamba"))
def test_standalone_cli_seals_host_eviction_evidence_and_ends_full_copy_association(
    tmp_path, eviction_file,
):
    arm = tmp_path / "arm"
    server = arm / "server"
    server.mkdir(parents=True)
    opportunities = arm / "opportunities"
    opportunities.mkdir()
    (opportunities / "admission_opportunities.jsonl").write_text(
        json.dumps(issue()) + "\n"
    )
    (server / "transfer_telemetry.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in (
            transfer("d2h", 2.), transfer("h2d", 4.),
        ))
    )
    eviction_path = server / "eviction_attribution.jsonl"
    if eviction_file != "missing":
        rows = (
            [] if eviction_file == "empty" else [{
                "event": "host_block_evicted", "pool": eviction_file,
                "node_id": 10, "ts_ms": 3.,
            }]
        )
        eviction_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    output = tmp_path / "report.json"
    script = Path(__file__).resolve().parents[1] / "scripts/audit_prepare_background.py"
    result = subprocess.run(
        [sys.executable, str(script), "--arm", str(arm), "--output", str(output)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_bytes())
    lifecycle = data["native_backup_lifecycle"]
    assert lifecycle["host_eviction_evidence_available"] is (eviction_file != "missing")
    assert lifecycle["restore_node_associations"] == (eviction_file != "full")
    assert lifecycle["acknowledged_full_bytes"] == 40
    assert data["directions"]["h2d"]["bytes"] == 40
    assert (str(eviction_path) in data["input_jsonl_prefixes"]) is (
        eviction_file != "missing"
    )
