"""CPU policy prototype for an interruptible, layer-ordered H2D prefix.

This does not change native transfer execution. Activation requires a native
pause/resume primitive with valid per-layer fences and retained page ownership.
Costs must describe transfer-stream service and compute, not submit-to-ACK polls.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class LayerCost:
    full_bytes: int
    state_bytes: int
    transfer_ms: float
    compute_ms: float

    def __post_init__(self) -> None:
        if (
            any(type(value) is not int or value < 0 for value in (
                self.full_bytes, self.state_bytes,
            ))
            or not all(math.isfinite(value) and value >= 0 for value in (
                self.transfer_ms, self.compute_ms,
            ))
        ):
            raise ValueError("invalid layer costs")


@dataclass(frozen=True)
class LayerCostProfile:
    layers: tuple[LayerCost, ...]
    allocation_bytes: int
    before_forward_layer: int | None
    before_forward_copy_ms: float
    transfer_prefix_ms: tuple[float, ...]
    byte_prefix: tuple[int, ...]
    lead_finish_offset_ms: tuple[float, ...]
    backlog_finish_offset_ms: tuple[float, ...]
    compute_ms: float

    @classmethod
    def build(
        cls, layers: tuple[LayerCost, ...], *, allocation_bytes: int,
        before_forward_layer: int | None = None,
        before_forward_copy_ms: float = 0.,
    ) -> LayerCostProfile:
        if (
            not layers or any(not isinstance(layer, LayerCost) for layer in layers)
            or type(allocation_bytes) is not int or allocation_bytes < 0
            or before_forward_layer is not None and (
                type(before_forward_layer) is not int
                or not 0 <= before_forward_layer < len(layers)
            )
            or not math.isfinite(before_forward_copy_ms) or before_forward_copy_ms < 0
        ):
            raise ValueError("invalid layer profile")
        transfer, sizes = [0.], [0]
        for layer in layers:
            transfer.append(transfer[-1] + layer.transfer_ms)
            sizes.append(sizes[-1] + layer.full_bytes + layer.state_bytes)
        compute_suffix = [0.] * (len(layers) + 1)
        for index in range(len(layers) - 1, -1, -1):
            compute_suffix[index] = compute_suffix[index + 1] + layers[index].compute_ms
        bounds = [
            transfer[index + 1] + compute_suffix[index]
            for index in range(len(layers))
        ]
        prefix_max, suffix_max = [-math.inf], [-math.inf] * (len(layers) + 1)
        for value in bounds:
            prefix_max.append(max(prefix_max[-1], value))
        for index in range(len(layers) - 1, -1, -1):
            suffix_max[index] = max(suffix_max[index + 1], bounds[index])
        base_compute = compute_suffix[0] + before_forward_copy_ms
        barrier_bound = (
            transfer[before_forward_layer + 1] + base_compute
            if before_forward_layer is not None else -math.inf
        )
        lead_offsets, backlog_offsets = [], []
        for depth in range(len(layers) + 1):
            barrier_pending = before_forward_layer is not None and depth <= before_forward_layer
            future = max(suffix_max[depth], barrier_bound if barrier_pending else -math.inf)
            past = max(prefix_max[depth], -math.inf if barrier_pending else barrier_bound)
            lead_offsets.append(max(base_compute, future - transfer[depth]))
            backlog_offsets.append(max(past, future))
        return cls(
            tuple(layers), allocation_bytes, before_forward_layer, before_forward_copy_ms,
            tuple(transfer), tuple(sizes), tuple(lead_offsets), tuple(backlog_offsets),
            compute_suffix[0],
        )

    def completion_ms(self, depth: int, *, lead_ms: float, backlog_ms: float) -> float:
        """Completion under the modeled pipeline, using two cached envelopes."""
        # Inputs are validated once by the caller, outside the depth scan.
        return max(
            lead_ms + self.lead_finish_offset_ms[depth],
            backlog_ms + self.backlog_finish_offset_ms[depth],
        )


@dataclass(frozen=True)
class LeadTimeScenario:
    lead_ms: float
    probability: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) and value >= 0 for value in (
            self.lead_ms, self.probability,
        )):
            raise ValueError("invalid lead scenario")


@dataclass(frozen=True)
class LayerPrefetchBudget:
    allocation_bytes: int
    transfer_bytes: int
    transfer_ms: float
    backlog_ms: float = 0.
    continuation_probability: float = 1.
    transfer_shadow_price: float = 0.
    residency_price_ms_per_gib_second: float = 0.
    control_ms: float = 0.

    def __post_init__(self) -> None:
        if (
            any(type(value) is not int or value < 0 for value in (
                self.allocation_bytes, self.transfer_bytes,
            ))
            or not all(math.isfinite(value) and value >= 0 for value in (
                self.transfer_ms, self.backlog_ms, self.continuation_probability,
                self.transfer_shadow_price, self.residency_price_ms_per_gib_second,
                self.control_ms,
            ))
            or self.continuation_probability > 1.
        ):
            raise ValueError("invalid layer prefetch budget")


@dataclass(frozen=True)
class LayerPrefetchDecision:
    depth: int
    transfer_bytes: int
    allocation_bytes: int
    conditional_latency_saved_ms: float
    expected_net_saved_ms: float
    feasible_depths: int
    reason: str


def choose_prefetch_depth(
    profile: LayerCostProfile, scenarios: tuple[LeadTimeScenario, ...], *,
    budget: LayerPrefetchBudget,
) -> LayerPrefetchDecision:
    """Scan cached layer costs; at most eight arrival scenarios are allowed.

    Prices and capacities belong to runtime, not the child/tool predictor.
    A false continuation charges its speculative transfer service as waste.
    HBM charges the entire reserved page allocation even for a shallow prefix.
    """
    if not 1 <= len(scenarios) <= 8 or not math.isclose(
        sum(item.probability for item in scenarios), 1., abs_tol=1e-9,
    ):
        raise ValueError("one to eight normalized lead scenarios required")
    if profile.allocation_bytes > budget.allocation_bytes:
        return LayerPrefetchDecision(0, 0, 0, 0., 0., 0, "allocation_budget")
    ordered = sorted(scenarios, key=lambda item: item.lead_ms)
    leads = tuple(item.lead_ms for item in ordered)
    probabilities, weighted_leads = [0.], [0.]
    for item in ordered:
        probabilities.append(probabilities[-1] + item.probability)
        weighted_leads.append(weighted_leads[-1] + item.lead_ms * item.probability)
    mean_lead = weighted_leads[-1]

    def expected_finish(depth):
        offset = profile.lead_finish_offset_ms[depth]
        threshold = budget.backlog_ms + profile.backlog_finish_offset_ms[depth] - offset
        split = bisect_left(leads, threshold)
        # E[max(T + a, Q + b)] from a small prefix-CDF, without simulating layers.
        return offset + mean_lead + threshold * probabilities[split] - weighted_leads[split]

    baseline = expected_finish(0)
    resident_cost = (
        profile.allocation_bytes / 1024 ** 3
        * mean_lead / 1000.
        * budget.residency_price_ms_per_gib_second
    )
    best_depth, best_gain, best_score, feasible = 0, 0., 0., 0
    for depth in range(1, len(profile.layers) + 1):
        size, service = profile.byte_prefix[depth], profile.transfer_prefix_ms[depth]
        if size > budget.transfer_bytes or service > budget.transfer_ms:
            break
        if size == 0:
            continue
        feasible += 1
        gain = baseline - expected_finish(depth)
        score = (
            budget.continuation_probability * gain
            - (1. - budget.continuation_probability + budget.transfer_shadow_price) * service
            - resident_cost - budget.control_ms
        )
        if score > best_score + 1e-12:
            best_depth, best_gain, best_score = depth, gain, score
    return LayerPrefetchDecision(
        best_depth, profile.byte_prefix[best_depth],
        profile.allocation_bytes if best_depth else 0,
        best_gain, best_score, feasible,
        "positive_expected_gain" if best_depth else "no_positive_expected_gain",
    )
