"""Process-isolated, bounded v0.5.20 admission demand inference."""

from __future__ import annotations

import math
import multiprocessing as mp
from queue import Empty, Full
import time

from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel,
    LocalFrontierFeatures,
)
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_prediction import (
    MAX_OUTPUT_TOKENS,
    NativeDemandHint,
)


_MODEL: FrontierBeliefModel | None = None


def _init_model(path: str) -> None:
    global _MODEL
    _MODEL = FrontierBeliefModel.load(path)


def predict_batch(
    items: tuple[tuple[PrefillCandidateKey, LocalFrontierFeatures, float], ...],
    *,
    model: FrontierBeliefModel | None = None,
) -> tuple[tuple[PrefillCandidateKey, int, float], ...]:
    model = model or _MODEL
    if model is None:
        raise RuntimeError("admission predictor is not loaded")
    results = []
    for key, features, revision in items:
        prediction = model.predict(features)
        if (
            prediction.head_support.get("next_output_demand") == "unavailable"
            or not prediction.next_output_tokens.values
            or prediction.ood_reasons
        ):
            continue
        tokens = int(round(prediction.next_output_tokens.quantile(0.5)))
        if 0 < tokens <= MAX_OUTPUT_TOKENS:
            results.append((key, tokens, revision))
    return tuple(results)


def _worker_main(
    artifact_path: str, input_queue: object, output_queue: object
) -> None:
    _init_model(artifact_path)
    while True:
        sequence, items = input_queue.get()
        output_queue.put((sequence, predict_batch(items)))


class NativePredictorWorker:
    """Single in-flight batch; retain only the newest waiting batch."""

    def __init__(
        self, artifact_path: str, predictor_sha256: str, *, timeout_s: float = 10.0
    ) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("worker timeout must be positive and finite")
        self.predictor_sha256 = predictor_sha256
        self._timeout_s = timeout_s
        context = mp.get_context("spawn")
        self._input_queue = context.Queue(maxsize=1)
        self._output_queue = context.Queue(maxsize=1)
        self._process = context.Process(
            target=_worker_main,
            args=(artifact_path, self._input_queue, self._output_queue),
            daemon=True,
        )
        self._process.start()
        self._active_sequence: int | None = None
        self._started_at: float | None = None
        self._latest_sequence = 0
        self._next: tuple[tuple[PrefillCandidateKey, LocalFrontierFeatures, float], ...] | None = None
        self.failure_count = 0
        self.disabled = False
        self._closed = False

    def submit(
        self, items: tuple[tuple[PrefillCandidateKey, LocalFrontierFeatures, float], ...]
    ) -> None:
        if len(items) > 8:
            raise ValueError("admission prediction batch exceeds bound")
        if self.disabled:
            return
        if self._closed:
            raise RuntimeError("admission predictor worker is closed")
        if items:
            self._latest_sequence += 1
            self._next = items
            self._dispatch()

    def _dispatch(self) -> None:
        if self._active_sequence is not None or self._next is None or self.disabled:
            return
        if not self._process.is_alive():
            self._fail()
            return
        try:
            self._input_queue.put_nowait((self._latest_sequence, self._next))
        except Full:
            self._fail()
            return
        except (OSError, ValueError):
            self._fail()
            return
        self._active_sequence = self._latest_sequence
        self._started_at = time.monotonic()
        self._next = None

    def poll(self) -> tuple[NativeDemandHint, ...]:
        if self.disabled or self._closed:
            return ()
        if not self._process.is_alive():
            self._fail()
            return ()
        if self._active_sequence is None:
            self._dispatch()
            return ()
        assert self._started_at is not None
        if time.monotonic() - self._started_at >= self._timeout_s:
            self._fail()
            return ()
        try:
            sequence, values = self._output_queue.get_nowait()
        except Empty:
            return ()
        except (OSError, ValueError, EOFError):
            self._fail()
            return ()
        if sequence != self._active_sequence:
            self._fail()
            return ()
        self._active_sequence = None
        self._started_at = None
        self._dispatch()
        if self.disabled or sequence != self._latest_sequence:
            return ()
        issued = time.monotonic() * 1000
        return tuple(
            NativeDemandHint(
                key=key,
                next_output_tokens=tokens,
                issued_monotonic_ms=issued,
                expires_monotonic_ms=issued + 5_000,
                predictor_sha256=self.predictor_sha256,
                invocation_revision_ts_ms=revision,
            )
            for key, tokens, revision in values
        )

    def _fail(self) -> None:
        if not self.disabled:
            self.failure_count += 1
            self.disabled = True
            self._next = None
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._next = None
        self._active_sequence = None
        self._started_at = None
        if self._process.is_alive():
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass
            self._process.join(timeout=0.1)
            if self._process.is_alive():
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
                self._process.join(timeout=0.1)
        else:
            self._process.join(timeout=0)
        for queue in (self._input_queue, self._output_queue):
            queue.cancel_join_thread()
            queue.close()
