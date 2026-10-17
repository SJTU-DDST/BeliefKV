import pytest

from scripts.audit_selection_transfer_overlap import selection_transfers


def sample():
    return {
        "batch_size": 1, "extend_tokens": 100, "request_ids": ["r"],
        "forward_iter": 1,
        "phases": {
            "selection": {
                "start_monotonic_ms": 10, "end_monotonic_ms": 20,
                "wall_ms": 10, "thread_cpu_ms": 8,
            },
        },
    }


def transfer(identity, start, end, *, direction="d2h"):
    return {
        "status": "completed", "command_id": identity, "direction": direction,
        "submit_ts_ms": start, "complete_ts_ms": end, "actual_bytes": 10,
        "node_ids": [1, 2], "transfer_stream_elapsed_ms": None,
    }


def test_submission_uses_offset_and_half_open_boundary_without_imputing_completion():
    result = selection_transfers([sample()], {"r": "positive_full_host"}, [
        transfer("before", 109, 115),
        transfer("begin", 110, 119),
        transfer("middle", 115, 125),
        transfer("end", 120, 121),
    ], 100)
    value = result["by_host_counter"]["positive_full_host"]["d2h"]
    assert value["submitted_batches"] == 2
    assert value["completed_by_selection_end"] == 1
    assert value["actual_bytes"] == 20
    assert value["multi_node_batches"] == 2
    assert value["unmeasured_stream_batches"] == 2
    assert result["rows"][0]["selection_wall_interval_ms"] == [110, 120]


def test_directions_stay_separate_and_missing_selection_is_explicit():
    missing = sample()
    missing["phases"].clear()
    result = selection_transfers([sample(), missing], {}, [
        transfer("same", 115, 116),
        transfer("same", 115, 116, direction="h2d"),
    ], 100)
    group = result["by_host_counter"]["host_counters_unobserved"]
    assert group["d2h"]["submitted_batches"] == group["h2d"]["submitted_batches"] == 1
    assert result["by_host_counter"]["selection_unobserved"]["selection_observed_samples"] == 0


@pytest.mark.parametrize("invalid", ["duplicate", "reversed", "clock"])
def test_invalid_evidence_does_not_create_transfer_attribution(invalid):
    rows = [transfer("a", 115, 116)]
    offset = 100
    if invalid == "duplicate":
        rows.append(rows[0])
    elif invalid == "reversed":
        rows[0]["complete_ts_ms"] = 114
    else:
        offset = float("nan")
    with pytest.raises(ValueError):
        selection_transfers([sample()], {}, rows, offset)
