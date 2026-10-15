import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime import prefill_turnover_probe as module
from beliefkv.runtime.prefill_turnover_probe import PrefillTurnoverProbe


def batch(*, extend=True, forward=0, request="r"):
    return NS(
        reqs=[NS(rid=request)],
        forward_mode=NS(is_extend=lambda: extend),
        forward_iter=forward, launch_ts=0.,
        extend_num_tokens=80, prefix_lens=[256],
    )


class Clock:
    wall = 0
    cpu = 0

    def advance(self, wall_ms, cpu_ms):
        self.wall += int(wall_ms * 1e6)
        self.cpu += int(cpu_ms * 1e6)

    def read(self):
        return self.wall, self.cpu


class Event:
    def __init__(self, device):
        self.device = device
        self.stream = None

    def record(self, *, stream):
        self.stream = stream

    def query(self):
        assert self.stream is not None
        return self.device.ready

    def elapsed_time(self, other):
        assert self.stream is other.stream
        return 12.5

    def synchronize(self):
        raise AssertionError("observations must not synchronize CUDA")


class Device:
    ready = False
    capturing = False

    def Event(self, *, enable_timing):
        assert enable_timing is True
        return Event(self)

    def is_current_stream_capturing(self):
        return self.capturing


def launch(probe, selected, *, forward=1, device=None):
    probe.on_selected(selected, probe.begin_selection(), loop="overlap")
    handle = probe.begin_forward(selected, device, object() if device else None)
    selected.forward_iter = forward
    probe.on_launch(selected)
    probe.end_forward(handle)
    return handle


def test_turnover_pairs_copied_result_with_cpu_phases_and_pending_gpu(monkeypatch):
    clock, records = Clock(), []
    monkeypatch.setattr(module, "_clock", clock.read)
    probe = PrefillTurnoverProbe(lambda *row: records.append(row), every=1)
    selected, device, stream = batch(), Device(), object()
    started = probe.begin_selection()
    clock.advance(2, 1)
    probe.on_selected(selected, started, loop="overlap")
    clock.advance(3, .5)
    forward = probe.begin_forward(selected, device, stream)
    selected.forward_iter = 17
    selected.launch_ts = clock.wall / 1e9
    probe.on_launch(selected)
    clock.advance(14, 8)
    probe.end_forward(forward)
    physical = probe.begin_phase(selected, "post_launch_physical")
    clock.advance(4, 3)
    probe.end_phase(physical)
    clock.advance(20, 1)
    copied = batch(forward=17)
    result = probe.begin_phase(copied, "result_processing")
    clock.advance(6, 2)
    probe.on_processed(result, NS(can_run_cuda_graph=True))
    assert not records
    assert probe.snapshot()["pending_samples"] == 1
    device.ready = True
    probe.poll()
    assert probe.snapshot()["pending_samples"] == 0
    domain, row = records[0]
    assert domain == "audit"
    assert row["forward_iter"] == 17
    assert row["gpu_run_batch_span_ms"] == 12.5
    assert row["gpu_span_status"] == "observed"
    assert row["phases"]["selection"]["wall_ms"] == 2
    assert row["phases"]["run_batch"]["wall_ms"] == 14
    assert row["phases"]["run_batch"]["thread_cpu_ms"] == 8
    assert row["phases"]["post_launch_physical"]["wall_ms"] == 4
    assert row["phases"]["result_processing"]["wall_ms"] == 6
    assert row["selection_end_to_run_start_ms"] == 3
    assert row["run_return_to_result_start_ms"] == 24
    probe.poll()
    assert len(records) == 1


def test_next_decode_cannot_overwrite_a_pending_prefill_forward():
    records = []
    probe = PrefillTurnoverProbe(lambda *row: records.append(row), every=1)
    selected = batch()
    launch(probe, selected, forward=7)
    previous = probe._samples[7].phases["run_batch"]
    reused = batch(extend=False, forward=7)
    probe.on_selected(reused, probe.begin_selection(), loop="overlap")
    assert probe.begin_forward(reused, None, None) is None
    assert probe._samples[7].phases["run_batch"] == previous
    result = probe.begin_phase(batch(forward=7), "result_processing")
    probe.on_processed(result, NS(can_run_cuda_graph=True))
    assert len(records) == 1
    assert records[0][1]["gpu_run_batch_span_ms"] is None


def test_changed_request_identity_does_not_authorize_phase_pairing():
    records = []
    probe = PrefillTurnoverProbe(lambda *row: records.append(row), every=1)
    selected = batch()
    launch(probe, selected, forward=9)
    assert probe.begin_phase(batch(forward=9, request="other"), "result_processing") is None
    assert probe.counts["phase_identity_unknown"] == 1
    probe.close()
    assert records[0][1]["event"] == "prefill_turnover_incomplete"
    assert records[0][1]["result_processed"] is False
    assert "result_processing" not in records[0][1]["phases"]


def test_sampling_and_backlog_bound_do_not_invent_completed_observations():
    records = []
    probe = PrefillTurnoverProbe(lambda *row: records.append(row), every=2, limit=1)
    launch(probe, batch(), forward=1)
    launch(probe, batch(), forward=2)
    launch(probe, batch(), forward=3)
    assert probe.snapshot()["pending_samples"] == 1
    assert probe.counts["eligible_prefills"] == 3
    assert probe.counts["bounded_queue_skips"] == 1
    assert probe.counts["completed_samples"] == 0
    probe.close()
    assert len(records) == 1
    assert records[0][1]["event"] == "prefill_turnover_incomplete"
    assert probe.counts["shutdown_incomplete_samples"] == 1


@pytest.mark.parametrize("capturing", [False, True])
def test_missing_or_captured_gpu_span_remains_unknown(capturing):
    records = []
    probe = PrefillTurnoverProbe(lambda *row: records.append(row), every=1)
    device = Device()
    device.capturing = capturing
    selected = batch()
    launch(probe, selected, device=device if capturing else None)
    result = probe.begin_phase(selected, "result_processing")
    probe.on_processed(result, NS(can_run_cuda_graph=False))
    row = records[0][1]
    assert row["gpu_run_batch_span_ms"] is None
    assert row["gpu_span_status"] == ("capturing" if capturing else "unavailable")


def test_selected_but_unlaunched_batch_does_not_survive_an_idle_selection():
    probe = PrefillTurnoverProbe(lambda *_row: None, every=1)
    probe.on_selected(batch(), probe.begin_selection(), loop="normal")
    probe.on_selected(None, probe.begin_selection(), loop="normal")
    assert probe._selected is None
    assert probe.counts["selected_without_launch"] == 1


def test_normal_forward_uses_current_stream_only_for_a_sampled_batch():
    records = []
    probe = PrefillTurnoverProbe(lambda *row: records.append(row), every=2)
    device = Device()
    device.ready = True
    current = object()
    reads = []
    device.current_stream = lambda: reads.append(current) or current
    selected = batch()
    probe.on_selected(selected, probe.begin_selection(), loop="normal")
    handle = probe.begin_forward(selected, device, None)
    assert handle[0].gpu_events[0].stream is current
    selected.forward_iter = 1
    probe.on_launch(selected)
    probe.end_forward(handle)
    probe.on_processed(
        probe.begin_phase(selected, "result_processing"), NS(can_run_cuda_graph=True),
    )
    assert records[0][1]["gpu_stream_scope"] == "current_stream"
    assert len(reads) == 1
    skipped = batch()
    probe.on_selected(skipped, probe.begin_selection(), loop="normal")
    assert probe.begin_forward(skipped, device, None) is None
    assert len(reads) == 1


def test_adjacent_decode_phases_retain_absolute_cpu_endpoints_without_completing_prefill(monkeypatch):
    clock, records = Clock(), []
    monkeypatch.setattr(module, "_clock", clock.read)
    probe = PrefillTurnoverProbe(lambda _stream, row: records.append(row), every=1)
    selected = batch(request="prefill")
    started = probe.begin_selection()
    clock.advance(1., 1.)
    probe.on_selected(selected, started, loop="overlap")
    clock.advance(.5, .1)
    forward = probe.begin_forward(selected, None, None)
    selected.forward_iter, selected.launch_ts = 17, clock.wall / 1e9
    probe.on_launch(selected)
    clock.advance(12., 6.)
    probe.end_forward(forward)
    war = probe.begin_phase(selected, "war_barrier_and_queue_publish")
    clock.advance(.5, .2)
    probe.end_phase(war)
    previous = batch(extend=False, forward=16, request="previous")
    predecessor = probe.begin_phase(previous, "result_processing")
    clock.advance(3., 1.)
    probe.on_processed(predecessor, NS(can_run_cuda_graph=True))
    assert not records and not probe._samples[17].processed
    physical = probe.begin_phase(selected, "post_launch_physical")
    clock.advance(2., 1.)
    probe.end_phase(physical)
    clock.advance(4., .5)
    successor = batch(extend=False, request="next")
    started = probe.begin_selection()
    clock.advance(5., 4.)
    probe.on_selected(successor, started, loop="overlap")
    assert probe.begin_forward(successor, None, None) is None
    clock.advance(1., .2)
    successor.forward_iter, successor.launch_ts = 18, clock.wall / 1e9
    probe.on_launch(successor)
    clock.advance(2., .1)
    result = probe.begin_phase(batch(forward=17, request="prefill"), "result_processing")
    clock.advance(1., .4)
    probe.on_processed(result, NS(can_run_cuda_graph=False))
    [row] = records
    adjacent = row["adjacent_transition"]
    assert adjacent["successor_status"] == adjacent["predecessor_status"] == "observed"
    assert adjacent["predecessor"] == {
        "forward_iter": 16, "request_ids": ("previous",), "batch_size": 1,
    }
    assert adjacent["successor_forward_iter"] == 18
    assert adjacent["successor_request_ids"] == ("next",)
    assert adjacent["successor_observer_monotonic_ms"] == 29.
    assert adjacent["successor_observer_thread_cpu_ns"] == 14_000_000
    assert adjacent["phases"]["successor_selection"]["wall_ms"] == 5.
    assert adjacent["phases"]["successor_selection"]["thread_cpu_ms"] == 4.
    assert adjacent["phases"]["predecessor_result_processing"]["wall_ms"] == 3.
    assert "predecessor_result_processing" not in row["phases"]
    assert row["phases"]["run_batch"]["end_thread_cpu_ns"] == 7_100_000
    assert row["phases"]["post_launch_physical"]["end_thread_cpu_ns"] == 9_300_000
    assert row["thread_cpu_clock_domain"].endswith(":thread_time_ns")
    attribution = adjacent["attribution"]
    assert attribution["status"] == "observed"
    assert attribution["wall_ms"] == 15.5
    assert attribution["thread_cpu_ms"] == 6.9
    assert attribution["measured_phase_wall_ms"] == 10.5
    assert attribution["measured_phase_thread_cpu_ms"] == 6.2
    assert attribution["between_phases_wall_ms"] == 5.
    assert attribution["between_phases_thread_cpu_ms"] == .7
    assert sum(part["wall_ms"] for part in attribution["segments"]) == 15.5
    assert sum(part["thread_cpu_ms"] for part in attribution["segments"]) == pytest.approx(6.9)
    assert probe._adjacent is None and not probe._samples


@pytest.mark.parametrize("change", ("forward_gap", "changed_request", "changed_batch", "selection_repeated"))
def test_unknown_successor_never_receives_a_timing_boundary(change):
    records = []
    probe = PrefillTurnoverProbe(lambda _stream, row: records.append(row), every=1)
    selected = batch()
    launch(probe, selected, forward=7)
    successor = batch(extend=False, request="next")
    probe.on_selected(successor, probe.begin_selection(), loop="overlap")
    if change == "changed_request":
        successor.reqs[0].rid = "changed"
    elif change == "changed_batch":
        successor = batch(extend=False, request="next")
    elif change == "selection_repeated":
        probe.on_selected(successor, probe.begin_selection(), loop="overlap")
    successor.forward_iter = 9 if change == "forward_gap" else 8
    probe.on_launch(successor)
    probe.on_processed(
        probe.begin_phase(selected, "result_processing"), NS(can_run_cuda_graph=True),
    )
    [row] = records
    assert row["adjacent_transition"]["successor_status"] == "identity_or_order_unknown"
    assert row["adjacent_transition"]["successor_observer_thread_cpu_ns"] is None
    assert row["adjacent_transition"]["attribution"]["status"] == "run_return_or_successor_unobserved"
    assert probe.counts["adjacent_successor_unknown"] == 1


def test_nonadjacent_or_duplicate_predecessor_remains_explicitly_unknown():
    records = []
    probe = PrefillTurnoverProbe(lambda _stream, row: records.append(row), every=1)
    selected = batch()
    launch(probe, selected, forward=7)
    assert probe.begin_phase(batch(extend=False, forward=5), "result_processing") is None
    predecessor = batch(extend=False, forward=6, request="previous")
    handle = probe.begin_phase(predecessor, "result_processing")
    probe.on_processed(handle, NS())
    assert probe.begin_phase(predecessor, "result_processing") is None
    assert not records and not probe._samples[7].processed
    probe.on_processed(probe.begin_phase(selected, "result_processing"), NS())
    [row] = records
    assert row["adjacent_transition"]["predecessor_status"] == "ambiguous"
    assert row["adjacent_transition"]["successor_status"] == "not_observed_before_result"
    assert row["adjacent_transition"]["successor_observer_monotonic_ms"] is None


def test_consecutive_sampled_prefills_share_one_result_span_without_false_completion(monkeypatch):
    clock, records = Clock(), []
    monkeypatch.setattr(module, "_clock", clock.read)
    probe = PrefillTurnoverProbe(lambda _stream, row: records.append(row), every=1)
    first, second = batch(request="first"), batch(request="second")
    launch(probe, first, forward=11)
    clock.advance(1., .5)
    launch(probe, second, forward=12)
    result = probe.begin_phase(batch(forward=11, request="first"), "result_processing")
    clock.advance(3., 1.)
    probe.on_processed(result, NS())
    assert len(records) == 1 and records[0]["request_ids"] == ("first",)
    assert not probe._samples[12].processed
    next_decode = batch(extend=False, request="decode")
    probe.on_selected(next_decode, probe.begin_selection(), loop="overlap")
    next_decode.forward_iter = 13
    probe.on_launch(next_decode)
    probe.on_processed(probe.begin_phase(second, "result_processing"), NS())
    assert len(records) == 2
    previous_span = records[0]["phases"]["result_processing"]
    adjacent = records[1]["adjacent_transition"]
    assert adjacent["phases"]["predecessor_result_processing"] == previous_span
    assert adjacent["predecessor"]["request_ids"] == ("first",)
    assert adjacent["successor_forward_iter"] == 13
    assert probe._adjacent is None


def test_close_releases_adjacent_tracking_without_inventing_a_successor():
    records = []
    probe = PrefillTurnoverProbe(lambda _stream, row: records.append(row), every=1)
    launch(probe, batch(), forward=11)
    assert probe._adjacent is not None
    probe.close()
    assert probe._adjacent is None
    [row] = records
    assert row["event"] == "prefill_turnover_incomplete"
    assert row["adjacent_transition"]["successor_observer_monotonic_ms"] is None


@pytest.mark.parametrize("change,reason", (
    ("missing", "missing_phases"),
    ("wall_overlap", "invalid_or_overlapping_clock_order"),
    ("cpu_backwards", "invalid_or_overlapping_clock_order"),
    ("outside_boundary", "invalid_or_overlapping_clock_order"),
    ("unknown_predecessor", "predecessor_identity_unknown"),
))
def test_incomplete_or_inconsistent_adjacent_phases_do_not_fabricate_cpu_attribution(change, reason):
    sample = module._Sample(1, {})
    sample.phases = {
        "run_batch": ((0, 0), (10, 5)),
        "war_barrier_and_queue_publish": ((10, 5), (12, 6)),
        "post_launch_physical": ((18, 8), (20, 9)),
    }
    sample.adjacent_phases = {
        "predecessor_result_processing": ((14, 6), (17, 7)),
        "successor_selection": ((22, 10), (24, 12)),
    }
    sample.adjacent_metadata["predecessor_status"] = "observed"
    sample.successor_observed = (30, 14)
    if change == "missing":
        del sample.adjacent_phases["successor_selection"]
    elif change == "wall_overlap":
        sample.phases["post_launch_physical"] = ((16, 8), (20, 9))
    elif change == "cpu_backwards":
        sample.phases["post_launch_physical"] = ((18, 6), (20, 9))
    elif change == "outside_boundary":
        sample.successor_observed = (23, 11)
    else:
        sample.adjacent_metadata["predecessor_status"] = "ambiguous"
    attribution = module._adjacent_attribution(sample)
    assert attribution["status"] == reason
    assert "segments" not in attribution and "between_phases_thread_cpu_ms" not in attribution


@pytest.mark.parametrize("prefill_every,iterations", ((1, 64), (5, 160), (16, 512)))
def test_actual_overlap_loop_exercises_complete_adjacent_attribution(prefill_every, iterations):
    from scripts.benchmark_prefill_turnover_probe import hook, measure

    records = []

    class CapturingProbe(PrefillTurnoverProbe):
        def __init__(self, emit):
            def capture(domain, row):
                records.append(row)
                emit(domain, row)
            super().__init__(capture)

    configured = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    engine = Path(configured) if configured else (
        Path(__file__).resolve().parents[1] / "third_party/sglang-v0.5.20"
    )
    scheduler = hook(engine, f"adjacent_probe_loop_{prefill_every}")
    if not configured and not hasattr(scheduler.Hook, "event_loop_overlap"):
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to the instrumented engine checkout")
    result = measure(
        scheduler, iterations=iterations, prefill_every=prefill_every,
        sampled=True, probe_class=CapturingProbe,
    )
    assert len(records) == result["sample_counts"]["prefill_turnover_sample"]
    assert len(records) > 1
    for record in records:
        adjacent = record["adjacent_transition"]
        if record["forward_iter"] == 1:
            assert adjacent["attribution"]["status"] == "predecessor_identity_unknown"
            continue
        attribution = adjacent["attribution"]
        assert adjacent["predecessor"]["forward_iter"] == record["forward_iter"] - 1
        assert adjacent["successor_forward_iter"] == record["forward_iter"] + 1
        assert attribution["status"] == "observed"
        assert attribution["wall_ms"] == pytest.approx(
            sum(part["wall_ms"] for part in attribution["segments"]),
        )
        assert attribution["thread_cpu_ms"] == pytest.approx(
            sum(part["thread_cpu_ms"] for part in attribution["segments"]),
        )
        assert attribution["between_phases_wall_ms"] >= 0
        assert attribution["between_phases_thread_cpu_ms"] >= 0
