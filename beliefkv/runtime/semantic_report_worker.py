"""Bounded CPU semantic inference in a separate process, never on the scheduler."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import multiprocessing as mp
import os
from queue import Empty, Full
import time

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey


SEMANTIC_TEXT = "beliefkv_semantic_child_text"


@dataclass(frozen=True)
class SemanticReportInput:
    key: PrefillCandidateKey
    observed_ts_ms: float
    observed_output_tokens: int
    content_chars: int
    content_tail: str
    notice_active: bool
    estimated_report_tokens: int
    prior_tool_calls: int
    prior_model_rounds: int


@dataclass(frozen=True)
class SemanticReportReply:
    observation: SemanticReportInput
    final_score: float
    lower_tokens: float
    middle_tokens: float
    upper_tokens: float
    inference_ms: float


def _worker_main(artifact: str, inputs: object, outputs: object) -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["OMP_NUM_THREADS"] = "2"
    import torch
    from beliefkv.predictor.child_report_phase import ReportObservation
    from beliefkv.predictor.child_semantic_work import SemanticReportPredictor

    torch.set_num_threads(2)
    try:
        model = SemanticReportPredictor.load(artifact)
        outputs.put(("ready", ()))
        while (items := inputs.get()) is not None:
            observations = [
                ReportObservation(
                    item.key.request_id, item.key.invocation_id, item.key.context_id,
                    item.key.context_epoch, item.observed_ts_ms,
                    item.content_tail, item.content_chars, item.observed_output_tokens,
                    notice_active=item.notice_active,
                    estimated_report_tokens=item.estimated_report_tokens,
                    prior_tool_calls=item.prior_tool_calls,
                    prior_model_rounds=item.prior_model_rounds,
                ) for item in items
            ]
            started = time.perf_counter()
            predictions = model.predict(observations)
            duration = (time.perf_counter() - started) * 1000
            outputs.put(("result", tuple(
                SemanticReportReply(
                    item, prediction.final_report_score,
                    *prediction.conditional_remaining_tokens,
                    duration / max(len(items), 1),
                ) for item, prediction in zip(items, predictions)
            )))
    except Exception as error:
        outputs.put(("error", f"{type(error).__name__}: {error}"))


class SemanticReportWorker:
    def __init__(self, artifact: str) -> None:
        context = mp.get_context("spawn")
        self._inputs = context.Queue(maxsize=1)
        self._outputs = context.Queue(maxsize=2)
        self._process = context.Process(
            target=_worker_main, args=(artifact, self._inputs, self._outputs), daemon=True,
        )
        self._process.start()
        self._pending: OrderedDict[str, SemanticReportInput] = OrderedDict()
        self._active = False
        self._started = time.monotonic()
        self.ready = False
        self.disabled = False
        self.error = ""
        self.dropped = 0

    def submit(self, item: SemanticReportInput) -> None:
        if self.disabled:
            return
        self._pending[item.key.request_id] = item
        self._pending.move_to_end(item.key.request_id)
        if len(self._pending) > 64:
            self._pending.popitem(last=False)
            self.dropped += 1
        self._dispatch()

    def _dispatch(self) -> None:
        if self.disabled or not self.ready or self._active or not self._pending:
            return
        prioritized = sorted(
            self._pending.values(),
            key=lambda item: (not item.notice_active, -item.observed_ts_ms),
        )[:4]
        items = tuple(self._pending.pop(item.key.request_id) for item in prioritized)
        try:
            self._inputs.put_nowait(items)
        except Full:
            for item in reversed(items):
                self._pending[item.key.request_id] = item
            return
        self._active = True
        self._started = time.monotonic()

    def cancel(self, request_id: str) -> None:
        self._pending.pop(request_id, None)

    def fileno(self) -> int | None:
        if self.disabled or not self._process.is_alive():
            return None
        return self._outputs._reader.fileno()

    def poll(self) -> tuple[SemanticReportReply, ...]:
        if self.disabled:
            return ()
        if not self._process.is_alive():
            self.disabled, self.error = True, "semantic worker exited"
            return ()
        if time.monotonic() - self._started > (10. if self.ready and self._active else 60.):
            if self._active or not self.ready:
                self.disabled, self.error = True, "semantic worker timed out"
                return ()
        try:
            kind, values = self._outputs.get_nowait()
        except Empty:
            return ()
        if kind == "error":
            self.disabled, self.error = True, values
            return ()
        if kind == "ready":
            self.ready = True
            self._dispatch()
            return ()
        self._active = False
        self._dispatch()
        return values

    def close(self) -> None:
        self.disabled = True
        try:
            self._inputs.put_nowait(None)
        except Full:
            pass
        self._process.join(timeout=2)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)
        self._inputs.close()
        self._outputs.close()
