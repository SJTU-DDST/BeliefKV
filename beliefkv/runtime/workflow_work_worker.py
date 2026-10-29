"""Coalesced CPU predictions; scheduling never waits for the worker."""

from collections import Counter, deque
from dataclasses import dataclass
import threading
import time

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.predictor.workflow_work import (
    WorkflowProgress, WorkflowWorkModel, task_from_prompt_tail, task_requirements,
)


@dataclass(frozen=True)
class WorkPrediction:
    probabilities: tuple
    bucket: int
    revision: int
    updated_at: float
    evidence: str


class WorkflowWorkWorker:
    interval_s = .2
    queue_limit = 4096
    workflow_limit = 256

    def __init__(self, path, digest, *, tokenizer_path=None, model=None, autostart=True):
        self.model = model if model is not None else WorkflowWorkModel.load(path, digest)
        self.digest = digest
        self.tokenizer_path = tokenizer_path
        self._tokenizer = None
        self._condition = threading.Condition()
        self._queue = deque()
        self._predictions = {}
        self._closed_workflows = set()
        self._progress = {}
        self._projections = {}
        self._dirty = set()
        self._revisions = Counter()
        self._submitted_revisions = Counter()
        self._pending_since = {}
        self.counts = Counter()
        self.error = ""
        self._stopped = False
        self._thread = None
        if autostart:
            self._thread = threading.Thread(target=self._run, name="workflow-work-cpu", daemon=True)
            self._thread.start()

    def submit(self, workflow, action, *args):
        with self._condition:
            if self._stopped or self.error or workflow in self._closed_workflows:
                return
            if action == "event" and args[0].kind is Kind.WORKFLOW_END:
                self._closed_workflows.add(workflow)
                self._predictions.pop(workflow, None)
            if len(self._queue) >= self.queue_limit:
                self.error = "observation_queue_overflow"
                self._predictions.clear()
                self.counts["overflow_abstentions"] += 1
                return
            self._queue.append((workflow, action, args))
            self._submitted_revisions[workflow] += 1
            self._pending_since.setdefault(workflow, time.monotonic())
            self.counts["submitted"] += 1
            self._condition.notify()

    def prediction(self, workflow):
        if self.error or workflow in self._closed_workflows:
            return None
        if (since := self._pending_since.get(workflow)) is not None and time.monotonic() - since > 2.:
            return None
        return self._predictions.get(workflow)

    def bucket(self, workflow, now):
        prediction = self.prediction(workflow)
        return 1 if prediction is None else prediction.bucket

    def _consume(self, rows):
        for workflow, action, args in rows:
            if action == "event" and args[0].kind is Kind.WORKFLOW_END:
                self._progress.pop(workflow, None)
                self._projections.pop(workflow, None)
                self._revisions.pop(workflow, None)
                self._pending_since.pop(workflow, None)
                self._dirty.discard(workflow)
                continue
            if workflow in self._closed_workflows:
                continue
            if workflow not in self._progress and len(self._progress) >= self.workflow_limit:
                self.counts["workflow_limit_abstentions"] += 1
                continue
            progress = self._progress.setdefault(workflow, WorkflowProgress())
            if action == "event":
                progress.event(args[0])
                if args[0].kind is Kind.WORKFLOW_START:
                    text = args[0].attributes.get("workflow_task_instructions", "")
                    if isinstance(text, str):
                        progress.text = task_requirements(text)
            elif action == "task":
                tokens, tokenizer = args
                if not progress.text:
                    if tokenizer is None and self.tokenizer_path:
                        if self._tokenizer is None:
                            from transformers import AutoTokenizer

                            self._tokenizer = AutoTokenizer.from_pretrained(
                                self.tokenizer_path, local_files_only=True, trust_remote_code=False,
                            )
                        tokenizer = self._tokenizer
                    progress.text = task_from_prompt_tail(tokens, tokenizer)
            elif action == "started":
                progress.started(*args)
            elif action == "generated":
                progress.generated(*args)
            elif action == "completed":
                progress.completed(*args)
            else:
                raise ValueError("unknown workflow progress operation")
            self._revisions[workflow] += 1
            self._dirty.add(workflow)

    def process_pending(self):
        """Also used by deterministic CPU lifecycle checks, without a live thread."""
        with self._condition:
            rows = tuple(self._queue)
            self._queue.clear()
        self._consume(rows)
        for workflow in tuple(self._dirty):
            if self._stopped:
                return
            self._dirty.discard(workflow)
            if workflow in self._closed_workflows:
                continue
            progress = self._progress[workflow]
            if workflow not in self._projections:
                if not progress.text:
                    self.counts["task_unavailable"] += 1
                    continue
                self._projections[workflow] = self.model.task_projection(progress.text)
                self.counts["task_encodings"] += 1
            projection = self._projections[workflow]
            if projection is None:
                self.counts["unsupported_task"] += 1
                continue
            wall, cpu = time.perf_counter(), time.thread_time()
            probabilities = self.model.predict(projection, progress.features())
            best = max(range(3), key=lambda index: probabilities[index])
            supported = (
                max(probabilities) >= .6
                and self.model.metadata.get("calibration_status") == "project_heldout_temperature"
                and self.model.metadata.get("decision_eligible") is True
                and len(self.model.metadata.get("calibration_class_projects", ())) == 3
                and min(self.model.metadata["calibration_class_projects"]) > 0
            )
            prediction = WorkPrediction(
                probabilities, best if supported else 1, self._revisions[workflow],
                time.monotonic(), "model" if supported else "uncertain_medium",
            )
            self.counts["inferences"] += 1
            self.counts["model_rank" if supported else "uncertain_medium"] += 1
            self.counts["inference_thread_cpu_us"] += (time.thread_time() - cpu) * 1e6
            self.counts["inference_wall_us"] += (time.perf_counter() - wall) * 1e6
            # Publication is one immutable object assignment. A terminal event
            # already published on the scheduler thread always wins.
            with self._condition:
                if not self._stopped and workflow not in self._closed_workflows:
                    self._predictions[workflow] = prediction
                    if self._revisions[workflow] == self._submitted_revisions[workflow]:
                        self._pending_since.pop(workflow, None)

    def _run(self):
        next_update = 0.
        while True:
            with self._condition:
                while not self._stopped and not self.error and (
                    not self._queue or time.monotonic() < next_update
                ):
                    timeout = max(.001, next_update - time.monotonic()) if self._queue else None
                    self._condition.wait(timeout=timeout)
                if self._stopped or self.error:
                    return
            try:
                self.process_pending()
                next_update = time.monotonic() + self.interval_s
            except Exception as error:
                self.error = type(error).__name__ + ": " + str(error)[:300]
                self._predictions.clear()
                return

    def status(self):
        return {
            "artifact_sha256": self.digest, "error": self.error,
            "calibration_status": self.model.metadata.get("calibration_status"),
            "decision_eligible": self.model.metadata.get("decision_eligible", False),
            "refresh_interval_ms": 200, "counts": dict(self.counts),
            "cached_workflows": len(self._predictions), "queue_depth": len(self._queue),
            "future_trace_used_online": False, "scheduler_waits_for_inference": False,
        }

    def close(self):
        with self._condition:
            self._stopped = True
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.)
        self._predictions.clear()
        self._queue.clear()
