from pathlib import Path
from collections import OrderedDict
import multiprocessing as mp
import os
import select
import time

import pytest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.semantic_report_worker import (
    SemanticReportInput, SemanticReportReply, SemanticReportWorker,
)


ARTIFACT = Path(os.environ["BELIEFKV_TEST_SEMANTIC_ARTIFACT"]) if (
    "BELIEFKV_TEST_SEMANTIC_ARTIFACT" in os.environ
) else Path(__file__).resolve().parents[1] / (
    "experiments/models/child_semantic_work_stage2_adapted_20260930_v1/"
    "semantic_event_calibrated.json"
)


def test_worker_prioritizes_fresh_notice_frames_and_cancels_stale_pending():
    worker = object.__new__(SemanticReportWorker)
    worker.disabled, worker.ready, worker._active = False, True, False
    items = []
    for index, (when, notice) in enumerate(((100., False), (300., False), (200., True))):
        key = PrefillCandidateKey(str(index), "wf", "child", "ctx", 1, 0, "s", 1)
        items.append(SemanticReportInput(key, when, 20, 128, "Report.", notice, 100, 1, 1))
    worker._pending = OrderedDict((item.key.request_id, item) for item in items)
    worker._inputs = NS(put_nowait=Mock())
    worker.cancel("0")
    worker._dispatch()
    batch = worker._inputs.put_nowait.call_args[0][0]
    assert [item.key.request_id for item in batch] == ["2", "1"]
    assert not worker._pending


@pytest.fixture
def queue_worker():
    worker = object.__new__(SemanticReportWorker)
    context = mp.get_context("spawn")
    worker._inputs = context.Queue(maxsize=1)
    worker._outputs = context.Queue(maxsize=2)
    worker._result_poller = select.poll()
    worker._result_poller.register(worker._outputs._reader, select.POLLIN)
    exit_reader, exit_writer = context.Pipe(duplex=False)
    worker._exit_sentinel = exit_reader.fileno()
    worker._result_poller.register(worker._exit_sentinel, select.POLLIN)
    worker._test_exit_writer = exit_writer
    worker._process = NS(is_alive=Mock(return_value=True), join=Mock())
    worker._pending = OrderedDict()
    worker._active = False
    worker._started = time.monotonic()
    worker.ready = False
    worker.disabled = False
    worker.error = ""
    worker.dropped = 0
    try:
        yield worker
    finally:
        for queue in (worker._inputs, worker._outputs):
            queue.close()
            queue.join_thread()
        exit_reader.close()
        exit_writer.close()


def test_empty_poll_then_ready_dispatches_pending_input_without_delay(queue_worker):
    worker = queue_worker
    key = PrefillCandidateKey("r", "wf", "child", "ctx", 1, 0, "s", 1)
    item = SemanticReportInput(key, 100., 20, 128, "Report.", True, 100, 1, 1)
    worker.submit(item)
    assert worker.poll() == ()
    assert not worker.ready and not worker._active
    worker._outputs.put(("ready", ()))
    assert select.select([worker.fileno()], [], [], 1)[0]
    assert worker.poll() == ()
    assert worker.ready and worker._active
    assert worker._inputs.get(timeout=1) == (item,)
    assert not worker._pending


def test_reply_poll_preserves_forecast_and_dispatches_next_batch(queue_worker):
    worker = queue_worker
    worker.ready = True
    key = PrefillCandidateKey("r", "wf", "child", "ctx", 1, 0, "s", 1)
    item = SemanticReportInput(key, 100., 20, 128, "Report.", True, 100, 1, 1)
    worker.submit(item)
    assert worker._inputs.get(timeout=1) == (item,)
    newer = SemanticReportInput(key, 200., 40, 256, "Report complete.", True, 100, 1, 1)
    worker.submit(newer)
    reply = SemanticReportReply(item, .9, 10., 20., 40., 0.)
    worker._outputs.put(("result", (reply,)))
    assert select.select([worker.fileno()], [], [], 1)[0]
    assert worker.poll() == (reply,)
    assert worker._active
    assert worker._inputs.get(timeout=1) == (newer,)
    assert worker.poll() == ()
    assert not worker.disabled


@pytest.mark.parametrize("failure", ("exit", "startup_timeout", "inference_timeout"))
def test_empty_result_queue_does_not_delay_worker_failure_detection(queue_worker, failure):
    worker = queue_worker
    worker._started = 0.
    if failure == "exit":
        worker._test_exit_writer.send_bytes(b"exit")
        expected = "semantic worker exited"
    else:
        if failure == "inference_timeout":
            worker.ready = worker._active = True
        expected = "semantic worker timed out"
    with patch("beliefkv.runtime.semantic_report_worker.time.monotonic", return_value=61.):
        assert worker.poll() == ()
    assert worker.disabled
    assert worker.error == expected
    if failure == "exit":
        worker._process.join.assert_called_once_with(timeout=0)


@pytest.mark.parametrize("inflight", (False, True))
def test_empty_poll_uses_exit_readiness_without_process_liveness_syscall(queue_worker, inflight):
    worker = queue_worker
    worker.ready, worker._active = True, inflight
    assert worker.poll() == ()
    worker._process.is_alive.assert_not_called()


def exit_on_release(started, release):
    started.set()
    release.wait(timeout=10)


@pytest.mark.parametrize("inflight", (False, True))
@pytest.mark.parametrize("queued_reply", (False, True))
def test_real_process_exit_wins_over_queued_result_without_delaying_failure(
    queue_worker, inflight, queued_reply,
):
    worker = queue_worker
    context = mp.get_context("spawn")
    started, release = context.Event(), context.Event()
    process = context.Process(target=exit_on_release, args=(started, release))
    process.start()
    worker._result_poller.unregister(worker._exit_sentinel)
    worker._process = process
    worker._exit_sentinel = process.sentinel
    worker._result_poller.register(worker._exit_sentinel, select.POLLIN)
    worker.ready, worker._active = True, inflight
    try:
        assert started.wait(timeout=5)
        if queued_reply:
            worker._outputs.put(("result", ("discard-after-exit",)))
            assert select.select([worker._outputs._reader], [], [], 1)[0]
        release.set()
        process.join(timeout=5)
        assert not process.is_alive()
        assert worker.poll() == ()
        assert worker.disabled and worker.error == "semantic worker exited"
    finally:
        release.set()
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        process.close()


@pytest.mark.skipif(not ARTIFACT.exists(), reason="local pinned encoder artifact required")
def test_process_worker_loads_pinned_model_and_wakes_without_scheduler_inference():
    worker = SemanticReportWorker(str(ARTIFACT))
    key = PrefillCandidateKey("r", "wf", "child", "ctx", 1, 0, "s", 1)
    item = SemanticReportInput(
        key, time.monotonic() * 1000, 20, 128,
        "The evidence is complete. The report is ready.",
        False, 0, 2, 3,
    )
    try:
        worker.submit(item)
        deadline = time.monotonic() + 30
        replies = ()
        while time.monotonic() < deadline and not replies and not worker.disabled:
            fd = worker.fileno()
            assert fd is not None
            select.select([fd], [], [], .1)
            replies = worker.poll()
        assert not worker.disabled, worker.error
        assert worker.ready
        assert len(replies) == 1
        assert replies[0].observation.key == key
        assert 0 <= replies[0].final_score <= 1
        assert replies[0].lower_tokens <= replies[0].middle_tokens <= replies[0].upper_tokens
    finally:
        worker.close()
    assert not worker._process.is_alive()
