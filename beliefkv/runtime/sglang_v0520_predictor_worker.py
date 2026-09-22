"""Process-isolated, bounded v0.5.20 admission demand inference."""

from __future__ import annotations

import math
import multiprocessing as mp
from queue import Empty, Full
import time

from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel,
    LocalFrontierFeatures,
    WaitBeliefKind,
)
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_prediction import (
    MAX_OUTPUT_TOKENS,
    MAX_TOOL_WAIT_MS,
    NativeDemandHint,
    NativeToolWaitHint,
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


def predict_tool_wait_batch(
    items: tuple[tuple[PrefillCandidateKey, LocalFrontierFeatures, float], ...],
    *,
    model: FrontierBeliefModel | None = None,
) -> tuple[tuple[PrefillCandidateKey, float, float, float, float], ...]:
    model = model or _MODEL
    if model is None:
        raise RuntimeError("tool-wait predictor is not loaded")
    results = []
    for key, features, revision in items:
        if features.state != "wait_tool":
            continue
        prediction = model.predict(features)
        wait = prediction.wait_belief
        if (
            not math.isfinite(prediction.calibration_coverage)
            or not 0 < prediction.calibration_coverage <= 1
            or prediction.head_support.get("tool_wait")
            not in {"exact", "role", "backoff", "global", "pooled"}
            or prediction.ood_reasons
            or wait is None
            or wait.kind is not WaitBeliefKind.TOOL
            or wait.dependency_composed
            or not wait.available
            or wait.ood_reasons
        ):
            continue
        quantiles = tuple(
            wait.residual_duration.quantile(q) for q in (0.1, 0.5, 0.9)
        )
        if (
            all(
                type(value) in (int, float)
                and math.isfinite(value)
                and 0 <= value <= MAX_TOOL_WAIT_MS
                for value in quantiles
            )
            and quantiles[0] <= quantiles[1] <= quantiles[2]
        ):
            results.append((key, *quantiles, revision))
    return tuple(results)


def _worker_main(
    artifact_path: str, input_queue: object, output_queue: object
) -> None:
    _init_model(artifact_path)
    while True:
        request = input_queue.get()
        if len(request) == 2:
            sequence, items = request
            output_queue.put((sequence, predict_batch(items)))
        else:
            sequence, kind, items = request
            if kind != "tool_wait":
                raise ValueError("unknown predictor batch kind")
            output_queue.put((sequence, kind, predict_tool_wait_batch(items)))


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
        self._active_kind: str | None = None
        self._started_at: float | None = None
        self._latest_sequence = 0
        self._next: tuple[tuple[PrefillCandidateKey, LocalFrontierFeatures, float], ...] | None = None
        self._next_kind: str | None = None
        self.failure_count = 0
        self.disabled = False
        self._closed = False

    def submit(
        self, items: tuple[tuple[PrefillCandidateKey, LocalFrontierFeatures, float], ...]
    ) -> None:
        self._submit(items, kind="admission")

    def submit_tool_wait(
        self, items: tuple[tuple[PrefillCandidateKey, LocalFrontierFeatures, float], ...]
    ) -> None:
        self._submit(items, kind="tool_wait")

    def _submit(
        self,
        items: tuple[tuple[PrefillCandidateKey, LocalFrontierFeatures, float], ...],
        *,
        kind: str,
    ) -> None:
        if len(items) > 8:
            raise ValueError(f"{kind} prediction batch exceeds bound")
        if self.disabled:
            return
        if self._closed:
            raise RuntimeError(f"{kind} predictor worker is closed")
        if items:
            if kind == "admission" and self._next_kind == "tool_wait":
                return
            self._latest_sequence += 1
            self._next = items
            self._next_kind = kind
            self._dispatch()

    def _dispatch(self) -> None:
        if self._active_sequence is not None or self._next is None or self.disabled:
            return
        if not self._process.is_alive():
            self._fail()
            return
        try:
            if self._next_kind == "tool_wait":
                request = (self._latest_sequence, "tool_wait", self._next)
            else:
                request = (self._latest_sequence, self._next)
            self._input_queue.put_nowait(request)
        except Full:
            self._fail()
            return
        except (OSError, ValueError):
            self._fail()
            return
        self._active_sequence = self._latest_sequence
        self._active_kind = self._next_kind
        self._started_at = time.monotonic()
        self._next = None
        self._next_kind = None

    def fileno(self) -> int | None:
        """Return the result pipe fd for idle scheduler wakeups."""
        if self.disabled or self._closed or not self._process.is_alive():
            return None
        try:
            fd = self._output_queue._reader.fileno()
        except (AttributeError, OSError, ValueError):
            return None
        return fd if type(fd) is int and fd >= 0 else None

    def poll(self) -> tuple[NativeDemandHint | NativeToolWaitHint, ...]:
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
            response = self._output_queue.get_nowait()
        except Empty:
            return ()
        except (OSError, ValueError, EOFError):
            self._fail()
            return ()
        if len(response) == 2:
            sequence, values = response
            kind = "admission"
        elif len(response) == 3:
            sequence, kind, values = response
        else:
            self._fail()
            return ()
        if sequence != self._active_sequence or kind != self._active_kind:
            self._fail()
            return ()
        # Admission may queue behind an in-flight tool wait without making the
        # still-live tool prediction obsolete. A newer tool wait does supersede it.
        superseded = self._next is not None and (
            kind == "admission" or self._next_kind == "tool_wait"
        )
        self._active_sequence = None
        self._active_kind = None
        self._started_at = None
        self._dispatch()
        if self.disabled or superseded:
            return ()
        issued = time.monotonic() * 1000
        if kind == "tool_wait":
            return tuple(
                NativeToolWaitHint(
                    key=key,
                    wait_p10_ms=p10,
                    wait_p50_ms=p50,
                    wait_p90_ms=p90,
                    issued_monotonic_ms=issued,
                    expires_monotonic_ms=issued + 5_000,
                    predictor_sha256=self.predictor_sha256,
                    invocation_revision_ts_ms=revision,
                )
                for key, p10, p50, p90, revision in values
            )
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
            self._next_kind = None
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._next = None
        self._next_kind = None
        self._active_sequence = None
        self._active_kind = None
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
