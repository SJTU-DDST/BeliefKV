import threading
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.runtime import workflow_work_worker as module
from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker
from tests.test_workflow_work import event
from tests.test_workflow_work_distribution import distribution_raw, root, start


@pytest.fixture
def waiting_worker(monkeypatch):
    clock = [1000.]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    worker = WorkflowWorkWorker("", "", model=WorkflowWorkModel(distribution_raw()), autostart=False)
    worker.submit("wf", "event", start())
    worker.submit("wf", "event", root())
    worker.submit("wf", "event", event(
        Kind.LLM_SUBMIT, invocation_id="root", context_id="c", attributes={"request_id": "r"},
    ))
    worker._thread = threading.Thread(target=worker._run, daemon=True)
    worker._thread.start()
    deadline = time.perf_counter() + 5.
    while worker.prediction("wf") is None and time.perf_counter() < deadline:
        if worker.error:
            pytest.fail(worker.error)
        threading.Event().wait(.001)
    prediction = worker.prediction("wf")
    assert prediction is not None and prediction.support == "calibrated_distribution"
    try:
        yield worker, clock, prediction
    finally:
        worker.close()


def test_live_ready_work_keeps_unchanged_prediction_without_another_inference(waiting_worker):
    worker, clock, original = waiting_worker
    inferred = worker.counts["inferences"]
    assert worker._progress["wf"].invocations["root"][0] == "ready"
    clock[0] = original.expires_at + 1.
    current = worker.prediction("wf")
    assert current is not None
    assert current.probabilities == original.probabilities
    assert current.updated_at == original.updated_at
    assert current.revision == original.revision
    assert current.expires_at > clock[0]
    assert worker.counts["inferences"] == inferred
    assert worker.counts["unchanged_input_validity_revalidations"] == 1
    assert worker.prediction("wf") is current


def test_unconsumed_observations_cannot_extend_old_validity(waiting_worker):
    worker, clock, original = waiting_worker
    clock[0] = original.expires_at + 1.
    with worker._condition:
        worker.submit("wf", "generated", "r", 128)
        assert worker.prediction("wf") is None
        assert worker.counts["unchanged_input_validity_revalidations"] == 0


def test_refresh_never_waits_for_the_observation_lock(waiting_worker):
    worker, clock, original = waiting_worker
    clock[0] = original.expires_at + 1.
    held, release, timeout = threading.Event(), threading.Event(), threading.Event()

    def holder():
        with worker._condition:
            held.set()
            if not release.wait(3.):
                timeout.set()

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(3.)
        assert worker.prediction("wf") is None
        assert not timeout.is_set()
    finally:
        release.set()
        thread.join(3.)
    assert not thread.is_alive()


def test_worker_failure_and_workflow_close_still_revoke_cached_service(waiting_worker):
    worker, clock, original = waiting_worker
    clock[0] = original.expires_at + 1.
    worker.error = "worker_failed"
    assert worker.prediction("wf") is None
    worker.error = ""
    worker.submit("wf", "event", event(Kind.WORKFLOW_END))
    assert worker.prediction("wf") is None


def test_structural_invalidation_still_wins_over_unchanged_work(waiting_worker):
    from beliefkv.core.events import RelationType

    worker, clock, original = waiting_worker
    clock[0] = original.expires_at + 1.
    with worker._condition:
        worker.submit("wf", "event", event(
            Kind.INVOCATION_CREATE, invocation_id="child", parent_invocation_id="root",
            relation_type=RelationType.SPAWN,
        ))
        assert worker.prediction("wf") is None


def test_dormant_manual_worker_without_a_live_producer_does_not_extend_validity(monkeypatch):
    clock = [1000.]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    worker = WorkflowWorkWorker("", "", model=WorkflowWorkModel(distribution_raw()), autostart=False)
    try:
        worker.submit("wf", "event", start())
        worker.submit("wf", "event", root())
        worker.process_pending()
        initial = worker.prediction("wf")
        clock[0] = initial.expires_at + 1.
        assert worker.prediction("wf") is None
    finally:
        worker.close()
