from types import SimpleNamespace as NS

from beliefkv.runtime.restore_wait_probe import RestoreWaitProbe


class Device:
    def __init__(self):
        self.clock = 0.
        self.ready = False
        self.capturing = False
        self.event_count = 0

    def current_stream(self):
        return self

    def is_current_stream_capturing(self):
        return self.capturing

    def Event(self, *, enable_timing):
        assert enable_timing
        self.event_count += 1
        device = self

        class Event:
            def record(self, *, stream):
                assert stream is device
                self.clock = device.clock

            def query(self):
                return device.ready

            def elapsed_time(self, finish):
                assert device.ready
                return finish.clock - self.clock

        return Event()

    def wait(self, layer):
        self.clock += layer + 1.


def batch(*, extend=True, consumer=0):
    return NS(
        forward_mode=NS(is_extend=lambda: extend),
        hicache_consumer_index=consumer, forward_iter=1, reqs=[NS(rid="r")],
    )


def test_measures_unique_native_layer_waits_without_blocking_or_double_counting():
    device = Device()
    probe = RestoreWaitProbe(every=1)
    sample = probe.begin(batch(), device)
    sample.wait(device, 0, 0)
    sample.wait(device, 0, 0)
    sample.wait(device, 1, 0)
    assert device.clock == 4.
    assert device.event_count == 4
    assert probe.poll() == []
    assert probe.snapshot()["pending_samples"] == 1
    device.ready = True
    row, = probe.poll()
    assert row["gpu_layer_dependency_wait_ms"] == 3.
    assert row["gpu_layer_dependency_wait_max_ms"] == 2.
    assert row["request_ids"] == ["r"]
    assert probe.snapshot()["pending_samples"] == 0


def test_probe_skips_capture_and_decode_but_still_honors_native_waits():
    device = Device()
    probe = RestoreWaitProbe(every=1)
    assert probe.begin(batch(extend=False), device) is None
    assert probe.begin(batch(consumer=-1), device) is None
    sample = probe.begin(batch(), device)
    device.capturing = True
    sample.wait(device, 0, 0)
    assert device.clock == 1.
    assert device.event_count == 0
    assert probe.poll() == []
    assert probe.snapshot()["counts"]["samples_without_python_layer_wait"] == 1


def test_sample_queue_is_bounded_without_stopping_execution():
    device = Device()
    probe = RestoreWaitProbe(every=2, limit=1)
    sample = probe.begin(batch(), device)
    sample.wait(device, 0, 0)
    assert probe.begin(batch(), device) is None
    assert probe.begin(batch(), device) is None
    assert probe.snapshot()["counts"]["bounded_queue_skips"] == 1
    device.ready = True
    probe.poll()
    assert probe.begin(batch(), device) is None
    assert probe.begin(batch(), device) is not None
