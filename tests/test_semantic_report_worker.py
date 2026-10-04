from pathlib import Path
from collections import OrderedDict
import select
import time

import pytest
from types import SimpleNamespace as NS
from unittest.mock import Mock

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.semantic_report_worker import SemanticReportInput, SemanticReportWorker


ARTIFACT = Path(__file__).resolve().parents[1] / (
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
