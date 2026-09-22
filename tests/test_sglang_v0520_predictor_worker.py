"""Bounded process worker projects only supported demand heads."""

from __future__ import annotations

from queue import Queue
import select
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.predictor.structured_frontier import (
    EmpiricalDistribution,
    FrontierBeliefModel,
    LocalFrontierFeatures,
    WaitBelief,
    WaitBeliefKind,
)
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_prediction import (
    MAX_TOOL_WAIT_MS,
    NativeToolWaitHint,
    NativeJoinWaitHint,
)
from beliefkv.runtime import sglang_v0520_predictor_worker as worker_module
from beliefkv.runtime.sglang_v0520_predictor_worker import (
    NativePredictorWorker,
    predict_batch,
    predict_tool_wait_batch,
    predict_join_wait_batch,
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


def test_predict_tool_wait_batch_uses_trained_calibrated_residual_head():
    def row(name, duration, *, split="development"):
        return {
            "schema_version": 2,
            "decision_id": name,
            "episode_group_id": f"episode-{name}",
            "split": split,
            "trigger_kind": "tool_start",
            "trigger_attributes": {"tool_family": "shell"},
            "invocations": [{
                "invocation_id": "worker",
                "agent_definition_id": "worker",
                "state": "wait_tool",
                "active_tool_family": "shell",
                "active_tool_elapsed_ms": 0,
                "context_tokens": 4096,
                "current_sequence_tokens": 4096,
                "active_tool_count": 1,
                "backend_pressure": "active_family:1",
            }],
            "labels": [{
                "invocation_id": "worker",
                "next_boundary_kind": "tool_end",
                "next_boundary_status": "success",
                "next_boundary_delay_ms": duration,
                "reentry_prompt_delta_tokens": 32,
                "censored": False,
            }],
        }

    model = FrontierBeliefModel()
    model.fit([
        row(f"train-{n}", duration)
        for n, duration in enumerate((10, 100, 200, 300))
    ])
    features = LocalFrontierFeatures(
        "worker", "wait_tool", agent_definition_id="worker",
        tool_family="shell", current_sequence_tokens=4096,
        active_tool_count=1, backend_pressure="active_family:1",
    )
    item = ((key("worker"), features, 7.0),)
    assert predict_tool_wait_batch(item, model=model) == ()
    model.calibrate([row("calibration", 150, split="calibration")])
    prediction = model.predict(features)
    assert prediction.wait_belief.available
    assert prediction.head_support["tool_wait"] != "unavailable"
    quantiles = tuple(
        prediction.wait_belief.residual_duration.quantile(q)
        for q in (0.1, 0.5, 0.9)
    )
    assert predict_tool_wait_batch(item, model=model) == (
        (key("worker"), *quantiles, 7.0),
    )
    assert predict_tool_wait_batch(
        ((key("worker"), LocalFrontierFeatures("worker", "wait_join"), 7.0),),
        model=model,
    ) == ()


def test_join_prediction_uses_child_completion_not_parent_structural_wait():
    class Model:
        def predict(self, features):
            duration = 20.0 if features.invocation_id == "a" else 40.0
            return NS(
                remaining_to_return_ms=EmpiricalDistribution(
                    (duration, duration + 10), (0.5, 0.5), 3
                ),
                head_support={"child_completion": "exact"},
                calibration_coverage=0.9,
                ood_reasons=(),
            )

    children = tuple(
        (name, LocalFrontierFeatures(name, "running_llm"), float(index), 0)
        for index, name in enumerate(("a", "b"), 1)
    )
    item = (key("parent"), 7.0, "join", "all", ("a", "b"), (), children)
    result = predict_join_wait_batch((item,), model=Model())
    assert result == ((
        key("parent"), 7.0, "join", "all", ("a", "b"),
        (("a", 1.0, "running_llm", 0), ("b", 2.0, "running_llm", 0)),
        40.0, 40.0, 50.0,
    ),)
    assert predict_join_wait_batch((
        (key("parent"), 7.0, "join", "any", ("a", "b"), (), children),
    ), model=Model())[0][-3:] == (20.0, 20.0, 30.0)
    assert predict_join_wait_batch((
        (key("parent"), 7.0, "join", "all", ("a", "b"), (), children[:1]),
    ), model=Model()) == ()


def test_join_wait_worker_preserves_causal_priority_and_provenance(fake_context):
    worker = NativePredictorWorker("unused", "a" * 64)
    try:
        worker.submit(batch("old"))
        item = (key("parent"), 7.0, "join", "all", ("a",), (),
                (("a", LocalFrontierFeatures("a", "running_llm"), 3.0, 0),))
        worker.submit_join_wait((item,))
        worker.submit(batch("later"))
        worker._input_queue.get_nowait()
        worker._output_queue.put_nowait((1, ()))
        assert worker.poll() == ()
        sequence, kind, submitted = worker._input_queue.get_nowait()
        assert kind == "join_wait" and submitted == (item,)
        worker._output_queue.put_nowait((sequence, kind, (
            (key("parent"), 7.0, "join", "all", ("a",),
             (("a", 3.0, "running_llm", 0),),
             10.0, 20.0, 30.0),
        )))
        (hint,) = worker.poll()
        assert isinstance(hint, NativeJoinWaitHint)
        assert hint.child_revisions == (("a", 3.0, "running_llm", 0),)
    finally:
        worker.close()


@pytest.mark.parametrize("variant", [
    "unavailable", "structural", "ood", "wait_ood", "uncalibrated",
    "wrong_kind", "missing", "unordered", "negative", "infinite", "excessive",
])
def test_tool_wait_rejects_unsupported_or_invalid_residuals(variant):
    quantiles = {
        "unordered": (30.0, 20.0, 40.0),
        "negative": (-1.0, 20.0, 40.0),
        "infinite": (10.0, float("inf"), 40.0),
        "excessive": (10.0, 20.0, MAX_TOOL_WAIT_MS + 1),
    }.get(variant, (10.0, 20.0, 40.0))
    wait = WaitBelief(
        kind=WaitBeliefKind.JOIN if variant == "wrong_kind" else WaitBeliefKind.TOOL,
        support_level="exact",
        residual_duration=EmpiricalDistribution((10.0, 20.0, 40.0), (0.3, 0.3, 0.4), 3),
        ood_reasons=("unknown_tool",) if variant == "wait_ood" else (),
    )
    if variant == "missing":
        wait = None
    if variant == "structural":
        wait = WaitBelief(kind=WaitBeliefKind.JOIN, support_level="structural",
                          dependency_composed=True)

    class Model:
        def predict(self, features):
            if wait is not None and variant in {"unordered", "negative", "infinite", "excessive"}:
                residual = NS(quantile=lambda q: dict(zip((0.1, 0.5, 0.9), quantiles))[q])
                selected_wait = NS(
                    kind=WaitBeliefKind.TOOL, dependency_composed=False,
                    available=True, ood_reasons=(), residual_duration=residual,
                )
            else:
                selected_wait = wait
            return NS(
                calibration_coverage=0.0 if variant == "uncalibrated" else 0.9,
                head_support={"tool_wait": "unavailable" if variant == "unavailable"
                              else "structural" if variant == "structural" else "exact"},
                ood_reasons=("unsupported",) if variant == "ood" else (),
                wait_belief=selected_wait,
            )

    assert predict_tool_wait_batch(batch("a"), model=Model()) == ()


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


def test_tool_wait_preempts_pending_admission_and_keeps_latest_wait(fake_context):
    worker = NativePredictorWorker("unused", "a" * 64)
    try:
        worker.submit(batch("inflight"))
        worker.submit(batch("admission"))
        worker.submit_tool_wait(batch("wait-old"))
        worker.submit(batch("admission-later"))
        worker.submit_tool_wait(batch("wait-new"))
        assert worker._input_queue.get_nowait() == (1, batch("inflight"))
        worker._output_queue.put_nowait((1, ((key("inflight"), 4, 7.0),)))
        assert worker.poll() == ()
        assert worker._input_queue.get_nowait() == (4, "tool_wait", batch("wait-new"))
        worker._output_queue.put_nowait(
            (4, "tool_wait", ((key("wait-new"), 10.0, 20.0, 30.0, 7.0),))
        )
        (hint,) = worker.poll()
        assert isinstance(hint, NativeToolWaitHint)
        assert (hint.key, hint.wait_p10_ms, hint.wait_p50_ms, hint.wait_p90_ms) == (
            key("wait-new"), 10.0, 20.0, 30.0,
        )
        assert hint.invocation_revision_ts_ms == 7.0
        assert hint.predictor_sha256 == "a" * 64
        assert hint.live(key("wait-new"), now_ms=hint.issued_monotonic_ms)
        assert worker.failure_count == 0
    finally:
        worker.close()


def test_tool_wait_active_result_survives_newer_admission(fake_context):
    worker = NativePredictorWorker("unused", "a" * 64)
    try:
        worker.submit_tool_wait(batch("wait"))
        assert worker._input_queue.get_nowait() == (1, "tool_wait", batch("wait"))
        worker.submit(batch("new"))
        worker._output_queue.put_nowait(
            (1, "tool_wait", ((key("wait"), 10.0, 20.0, 30.0, 7.0),))
        )
        (hint,) = worker.poll()
        assert isinstance(hint, NativeToolWaitHint)
        assert hint.key == key("wait")
        assert worker._input_queue.get_nowait() == (2, batch("new"))
        worker._output_queue.put_nowait((2, ((key("new"), 4, 7.0),)))
        assert worker.poll()[0].next_output_tokens == 4
    finally:
        worker.close()


def test_tool_wait_timeout_and_mismatched_response_fail_closed(fake_context):
    worker = NativePredictorWorker("unused", "a" * 64, timeout_s=1.0)
    worker.submit_tool_wait(batch("wait"))
    worker._output_queue.put_nowait(
        (1, "tool_wait", ((key("wait"), 10.0, 20.0, 30.0, 7.0),))
    )
    worker._started_at = time.monotonic() - 2
    assert worker.poll() == ()
    assert worker.disabled and worker.failure_count == 1
    worker.close()

    worker = NativePredictorWorker("unused", "a" * 64)
    worker.submit_tool_wait(batch("wait"))
    worker._output_queue.put_nowait((1, ((key("wait"), 4, 7.0),)))
    assert worker.poll() == ()
    assert worker.disabled and worker.failure_count == 1
    worker.close()


def test_tool_wait_submission_is_bounded_and_closed_worker_rejects(fake_context):
    worker = NativePredictorWorker("unused", "a" * 64)
    with pytest.raises(ValueError, match="exceeds bound"):
        worker.submit_tool_wait(batch("wait") * 9)
    assert worker._input_queue.empty()
    worker.close()
    with pytest.raises(RuntimeError, match="closed"):
        worker.submit_tool_wait(batch("wait"))


def test_worker_exposes_output_reader_fd_until_closed_or_failed(fake_context):
    worker = NativePredictorWorker("unused", "a" * 64)
    try:
        assert worker.fileno() is None
        worker._output_queue._reader = NS(fileno=lambda: 42)
        assert worker.fileno() == 42
        worker._output_queue._reader = NS(fileno=lambda: -1)
        assert worker.fileno() is None
        worker._output_queue._reader = NS(fileno=lambda: 42)
        worker.submit_tool_wait(batch("wait"))
        fake_context.process.alive = False
        assert worker.fileno() is None
        assert worker.poll() == ()
        assert worker.fileno() is None
    finally:
        worker.close()
    assert worker.fileno() is None


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
        assert isinstance(worker.fileno(), int)
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


def test_spawned_tool_wait_completion_wakes_output_fd(tmp_path):
    path = tmp_path / "empty.json"
    FrontierBeliefModel().save(path)
    worker = NativePredictorWorker(str(path), "a" * 64)
    try:
        fd = worker.fileno()
        assert isinstance(fd, int)
        worker.submit_tool_wait(batch("wait"))
        readable, _, _ = select.select((fd,), (), (), 10)
        assert readable == [fd]
        assert worker.poll() == ()
        assert worker._active_sequence is None
        assert worker.failure_count == 0
    finally:
        worker.close()
    assert worker.fileno() is None


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
