"""Cached workflow work at reference ThunderAgent recovery boundaries."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import time

from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet, _number


@dataclass(frozen=True)
class WorkflowToolState:
    blocked: bool = False
    pause_safe: bool = False
    retention: float = 1.
    protected: bool = False
    source: str = "unobserved"
    elapsed_s: float = 0.
    horizon_ms: float = 0.
    return_probability: float | None = None

    @property
    def weight(self):
        return self.retention if self.blocked and self.pause_safe and not self.protected else 1.


def resume_category(item):
    return 1 if item.steps == 1 else 0 if not item.acting else 2


class WorkflowSizeWorkingSet(ThunderActingDecayWorkingSet):
    """Change recovery ordering; inherit decay, pressure, and native authority."""

    def __init__(
        self, capacity_tokens, *, rank, sibling_group, observe_ready=None,
        select_set=None, defer_first=False, rank_before_category=False, **kwargs,
    ):
        super().__init__(capacity_tokens, **kwargs)
        self.rank = rank
        self.sibling_group = sibling_group
        self.observe_ready = observe_ready
        self.select_set = select_set
        self.defer_first = defer_first
        self.rank_before_category = rank_before_category
        self.last_selection = {}
        self.pressure_preference = None
        self.observe_pressure_candidates = False
        self.last_pressure = []
        self.tool_pressure_state = None
        self._tool_states = {}
        self._tool_states_at = None

    def invalidate_tool_states(self):
        self._tool_states.clear()
        self._tool_states_at = None

    def tool_state(self, item, now):
        if self._tool_states_at != now:
            self.invalidate_tool_states()
            self._tool_states_at = now
        if item.identity not in self._tool_states:
            state = self.tool_pressure_state(item, now)
            if not isinstance(state, WorkflowToolState) or not 0. <= state.retention <= 1.:
                raise ValueError("tool pressure requires an observed bounded retention weight")
            self._tool_states[item.identity] = state
        return self._tool_states[item.identity]

    def remaining_capacity_with_decay(self, *, now):
        if self.tool_pressure_state is None:
            return super().remaining_capacity_with_decay(now=now)
        now = _number(now, "tool-weighted capacity observation")
        active = [item for item in self.programs.values() if item.active]
        used = sum(item.tokens * self.tool_state(item, now).weight for item in active)
        return self.capacity_tokens - int(used) - len(active) * self.buffer_tokens

    def arrive(self, identity, tokens, *, now):
        super().arrive(identity, tokens, now=now)
        item = self.programs[identity]
        if self.defer_first and item.steps == 1 and item.active:
            self._pause(item, now)

    def _resume(self):
        if self._resume_now is None:
            raise ValueError("workflow resume requires the reference control timestamp")
        started, cpu = time.perf_counter(), time.thread_time()
        capacity = self.remaining_capacity_with_decay(now=self._resume_now)
        self.last_resume_capacity_tokens = capacity
        if capacity <= self.buffer_tokens:
            self.last_selection = {
                "capacity_tokens": capacity, "selected_tokens": 0, "ordered": (),
                "selected_in_priority_order": (),
                "thread_cpu_us": (time.thread_time() - cpu) * 1e6,
                "wall_us": (time.perf_counter() - started) * 1e6,
            }
            return ()
        paused = [
            item for item in self.programs.values()
            if not item.active and (self.resume_eligible is None or self.resume_eligible(item))
            and (
                self.tool_pressure_state is None
                or not self.tool_state(item, self._resume_now).blocked
            )
            and (
                not self.rank_before_category
                or self.observe_ready is not None and self.observe_ready(item)
            )
        ]
        readiness = (
            {item.identity: bool(self.observe_ready(item)) for item in paused}
            if self.observe_ready is not None else None
        )
        ranks = {item.identity: self.rank(item.identity, self._resume_now) for item in paused}
        reference_ranks = {
            identity: 0. if value == 0. else 1. for identity, value in ranks.items()
        } if self.select_set is not None and self.rank_before_category else ranks
        paused.sort(key=lambda item: (
            (resume_category(item), item.tokens, item.queue_sequence)
            if self.select_set is not None and not self.rank_before_category else
            (reference_ranks[item.identity], resume_category(item), item.tokens, item.queue_sequence)
            if self.rank_before_category else
            (resume_category(item), ranks[item.identity], item.tokens, item.queue_sequence)
        ))
        # Only ready members of an observed blocking JOIN may become adjacent.
        groups, memberships = defaultdict(list), {}
        for item in paused:
            group = self.sibling_group(item.identity) if (
                self.select_set is None or self.rank_before_category
            ) else None
            if group is not None:
                key = resume_category(item), reference_ranks[item.identity], group
                memberships[item.identity] = key
                groups[key].append(item)
        ordered, seen = [], set()
        for item in paused:
            if item.identity in seen:
                continue
            peers = groups.get(memberships.get(item.identity), (item,))
            for peer in peers:
                if peer.identity not in seen:
                    seen.add(peer.identity)
                    ordered.append(peer)
        selected, used = [], 0
        for item in ordered:
            required = item.tokens + self.buffer_tokens
            if used + required <= capacity:
                selected.append(item)
                used += required
        if self.select_set is not None:
            chosen = self.select_set(ordered, selected, capacity)
            if (
                len({item.identity for item in chosen}) != len(chosen)
                or any(item not in ordered for item in chosen)
                or sum(item.tokens + self.buffer_tokens for item in chosen) > capacity
            ):
                raise ValueError("combination selector violated the reference logical budget")
            selected = list(chosen)
            used = sum(item.tokens + self.buffer_tokens for item in selected)
        selection_order = tuple(item.identity for item in selected)
        # Keep the public one-backend placement convention and post-resume pressure.
        selected.sort(key=lambda item: -item.tokens)
        for item in selected:
            item.active = True
        self.last_selection = {
            "capacity_tokens": capacity, "selected_tokens": used,
            "ordered": tuple(
                (item.identity, resume_category(item), ranks[item.identity],
                 item.tokens, item.queue_sequence, memberships.get(item.identity))
                for item in ordered
            ),
            "selected_in_priority_order": selection_order,
            "rank_before_category": self.rank_before_category and self.select_set is None,
            "thread_cpu_us": (time.thread_time() - cpu) * 1e6,
            "wall_us": (time.perf_counter() - started) * 1e6,
            **({"logical_ready_paused_before_resume": readiness} if readiness is not None else {}),
        }
        return tuple(item.identity for item in selected)

    def tick(self, *, now):
        self.last_pressure = []
        if self.tool_pressure_state is not None:
            return self._tick_tool_pressure(now=now)
        if self.pressure_preference is None:
            return super().tick(now=now)
        timestamp = _number(now, "workflow pressure control")
        self._resume_now = timestamp
        preferences = {}

        def order(items):
            for item in items:
                if item.identity not in preferences:
                    preferences[item.identity] = self.pressure_preference(item, timestamp)
            if any(preferences[item.identity] is None for item in items):
                return sorted(items, key=lambda item: item.tokens), False
            return sorted(items, key=lambda item: (
                -preferences[item.identity], -item.tokens, item.queue_sequence,
            )), True

        def record(selected, reference, *, predicted, marked):
            self.last_pressure.append({
                "selected": selected.identity, "reference_selected": reference.identity,
                "prediction_used": predicted,
                "remaining_work_priority": preferences.get(selected.identity),
                "context_tokens": selected.tokens, "marked_for_pause": marked,
                "remaining_actual_tokens": self.remaining_capacity(),
                "physical_capacity_credit": False,
            })

        try:
            resumed = self._resume()
            while self.remaining_capacity() < 0:
                acting = [p for p in self.programs.values() if p.active and p.acting]
                if acting:
                    ordered, predicted = order(acting)
                    selected = ordered[0]
                    reference = min(acting, key=lambda item: item.tokens)
                    self._pause(selected, timestamp)
                    record(selected, reference, predicted=predicted, marked=False)
                    continue
                reasoning = [
                    p for p in self.programs.values()
                    if p.active and not p.acting and not p.marked_for_pause
                ]
                if reasoning:
                    ordered, predicted = order(reasoning)
                    deficit = -self.remaining_capacity()
                    # Existing marks cover future release, never actual capacity.
                    planned = sum(
                        p.tokens + self.buffer_tokens for p in self.programs.values()
                        if p.active and p.marked_for_pause
                    )
                    for selected in ordered:
                        if planned >= deficit:
                            break
                        reference = min(
                            (p for p in reasoning if not p.marked_for_pause),
                            key=lambda item: item.tokens,
                        )
                        selected.marked_for_pause = True
                        planned += selected.tokens + self.buffer_tokens
                        record(selected, reference, predicted=predicted, marked=True)
                break
            return resumed
        finally:
            self._resume_now = None

    def _tick_tool_pressure(self, *, now):
        timestamp = _number(now, "tool-aware workflow pressure control")
        self._resume_now = timestamp
        preferences = {}

        def order(items):
            for item in items:
                if item.identity not in preferences:
                    preferences[item.identity] = (
                        self.pressure_preference(item, timestamp)
                        if self.pressure_preference is not None else None
                    )
            predicted = all(preferences[item.identity] is not None for item in items)
            return sorted(items, key=lambda item: (
                -preferences[item.identity] if predicted else 0,
                -item.tokens if predicted else item.tokens, item.queue_sequence,
            )), predicted

        def record(selected, reference, *, predicted, marked, candidates):
            entry = {
                "selected": selected.identity, "reference_selected": reference.identity,
                "prediction_used": predicted,
                "remaining_work_priority": preferences.get(selected.identity),
                "context_tokens": selected.tokens, "marked_for_pause": marked,
                "remaining_actual_tokens": self.remaining_capacity(),
                "tool_state": asdict(self.tool_state(selected, timestamp)),
                "tool_retention_weight": self.tool_state(selected, timestamp).weight,
                "physical_capacity_credit": False,
            }
            if self.observe_pressure_candidates:
                entry.update(
                    same_band_reference_selected=min(candidates, key=lambda item: item.tokens).identity,
                    eligible_candidates=[{
                        "invocation_id": item.identity, "context_tokens": item.tokens,
                        "remaining_work_priority": preferences.get(item.identity),
                        "tool_state": asdict(self.tool_state(item, timestamp)),
                        "queue_sequence": item.queue_sequence,
                    } for item in candidates],
                )
            self.last_pressure.append(entry)

        try:
            resumed = self._resume()
            while self.remaining_capacity() < 0:
                active = [item for item in self.programs.values() if item.active]
                blocked = [
                    item for item in active
                    if (state := self.tool_state(item, timestamp)).blocked
                    and state.pause_safe and not state.protected
                ]
                if blocked:
                    # Compare work within coarse retention bands; small forecast
                    # changes must not dominate every remaining-work decision.
                    bands = {
                        item.identity: int(4 * self.tool_state(item, timestamp).weight)
                        for item in blocked
                    }
                    band = min(bands.values())
                    ordered, predicted = order([
                        item for item in blocked if bands[item.identity] == band
                    ])
                    selected = ordered[0]
                    reference = min(
                        (item for item in active if item.acting), key=lambda item: item.tokens,
                        default=min(blocked, key=lambda item: item.tokens),
                    )
                    self._pause(selected, timestamp)
                    selected.marked_for_pause = False
                    record(selected, reference, predicted=predicted, marked=False, candidates=ordered)
                    continue
                reasoning = [
                    item for item in active if item.pending and not item.marked_for_pause
                    and not self.tool_state(item, timestamp).protected
                ]
                ordered, predicted = order(reasoning)
                deficit = -self.remaining_capacity()
                planned = sum(
                    item.tokens + self.buffer_tokens
                    for item in active if item.marked_for_pause
                )
                for selected in ordered:
                    if planned >= deficit:
                        break
                    reference = min(
                        (item for item in reasoning if not item.marked_for_pause),
                        key=lambda item: item.tokens,
                    )
                    selected.marked_for_pause = True
                    planned += selected.tokens + self.buffer_tokens
                    record(
                        selected, reference, predicted=predicted, marked=True,
                        candidates=(
                            [item for item in ordered if item is selected or not item.marked_for_pause]
                            if self.observe_pressure_candidates else ()
                        ),
                    )
                break
            return resumed
        finally:
            self._resume_now = None
