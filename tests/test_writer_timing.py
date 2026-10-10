import json
from unittest.mock import patch

import pytest

from beliefkv.runtime.v0520_opportunity_telemetry import NativeOpportunityTelemetry
from beliefkv.runtime.writer_timing import SampledWriterTiming


def test_samples_separate_cpu_from_waiting_and_do_not_scale_to_all_records():
    timing = SampledWriterTiming()
    with patch(
        "beliefkv.runtime.writer_timing.time.thread_time_ns",
        side_effect=[10, 1_000_010, 20, 2_000_020],
    ) as cpu_clock, patch(
        "beliefkv.runtime.writer_timing.time.perf_counter_ns",
        side_effect=[50, 10_000_050, 60, 20_000_060],
    ) as wall_clock:
        for _ in range(65):
            timing.finish(timing.begin("prefix"))
    row = timing.snapshot()["categories"]["prefix"]
    assert row["records_seen"] == 65
    assert row["samples"] == 2
    assert row["sampled_cpu_ms"] == 3.
    assert row["sampled_wall_ms"] == 30.
    assert row["mean_sample_cpu_ms"] == 1.5
    assert cpu_clock.call_count == wall_clock.call_count == 4


def test_categories_sample_independently_and_exception_cleanup_remains_measurable():
    timing = SampledWriterTiming()
    first = timing.begin("prefix")
    try:
        raise ValueError("original")
    except ValueError:
        pass
    finally:
        timing.finish(first)
    assert timing.begin("prefix") is None
    timing.finish(timing.begin("audit"))
    rows = timing.snapshot()["categories"]
    assert rows["prefix"]["records_seen"] == 2
    assert rows["prefix"]["samples"] == 1
    assert rows["audit"]["records_seen"] == rows["audit"]["samples"] == 1


def test_opportunity_writer_keeps_order_and_snapshots_completed_processing(tmp_path):
    writer = NativeOpportunityTelemetry(tmp_path)
    events = [{"event": "candidate", "index": index} for index in range(65)]
    state = {"event": "admission_runtime_state", "final": True}
    for event in (*events, state):
        writer.record(event)
    writer.close()
    rows = [json.loads(line) for line in writer.path.read_text().splitlines()]
    assert rows[:-1] == events
    assert state == {"event": "admission_runtime_state", "final": True}
    profile = rows[-1]["opportunity_writer_timing"]["categories"]["candidate"]
    assert profile["records_seen"] == 65 and profile["samples"] == 2
    status = json.loads(writer.status_path.read_text())
    assert status["complete"]
    assert status["enqueued"] == {"candidate": 65, "admission_runtime_state": 1}
    assert status["background_writer_timing"]["categories"]["admission_runtime_state"]["samples"] == 1


def test_invalid_json_evidence_stays_an_error_with_sampled_processing(tmp_path):
    writer = NativeOpportunityTelemetry(tmp_path)
    writer.record({"event": "invalid", "value": float("nan")})
    writer.close()
    status = json.loads(writer.status_path.read_text())
    assert not status["complete"]
    assert status["background_writer_timing"]["categories"]["invalid"]["samples"] == 1
    assert status["background_writer_timing"]["categories"]["invalid"]["sampled_cpu_ms"] >= 0
    with pytest.raises(RuntimeError, match="unavailable"):
        writer.record({"event": "later"})
