# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
"""TokenCake public policy port and explicitly separate paper temporal helpers.

Public score, type history, partition and benefit formulas follow commit
1901ebf31fdf63590148e61d5c2f65e9fb7ec010. Paper upload budgets implement
arXiv:2510.18586v5 equations 3-4; they do not submit physical transfers.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
import math


@dataclass(frozen=True)
class TokenCakeMetadata:
    agent_type: str = ""
    agent_name: str = ""
    importance: float = 0.
    depth: int = 0
    in_degree: int = 0
    out_degree: int = 0
    similarity: float = 0.
    application_started_at_s: float = 0.
    application_start_offset_s: float = 0.
    application_elapsed_s: float = 0.
    application_max_depth: int = 0
    remaining_depth: int = 0
    critical_path: bool = False
    near_completion: bool = False
    join_group: str = ""
    dependency_depth: int = 0
    fanout_width: int = 1
    memory_weight: float = 1.
    offload_eligible: bool = False
    reusable_prefix: bool = False

    def __post_init__(self):
        numeric = (
            self.importance, self.similarity, self.application_started_at_s,
            self.application_start_offset_s, self.application_elapsed_s, self.memory_weight,
        )
        counts = (
            self.depth, self.in_degree, self.out_degree, self.application_max_depth,
            self.remaining_depth, self.dependency_depth,
        )
        if (
            any(type(value) not in (float, int) or not math.isfinite(value) or value < 0 for value in numeric)
            or self.similarity > 1
            or any(type(value) is not int or value < 0 for value in counts)
            or type(self.fanout_width) is not int or self.fanout_width < 1
        ):
            raise ValueError("invalid observed or application-declared TokenCake metadata")


def request_score(metadata: TokenCakeMetadata, arrival_time: float, now: float) -> int:
    depth = metadata.depth
    max_depth = max(depth, metadata.application_max_depth, 1)
    remaining = metadata.remaining_depth
    remaining_ratio = min(1.0, remaining / max_depth)
    progress = min(1.0, depth / max_depth)
    elapsed = metadata.application_elapsed_s
    if metadata.application_started_at_s > 0:
        elapsed = max(elapsed, now - metadata.application_started_at_s)
    wait = max(0.0, now - arrival_time)
    similarity = metadata.similarity
    parallel_width = 1 / similarity if 0 < similarity < 1 else 1.0
    age = min(elapsed, 300) * (1 + 0.75 * remaining_ratio)
    queue = min(wait, 180) * (1 + 0.5 * remaining_ratio)
    completion = min(elapsed, 240) * progress
    return int(
        100 * metadata.importance
        + 15 * depth + 45 * metadata.out_degree + 10 * metadata.in_degree
        + 20 * similarity + 20 * remaining + 120 * (parallel_width - 1)
        + 6 * min(metadata.application_start_offset_s, 60)
        + 5 * age + 8 * queue + 5 * completion
        + 180 * metadata.critical_path + 90 * metadata.near_completion
        + 20 * bool(metadata.join_group) + 4 * (metadata.dependency_depth or depth)
        + 12 * max(metadata.fanout_width - 1, 0) - 8 * max(metadata.memory_weight - 1, 0)
    )


@dataclass
class AgentHistory:
    importance: float = 0.
    deferrals: int = 0
    preemptions: int = 0
    samples: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    completed: int = 0
    duration: float = 0.
    depth: int = 0
    out_degree: int = 0
    in_degree: int = 0
    elapsed: float = 0.
    remaining: int = 0

    def score(self, importance: float, waiting: int, average_wait: float) -> float:
        urgency = (
            math.log1p(average_wait) + math.log1p(waiting)
            + math.log1p(self.deferrals) + 1.5 * math.log1p(self.preemptions)
        )
        tokens = (self.input_tokens + self.output_tokens) / max(1, self.samples)
        duration = self.duration / max(1, self.completed)
        cost = math.log1p(tokens)
        if duration > 0 and tokens > 0:
            cost += math.log1p(duration) + math.log1p(tokens / duration)
        count = max(1, self.samples)
        graph = (
            0.7 * self.depth / count + 0.5 * self.out_degree / count
            + 0.25 * self.in_degree / count + 0.20 * self.remaining / count
            + 0.10 * min(self.elapsed / count, 180)
        )
        return max(1.0, 2 * importance + urgency + 0.5 * cost + graph)


@dataclass
class CapacityPlan:
    critical: set[str] = field(default_factory=set)
    scores: dict[str, float] = field(default_factory=dict)
    reserved: dict[str, int] = field(default_factory=dict)
    shared: int = 0


def partition_capacity(scores, importance, used, total, reserve_ratio, critical_ratio):
    ordered = sorted(scores, key=lambda key: (scores[key], importance[key], key), reverse=True)
    critical = set(ordered[:max(1, int(len(ordered) * critical_ratio))])
    reserved = int(total * reserve_ratio) if critical else 0
    score_sum, used_sum = sum(scores[key] for key in critical), sum(used.values())
    weights = {}
    for key in sorted(critical):
        score_share = scores[key] / score_sum
        memory_share = used.get(key, 0) / used_sum if used_sum else 0.
        weights[key] = max(score_share, (memory_share + score_share) / 2, 1e-6)
    weight_sum = sum(weights.values())
    exact = {key: reserved * weight / weight_sum for key, weight in weights.items()}
    counts = {key: int(value) for key, value in exact.items()}
    remainder = reserved - sum(counts.values())
    for key in sorted(exact, key=lambda key: (exact[key] - counts[key], key), reverse=True)[:remainder]:
        counts[key] += 1
    return CapacityPlan(critical, scores, counts, total - reserved)


@dataclass(frozen=True)
class OffloadSettings:
    min_gpu_usage: float = .60
    high_pressure_gpu_usage: float = .85
    score_threshold: float = 1.


@dataclass(frozen=True)
class Pressure:
    usage: float
    free: int
    waiting_count: int
    waiting_demand: int
    critical_demand: int
    shared_available: int
    fit_demand: int


class DecisionReason(str, Enum):
    SELECTED = "selected"
    NOT_ELIGIBLE = "not_eligible"
    EMPTY = "empty"
    NO_WAITING_DEMAND = "no_waiting_demand"
    CPU_CAPACITY = "cpu_capacity"
    UNPROFITABLE = "unprofitable"
    LOW_PRESSURE = "low_pressure"


@dataclass(frozen=True)
class Benefit:
    reason: DecisionReason
    score: float


def public_offload_benefit(
    settings: OffloadSettings, metadata: TokenCakeMetadata, pressure: Pressure, *,
    blocks: int, cpu_available: int, cpu_needed: int, duration: float, transfer_time: float,
    agent_score: float = 0., churn_penalty: float = 0.,
) -> Benefit:
    importance = min(10., max(metadata.importance, min(10., agent_score / 2)))
    near_completion = metadata.near_completion or (
        metadata.application_max_depth > 0 and metadata.remaining_depth <= 1
    )
    margin = duration - transfer_time
    pressure_score = max(0., (pressure.usage - settings.min_gpu_usage) * 6) + min(
        2., pressure.waiting_demand / max(1, blocks + 1),
    )
    fit_score = min(2., pressure.fit_demand / max(1, blocks))
    stall_score = min(6., margin / max(.25, transfer_time))
    upload_safety = 1. if duration >= 2 * transfer_time else -1.
    cpu_score = 1. if cpu_available >= cpu_needed else -4.
    score = (
        stall_score + pressure_score + fit_score + upload_safety + cpu_score
        - .35 * importance - (2. if near_completion else 0.) - churn_penalty
    )
    emergency = (
        pressure.usage >= settings.high_pressure_gpu_usage
        and margin >= max(4 * transfer_time, 5.) and pressure.waiting_count > 0
    )
    low_pressure = pressure.usage < settings.min_gpu_usage
    no_queue_pressure = (
        pressure.waiting_demand <= pressure.free
        and pressure.critical_demand <= pressure.shared_available
    )
    reason = DecisionReason.SELECTED
    if not metadata.offload_eligible or not metadata.reusable_prefix:
        reason = DecisionReason.NOT_ELIGIBLE
    elif blocks <= 0:
        reason = DecisionReason.EMPTY
    elif pressure.fit_demand <= 0:
        reason = DecisionReason.NO_WAITING_DEMAND
    elif cpu_available < cpu_needed:
        reason = DecisionReason.CPU_CAPACITY
    elif duration <= transfer_time or (
        margin < max(.05, 2 * transfer_time) and not emergency
    ):
        reason = DecisionReason.UNPROFITABLE
    elif low_pressure and (
        no_queue_pressure or pressure.fit_demand < blocks or pressure.waiting_count == 0
    ):
        reason = DecisionReason.LOW_PRESSURE
    elif (
        importance >= 8. and not emergency
        and (near_completion or margin < max(.5, 8 * transfer_time) or upload_safety < 0)
        or score < settings.score_threshold and not emergency
    ):
        reason = DecisionReason.UNPROFITABLE
    return Benefit(reason, score)


class DurationHistory:
    """Event-only EWMA with the public cold start and user/history blend."""

    def __init__(self, *, alpha=.5, beta=.5, default_seconds=1., limit=4096):
        if (
            not 0 < alpha <= 1 or not 0 <= beta <= 1
            or not math.isfinite(default_seconds) or default_seconds <= 0
            or type(limit) is not int or limit <= 0
        ):
            raise ValueError("invalid TokenCake forecast parameters")
        self.alpha, self.beta, self.default, self.limit = alpha, beta, default_seconds, limit
        self.history: OrderedDict[tuple, float] = OrderedDict()

    def predict(self, key, *, user_estimate=None):
        if user_estimate is not None and (
            not math.isfinite(user_estimate) or user_estimate < 0
        ):
            raise ValueError("invalid declared tool duration")
        history = self.history.get(key) if key is not None else None
        if history is not None:
            self.history.move_to_end(key)
        if user_estimate is None:
            return self.default if history is None else history
        return user_estimate if history is None else (
            self.beta * user_estimate + (1 - self.beta) * history
        )

    def observe(self, key, *, elapsed_seconds):
        if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
            raise ValueError("invalid observed tool duration")
        if key is None:
            return
        previous = self.history.pop(key, None)
        self.history[key] = (
            elapsed_seconds if previous is None
            else self.alpha * elapsed_seconds + (1 - self.alpha) * previous
        )
        while len(self.history) > self.limit:
            self.history.popitem(last=False)


@dataclass(frozen=True)
class WaitingWork:
    request_id: str
    incremental_blocks: int
    estimated_work_tokens: int
    native_eligible: bool = True


def first_fit_work(waiting, *, freed_blocks, window_seconds, observed_tokens_per_second):
    if window_seconds <= 0 or observed_tokens_per_second <= 0:
        return None
    token_capacity = window_seconds * observed_tokens_per_second
    for item in waiting:
        if (
            item.native_eligible and 0 < item.incremental_blocks <= freed_blocks
            and 0 <= item.estimated_work_tokens <= token_capacity
        ):
            return item.request_id
    return None


@dataclass(frozen=True)
class UploadCandidate:
    agent_id: str
    remaining_blocks: int
    reservation_deficit: int
    importance: float
    urgency: float
    predicted_finish_s: float


def paper_upload_reservations(candidates, *, gpu_free, critical_waiting_demand, shared_free):
    """Paper equations 3-4. I+U ranking is separate from real H2D ownership."""
    if any(type(value) is not int or value < 0 for value in (
        gpu_free, critical_waiting_demand, shared_free,
    )):
        raise ValueError("invalid TokenCake upload capacity")
    candidates = tuple(candidates)
    if len({item.agent_id for item in candidates}) != len(candidates) or any(
        not item.agent_id or type(item.remaining_blocks) is not int or item.remaining_blocks < 0
        or type(item.reservation_deficit) is not int
        or not 0 <= item.reservation_deficit <= item.remaining_blocks
        or any(not math.isfinite(value) or value < 0 for value in (
            item.importance, item.urgency, item.predicted_finish_s,
        ))
        for item in candidates
    ):
        raise ValueError("invalid TokenCake upload candidate")
    budget = max(0, gpu_free - max(0, critical_waiting_demand - shared_free))
    grants = {}
    for item in sorted(candidates, key=lambda item: (
        -(item.importance + item.urgency), item.predicted_finish_s, item.agent_id,
    )):
        grant = min(item.remaining_blocks, (item.reservation_deficit + 1) // 2, budget)
        if grant:
            grants[item.agent_id] = grant
            budget -= grant
    return grants
