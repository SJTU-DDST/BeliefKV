from __future__ import annotations

import heapq
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping

from beliefkv.oracle.contracts import (
    FrozenActionBoundaryKind,
    FrozenAgentDemand,
    FrozenInvocationDemand,
    FrozenJoinDemand,
    FrozenJoinMode,
    FrozenLLMCallDemand,
    LogicalInvocationKey,
)
from beliefkv.oracle.physical_sidecar import FrozenPhysicalCall, FrozenPhysicalSidecar
from beliefkv.predictor.hardware_service import (
    GPURequestServiceDemand,
    GPUServiceCurveModel,
    GPUServiceFeatures,
)


class CPUOracleArm(str, Enum):
    C0_CURRENT = "c0_current"
    C1_AGENT = "c1_agent"
    C2_KV = "c2_kv"
    C3_JOINT = "c3_joint"

    @property
    def agent_future(self) -> bool:
        return self in {CPUOracleArm.C1_AGENT, CPUOracleArm.C3_JOINT}

    @property
    def kv_future(self) -> bool:
        return self in {CPUOracleArm.C2_KV, CPUOracleArm.C3_JOINT}


class ServiceEnvelope(str, Enum):
    SLOW = "slow_p95_graph16"
    NOMINAL = "nominal_p50_graph16"
    GRAPH32_SENSITIVITY = "graph32_sensitivity"


class PlannerOverhead(str, Enum):
    ZERO = "zero_overhead"
    MEASURED_FASTPATH = "measured_fastpath"


class ExecutionPackageKind(str, Enum):
    OBSERVED = "observed"
    MIN_REMAINING_DEMAND = "min_remaining_demand"
    ACTION_UNLOCK = "action_unlock"
    MAX_BATCH_FILL = "max_batch_fill"


class WholeRunExecutionPolicy(str, Enum):
    """One execution rule held fixed for an entire counterfactual rollout."""

    C0_OBSERVED = "c0_observed"
    PACKAGE_OBSERVED = "observed"
    MIN_REMAINING_DEMAND = "min_remaining_demand"
    ACTION_UNLOCK = "action_unlock"
    MAX_BATCH_FILL = "max_batch_fill"

    @property
    def package_kind(self) -> ExecutionPackageKind | None:
        if self == WholeRunExecutionPolicy.C0_OBSERVED:
            return None
        return ExecutionPackageKind(self.value)


@dataclass(frozen=True)
class CPUOracleConfig:
    hbm_capacity_tokens: int = 850_000
    host_capacity_bytes: int = 96 * 1024**3
    max_running_requests: int = 32
    prefill_chunk_tokens: int = 16_384
    pressure_threshold: float = 0.80
    starvation_floor_ms: float = 30_000.0
    measured_fastpath_ms: float = 0.413
    measured_physical_validation_ms: float = 5.0
    transfer_commit_guard_ms: float = 25.0
    execution_package_width: int = 4
    control_quantum_tokens: int = 256

    def __post_init__(self) -> None:
        if min(
            self.hbm_capacity_tokens,
            self.host_capacity_bytes,
            self.max_running_requests,
            self.prefill_chunk_tokens,
        ) <= 0:
            raise ValueError("CPU oracle capacities must be positive")
        if (
            self.transfer_commit_guard_ms < 0
            or self.execution_package_width <= 0
            or self.control_quantum_tokens <= 0
        ):
            raise ValueError("CPU oracle planning limits are invalid")


@dataclass(frozen=True)
class CPUOracleEstimateResult:
    arm: CPUOracleArm
    service_envelope: ServiceEnvelope
    planner_overhead: PlannerOverhead
    execution_policy: WholeRunExecutionPolicy
    workflow_count: int
    makespan_ms: float
    workflows_per_hour: float
    workflow_jct_ms: Mapping[str, float]
    decode_batch_counts: Mapping[int, int]
    decode_batch_mean: float
    prefill_batch_counts: Mapping[int, int]
    action_unlock_mean_ms: float
    hbm_peak_tokens: int
    hbm_peak_pressure: float
    hbm_pressure_duration_ms: float
    d2h_bytes: int
    h2d_bytes: int
    recompute_tokens: int
    planner_overhead_ms: float
    transfer_count: int
    kv_opportunity_count: int
    opportunity_pair_count: int
    eviction_opportunity_window_count: int
    eviction_unique_victim_byte_ms: float
    eviction_blocked_beneficiary_work_ms: float
    stall_free_round_trip_window_count: int
    stall_free_unique_victim_byte_ms: float
    stall_free_blocked_beneficiary_work_ms: float
    net_positive_opportunity_window_count: int
    net_positive_unique_victim_byte_ms: float
    net_positive_blocked_beneficiary_work_ms: float
    joint_opportunity_window_count: int
    joint_opportunity_byte_ms: float
    hbm_blocked_ready_work_ms: float
    max_oracle_reclaimable_bytes: int
    opportunity_victim_count: int
    opportunity_victim_reentry_count: int
    opportunity_victim_reentry_ratio: float
    proactive_shadow_count: int
    proactive_shadow_bytes: int
    shadow_commit_count: int
    latest_prefetch_count: int
    execution_package_counts: Mapping[str, int]
    cross_run_prefix_identity: str

    def to_dict(self) -> dict[str, object]:
        return {
            "arm": self.arm.value,
            "service_envelope": self.service_envelope.value,
            "planner_overhead": self.planner_overhead.value,
            "execution_policy": self.execution_policy.value,
            "workflow_count": self.workflow_count,
            "makespan_ms": self.makespan_ms,
            "workflows_per_hour": self.workflows_per_hour,
            "workflow_jct_ms": dict(sorted(self.workflow_jct_ms.items())),
            "decode_batch_counts": {
                str(key): value for key, value in sorted(self.decode_batch_counts.items())
            },
            "decode_batch_mean": self.decode_batch_mean,
            "prefill_batch_counts": {
                str(key): value for key, value in sorted(self.prefill_batch_counts.items())
            },
            "action_unlock_mean_ms": self.action_unlock_mean_ms,
            "hbm_peak_tokens": self.hbm_peak_tokens,
            "hbm_peak_pressure": self.hbm_peak_pressure,
            "hbm_pressure_duration_ms": self.hbm_pressure_duration_ms,
            "d2h_bytes": self.d2h_bytes,
            "h2d_bytes": self.h2d_bytes,
            "recompute_tokens": self.recompute_tokens,
            "planner_overhead_ms": self.planner_overhead_ms,
            "transfer_count": self.transfer_count,
            "kv_opportunity_count": self.kv_opportunity_count,
            "opportunity_pair_count": self.opportunity_pair_count,
            "eviction_opportunity_window_count": self.eviction_opportunity_window_count,
            "eviction_unique_victim_byte_ms": self.eviction_unique_victim_byte_ms,
            "eviction_blocked_beneficiary_work_ms": self.eviction_blocked_beneficiary_work_ms,
            "stall_free_round_trip_window_count": self.stall_free_round_trip_window_count,
            "stall_free_unique_victim_byte_ms": self.stall_free_unique_victim_byte_ms,
            "stall_free_blocked_beneficiary_work_ms": self.stall_free_blocked_beneficiary_work_ms,
            "net_positive_opportunity_window_count": self.net_positive_opportunity_window_count,
            "net_positive_unique_victim_byte_ms": self.net_positive_unique_victim_byte_ms,
            "net_positive_blocked_beneficiary_work_ms": self.net_positive_blocked_beneficiary_work_ms,
            "joint_opportunity_window_count": self.joint_opportunity_window_count,
            "joint_opportunity_byte_ms": self.joint_opportunity_byte_ms,
            "hbm_blocked_ready_work_ms": self.hbm_blocked_ready_work_ms,
            "max_oracle_reclaimable_bytes": self.max_oracle_reclaimable_bytes,
            "opportunity_victim_count": self.opportunity_victim_count,
            "opportunity_victim_reentry_count": self.opportunity_victim_reentry_count,
            "opportunity_victim_reentry_ratio": self.opportunity_victim_reentry_ratio,
            "proactive_shadow_count": self.proactive_shadow_count,
            "proactive_shadow_bytes": self.proactive_shadow_bytes,
            "shadow_commit_count": self.shadow_commit_count,
            "latest_prefetch_count": self.latest_prefetch_count,
            "execution_package_counts": dict(sorted(self.execution_package_counts.items())),
            "cross_run_prefix_identity": self.cross_run_prefix_identity,
        }


@dataclass
class _TrieNode:
    refs: int = 0
    children: dict[int, "_TrieNode"] = field(default_factory=dict)


class _ContextRadixResidency:
    def __init__(self) -> None:
        self.root = _TrieNode()
        self.paths: dict[LogicalInvocationKey, tuple[int, ...]] = {}
        self.leaves: dict[LogicalInvocationKey, _TrieNode] = {}
        self.unique_tokens = 0

    def contains(self, owner: LogicalInvocationKey) -> bool:
        return owner in self.paths

    def path(self, owner: LogicalInvocationKey) -> tuple[int, ...]:
        return self.paths.get(owner, ())

    def prefix_hit(self, path: tuple[int, ...]) -> int:
        node = self.root
        count = 0
        for symbol in path:
            child = node.children.get(symbol)
            if child is None or child.refs <= 0:
                break
            count += 1
            node = child
        return count

    def prefix_hit_for_owner(
        self, owner: LogicalInvocationKey, path: tuple[int, ...]
    ) -> int:
        current = self.paths.get(owner)
        if (
            current is not None
            and len(path) >= len(current)
            and path[: len(current)] == current
        ):
            return len(current)
        return self.prefix_hit(path)

    def marginal_tokens(self, path: tuple[int, ...]) -> int:
        return len(path) - self.prefix_hit(path)

    def reclaimable_tokens(self, owner: LogicalInvocationKey) -> int:
        path = self.paths.get(owner, ())
        if not path:
            return 0
        node = self.root
        reclaimable = 0
        for symbol in path:
            child = node.children.get(symbol)
            if child is None:
                raise RuntimeError("Radix owner path is inconsistent")
            if child.refs == 1:
                reclaimable += 1
            node = child
        return reclaimable

    def replace(self, owner: LogicalInvocationKey, path: tuple[int, ...]) -> int:
        before = self.unique_tokens
        current = self.paths.get(owner)
        if current == path:
            return 0
        if (
            current is not None
            and len(path) >= len(current)
            and path[: len(current)] == current
        ):
            node = self.leaves[owner]
            for symbol in path[len(current) :]:
                child = node.children.get(symbol)
                if child is None:
                    child = _TrieNode()
                    node.children[symbol] = child
                if child.refs == 0:
                    self.unique_tokens += 1
                child.refs += 1
                node = child
            self.paths[owner] = path
            self.leaves[owner] = node
            return self.unique_tokens - before
        if current is not None:
            self.remove(owner)
        self.leaves[owner] = self._add(path)
        self.paths[owner] = path
        return self.unique_tokens - before

    def remove(self, owner: LogicalInvocationKey) -> int:
        path = self.paths.pop(owner, ())
        self.leaves.pop(owner, None)
        if not path:
            return 0
        before = self.unique_tokens
        node = self.root
        stack: list[tuple[_TrieNode, int, _TrieNode]] = []
        for symbol in path:
            child = node.children.get(symbol)
            if child is None:
                raise RuntimeError("Radix owner path is inconsistent")
            stack.append((node, symbol, child))
            child.refs -= 1
            node = child
        for parent, symbol, child in reversed(stack):
            if child.refs == 0:
                self.unique_tokens -= 1
                if not child.children or all(
                    item.refs == 0 for item in child.children.values()
                ):
                    parent.children.pop(symbol, None)
            else:
                break
        return before - self.unique_tokens

    def _add(self, path: tuple[int, ...]) -> _TrieNode:
        node = self.root
        for symbol in path:
            child = node.children.get(symbol)
            if child is None:
                child = _TrieNode()
                node.children[symbol] = child
            if child.refs == 0:
                self.unique_tokens += 1
            child.refs += 1
            node = child
        return node


@dataclass
class _InvocationState:
    demand: FrozenInvocationDemand
    created: bool = False
    completed: bool = False
    next_call_index: int = 0
    pending_tools: int = 0
    ready_since_ms: float | None = None
    tool_completion_ms: float | None = None


@dataclass
class _CallState:
    invocation: LogicalInvocationKey
    demand: FrozenLLMCallDemand
    physical: FrozenPhysicalCall
    ready_since_ms: float
    ready_sequence: int
    admitted_ms: float | None = None
    prefill_remaining: int = 0
    prefilled_tokens: int = 0
    decode_remaining: int = 0
    decoded_tokens: int = 0
    cache_hit_tokens: int = 0


@dataclass(frozen=True)
class _JointOpportunity:
    victim: LogicalInvocationKey
    beneficiary: tuple[LogicalInvocationKey, int]
    reclaimable_bytes: int
    slack_ms: float
    d2h_ms: float
    h2d_ms: float
    beneficiary_gain_ms: float
    restore_stall_ms: float
    stall_free_round_trip: bool
    net_positive: bool


@dataclass
class _TransferState:
    owner: LogicalInvocationKey
    direction: str
    path: tuple[int, ...]
    bytes: int
    completion_ms: float
    reserved_tokens: int = 0
    evict_on_complete: bool = True
    latest_start_prefetch: bool = False


class _ServiceEstimator:
    GRAPH16_BS32_MS = 48.40
    GRAPH32_BS32_MS = 6.26

    def __init__(
        self,
        model: GPUServiceCurveModel,
        envelope: ServiceEnvelope,
        runtime_profile: Mapping[
            str, Mapping[int, Mapping[str, float]]
        ] | None = None,
        runtime_statistic: str | None = None,
    ) -> None:
        self.model = model
        self.envelope = envelope
        self.runtime_profile = runtime_profile or {}
        if runtime_statistic not in {None, "p50_ms", "p95_ms", "mean_ms"}:
            raise ValueError("invalid runtime service statistic")
        self.runtime_statistic = runtime_statistic
        self.cache: dict[tuple[object, ...], float] = {}

    def _runtime_ms(self, phase: str, batch: int) -> float | None:
        rows = self.runtime_profile.get(phase, {})
        if not rows:
            return None
        nearest = min(rows, key=lambda value: abs(value - batch))
        quantile = self.runtime_statistic or (
            "p95_ms"
            if self.envelope == ServiceEnvelope.SLOW
            else "p50_ms"
        )
        value = float(rows[nearest].get(quantile, 0.0))
        return value if value > 0 else None

    def _runtime_tail_ratio(self, phase: str, batch: int) -> float:
        rows = self.runtime_profile.get(phase, {})
        if not rows:
            return 1.0
        nearest = min(rows, key=lambda value: abs(value - batch))
        p50 = float(rows[nearest].get("p50_ms", 0.0))
        p95 = float(rows[nearest].get("p95_ms", 0.0))
        return max(1.0, p95 / max(p50, 1e-9))

    def prefill_ms(
        self, *, sequence_tokens: int, token_delta: int, first_chunk: bool
    ) -> float:
        runtime_ms = self._runtime_ms("prefill", 1)
        if runtime_ms is not None:
            return runtime_ms
        key = (
            "prefill",
            min(64, sequence_tokens // 4096),
            token_delta,
            first_chunk,
            self.envelope,
        )
        if key not in self.cache:
            estimate = self.model.predict(
                GPUServiceFeatures(
                    phase="prefill",
                    request_demands=(
                        GPURequestServiceDemand(sequence_tokens, token_delta),
                    ),
                    chunk_position="first" if first_chunk else "continuation",
                )
            )
            if estimate.source == "unavailable":
                raise RuntimeError("GPU prefill service estimate is unavailable")
            self.cache[key] = (
                estimate.p95_ms
                if self.envelope == ServiceEnvelope.SLOW
                else estimate.p50_ms
            )
        return self.cache[key]

    def decode_ms(self, sequence_tokens: tuple[int, ...]) -> float:
        batch = len(sequence_tokens)
        seq_bucket = tuple(min(64, item // 4096) for item in sequence_tokens[:4])
        key = ("decode", batch, seq_bucket, self.envelope)
        if key in self.cache:
            return self.cache[key]
        base = self._runtime_ms("decode", batch)
        if base is None:
            sampled = sequence_tokens[: min(batch, 4)]
            estimate = self.model.predict(
                GPUServiceFeatures(
                    phase="decode",
                    request_demands=tuple(
                        GPURequestServiceDemand(item, 1) for item in sampled
                    ),
                    chunk_position="decode",
                )
            )
            if estimate.source == "unavailable":
                raise RuntimeError("GPU decode service estimate is unavailable")
            base = (
                estimate.p95_ms
                if self.envelope == ServiceEnvelope.SLOW
                else estimate.p50_ms
            )
            if batch > 4:
                target32 = self.GRAPH16_BS32_MS * (
                    estimate.p95_ms / max(estimate.p50_ms, 1e-9)
                    if self.envelope == ServiceEnvelope.SLOW
                    else 1.0
                )
                base += (target32 - base) * (batch - 4) / 28
        if self.envelope == ServiceEnvelope.GRAPH32_SENSITIVITY and batch > 16:
            graph_fraction = min(1.0, (batch - 16) / 15)
            graph_target = self.GRAPH32_BS32_MS
            base = base * (1.0 - graph_fraction) + graph_target * graph_fraction
        elif self.envelope == ServiceEnvelope.SLOW and batch > 16:
            base = max(base, self.GRAPH16_BS32_MS * self._runtime_tail_ratio("decode", batch))
        self.cache[key] = max(0.01, base)
        return self.cache[key]



class CPUCounterfactualOracleEstimator:
    """CPU discrete-event estimate of the implementable Oracle v2 action space.

    This is deliberately separate from the legacy rolling oracle. Agent release
    follows FrozenAgentDemand boundaries; no observed request finish or queue
    delay is consumed.
    """

    def __init__(
        self,
        truth: FrozenAgentDemand,
        sidecar: FrozenPhysicalSidecar,
        *,
        service_model_path: Path,
        transfer_summary_path: Path,
        workflow_release_ms: Mapping[str, float],
        config: CPUOracleConfig | None = None,
        cross_run_prefix_identity: str = "exact_within_one_trace",
        runtime_service_profile: Mapping[
            str, Mapping[int, Mapping[str, float]]
        ] | None = None,
        runtime_service_statistic: str | None = None,
    ) -> None:
        if sidecar.truth_id != truth.truth_id or sidecar.truth_digest != truth.truth_digest:
            raise ValueError("physical sidecar is not bound to the supplied truth")
        self.truth = truth
        self.sidecar = sidecar
        self.config = config or CPUOracleConfig()
        workloads = {item.key.workload_instance for item in truth.invocations}
        if set(workflow_release_ms) != workloads:
            raise ValueError("workflow release schedule must cover truth exactly")
        self.workflow_release_ms = dict(workflow_release_ms)
        self.service_model_path = service_model_path
        self.transfer_summary_path = transfer_summary_path
        self.cross_run_prefix_identity = cross_run_prefix_identity
        self.runtime_service_profile = runtime_service_profile or {}
        self.runtime_service_statistic = runtime_service_statistic

    def run(
        self,
        arm: CPUOracleArm,
        envelope: ServiceEnvelope,
        overhead: PlannerOverhead,
        execution_policy: WholeRunExecutionPolicy = WholeRunExecutionPolicy.C0_OBSERVED,
    ) -> CPUOracleEstimateResult:
        if not arm.agent_future and execution_policy != WholeRunExecutionPolicy.C0_OBSERVED:
            raise ValueError("non-agent Oracle arms require the C0 execution policy")
        simulation = _Simulation(
            truth=self.truth,
            sidecar=self.sidecar,
            service_model=GPUServiceCurveModel.load(self.service_model_path),
            transfer_rates=self._transfer_rates(self.transfer_summary_path),
            workflow_release_ms=self.workflow_release_ms,
            config=self.config,
            arm=arm,
            envelope=envelope,
            overhead=overhead,
            execution_policy=execution_policy,
            cross_run_prefix_identity=self.cross_run_prefix_identity,
            runtime_service_profile=self.runtime_service_profile,
            runtime_service_statistic=self.runtime_service_statistic,
        )
        return simulation.run()

    @staticmethod
    def _transfer_rates(path: Path) -> dict[str, dict[str, float]]:
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
        rates: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for bucket in raw.get("buckets", ()):
            direction = str(bucket.get("direction"))
            duration50 = float(bucket.get("duration_ms_p50", 0.0))
            duration95 = float(bucket.get("duration_ms_p95", 0.0))
            size = float(bucket.get("actual_bytes", 0.0))
            if direction in {"d2h", "h2d"} and min(duration50, duration95, size) > 0:
                rates[direction]["p50"].append(size / duration50)
                rates[direction]["p95"].append(size / duration95)
        result = {}
        for direction in ("d2h", "h2d"):
            if not rates[direction]["p50"] or not rates[direction]["p95"]:
                raise ValueError(f"transfer summary lacks {direction} support")
            result[direction] = {
                "p50": sorted(rates[direction]["p50"])[len(rates[direction]["p50"]) // 2],
                "p95": min(rates[direction]["p95"]),
            }
        return result


class _Simulation:
    def __init__(
        self,
        *,
        truth: FrozenAgentDemand,
        sidecar: FrozenPhysicalSidecar,
        service_model: GPUServiceCurveModel,
        transfer_rates: Mapping[str, Mapping[str, float]],
        workflow_release_ms: Mapping[str, float],
        config: CPUOracleConfig,
        arm: CPUOracleArm,
        envelope: ServiceEnvelope,
        overhead: PlannerOverhead,
        execution_policy: WholeRunExecutionPolicy = WholeRunExecutionPolicy.C0_OBSERVED,
        cross_run_prefix_identity: str = "exact_within_one_trace",
        runtime_service_profile: Mapping[
            str, Mapping[int, Mapping[str, float]]
        ],
        runtime_service_statistic: str | None,
    ) -> None:
        self.truth = truth
        self.sidecar = sidecar
        self.config = config
        self.arm = arm
        self.envelope = envelope
        self.overhead = overhead
        self.execution_policy = execution_policy
        self.cross_run_prefix_identity = cross_run_prefix_identity
        self.service = _ServiceEstimator(
            service_model,
            envelope,
            runtime_profile=runtime_service_profile,
            runtime_statistic=runtime_service_statistic,
        )
        quantile = "p95" if envelope == ServiceEnvelope.SLOW else "p50"
        self.transfer_rates = {
            direction: float(values[quantile])
            for direction, values in transfer_rates.items()
        }
        self.invocations = {
            item.key: _InvocationState(item) for item in truth.invocations
        }
        self.physical = {
            (item.invocation, item.call_ordinal): item for item in sidecar.calls
        }
        self.joins = {item.key: item for item in truth.joins}
        self.joins_by_member: dict[LogicalInvocationKey, list[FrozenJoinDemand]] = defaultdict(list)
        for join in truth.joins:
            for member in join.members:
                self.joins_by_member[member].append(join)
        self.releases = dict(workflow_release_ms)
        self.radix = _ContextRadixResidency()
        self.host_paths: dict[LogicalInvocationKey, tuple[int, ...]] = {}
        self.host_used_bytes = 0
        self.active: dict[tuple[LogicalInvocationKey, int], _CallState] = {}
        self.ready: dict[tuple[LogicalInvocationKey, int], _CallState] = {}
        self.transfers: dict[LogicalInvocationKey, _TransferState] = {}
        self.event_heap: list[tuple[float, int, str, object]] = []
        self.event_sequence = 0
        self.ready_sequence = 0
        self.now_ms = 0.0
        self.pcie_available_ms = 0.0
        self.active_output_tokens = 0
        self.hbm_reserved_tokens = 0
        self.hbm_peak_tokens = 0
        self.pressure_duration_ms = 0.0
        self.last_accounted_ms = 0.0
        self.decode_batches: Counter[int] = Counter()
        self.prefill_batches: Counter[int] = Counter()
        self.action_unlocks: list[float] = []
        self.workflow_started: dict[str, float] = {}
        self.workflow_finished: dict[str, float] = {}
        self.context_last_service: dict[LogicalInvocationKey, float] = defaultdict(float)
        self.context_last_service_history: dict[
            LogicalInvocationKey, float
        ] = defaultdict(float)
        self.d2h_bytes = 0
        self.h2d_bytes = 0
        self.recompute_tokens = 0
        self.transfer_count = 0
        self.kv_opportunity_count = 0
        self.planner_overhead_ms = 0.0
        self.prefer_prefill = False
        self.execution_package_counts: Counter[str] = Counter()
        self.current_joint_opportunities: dict[
            tuple[LogicalInvocationKey, tuple[LogicalInvocationKey, int]],
            _JointOpportunity,
        ] = {}
        self.previous_opportunity_pairs: dict[str, set[tuple[LogicalInvocationKey, tuple[LogicalInvocationKey, int]]]] = {
            "eviction": set(),
            "stall_free_round_trip": set(),
            "net_positive": set(),
        }
        self.seen_opportunity_pairs: dict[str, set[tuple[LogicalInvocationKey, tuple[LogicalInvocationKey, int]]]] = {
            name: set() for name in self.previous_opportunity_pairs
        }
        self.opportunity_window_counts: Counter[str] = Counter()
        self.opportunity_unique_victim_byte_ms: dict[str, float] = defaultdict(float)
        self.opportunity_blocked_beneficiary_work_ms: dict[str, float] = defaultdict(float)
        self.opportunity_victim_first_seen: dict[LogicalInvocationKey, float] = {}
        self.max_oracle_reclaimable_bytes = 0
        self.proactive_shadow_count = 0
        self.proactive_shadow_bytes = 0
        self.shadow_commit_count = 0
        self.latest_prefetch_count = 0
        self.prefetch_due_ms: dict[LogicalInvocationKey, float] = {}
        self.victim_history: list[LogicalInvocationKey] = []
        self.control_dirty = True
        self.control_tokens = 0
        for workload, release in self.releases.items():
            self._schedule(release, "workflow_release", workload)

    def run(self) -> CPUOracleEstimateResult:
        while len(self.workflow_finished) < len(self.releases):
            self._drain_events()
            self._run_control_epoch()
            self._admit_ready()
            self._run_control_epoch()
            if self._run_gpu_step():
                continue
            if not self.event_heap:
                blocked = sorted(
                    str(key) for key, item in self.invocations.items() if not item.completed
                )
                raise RuntimeError("CPU oracle deadlock: " + ",".join(blocked[:8]))
            self._advance_time(self.event_heap[0][0])
        makespan = max(self.workflow_finished.values()) - min(self.workflow_started.values())
        decode_total = sum(self.decode_batches.values())
        decode_mean = (
            sum(batch * count for batch, count in self.decode_batches.items()) / decode_total
            if decode_total
            else 0.0
        )
        jct = {
            workload: self.workflow_finished[workload] - self.workflow_started[workload]
            for workload in self.workflow_finished
        }
        opportunity_reentry_count = sum(
            self.context_last_service_history.get(owner, -math.inf) > first_seen
            for owner, first_seen in self.opportunity_victim_first_seen.items()
        )
        return CPUOracleEstimateResult(
            arm=self.arm,
            service_envelope=self.envelope,
            planner_overhead=self.overhead,
            execution_policy=self.execution_policy,
            workflow_count=len(self.releases),
            makespan_ms=makespan,
            workflows_per_hour=len(self.releases) * 3_600_000 / makespan,
            workflow_jct_ms=jct,
            decode_batch_counts=dict(self.decode_batches),
            decode_batch_mean=decode_mean,
            prefill_batch_counts=dict(self.prefill_batches),
            action_unlock_mean_ms=(
                sum(self.action_unlocks) / len(self.action_unlocks)
                if self.action_unlocks
                else 0.0
            ),
            hbm_peak_tokens=self.hbm_peak_tokens,
            hbm_peak_pressure=self.hbm_peak_tokens / self.config.hbm_capacity_tokens,
            hbm_pressure_duration_ms=self.pressure_duration_ms,
            d2h_bytes=self.d2h_bytes,
            h2d_bytes=self.h2d_bytes,
            recompute_tokens=self.recompute_tokens,
            planner_overhead_ms=self.planner_overhead_ms,
            transfer_count=self.transfer_count,
            kv_opportunity_count=self.kv_opportunity_count,
            opportunity_pair_count=len(self.seen_opportunity_pairs["eviction"]),
            eviction_opportunity_window_count=self.opportunity_window_counts["eviction"],
            eviction_unique_victim_byte_ms=self.opportunity_unique_victim_byte_ms["eviction"],
            eviction_blocked_beneficiary_work_ms=self.opportunity_blocked_beneficiary_work_ms["eviction"],
            stall_free_round_trip_window_count=self.opportunity_window_counts["stall_free_round_trip"],
            stall_free_unique_victim_byte_ms=self.opportunity_unique_victim_byte_ms["stall_free_round_trip"],
            stall_free_blocked_beneficiary_work_ms=self.opportunity_blocked_beneficiary_work_ms["stall_free_round_trip"],
            net_positive_opportunity_window_count=self.opportunity_window_counts["net_positive"],
            net_positive_unique_victim_byte_ms=self.opportunity_unique_victim_byte_ms["net_positive"],
            net_positive_blocked_beneficiary_work_ms=self.opportunity_blocked_beneficiary_work_ms["net_positive"],
            joint_opportunity_window_count=self.opportunity_window_counts["stall_free_round_trip"],
            joint_opportunity_byte_ms=self.opportunity_unique_victim_byte_ms["stall_free_round_trip"],
            hbm_blocked_ready_work_ms=self.opportunity_blocked_beneficiary_work_ms["stall_free_round_trip"],
            max_oracle_reclaimable_bytes=self.max_oracle_reclaimable_bytes,
            opportunity_victim_count=len(self.opportunity_victim_first_seen),
            opportunity_victim_reentry_count=opportunity_reentry_count,
            opportunity_victim_reentry_ratio=(
                opportunity_reentry_count / len(self.opportunity_victim_first_seen)
                if self.opportunity_victim_first_seen
                else 0.0
            ),
            proactive_shadow_count=self.proactive_shadow_count,
            proactive_shadow_bytes=self.proactive_shadow_bytes,
            shadow_commit_count=self.shadow_commit_count,
            latest_prefetch_count=self.latest_prefetch_count,
            execution_package_counts=dict(self.execution_package_counts),
            cross_run_prefix_identity=self.cross_run_prefix_identity,
        )

    def _schedule(self, timestamp: float, kind: str, payload: object) -> None:
        self.event_sequence += 1
        heapq.heappush(
            self.event_heap, (timestamp, self.event_sequence, kind, payload)
        )

    def _advance_time(self, timestamp: float) -> None:
        if timestamp < self.now_ms - 1e-9:
            raise RuntimeError("simulation time moved backwards")
        pressure = self._hbm_tokens() / self.config.hbm_capacity_tokens
        delta_ms = timestamp - self.now_ms
        if delta_ms > 0 and self.current_joint_opportunities:
            categories = {
                "eviction": tuple(self.current_joint_opportunities.values()),
                "stall_free_round_trip": tuple(
                    item
                    for item in self.current_joint_opportunities.values()
                    if item.stall_free_round_trip
                ),
                "net_positive": tuple(
                    item
                    for item in self.current_joint_opportunities.values()
                    if item.net_positive
                ),
            }
            for category, opportunities in categories.items():
                victims: dict[LogicalInvocationKey, int] = {}
                beneficiaries = set()
                for opportunity in opportunities:
                    victims[opportunity.victim] = max(
                        victims.get(opportunity.victim, 0),
                        opportunity.reclaimable_bytes,
                    )
                    beneficiaries.add(opportunity.beneficiary)
                self.opportunity_unique_victim_byte_ms[category] += (
                    sum(victims.values()) * delta_ms
                )
                self.opportunity_blocked_beneficiary_work_ms[category] += (
                    len(beneficiaries) * delta_ms
                )
        if pressure >= self.config.pressure_threshold:
            self.pressure_duration_ms += timestamp - self.now_ms
        self.now_ms = timestamp
        self.last_accounted_ms = timestamp
        self.hbm_peak_tokens = max(self.hbm_peak_tokens, self._hbm_tokens())

    def _drain_events(self) -> None:
        changed = False
        while self.event_heap and self.event_heap[0][0] <= self.now_ms + 1e-9:
            _, _, kind, payload = heapq.heappop(self.event_heap)
            changed = True
            if kind == "workflow_release":
                self._release_workflow(str(payload))
            elif kind == "tool_complete":
                self._tool_complete(payload)  # type: ignore[arg-type]
            elif kind == "transfer_complete":
                self._transfer_complete(payload)  # type: ignore[arg-type]
            elif kind == "prefetch_due":
                owner, due_ms = payload  # type: ignore[misc]
                if self.prefetch_due_ms.get(owner) == due_ms:
                    self.prefetch_due_ms.pop(owner, None)
                    if (
                        owner in self.host_paths
                        and not self.radix.contains(owner)
                        and owner not in self.transfers
                    ):
                        self._start_restore(owner, latest_start=True)
            else:
                raise RuntimeError(f"unknown simulation event: {kind}")
        if changed:
            self.control_dirty = True

    def _run_control_epoch(self) -> None:
        if (
            not self.control_dirty
            and self.control_tokens < self.config.control_quantum_tokens
        ):
            return
        self._plan_predictive_kv_actions()
        self._refresh_joint_opportunities()
        self.control_dirty = False
        self.control_tokens = 0

    def _release_workflow(self, workload: str) -> None:
        roots = [
            item for item in self.invocations.values()
            if item.demand.key.workload_instance == workload and item.demand.parent is None
        ]
        if len(roots) != 1:
            raise RuntimeError("workflow root closure is invalid")
        self.workflow_started[workload] = self.now_ms
        roots[0].created = True
        self._advance_invocation(roots[0])

    def _advance_invocation(self, state: _InvocationState) -> None:
        while state.created and not state.completed:
            if state.pending_tools:
                return
            if state.next_call_index >= len(state.demand.calls):
                raise RuntimeError("invocation exhausted calls without terminal boundary")
            call = state.demand.calls[state.next_call_index]
            if call.prompt_tokens == 0 and call.output_tokens == 0:
                if call.boundary.kind == FrozenActionBoundaryKind.JOIN:
                    if not all(
                        self._join_satisfied(self.joins[key])
                        for key in call.boundary.joins
                    ):
                        return
                    state.next_call_index += 1
                    continue
                if call.boundary.kind in {
                    FrozenActionBoundaryKind.RETURN,
                    FrozenActionBoundaryKind.FINAL,
                }:
                    state.next_call_index += 1
                    self._complete_invocation(state)
                    return
                raise RuntimeError(
                    "unsupported synthetic zero-demand action boundary"
                )
            physical = self.physical.get((state.demand.key, call.call_ordinal))
            if physical is None:
                raise RuntimeError("actual LLM call lacks physical sidecar record")
            call_id = (state.demand.key, call.call_ordinal)
            if call_id not in self.ready and call_id not in self.active:
                self.ready_sequence += 1
                self.ready[call_id] = _CallState(
                    invocation=state.demand.key,
                    demand=call,
                    physical=physical,
                    ready_since_ms=self.now_ms,
                    ready_sequence=self.ready_sequence,
                )
            return

    def _admit_ready(self) -> None:
        slots = self.config.max_running_requests - len(self.active)
        if slots <= 0 or not self.ready:
            return
        candidates = self._ordered_execution_candidates(
            self.ready.values(), limit=slots
        )
        changed = False
        for call in candidates:
            if len(self.active) >= self.config.max_running_requests:
                break
            owner = self.invocations[call.invocation].demand.semantic_owner
            if owner in self.transfers:
                continue
            if owner in self.host_paths and not self.radix.contains(owner):
                changed = self._start_restore(owner) or changed
                continue
            hit = self.radix.prefix_hit_for_owner(
                owner, call.physical.prompt_token_symbols
            )
            marginal = len(call.physical.prompt_token_symbols) - hit
            if not self._fund_hbm(marginal, beneficiary=call.invocation):
                continue
            self.radix.replace(owner, call.physical.prompt_token_symbols)
            call.cache_hit_tokens = hit
            call.prefill_remaining = marginal
            call.decode_remaining = call.demand.output_tokens
            call.admitted_ms = self.now_ms
            self.active[(call.invocation, call.demand.call_ordinal)] = call
            self.ready.pop((call.invocation, call.demand.call_ordinal), None)
            changed = True
            self._update_peak()
        if changed:
            self.control_dirty = True

    def _run_gpu_step(self) -> bool:
        prefill = [item for item in self.active.values() if item.prefill_remaining > 0]
        decode = [
            item
            for item in self.active.values()
            if item.prefill_remaining == 0 and item.decode_remaining > 0
        ]
        run_prefill = bool(prefill) and (not decode or self.prefer_prefill)
        if decode and not run_prefill:
            batch = sorted(decode, key=self._execution_priority)[
                : self.config.max_running_requests
            ]
            if not self._fund_hbm(len(batch), beneficiary=None):
                return False
            sequences = tuple(
                len(item.physical.prompt_token_symbols) + item.decoded_tokens
                for item in batch
            )
            elapsed = self.service.decode_ms(sequences)
            self.decode_batches[len(batch)] += 1
            self.active_output_tokens += len(batch)
            self.control_tokens += len(batch)
            for call in batch:
                call.decode_remaining -= 1
                call.decoded_tokens += 1
                owner = self.invocations[call.invocation].demand.semantic_owner
                self.context_last_service[owner] = self.now_ms
                self.context_last_service_history[owner] = self.now_ms
            self._advance_time(self.now_ms + elapsed)
            self._drain_events()
            for call in tuple(batch):
                if call.decode_remaining == 0:
                    self._complete_call(call)
            self.prefer_prefill = bool(prefill)
            return True
        if run_prefill:
            # The execution package is selected when calls enter the active set.
            # Re-solving it for every prefill chunk changes no admission decision.
            call = min(prefill, key=self._execution_priority)
            tokens = min(call.prefill_remaining, self.config.prefill_chunk_tokens)
            elapsed = self.service.prefill_ms(
                sequence_tokens=call.cache_hit_tokens + call.prefilled_tokens,
                token_delta=tokens,
                first_chunk=call.prefilled_tokens == 0,
            )
            self.prefill_batches[1] += 1
            call.prefill_remaining -= tokens
            call.prefilled_tokens += tokens
            owner = self.invocations[call.invocation].demand.semantic_owner
            self.context_last_service[owner] = self.now_ms
            self.context_last_service_history[owner] = self.now_ms
            self._advance_time(self.now_ms + elapsed)
            self._drain_events()
            if call.prefill_remaining == 0 and call.decode_remaining == 0:
                self._complete_call(call)
            self.prefer_prefill = False
            return True
        return False

    def _complete_call(self, call: _CallState) -> None:
        call_id = (call.invocation, call.demand.call_ordinal)
        if call_id not in self.active:
            return
        state = self.invocations[call.invocation]
        owner = state.demand.semantic_owner
        if self.host_paths.get(owner) != call.physical.cache_commit_token_symbols:
            self._invalidate_host_copy(owner)
        self.radix.replace(owner, call.physical.cache_commit_token_symbols)
        self.active_output_tokens = max(0, self.active_output_tokens - call.decoded_tokens)
        self.active.pop(call_id)
        self.control_dirty = True
        self.action_unlocks.append(self.now_ms - call.ready_since_ms)
        state.next_call_index += 1
        self._charge_fastpath(physical=False)
        boundary = call.demand.boundary
        if boundary.kind == FrozenActionBoundaryKind.TOOL:
            tools = [
                item for item in state.demand.tools
                if item.starts_after_call_ordinal == call.demand.call_ordinal
            ]
            state.pending_tools = len(tools)
            state.tool_completion_ms = (
                self.now_ms + max(tool.service_duration_ms for tool in tools)
                if tools
                else None
            )
            for tool in tools:
                self._schedule(
                    self.now_ms + tool.service_duration_ms,
                    "tool_complete",
                    state.demand.key,
                )
        elif boundary.kind == FrozenActionBoundaryKind.SPAWN:
            for target in boundary.target_invocations:
                child = self.invocations[target]
                child.created = True
                self._advance_invocation(child)
            self._advance_invocation(state)
        elif boundary.kind in {
            FrozenActionBoundaryKind.CONTINUE,
            FrozenActionBoundaryKind.CALL,
            FrozenActionBoundaryKind.MESSAGE,
            FrozenActionBoundaryKind.HANDOFF,
        }:
            self._advance_invocation(state)
        elif boundary.kind == FrozenActionBoundaryKind.JOIN:
            self._advance_invocation(state)
        elif boundary.kind in {
            FrozenActionBoundaryKind.RETURN,
            FrozenActionBoundaryKind.FINAL,
        }:
            self._complete_invocation(state)
        else:
            raise RuntimeError(f"unsupported action boundary: {boundary.kind.value}")
        self._update_peak()

    def _tool_complete(self, key: LogicalInvocationKey) -> None:
        state = self.invocations[key]
        state.pending_tools -= 1
        if state.pending_tools < 0:
            raise RuntimeError("tool completion underflow")
        if state.pending_tools == 0:
            state.tool_completion_ms = None
            self._advance_invocation(state)

    def _complete_invocation(self, state: _InvocationState) -> None:
        state.completed = True
        self.control_dirty = True
        owner = state.demand.semantic_owner
        if state.demand.parent is not None and owner == state.demand.key:
            self._drop_context(owner)
        for join in self.joins_by_member.get(state.demand.key, ()):
            if self._join_satisfied(join):
                for waiter in join.waiters:
                    self._advance_invocation(self.invocations[waiter])
        if state.demand.parent is None:
            workflow = state.demand.key.workload_instance
            self.workflow_finished[workflow] = self.now_ms
            self._drop_context(owner)

    def _join_satisfied(self, join: FrozenJoinDemand) -> bool:
        values = [self.invocations[item].completed for item in join.members]
        return all(values) if join.mode == FrozenJoinMode.ALL else any(values)

    def _execution_priority(self, call: _CallState) -> tuple[float, ...]:
        waited = self.now_ms - call.ready_since_ms
        if waited >= self.config.starvation_floor_ms:
            return (-1.0, call.ready_since_ms, call.ready_sequence)
        return (0.0, call.ready_sequence)

    def _ordered_execution_candidates(
        self, calls: Iterable[_CallState], *, limit: int
    ) -> list[_CallState]:
        values = list(calls)
        observed = sorted(values, key=self._execution_priority)
        package_kind = self.execution_policy.package_kind
        if not self.arm.agent_future or package_kind is None or len(observed) <= 1:
            return observed

        width = min(len(observed), max(1, limit), self.config.execution_package_width)
        overdue = [
            call
            for call in observed
            if self.now_ms - call.ready_since_ms >= self.config.starvation_floor_ms
        ]
        overdue_ids = {
            (call.invocation, call.demand.call_ordinal) for call in overdue
        }
        movable = [
            call
            for call in observed
            if (call.invocation, call.demand.call_ordinal) not in overdue_ids
        ]

        def with_overdue(ordered: Iterable[_CallState]) -> tuple[_CallState, ...]:
            merged = overdue + list(ordered)
            return tuple(merged[:width])

        packages = {
            ExecutionPackageKind.OBSERVED: with_overdue(movable),
            ExecutionPackageKind.MIN_REMAINING_DEMAND: with_overdue(
                sorted(movable, key=self._call_remaining_ms)
            ),
            ExecutionPackageKind.ACTION_UNLOCK: with_overdue(
                sorted(movable, key=self._action_unlock_key)
            ),
            ExecutionPackageKind.MAX_BATCH_FILL: with_overdue(
                sorted(movable, key=self._batch_fill_key)
            ),
        }
        selected = packages[package_kind]
        self.execution_package_counts[package_kind.value] += 1
        selected_ids = {
            (call.invocation, call.demand.call_ordinal) for call in selected
        }
        return list(selected) + [
            call
            for call in observed
            if (call.invocation, call.demand.call_ordinal) not in selected_ids
        ]

    def _execution_package_objective(
        self,
        package: tuple[_CallState, ...],
        remaining_by_workflow: Mapping[str, float],
    ) -> float:
        progress: dict[str, float] = defaultdict(float)
        for call in package:
            progress[call.invocation.workload_instance] += self._call_remaining_ms(call)
        residual = max(
            (
                max(0.0, remaining - progress.get(workflow, 0.0))
                for workflow, remaining in remaining_by_workflow.items()
            ),
            default=0.0,
        )
        return self._package_service_ms(package) + residual

    def _remaining_workflow_estimates(self) -> dict[str, float]:
        return {
            workload: self._estimate_invocation_remaining_ms(root.demand.key)
            for workload in self.releases
            for root in self.invocations.values()
            if (
                root.demand.key.workload_instance == workload
                and root.demand.parent is None
                and not root.completed
            )
        }

    def _package_service_ms(self, package: tuple[_CallState, ...]) -> float:
        if not package:
            return math.inf
        prefill_ms = 0.0
        decode_rounds = 0
        sequences = []
        restore_ms = 0.0
        for call in package:
            owner = self.invocations[call.invocation].demand.semantic_owner
            hit = self.radix.prefix_hit_for_owner(
                owner, call.physical.prompt_token_symbols
            )
            prompt_delta = len(call.physical.prompt_token_symbols) - hit
            if prompt_delta:
                chunks = math.ceil(prompt_delta / self.config.prefill_chunk_tokens)
                prefill_ms += chunks * self.service.prefill_ms(
                    sequence_tokens=hit,
                    token_delta=min(prompt_delta, self.config.prefill_chunk_tokens),
                    first_chunk=True,
                )
            decode_rounds = max(decode_rounds, call.demand.output_tokens)
            sequences.append(len(call.physical.prompt_token_symbols))
            if owner in self.host_paths and not self.radix.contains(owner):
                restore_ms = max(
                    restore_ms,
                    len(self.host_paths[owner])
                    * self.sidecar.kv_bytes_per_token
                    / self.transfer_rates["h2d"],
                )
        decode_ms = (
            decode_rounds * self.service.decode_ms(tuple(sequences))
            if decode_rounds and sequences
            else 0.0
        )
        return prefill_ms + decode_ms + restore_ms

    def _call_remaining_ms(self, call: _CallState) -> float:
        prompt_remaining = (
            call.prefill_remaining
            if call.admitted_ms is not None
            else call.demand.incremental_prompt_tokens
        )
        prompt_ms = 0.0
        if prompt_remaining:
            chunks = math.ceil(prompt_remaining / self.config.prefill_chunk_tokens)
            prompt_ms = chunks * self.service.prefill_ms(
                sequence_tokens=max(0, len(call.physical.prompt_token_symbols) - prompt_remaining),
                token_delta=min(prompt_remaining, self.config.prefill_chunk_tokens),
                first_chunk=call.prefilled_tokens == 0,
            )
        decode_remaining = (
            call.decode_remaining
            if call.admitted_ms is not None
            else call.demand.output_tokens
        )
        sequence = len(call.physical.prompt_token_symbols) + call.decoded_tokens
        return prompt_ms + decode_remaining * self.service.decode_ms((sequence,))

    def _frozen_call_remaining_ms(
        self,
        demand: FrozenLLMCallDemand,
        physical: FrozenPhysicalCall,
    ) -> float:
        prompt_remaining = demand.incremental_prompt_tokens
        prompt_ms = 0.0
        if prompt_remaining:
            chunks = math.ceil(prompt_remaining / self.config.prefill_chunk_tokens)
            prompt_ms = chunks * self.service.prefill_ms(
                sequence_tokens=max(0, demand.prompt_tokens - prompt_remaining),
                token_delta=min(prompt_remaining, self.config.prefill_chunk_tokens),
                first_chunk=True,
            )
        sequence = len(physical.prompt_token_symbols)
        return prompt_ms + demand.output_tokens * self.service.decode_ms((sequence,))

    def _action_unlock_key(self, call: _CallState) -> tuple[float, ...]:
        rank = {
            FrozenActionBoundaryKind.FINAL: 0,
            FrozenActionBoundaryKind.RETURN: 0,
            FrozenActionBoundaryKind.JOIN: 1,
            FrozenActionBoundaryKind.TOOL: 2,
            FrozenActionBoundaryKind.SPAWN: 2,
        }.get(call.demand.boundary.kind, 3)
        return (float(rank), self._call_remaining_ms(call), call.ready_sequence)

    def _batch_fill_key(self, call: _CallState) -> tuple[float, ...]:
        owner = self.invocations[call.invocation].demand.semantic_owner
        resident = 0.0 if self.radix.contains(owner) else 1.0
        marginal = len(call.physical.prompt_token_symbols) - (
            self.radix.prefix_hit_for_owner(
                owner, call.physical.prompt_token_symbols
            )
        )
        return (resident, float(marginal), float(call.demand.output_tokens), call.ready_sequence)

    def _estimate_invocation_remaining_ms(
        self,
        key: LogicalInvocationKey,
        stack: set[LogicalInvocationKey] | None = None,
    ) -> float:
        state = self.invocations[key]
        if state.completed:
            return 0.0
        active_stack = set() if stack is None else set(stack)
        if key in active_stack:
            return 0.0
        active_stack.add(key)
        total = 0.0
        if state.pending_tools and state.tool_completion_ms is not None:
            total += max(0.0, state.tool_completion_ms - self.now_ms)

        for index, demand in enumerate(
            state.demand.calls[state.next_call_index :],
            start=state.next_call_index,
        ):
            call = self.active.get((key, demand.call_ordinal)) or self.ready.get(
                (key, demand.call_ordinal)
            )
            if call is not None:
                total += self._call_remaining_ms(call)
            elif demand.prompt_tokens or demand.output_tokens:
                physical = self.physical.get((key, demand.call_ordinal))
                if physical is not None:
                    total += self._frozen_call_remaining_ms(demand, physical)

            tools = [
                tool.service_duration_ms
                for tool in state.demand.tools
                if tool.starts_after_call_ordinal == demand.call_ordinal
            ]
            if tools and not (
                index == state.next_call_index and state.pending_tools
            ):
                total += max(tools)

            if demand.boundary.kind == FrozenActionBoundaryKind.JOIN:
                waits = [
                    self._join_remaining_ms(self.joins[join_key], active_stack)
                    for join_key in demand.boundary.joins
                    if not self._join_satisfied(self.joins[join_key])
                ]
                if waits:
                    total += max(waits)
            elif demand.boundary.kind == FrozenActionBoundaryKind.CALL:
                child_times = [
                    self._estimate_invocation_remaining_ms(target, active_stack)
                    for target in demand.boundary.target_invocations
                ]
                if child_times:
                    total += max(child_times)
        return total

    def _join_remaining_ms(
        self, join: FrozenJoinDemand, stack: set[LogicalInvocationKey]
    ) -> float:
        values = [
            self._estimate_invocation_remaining_ms(member, stack)
            for member in join.members
            if not self.invocations[member].completed
        ]
        if not values:
            return 0.0
        return max(values) if join.mode == FrozenJoinMode.ALL else min(values)

    def _time_until_creation_ms(
        self,
        key: LogicalInvocationKey,
        stack: set[LogicalInvocationKey] | None = None,
    ) -> float | None:
        state = self.invocations[key]
        if state.created:
            return 0.0
        parent_key = state.demand.parent
        if parent_key is None:
            release = self.releases.get(key.workload_instance)
            return None if release is None else max(0.0, release - self.now_ms)
        active_stack = set() if stack is None else set(stack)
        if key in active_stack:
            return None
        active_stack.add(key)
        parent = self.invocations[parent_key]
        prefix = self._time_until_creation_ms(parent_key, active_stack)
        if prefix is None:
            return None
        elapsed = prefix
        for demand in parent.demand.calls[parent.next_call_index :]:
            physical = self.physical.get((parent_key, demand.call_ordinal))
            if physical is not None:
                call = self.active.get((parent_key, demand.call_ordinal)) or self.ready.get(
                    (parent_key, demand.call_ordinal)
                )
                elapsed += (
                    self._call_remaining_ms(call)
                    if call is not None
                    else self._frozen_call_remaining_ms(demand, physical)
                )
            if key in demand.boundary.target_invocations:
                return elapsed
            tools = [
                tool.service_duration_ms
                for tool in parent.demand.tools
                if tool.starts_after_call_ordinal == demand.call_ordinal
            ]
            if tools:
                elapsed += max(tools)
            if demand.boundary.kind == FrozenActionBoundaryKind.JOIN:
                waits = [
                    self._join_remaining_ms(self.joins[item], active_stack)
                    for item in demand.boundary.joins
                ]
                if waits:
                    elapsed += max(waits)
        return None

    def _next_use_time_ms(self, owner: LogicalInvocationKey) -> float | None:
        owner_active = {
            self.invocations[item.invocation].demand.semantic_owner
            for item in (*self.active.values(), *self.ready.values())
        }
        if owner in owner_active:
            return self.now_ms

        candidates = []
        for state in self.invocations.values():
            if state.demand.semantic_owner != owner or state.completed:
                continue
            if not state.created:
                creation = self._time_until_creation_ms(state.demand.key)
                if creation is not None:
                    candidates.append(self.now_ms + creation)
                continue
            if state.pending_tools:
                if state.tool_completion_ms is not None:
                    candidates.append(state.tool_completion_ms)
                continue
            if state.next_call_index >= len(state.demand.calls):
                continue
            demand = state.demand.calls[state.next_call_index]
            if demand.prompt_tokens or demand.output_tokens:
                candidates.append(self.now_ms)
                continue
            if demand.boundary.kind == FrozenActionBoundaryKind.JOIN:
                waits = [
                    self._join_remaining_ms(self.joins[item], set())
                    for item in demand.boundary.joins
                    if not self._join_satisfied(self.joins[item])
                ]
                candidates.append(self.now_ms + (max(waits) if waits else 0.0))
        return min(candidates) if candidates else None

    def _belady_key(self, owner: LogicalInvocationKey) -> tuple[float, int]:
        next_use = self._next_use_time_ms(owner)
        causal_distance = math.inf if next_use is None else max(0.0, next_use - self.now_ms)
        return causal_distance, self.radix.reclaimable_tokens(owner)

    def _blocked_ready_calls(
        self,
    ) -> list[tuple[tuple[LogicalInvocationKey, int], _CallState, int]]:
        available = max(0, self.config.hbm_capacity_tokens - self._hbm_tokens())
        blocked = []
        for call_id, call in self.ready.items():
            owner = self.invocations[call.invocation].demand.semantic_owner
            path = (
                self.host_paths[owner]
                if owner in self.host_paths and not self.radix.contains(owner)
                else call.physical.prompt_token_symbols
            )
            marginal = len(path) - self.radix.prefix_hit_for_owner(owner, path)
            if marginal > available:
                blocked.append((call_id, call, marginal - available))
        return blocked

    def _refresh_joint_opportunities(self) -> None:
        blocked = self._blocked_ready_calls()
        if not blocked:
            self._install_joint_opportunities({})
            return
        active_or_ready_owners = {
            self.invocations[item.invocation].demand.semantic_owner
            for item in (*self.active.values(), *self.ready.values())
        }
        opportunities = {}
        for owner in self.radix.paths:
            if owner in active_or_ready_owners or owner in self.transfers:
                continue
            next_use = self._next_use_time_ms(owner)
            if next_use is None or next_use <= self.now_ms:
                continue
            path = self.radix.path(owner)
            copy_bytes = len(path) * self.sidecar.kv_bytes_per_token
            reclaimable_bytes = (
                self.radix.reclaimable_tokens(owner)
                * self.sidecar.kv_bytes_per_token
            )
            if reclaimable_bytes <= 0:
                continue
            host_feasible = (
                self.host_paths.get(owner) == path
                or self.host_used_bytes + copy_bytes <= self.config.host_capacity_bytes
            )
            d2h_ms = (
                0.0
                if self.host_paths.get(owner) == path
                else copy_bytes / self.transfer_rates["d2h"]
            )
            h2d_ms = copy_bytes / self.transfer_rates["h2d"]
            slack_ms = next_use - self.now_ms
            if (
                not host_feasible
                or slack_ms <= d2h_ms + self.config.transfer_commit_guard_ms
            ):
                continue
            stall_free = slack_ms > (
                d2h_ms
                + h2d_ms
                + 2 * self.config.transfer_commit_guard_ms
            )
            restore_stall_ms = max(
                0.0,
                d2h_ms
                + h2d_ms
                + 2 * self.config.transfer_commit_guard_ms
                - slack_ms,
            )
            for call_id, call, deficit_tokens in blocked:
                if reclaimable_bytes < deficit_tokens * self.sidecar.kv_bytes_per_token:
                    continue
                beneficiary_gain_ms = min(
                    slack_ms,
                    max(0.0, self._call_remaining_ms(call)),
                )
                key = (owner, call_id)
                opportunities[key] = _JointOpportunity(
                    victim=owner,
                    beneficiary=call_id,
                    reclaimable_bytes=reclaimable_bytes,
                    slack_ms=slack_ms,
                    d2h_ms=d2h_ms,
                    h2d_ms=h2d_ms,
                    beneficiary_gain_ms=beneficiary_gain_ms,
                    restore_stall_ms=restore_stall_ms,
                    stall_free_round_trip=stall_free,
                    net_positive=beneficiary_gain_ms > restore_stall_ms,
                )
        self._install_joint_opportunities(opportunities)

    def _install_joint_opportunities(
        self,
        opportunities: Mapping[
            tuple[LogicalInvocationKey, tuple[LogicalInvocationKey, int]],
            _JointOpportunity,
        ],
    ) -> None:
        category_pairs = {
            "eviction": set(opportunities),
            "stall_free_round_trip": {
                key
                for key, value in opportunities.items()
                if value.stall_free_round_trip
            },
            "net_positive": {
                key for key, value in opportunities.items() if value.net_positive
            },
        }
        for category, pairs in category_pairs.items():
            self.opportunity_window_counts[category] += len(
                pairs - self.previous_opportunity_pairs[category]
            )
            self.seen_opportunity_pairs[category].update(pairs)
            self.previous_opportunity_pairs[category] = pairs
        self.current_joint_opportunities = dict(opportunities)
        if opportunities:
            self.max_oracle_reclaimable_bytes = max(
                self.max_oracle_reclaimable_bytes,
                max(item.reclaimable_bytes for item in opportunities.values()),
            )
            for item in opportunities.values():
                self.opportunity_victim_first_seen.setdefault(
                    item.victim, self.now_ms
                )

    def _future_ready_call(
        self,
        state: _InvocationState,
        busy_invocations: set[LogicalInvocationKey],
    ) -> tuple[float, FrozenLLMCallDemand, FrozenPhysicalCall] | None:
        if state.completed or state.demand.key in busy_invocations:
            return None
        if not state.created:
            delay = self._time_until_creation_ms(state.demand.key)
            if delay is None:
                return None
            timestamp = self.now_ms + delay
        elif state.pending_tools:
            if state.tool_completion_ms is None:
                return None
            timestamp = state.tool_completion_ms
        else:
            timestamp = self.now_ms

        index = state.next_call_index
        while index < len(state.demand.calls):
            demand = state.demand.calls[index]
            physical = self.physical.get((state.demand.key, demand.call_ordinal))
            if demand.prompt_tokens or demand.output_tokens:
                if physical is None:
                    return None
                return timestamp, demand, physical
            if demand.boundary.kind == FrozenActionBoundaryKind.JOIN:
                waits = [
                    self._join_remaining_ms(self.joins[item], set())
                    for item in demand.boundary.joins
                    if not self._join_satisfied(self.joins[item])
                ]
                if waits:
                    timestamp = max(timestamp, self.now_ms + max(waits))
            elif demand.boundary.kind in {
                FrozenActionBoundaryKind.RETURN,
                FrozenActionBoundaryKind.FINAL,
            }:
                return None
            index += 1
        return None

    def _predicted_pressure_time_ms(self) -> float | None:
        projected = self._hbm_tokens()
        growth: list[tuple[float, int]] = []
        for call in self.active.values():
            if call.decode_remaining:
                growth.append(
                    (
                        self.now_ms + self._call_remaining_ms(call),
                        call.decode_remaining,
                    )
                )
        elapsed = 0.0
        forecast_limit = max(
            1, self.config.max_running_requests - len(self.active)
        )
        for call in sorted(
            self.ready.values(), key=self._execution_priority
        )[:forecast_limit]:
            owner = self.invocations[call.invocation].demand.semantic_owner
            path = (
                self.host_paths[owner]
                if owner in self.host_paths and not self.radix.contains(owner)
                else call.physical.prompt_token_symbols
            )
            elapsed += self._call_remaining_ms(call)
            growth.append(
                (
                    self.now_ms + elapsed,
                    len(path) - self.radix.prefix_hit_for_owner(owner, path),
                )
            )
        # Proactive actions additionally include frozen root releases. Summing
        # every not-yet-created child/tool continuation treats mutually
        # exclusive or already-terminal contexts as concurrently resident and
        # creates false pressure; those continuations become visible only after
        # their real causal event advances the execution frontier.
        busy_invocations = {
            call.invocation
            for call in (*self.active.values(), *self.ready.values())
        }
        for state in self.invocations.values():
            if state.created or state.demand.parent is not None:
                continue
            future = self._future_ready_call(state, busy_invocations)
            if future is None:
                continue
            timestamp, _, physical = future
            owner = state.demand.semantic_owner
            path = physical.prompt_token_symbols
            growth.append(
                (
                    timestamp,
                    len(path) - self.radix.prefix_hit_for_owner(owner, path),
                )
            )
        for timestamp, tokens in sorted(growth):
            projected += tokens
            if projected > self.config.hbm_capacity_tokens:
                return max(self.now_ms, timestamp)
        return None

    def _plan_predictive_kv_actions(self) -> None:
        if not self.arm.kv_future:
            return
        self._schedule_latest_prefetches()
        pressure_time = self._predicted_pressure_time_ms()
        if pressure_time is None or self.pcie_available_ms > self.now_ms:
            return
        occupied = {
            self.invocations[item.invocation].demand.semantic_owner
            for item in (*self.active.values(), *self.ready.values())
        }
        candidates = []
        for owner in self.radix.paths:
            if (
                owner in occupied
                or owner in self.transfers
                or self.host_paths.get(owner) == self.radix.path(owner)
            ):
                continue
            next_use = self._next_use_time_ms(owner)
            if next_use is None or next_use <= pressure_time:
                continue
            path = self.radix.path(owner)
            copy_bytes = len(path) * self.sidecar.kv_bytes_per_token
            completion = self.now_ms + copy_bytes / self.transfer_rates["d2h"]
            if (
                self.host_used_bytes + copy_bytes <= self.config.host_capacity_bytes
                and completion + self.config.transfer_commit_guard_ms <= pressure_time
            ):
                candidates.append(
                    (
                        next_use,
                        self.radix.reclaimable_tokens(owner),
                        owner,
                    )
                )
        if candidates:
            _, _, owner = max(candidates)
            self._start_shadow(owner)

    def _start_shadow(self, owner: LogicalInvocationKey) -> bool:
        path = self.radix.path(owner)
        if not path or owner in self.transfers or self.host_paths.get(owner) == path:
            return False
        bytes_to_copy = len(path) * self.sidecar.kv_bytes_per_token
        if self.host_used_bytes + bytes_to_copy > self.config.host_capacity_bytes:
            return False
        duration = bytes_to_copy / self.transfer_rates["d2h"]
        completion = max(self.now_ms, self.pcie_available_ms) + duration
        transfer = _TransferState(
            owner,
            "d2h",
            path,
            bytes_to_copy,
            completion,
            evict_on_complete=False,
        )
        self.pcie_available_ms = completion
        self.transfers[owner] = transfer
        self._schedule(completion, "transfer_complete", transfer)
        self.proactive_shadow_count += 1
        self.proactive_shadow_bytes += bytes_to_copy
        self._charge_fastpath(physical=True)
        return True

    def _schedule_latest_prefetches(self) -> None:
        for owner, path in tuple(self.host_paths.items()):
            if self.radix.contains(owner) or owner in self.transfers:
                continue
            next_use = self._next_use_time_ms(owner)
            if next_use is None:
                self.prefetch_due_ms.pop(owner, None)
                continue
            duration = len(path) * self.sidecar.kv_bytes_per_token / self.transfer_rates["h2d"]
            due = max(
                self.now_ms,
                next_use - duration - self.config.transfer_commit_guard_ms,
            )
            if due <= self.now_ms + 1e-9:
                self._start_restore(owner, latest_start=True)
                continue
            previous = self.prefetch_due_ms.get(owner)
            if previous is not None and abs(previous - due) <= 1e-6:
                continue
            self.prefetch_due_ms[owner] = due
            self._schedule(due, "prefetch_due", (owner, due))

    def _fund_hbm(
        self, required_tokens: int, *, beneficiary: LogicalInvocationKey | None
    ) -> bool:
        while self._hbm_tokens() + required_tokens > self.config.hbm_capacity_tokens:
            victim = self._choose_victim(beneficiary)
            if victim is None:
                return False
            self.kv_opportunity_count += 1
            if not self._evict(victim):
                return False
        return True

    def _choose_victim(
        self, beneficiary: LogicalInvocationKey | None
    ) -> LogicalInvocationKey | None:
        occupied = {
            self.invocations[item.invocation].demand.semantic_owner
            for item in (*self.active.values(), *self.ready.values())
        }
        if beneficiary in self.invocations:
            occupied.add(self.invocations[beneficiary].demand.semantic_owner)
        candidates = [
            owner
            for owner in self.radix.paths
            if owner not in occupied and owner not in self.transfers
        ]
        if not candidates:
            return None
        if self.arm.kv_future:
            return max(candidates, key=self._belady_key)
        return min(candidates, key=lambda item: self.context_last_service[item])

    def _evict(self, owner: LogicalInvocationKey) -> bool:
        path = self.radix.path(owner)
        if not path:
            return False
        self.victim_history.append(owner)
        if self._next_use_time_ms(owner) is None:
            self._drop_context(owner)
            return True

        existing_host = self.host_paths.get(owner)
        if existing_host == path:
            self.radix.remove(owner)
            self.shadow_commit_count += 1
            self._charge_fastpath(physical=True)
            return True
        if existing_host:
            self._invalidate_host_copy(owner)

        bytes_to_copy = len(path) * self.sidecar.kv_bytes_per_token
        if self.host_used_bytes + bytes_to_copy > self.config.host_capacity_bytes:
            self.radix.remove(owner)
            self.recompute_tokens += len(path)
            self._charge_fastpath(physical=True)
            return True
        duration = bytes_to_copy / self.transfer_rates["d2h"]
        completion = max(self.now_ms, self.pcie_available_ms) + duration
        self.pcie_available_ms = completion
        transfer = _TransferState(
            owner,
            "d2h",
            path,
            bytes_to_copy,
            completion,
            evict_on_complete=True,
        )
        self.transfers[owner] = transfer
        self._schedule(completion, "transfer_complete", transfer)
        self._charge_fastpath(physical=True)
        return False

    def _start_restore(
        self, owner: LogicalInvocationKey, *, latest_start: bool = False
    ) -> bool:
        if owner in self.transfers or owner not in self.host_paths:
            return False
        path = self.host_paths[owner]
        marginal = len(path) - self.radix.prefix_hit_for_owner(owner, path)
        if not self._fund_hbm(marginal, beneficiary=owner):
            return False
        self.hbm_reserved_tokens += marginal
        bytes_to_copy = len(path) * self.sidecar.kv_bytes_per_token
        duration = bytes_to_copy / self.transfer_rates["h2d"]
        completion = max(self.now_ms, self.pcie_available_ms) + duration
        self.pcie_available_ms = completion
        transfer = _TransferState(
            owner,
            "h2d",
            path,
            bytes_to_copy,
            completion,
            marginal,
            latest_start_prefetch=latest_start,
        )
        self.transfers[owner] = transfer
        self.prefetch_due_ms.pop(owner, None)
        self._schedule(completion, "transfer_complete", transfer)
        if latest_start:
            self.latest_prefetch_count += 1
        self._charge_fastpath(physical=True)
        return True

    def _transfer_complete(self, transfer: _TransferState) -> None:
        current = self.transfers.get(transfer.owner)
        if current is not transfer:
            raise RuntimeError("transfer ownership is inconsistent")
        if transfer.direction == "d2h":
            if transfer.evict_on_complete:
                self.radix.remove(transfer.owner)
            previous = self.host_paths.get(transfer.owner)
            if previous and previous != transfer.path:
                self.host_used_bytes -= (
                    len(previous) * self.sidecar.kv_bytes_per_token
                )
            if previous != transfer.path:
                self.host_used_bytes += transfer.bytes
            self.host_paths[transfer.owner] = transfer.path
            self.d2h_bytes += transfer.bytes
        elif transfer.direction == "h2d":
            self.hbm_reserved_tokens -= transfer.reserved_tokens
            self.radix.replace(transfer.owner, transfer.path)
            self.h2d_bytes += transfer.bytes
        else:
            raise RuntimeError("unknown transfer direction")
        self.transfer_count += 1
        self.transfers.pop(transfer.owner)
        self._update_peak()

    def _invalidate_host_copy(self, owner: LogicalInvocationKey) -> None:
        path = self.host_paths.pop(owner, ())
        self.host_used_bytes -= len(path) * self.sidecar.kv_bytes_per_token
        self.prefetch_due_ms.pop(owner, None)

    def _drop_context(self, owner: LogicalInvocationKey) -> None:
        self.radix.remove(owner)
        self._invalidate_host_copy(owner)
        self.context_last_service.pop(owner, None)
    def _charge_fastpath(self, *, physical: bool) -> None:
        if self.overhead != PlannerOverhead.MEASURED_FASTPATH:
            return
        elapsed = (
            self.config.measured_physical_validation_ms
            if physical
            else self.config.measured_fastpath_ms
        )
        self.planner_overhead_ms += elapsed
        self._advance_time(self.now_ms + elapsed)
        self._drain_events()

    def _hbm_tokens(self) -> int:
        return self.radix.unique_tokens + self.active_output_tokens + self.hbm_reserved_tokens

    def _update_peak(self) -> None:
        self.hbm_peak_tokens = max(self.hbm_peak_tokens, self._hbm_tokens())
