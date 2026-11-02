"""Coalesced CPU predictions; scheduling never waits for the worker."""

from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import math
import threading
import time

from beliefkv.core.events import EventConfidence, RuntimeEventKind as Kind, is_unobserved_join_event
from beliefkv.predictor.workflow_work import (
    R5_SCHEMA, WorkflowProgress, WorkflowWorkModel, task_from_prompt_tail, task_requirements,
)
from beliefkv.predictor.workflow_work_distribution import (
    DistributionProgress, STRUCTURAL_EVENTS, support_status,
)


@dataclass(frozen=True)
class WorkPrediction:
    probabilities: tuple
    bucket: int
    revision: int
    updated_at: float
    evidence: str


@dataclass(frozen=True)
class DistributionWorkPrediction(WorkPrediction):
    support: str
    expires_at: float
    structural_revision: int


class WorkflowWorkWorker:
    interval_s = .2
    queue_limit = 4096
    workflow_limit = 256

    def __init__(
        self, path, digest, *, tokenizer_path=None, model=None, autostart=True,
        execution_environment=None,
    ):
        self.model = model if model is not None else WorkflowWorkModel.load(path, digest)
        self._r5 = self.model.schema == R5_SCHEMA
        self.task_prefix = self.model.task_input_prefix(execution_environment)
        self.digest = digest
        self.tokenizer_path = tokenizer_path
        self._tokenizer = None
        self._condition = threading.Condition()
        self._queue = deque()
        self._predictions = {}
        self._closed_workflows = set()
        self._progress = {}
        self._projections = {}
        self._task_coverage = {}
        self._next_inference = {}
        self._structural_submitted = Counter()
        self._structural_consumed = Counter()
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
            if self._r5 and action == "task":
                self.counts["full_prompt_fallback_disabled"] += 1
                return
            if self._r5 and action == "event" and (
                args[0].confidence is EventConfidence.INFERRED or is_unobserved_join_event(args[0])
            ):
                self.counts["unobserved_event_ignored"] += 1
                return
            if self._r5 and action == "event" and args[0].kind in STRUCTURAL_EVENTS:
                self._structural_submitted[workflow] += 1
                self._predictions.pop(workflow, None)
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
        now = time.monotonic()
        lag = self.model.distribution_contract["maximum_observation_lag_s"] if self._r5 else 2.
        if (since := self._pending_since.get(workflow)) is not None and now - since > lag:
            return None
        prediction = self._predictions.get(workflow)
        if self._r5 and prediction is not None and now > prediction.expires_at:
            return None
        return prediction

    def bucket(self, workflow, now):
        prediction = self.prediction(workflow)
        return 1 if prediction is None else prediction.bucket

    def _consume(self, rows):
        for workflow, action, args in rows:
            if action == "event" and args[0].kind is Kind.WORKFLOW_END:
                self._progress.pop(workflow, None)
                self._projections.pop(workflow, None)
                self._task_coverage.pop(workflow, None)
                self._next_inference.pop(workflow, None)
                self._structural_submitted.pop(workflow, None)
                self._structural_consumed.pop(workflow, None)
                self._revisions.pop(workflow, None)
                self._submitted_revisions.pop(workflow, None)
                self._pending_since.pop(workflow, None)
                self._dirty.discard(workflow)
                continue
            if workflow in self._closed_workflows:
                continue
            if workflow not in self._progress and len(self._progress) >= self.workflow_limit:
                self.counts["workflow_limit_abstentions"] += 1
                continue
            if workflow not in self._progress:
                self._progress[workflow] = DistributionProgress() if self._r5 else WorkflowProgress()
            progress = self._progress[workflow]
            if action == "event":
                progress.event(args[0])
                if self._r5 and args[0].kind in STRUCTURAL_EVENTS:
                    self._structural_consumed[workflow] += 1
                if args[0].kind is Kind.WORKFLOW_START and not self._r5:
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
        wall, cpu = time.perf_counter(), time.thread_time()
        try:
            self._process_pending()
        finally:
            self.counts["worker_cycles"] += 1
            self.counts["worker_thread_cpu_us"] += (time.thread_time() - cpu) * 1e6
            self.counts["worker_wall_us"] += (time.perf_counter() - wall) * 1e6

    def _process_pending(self):
        with self._condition:
            rows = tuple(self._queue)
            self._queue.clear()
        self.counts["worker_observations_processed"] += len(rows)
        self._consume(rows)
        for workflow in tuple(self._dirty):
            if self._stopped:
                return
            now = time.monotonic()
            if self._r5 and now < self._next_inference.get(workflow, 0.):
                self.counts["per_workflow_refresh_coalesced"] += 1
                continue
            self._dirty.discard(workflow)
            if workflow in self._closed_workflows:
                continue
            progress = self._progress[workflow]
            if workflow not in self._projections:
                if not progress.text:
                    self.counts["task_unavailable"] += 1
                    continue
                encoding_wall, encoding_cpu = time.perf_counter(), time.thread_time()
                if self._r5:
                    projection, coverage = self.model.task_encoding(self.task_prefix + progress.text)
                    self._projections[workflow] = projection
                    self._task_coverage[workflow] = coverage
                else:
                    self._projections[workflow] = self.model.task_projection(self.task_prefix + progress.text)
                self.counts["task_encodings"] += 1
                self.counts["task_encoding_thread_cpu_us"] += (time.thread_time() - encoding_cpu) * 1e6
                self.counts["task_encoding_wall_us"] += (time.perf_counter() - encoding_wall) * 1e6
            projection = self._projections[workflow]
            if projection is None:
                self.counts["unsupported_task"] += 1
                continue
            wall, cpu = time.perf_counter(), time.thread_time()
            features = progress.features(feature_schema=self.model.schema)
            probabilities = self.model.predict(projection, features)
            best = max(range(3), key=lambda index: probabilities[index])
            supported = not self._r5 and (
                max(probabilities) >= .6
                and self.model.metadata.get("calibration_status") in (
                    "project_heldout_temperature", "capture_heldout_temperature",
                    "workflow_heldout_temperature",
                )
                and self.model.metadata.get("decision_eligible") is True
                and len(self.model.metadata.get("calibration_class_projects", ())) == 3
                and min(self.model.metadata["calibration_class_projects"]) > 0
            )
            prediction = WorkPrediction(
                probabilities, best if supported else 1, self._revisions[workflow],
                time.monotonic(), "model" if supported else "uncertain_medium",
            )
            if self._r5:
                support = support_status(self.model, features, self._task_coverage[workflow])
                supported = support == "calibrated_distribution"
                prediction = DistributionWorkPrediction(
                    probabilities, best if supported else 1, self._revisions[workflow],
                    now, "model" if supported else "unsupported_distribution",
                    support, now + self.model.distribution_contract["maximum_cache_age_s"],
                    self._structural_consumed[workflow],
                )
                self._next_inference[workflow] = now + self.model.distribution_contract["minimum_refresh_s"]
                self.counts["distribution_" + support] += 1
            self.counts["inferences"] += 1
            self.counts["model_rank" if supported else "uncertain_medium"] += 1
            self.counts["inference_thread_cpu_us"] += (time.thread_time() - cpu) * 1e6
            self.counts["inference_wall_us"] += (time.perf_counter() - wall) * 1e6
            # Publication is one immutable object assignment. A terminal event
            # already published on the scheduler thread always wins.
            with self._condition:
                if not self._stopped and workflow not in self._closed_workflows and (
                    not self._r5 or self._structural_consumed[workflow] == self._structural_submitted[workflow]
                ):
                    self._predictions[workflow] = prediction
                    if self._revisions[workflow] == self._submitted_revisions[workflow]:
                        self._pending_since.pop(workflow, None)

    def _run(self):
        next_update = 0.
        while True:
            with self._condition:
                while not self._stopped and not self.error and (
                    (not self._queue and not (self._r5 and self._dirty))
                    or time.monotonic() < next_update
                    or self._r5 and not self._queue and all(
                        time.monotonic() < self._next_inference.get(workflow, 0.)
                        for workflow in self._dirty
                    )
                ):
                    deadline = next_update if self._queue else (
                        max(next_update, min(self._next_inference.get(workflow, 0.) for workflow in self._dirty))
                        if self._r5 and self._dirty else math.inf
                    )
                    timeout = max(.001, deadline - time.monotonic()) if math.isfinite(deadline) else None
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
            "validation_scope": self.model.metadata.get("validation_scope", "unseen_projects"),
            "task_input_kind": self.model.task_input_kind,
            "feature_schema": self.model.schema,
            "execution_controls": self.model.deployment_controls,
            "task_prefix_sha256": hashlib.sha256(self.task_prefix.encode()).hexdigest(),
            "refresh_interval_ms": (
                self.model.distribution_contract["minimum_refresh_s"] * 1000 if self._r5 else 200
            ), "counts": dict(self.counts),
            "cached_workflows": len(self._predictions), "queue_depth": len(self._queue),
            "future_trace_used_online": False, "scheduler_waits_for_inference": False,
            **({
                "conditional_distribution": True, "full_prompt_fallback_enabled": False,
                "support_uses_maximum_softmax_threshold": False,
                "structural_events_revoke_stale_predictions": True,
                "maximum_cache_age_s": self.model.distribution_contract["maximum_cache_age_s"],
            } if self._r5 else {}),
        }

    def close(self):
        with self._condition:
            self._stopped = True
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.)
        self._predictions.clear()
        self._queue.clear()
