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
