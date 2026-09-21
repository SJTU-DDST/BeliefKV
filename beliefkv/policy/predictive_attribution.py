from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


TERMINAL_OUTCOMES = frozenset({"useful", "wasted", "too_late", "censored", "failed"})
PREFETCH_ACTIONS = frozenset(
    {"prefetch_gpu", "partial_prefetch_gpu", "reclaim_and_prefetch"}
)


@dataclass
class PredictiveActionOutcome:
    intent_id: str
    action: str
    context_id: str
    context_epoch: int
    command_id: str
    created_ts_ms: float
    decision_ts_ms: float | None = None
    state: str = "pending_transfer"
    transfer_completed_ts_ms: float | None = None
    terminal_ts_ms: float | None = None
    actual_bytes: int = 0
    reason: str | None = None

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_OUTCOMES


class PredictiveActionAttributionLedger:
    """Small runtime ledger for causal attribution of predictive KV actions."""

    def __init__(
        self,
        emit: Callable[[str, float, PredictiveActionOutcome], None] | None = None,
    ) -> None:
        self._by_intent: dict[str, PredictiveActionOutcome] = {}
        self._emit = emit

    def register(
        self,
        *,
        intent_id: str,
        action: str,
        context_id: str,
        context_epoch: int,
        command_id: str,
        now_ms: float,
        decision_ts_ms: float | None = None,
    ) -> None:
        if intent_id in self._by_intent:
            raise ValueError(f"predictive intent already attributed: {intent_id}")
        outcome = PredictiveActionOutcome(
            intent_id=intent_id,
            action=action,
            context_id=context_id,
            context_epoch=context_epoch,
            command_id=command_id,
            created_ts_ms=now_ms,
            decision_ts_ms=decision_ts_ms,
        )
        self._by_intent[intent_id] = outcome
        self._notify("registered", now_ms, outcome)

    def transfer_terminal(
        self,
        intent_id: str,
        *,
        completed: bool,
        actual_bytes: int,
        now_ms: float,
        reason: str | None = None,
    ) -> None:
        outcome = self._by_intent.get(intent_id)
        if outcome is None or outcome.state != "pending_transfer":
            return
        outcome.actual_bytes = max(0, actual_bytes)
        outcome.transfer_completed_ts_ms = now_ms
        outcome.reason = reason
        if completed:
            outcome.state = "prepared"
            self._notify("prepared", now_ms, outcome)
        else:
            self._finish(outcome, "failed", now_ms, reason or "transfer_failed")

    def consume_context(
        self,
        context_id: str,
        *,
        context_epoch: int | None,
        now_ms: float,
        reason: str,
        consumed_intent_id: str | None = None,
    ) -> tuple[PredictiveActionOutcome, ...]:
        """Attribute observed consumption; an intent ID asserts actual reuse.

        Runtime COMMIT notifications have context/epoch but no page-level
        identity, so unverified PREPAREs are censored rather than credited.
        """
        if context_epoch is None:
            return ()
        if reason == "prefetch_first_gpu_service":
            actions = PREFETCH_ACTIONS
        elif reason == "observed_pressure_commit":
            actions = frozenset({"prepare_host"})
        else:
            return ()
        candidates = tuple(
            outcome
            for outcome in self._by_intent.values()
            if outcome.action in actions
            and outcome.context_id == context_id
            and not outcome.terminal
            and outcome.created_ts_ms <= now_ms
            and outcome.context_epoch == context_epoch
        )
        ready = tuple(
            outcome
            for outcome in candidates
            if outcome.state == "prepared"
            and outcome.actual_bytes > 0
            and outcome.transfer_completed_ts_ms is not None
            and outcome.transfer_completed_ts_ms <= now_ms
        )
        # A context-level COMMIT does not identify the pages it committed.
        # Only explicit intent-level consumption establishes PREPARE reuse.
        if reason == "observed_pressure_commit":
            for outcome in candidates:
                if outcome in ready and outcome.intent_id == consumed_intent_id:
                    self._finish(outcome, "useful", now_ms, reason)
                else:
                    self._finish(outcome, "censored", now_ms, "commit_reuse_unverified")
            return candidates

        # A service lease identifies use only with a unique ready candidate,
        # or when the consuming intent is supplied explicitly.
        selected = next(
            (item for item in ready if item.intent_id == consumed_intent_id),
            None,
        )
        if (
            selected is None
            and consumed_intent_id is None
            and len(candidates) == 1
            and len(ready) == 1
        ):
            selected = ready[0]
        if selected is not None:
            self._finish(selected, "useful", now_ms, reason)
            return (selected,)
        if len(ready) > 1 and consumed_intent_id is None:
            for outcome in ready:
                self._finish(outcome, "censored", now_ms, "prefetch_use_ambiguous")
            return ready
        return ()

    def terminal_context(
        self,
        context_id: str,
        *,
        context_epoch: int | None,
        now_ms: float,
        reason: str,
    ) -> tuple[PredictiveActionOutcome, ...]:
        wasted = []
        for outcome in self._by_intent.values():
            if (
                outcome.context_id == context_id
                and not outcome.terminal
                and (
                    context_epoch is None
                    or outcome.context_epoch == context_epoch
                )
            ):
                self._finish(outcome, "wasted", now_ms, reason)
                wasted.append(outcome)
        return tuple(wasted)

    def censor_all(self, *, now_ms: float, reason: str) -> None:
        for outcome in self._by_intent.values():
            if not outcome.terminal:
                self._finish(outcome, "censored", now_ms, reason)

    def outcomes(self) -> tuple[PredictiveActionOutcome, ...]:
        return tuple(self._by_intent.values())

    def get(self, intent_id: str) -> PredictiveActionOutcome | None:
        return self._by_intent.get(intent_id)

    def _finish(
        self,
        outcome: PredictiveActionOutcome,
        state: str,
        now_ms: float,
        reason: str,
    ) -> None:
        outcome.state = state
        outcome.terminal_ts_ms = now_ms
        outcome.reason = reason
        self._notify(state, now_ms, outcome)

    def _notify(
        self, event: str, now_ms: float, outcome: PredictiveActionOutcome
    ) -> None:
        if self._emit is not None:
            self._emit(event, now_ms, outcome)
