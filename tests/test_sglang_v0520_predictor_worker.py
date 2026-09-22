"""Bounded process worker projects only supported demand heads."""

from __future__ import annotations

from queue import Queue
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel,
    LocalFrontierFeatures,
)
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime import sglang_v0520_predictor_worker as worker_module
from beliefkv.runtime.sglang_v0520_predictor_worker import (
    NativePredictorWorker,
    predict_batch,
)


def key(name: str):
    return PrefillCandidateKey(name, "wf", name, f"ctx-{name}", 0, 0)


def batch(name: str):
    return ((key(name), LocalFrontierFeatures(name, "ready"), 7.0),)


@pytest.fixture
def fake_context(monkeypatch):
    class FakeQueue(Queue):
        def cancel_join_thread(self):
            pass

        def close(self):
            self.closed = True

    class FakeProcess:
        def __init__(self, **kwargs):
            self.alive = False
            self.stuck = False
            self.joins = []
            self.terminated = False
            self.killed = False

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            if not self.stuck:
                self.alive = False

        def kill(self):
            self.killed = True
            self.alive = False

        def join(self, timeout=None):
            self.joins.append(timeout)
            assert timeout is not None

    class FakeContext:
        def Queue(self, maxsize):
            return FakeQueue(maxsize=maxsize)

        def Process(self, **kwargs):
            self.process = FakeProcess(**kwargs)
            return self.process

    context = FakeContext()
    monkeypatch.setattr(worker_module.mp, "get_context", lambda method: context)
    return context


def test_predict_batch_skips_unsupported_and_ood_heads():
    class Model:
        def predict(self, features):
            return NS(
                head_support={
                    "next_output_demand":
                        "unavailable" if features.invocation_id == "missing" else "pooled"
                },
                next_output_tokens=NS(
                    values=(5.0,),
                    quantile=lambda q: 5.0 if features.invocation_id != "ood" else 0,
                ),
                ood_reasons=(
                    ("unsupported_context",)
                    if features.invocation_id == "ood" else ()
                ),
            )

    items = tuple(
        (key(name), LocalFrontierFeatures(name, "ready"), 7.0)
        for name in ("ok", "missing", "ood")
    )
    assert predict_batch(items, model=Model()) == ((key("ok"), 5, 7.0),)


def test_only_newest_batch_is_dispatched_and_published(fake_context):
    worker = NativePredictorWorker("unused", "a" * 64)
    try:
        worker.submit(batch("old"))
        worker.submit(batch("replaced"))
        worker.submit(batch("new"))
        assert worker._input_queue.qsize() == 1
        assert worker._next == batch("new")
        assert worker._input_queue.get_nowait() == (1, batch("old"))

        # The old result has completed, but must not be issued after newer submits.
        worker._output_queue.put_nowait((1, ((key("old"), 4, 7.0),)))
        assert worker.poll() == ()
        assert worker._input_queue.get_nowait() == (3, batch("new"))
        worker._output_queue.put_nowait((3, ((key("new"), 6, 7.0),)))
        hints = worker.poll()
        assert len(hints) == 1
        assert hints[0].key == key("new")
        assert hints[0].next_output_tokens == 6
        assert hints[0].predictor_sha256 == "a" * 64
        assert worker.failure_count == 0
    finally:
        worker.close()


def test_worker_timeout_fails_closed_even_with_a_ready_result(fake_context):
    worker = NativePredictorWorker("unused", "a" * 64, timeout_s=1.0)
    worker.submit(batch("old"))
    worker._output_queue.put_nowait((1, ((key("old"), 4, 7.0),)))
    worker._started_at = time.monotonic() - 2

    assert worker.poll() == ()
    assert worker.disabled
    assert worker.failure_count == 1
    assert fake_context.process.terminated
    worker.submit(batch("new"))
    assert worker.poll() == ()
    assert worker.failure_count == 1
    worker.close()


def test_worker_crash_discards_ready_result_and_pending_batch(fake_context):
    worker = NativePredictorWorker("unused", "a" * 64)
    worker.submit(batch("old"))
    worker.submit(batch("new"))
    worker._output_queue.put_nowait((1, ((key("old"), 4, 7.0),)))
    fake_context.process.alive = False

    assert worker.poll() == ()
    assert worker.disabled
    assert worker.failure_count == 1
    assert worker._next is None
    assert worker.poll() == ()
    worker.close()


def test_close_is_bounded_when_child_ignores_termination(fake_context):
    worker = NativePredictorWorker("unused", "a" * 64)
    worker.submit(batch("old"))
    fake_context.process.stuck = True

    worker.close()
    assert fake_context.process.terminated
    assert fake_context.process.killed
    assert fake_context.process.joins == [0.1, 0.1]
    assert worker._input_queue.closed
    assert worker._output_queue.closed
    worker.close()
    with pytest.raises(RuntimeError, match="closed"):
        worker.submit(batch("new"))
    assert worker.poll() == ()


def test_unexpected_result_sequence_fails_closed(fake_context):
    worker = NativePredictorWorker("unused", "a" * 64)
    worker.submit(batch("old"))
    worker._output_queue.put_nowait((2, ((key("old"), 4, 7.0),)))

    assert worker.poll() == ()
    assert worker.disabled
    assert worker.failure_count == 1
    worker.close()


def test_spawned_worker_loads_model_without_blocking_scheduler(tmp_path):
    path = tmp_path / "empty.json"
    FrontierBeliefModel().save(path)
    worker = NativePredictorWorker(str(path), "a" * 64)
    try:
        worker.submit(((key("a"), LocalFrontierFeatures("a", "ready"), 3.0),))
        assert worker.poll() == ()
        deadline = time.monotonic() + 10
        while worker._active_sequence is not None and time.monotonic() < deadline:
            worker.poll()
            time.sleep(0.01)
        assert worker._active_sequence is None
        assert worker.failure_count == 0
        assert not worker.disabled
    finally:
        worker.close()


def test_spawned_worker_fails_closed_when_child_exits(tmp_path):
    path = tmp_path / "empty.json"
    FrontierBeliefModel().save(path)
    worker = NativePredictorWorker(str(path), "a" * 64)
    try:
        worker.submit(batch("old"))
        worker.submit(batch("new"))
        worker._process.terminate()
        worker._process.join(timeout=2)
        assert not worker._process.is_alive()
        assert worker.poll() == ()
        assert worker.disabled
        assert worker.failure_count == 1
        assert worker._next is None
    finally:
        worker.close()
