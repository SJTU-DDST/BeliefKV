"""Independent paper decision rules; no BeliefKV predictor or ranking imports.

These are policy components, not completed system reproductions. Physical
capabilities and runtime integration are qualified separately.
"""

from __future__ import annotations

from bisect import bisect_right, insort
from dataclasses import dataclass
import math
from typing import Callable, Sequence


def _number(value: float, name: str, *, minimum: float = 0.) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be finite and at least {minimum}")
    return float(value)


def _integer(value: int, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer at least {minimum}")
    return value


@dataclass(frozen=True)
class TTLChoice:
    seconds: float
    expected_net_seconds: float
    evidence: str
    samples: int


class ContinuumTTL:
    """Continnum v7, sections 4.1-4.3, including the missing paper estimator."""

    def __init__(self, *, cold_start_records: int = 100):
        self.cold_start_records = _integer(cold_start_records, "cold-start records")
        self.global_gaps: list[float] = []
        self.tool_gaps: dict[str, list[float]] = {}

    def observe_gap(self, tool: str, seconds: float) -> None:
        if type(tool) is not str or not tool:
            raise ValueError("an observed tool identity is required")
        sample = _number(seconds, "observed inter-request gap")
        insort(self.global_gaps, sample)
        insort(self.tool_gaps.setdefault(tool, []), sample)

    def choose(
        self, tool: str, *, evicted_queue_seconds: float,
        prefill_reload_seconds: float, memoryfulness: float,
    ) -> TTLChoice:
        queue = _number(evicted_queue_seconds, "evicted-request queue delay")
        restore = _number(prefill_reload_seconds, "profiled reconstruction time")
        eta = _number(memoryfulness, "memoryfulness", minimum=-1.)
        if eta > 1.:
            raise ValueError("memoryfulness must not exceed one")
        local = self.tool_gaps.get(tool, ())
        if len(self.global_gaps) <= self.cold_start_records:
            # The paper's cold-start model uses Exp(1), eta=1 and T initially 0.
            benefit = queue + restore
            ttl = math.log(benefit) if benefit > 1. else 0.
            return TTLChoice(
                ttl, -math.expm1(-ttl) * benefit - ttl, "exponential_cold_start", 0,
            )
        if len(local) > self.cold_start_records:
            records, evidence = local, "tool_empirical_cdf"
        else:
            records, evidence = self.global_gaps, "global_empirical_cdf"
        benefit = queue * eta + restore
        best_ttl, best_reward = 0., 0.
        # Between empirical-CDF jumps the objective decreases linearly.
        for ttl in dict.fromkeys(records):
            reward = bisect_right(records, ttl) / len(records) * benefit - ttl
            if reward > best_reward:
                best_ttl, best_reward = ttl, reward
        return TTLChoice(best_ttl, best_reward, evidence, len(records))


def continuum_public_ttl(
    observed_tool_gaps: Sequence[float], *, tool_identified: bool,
) -> float:
    """Public commit 316a587: mean gap <=2 seconds receives a 2 second pin."""
    if type(tool_identified) is not bool:
        raise ValueError("tool identification must be boolean")
    samples = [_number(value, "observed tool gap") for value in observed_tool_gaps]
    if not tool_identified:
        return 0.
    return 0. if samples and sum(samples) / len(samples) > 2. else 2.


def continuum_priority(
    *, preempted: bool, ttl_retained: bool, program_arrival: float, native_position: int,
) -> tuple:
    if type(preempted) is not bool or type(ttl_retained) is not bool:
        raise ValueError("preemption and TTL state must be observed booleans")
    return (
        not preempted, not ttl_retained,
        _number(program_arrival, "program arrival"),
        _integer(native_position, "native position"),
    )


def continuum_public_priority(
    *, ttl_retained: bool, program_arrival: float, native_position: int,
) -> tuple:
    """Public queue: retained program first, then program FCFS and stable ties."""
    if type(ttl_retained) is not bool:
        raise ValueError("TTL state must be an observed boolean")
    return (
        not ttl_retained, _number(program_arrival, "program arrival"),
        _integer(native_position, "native position"),
    )


@dataclass(frozen=True)
class ConcurConfig:
    additive_increase: int = 2
    multiplicative_decrease: float = .5
    low_usage: float = .2
    high_usage: float = .5
    low_hit_rate: float = .2

    def __post_init__(self):
        _integer(self.additive_increase, "additive increase", minimum=1)
        for name in ("multiplicative_decrease", "low_usage", "high_usage", "low_hit_rate"):
            value = _number(getattr(self, name), name)
            if value > 1.:
                raise ValueError(f"{name} must not exceed one")
        if not 0 < self.multiplicative_decrease < 1 or self.low_usage >= self.high_usage:
            raise ValueError("invalid AIMD factor or utilization thresholds")


class ConcurWindow:
    """CONCUR equation 1; a window regulates agents, not decode batch size."""

    def __init__(self, initial: int, *, maximum: int, config: ConcurConfig = ConcurConfig()):
        self.maximum = _integer(maximum, "maximum agents", minimum=1)
        self.window = _integer(initial, "initial agents", minimum=1)
        if self.window > maximum:
            raise ValueError("initial window exceeds population")
        self.config = config
        self.last_sample: int | None = None

    def observe(self, *, sample_id: int, gpu_usage: float, gpu_hit_rate: float | None) -> int:
        sample = _integer(sample_id, "sample ID")
        usage = _number(gpu_usage, "GPU KV utilization")
        hit = None if gpu_hit_rate is None else _number(gpu_hit_rate, "GPU prefix hit rate")
        if usage > 1 or hit is not None and hit > 1:
            raise ValueError("utilization and hit rate must be fractions")
        if self.last_sample is not None:
            if sample < self.last_sample:
                raise ValueError("control samples must be monotonic")
            if sample == self.last_sample:
                return self.window
        self.last_sample = sample
        if usage < self.config.low_usage:
            self.window = min(self.maximum, self.window + self.config.additive_increase)
        elif (
            usage > self.config.high_usage and hit is not None
            and hit < self.config.low_hit_rate
        ):
            self.window = max(1, math.floor(self.window * self.config.multiplicative_decrease))
        return self.window


@dataclass(frozen=True)
class Intercept:
    identity: str
    context_tokens: int
    estimated_wait_seconds: float
    order: int

    def __post_init__(self):
        if type(self.identity) is not str or not self.identity:
            raise ValueError("interception identity is required")
        _integer(self.context_tokens, "intercept context", minimum=1)
        _number(self.estimated_wait_seconds, "estimated interception duration")
        _integer(self.order, "interception order")


@dataclass(frozen=True)
class InterceptChoice:
    identity: str
    swap_tokens: int
    remaining_tokens: int
    remaining_action: str
    min_waste_token_seconds: float


def infercept_chunk_discard_waste(
    *, context_tokens: int, other_context_tokens: int, chunks: int,
    full_forward_seconds: float, chunk_forward_seconds: float,
) -> float:
    """InferCept equation 4 with the common bytes-per-token factor removed."""
    context = _integer(context_tokens, "context tokens")
    other = _integer(other_context_tokens, "other context tokens")
    count = _integer(chunks, "recompute chunks", minimum=1)
    full = _number(full_forward_seconds, "profiled full forward")
    chunk = _number(chunk_forward_seconds, "profiled chunk forward")
    return full * context / 2. + count * chunk * other


def infercept_decisions(
    intercepted: Sequence[Intercept], *, swap_out_tokens: int, free_host_tokens: int,
    discard_waste: Callable[[int], float],
) -> tuple[InterceptChoice, ...]:
    """InferCept section 4.3: spend swap budget in descending min-waste order.

    The caller must supply the paper's profiled per-iteration swap budget and
    recomputation cost. Neither is replaced with a duration threshold.
    """
    budget = min(
        _integer(swap_out_tokens, "swap-out budget"),
        _integer(free_host_tokens, "free Host tokens"),
    )
    if len({item.identity for item in intercepted}) != len(intercepted):
        raise ValueError("duplicate intercepted context")
    ranked = []
    for item in intercepted:
        keep = item.context_tokens * item.estimated_wait_seconds
        discard = _number(discard_waste(item.context_tokens), "profiled discard waste")
        ranked.append((min(keep, discard), item))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].order))
    result = []
    for waste, item in ranked:
        swapped = min(budget, item.context_tokens)
        budget -= swapped
        remaining = item.context_tokens - swapped
        if not remaining:
            action = "none"
        else:
            keep = remaining * item.estimated_wait_seconds
            discard = _number(discard_waste(remaining), "residual discard waste")
            action = "preserve" if keep <= discard else "discard"
        result.append(InterceptChoice(item.identity, swapped, remaining, action, waste))
    return tuple(result)


@dataclass
class ThunderProgram:
    identity: str
    tokens: int
    steps: int = 0
    acting: bool = False
    active: bool = False
    marked_for_pause: bool = False
    pending: bool = False
    queued_at: float = 0.
    queue_sequence: int = 0


class ThunderWorkingSet:
    """Single-backend projection of ThunderAgent's public working-set policy.

    Use exact token counts from the shared pretokenized ingress. Multi-node
    routing, character/token estimation and optional acting-token decay are
    outside this declared single-backend projection.
    """

    def __init__(
        self, capacity_tokens: int, *, acting_weight: float = 1.,
        buffer_tokens: int = 100, resume_eligible=None,
    ):
        self.capacity_tokens = _integer(capacity_tokens, "KV capacity", minimum=1)
        self.acting_weight = _number(acting_weight, "acting-token weight")
        self.buffer_tokens = _integer(buffer_tokens, "per-program buffer")
        self.programs: dict[str, ThunderProgram] = {}
        self._queue_sequence = 0
        self.resume_eligible = resume_eligible

    def _pause(self, item: ThunderProgram, now: float) -> None:
        self._queue_sequence += 1
        item.active, item.queued_at, item.queue_sequence = False, now, self._queue_sequence

    def remaining_capacity(self) -> int:
        active = [item for item in self.programs.values() if item.active]
        reasoning = sum(item.tokens for item in active if not item.acting)
        acting = sum(item.tokens for item in active if item.acting)
        return self.capacity_tokens - int(
            reasoning + self.acting_weight * acting
        ) - len(active) * self.buffer_tokens

    def arrive(self, identity: str, tokens: int, *, now: float) -> None:
        if type(identity) is not str or not identity:
            raise ValueError("program identity is required")
        size = _integer(tokens, "current context", minimum=1)
        arrived = _number(now, "arrival")
        item = self.programs.setdefault(identity, ThunderProgram(identity, size))
        if item.pending:
            raise ValueError("a program cannot have concurrent sequential calls")
        item.tokens, item.steps, item.acting, item.pending = size, item.steps + 1, False, True
        if item.steps == 1:
            others_waiting = any(
                other is not item and not other.active for other in self.programs.values()
            )
            if not others_waiting and size + self.buffer_tokens <= self.remaining_capacity():
                item.active = True
            else:
                self._pause(item, arrived)

    def completed(self, identity: str, tokens: int, *, now: float) -> None:
        item = self.programs[identity]
        if not item.pending:
            raise ValueError("program has no pending call")
        item.tokens = _integer(tokens, "completed context", minimum=1)
        item.pending, item.acting = False, True
        if item.marked_for_pause:
            item.marked_for_pause = False
            self._pause(item, _number(now, "completion"))

    def generated(self, identity: str, tokens: int) -> None:
        item = self.programs[identity]
        size = _integer(tokens, "observed streaming context", minimum=1)
        if not item.pending or item.acting or not item.active or size < item.tokens:
            raise ValueError("invalid observed ThunderAgent streaming progress")
        item.tokens = size

    def _resume(self) -> tuple[str, ...]:
        capacity = self.remaining_capacity()
        if capacity <= self.buffer_tokens:
            return ()
        paused = [
            item for item in self.programs.values() if not item.active
            and (self.resume_eligible is None or self.resume_eligible(item))
        ]
        paused.sort(key=lambda item: (
            1 if item.steps == 1 else 0 if not item.acting else 2,
            item.tokens, item.queue_sequence,
        ))
        selected, cumulative = [], 0
        for item in paused:
            required = item.tokens + self.buffer_tokens
            if cumulative + required <= capacity:
                selected.append(item)
                cumulative += required
        # Public BFD placement degenerates to descending size on one backend.
        selected.sort(key=lambda item: -item.tokens)
        for item in selected:
            item.active = True
        return tuple(item.identity for item in selected)

    def tick(self, *, now: float) -> tuple[str, ...]:
        timestamp = _number(now, "control tick")
        resumed = self._resume()
        while self.remaining_capacity() < 0:
            acting = sorted(
                (item for item in self.programs.values() if item.active and item.acting),
                key=lambda item: item.tokens,
            )
            if acting:
                self._pause(acting[0], timestamp)
                continue
            reasoning = sorted(
                (item for item in self.programs.values()
                 if item.active and not item.acting and not item.marked_for_pause),
                key=lambda item: item.tokens,
            )
            if not reasoning:
                break
            # Retain the public code's conservative marking behavior: marking
            # does not credit capacity before the call actually completes.
            reasoning[0].marked_for_pause = True
        return resumed

    def forget(self, identity: str) -> None:
        self.programs.pop(identity, None)


class ThunderActingDecayWorkingSet(ThunderWorkingSet):
    """Public opt-in acting decay for resume; pressure still uses full tokens."""

    def __init__(
        self, capacity_tokens: int, *, acting_weight: float = 1.,
        buffer_tokens: int = 100, resume_eligible=None,
    ):
        super().__init__(
            capacity_tokens, acting_weight=acting_weight, buffer_tokens=buffer_tokens,
            resume_eligible=resume_eligible,
        )
        self.acting_since: dict[str, float] = {}
        self._resume_now: float | None = None
        self.last_resume_capacity_tokens: int | None = None

    def arrive(self, identity: str, tokens: int, *, now: float) -> None:
        super().arrive(identity, tokens, now=now)
        self.acting_since.pop(identity, None)

    def completed(self, identity: str, tokens: int, *, now: float) -> None:
        timestamp = _number(now, "ACTING transition")
        super().completed(identity, tokens, now=timestamp)
        self.acting_since[identity] = timestamp

    def remaining_capacity_with_decay(self, *, now: float) -> int:
        timestamp = _number(now, "decay observation")
        reasoning, acting, count = 0, 0., 0
        for item in self.programs.values():
            if not item.active:
                continue
            count += 1
            if not item.acting:
                reasoning += item.tokens
            elif (since := self.acting_since.get(item.identity)) is not None:
                if since > timestamp:
                    raise ValueError("decay observation precedes the ACTING transition")
                acting += item.tokens * (2. ** -(timestamp - since))
        # The author's optional formula does not apply tool_coefficient here.
        return self.capacity_tokens - int(reasoning + acting) - count * self.buffer_tokens

    def _resume(self) -> tuple[str, ...]:
        if self._resume_now is None:
            raise ValueError("decay resume requires its current control timestamp")
        capacity = self.remaining_capacity_with_decay(now=self._resume_now)
        self.last_resume_capacity_tokens = capacity
        if capacity <= self.buffer_tokens:
            return ()
        paused = sorted(
            (
                item for item in self.programs.values() if not item.active
                and (self.resume_eligible is None or self.resume_eligible(item))
            ),
            key=lambda item: (
                1 if item.steps == 1 else 0 if not item.acting else 2,
                item.tokens, item.queue_sequence,
            ),
        )
        selected, cumulative = [], 0
        for item in paused:
            required = item.tokens + self.buffer_tokens
            if cumulative + required <= capacity:
                selected.append(item)
                cumulative += required
        selected.sort(key=lambda item: -item.tokens)
        for item in selected:
            item.active = True
        return tuple(item.identity for item in selected)

    def tick(self, *, now: float) -> tuple[str, ...]:
        self._resume_now = _number(now, "decay control tick")
        try:
            return super().tick(now=self._resume_now)
        finally:
            self._resume_now = None

    def forget(self, identity: str) -> None:
        super().forget(identity)
        self.acting_since.pop(identity, None)
