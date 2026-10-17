import pytest

from scripts.audit_checkpoint_write_read_balance import copy_balance


SIZING = {"kv": 2, "mamba": 10}


def receipt(identity, direction, start, *, kv=0, mamba=0, complete=None):
    return {
        "status": "completed", "command_id": identity, "direction": direction,
        "submit_ts_ms": start, "complete_ts_ms": start + 1 if complete is None else complete,
        "num_tokens_by_pool": {"kv": kv, "mamba": mamba},
        "actual_bytes": 2 * kv + 10 * mamba,
        "transfer_stream_elapsed_ms": None,
    }


def test_conservative_read_bound_uses_all_reads_including_earlier_duplicate_cow_copies():
    result = copy_balance([
        receipt("before", "h2d", 1, mamba=2),
        receipt("writes", "d2h", 10, kv=10, mamba=10),
        receipt("end", "d2h", 20, mamba=100),
        receipt("after", "h2d", 25, mamba=2),
    ], 10, 20, SIZING)
    bound = result["completed_read_source_bounds"]["mamba"]
    assert bound["cohort_completed_write_copy_units"] == 10
    assert bound["all_observed_completed_read_copy_units"] == 4
    assert bound["maximum_fraction_that_can_back_completed_reads"] == .4
    assert bound["minimum_write_copy_units_not_needed_as_distinct_sources_of_completed_reads"] == 6
    assert result["totals"]["cohort_d2h"]["bytes_by_pool"] == {"kv": 20, "mamba": 100}


def test_more_reads_than_writes_and_zero_write_cohort_are_not_imputed_hits():
    result = copy_balance([
        receipt("write", "d2h", 11, kv=1),
        receipt("read", "h2d", 21, kv=10, mamba=4),
    ], 10, 20, SIZING)
    assert result["completed_read_source_bounds"]["kv"]["maximum_fraction_that_can_back_completed_reads"] == 1
    assert result["completed_read_source_bounds"]["mamba"]["maximum_fraction_that_can_back_completed_reads"] is None


def test_incomplete_copies_are_not_credited_and_unknown_stream_time_stays_unknown():
    pending = receipt("pending", "h2d", 15, mamba=100)
    pending["status"] = "pending"
    result = copy_balance([
        receipt("write", "d2h", 11, mamba=4),
        pending, receipt("later", "d2h", 21, kv=1),
    ], 10, 20, SIZING)
    assert result["non_completed_records_not_credited"] == 1
    assert result["completed_read_source_bounds"]["mamba"]["maximum_fraction_that_can_back_completed_reads"] == 0
    assert result["totals"]["cohort_d2h"]["unmeasured_stream_batches"] == 1


@pytest.mark.parametrize("invalid", ["duplicate", "size", "units", "clock", "coverage"])
def test_invalid_ledger_or_missing_coverage_is_rejected(invalid):
    row = receipt("r", "d2h", 11, mamba=1, complete=25)
    rows = [row]
    if invalid == "duplicate":
        rows.append(row)
    elif invalid == "size":
        row["actual_bytes"] += 1
    elif invalid == "units":
        row["num_tokens_by_pool"]["mamba"] = True
    elif invalid == "clock":
        row["complete_ts_ms"] = 10
    else:
        row["complete_ts_ms"] = 15
    with pytest.raises(ValueError):
        copy_balance(rows, 10, 20, SIZING)
