import pytest

from scripts.screen_prepare_host_value import category, summarize


def backup():
    return {
        "source": "idle", "ack_evidence": "reconciled_full_batch", "ack_ts_ms": 5.,
        "ack_transfer_command_id": "copy", "acknowledged_full_bytes": 100,
        "native_nodes": [(1, 10., 5), (2, 20., 5)],
        "capacity_releases": [], "restore_associations": [],
        "copy_end_observations": [{"node_id": 1, "ts_ms": 10.}],
    }


def receipt():
    return {
        "command_id": "copy", "direction": "d2h", "status": "completed",
        "actual_bytes": 100, "complete_ts_ms": 5.,
        "transfer_stream_elapsed_ms": 2., "submit_to_ack_ms": 4.,
    }


def test_partially_ended_backup_remains_censored():
    row = backup()
    assert category(row) == "right_censored_without_observed_release_or_restore"
    row["copy_end_observations"].append({"node_id": 2, "ts_ms": 11.})
    assert category(row) == "ended_without_observed_release_or_restore"
    row["restore_associations"].append({"node_id": 1})
    assert category(row) == "observed_release_or_restore"


def test_transfer_service_is_reconciled_and_kept_separate_from_ACK_envelope():
    result = summarize([backup()], [receipt()])
    group = result["by_source_and_lifecycle"]["idle"]["right_censored_without_observed_release_or_restore"]
    assert group["acknowledged_bytes"] == 100
    assert group["stream_service_ms"]["sum"] == 2.
    assert group["submit_to_ACK_ms"]["sum"] == 4.
    assert not result["GPU_action"]


@pytest.mark.parametrize("mutation", ("missing", "bytes", "direction", "duplicate"))
def test_missing_ambiguous_or_inconsistent_receipts_do_not_gain_transfer_credit(mutation):
    transfers = [receipt()]
    if mutation == "missing":
        transfers.clear()
    elif mutation == "bytes":
        transfers[0]["actual_bytes"] += 1
    elif mutation == "direction":
        transfers[0]["direction"] = "h2d"
    else:
        transfers.append(receipt())
    with pytest.raises(ValueError):
        summarize([backup()], transfers)
