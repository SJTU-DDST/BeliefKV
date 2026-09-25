from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from beliefkv.control.causal_graph import InvocationState, JoinMode, RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEventKind
from beliefkv.predictor.action_frontier import (
    ActionTimingCurve,
    OperationalReleaseModel,
    PooledConditionalClassifier,
    PooledConditionalDemandModel,
)
from beliefkv.predictor.frontier_belief import (
    BeliefScope,
    BoundaryEvent,
    DemandPhase,
    DemandScenario,
    DependencyMode,
    ExternalDemandSegment,
    FinitePlanningHorizon,
    FrontierDemandOutcome,
    FrontierBeliefSnapshot,
    OtherResidualPolicy,
    PredictiveEvidenceReadSet,
    ScenarioProjection,
)


STRUCTURED_FRONTIER_SCHEMA_VERSION = 7
SUPPORTED_STRUCTURED_FRONTIER_SCHEMA_VERSIONS = frozenset({4, 5, 6, 7})
TOOL_FEATURE_CONTRACTS = frozenset({"legacy", "observed_command_child_v1"})
MINIMUM_DEMAND_DECISION_SCHEMA_VERSION = 2
FORMAL_P6_DATASET_KIND = "beliefkv_p6_training_evidence"
FORMAL_P6_PLAN_IDS = frozenset(
    {
        "p6-agent-semantics-v1",
        "h200-bf16-formal-train-v1",
        "h200-bf16-formal-calibration-v1",
        "qwen35-native-reactive-v0520-v1",
        "qwen35-native-reactive-v0520-v2",
        "qwen35-native-reactive-v0520-v3",
        "qwen35-native-reactive-v0520-v4-128root",
        "qwen35-native-reactive-v0520-v5-overlapped-128root",
        "qwen35-native-reactive-v0520-v1-calibration-66root",
    }
)
FORBIDDEN_LOAD_COUPLED_LABELS = frozenset(
    {"remaining_gpu_service_ms", "next_gpu_service_ms"}
)
FORBIDDEN_LOAD_COUPLED_FEATURES = frozenset(
    {"batch_size", "elapsed_gpu_service_ms", "observed_gpu_service_ms"}
)


def runtime_environment_identity(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable model/runtime/hardware identity shared across splits."""

    profile = contract.get("runtime_profile") or {}
    hardware = contract.get("hardware") or {}
    server = contract.get("server_identity") or {}
    return {
        "runtime_profile": {
            "profile_id": profile.get("profile_id"),
            "sha256": profile.get("sha256"),
        },
        "model_revision_sha256": contract.get("model_revision_sha256") or {},
        "hardware": {
            key: hardware.get(key)
            for key in ("name", "uuid", "driver_version", "memory_total_mib")
        },
        "server_identity": {
            key: server.get(key)
            for key in (
                "model_path",
                "served_model_name",
                "sglang_version",
                "weight_dtype",
                "configured_kv_dtype",
                "resolved_kv_dtype",
            )
        },
        "sglang_commit": contract.get("sglang_commit"),
        "sglang_patch_sha256": contract.get("sglang_patch_sha256"),
    }


def runtime_environment_digest(contract: Mapping[str, Any]) -> str:
    identity = runtime_environment_identity(contract)
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class FrontierModelHyperparameters:
    boundary_max_order: int = 4
    boundary_minimum_support: float = 3.0
    boundary_smoothing: float = 0.5
    empirical_minimum_support: float = 4.0
    tool_minimum_support: float = 4.0
    tool_smoothing: float = 0.5
    pooled_demand_regularization: float = 1e-3
    operational_timing_regularization: float = 1e-5
    pooled_classifier_regularization: float = 1e-3
    pooled_classifier_balance_power: float = 0.5

    def __post_init__(self) -> None:
        if self.boundary_max_order < 0:
            raise ValueError("boundary_max_order must be non-negative")
        if min(
            self.boundary_minimum_support,
            self.boundary_smoothing,
            self.empirical_minimum_support,
            self.tool_minimum_support,
            self.tool_smoothing,
        ) <= 0:
            raise ValueError("frontier hyperparameters must be positive")
        if min(
            self.pooled_demand_regularization,
            self.operational_timing_regularization,
            self.pooled_classifier_regularization,
        ) < 0:
            raise ValueError("pooled model regularization must be non-negative")
        if not 0.0 <= self.pooled_classifier_balance_power <= 1.0:
            raise ValueError("classifier balance power must be in [0, 1]")

    def to_dict(self) -> dict[str, float | int]:
        return {
            "boundary_max_order": self.boundary_max_order,
            "boundary_minimum_support": self.boundary_minimum_support,
            "boundary_smoothing": self.boundary_smoothing,
            "empirical_minimum_support": self.empirical_minimum_support,
            "tool_minimum_support": self.tool_minimum_support,
            "tool_smoothing": self.tool_smoothing,
            "pooled_demand_regularization": self.pooled_demand_regularization,
            "operational_timing_regularization": (
                self.operational_timing_regularization
            ),
            "pooled_classifier_regularization": (
                self.pooled_classifier_regularization
            ),
            "pooled_classifier_balance_power": (
                self.pooled_classifier_balance_power
            ),
        }

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any] | None
    ) -> "FrontierModelHyperparameters":
        values = raw or {}
        return cls(
            boundary_max_order=int(values.get("boundary_max_order", 4)),
            boundary_minimum_support=float(
                values.get("boundary_minimum_support", 3.0)
            ),
            boundary_smoothing=float(values.get("boundary_smoothing", 0.5)),
            empirical_minimum_support=float(
                values.get("empirical_minimum_support", 4.0)
            ),
            tool_minimum_support=float(values.get("tool_minimum_support", 4.0)),
            tool_smoothing=float(values.get("tool_smoothing", 0.5)),
            pooled_demand_regularization=float(
                values.get("pooled_demand_regularization", 1e-3)
            ),
            operational_timing_regularization=float(
                values.get("operational_timing_regularization", 1e-5)
            ),
            pooled_classifier_regularization=float(
                values.get("pooled_classifier_regularization", 1e-3)
            ),
            pooled_classifier_balance_power=float(
                values.get("pooled_classifier_balance_power", 0.5)
            ),
        )


@dataclass(frozen=True)
class LocalFrontierFeatures:
    invocation_id: str
    state: str
    agent_definition_id: str = "unknown"
    boundary_history: tuple[str, ...] = ()
    tool_family: str = "unknown"
    backend_class: str = "unknown"
    command_class: str = "unknown"
    observed_command_class: str = "unknown"
    generated_tokens: int = 0
    elapsed_wait_ms: float = 0.0
    current_sequence_tokens: int = 0
    active_tool_count: int = 0
    backend_pressure: str = "unknown"
    invocation_elapsed_ms: float = 0.0
    state_elapsed_ms: float = 0.0
    llm_round: int = 0
    child_count: int = 0
    unfinished_child_count: int = 0
    is_child: bool = False

    def __post_init__(self) -> None:
        if min(
            self.generated_tokens,
            self.current_sequence_tokens,
            self.active_tool_count,
            self.llm_round,
            self.child_count,
            self.unfinished_child_count,
        ) < 0:
            raise ValueError("frontier demand features must be non-negative")
        if min(self.invocation_elapsed_ms, self.state_elapsed_ms) < 0:
            raise ValueError("frontier elapsed features must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "state": self.state,
            "agent_definition_id": self.agent_definition_id,
            "boundary_history": list(self.boundary_history),
            "tool_family": self.tool_family,
            "backend_class": self.backend_class,
            "command_class": self.command_class,
            "observed_command_class": self.observed_command_class,
            "generated_tokens": self.generated_tokens,
            "elapsed_wait_ms": self.elapsed_wait_ms,
            "current_sequence_tokens": self.current_sequence_tokens,
            "active_tool_count": self.active_tool_count,
            "backend_pressure": self.backend_pressure,
            "invocation_elapsed_ms": self.invocation_elapsed_ms,
            "state_elapsed_ms": self.state_elapsed_ms,
            "llm_round": self.llm_round,
            "child_count": self.child_count,
            "unfinished_child_count": self.unfinished_child_count,
            "is_child": self.is_child,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LocalFrontierFeatures":
        return cls(
            invocation_id=str(raw["invocation_id"]),
            state=str(raw["state"]),
            agent_definition_id=str(raw.get("agent_definition_id") or "unknown"),
            boundary_history=tuple(
                str(item) for item in raw.get("boundary_history", ())
            ),
            tool_family=str(raw.get("tool_family") or "unknown"),
            backend_class=str(raw.get("backend_class") or "unknown"),
            command_class=str(raw.get("command_class") or "unknown"),
            observed_command_class=str(
                raw.get("observed_command_class") or "unknown"
            ),
            generated_tokens=int(raw.get("generated_tokens") or 0),
            elapsed_wait_ms=float(raw.get("elapsed_wait_ms") or 0.0),
            current_sequence_tokens=int(raw.get("current_sequence_tokens") or 0),
            active_tool_count=int(raw.get("active_tool_count") or 0),
            backend_pressure=str(raw.get("backend_pressure") or "unknown"),
            invocation_elapsed_ms=float(
                raw.get("invocation_elapsed_ms") or 0.0
            ),
            state_elapsed_ms=float(raw.get("state_elapsed_ms") or 0.0),
            llm_round=int(raw.get("llm_round") or 0),
            child_count=int(raw.get("child_count") or 0),
            unfinished_child_count=int(
                raw.get("unfinished_child_count") or 0
            ),
            is_child=raw.get("is_child") is True,
        )


@dataclass(frozen=True)
class EmpiricalDistribution:
    values: tuple[float, ...]
    probability_mass: tuple[float, ...]
    support: float

    def __post_init__(self) -> None:
        if len(self.values) != len(self.probability_mass):
            raise ValueError("empirical values and probabilities must align")
        if self.support < 0 or any(value < 0 for value in self.values):
            raise ValueError("empirical distributions require non-negative values")
        if self.values and not math.isclose(
            sum(self.probability_mass), 1.0, rel_tol=1e-7, abs_tol=1e-7
        ):
            raise ValueError("empirical probability mass must sum to one")

    @classmethod
    def empty(cls) -> "EmpiricalDistribution":
        return cls((), (), 0.0)

    def sample(self, quantile: float) -> float:
        if not self.values:
            return 0.0
        threshold = min(1.0, max(0.0, quantile))
        cumulative = 0.0
        for value, probability in zip(self.values, self.probability_mass):
            cumulative += probability
            if threshold <= cumulative:
                return value
        return self.values[-1]

    def quantile(self, quantile: float) -> float:
        return self.sample(quantile)

    def probability_greater_than(self, threshold: float) -> float:
        """Return P(X > threshold) without fitting an absolute-time regressor."""

        if not self.values:
            return 0.0
        return sum(
            probability
            for value, probability in zip(self.values, self.probability_mass)
            if value > max(0.0, threshold)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": list(self.values),
            "probability_mass": list(self.probability_mass),
            "support": self.support,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EmpiricalDistribution":
        return cls(
            tuple(float(item) for item in raw.get("values", ())),
            tuple(float(item) for item in raw.get("probability_mass", ())),
            float(raw.get("support", 0.0)),
        )


class WaitBeliefKind(str, Enum):
    NONE = "none"
    TOOL = "tool"
    JOIN = "join"
    CHILD = "child"
    MESSAGE = "message"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WaitBelief:
    """Action-specific wait evidence; structural waits are composed by RCCG."""

    kind: WaitBeliefKind
    residual_duration: EmpiricalDistribution = field(
        default_factory=EmpiricalDistribution.empty
    )
    terminal_distribution: Mapping[str, float] = field(default_factory=dict)
    support_level: str = "unavailable"
    support_detail: str = "unspecified"
    dependency_composed: bool = False
    ood_reasons: tuple[str, ...] = ()
    survival_logit_scale: float = 1.0
    survival_logit_offset: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", WaitBeliefKind(self.kind))
        if self.support_level not in {
            "exact",
            "role",
            "backoff",
            "global",
            "structural",
            "unavailable",
        }:
            raise ValueError("invalid wait-belief support level")
        if not self.support_detail:
            raise ValueError("wait-belief support detail is required")
        if self.dependency_composed and self.kind not in {
            WaitBeliefKind.JOIN,
            WaitBeliefKind.CHILD,
            WaitBeliefKind.MESSAGE,
        }:
            raise ValueError("only causal waits may be dependency-composed")
        if self.dependency_composed and self.residual_duration.values:
            raise ValueError("dependency-composed waits cannot carry wall-clock fits")
        if (
            not math.isfinite(self.survival_logit_scale)
            or self.survival_logit_scale <= 0
            or not math.isfinite(self.survival_logit_offset)
        ):
            raise ValueError("wait survival calibration must be finite")
        object.__setattr__(
            self,
            "terminal_distribution",
            {
                str(name): float(probability)
                for name, probability in self.terminal_distribution.items()
            },
        )
        object.__setattr__(
            self,
            "ood_reasons",
            tuple(sorted(set(str(item) for item in self.ood_reasons if item))),
        )

    @property
    def available(self) -> bool:
        if self.dependency_composed:
            return self.support_level == "structural"
        return bool(
            self.residual_duration.values
            and self.residual_duration.support > 0
            and self.support_level != "unavailable"
        )

    def release_after_probability(self, operational_tau_ms: float) -> float | None:
        """Return P(release occurs after a live transfer deadline)."""

        if self.dependency_composed or not self.available:
            return None
        raw = self.raw_release_after_probability(operational_tau_ms)
        if raw is None:
            return None
        return _calibrate_binary_probability(
            raw,
            scale=self.survival_logit_scale,
            offset=self.survival_logit_offset,
        )

    def raw_release_after_probability(
        self, operational_tau_ms: float
    ) -> float | None:
        """Return the uncalibrated survival probability at an action deadline."""

        if self.dependency_composed or not self.available:
            return None
        return self.residual_duration.probability_greater_than(operational_tau_ms)

    def release_within_probability(self, operational_tau_ms: float) -> float | None:
        """Return P(reentry occurs within a live restore deadline)."""

        probability = self.release_after_probability(operational_tau_ms)
        return None if probability is None else 1.0 - probability

    def slack_probability(self, required_wait_ms: float) -> float | None:
        """Compatibility alias for PREPARE_HOST release-after probability."""

        return self.release_after_probability(required_wait_ms)

    def conservative_wait_ms(self, quantile: float = 0.05) -> float | None:
        if self.dependency_composed or not self.available:
            return None
        return self.residual_duration.quantile(quantile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "residual_duration": self.residual_duration.to_dict(),
            "terminal_distribution": dict(self.terminal_distribution),
            "support_level": self.support_level,
            "support_detail": self.support_detail,
            "dependency_composed": self.dependency_composed,
            "ood_reasons": list(self.ood_reasons),
            "survival_logit_scale": self.survival_logit_scale,
            "survival_logit_offset": self.survival_logit_offset,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "WaitBelief":
        return cls(
            kind=WaitBeliefKind(str(raw.get("kind") or "unknown")),
            residual_duration=EmpiricalDistribution.from_dict(
                raw.get("residual_duration", {})
            ),
            terminal_distribution={
                str(name): float(probability)
                for name, probability in raw.get(
                    "terminal_distribution", {}
                ).items()
            },
            support_level=str(raw.get("support_level") or "unavailable"),
            support_detail=str(raw.get("support_detail") or "unspecified"),
            dependency_composed=bool(raw.get("dependency_composed", False)),
            ood_reasons=tuple(str(item) for item in raw.get("ood_reasons", ())),
            survival_logit_scale=float(raw.get("survival_logit_scale", 1.0)),
            survival_logit_offset=float(raw.get("survival_logit_offset", 0.0)),
        )


@dataclass(frozen=True)
class ActionTimingPrediction:
    """Action-aligned timing evidence consumed by JointPlan."""

    action: str
    operational_tau_ms: float
    favorable_probability: float
    semantics: str
    support_level: str
    calibration_brier_skill: float | None = None
    calibration_balanced_accuracy: float | None = None
    calibration_episode_weight: float = 0.0
    decision_threshold: float = 0.5
    raw_decision_threshold: float = 0.5
    precision_at_decision_threshold: float | None = None
    recall_at_decision_threshold: float | None = None

    @property
    def informative(self) -> bool:
        return (
            self.calibration_brier_skill is None
            or self.calibration_brier_skill > 0.0
        )


@dataclass(frozen=True)
class LocalFrontierPrediction:
    invocation_id: str
    boundary_distribution: Mapping[str, float]
    current_sequence_tokens: int
    remaining_decode_tokens: EmpiricalDistribution
    remaining_external_wait: EmpiricalDistribution
    tool_terminal_distribution: Mapping[str, float]
    prompt_growth_tokens: EmpiricalDistribution
    next_output_tokens: EmpiricalDistribution
    support_level: str
    calibration_coverage: float
    remaining_to_return_ms: EmpiricalDistribution = field(
        default_factory=EmpiricalDistribution.empty
    )
    ood_reasons: tuple[str, ...] = ()
    calibrated_intervals: Mapping[str, tuple[float, float]] = field(
        default_factory=dict
    )
    wait_belief: WaitBelief | None = None
    head_support: Mapping[str, str] = field(default_factory=dict)
    action_timing_calibration: Mapping[str, Mapping[str, float]] = field(
        default_factory=dict
    )
    operational_timing_curve: ActionTimingCurve | None = None

    def __post_init__(self) -> None:
        wait = self.wait_belief
        if wait is None:
            legacy_available = bool(self.remaining_external_wait.values)
            wait = WaitBelief(
                kind=(
                    WaitBeliefKind.TOOL
                    if legacy_available
                    else WaitBeliefKind.UNKNOWN
                ),
                residual_duration=self.remaining_external_wait,
                terminal_distribution=self.tool_terminal_distribution,
                support_level=(
                    self.support_level if legacy_available else "unavailable"
                ),
                ood_reasons=("legacy_wait_contract",),
            )
            object.__setattr__(self, "wait_belief", wait)
        support = {
            str(name): str(level)
            for name, level in self.head_support.items()
        }
        if not support:
            legacy_level = (
                self.support_level
                if self.support_level in {"exact", "backoff"}
                else "unavailable"
            )
            support = {
                "boundary": (
                    legacy_level
                    if self.boundary_distribution
                    else "unavailable"
                ),
                "remaining_decode_demand": (
                    legacy_level
                    if self.remaining_decode_tokens.values
                    else "unavailable"
                ),
                "next_output_demand": (
                    legacy_level
                    if self.next_output_tokens.values
                    else "unavailable"
                ),
                "prompt_growth": (
                    legacy_level
                    if self.prompt_growth_tokens.values
                    else "unavailable"
                ),
                "tool_wait": (
                    self.wait_belief.support_level
                    if self.wait_belief.kind == WaitBeliefKind.TOOL
                    else "unavailable"
                ),
                "join_dependency": (
                    "structural"
                    if self.wait_belief.kind
                    in {WaitBeliefKind.JOIN, WaitBeliefKind.CHILD}
                    else "unavailable"
                ),
                "message_dependency": (
                    "structural"
                    if self.wait_belief.kind == WaitBeliefKind.MESSAGE
                    else "unavailable"
                ),
                "child_completion": (
                    legacy_level
                    if self.remaining_to_return_ms.values
                    else "unavailable"
                ),
            }
        object.__setattr__(self, "head_support", support)
        object.__setattr__(
            self,
            "action_timing_calibration",
            {
                str(action): {
                    str(name): float(value)
                    for name, value in quality.items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                }
                for action, quality in self.action_timing_calibration.items()
            },
        )

    def support_for(self, head: str) -> str:
        return str(self.head_support.get(head, "unavailable"))

    def action_timing(
        self, action: str, operational_tau_ms: float
    ) -> ActionTimingPrediction | None:
        """Project local wait belief onto a live transfer deadline."""

        if action not in {"prepare_host", "prefetch_gpu"}:
            raise ValueError(f"unsupported timing action: {action}")
        if self.operational_timing_curve is not None:
            raw_within = self.operational_timing_curve.release_within(
                operational_tau_ms
            )
            raw_after = 1.0 - raw_within
            timing_support_level = self.operational_timing_curve.support_level
        else:
            raw_after = self.wait_belief.raw_release_after_probability(
                operational_tau_ms
            )
            if raw_after is None:
                return None
            timing_support_level = self.wait_belief.support_level
        raw = raw_after if action == "prepare_host" else 1.0 - raw_after
        quality = self.action_timing_calibration.get(action, {})
        if "logit_scale" in quality and "logit_offset" in quality:
            scale = float(quality["logit_scale"])
            offset = float(quality["logit_offset"])
            probability = _calibrate_binary_probability(
                raw,
                scale=scale,
                offset=offset,
            )
        else:
            scale = 1.0
            offset = 0.0
            probability = raw
        if probability is None:
            return None
        decision_threshold = float(quality.get("decision_threshold", 0.5))
        return ActionTimingPrediction(
            action=action,
            operational_tau_ms=operational_tau_ms,
            favorable_probability=probability,
            semantics=(
                "release_after_transfer"
                if action == "prepare_host"
                else "release_within_transfer"
            ),
            support_level=timing_support_level,
            calibration_brier_skill=quality.get("brier_skill"),
            calibration_balanced_accuracy=quality.get("balanced_accuracy_at_0_5"),
            calibration_episode_weight=float(quality.get("episode_weight", 0.0)),
            decision_threshold=decision_threshold,
            raw_decision_threshold=_uncalibrate_binary_probability(
                decision_threshold,
                scale=scale,
                offset=offset,
            ),
            precision_at_decision_threshold=quality.get(
                "precision_at_decision_threshold"
            ),
            recall_at_decision_threshold=quality.get(
                "recall_at_decision_threshold"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "boundary_distribution": dict(self.boundary_distribution),
            "current_sequence_tokens": self.current_sequence_tokens,
            "remaining_decode_tokens": self.remaining_decode_tokens.to_dict(),
            "remaining_external_wait": self.remaining_external_wait.to_dict(),
            "wait_belief": self.wait_belief.to_dict(),
            "tool_terminal_distribution": dict(self.tool_terminal_distribution),
            "prompt_growth_tokens": self.prompt_growth_tokens.to_dict(),
            "next_output_tokens": self.next_output_tokens.to_dict(),
            "remaining_to_return_ms": self.remaining_to_return_ms.to_dict(),
            "support_level": self.support_level,
            "calibration_coverage": self.calibration_coverage,
            "ood_reasons": list(self.ood_reasons),
            "calibrated_intervals": {
                name: list(interval)
                for name, interval in sorted(self.calibrated_intervals.items())
            },
            "head_support": dict(sorted(self.head_support.items())),
            "action_timing_calibration": {
                action: dict(sorted(quality.items()))
                for action, quality in sorted(
                    self.action_timing_calibration.items()
                )
            },
            "operational_timing_curve": (
                self.operational_timing_curve.to_dict()
                if self.operational_timing_curve is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LocalFrontierPrediction":
        intervals = raw.get("calibrated_intervals", {})
        return cls(
            invocation_id=str(raw["invocation_id"]),
            boundary_distribution={
                str(name): float(probability)
                for name, probability in raw.get(
                    "boundary_distribution", {}
                ).items()
            },
            current_sequence_tokens=int(raw.get("current_sequence_tokens", 0)),
            remaining_decode_tokens=EmpiricalDistribution.from_dict(
                raw.get("remaining_decode_tokens", {})
            ),
            remaining_external_wait=EmpiricalDistribution.from_dict(
                raw.get("remaining_external_wait", {})
            ),
            tool_terminal_distribution={
                str(name): float(probability)
                for name, probability in raw.get(
                    "tool_terminal_distribution", {}
                ).items()
            },
            prompt_growth_tokens=EmpiricalDistribution.from_dict(
                raw.get("prompt_growth_tokens", {})
            ),
            next_output_tokens=EmpiricalDistribution.from_dict(
                raw.get("next_output_tokens", {})
            ),
            support_level=str(raw.get("support_level", "unavailable")),
            calibration_coverage=float(raw.get("calibration_coverage", 0.0)),
            remaining_to_return_ms=EmpiricalDistribution.from_dict(
                raw.get("remaining_to_return_ms", {})
            ),
            ood_reasons=tuple(str(item) for item in raw.get("ood_reasons", ())),
            calibrated_intervals={
                str(name): (float(value[0]), float(value[1]))
                for name, value in intervals.items()
            },
            wait_belief=(
                WaitBelief.from_dict(raw.get("wait_belief", {}))
                if raw.get("wait_belief")
                else None
            ),
            head_support={
                str(name): str(level)
                for name, level in raw.get("head_support", {}).items()
            },
            action_timing_calibration={
                str(action): {
                    str(name): float(value)
                    for name, value in quality.items()
                }
                for action, quality in raw.get(
                    "action_timing_calibration", {}
                ).items()
            },
            operational_timing_curve=(
                ActionTimingCurve.from_dict(raw["operational_timing_curve"])
                if isinstance(raw.get("operational_timing_curve"), Mapping)
                else None
            ),
        )


class _HierarchicalEmpiricalModel:
    def __init__(self, *, minimum_support: float = 4.0) -> None:
        self.minimum_support = minimum_support
        self._groups: dict[tuple[str, ...], Counter[float]] = defaultdict(Counter)

    def observe(self, key: Sequence[str], value: float, *, weight: float = 1.0) -> None:
        if value < 0 or weight <= 0:
            return
        bucket = _log_bucket(value)
        for candidate in _backoff_keys(tuple(key)):
            self._groups[candidate][bucket] += weight

    def predict(self, key: Sequence[str]) -> tuple[EmpiricalDistribution, str]:
        candidates = _backoff_keys(tuple(key))
        selected = candidates[-1]
        for candidate in candidates:
            support = sum(self._groups.get(candidate, {}).values())
            if support >= self.minimum_support:
                selected = candidate
                break
        counts = self._groups.get(selected, Counter())
        total = sum(counts.values())
        if total <= 0:
            return EmpiricalDistribution.empty(), "unavailable"
        values = tuple(sorted(counts))
        distribution = EmpiricalDistribution(
            values,
            tuple(counts[value] / total for value in values),
            total,
        )
        level = "exact" if selected == candidates[0] else (
            "global" if selected == ("*",) else "backoff"
        )
        return distribution, level

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum_support": self.minimum_support,
            "groups": [
                {
                    "key": list(key),
                    "counts": {str(value): count for value, count in sorted(counts.items())},
                }
                for key, counts in sorted(self._groups.items())
            ],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "_HierarchicalEmpiricalModel":
        model = cls(minimum_support=float(raw.get("minimum_support", 4.0)))
        for item in raw.get("groups", ()):
            model._groups[tuple(str(value) for value in item["key"])] = Counter(
                {float(value): float(count) for value, count in item["counts"].items()}
            )
        return model


class _BoundaryContextTree:
    def __init__(
        self, *, max_order: int = 4, minimum_support: float = 3.0, smoothing: float = 0.5
    ) -> None:
        self.max_order = max_order
        self.minimum_support = minimum_support
        self.smoothing = smoothing
        self._counts: dict[tuple[str, str, tuple[str, ...]], Counter[str]] = defaultdict(Counter)

    def observe(
        self,
        *,
        role: str,
        state: str,
        history: Sequence[str],
        target: str,
        weight: float,
    ) -> None:
        history = tuple(history)
        for order in range(min(self.max_order, len(history)) + 1):
            context = history[-order:] if order else ()
            self._counts[(role, state, context)][target] += weight
            self._counts[("*", state, context)][target] += weight

    def predict(
        self, *, role: str, state: str, history: Sequence[str]
    ) -> tuple[dict[str, float], str, float]:
        history = tuple(history)
        selected: tuple[str, str, tuple[str, ...]] | None = None
        for candidate_role in (role, "*"):
            for order in range(min(self.max_order, len(history)), -1, -1):
                candidate = (candidate_role, state, history[-order:] if order else ())
                if sum(self._counts.get(candidate, {}).values()) >= self.minimum_support:
                    selected = candidate
                    break
            if selected is not None:
                break
        global_counts = self._counts.get(("*", state, ()), Counter())
        counts = self._counts.get(selected, Counter()) if selected else global_counts
        vocabulary = tuple(
            item.value
            for item in (
                BoundaryEvent.TOOL,
                BoundaryEvent.SPAWN,
                BoundaryEvent.HANDOFF,
                BoundaryEvent.RETURN,
                BoundaryEvent.FINAL,
            )
        )
        if not global_counts:
            return {BoundaryEvent.UNKNOWN.value: 1.0}, "unavailable", 0.0
        total = sum(counts.values())
        global_total = sum(global_counts.values())
        probabilities = {
            target: (
                counts[target]
                + self.smoothing
                * (
                    global_counts[target] + self.smoothing / len(vocabulary)
                )
                / (global_total + self.smoothing)
            )
            / (total + self.smoothing)
            for target in vocabulary
        }
        normalizer = sum(probabilities.values())
        probabilities = {key: value / normalizer for key, value in probabilities.items()}
        level = (
            "exact"
            if selected and selected[0] == role and selected[2]
            else "role"
            if selected and selected[0] == role
            else "global"
        )
        return probabilities, level, total

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_order": self.max_order,
            "minimum_support": self.minimum_support,
            "smoothing": self.smoothing,
            "counts": [
                {
                    "role": role,
                    "state": state,
                    "history": list(history),
                    "targets": dict(sorted(counts.items())),
                }
                for (role, state, history), counts in sorted(self._counts.items())
            ],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "_BoundaryContextTree":
        model = cls(
            max_order=int(raw.get("max_order", 4)),
            minimum_support=float(raw.get("minimum_support", 3.0)),
            smoothing=float(raw.get("smoothing", 0.5)),
        )
        for item in raw.get("counts", ()):
            key = (
                str(item["role"]),
                str(item["state"]),
                tuple(str(value) for value in item.get("history", ())),
            )
            model._counts[key] = Counter(
                {str(target): float(count) for target, count in item["targets"].items()}
            )
        return model


class _CompetingRiskToolModel:
    def __init__(self, *, minimum_support: float = 4.0, smoothing: float = 0.5) -> None:
        self.minimum_support = minimum_support
        self.smoothing = smoothing
        self._status: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
        self._wait = _HierarchicalEmpiricalModel(minimum_support=minimum_support)
        self._duration_by_status: dict[
            tuple[str, ...], dict[str, Counter[float]]
        ] = defaultdict(lambda: defaultdict(Counter))

    def observe(
        self,
        key: Sequence[str],
        *,
        status: str,
        duration_ms: float,
        weight: float = 1.0,
    ) -> None:
        normalized = _normalize_tool_terminal(status)
        bucket = _log_bucket(duration_ms)
        for candidate in _backoff_keys(tuple(key)):
            self._status[candidate][normalized] += weight
            self._duration_by_status[candidate][normalized][bucket] += weight
        self._wait.observe(key, duration_ms, weight=weight)

    def predict(
        self, key: Sequence[str], *, elapsed_ms: float = 0.0
    ) -> tuple[dict[str, float], EmpiricalDistribution, str, str]:
        candidates = _backoff_keys(tuple(key))
        selected = candidates[-1]
        for candidate in candidates:
            if sum(self._status.get(candidate, {}).values()) >= self.minimum_support:
                selected = candidate
                break
        duration_by_status = self._duration_by_status.get(selected, {})
        has_joint_observations = bool(duration_by_status)
        survivors: Counter[str] = Counter()
        residuals: Counter[float] = Counter()
        elapsed_ms = max(0.0, float(elapsed_ms))
        for status, durations in duration_by_status.items():
            for duration, weight in durations.items():
                if duration + 1e-9 < elapsed_ms:
                    continue
                survivors[status] += weight
                residuals[_log_bucket(max(0.0, duration - elapsed_ms))] += weight

        # Schema-v1 models have no joint duration/status observations. Preserve
        # their unconditional behavior for development-artifact compatibility.
        counts = (
            survivors
            if has_joint_observations
            else self._status.get(selected, Counter())
        )
        total = sum(counts.values())
        if total <= 0:
            statuses = {"censored": 1.0}
            level = "unavailable"
        else:
            vocabulary = ("success", "error", "censored")
            statuses = {
                item: (counts[item] + self.smoothing / len(vocabulary))
                / (total + self.smoothing)
                for item in vocabulary
            }
            level = "exact" if selected == candidates[0] else (
                "global" if selected == ("*",) else "backoff"
            )
        if residuals:
            residual_total = sum(residuals.values())
            wait = EmpiricalDistribution(
                tuple(sorted(residuals)),
                tuple(
                    residuals[value] / residual_total for value in sorted(residuals)
                ),
                residual_total,
            )
            wait_level = level
        elif not has_joint_observations:
            wait, wait_level = self._wait.predict(key)
        else:
            wait = EmpiricalDistribution.empty()
            wait_level = "unavailable"
        effective_level = level if level != "unavailable" else wait_level
        return (
            statuses,
            wait,
            effective_level,
            _tool_support_detail(tuple(key), selected, effective_level),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum_support": self.minimum_support,
            "smoothing": self.smoothing,
            "status": [
                {"key": list(key), "counts": dict(sorted(counts.items()))}
                for key, counts in sorted(self._status.items())
            ],
            "duration_by_status": [
                {
                    "key": list(key),
                    "durations": {
                        status: {
                            str(duration): count
                            for duration, count in sorted(counts.items())
                        }
                        for status, counts in sorted(by_status.items())
                    },
                }
                for key, by_status in sorted(self._duration_by_status.items())
            ],
            "wait": self._wait.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "_CompetingRiskToolModel":
        model = cls(
            minimum_support=float(raw.get("minimum_support", 4.0)),
            smoothing=float(raw.get("smoothing", 0.5)),
        )
        for item in raw.get("status", ()):
            model._status[tuple(str(value) for value in item["key"])] = Counter(
                {str(status): float(count) for status, count in item["counts"].items()}
            )
        for item in raw.get("duration_by_status", ()):
            key = tuple(str(value) for value in item["key"])
            model._duration_by_status[key] = defaultdict(
                Counter,
                {
                    str(status): Counter(
                        {
                            float(duration): float(count)
                            for duration, count in counts.items()
                        }
                    )
                    for status, counts in item.get("durations", {}).items()
                },
            )
        model._wait = _HierarchicalEmpiricalModel.from_dict(raw.get("wait", {}))
        return model


class FrontierBeliefModel:
    """One versioned model that publishes local beliefs but never scheduling actions."""

    def __init__(
        self,
        *,
        model_version: str = "frontier-development",
        hyperparameters: FrontierModelHyperparameters | None = None,
        tool_feature_contract: str = "legacy",
    ) -> None:
        if tool_feature_contract not in TOOL_FEATURE_CONTRACTS:
            raise ValueError("unsupported tool feature contract")
        self.model_version = model_version
        self.tool_feature_contract = tool_feature_contract
        self.hyperparameters = hyperparameters or FrontierModelHyperparameters()
        self.boundary = _BoundaryContextTree(
            max_order=self.hyperparameters.boundary_max_order,
            minimum_support=self.hyperparameters.boundary_minimum_support,
            smoothing=self.hyperparameters.boundary_smoothing,
        )
        self.decode_demand = _HierarchicalEmpiricalModel(
            minimum_support=self.hyperparameters.empirical_minimum_support
        )
        self.next_output = _HierarchicalEmpiricalModel(
            minimum_support=self.hyperparameters.empirical_minimum_support
        )
        self.prompt_growth = _HierarchicalEmpiricalModel(
            minimum_support=self.hyperparameters.empirical_minimum_support
        )
        self.pooled_decode_demand = PooledConditionalDemandModel(
            regularization=self.hyperparameters.pooled_demand_regularization
        )
        self.pooled_next_output = PooledConditionalDemandModel(
            regularization=self.hyperparameters.pooled_demand_regularization
        )
        self.pooled_prompt_growth = PooledConditionalDemandModel(
            regularization=self.hyperparameters.pooled_demand_regularization
        )
        self.pooled_child_completion = PooledConditionalDemandModel(
            regularization=self.hyperparameters.pooled_demand_regularization
        )
        self.pooled_boundary = PooledConditionalClassifier(
            regularization=self.hyperparameters.pooled_classifier_regularization,
            balance_power=self.hyperparameters.pooled_classifier_balance_power,
        )
        self.pooled_tool_terminal = PooledConditionalClassifier(
            regularization=self.hyperparameters.pooled_classifier_regularization,
            balance_power=self.hyperparameters.pooled_classifier_balance_power,
        )
        self.tool = _CompetingRiskToolModel(
            minimum_support=self.hyperparameters.tool_minimum_support,
            smoothing=self.hyperparameters.tool_smoothing,
        )
        self.child_tool = _CompetingRiskToolModel(
            minimum_support=self.hyperparameters.tool_minimum_support,
            smoothing=self.hyperparameters.tool_smoothing,
        )
        self.operational_release = OperationalReleaseModel(
            regularization=(
                self.hyperparameters.operational_timing_regularization
            )
        )
        self.training_summary: dict[str, Any] = {}
        self.calibration_summary: dict[str, Any] = {}
        self.boundary_temperature = 1.0
        self.tool_temperature = 1.0
        self.tool_survival_logit_scale = 1.0
        self.tool_survival_logit_offset = 0.0
        self.action_timing_calibration: dict[str, dict[str, float]] = {}
        self.interval_slack: dict[str, float] = {}
        self.calibration_coverage = 0.0
        self.artifact_metadata: dict[str, Any] = {}

    def fit(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        action_targets: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        values = [dict(row) for row in rows]
        action_values = [dict(row) for row in action_targets]
        _validate_demand_rows(values)
        episode_counts = Counter(
            str(row.get("episode_group_id") or row.get("decision_id")) for row in values
        )
        local_episode_counts = _local_episode_counts(values)
        workflow_episode_counts = _workflow_local_episode_counts(values)
        tool_fit_weights = _tool_fit_weights(
            values, trigger_only=self.tool_feature_contract != "legacy"
        )
        child_completion_weights = _child_completion_fit_weights(values)
        pooled_decode_samples: list[tuple[object, float, float]] = []
        pooled_output_samples: list[tuple[object, float, float]] = []
        pooled_prompt_samples: list[tuple[object, float, float]] = []
        pooled_child_completion_samples: list[
            tuple[object, float, float]
        ] = []
        pooled_boundary_samples: list[tuple[object, str, float]] = []
        pooled_tool_terminal_samples: list[tuple[object, str, float]] = []
        observed = Counter()
        split_counts = Counter(str(row.get("split") or "unknown") for row in values)
        for row in values:
            episode = str(row.get("episode_group_id") or row.get("decision_id"))
            labels = {
                str(item.get("invocation_id")): item for item in row.get("labels", ())
            }
            trigger = str(row.get("trigger_kind") or "")
            trigger_attrs = row.get("trigger_attributes") or {}
            for features in row.get("invocations", ()):
                invocation_id = str(features.get("invocation_id") or "")
                label = labels.get(invocation_id)
                if label is None:
                    continue
                weight = 1.0 / max(
                    1, local_episode_counts[(episode, invocation_id)]
                )
                workflow = _workflow_group_id(row)
                weight /= max(1, workflow_episode_counts[workflow])
                role = str(features.get("agent_definition_id") or "unknown")
                state = str(features.get("state") or "unknown")
                family = str(
                    trigger_attrs.get("tool_family")
                    or features.get("active_tool_family")
                    or "unknown"
                )
                backend = str(
                    trigger_attrs.get("backend_class")
                    or features.get("backend_class")
                    or "unknown"
                )
                command = str(
                    trigger_attrs.get("command_class")
                    or trigger_attrs.get("tool_name")
                    or backend
                    or "unknown"
                )
                key = _demand_feature_key(role, state, family, features)
                local_features = _local_features_from_row(
                    row, features, tool_feature_contract=self.tool_feature_contract
                )
                boundary = _normalize_boundary(label.get("next_boundary_kind"))
                if state == InvocationState.RUNNING_LLM.value and boundary is not None and _target_eligible(label, "action_boundary"):
                    self.boundary.observe(
                        role=role,
                        state=state,
                        history=features.get("boundary_history", ()),
                        target=boundary,
                        weight=weight,
                    )
                    pooled_boundary_samples.append(
                        (local_features, boundary, weight)
                    )
                    observed["boundary"] += 1
                remaining_decode = (
                    label.get("remaining_output_tokens")
                    if state == InvocationState.RUNNING_LLM.value
                    else None
                )
                if remaining_decode is not None and _target_eligible(label, "remaining_decode_demand"):
                    self.decode_demand.observe(
                        key, float(remaining_decode), weight=weight
                    )
                    pooled_decode_samples.append(
                        (local_features, float(remaining_decode), weight)
                    )
                    observed["remaining_decode_demand"] += 1
                elif state != InvocationState.RUNNING_LLM.value:
                    # State-semantic decode target (P6 improvement, deepseek):
                    # for non-running states the scheduler-relevant quantity is
                    # the decode work of the *next* LLM call after the boundary
                    # (tool result / join / child / ready dispatch). Without
                    # this, (role, state, ...) keys have no support and every
                    # non-running invocation falls to the global bucket,
                    # producing constant p50 predictions for the scheduler.
                    next_decode = label.get("next_output_tokens")
                    if next_decode is not None and _target_eligible(label, "next_output_demand"):
                        self.decode_demand.observe(
                            key, float(next_decode), weight=weight
                        )
                        observed["state_conditional_decode_demand"] += 1
                next_output = label.get("next_output_tokens")
                if next_output is not None and _target_eligible(label, "next_output_demand"):
                    self.next_output.observe(key, float(next_output), weight=weight)
                    pooled_output_samples.append(
                        (local_features, float(next_output), weight)
                    )
                    observed["next_output_demand"] += 1
                prompt_growth = label.get("reentry_prompt_delta_tokens")
                if prompt_growth is not None and _target_eligible(label, "prompt_growth"):
                    self.prompt_growth.observe(key, float(prompt_growth), weight=weight)
                    pooled_prompt_samples.append(
                        (local_features, float(prompt_growth), weight)
                    )
                    observed["prompt_growth"] += 1
                remaining_to_return = label.get("remaining_to_return_ms")
                if (
                    remaining_to_return is not None
                    and _target_eligible(label, "child_completion")
                ):
                    pooled_child_completion_samples.append(
                        (
                            local_features,
                            float(remaining_to_return),
                            child_completion_weights.get(
                                (
                                    str(row.get("decision_id") or ""),
                                    invocation_id,
                                ),
                                weight,
                            ),
                        )
                    )
                    observed["child_completion"] += 1
                if (
                    trigger == RuntimeEventKind.TOOL_START.value
                    and state == InvocationState.WAIT_TOOL.value
                ):
                    if self.tool_feature_contract != "legacy":
                        if not row.get("trigger_invocation_id"):
                            raise ValueError("tool start lacks trigger invocation identity")
                        if row["trigger_invocation_id"] != invocation_id:
                            continue
                        if type(trigger_attrs.get("is_child")) is not bool:
                            raise ValueError("tool start lacks root/child provenance")
                        if not trigger_attrs.get("observed_command_class"):
                            raise ValueError("tool start lacks observed command class")
                    right_censored = _target_right_censored(
                        label, "external_wait"
                    )
                    status = str(label.get("next_boundary_status") or "error")
                    if right_censored:
                        status = "censored"
                    delay = (
                        label.get("external_wait_observed_duration_ms")
                        if right_censored
                        else label.get("next_boundary_delay_ms")
                    )
                    if delay is not None and _target_eligible(label, "external_wait"):
                        tool_weight = tool_fit_weights.get(
                            _tool_row_identity(row, features), weight
                        )
                        tool_model = (
                            self.child_tool
                            if self.tool_feature_contract != "legacy"
                            and trigger_attrs["is_child"]
                            else self.tool
                        )
                        tool_model.observe(
                            _tool_feature_key(
                                role,
                                family,
                                {
                                    **features,
                                    "backend_class": backend,
                                    "command_class": (
                                        local_features.observed_command_class
                                        if self.tool_feature_contract != "legacy"
                                        else command
                                    ),
                                },
                            ),
                            status=status,
                            duration_ms=float(delay),
                            weight=tool_weight,
                        )
                        if not right_censored:
                            pooled_tool_terminal_samples.append(
                                (local_features, status, tool_weight)
                            )
                        observed["tool"] += 1
        pooled_summary = {
            "remaining_decode_tokens": self.pooled_decode_demand.fit(
                pooled_decode_samples
            ),
            "next_output_tokens": self.pooled_next_output.fit(
                pooled_output_samples
            ),
            "prompt_growth_tokens": self.pooled_prompt_growth.fit(
                pooled_prompt_samples
            ),
            "remaining_to_return_ms": self.pooled_child_completion.fit(
                pooled_child_completion_samples
            ),
        }
        pooled_classification_summary = {
            "boundary": self.pooled_boundary.fit(pooled_boundary_samples),
            "tool_terminal": self.pooled_tool_terminal.fit(
                pooled_tool_terminal_samples
            ),
        }
        action_weights = _action_target_weights(action_values)
        timing_samples: list[tuple[object, float, bool, float]] = []
        for target in action_values:
            features = _local_features_from_action_target(target)
            identity = _action_target_identity(target)
            known = [
                (action, value)
                for action, value in (target.get("actions") or {}).items()
                if bool(value.get("outcome_known"))
                and action in {"prepare_host", "prefetch_gpu"}
            ]
            if not known:
                continue
            weight = action_weights.get(identity, 0.0) / len(known)
            for action, value in known:
                favorable = bool(value.get("outcome"))
                release_within = (
                    favorable if action == "prefetch_gpu" else not favorable
                )
                timing_samples.append(
                    (
                        features,
                        float(value["operational_tau_ms"]),
                        release_within,
                        weight,
                    )
                )
        timing_summary = self.operational_release.fit(timing_samples)
        observed["operational_timing"] = len(timing_samples)
        self.training_summary = {
            "decision_point_count": len(values),
            "episode_count": len(episode_counts),
            "local_episode_count": len(local_episode_counts),
            "workflow_count": len(workflow_episode_counts),
            "split_counts": dict(sorted(split_counts.items())),
            "observation_counts": dict(sorted(observed.items())),
            "pooled_demand": pooled_summary,
            "pooled_classification": pooled_classification_summary,
            "operational_timing": timing_summary,
            "action_target_count": len(action_values),
            "episode_weighting": (
                "decision points are normalized within each local episode, then "
                "local episodes are normalized within each workflow rollout"
            ),
        }
        return dict(self.training_summary)

    def predict(self, features: LocalFrontierFeatures) -> LocalFrontierPrediction:
        key = _demand_feature_key(
            features.agent_definition_id,
            features.state,
            features.tool_family,
            {
                "current_sequence_tokens": features.current_sequence_tokens,
                "generated_tokens": features.generated_tokens,
                "backend_class": features.backend_class,
                "command_class": features.command_class,
            },
        )
        boundary, boundary_level, boundary_support = self.boundary.predict(
            role=features.agent_definition_id,
            state=features.state,
            history=features.boundary_history,
        )
        pooled_boundary = self.pooled_boundary.predict(features)
        if pooled_boundary and self.pooled_boundary.training_count >= 32:
            boundary = pooled_boundary
            boundary_level = "pooled"
        boundary = _temperature_scale(boundary, self.boundary_temperature)
        decode, decode_level = self.decode_demand.predict(key)
        output, output_level = self.next_output.predict(key)
        prompt, prompt_level = self.prompt_growth.predict(key)
        pooled_values, pooled_mass, pooled_support, pooled_level = (
            self.pooled_decode_demand.predict(features)
        )
        if (
            pooled_values
            and features.state == InvocationState.RUNNING_LLM.value
        ):
            decode = EmpiricalDistribution(
                pooled_values, pooled_mass, pooled_support
            )
            decode_level = pooled_level
        pooled_values, pooled_mass, pooled_support, pooled_level = (
            self.pooled_next_output.predict(features)
        )
        if pooled_values:
            output = EmpiricalDistribution(
                pooled_values, pooled_mass, pooled_support
            )
            output_level = pooled_level
        pooled_values, pooled_mass, pooled_support, pooled_level = (
            self.pooled_prompt_growth.predict(features)
        )
        if pooled_values:
            prompt = EmpiricalDistribution(
                pooled_values, pooled_mass, pooled_support
            )
            prompt_level = pooled_level
        child_values, child_mass, child_support, child_level = (
            self.pooled_child_completion.predict(features)
        )
        child_completion = (
            EmpiricalDistribution(child_values, child_mass, child_support)
            if child_values
            else EmpiricalDistribution.empty()
        )
        wait = EmpiricalDistribution.empty()
        terminal: Mapping[str, float] = {}
        wait_belief: WaitBelief
        if features.state == InvocationState.WAIT_TOOL.value:
            tool_model = (
                self.child_tool
                if self.tool_feature_contract != "legacy" and features.is_child
                else self.tool
            )
            terminal, wait, tool_level, tool_support_detail = tool_model.predict(
                _tool_feature_key(
                    features.agent_definition_id,
                    features.tool_family,
                    {
                        "current_sequence_tokens": features.current_sequence_tokens,
                        "active_tool_count": features.active_tool_count,
                        "backend_pressure": features.backend_pressure,
                        "backend_class": features.backend_class,
                        "command_class": (
                            features.observed_command_class
                            if self.tool_feature_contract != "legacy"
                            and features.observed_command_class != "unknown"
                            else features.command_class
                        ),
                    },
                ),
                elapsed_ms=features.elapsed_wait_ms,
            )
            terminal = _temperature_scale(terminal, self.tool_temperature)
            pooled_terminal = self.pooled_tool_terminal.predict(features)
            if (
                pooled_terminal
                and self.pooled_tool_terminal.training_count >= 32
            ):
                terminal = _temperature_scale(
                    pooled_terminal, self.tool_temperature
                )
            wait_belief = WaitBelief(
                kind=WaitBeliefKind.TOOL,
                residual_duration=wait,
                terminal_distribution=terminal,
                support_level=tool_level,
                support_detail=tool_support_detail,
                survival_logit_scale=self.tool_survival_logit_scale,
                survival_logit_offset=self.tool_survival_logit_offset,
                ood_reasons=(
                    ("tool_wait_unavailable",)
                    if tool_level == "unavailable"
                    else ()
                ),
            )
            operational_timing_curve = self.operational_release.curve(features)
            if operational_timing_curve is not None:
                tool_level = operational_timing_curve.support_level
        elif features.state == InvocationState.WAIT_JOIN.value:
            operational_timing_curve = None
            tool_level = "structural"
            wait_belief = WaitBelief(
                kind=WaitBeliefKind.JOIN,
                support_level="structural",
                dependency_composed=True,
            )
        elif features.state == InvocationState.WAIT_CHILD.value:
            operational_timing_curve = None
            tool_level = "structural"
            wait_belief = WaitBelief(
                kind=WaitBeliefKind.CHILD,
                support_level="structural",
                dependency_composed=True,
            )
        elif features.state == InvocationState.WAIT_MESSAGE.value:
            operational_timing_curve = None
            tool_level = "structural"
            wait_belief = WaitBelief(
                kind=WaitBeliefKind.MESSAGE,
                support_level="structural",
                dependency_composed=True,
            )
        else:
            operational_timing_curve = None
            tool_level = "unavailable"
            wait_belief = WaitBelief(kind=WaitBeliefKind.NONE)

        head_support = {
            "boundary": boundary_level,
            "remaining_decode_demand": decode_level,
            "next_output_demand": output_level,
            "prompt_growth": prompt_level,
            "tool_wait": (
                tool_level
                if features.state == InvocationState.WAIT_TOOL.value
                else "unavailable"
            ),
            "join_dependency": (
                "structural"
                if features.state
                in {
                    InvocationState.WAIT_JOIN.value,
                    InvocationState.WAIT_CHILD.value,
                }
                else "unavailable"
            ),
            "message_dependency": (
                "structural"
                if features.state == InvocationState.WAIT_MESSAGE.value
                else "unavailable"
            ),
            "child_completion": child_level,
        }
        relevant_heads = _required_prediction_heads_for_state(
            features.state, is_child=features.is_child
        )
        relevant_levels = tuple(head_support[name] for name in relevant_heads)
        unavailable = [
            name
            for name in relevant_heads
            if head_support.get(name) == "unavailable"
        ]
        if unavailable:
            support_level = "unavailable"
        elif relevant_levels and all(
            level in {"exact", "structural"} for level in relevant_levels
        ):
            support_level = "exact"
        else:
            support_level = "backoff"
        del boundary_support
        intervals = {
            name: _calibrated_interval(
                distribution,
                target_coverage=self.calibration_coverage,
                slack=self.interval_slack.get(name, 0.0),
            )
            for name, distribution in (
                ("remaining_decode_tokens", decode),
                ("prompt_growth_tokens", prompt),
                ("next_output_tokens", output),
                ("remaining_to_return_ms", child_completion),
            )
            if distribution.values and self.calibration_coverage > 0
        }
        return LocalFrontierPrediction(
            invocation_id=features.invocation_id,
            boundary_distribution=boundary,
            current_sequence_tokens=features.current_sequence_tokens,
            remaining_decode_tokens=decode,
            remaining_external_wait=wait,
            tool_terminal_distribution=terminal,
            prompt_growth_tokens=prompt,
            next_output_tokens=output,
            support_level=support_level,
            calibration_coverage=self.calibration_coverage,
            remaining_to_return_ms=child_completion,
            ood_reasons=tuple(f"{item}_unavailable" for item in unavailable),
            calibrated_intervals=intervals,
            wait_belief=wait_belief,
            head_support=head_support,
            action_timing_calibration=self.action_timing_calibration,
            operational_timing_curve=operational_timing_curve,
        )

    def calibrate(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        target_coverage: float = 0.9,
        allow_development: bool = False,
        action_targets: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Calibrate probabilities and intervals without refitting train counts."""

        if not 0 < target_coverage < 1:
            raise ValueError("target coverage must be between zero and one")
        if self.calibration_summary:
            raise ValueError("model is already calibrated")
        values = [dict(row) for row in rows]
        action_values = [dict(item) for item in action_targets]
        if not values:
            raise ValueError("calibration requires decision points")
        _validate_demand_rows(values)
        splits = {str(row.get("split") or "unknown") for row in values}
        if allow_development:
            if not splits.issubset({"calibration", "train", "development"}):
                raise ValueError(
                    "development calibration may only consume "
                    "calibration, train, or development rows"
                )
        elif splits != {"calibration"}:
            raise ValueError("calibration may consume only the calibration split")
        action_splits = {
            str(row.get("split") or "unknown") for row in action_values
        }
        allowed_action_splits = (
            {"calibration", "train", "development"}
            if allow_development
            else {"calibration"}
        )
        if action_values and not action_splits.issubset(allowed_action_splits):
            raise ValueError("action targets do not match the calibration split")

        episode_counts = Counter(
            str(row.get("episode_group_id") or row.get("decision_id"))
            for row in values
        )
        local_episode_counts = _local_episode_counts(values)
        workflow_episode_counts = _workflow_local_episode_counts(values)
        tool_weights = _tool_fit_weights(
            values, trigger_only=self.tool_feature_contract != "legacy"
        )
        boundary_records: list[tuple[Mapping[str, float], str, float]] = []
        tool_records: list[tuple[Mapping[str, float], str, float]] = []
        tool_survival_records: list[tuple[float, bool, float]] = []
        action_timing_records: defaultdict[
            str, list[tuple[float, bool, float]]
        ] = defaultdict(list)
        scores: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        observation_counts: Counter[str] = Counter()
        for row in values:
            episode = str(row.get("episode_group_id") or row.get("decision_id"))
            trigger = str(row.get("trigger_kind") or "")
            labels = {
                str(item.get("invocation_id")): item
                for item in row.get("labels", ())
            }
            for raw_features in row.get("invocations", ()):
                invocation_id = str(raw_features.get("invocation_id") or "")
                label = labels.get(invocation_id)
                if label is None:
                    continue
                local_episode = f"{episode}|{invocation_id}"
                weight = 1.0 / max(
                    1, local_episode_counts[(episode, invocation_id)]
                )
                workflow = _workflow_group_id(row)
                weight /= max(1, workflow_episode_counts[workflow])
                features = _local_features_from_row(
                    row, raw_features,
                    tool_feature_contract=self.tool_feature_contract,
                )
                prediction = self.predict(features)
                boundary = _normalize_boundary(label.get("next_boundary_kind"))
                if (
                    features.state == InvocationState.RUNNING_LLM.value
                    and boundary is not None
                    and _target_eligible(label, "action_boundary")
                ):
                    boundary_records.append(
                        (prediction.boundary_distribution, boundary, weight)
                    )
                    observation_counts["boundary"] += 1
                if (
                    trigger == RuntimeEventKind.TOOL_START.value
                    and features.state == InvocationState.WAIT_TOOL.value
                    and _target_eligible(label, "external_wait")
                    and (
                        self.tool_feature_contract == "legacy"
                        or row.get("trigger_invocation_id") == invocation_id
                    )
                ):
                    if _target_right_censored(label, "external_wait"):
                        observation_counts[
                            "tool_right_censored_excluded_from_terminal"
                        ] += 1
                    else:
                        status = str(
                            label.get("next_boundary_status") or "error"
                        )
                        tool_records.append(
                            (
                                prediction.tool_terminal_distribution,
                                status,
                                tool_weights.get(
                                    _tool_row_identity(row, raw_features), weight
                                ),
                            )
                        )
                        observation_counts["tool_terminal"] += 1
                wait_target = (
                    "external_wait"
                    if features.state == InvocationState.WAIT_TOOL.value
                    else None
                )
                scalar_targets = (
                    (
                        "remaining_decode_tokens",
                        label.get("remaining_output_tokens")
                        if features.state == InvocationState.RUNNING_LLM.value
                        and _target_eligible(label, "remaining_decode_demand")
                        else None,
                        prediction.remaining_decode_tokens,
                    ),
                    (
                        "prompt_growth_tokens",
                        label.get("reentry_prompt_delta_tokens")
                        if _target_eligible(label, "prompt_growth")
                        else None,
                        prediction.prompt_growth_tokens,
                    ),
                    (
                        "next_output_tokens",
                        label.get("next_output_tokens")
                        if _target_eligible(label, "next_output_demand")
                        else None,
                        prediction.next_output_tokens,
                    ),
                    (
                        "remaining_to_return_ms",
                        label.get("remaining_to_return_ms")
                        if _target_eligible(label, "child_completion")
                        else None,
                        prediction.remaining_to_return_ms,
                    ),
                )
                for name, actual, distribution in scalar_targets:
                    if actual is None or not distribution.values:
                        continue
                    lower, upper = _raw_interval(distribution, target_coverage)
                    score = max(lower - float(actual), float(actual) - upper, 0.0)
                    score_episode = (
                        f"{workflow}|child:{invocation_id}"
                        if name == "remaining_to_return_ms"
                        else local_episode
                    )
                    scores[name][score_episode].append(score)
                    observation_counts[name] += 1
                if (
                    wait_target
                    and _target_right_censored(label, wait_target)
                ):
                    observation_counts[
                        f"{wait_target}_right_censored_excluded_from_interval"
                    ] += 1

        # Always calibrate the current model's raw action probability. This also
        # makes recalibration idempotent when loading an already calibrated model.
        self.action_timing_calibration = {}
        action_weights = _action_target_weights(action_values)
        for target in action_values:
            features = _local_features_from_action_target(target)
            prediction = self.predict(features)
            identity = _action_target_identity(target)
            known = [
                (name, value)
                for name, value in (target.get("actions") or {}).items()
                if bool(value.get("outcome_known"))
            ]
            weight = action_weights.get(identity, 0.0)
            for action, value in known:
                tau_ms = float(value["operational_tau_ms"])
                timing = prediction.action_timing(action, tau_ms)
                if timing is None:
                    continue
                if action == "prepare_host":
                    probability = timing.favorable_probability
                    outcome = bool(value["outcome"])
                    survival_probability = probability
                    survival_outcome = outcome
                elif action == "prefetch_gpu":
                    probability = timing.favorable_probability
                    outcome = bool(value["outcome"])
                    survival_probability = 1.0 - probability
                    survival_outcome = not outcome
                else:
                    continue
                action_timing_records[action].append(
                    (probability, outcome, weight)
                )
                tool_survival_records.append(
                    (survival_probability, survival_outcome, weight)
                )
                observation_counts[f"{action}_operational_tau"] += 1
                observation_counts["tool_wait_action_slack"] += 1

        self.boundary_temperature = _fit_temperature(boundary_records)
        self.tool_temperature = _fit_temperature(tool_records)
        (
            self.tool_survival_logit_scale,
            self.tool_survival_logit_offset,
        ) = _fit_binary_logit_calibration(tool_survival_records)
        self.action_timing_calibration = {}
        for action, records in sorted(action_timing_records.items()):
            scale, offset = _fit_binary_brier_calibration(records)
            self.action_timing_calibration[action] = {
                "logit_scale": scale,
                "logit_offset": offset,
                **_binary_probability_metrics(
                    records, scale=scale, offset=offset
                ),
            }
        self.interval_slack = {
            name: _finite_sample_quantile(
                [max(items) for items in by_episode.values()],
                target_coverage,
            )
            for name, by_episode in scores.items()
            if by_episode
        }
        self.interval_slack.pop("remaining_external_wait_ms", None)
        self.calibration_coverage = target_coverage
        self.calibration_summary = {
            "split": (
                "development_train"
                if allow_development
                else "calibration"
            ),
            "decision_point_count": len(values),
            "episode_count": len(episode_counts),
            "local_episode_count": len(local_episode_counts),
            "target_coverage": target_coverage,
            "boundary_temperature": self.boundary_temperature,
            "tool_temperature": self.tool_temperature,
            "tool_survival_logit_scale": self.tool_survival_logit_scale,
            "tool_survival_logit_offset": self.tool_survival_logit_offset,
            "action_timing_calibration": self.action_timing_calibration,
            "interval_slack": dict(sorted(self.interval_slack.items())),
            "observation_counts": dict(sorted(observation_counts.items())),
            "conformal_unit": "episode_max_nonconformity",
            "training_counts_refit": False,
            "action_target_schema_version": 4 if action_values else None,
            "action_target_count": len(action_values),
        }
        return dict(self.calibration_summary)

    def to_dict(self, *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {
            "schema_version": STRUCTURED_FRONTIER_SCHEMA_VERSION,
            "model_kind": "pooled_action_conditional_particle_frontier",
            "model_version": self.model_version,
            "tool_feature_contract": self.tool_feature_contract,
            "decision_authority": "none; ScenarioRiskPlanner owns actions",
            "join_semantics": "not learned; RCCG composer applies ALL/ANY",
            "hyperparameters": self.hyperparameters.to_dict(),
            "training_summary": self.training_summary,
            "calibration_summary": self.calibration_summary,
            "calibration_coverage": self.calibration_coverage,
            "boundary_temperature": self.boundary_temperature,
            "tool_temperature": self.tool_temperature,
            "tool_survival_logit_scale": self.tool_survival_logit_scale,
            "tool_survival_logit_offset": self.tool_survival_logit_offset,
            "action_timing_calibration": {
                action: dict(sorted(quality.items()))
                for action, quality in sorted(
                    self.action_timing_calibration.items()
                )
            },
            "interval_slack": dict(sorted(self.interval_slack.items())),
            "metadata": dict(metadata or {}),
            "components": {
                "boundary": self.boundary.to_dict(),
                "decode_demand": self.decode_demand.to_dict(),
                "next_output": self.next_output.to_dict(),
                "prompt_growth": self.prompt_growth.to_dict(),
                "pooled_decode_demand": self.pooled_decode_demand.to_dict(),
                "pooled_next_output": self.pooled_next_output.to_dict(),
                "pooled_prompt_growth": self.pooled_prompt_growth.to_dict(),
                "pooled_child_completion": (
                    self.pooled_child_completion.to_dict()
                ),
                "pooled_boundary": self.pooled_boundary.to_dict(),
                "pooled_tool_terminal": self.pooled_tool_terminal.to_dict(),
                "tool": self.tool.to_dict(),
                "child_tool": self.child_tool.to_dict(),
                "operational_release": self.operational_release.to_dict(),
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FrontierBeliefModel":
        schema_version = int(raw.get("schema_version", -1))
        if schema_version not in SUPPORTED_STRUCTURED_FRONTIER_SCHEMA_VERSIONS:
            raise ValueError("unsupported structured frontier model schema")
        model = cls(
            model_version=str(raw.get("model_version") or "unknown"),
            hyperparameters=FrontierModelHyperparameters.from_dict(
                raw.get("hyperparameters")
            ),
            tool_feature_contract=str(raw.get("tool_feature_contract") or "legacy"),
        )
        if schema_version < 7 and model.tool_feature_contract != "legacy":
            raise ValueError("older model schema cannot use observed tool features")
        components = raw.get("components", {})
        model.boundary = _BoundaryContextTree.from_dict(components.get("boundary", {}))
        model.decode_demand = _HierarchicalEmpiricalModel.from_dict(
            components.get("decode_demand", {})
        )
        model.next_output = _HierarchicalEmpiricalModel.from_dict(
            components.get("next_output", {})
        )
        model.prompt_growth = _HierarchicalEmpiricalModel.from_dict(components.get("prompt_growth", {}))
        model.pooled_decode_demand = PooledConditionalDemandModel.from_dict(
            components.get("pooled_decode_demand", {})
        )
        model.pooled_next_output = PooledConditionalDemandModel.from_dict(
            components.get("pooled_next_output", {})
        )
        model.pooled_prompt_growth = PooledConditionalDemandModel.from_dict(
            components.get("pooled_prompt_growth", {})
        )
        model.pooled_child_completion = PooledConditionalDemandModel.from_dict(
            components.get("pooled_child_completion", {})
        )
        model.pooled_boundary = PooledConditionalClassifier.from_dict(
            components.get("pooled_boundary", {})
        )
        model.pooled_tool_terminal = PooledConditionalClassifier.from_dict(
            components.get("pooled_tool_terminal", {})
        )
        model.tool = _CompetingRiskToolModel.from_dict(components.get("tool", {}))
        model.child_tool = _CompetingRiskToolModel.from_dict(
            components.get("child_tool", {})
        )
        model.operational_release = OperationalReleaseModel.from_dict(
            components.get("operational_release", {})
        )
        model.training_summary = dict(raw.get("training_summary", {}))
        model.calibration_summary = dict(raw.get("calibration_summary", {}))
        model.calibration_coverage = float(raw.get("calibration_coverage", 0.0))
        model.boundary_temperature = float(raw.get("boundary_temperature", 1.0))
        model.tool_temperature = float(raw.get("tool_temperature", 1.0))
        model.tool_survival_logit_scale = float(
            raw.get("tool_survival_logit_scale", 1.0)
        )
        model.tool_survival_logit_offset = float(
            raw.get("tool_survival_logit_offset", 0.0)
        )
        model.action_timing_calibration = {
            str(action): {
                str(name): float(value)
                for name, value in quality.items()
            }
            for action, quality in raw.get(
                "action_timing_calibration", {}
            ).items()
        }
        model.interval_slack = {
            str(key): float(value)
            for key, value in raw.get("interval_slack", {}).items()
        }
        model.artifact_metadata = dict(raw.get("metadata", {}))
        return model

    def save(self, path: str | Path, *, metadata: Mapping[str, Any] | None = None) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict(metadata=metadata)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(destination)

    @classmethod
    def load(cls, path: str | Path) -> "FrontierBeliefModel":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


class FrontierScenarioComposer:
    """Construct joint particles from local marginals and deterministic RCCG structure."""

    def __init__(
        self,
        *,
        particle_count: int = 128,
        top_k: int = 8,
        shared_episode_probability: float = 0.35,
        local_particle_cache_entries: int = 4096,
    ) -> None:
        if particle_count <= 0 or top_k <= 0:
            raise ValueError("particle count and top-k must be positive")
        if not 0 <= shared_episode_probability <= 1:
            raise ValueError("shared episode probability must be in [0, 1]")
        self.particle_count = particle_count
        self.top_k = top_k
        self.shared_episode_probability = shared_episode_probability
        self.local_particle_cache_entries = max(0, local_particle_cache_entries)
        self._local_particle_cache: OrderedDict[
            tuple[object, ...], tuple[FrontierDemandOutcome, ...]
        ] = OrderedDict()
        self._quantile_cache: OrderedDict[
            tuple[int, str], tuple[tuple[float, float], ...]
        ] = OrderedDict()
        self._stratified_quantiles = tuple(
            (index + 0.5) / particle_count for index in range(particle_count)
        )
        self.local_particle_cache_hits = 0
        self.local_particle_cache_misses = 0

    def local_particle_cache_stats(self) -> tuple[int, int, int]:
        return (
            self.local_particle_cache_hits,
            self.local_particle_cache_misses,
            len(self._local_particle_cache),
        )

    def compose(
        self,
        *,
        graph: RuntimeCausalContextGraph,
        scope: BeliefScope,
        local_predictions: Mapping[str, LocalFrontierPrediction],
        generated_ts_ms: float,
        evidence_read_set: PredictiveEvidenceReadSet,
        seed: int = 0,
        horizon: FinitePlanningHorizon = FinitePlanningHorizon(),
        projection: ScenarioProjection = ScenarioProjection.FULL,
        target_invocation_id: str | None = None,
    ) -> FrontierBeliefSnapshot:
        particles = self.sample_particles(
            graph=graph,
            scope=scope,
            local_predictions=local_predictions,
            seed=seed,
        )
        return self.reduce_particles(
            particles=particles,
            scope=scope,
            local_predictions=local_predictions,
            generated_ts_ms=generated_ts_ms,
            evidence_read_set=evidence_read_set,
            horizon=horizon,
            projection=projection,
            target_invocation_id=target_invocation_id,
        )

    def sample_particles(
        self,
        *,
        graph: RuntimeCausalContextGraph,
        scope: BeliefScope,
        local_predictions: Mapping[str, LocalFrontierPrediction],
        seed: int = 0,
    ) -> tuple[tuple[FrontierDemandOutcome, ...], ...]:
        missing = set(scope.invocation_ids).difference(local_predictions)
        if missing:
            raise ValueError(f"local predictions missing scoped invocations: {sorted(missing)}")
        ordered_ids = tuple(sorted(scope.invocation_ids))
        local_particles: dict[str, tuple[FrontierDemandOutcome, ...]] = {}
        for invocation_id in ordered_ids:
            prediction = local_predictions[invocation_id]
            local_key = self._local_particle_key(
                graph,
                invocation_id,
                prediction,
                seed=seed,
            )
            cached = self._local_particle_cache.get(local_key)
            if cached is not None:
                self._local_particle_cache.move_to_end(local_key)
                self.local_particle_cache_hits += 1
                local_particles[invocation_id] = cached
                continue
            self.local_particle_cache_misses += 1
            quantiles = self._invocation_quantiles(seed, invocation_id)
            outcomes = tuple(
                self._sample_local(
                    graph,
                    invocation_id,
                    prediction,
                    quantile,
                    category_quantile,
                )
                for quantile, category_quantile in quantiles
            )
            local_particles[invocation_id] = outcomes
            if self.local_particle_cache_entries:
                self._local_particle_cache[local_key] = outcomes
                self._local_particle_cache.move_to_end(local_key)
                while (
                    len(self._local_particle_cache)
                    > self.local_particle_cache_entries
                ):
                    self._local_particle_cache.popitem(last=False)
        return tuple(
            tuple(
                local_particles[invocation_id][particle_index]
                for invocation_id in ordered_ids
            )
            for particle_index in range(self.particle_count)
        )

    def _invocation_quantiles(
        self,
        seed: int,
        invocation_id: str,
    ) -> tuple[tuple[float, float], ...]:
        key = (seed, invocation_id)
        cached = self._quantile_cache.get(key)
        if cached is not None:
            self._quantile_cache.move_to_end(key)
            return cached
        digest = hashlib.blake2b(
            repr(key).encode(),
            digest_size=24,
            person=b"bkv-particle",
        ).digest()
        values = tuple(
            int.from_bytes(digest[offset : offset + 4], "big")
            for offset in range(0, len(digest), 4)
        )

        def normalized_stride(stride_seed: int) -> int:
            stride = stride_seed % self.particle_count
            if stride == 0:
                stride = 1
            while math.gcd(stride, self.particle_count) != 1:
                stride = (stride + 1) % self.particle_count or 1
            return stride

        selector_offset = values[0] % self.particle_count
        selector_stride = normalized_stride(values[1])
        local_offset = values[2] % self.particle_count
        local_stride = normalized_stride(values[3])
        category_offset = values[4] % self.particle_count
        category_stride = normalized_stride(values[5])

        quantiles = tuple(
            (
                self._stratified_quantiles[index]
                if self._stratified_quantiles[
                    (selector_offset + index * selector_stride)
                    % self.particle_count
                ]
                < self.shared_episode_probability
                else self._stratified_quantiles[
                    (local_offset + index * local_stride)
                    % self.particle_count
                ],
                self._stratified_quantiles[
                    (category_offset + index * category_stride)
                    % self.particle_count
                ],
            )
            for index in range(self.particle_count)
        )
        if self.local_particle_cache_entries:
            self._quantile_cache[key] = quantiles
            self._quantile_cache.move_to_end(key)
            while len(self._quantile_cache) > self.local_particle_cache_entries:
                self._quantile_cache.popitem(last=False)
        return quantiles

    @staticmethod
    def _local_particle_key(
        graph: RuntimeCausalContextGraph,
        invocation_id: str,
        prediction: LocalFrontierPrediction,
        *,
        seed: int,
    ) -> tuple[object, ...]:
        invocation = graph.invocations[invocation_id]
        join = (
            graph.joins.get(invocation.join_id)
            if invocation.join_id is not None
            else None
        )
        return (
            seed,
            invocation_id,
            invocation.state.value,
            invocation.active_tool_family,
            tuple(sorted(invocation.blocking_child_ids)),
            invocation.join_id,
            (
                (
                    join.mode.value,
                    tuple(sorted(join.member_invocation_ids)),
                )
                if join is not None
                else None
            ),
            tuple(
                sorted(
                    edge.target_invocation_id
                    for edge in graph.communication_edges.values()
                    if edge.source_invocation_id == invocation_id
                    and edge.target_invocation_id in graph.invocations
                )
            ),
            FrontierScenarioComposer.prediction_particle_key(prediction),
        )

    @staticmethod
    def prediction_particle_key(
        prediction: LocalFrontierPrediction,
    ) -> tuple[object, ...]:
        """Return exactly the prediction fields consumed by local sampling."""

        wait = prediction.wait_belief
        assert wait is not None
        return (
            tuple(sorted(prediction.boundary_distribution.items())),
            prediction.current_sequence_tokens,
            prediction.remaining_decode_tokens,
            prediction.prompt_growth_tokens,
            prediction.next_output_tokens,
            (
                wait.kind.value,
                wait.residual_duration,
                tuple(sorted(wait.terminal_distribution.items())),
            ),
            prediction.remaining_to_return_ms,
        )

    def reduce_particles(
        self,
        *,
        particles: tuple[tuple[FrontierDemandOutcome, ...], ...],
        scope: BeliefScope,
        local_predictions: Mapping[str, LocalFrontierPrediction],
        generated_ts_ms: float,
        evidence_read_set: PredictiveEvidenceReadSet,
        horizon: FinitePlanningHorizon = FinitePlanningHorizon(),
        projection: ScenarioProjection = ScenarioProjection.FULL,
        target_invocation_id: str | None = None,
    ) -> FrontierBeliefSnapshot:
        projection = ScenarioProjection(projection)
        if not particles:
            raise ValueError("scenario reduction requires sampled particles")
        if projection != ScenarioProjection.FULL and (
            target_invocation_id not in scope.invocation_ids
        ):
            raise ValueError("action-projected reduction requires a scoped target")

        if projection == ScenarioProjection.FULL:
            scenarios, residual_mass = self._reduce_full_particles(particles)
        else:
            scenarios = self._reduce_action_projected_particles(
                particles,
                projection=projection,
                target_invocation_id=str(target_invocation_id),
            )
            residual_mass = 0.0

        selected_mass = sum(item.probability_mass for item in scenarios)
        ood = sorted(
            {
                reason
                for prediction in local_predictions.values()
                for reason in prediction.ood_reasons
            }
        )
        support = (
            "unavailable"
            if all(item.support_level == "unavailable" for item in local_predictions.values())
            else "backoff"
            if any(item.support_level != "exact" for item in local_predictions.values())
            else "exact"
        )
        digest = hashlib.blake2b(
            (
                f"{scope.scope_id}|{generated_ts_ms}|"
                f"{evidence_read_set.model_version}|{projection.value}|"
                f"{target_invocation_id or ''}"
            ).encode(),
            digest_size=16,
            person=b"bkv-frontier",
        ).hexdigest()
        return FrontierBeliefSnapshot(
            belief_id=f"frontier-{digest}",
            generated_ts_ms=generated_ts_ms,
            scope=scope,
            scenarios=scenarios,
            other_probability_mass=max(0.0, 1.0 - selected_mass)
            if projection == ScenarioProjection.FULL
            else residual_mass,
            calibration_coverage=min(
                (item.calibration_coverage for item in local_predictions.values()),
                default=0.0,
            ),
            support_level=support,
            ood_reasons=tuple(ood),
            evidence_read_set=evidence_read_set,
            horizon=horizon,
            other_policy=OtherResidualPolicy(
                finite_risk_bound=(projection != ScenarioProjection.FULL or not ood)
            ),
        )

    def _reduce_full_particles(
        self,
        particles: tuple[tuple[FrontierDemandOutcome, ...], ...],
    ) -> tuple[tuple[DemandScenario, ...], float]:
        counts: Counter[tuple[Any, ...]] = Counter()
        representatives: dict[tuple[Any, ...], tuple[FrontierDemandOutcome, ...]] = {}
        for outcomes in particles:
            key = _scenario_key(outcomes)
            counts[key] += 1
            representatives.setdefault(key, outcomes)
        ranked = sorted(counts, key=lambda key: (-counts[key], key))
        selected = ranked[: self.top_k]
        scenarios = tuple(
            DemandScenario(
                scenario_id=f"scenario-{index:03d}-{hashlib.blake2b(repr(key).encode(), digest_size=8).hexdigest()}",
                outcomes=representatives[key],
                probability_mass=counts[key] / len(particles),
            )
            for index, key in enumerate(selected)
        )
        return scenarios, max(
            0.0, 1.0 - sum(item.probability_mass for item in scenarios)
        )

    def _reduce_action_projected_particles(
        self,
        particles: tuple[tuple[FrontierDemandOutcome, ...], ...],
        *,
        projection: ScenarioProjection,
        target_invocation_id: str,
    ) -> tuple[DemandScenario, ...]:
        vectors = tuple(
            _action_projection_vector(
                outcomes,
                projection=projection,
                target_invocation_id=target_invocation_id,
            )
            for outcomes in particles
        )
        clusters = _deterministic_medoid_clusters(vectors, self.top_k)
        scenarios: list[DemandScenario] = []
        for index, (medoid_index, member_indices) in enumerate(clusters):
            medoid = particles[medoid_index]
            members = tuple(particles[item] for item in member_indices)
            conservative = _conservative_cluster_outcomes(medoid, members)
            identity = (
                projection.value,
                target_invocation_id,
                _scenario_key(medoid),
                tuple(member_indices),
            )
            scenarios.append(
                DemandScenario(
                    scenario_id=(
                        f"{projection.value}-cluster-{index:03d}-"
                        f"{hashlib.blake2b(repr(identity).encode(), digest_size=8).hexdigest()}"
                    ),
                    outcomes=medoid,
                    conservative_outcomes=conservative,
                    probability_mass=len(member_indices) / len(particles),
                    projection=projection,
                )
            )
        return tuple(scenarios)

    @staticmethod
    def _sample_local(
        graph: RuntimeCausalContextGraph,
        invocation_id: str,
        prediction: LocalFrontierPrediction,
        quantile: float,
        categorical_quantile: float,
    ) -> FrontierDemandOutcome:
        invocation = graph.invocations[invocation_id]
        boundary = BoundaryEvent(_sample_categorical(prediction.boundary_distribution, categorical_quantile))
        dependency = DependencyMode.NONE
        dependencies: tuple[str, ...] = ()
        join_id: str | None = None
        external_segments: tuple[ExternalDemandSegment, ...] = ()
        phase = (
            DemandPhase.DECODE
            if invocation.state == InvocationState.RUNNING_LLM
            else DemandPhase.PREFILL
            if invocation.state == InvocationState.READY
            else DemandPhase.EXTERNAL
        )
        if invocation.state == InvocationState.WAIT_TOOL:
            dependency = DependencyMode.EXTERNAL
            boundary = BoundaryEvent.TOOL
            tool_wait = prediction.wait_belief
            if tool_wait.kind != WaitBeliefKind.TOOL:
                tool_wait = WaitBelief(kind=WaitBeliefKind.UNKNOWN)
            external_segments = (
                ExternalDemandSegment(
                    segment_kind="tool",
                    service_family=invocation.active_tool_family or "unknown",
                    residual_delay_ms=tool_wait.residual_duration.sample(quantile),
                    terminal_status=_sample_raw_category(
                        tool_wait.terminal_distribution,
                        categorical_quantile,
                        default="censored",
                    ),
                ),
            )
        elif invocation.state == InvocationState.WAIT_CHILD:
            dependency = DependencyMode.JOIN_ALL
            dependencies = tuple(invocation.blocking_child_ids)
        elif invocation.state == InvocationState.WAIT_JOIN:
            join = graph.joins.get(invocation.join_id or "")
            dependency = (
                DependencyMode.JOIN_ANY
                if join is not None and join.mode == JoinMode.ANY
                else DependencyMode.JOIN_ALL
            )
            dependencies = tuple(join.member_invocation_ids) if join is not None else ()
            join_id = invocation.join_id
            boundary = BoundaryEvent.UNKNOWN
        elif invocation.state == InvocationState.WAIT_MESSAGE:
            dependency = DependencyMode.PRODUCER
            dependencies = tuple(
                sorted(
                    {
                        edge.target_invocation_id
                        for edge in graph.communication_edges.values()
                        if edge.source_invocation_id == invocation_id
                        and edge.target_invocation_id in graph.invocations
                    }
                )
            )
        return FrontierDemandOutcome(
            invocation_id=invocation_id,
            boundary_event=boundary,
            dependency_mode=dependency,
            phase=phase,
            current_sequence_tokens=prediction.current_sequence_tokens,
            remaining_decode_tokens=int(
                round(prediction.remaining_decode_tokens.sample(quantile))
            ),
            prompt_growth_tokens=int(
                round(prediction.prompt_growth_tokens.sample(quantile))
            ),
            next_output_tokens=int(
                round(prediction.next_output_tokens.sample(quantile))
            ),
            completion_floor_ms=(
                prediction.remaining_to_return_ms.sample(quantile)
                if invocation.parent_invocation_id is not None
                else 0.0
            ),
            external_segments=external_segments,
            dependency_invocation_ids=dependencies,
            join_id=join_id,
        )


def _child_return_targets(
    root: Path,
) -> dict[str, tuple[float, float | None]]:
    path = root / "reentries.jsonl"
    if not path.is_file():
        return {}
    targets: dict[str, tuple[float, float | None]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if (
            row.get("reentry_kind") != "join"
            or row.get("training_eligible") is not True
            or row.get("terminal_status") != "satisfied"
        ):
            continue
        for member in row.get("member_outcomes") or ():
            invocation_id = str(member.get("invocation_id") or "")
            raw_return_ts = member.get("return_ts_ms")
            if not invocation_id or raw_return_ts is None:
                continue
            return_ts = float(raw_return_ts)
            raw_start_ts = member.get("start_ts_ms")
            start_ts = (
                float(raw_start_ts) if raw_start_ts is not None else None
            )
            if not math.isfinite(return_ts):
                raise ValueError(f"non-finite child RETURN timestamp: {root}")
            if start_ts is not None and (
                not math.isfinite(start_ts) or start_ts > return_ts
            ):
                raise ValueError(f"invalid child start timestamp: {root}")
            previous = targets.get(invocation_id)
            if previous is not None and not math.isclose(
                previous[0], return_ts, abs_tol=1e-6
            ):
                raise ValueError(
                    "conflicting child RETURN timestamps for "
                    f"{invocation_id}: {root}"
                )
            targets[invocation_id] = (return_ts, start_ts)
    return targets


def _attach_child_completion_targets(
    row: dict[str, Any],
    return_ts_by_invocation: Mapping[str, tuple[float, float | None]],
) -> dict[str, Any]:
    raw_ts = row.get("timestamp_ms", row.get("ts_ms"))
    if raw_ts is None or not return_ts_by_invocation:
        return row
    timestamp_ms = float(raw_ts)
    labels = []
    changed = False
    for raw_label in row.get("labels", ()):
        label = dict(raw_label)
        invocation_id = str(label.get("invocation_id") or "")
        target = return_ts_by_invocation.get(invocation_id)
        if target is not None and target[0] >= timestamp_ms:
            return_ts, _ = target
            label["remaining_to_return_ms"] = return_ts - timestamp_ms
            eligibility = dict(label.get("target_training_eligible") or {})
            eligibility["child_completion"] = True
            label["target_training_eligible"] = eligibility
            horizons = dict(label.get("target_horizon_timestamp_ms") or {})
            horizons["child_completion"] = return_ts
            label["target_horizon_timestamp_ms"] = horizons
            changed = True
        labels.append(label)
    if changed:
        row = dict(row)
        row["labels"] = labels
        invocations = []
        for raw_features in row.get("invocations", ()):
            features = dict(raw_features)
            target = return_ts_by_invocation.get(
                str(features.get("invocation_id") or "")
            )
            if target is not None and target[1] is not None:
                features["invocation_elapsed_ms"] = max(
                    0.0, timestamp_ms - target[1]
                )
            if target is not None:
                features["is_child"] = True
            invocations.append(features)
        row["invocations"] = invocations
    return row


def load_decision_rows(
    dataset_dirs: Iterable[str | Path], *, allowed_splits: Iterable[str],
    allow_formal_local: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed = frozenset(allowed_splits)
    if allowed.intersection({"calibration", "test_id", "test_ood"}):
        raise ValueError("model fitting cannot consume calibration or test splits")
    rows: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    seen_runs: set[str] = set()
    seen_decisions: set[str] = set()
    for directory in dataset_dirs:
        root = Path(directory)
        manifest = json.loads((root / "dataset_manifest.json").read_text(encoding="utf-8"))
        local_eligible = manifest.get(
            "formal_local_training_eligible",
            manifest.get("formal_training_eligible"),
        )
        if "train" in allowed and local_eligible is not True:
            raise ValueError(f"formal training input is ineligible: {root}")
        if "train" in allowed:
            _validate_formal_p6_manifest(
                root, manifest, expected_split="train",
                allow_formal_local=allow_formal_local,
            )
        run_id = str(manifest.get("source", {}).get("run_id") or "")
        if not run_id:
            raise ValueError(f"dataset has no source run_id: {root}")
        if run_id in seen_runs:
            raise ValueError(f"duplicate source run in fitting inputs: {run_id}")
        seen_runs.add(run_id)
        manifests.append(manifest)
        child_return_targets = _child_return_targets(root)
        for line in (root / "frontier_decision_points.jsonl").read_text(encoding="utf-8").splitlines():
            row = _attach_child_completion_targets(
                json.loads(line), child_return_targets
            )
            if str(row.get("split")) in allowed and row.get("training_eligible") is not False:
                decision_id = str(row.get("decision_id") or "")
                if not decision_id:
                    raise ValueError(f"decision point has no identity: {root}")
                if decision_id in seen_decisions:
                    raise ValueError(f"duplicate decision point: {decision_id}")
                seen_decisions.add(decision_id)
                rows.append(row)
    _validate_demand_rows(rows)
    return rows, manifests


def summarize_training_corpus(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize independent sampling units, not scheduler decision rows."""

    values = [dict(row) for row in rows]
    projects = {
        str(row.get("project"))
        for row in values
        if row.get("project") not in {None, "", "unknown"}
    }
    tasks = {
        (
            str(row.get("project") or "unknown"),
            str(row.get("instance_id") or "unknown"),
            str(row.get("base_commit") or "unknown"),
        )
        for row in values
    }
    workflows = {_workflow_group_id(row) for row in values}
    runs = {
        str(row.get("run_id"))
        for row in values
        if row.get("run_id") not in {None, ""}
    }
    return {
        "decision_point_count": len(values),
        "project_count": len(projects),
        "projects": sorted(projects),
        "task_count": len(tasks),
        "workflow_count": len(workflows),
        "run_count": len(runs),
    }


def validate_training_corpus_diversity(
    rows: Iterable[Mapping[str, Any]],
    *,
    minimum_projects: int = 5,
    minimum_tasks: int = 40,
    minimum_workflows: int = 40,
) -> dict[str, Any]:
    """Reject a formally fitted model dominated by a fixed small workflow set."""

    if min(minimum_projects, minimum_tasks, minimum_workflows) <= 0:
        raise ValueError("training diversity thresholds must be positive")
    summary = summarize_training_corpus(rows)
    failures = []
    for field, minimum in (
        ("project_count", minimum_projects),
        ("task_count", minimum_tasks),
        ("workflow_count", minimum_workflows),
    ):
        if int(summary[field]) < minimum:
            failures.append(f"{field}={summary[field]}<{minimum}")
    if failures:
        raise ValueError(
            "formal Frontier corpus is too small and risks workflow memorization: "
            + ", ".join(failures)
        )
    return summary


def load_evaluation_rows(
    dataset_dirs: Iterable[str | Path],
    *,
    split: str,
    allow_formal_local: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if split not in {"calibration", "test_id", "test_ood"}:
        raise ValueError("evaluation rows require calibration, test_id, or test_ood")
    if allow_formal_local and split != "calibration":
        raise ValueError(
            "formal-local evaluation evidence is allowed only for calibration"
        )
    rows: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    seen_runs: set[str] = set()
    seen_decisions: set[str] = set()
    for directory in dataset_dirs:
        root = Path(directory)
        manifest = json.loads(
            (root / "dataset_manifest.json").read_text(encoding="utf-8")
        )
        eligible = manifest.get("formal_training_eligible") is True
        if allow_formal_local:
            eligible = eligible or (
                manifest.get("formal_local_training_eligible") is True
            )
        if not eligible:
            raise ValueError(f"formal evaluation input is ineligible: {root}")
        _validate_formal_p6_manifest(
            root,
            manifest,
            expected_split=split,
            allow_formal_local=allow_formal_local,
        )
        run_id = str(manifest.get("source", {}).get("run_id") or "")
        if not run_id:
            raise ValueError(f"dataset has no source run_id: {root}")
        if run_id in seen_runs:
            raise ValueError(f"duplicate source run in evaluation inputs: {run_id}")
        seen_runs.add(run_id)
        manifests.append(manifest)
        child_return_targets = _child_return_targets(root)
        for line in (root / "frontier_decision_points.jsonl").read_text(
            encoding="utf-8"
        ).splitlines():
            row = _attach_child_completion_targets(
                json.loads(line), child_return_targets
            )
            if str(row.get("split")) == split and row.get("training_eligible") is not False:
                decision_id = str(row.get("decision_id") or "")
                if not decision_id:
                    raise ValueError(f"decision point has no identity: {root}")
                if decision_id in seen_decisions:
                    raise ValueError(f"duplicate decision point: {decision_id}")
                seen_decisions.add(decision_id)
                rows.append(row)
    _validate_demand_rows(rows)
    return rows, manifests


def _validate_formal_p6_manifest(
    root: Path,
    manifest: Mapping[str, Any],
    *,
    expected_split: str,
    allow_formal_local: bool = False,
) -> None:
    if manifest.get("dataset_kind") != FORMAL_P6_DATASET_KIND:
        raise ValueError(f"formal input has an unsupported dataset kind: {root}")
    if manifest.get("evaluation_role") not in {
        "frozen_split_training_evidence",
        "frozen_split_local_training_evidence",
    }:
        raise ValueError(f"formal input is not frozen-split evidence: {root}")
    split_contract = manifest.get("split_contract") or {}
    if (
        split_contract.get("source") != "explicit frozen split manifest"
        or split_contract.get("development_only") is not False
        or not split_contract.get("manifest_digest")
    ):
        raise ValueError(f"formal input has no frozen project split contract: {root}")
    source = manifest.get("source") or {}
    contract = source.get("collection_contract") or {}
    plan_id = contract.get("plan_id")
    if plan_id not in FORMAL_P6_PLAN_IDS:
        raise ValueError(f"formal input did not use the P6 collection plan: {root}")
    plan_split = {
        "h200-bf16-formal-train-v1": "train",
        "h200-bf16-formal-calibration-v1": "calibration",
        "qwen35-native-reactive-v0520-v1-calibration-66root": "calibration",
    }.get(str(plan_id))
    if plan_split is not None and plan_split != expected_split:
        raise ValueError(
            f"formal plan {plan_id!r} cannot provide {expected_split!r} evidence: "
            f"{root}"
        )
    environment = source.get("runtime_environment_contract") or {}
    native_reactive = plan_id in {
        "qwen35-native-reactive-v0520-v1",
        "qwen35-native-reactive-v0520-v2",
        "qwen35-native-reactive-v0520-v3",
        "qwen35-native-reactive-v0520-v4-128root",
        "qwen35-native-reactive-v0520-v5-overlapped-128root",
        "qwen35-native-reactive-v0520-v1-calibration-66root",
    }
    if native_reactive and (
        not allow_formal_local
        or manifest.get("formal_local_training_eligible") is not True
        or contract.get("runtime_policy") != "frozen_native_reactive_v0520"
        or contract.get("raw_trace_eligible") is not True
        or contract.get("model_revision_stable") is not True
        or environment.get("runtime_kind") != "native_reactive_v0520"
        or (source.get("native_request_evidence") or {}).get(
            "telemetry_complete"
        ) is not True
    ):
        raise ValueError(f"native reactive input is not verified local {expected_split}: {root}")
    profile = environment.get("runtime_profile") or {}
    revisions = environment.get("model_revision_sha256") or {}
    identity = environment.get("server_identity") or {}
    hardware = environment.get("hardware") or {}
    if (
        environment.get("uniform") is not True
        or (not native_reactive and not profile.get("sha256"))
        or not revisions.get("config.json")
        or not revisions.get("tokenizer.json")
        or not identity.get("weight_dtype")
        or not identity.get("resolved_kv_dtype")
        or not environment.get("sglang_commit")
        or not environment.get("sglang_patch_sha256")
        or not hardware.get("uuid")
    ):
        raise ValueError(f"formal input has no frozen runtime environment: {root}")
    if contract.get("split") != expected_split:
        raise ValueError(
            f"collection split {contract.get('split')!r} does not match "
            f"{expected_split!r}: {root}"
        )
    local_training = (
        (expected_split == "train" or allow_formal_local)
        and manifest.get("formal_local_training_eligible") is True
    )
    if (
        (not local_training and contract.get("training_eligible") is not True)
        or contract.get("runtime_source_stable") is not True
        or contract.get("runtime_policy") != (
            "frozen_native_reactive_v0520" if native_reactive
            else "frozen_p5_observed"
        )
        or bool(contract.get("predictor_enabled"))
        or bool(contract.get("predictive_actions_enabled"))
    ):
        raise ValueError(f"formal input violates the frozen P6 collection contract: {root}")
    if not source.get("workload_manifest_sha256"):
        raise ValueError(f"formal input has no workload manifest identity: {root}")


def select_frontier_hyperparameters(
    rows: Iterable[Mapping[str, Any]],
    *,
    candidates: Iterable[FrontierModelHyperparameters] | None = None,
    action_targets: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Select structured-model smoothing/backoff by train-project LOPO.

    Each held-out project is scored as one macro fold. Calibration and test
    rows are rejected so this procedure cannot silently tune on reportable
    evaluation data.
    """

    values = [dict(row) for row in rows]
    action_values = [dict(row) for row in action_targets]
    _validate_demand_rows(values)
    if not values or {str(row.get("split")) for row in values} != {"train"}:
        raise ValueError("LOPO selection requires only formal train rows")
    projects = sorted({str(row.get("project") or "unknown") for row in values})
    if "unknown" in projects or len(projects) < 2:
        raise ValueError("LOPO selection requires at least two identified projects")
    options = tuple(candidates or _default_hyperparameter_candidates())
    if not options:
        raise ValueError("LOPO selection requires candidate hyperparameters")
    if action_values and {
        str(row.get("split") or "unknown") for row in action_values
    } != {"train"}:
        raise ValueError("LOPO action targets require only train rows")

    reports = []
    for index, option in enumerate(options):
        folds = []
        for held_out in projects:
            fit_rows = [row for row in values if str(row.get("project")) != held_out]
            validation_rows = [
                {**row, "split": "calibration"}
                for row in values
                if str(row.get("project")) == held_out
            ]
            validation_action_targets = [
                {**row, "split": "calibration"}
                for row in action_values
                if str(row.get("project")) == held_out
            ]
            model = FrontierBeliefModel(
                model_version=f"lopo-candidate-{index}",
                hyperparameters=option,
            )
            fit_action_targets = [
                row
                for row in action_values
                if str(row.get("project")) != held_out
            ]
            model.fit(
                fit_rows,
                action_targets=fit_action_targets,
            )
            metrics = evaluate_frontier_model(
                model,
                validation_rows,
                validation_action_targets,
            )
            components = _lopo_loss_components(metrics, validation_rows)
            operational_regret = components.get(
                "tool_wait_slack_brier_regret"
            )
            secondary_components = {
                name: value
                for name, value in components.items()
                if name
                not in {
                    "tool_wait_slack_brier",
                    "tool_wait_slack_brier_regret",
                }
            }
            secondary_loss = sum(secondary_components.values()) / max(
                len(secondary_components), 1
            )
            folds.append(
                {
                    "held_out_project": held_out,
                    "loss": (
                        float(operational_regret)
                        if action_values and operational_regret is not None
                        else sum(components.values()) / max(len(components), 1)
                    ),
                    "operational_tau_brier_regret": operational_regret,
                    "operational_tau_brier": components.get(
                        "tool_wait_slack_brier"
                    ),
                    "secondary_loss": secondary_loss,
                    "loss_components": components,
                    "local_episode_count": metrics["local_episode_count"],
                }
            )
        reports.append(
            {
                "candidate_index": index,
                "hyperparameters": option.to_dict(),
                "project_macro_loss": sum(fold["loss"] for fold in folds)
                / len(folds),
                "project_macro_operational_tau_brier_regret": (
                    sum(
                        float(fold["operational_tau_brier_regret"])
                        for fold in folds
                    )
                    / len(folds)
                    if action_values
                    and all(
                        fold["operational_tau_brier_regret"] is not None
                        for fold in folds
                    )
                    else None
                ),
                "project_macro_secondary_loss": sum(
                    fold["secondary_loss"] for fold in folds
                )
                / len(folds),
                "folds": folds,
            }
        )
    selected = min(
        reports,
        key=lambda item: (
            (
                item["project_macro_operational_tau_brier_regret"]
                if action_values
                else item["project_macro_loss"]
            ),
            item["project_macro_secondary_loss"],
            item["candidate_index"],
        ),
    )
    return {
        "schema_version": 1,
        "selection_method": "leave_one_train_project_out_project_macro",
        "selection_objective": (
                "primary: project-macro operational-tau Brier regret versus "
                "the held-out-project constant-prevalence baseline; "
            "secondary: boundary/tool NLL, scale-normalized token-demand MAE, "
            "and action-specific required-head OOD"
            if action_values
            else "legacy mean available local-head loss"
        ),
        "projects": projects,
        "candidate_count": len(reports),
        "action_target_count": len(action_values),
        "selected_candidate_index": selected["candidate_index"],
        "selected_hyperparameters": selected["hyperparameters"],
        "candidates": reports,
    }


def evaluate_frontier_model(
    model: FrontierBeliefModel,
    rows: Iterable[Mapping[str, Any]],
    action_targets: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Evaluate local beliefs with local-episode weights and no model updates."""

    values = [dict(row) for row in rows]
    action_values = [dict(item) for item in action_targets]
    if not values:
        raise ValueError("evaluation requires decision points")
    _validate_demand_rows(values)
    splits = {str(row.get("split") or "unknown") for row in values}
    if not splits.issubset({"calibration", "test_id", "test_ood"}):
        raise ValueError("evaluation cannot consume train or development rows")
    action_splits = {
        str(row.get("split") or "unknown") for row in action_values
    }
    if action_values and not action_splits.issubset(splits):
        raise ValueError("action targets do not match the evaluation split")
    local_counts = _local_episode_counts(values)
    workflow_episode_counts = _workflow_local_episode_counts(values)
    classification: dict[str, dict[str, Any]] = {
        "boundary": _classification_accumulator(),
        "tool_terminal": _classification_accumulator(),
    }
    scalar: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "weight": 0.0,
            "absolute_error": 0.0,
            "interval_weight": 0.0,
            "covered_weight": 0.0,
            "interval_width": 0.0,
        }
    )
    interval_episode_coverage: defaultdict[
        str, defaultdict[str, list[bool]]
    ] = defaultdict(lambda: defaultdict(list))
    interval_episode_workflow: dict[str, str] = {}
    target_availability: defaultdict[str, dict[str, float]] = defaultdict(
        lambda: {"weight": 0.0, "available_weight": 0.0}
    )
    required_head_weight = 0.0
    required_head_ood_weight = 0.0
    action_head_availability: defaultdict[str, dict[str, float]] = defaultdict(
        lambda: {"weight": 0.0, "available_weight": 0.0}
    )
    wait_slack: defaultdict[str, dict[str, float]] = defaultdict(
        lambda: {
            "weight": 0.0,
            "known_outcome_weight": 0.0,
            "brier": 0.0,
            "correct": 0.0,
            "true_positive": 0.0,
            "false_positive": 0.0,
            "true_negative": 0.0,
            "false_negative": 0.0,
            "true_positive_at_0_9": 0.0,
            "false_positive_at_0_9": 0.0,
            "true_negative_at_0_9": 0.0,
            "false_negative_at_0_9": 0.0,
            "predicted_positive": 0.0,
            "actual_positive": 0.0,
        }
    )
    operational_support: defaultdict[str, Counter[str]] = defaultdict(Counter)
    operational_command_support: defaultdict[str, Counter[str]] = defaultdict(
        Counter
    )
    operational_tau: defaultdict[str, list[float]] = defaultdict(list)
    tool_weights = _tool_fit_weights(values)
    child_completion_weights = _child_completion_fit_weights(values)
    support_weight: Counter[str] = Counter()
    for row in values:
        episode = str(row.get("episode_group_id") or row.get("decision_id"))
        trigger = str(row.get("trigger_kind") or "")
        labels = {
            str(item.get("invocation_id")): item
            for item in row.get("labels", ())
        }
        for raw_features in row.get("invocations", ()):
            invocation_id = str(raw_features.get("invocation_id") or "")
            label = labels.get(invocation_id)
            if label is None:
                continue
            local_episode = f"{episode}|{invocation_id}"
            weight = 1.0 / max(1, local_counts[(episode, invocation_id)])
            workflow = _workflow_group_id(row)
            weight /= max(1, workflow_episode_counts[workflow])
            features = _local_features_from_row(
                row, raw_features,
                tool_feature_contract=model.tool_feature_contract,
            )
            prediction = model.predict(features)
            support_weight[prediction.support_level] += weight
            generic_action_heads = (
                ()
                if features.state == InvocationState.WAIT_TOOL.value
                else _action_head_requirements_for_state(features.state)
            )
            for action, head in generic_action_heads:
                key = f"{action}|{features.state}|{head}"
                available = prediction.support_for(head) != "unavailable"
                action_head_availability[key]["weight"] += weight
                action_head_availability[key]["available_weight"] += (
                    weight * available
                )
                required_head_weight += weight
                required_head_ood_weight += weight * (not available)

            boundary = _normalize_boundary(label.get("next_boundary_kind"))
            if (
                features.state == InvocationState.RUNNING_LLM.value
                and boundary is not None
                and _target_eligible(label, "action_boundary")
            ):
                _observe_classification(
                    classification["boundary"],
                    prediction.boundary_distribution,
                    boundary,
                    weight,
                )
                availability = target_availability["action_boundary"]
                availability["weight"] += weight
                availability["available_weight"] += weight * bool(
                    prediction.boundary_distribution
                )
            if (
                trigger == RuntimeEventKind.TOOL_START.value
                and features.state == InvocationState.WAIT_TOOL.value
                and _target_eligible(label, "external_wait")
                and not _target_right_censored(label, "external_wait")
            ):
                status = str(label.get("next_boundary_status") or "error")
                if status not in {"success", "error", "censored"}:
                    status = "error"
                _observe_classification(
                    classification["tool_terminal"],
                    prediction.tool_terminal_distribution,
                    status,
                    tool_weights.get(
                        _tool_row_identity(row, raw_features), weight
                    ),
                )
                availability = target_availability["tool_terminal"]
                availability["weight"] += weight
                availability["available_weight"] += weight * bool(
                    prediction.tool_terminal_distribution
                )

            targets = (
                (
                    "remaining_decode_tokens",
                    "remaining_decode_demand",
                    label.get("remaining_output_tokens")
                    if features.state == InvocationState.RUNNING_LLM.value
                    and _target_eligible(label, "remaining_decode_demand")
                    else None,
                    prediction.remaining_decode_tokens,
                ),
                (
                    "prompt_growth_tokens",
                    "prompt_growth",
                    label.get("reentry_prompt_delta_tokens")
                    if _target_eligible(label, "prompt_growth")
                    else None,
                    prediction.prompt_growth_tokens,
                ),
                (
                    "next_output_tokens",
                    "next_output_demand",
                    label.get("next_output_tokens")
                    if _target_eligible(label, "next_output_demand")
                    else None,
                    prediction.next_output_tokens,
                ),
                (
                    "remaining_to_return_ms",
                    "child_completion",
                    label.get("remaining_to_return_ms")
                    if _target_eligible(label, "child_completion")
                    else None,
                    prediction.remaining_to_return_ms,
                ),
            )
            for name, target_name, actual, distribution in targets:
                if actual is None or not distribution.values:
                    if actual is not None:
                        availability = target_availability[target_name]
                        availability["weight"] += weight
                    continue
                availability = target_availability[target_name]
                availability["weight"] += weight
                availability["available_weight"] += weight
                actual_value = float(actual)
                scalar_weight = (
                    child_completion_weights.get(
                        (
                            str(row.get("decision_id") or ""),
                            invocation_id,
                        ),
                        weight,
                    )
                    if name == "remaining_to_return_ms"
                    else weight
                )
                scalar_episode = (
                    f"{workflow}|child:{invocation_id}"
                    if name == "remaining_to_return_ms"
                    else local_episode
                )
                metrics = scalar[name]
                metrics["weight"] += scalar_weight
                metrics["absolute_error"] += scalar_weight * abs(
                    actual_value - distribution.quantile(0.5)
                )
                interval = prediction.calibrated_intervals.get(name)
                if interval is not None:
                    lower, upper = interval
                    metrics["interval_weight"] += scalar_weight
                    metrics["covered_weight"] += scalar_weight * (
                        lower <= actual_value <= upper
                    )
                    metrics["interval_width"] += scalar_weight * (upper - lower)
                    interval_episode_coverage[name][scalar_episode].append(
                        lower <= actual_value <= upper
                    )
                    interval_episode_workflow[scalar_episode] = workflow

    action_weights = _action_target_weights(action_values)
    action_target_known_count: Counter[str] = Counter()
    action_target_total_count: Counter[str] = Counter()
    action_evidence: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for target in action_values:
        features = _local_features_from_action_target(target)
        prediction = model.predict(features)
        base_support = (
            prediction.operational_timing_curve.support_level
            if prediction.operational_timing_curve is not None
            else prediction.wait_belief.support_detail
        )
        identity = _action_target_identity(target)
        base_weight = action_weights.get(identity, 0.0)
        known_weight = base_weight
        for action, value in (target.get("actions") or {}).items():
            action_target_total_count[action] += 1
            if not bool(value.get("outcome_known")):
                continue
            action_target_known_count[action] += 1
            tau_ms = float(value["operational_tau_ms"])
            if action not in {"prepare_host", "prefetch_gpu"}:
                continue
            key = f"{action}|wait_tool|operational_tau"
            wait_slack[key]["known_outcome_weight"] += known_weight
            timing = prediction.action_timing(action, tau_ms)
            if timing is None:
                support = "unavailable"
            else:
                support = base_support
                probability = timing.favorable_probability
            required_heads = (
                ("tool_wait", "prompt_growth")
                if action == "prefetch_gpu"
                else ("tool_wait",)
            )
            for head in required_heads:
                availability_key = f"{action}|wait_tool|{head}"
                available = prediction.support_for(head) != "unavailable"
                action_head_availability[availability_key]["weight"] += base_weight
                action_head_availability[availability_key][
                    "available_weight"
                ] += base_weight * available
                required_head_weight += base_weight
                required_head_ood_weight += base_weight * (not available)
            operational_support[action][support] += known_weight
            command = str(target.get("command_class") or "unknown")
            operational_command_support[f"{action}|{command}"][support] += (
                known_weight
            )
            operational_tau[action].append(tau_ms)
            action_evidence[action][
                str(target.get("tau_evidence") or "unknown")
            ] += 1
            if timing is None:
                continue
            outcome = float(bool(value["outcome"]))
            wait_slack[key]["weight"] += known_weight
            wait_slack[key]["brier"] += known_weight * (
                probability - outcome
            ) ** 2
            predicted_positive = probability >= 0.5
            actual_positive = bool(outcome)
            wait_slack[key]["correct"] += known_weight * (
                predicted_positive == actual_positive
            )
            wait_slack[key]["predicted_positive"] += (
                known_weight * predicted_positive
            )
            wait_slack[key]["actual_positive"] += (
                known_weight * actual_positive
            )
            if predicted_positive and actual_positive:
                wait_slack[key]["true_positive"] += known_weight
            elif predicted_positive:
                wait_slack[key]["false_positive"] += known_weight
            elif actual_positive:
                wait_slack[key]["false_negative"] += known_weight
            else:
                wait_slack[key]["true_negative"] += known_weight
            predicted_high_confidence = probability >= 0.9
            if predicted_high_confidence and actual_positive:
                wait_slack[key]["true_positive_at_0_9"] += known_weight
            elif predicted_high_confidence:
                wait_slack[key]["false_positive_at_0_9"] += known_weight
            elif actual_positive:
                wait_slack[key]["false_negative_at_0_9"] += known_weight
            else:
                wait_slack[key]["true_negative_at_0_9"] += known_weight

    return {
        "model_version": model.model_version,
        "splits": sorted(splits),
        "decision_point_count": len(values),
        "local_episode_count": len(local_counts),
        "workflow_count": len(workflow_episode_counts),
        "classification": {
            name: _finalize_classification(metrics)
            for name, metrics in classification.items()
        },
        "scalar": {
            name: {
                "episode_weighted_mae": metrics["absolute_error"]
                / max(metrics["weight"], 1e-12),
                "calibrated_interval_coverage": (
                    metrics["covered_weight"] / metrics["interval_weight"]
                    if metrics["interval_weight"]
                    else None
                ),
                "mean_calibrated_interval_width": (
                    metrics["interval_width"] / metrics["interval_weight"]
                    if metrics["interval_weight"]
                    else None
                ),
                "episode_weight": metrics["weight"],
                **_episode_interval_diagnostics(
                    interval_episode_coverage.get(name, {}),
                    interval_episode_workflow,
                ),
            }
            for name, metrics in sorted(scalar.items())
        },
        "ood_fallback_rate": required_head_ood_weight
        / max(required_head_weight, 1e-12),
        "ood_fallback_semantics": "action_state_required_head_unavailable",
        "action_head_availability": {
            key: {
                "available_rate": values["available_weight"]
                / max(values["weight"], 1e-12),
                "episode_weight": values["weight"],
            }
            for key, values in sorted(action_head_availability.items())
        },
        "wait_slack": {
            key: {
                "brier": values["brier"] / max(values["weight"], 1e-12),
                "available_prediction_rate": values["weight"]
                / max(values["known_outcome_weight"], 1e-12),
                "climatology_brier": (
                    (values["actual_positive"] / max(values["weight"], 1e-12))
                    * (
                        1.0
                        - values["actual_positive"]
                        / max(values["weight"], 1e-12)
                    )
                ),
                "brier_skill": _brier_skill(
                    values["brier"] / max(values["weight"], 1e-12),
                    (
                        values["actual_positive"]
                        / max(values["weight"], 1e-12)
                    )
                    * (
                        1.0
                        - values["actual_positive"]
                        / max(values["weight"], 1e-12)
                    ),
                ),
                "accuracy_at_0_5": (
                    values["correct"] / max(values["weight"], 1e-12)
                ),
                "majority_baseline_accuracy": max(
                    values["actual_positive"] / max(values["weight"], 1e-12),
                    1.0
                    - values["actual_positive"] / max(values["weight"], 1e-12),
                ),
                "precision_at_0_5": (
                    values["true_positive"]
                    / max(
                        values["true_positive"] + values["false_positive"],
                        1e-12,
                    )
                ),
                "recall_at_0_5": (
                    values["true_positive"]
                    / max(
                        values["true_positive"] + values["false_negative"],
                        1e-12,
                    )
                ),
                "specificity_at_0_5": (
                    values["true_negative"]
                    / max(
                        values["true_negative"] + values["false_positive"],
                        1e-12,
                    )
                ),
                "balanced_accuracy_at_0_5": 0.5
                * (
                    values["true_positive"]
                    / max(
                        values["true_positive"] + values["false_negative"],
                        1e-12,
                    )
                    + values["true_negative"]
                    / max(
                        values["true_negative"] + values["false_positive"],
                        1e-12,
                    )
                ),
                "precision_at_0_9": (
                    values["true_positive_at_0_9"]
                    / max(
                        values["true_positive_at_0_9"]
                        + values["false_positive_at_0_9"],
                        1e-12,
                    )
                ),
                "recall_at_0_9": (
                    values["true_positive_at_0_9"]
                    / max(
                        values["true_positive_at_0_9"]
                        + values["false_negative_at_0_9"],
                        1e-12,
                    )
                ),
                "specificity_at_0_9": (
                    values["true_negative_at_0_9"]
                    / max(
                        values["true_negative_at_0_9"]
                        + values["false_positive_at_0_9"],
                        1e-12,
                    )
                ),
                "predicted_positive_rate": (
                    values["predicted_positive"]
                    / max(values["weight"], 1e-12)
                ),
                "actual_positive_rate": (
                    values["actual_positive"]
                    / max(values["weight"], 1e-12)
                ),
                "episode_weight": values["weight"],
            }
            for key, values in sorted(wait_slack.items())
        },
        "operational_tau_coverage": {
            action: {
                "target_row_count": action_target_total_count[action],
                "known_outcome_count": action_target_known_count[action],
                "known_outcome_rate": action_target_known_count[action]
                / max(action_target_total_count[action], 1),
                "tau_ms": _numeric_distribution_summary(
                    operational_tau.get(action, ())
                ),
                "evidence_grade_count": dict(
                    sorted(action_evidence.get(action, {}).items())
                ),
            }
            for action in sorted(action_target_total_count)
        },
        "operational_action_support_weight": {
            action: dict(sorted(values.items()))
            for action, values in sorted(operational_support.items())
        },
        "operational_command_class_support_weight": {
            key: dict(sorted(values.items()))
            for key, values in sorted(operational_command_support.items())
        },
        "target_availability": {
            name: {
                "available_rate": values["available_weight"]
                / max(values["weight"], 1e-12),
                "episode_weight": values["weight"],
            }
            for name, values in sorted(target_availability.items())
        },
        "support_weight": dict(sorted(support_weight.items())),
        "calibration_coverage_target": model.calibration_coverage,
    }


def _numeric_distribution_summary(
    values: Sequence[float],
) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "p50": None,
            "p95": None,
            "max": None,
        }
    ordered = sorted(float(value) for value in values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p50": ordered[(len(ordered) - 1) // 2],
        "p95": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "max": ordered[-1],
    }


def _episode_interval_diagnostics(
    by_episode: Mapping[str, Sequence[bool]],
    episode_workflow: Mapping[str, str],
) -> dict[str, Any]:
    if not by_episode:
        return {
            "local_episode_interval_coverage": None,
            "workflow_macro_local_episode_interval_coverage": None,
            "interval_local_episode_count": 0,
        }
    covered = {
        episode: all(values) for episode, values in by_episode.items()
    }
    by_workflow: defaultdict[str, list[bool]] = defaultdict(list)
    for episode, value in covered.items():
        by_workflow[episode_workflow.get(episode, "unknown")].append(value)
    return {
        "local_episode_interval_coverage": sum(covered.values())
        / len(covered),
        "workflow_macro_local_episode_interval_coverage": sum(
            sum(values) / len(values) for values in by_workflow.values()
        )
        / len(by_workflow),
        "interval_local_episode_count": len(covered),
    }


def _classification_accumulator() -> dict[str, Any]:
    return {
        "weight": 0.0,
        "negative_log_likelihood": 0.0,
        "brier": 0.0,
        "correct": 0.0,
        "top2_correct": 0.0,
        "confidence_records": [],
        "target_weight": Counter(),
        "top2_target_hit_weight": Counter(),
        "predicted_weight": Counter(),
        "confusion_weight": Counter(),
    }


def _default_hyperparameter_candidates() -> tuple[FrontierModelHyperparameters, ...]:
    return (
        FrontierModelHyperparameters(),
        FrontierModelHyperparameters(
            boundary_max_order=2,
            boundary_minimum_support=2.0,
            empirical_minimum_support=2.0,
            tool_minimum_support=2.0,
            pooled_demand_regularization=1e-4,
            operational_timing_regularization=0.0,
        ),
        FrontierModelHyperparameters(
            boundary_max_order=2,
            boundary_minimum_support=4.0,
            boundary_smoothing=1.0,
            empirical_minimum_support=4.0,
            tool_minimum_support=4.0,
            tool_smoothing=1.0,
            pooled_demand_regularization=1e-3,
            operational_timing_regularization=1e-4,
        ),
        FrontierModelHyperparameters(
            boundary_max_order=4,
            boundary_minimum_support=6.0,
            empirical_minimum_support=8.0,
            tool_minimum_support=8.0,
            pooled_demand_regularization=1e-2,
            operational_timing_regularization=1e-3,
        ),
    )


def _lopo_loss_components(
    metrics: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> dict[str, float]:
    components: dict[str, float] = {}
    for name in ("boundary", "tool_terminal"):
        loss = metrics["classification"][name]["negative_log_likelihood"]
        if loss is not None:
            components[f"{name}_nll"] = float(loss)
    scales = _target_scales(rows)
    for name, item in metrics["scalar"].items():
        if item["episode_weight"] > 0:
            components[f"{name}_normalized_mae"] = (
                float(item["episode_weighted_mae"])
                / max(scales.get(name, 1.0), 1.0)
            )
    slack_rows = tuple(metrics.get("wait_slack", {}).values())
    slack_weight = sum(float(item["episode_weight"]) for item in slack_rows)
    if slack_weight > 0:
        components["tool_wait_slack_brier"] = sum(
            float(item["brier"]) * float(item["episode_weight"])
            for item in slack_rows
        ) / slack_weight
        components["tool_wait_slack_brier_regret"] = sum(
            (
                float(item["brier"])
                - float(item["climatology_brier"])
            )
            * float(item["episode_weight"])
            for item in slack_rows
        ) / slack_weight
    components["ood_penalty"] = 0.25 * float(metrics["ood_fallback_rate"])
    return components


def _target_scales(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        labels = {
            str(item.get("invocation_id")): item for item in row.get("labels", ())
        }
        for features in row.get("invocations", ()):
            label = labels.get(str(features.get("invocation_id") or ""))
            if label is None:
                continue
            state = str(features.get("state") or "unknown")
            targets = {
                "remaining_decode_tokens": (
                    label.get("remaining_output_tokens")
                    if state == InvocationState.RUNNING_LLM.value
                    else None
                ),
                "prompt_growth_tokens": label.get("reentry_prompt_delta_tokens"),
                "next_output_tokens": label.get("next_output_tokens"),
            }
            for name, target in targets.items():
                if target is not None:
                    values[name].append(float(target))
    return {
        name: sorted(items)[len(items) // 2]
        for name, items in values.items()
        if items
    }


def _observe_classification(
    metrics: dict[str, Any],
    distribution: Mapping[str, float],
    target: str,
    weight: float,
) -> None:
    if not distribution:
        return
    probability = max(float(distribution.get(target, 0.0)), 1e-12)
    prediction = max(distribution, key=distribution.get)
    top2 = {
        name
        for name, _probability in sorted(
            distribution.items(), key=lambda item: (-item[1], item[0])
        )[:2]
    }
    confidence = float(distribution[prediction])
    correct = prediction == target
    vocabulary = set(distribution) | {target}
    brier = sum(
        (float(distribution.get(item, 0.0)) - float(item == target)) ** 2
        for item in vocabulary
    )
    metrics["weight"] += weight
    metrics["negative_log_likelihood"] += weight * -math.log(probability)
    metrics["brier"] += weight * brier
    metrics["correct"] += weight * correct
    metrics["top2_correct"] += weight * (target in top2)
    metrics["confidence_records"].append((confidence, correct, weight))
    metrics["target_weight"][target] += weight
    metrics["top2_target_hit_weight"][target] += weight * (target in top2)
    metrics["predicted_weight"][prediction] += weight
    metrics["confusion_weight"][(target, prediction)] += weight


def _finalize_classification(metrics: Mapping[str, Any]) -> dict[str, Any]:
    weight = float(metrics["weight"])
    if weight <= 0:
        return {
            "episode_weight": 0.0,
            "negative_log_likelihood": None,
            "brier": None,
            "accuracy": None,
            "majority_baseline_accuracy": None,
            "macro_recall": None,
            "per_class": {},
            "ece_10": None,
        }
    bins: list[list[tuple[float, bool, float]]] = [[] for _ in range(10)]
    for confidence, correct, item_weight in metrics["confidence_records"]:
        index = min(9, int(float(confidence) * 10))
        bins[index].append((float(confidence), bool(correct), float(item_weight)))
    ece = 0.0
    for bucket in bins:
        bucket_weight = sum(item[2] for item in bucket)
        if not bucket_weight:
            continue
        mean_confidence = sum(item[0] * item[2] for item in bucket) / bucket_weight
        accuracy = sum(float(item[1]) * item[2] for item in bucket) / bucket_weight
        ece += bucket_weight / weight * abs(mean_confidence - accuracy)
    classes = sorted(metrics["target_weight"])
    per_class = {}
    recalls = []
    for name in classes:
        support = float(metrics["target_weight"][name])
        predicted = float(metrics["predicted_weight"][name])
        true_positive = float(metrics["confusion_weight"][(name, name)])
        recall = true_positive / support if support else None
        top2_recall = (
            float(metrics["top2_target_hit_weight"][name]) / support
            if support
            else None
        )
        precision = true_positive / predicted if predicted else None
        if recall is not None:
            recalls.append(recall)
        per_class[name] = {
            "episode_weight": support,
            "prevalence": support / weight,
            "recall": recall,
            "top2_recall": top2_recall,
            "precision": precision,
        }
    return {
        "episode_weight": weight,
        "negative_log_likelihood": metrics["negative_log_likelihood"] / weight,
        "brier": metrics["brier"] / weight,
        "accuracy": metrics["correct"] / weight,
        "top2_accuracy": metrics["top2_correct"] / weight,
        "majority_baseline_accuracy": max(
            metrics["target_weight"].values(), default=0.0
        )
        / weight,
        "macro_recall": sum(recalls) / len(recalls) if recalls else None,
        "per_class": per_class,
        "ece_10": ece,
    }


def _local_features_from_row(
    row: Mapping[str, Any],
    features: Mapping[str, Any],
    *,
    tool_feature_contract: str = "legacy",
) -> LocalFrontierFeatures:
    trigger_attributes = (
        row.get("trigger_attributes") or {}
        if tool_feature_contract == "legacy"
        or row.get("trigger_invocation_id") == features.get("invocation_id")
        else {}
    )
    return LocalFrontierFeatures(
        invocation_id=str(features.get("invocation_id") or ""),
        state=str(features.get("state") or "unknown"),
        agent_definition_id=str(
            features.get("agent_definition_id") or "unknown"
        ),
        boundary_history=tuple(
            str(item) for item in features.get("boundary_history", ())
        ),
        tool_family=str(
            trigger_attributes.get("tool_family")
            or features.get("active_tool_family")
            or "unknown"
        ),
        backend_class=str(
            trigger_attributes.get("backend_class")
            or features.get("backend_class")
            or "unknown"
        ),
        command_class=str(
            trigger_attributes.get("command_class")
            or trigger_attributes.get("tool_name")
            or trigger_attributes.get("backend_class")
            or "unknown"
        ),
        observed_command_class=str(
            trigger_attributes.get("observed_command_class") or "unknown"
        ),
        generated_tokens=int(features.get("observed_output_tokens") or 0),
        elapsed_wait_ms=float(features.get("active_tool_elapsed_ms") or 0.0),
        current_sequence_tokens=int(
            features.get("current_sequence_tokens")
            or features.get("context_tokens")
            or 0
        ),
        active_tool_count=int(features.get("active_tool_count") or 0),
        backend_pressure=str(features.get("backend_pressure") or "unknown"),
        invocation_elapsed_ms=float(
            features.get("invocation_elapsed_ms") or 0.0
        ),
        state_elapsed_ms=float(features.get("state_elapsed_ms") or 0.0),
        llm_round=int(features.get("llm_round") or 0),
        child_count=int(features.get("child_count") or 0),
        unfinished_child_count=int(
            features.get("unfinished_child_count") or 0
        ),
        is_child=(
            features.get("is_child") is True
            or (
                tool_feature_contract != "legacy"
                and row.get("trigger_invocation_id") == features.get("invocation_id")
                and trigger_attributes.get("is_child") is True
            )
        ),
    )




def _target_eligible(label: Mapping[str, Any], target: str) -> bool:
    eligibility = label.get("target_training_eligible")
    if isinstance(eligibility, Mapping):
        return bool(eligibility.get(target, False))
    return not bool(label.get("censored", False))


def _target_right_censored(label: Mapping[str, Any], target: str) -> bool:
    values = label.get("target_right_censored")
    return bool(isinstance(values, Mapping) and values.get(target, False))
def _validate_demand_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    for row in rows:
        schema_version = int(row.get("schema_version") or 0)
        if schema_version < MINIMUM_DEMAND_DECISION_SCHEMA_VERSION:
            raise ValueError(
                "frontier demand rows must be re-exported with load-independent "
                f"schema v{MINIMUM_DEMAND_DECISION_SCHEMA_VERSION}+"
            )
        for label in row.get("labels", ()):
            contaminated = FORBIDDEN_LOAD_COUPLED_LABELS.intersection(label)
            if contaminated:
                raise ValueError(
                    "load-coupled GPU service labels are forbidden in Frontier fit: "
                    f"{sorted(contaminated)}"
                )
        for features in row.get("invocations", ()):
            contaminated = FORBIDDEN_LOAD_COUPLED_FEATURES.intersection(features)
            if contaminated:
                raise ValueError(
                    "load-coupled scheduler features are forbidden in Frontier fit; "
                    "retain them under diagnostics only: "
                    f"{sorted(contaminated)}"
                )


def _local_episode_counts(
    rows: Sequence[Mapping[str, Any]],
) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        episode = str(row.get("episode_group_id") or row.get("decision_id"))
        labels = {
            str(item.get("invocation_id")) for item in row.get("labels", ())
        }
        for features in row.get("invocations", ()):
            invocation_id = str(features.get("invocation_id") or "")
            if invocation_id in labels:
                counts[(episode, invocation_id)] += 1
    return counts


def _workflow_group_id(row: Mapping[str, Any]) -> str:
    return str(
        row.get("workflow_id")
        or row.get("workload_group_id")
        or row.get("episode_group_id")
        or row.get("decision_id")
        or "unknown"
    )



def _tool_row_identity(
    row: Mapping[str, Any], features: Mapping[str, Any]
) -> tuple[str, str, str]:
    attributes = row.get("trigger_attributes") or {}
    return (
        _workflow_group_id(row),
        str(attributes.get("tool_call_id") or row.get("decision_id") or ""),
        str(features.get("invocation_id") or ""),
    )


def _tool_fit_weights(
    rows: Sequence[Mapping[str, Any]],
    *,
    trigger_only: bool = False,
) -> dict[tuple[str, str, str], float]:
    identities_by_workflow: defaultdict[str, set[tuple[str, str, str]]] = (
        defaultdict(set)
    )
    for row in rows:
        if str(row.get("trigger_kind") or "") != RuntimeEventKind.TOOL_START.value:
            continue
        labels = {
            str(item.get("invocation_id") or ""): item
            for item in row.get("labels", ())
        }
        for features in row.get("invocations", ()):
            invocation_id = str(features.get("invocation_id") or "")
            if trigger_only and invocation_id != row.get("trigger_invocation_id"):
                continue
            label = labels.get(invocation_id)
            if (
                str(features.get("state") or "") == InvocationState.WAIT_TOOL.value
                and label is not None
                and _target_eligible(label, "external_wait")
            ):
                identity = _tool_row_identity(row, features)
                identities_by_workflow[identity[0]].add(identity)
    return {
        identity: 1.0 / len(identities)
        for identities in identities_by_workflow.values()
        for identity in identities
    }


def _child_completion_fit_weights(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], float]:
    row_counts: Counter[tuple[str, str]] = Counter()
    children_by_workflow: defaultdict[str, set[str]] = defaultdict(set)
    row_child: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        decision_id = str(row.get("decision_id") or "")
        workflow = _workflow_group_id(row)
        for label in row.get("labels", ()):
            invocation_id = str(label.get("invocation_id") or "")
            if (
                not invocation_id
                or label.get("remaining_to_return_ms") is None
                or not _target_eligible(label, "child_completion")
            ):
                continue
            child = (workflow, invocation_id)
            row_counts[child] += 1
            children_by_workflow[workflow].add(invocation_id)
            row_child[(decision_id, invocation_id)] = child
    return {
        identity: 1.0
        / max(1, row_counts[child])
        / max(1, len(children_by_workflow[child[0]]))
        for identity, child in row_child.items()
    }


def _action_target_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("decision_id") or ""),
        str(row.get("invocation_id") or ""),
    )


def _action_target_weights(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], float]:
    row_counts: Counter[tuple[str, str]] = Counter()
    episodes_by_workflow: defaultdict[str, set[str]] = defaultdict(set)
    row_episode: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        identity = _action_target_identity(row)
        workflow = str(row.get("workflow_id") or "unknown")
        episode = str(row.get("tool_wait_episode_id") or "|".join(identity))
        key = (workflow, episode)
        row_counts[key] += 1
        episodes_by_workflow[workflow].add(episode)
        row_episode[identity] = key
    return {
        identity: 1.0
        / max(1, row_counts[key])
        / max(1, len(episodes_by_workflow[key[0]]))
        for identity, key in row_episode.items()
    }


def _local_features_from_action_target(
    row: Mapping[str, Any],
) -> LocalFrontierFeatures:
    return LocalFrontierFeatures(
        invocation_id=str(row.get("invocation_id") or ""),
        state=InvocationState.WAIT_TOOL.value,
        agent_definition_id=str(row.get("agent_definition_id") or "unknown"),
        boundary_history=tuple(
            str(item) for item in row.get("boundary_history", ())
        ),
        tool_family=str(row.get("tool_family") or "unknown"),
        backend_class=str(row.get("backend_class") or "unknown"),
        command_class=str(row.get("command_class") or "unknown"),
        elapsed_wait_ms=float(row.get("elapsed_wait_ms") or 0.0),
        current_sequence_tokens=int(row.get("current_sequence_tokens") or 0),
        active_tool_count=int(row.get("active_tool_count") or 0),
        backend_pressure=(
            f"active_family:{int(row.get('active_tool_count') or 0)}"
        ),
    )


def _workflow_local_episode_counts(
    rows: Sequence[Mapping[str, Any]],
) -> Counter[str]:
    local_episodes: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        workflow = _workflow_group_id(row)
        episode = str(row.get("episode_group_id") or row.get("decision_id"))
        labels = {
            str(item.get("invocation_id")) for item in row.get("labels", ())
        }
        for features in row.get("invocations", ()):
            invocation_id = str(features.get("invocation_id") or "")
            if invocation_id in labels:
                local_episodes[workflow].add((episode, invocation_id))
    return Counter(
        {workflow: len(episodes) for workflow, episodes in local_episodes.items()}
    )


def _temperature_scale(
    distribution: Mapping[str, float], temperature: float
) -> dict[str, float]:
    if not distribution:
        return {}
    temperature = max(1e-3, temperature)
    powered = {
        key: max(float(probability), 1e-12) ** (1.0 / temperature)
        for key, probability in distribution.items()
    }
    normalizer = sum(powered.values())
    return {key: value / normalizer for key, value in powered.items()}


def _fit_temperature(
    records: Sequence[tuple[Mapping[str, float], str, float]],
) -> float:
    if not records:
        return 1.0
    candidates = [0.5 + index * 0.05 for index in range(51)]
    return min(
        candidates,
        key=lambda temperature: sum(
            -weight
            * math.log(
                max(
                    _temperature_scale(distribution, temperature).get(target, 0.0),
                    1e-12,
                )
            )
            for distribution, target, weight in records
        ),
    )


def _calibrate_binary_probability(
    probability: float,
    *,
    scale: float,
    offset: float,
) -> float:
    if math.isclose(scale, 1.0) and math.isclose(offset, 0.0):
        return min(1.0, max(0.0, float(probability)))
    clipped = min(1.0 - 1e-6, max(1e-6, float(probability)))
    logit = math.log(clipped / (1.0 - clipped))
    calibrated_logit = scale * logit + offset
    if calibrated_logit >= 0:
        decay = math.exp(-calibrated_logit)
        return 1.0 / (1.0 + decay)
    growth = math.exp(calibrated_logit)
    return growth / (1.0 + growth)


def _uncalibrate_binary_probability(
    probability: float,
    *,
    scale: float,
    offset: float,
) -> float:
    """Invert the monotone Platt map for distribution-quantile lookup."""

    if not math.isfinite(scale) or scale <= 0 or not math.isfinite(offset):
        raise ValueError("binary calibration must be finite with positive scale")
    clipped = min(1.0 - 1e-6, max(1e-6, float(probability)))
    calibrated_logit = math.log(clipped / (1.0 - clipped))
    raw_logit = (calibrated_logit - offset) / scale
    if raw_logit >= 0:
        decay = math.exp(-min(raw_logit, 40.0))
        return 1.0 / (1.0 + decay)
    growth = math.exp(max(raw_logit, -40.0))
    return growth / (1.0 + growth)


def _fit_binary_logit_calibration(
    records: Sequence[tuple[float, bool, float]],
) -> tuple[float, float]:
    """Fit a regularized Platt map without adding a second wait predictor."""

    if not records:
        return 1.0, 0.0
    prepared = tuple(
        (
            math.log(
                min(1.0 - 1e-4, max(1e-4, probability))
                / (1.0 - min(1.0 - 1e-4, max(1e-4, probability)))
            ),
            float(outcome),
            max(0.0, float(weight)),
        )
        for probability, outcome, weight in records
        if weight > 0
    )
    if not prepared:
        return 1.0, 0.0
    scale = 1.0
    offset = 0.0
    regularization = max(1e-6, sum(item[2] for item in prepared) * 1e-3)
    for _ in range(40):
        gradient_scale = regularization * (scale - 1.0)
        gradient_offset = regularization * offset
        hessian_scale = regularization
        hessian_offset = regularization
        hessian_cross = 0.0
        for logit, outcome, weight in prepared:
            probability = _calibrate_binary_probability(
                1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, logit)))),
                scale=scale,
                offset=offset,
            )
            residual = weight * (probability - outcome)
            curvature = weight * probability * (1.0 - probability)
            gradient_scale += residual * logit
            gradient_offset += residual
            hessian_scale += curvature * logit * logit
            hessian_cross += curvature * logit
            hessian_offset += curvature
        determinant = hessian_scale * hessian_offset - hessian_cross**2
        if determinant <= 1e-12:
            break
        step_scale = (
            hessian_offset * gradient_scale
            - hessian_cross * gradient_offset
        ) / determinant
        step_offset = (
            hessian_scale * gradient_offset
            - hessian_cross * gradient_scale
        ) / determinant
        scale = min(5.0, max(0.05, scale - step_scale))
        offset = min(5.0, max(-5.0, offset - step_offset))
        if max(abs(step_scale), abs(step_offset)) < 1e-6:
            break
    return scale, offset


def _fit_binary_brier_calibration(
    records: Sequence[tuple[float, bool, float]],
) -> tuple[float, float]:
    """Fit an action probability map against the decision-facing Brier loss."""

    prepared = tuple(
        (
            math.log(
                min(1.0 - 1e-4, max(1e-4, probability))
                / (1.0 - min(1.0 - 1e-4, max(1e-4, probability)))
            ),
            float(outcome),
            max(0.0, float(weight)),
        )
        for probability, outcome, weight in records
        if weight > 0
    )
    total_weight = sum(item[2] for item in prepared)
    if total_weight <= 0:
        return 1.0, 0.0
    prevalence = sum(item[1] * item[2] for item in prepared) / total_weight
    mean_logit = sum(item[0] * item[2] for item in prepared) / total_weight
    prevalence = min(1.0 - 1e-6, max(1e-6, prevalence))
    prevalence_logit = math.log(prevalence / (1.0 - prevalence))

    def loss(scale: float, offset: float) -> float:
        return sum(
            weight
            * (
                _calibrate_binary_probability(
                    1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, logit)))),
                    scale=scale,
                    offset=offset,
                )
                - outcome
            )
            ** 2
            for logit, outcome, weight in prepared
        ) / total_weight

    candidates = [(1.0, 0.0), _fit_binary_logit_calibration(records)]
    for scale in (
        0.05,
        0.1,
        0.2,
        0.3,
        0.4,
        0.5,
        0.65,
        0.8,
        1.0,
        1.25,
        1.5,
        2.0,
    ):
        centered_offset = prevalence_logit - scale * mean_logit
        candidates.extend(
            (scale, centered_offset + index * 0.05)
            for index in range(-30, 31)
        )
    return min(candidates, key=lambda item: loss(*item))


def _brier_skill(brier: float, climatology_brier: float) -> float | None:
    if climatology_brier <= 1e-12:
        return None
    return 1.0 - brier / climatology_brier


def _binary_probability_metrics(
    records: Sequence[tuple[float, bool, float]],
    *,
    scale: float,
    offset: float,
) -> dict[str, float]:
    weight = sum(max(0.0, float(item_weight)) for _, _, item_weight in records)
    if weight <= 0:
        return {
            "episode_weight": 0.0,
            "brier": 0.0,
            "climatology_brier": 0.0,
            "brier_skill": 0.0,
            "accuracy_at_0_5": 0.0,
            "balanced_accuracy_at_0_5": 0.0,
            "actual_positive_rate": 0.0,
        }
    positive = sum(
        max(0.0, float(item_weight)) * float(bool(outcome))
        for _, outcome, item_weight in records
    )
    calibrated_records: list[tuple[float, bool, float]] = []
    true_positive = false_positive = true_negative = false_negative = 0.0
    brier = 0.0
    correct = 0.0
    for raw, outcome, item_weight in records:
        item_weight = max(0.0, float(item_weight))
        probability = _calibrate_binary_probability(
            raw, scale=scale, offset=offset
        )
        calibrated_records.append((probability, bool(outcome), item_weight))
        actual = bool(outcome)
        predicted = probability >= 0.5
        brier += item_weight * (probability - float(actual)) ** 2
        correct += item_weight * (predicted == actual)
        if predicted and actual:
            true_positive += item_weight
        elif predicted:
            false_positive += item_weight
        elif actual:
            false_negative += item_weight
        else:
            true_negative += item_weight
    brier /= weight
    prevalence = positive / weight
    climatology_brier = prevalence * (1.0 - prevalence)
    recall = true_positive / max(true_positive + false_negative, 1e-12)
    specificity = true_negative / max(
        true_negative + false_positive, 1e-12
    )
    decision_threshold, threshold_metrics = _recall_oriented_threshold(
        calibrated_records
    )
    return {
        "episode_weight": weight,
        "brier": brier,
        "climatology_brier": climatology_brier,
        "brier_skill": _brier_skill(brier, climatology_brier) or 0.0,
        "accuracy_at_0_5": correct / weight,
        "balanced_accuracy_at_0_5": 0.5 * (recall + specificity),
        "actual_positive_rate": prevalence,
        "decision_threshold": decision_threshold,
        **threshold_metrics,
    }


def _recall_oriented_threshold(
    records: Sequence[tuple[float, bool, float]],
) -> tuple[float, dict[str, float]]:
    """Choose a recall-oriented threshold before physical/value validation."""

    total_weight = sum(max(0.0, weight) for _, _, weight in records)
    positive_weight = sum(
        max(0.0, weight) for _, outcome, weight in records if outcome
    )
    if total_weight <= 0 or positive_weight <= 0:
        return 0.5, {
            "precision_at_decision_threshold": 0.0,
            "recall_at_decision_threshold": 0.0,
            "f2_at_decision_threshold": 0.0,
        }
    prevalence = positive_weight / total_weight
    precision_floor = min(0.5, max(0.25, prevalence * 1.5))
    thresholds = sorted(
        {0.0, 0.5, 1.0, *(float(probability) for probability, _, _ in records)}
    )

    candidates: list[tuple[float, float, float, float]] = []
    fallback: list[tuple[float, float, float, float]] = []
    for threshold in thresholds:
        true_positive = false_positive = false_negative = 0.0
        for probability, outcome, weight in records:
            weight = max(0.0, weight)
            predicted = probability >= threshold
            if predicted and outcome:
                true_positive += weight
            elif predicted:
                false_positive += weight
            elif outcome:
                false_negative += weight
        precision = true_positive / max(true_positive + false_positive, 1e-12)
        recall = true_positive / max(true_positive + false_negative, 1e-12)
        f2 = 5.0 * precision * recall / max(4.0 * precision + recall, 1e-12)
        item = (f2, recall, precision, threshold)
        fallback.append(item)
        if precision >= precision_floor:
            candidates.append(item)
    f2, recall, precision, threshold = max(
        candidates or fallback,
        key=lambda item: (item[0], item[1], item[2], item[3]),
    )
    return threshold, {
        "precision_at_decision_threshold": precision,
        "recall_at_decision_threshold": recall,
        "f2_at_decision_threshold": f2,
        "decision_precision_floor": precision_floor,
    }


def _raw_interval(
    distribution: EmpiricalDistribution, target_coverage: float
) -> tuple[float, float]:
    tail = (1.0 - target_coverage) / 2.0
    return distribution.quantile(tail), distribution.quantile(1.0 - tail)


def _calibrated_interval(
    distribution: EmpiricalDistribution,
    *,
    target_coverage: float,
    slack: float,
) -> tuple[float, float]:
    lower, upper = _raw_interval(distribution, target_coverage)
    return max(0.0, lower - slack), upper + slack


def _finite_sample_quantile(values: Sequence[float], coverage: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    rank = math.ceil((len(ordered) + 1) * coverage)
    return ordered[min(len(ordered), max(1, rank)) - 1]


def _demand_feature_key(
    role: str, state: str, family: str, features: Mapping[str, Any]
) -> tuple[str, ...]:
    return (
        role,
        state,
        family,
        f"context:{_power_two_bucket(int(features.get('current_sequence_tokens') or features.get('context_tokens') or 0))}",
        f"generated:{_power_two_bucket(int(features.get('generated_tokens') or features.get('observed_output_tokens') or 0))}",
        f"backend:{str(features.get('backend_class') or 'unknown')}",
    )


def _tool_feature_key(
    role: str, family: str, features: Mapping[str, Any]
) -> tuple[str, ...]:
    return (
        role,
        InvocationState.WAIT_TOOL.value,
        family,
        f"backend:{str(features.get('backend_class') or 'unknown')}",
        f"command:{str(features.get('command_class') or 'unknown')}",
        f"active:{_power_two_bucket(int(features.get('active_tool_count') or 0))}",
        f"context:{_power_two_bucket(int(features.get('current_sequence_tokens') or features.get('context_tokens') or 0))}",
        f"pressure:{str(features.get('backend_pressure') or 'unknown')}",
    )


def _backoff_keys(key: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    if len(key) == 8:
        role, state, family, backend, command, active, context, pressure = key
        return (
            key,
            (role, state, family, backend, command, active, context),
            (role, state, family, backend, command),
            (role, state, family, backend),
            (role, state, family),
            (role, state),
            ("*", state, family, backend, command),
            ("*", state, family, backend),
            ("*", state, family),
            ("*", state),
            ("*",),
        )
    if len(key) != 6:
        raise ValueError("unsupported hierarchical feature key")
    role, state, family, condition_a, condition_b, condition_c = key
    return (
        (role, state, family, condition_a, condition_b, condition_c),
        (role, state, family, condition_a, condition_b),
        (role, state, family),
        (role, state),
        ("*", state, family),
        ("*", state),
        ("*",),
    )


def _tool_support_detail(
    requested: tuple[str, ...],
    selected: tuple[str, ...],
    level: str,
) -> str:
    if level == "unavailable":
        return "unavailable"
    if selected == requested:
        return "exact"
    if selected == ("*",):
        return "global"
    if len(requested) != 8:
        return level
    role_scope = "role" if selected[0] != "*" else "cross_role"
    specificity = {
        7: "shape",
        5: "command",
        4: "backend",
        3: "family",
        2: "state",
    }.get(len(selected), "hierarchical")
    return f"{role_scope}_{specificity}_backoff"


def _power_two_bucket(value: int) -> int:
    result = 1
    while result < max(1, value):
        result *= 2
    return result


def _log_bucket(value: float) -> float:
    if value <= 0:
        return 0.0
    exponent = round(math.log2(value) * 4.0) / 4.0
    return round(2.0**exponent, 6)


def _required_prediction_heads_for_state(
    state: str, *, is_child: bool = False
) -> tuple[str, ...]:
    """Return only heads that can affect the next action from this state."""

    if state == InvocationState.RUNNING_LLM.value:
        return (
            ("remaining_decode_demand", "child_completion")
            if is_child
            else ("remaining_decode_demand",)
        )
    if state == InvocationState.READY.value:
        return ("next_output_demand", "prompt_growth")
    if state == InvocationState.WAIT_TOOL.value:
        return ("tool_wait", "prompt_growth")
    if state in {
        InvocationState.WAIT_JOIN.value,
        InvocationState.WAIT_CHILD.value,
    }:
        return ("join_dependency", "prompt_growth")
    if state == InvocationState.WAIT_MESSAGE.value:
        return ("message_dependency", "prompt_growth")
    return ()


def _action_head_requirements_for_state(
    state: str,
) -> tuple[tuple[str, str], ...]:
    """Expose availability only for heads consumed by each online action."""

    if state == InvocationState.RUNNING_LLM.value:
        return (("schedule", "remaining_decode_demand"),)
    if state == InvocationState.READY.value:
        return (
            ("admit", "next_output_demand"),
            ("admit", "prompt_growth"),
        )
    if state == InvocationState.WAIT_TOOL.value:
        return (
            ("prepare_host", "tool_wait"),
            ("prefetch_gpu", "tool_wait"),
            ("prefetch_gpu", "prompt_growth"),
        )
    if state in {
        InvocationState.WAIT_JOIN.value,
        InvocationState.WAIT_CHILD.value,
    }:
        return (
            ("prepare_host", "join_dependency"),
            ("prefetch_gpu", "join_dependency"),
            ("prefetch_gpu", "prompt_growth"),
        )
    if state == InvocationState.WAIT_MESSAGE.value:
        return (
            ("prepare_host", "message_dependency"),
            ("prefetch_gpu", "message_dependency"),
            ("prefetch_gpu", "prompt_growth"),
        )
    return ()


def _normalize_boundary(value: Any) -> str | None:
    normalized = str(value or "").lower()
    mapping = {
        "function_call": BoundaryEvent.TOOL.value,
        "tool": BoundaryEvent.TOOL.value,
        "spawn": BoundaryEvent.SPAWN.value,
        "handoff": BoundaryEvent.HANDOFF.value,
        "message": BoundaryEvent.MESSAGE.value,
        "return": BoundaryEvent.RETURN.value,
        "final": BoundaryEvent.FINAL.value,
        "final_answer": BoundaryEvent.FINAL.value,
    }
    return mapping.get(normalized)


def _normalize_tool_terminal(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"success", "ok", "completed"}:
        return "success"
    if normalized in {
        "censored",
        "cancelled",
        "canceled",
        "timeout",
        "aborted",
        "duplicate_suppressed",
        "recursion_limit",
    }:
        return "censored"
    return "error"


def _sample_raw_category(
    distribution: Mapping[str, float], quantile: float, *, default: str
) -> str:
    cumulative = 0.0
    for key, probability in sorted(distribution.items()):
        cumulative += probability
        if quantile <= cumulative:
            return str(key)
    return default


def _sample_categorical(distribution: Mapping[str, float], quantile: float) -> str:
    cumulative = 0.0
    for key, probability in sorted(distribution.items()):
        cumulative += probability
        if quantile <= cumulative:
            return _normalize_boundary(key) or BoundaryEvent.UNKNOWN.value
    return BoundaryEvent.UNKNOWN.value


def _action_projection_vector(
    outcomes: tuple[FrontierDemandOutcome, ...],
    *,
    projection: ScenarioProjection,
    target_invocation_id: str,
) -> tuple[float, ...]:
    by_id = {item.invocation_id: item for item in outcomes}
    reentry_cache: dict[str, float] = {}
    target = by_id[target_invocation_id]
    target_wait = _external_reentry_proxy_ms(
        target_invocation_id,
        by_id,
        set(),
        reentry_cache,
    )
    target_gpu_demand = float(
        target.remaining_decode_tokens
        + target.prompt_growth_tokens
        + target.next_output_tokens
    )
    aggregate_growth = float(
        sum(
            item.remaining_decode_tokens
            + item.prompt_growth_tokens
            + item.next_output_tokens
            for item in outcomes
        )
    )
    pressure_arrival = min(
        (
            _external_reentry_proxy_ms(
                item.invocation_id,
                by_id,
                set(),
                reentry_cache,
            )
            for item in outcomes
            if item.invocation_id != target_invocation_id
        ),
        default=target_wait,
    )
    if projection == ScenarioProjection.PREFETCH:
        return (
            target_wait,
            float(target.prompt_growth_tokens),
            aggregate_growth,
            target_gpu_demand,
            float(target.current_sequence_tokens),
        )
    if projection == ScenarioProjection.PREPARE_HOST:
        return (
            target_wait,
            pressure_arrival,
            aggregate_growth,
            float(target.current_sequence_tokens),
        )
    raise ValueError(f"unsupported action projection: {projection.value}")


def _external_reentry_proxy_ms(
    invocation_id: str,
    outcomes: Mapping[str, FrontierDemandOutcome],
    visiting: set[str],
    cache: dict[str, float] | None = None,
) -> float:
    if cache is not None and invocation_id in cache:
        return cache[invocation_id]
    if invocation_id in visiting:
        return 0.0
    outcome = outcomes[invocation_id]
    if outcome.dependency_mode == DependencyMode.EXTERNAL:
        value = max(
            outcome.completion_floor_ms,
            sum(item.residual_delay_ms for item in outcome.external_segments),
        )
        if cache is not None:
            cache[invocation_id] = value
        return value
    dependencies = tuple(
        item
        for item in outcome.dependency_invocation_ids
        if item in outcomes
    )
    if not dependencies:
        value = outcome.completion_floor_ms
        if cache is not None:
            cache[invocation_id] = value
        return value
    nested_visiting = {*visiting, invocation_id}
    values = tuple(
        _external_reentry_proxy_ms(item, outcomes, nested_visiting, cache)
        for item in dependencies
    )
    if outcome.dependency_mode == DependencyMode.JOIN_ALL:
        value = max(values)
    elif outcome.dependency_mode in {
        DependencyMode.JOIN_ANY,
        DependencyMode.PRODUCER,
    }:
        value = min(values)
    else:
        value = 0.0
    value = max(value, outcome.completion_floor_ms)
    if cache is not None:
        cache[invocation_id] = value
    return value


def _deterministic_medoid_clusters(
    vectors: tuple[tuple[float, ...], ...],
    max_clusters: int,
) -> tuple[tuple[int, tuple[int, ...]], ...]:
    """Bounded deterministic medoids for equal-mass action particles.

    Exact pairwise k-medoids is quadratic in the particle count. Action
    projection only needs stable representatives plus a conservative member
    envelope, so each refinement selects the observed vector nearest the
    component-wise median of its assigned cluster.
    """

    if not vectors:
        return ()
    dimensions = len(vectors[0])
    if any(len(item) != dimensions for item in vectors):
        raise ValueError("projected scenario vectors must have equal dimensions")
    minima = tuple(min(item[index] for item in vectors) for index in range(dimensions))
    maxima = tuple(max(item[index] for item in vectors) for index in range(dimensions))
    normalized = tuple(
        tuple(
            0.0
            if maxima[index] <= minima[index]
            else (value - minima[index]) / (maxima[index] - minima[index])
            for index, value in enumerate(item)
        )
        for item in vectors
    )
    unique_count = len(set(normalized))
    cluster_count = min(max_clusters, max(1, unique_count), len(vectors))
    ordered = sorted(range(len(vectors)), key=lambda item: (normalized[item], item))
    medoids = [
        ordered[min(len(ordered) - 1, ((2 * index + 1) * len(ordered)) // (2 * cluster_count))]
        for index in range(cluster_count)
    ]
    medoids = list(dict.fromkeys(medoids))

    def distance_to_vector(left: int, right: tuple[float, ...]) -> float:
        return sum(
            abs(a - b)
            for a, b in zip(normalized[left], right, strict=True)
        )

    assignments: dict[int, list[int]] = {}
    for _ in range(4):
        assignments = {item: [] for item in medoids}
        for particle_index in range(len(vectors)):
            selected = min(
                medoids,
                key=lambda item: (
                    distance_to_vector(particle_index, normalized[item]),
                    item,
                ),
            )
            assignments[selected].append(particle_index)
        updated = []
        for medoid in medoids:
            members = assignments[medoid]
            center = tuple(
                sorted(normalized[item][dimension] for item in members)[
                    len(members) // 2
                ]
                for dimension in range(dimensions)
            )
            updated.append(
                min(
                    members,
                    key=lambda candidate: (
                        distance_to_vector(candidate, center),
                        candidate,
                    ),
                )
            )
        updated = list(dict.fromkeys(updated))
        if updated == medoids:
            break
        medoids = updated

    assignments = {item: [] for item in medoids}
    for particle_index in range(len(vectors)):
        selected = min(
            medoids,
            key=lambda item: (
                distance_to_vector(particle_index, normalized[item]),
                item,
            ),
        )
        assignments[selected].append(particle_index)
    return tuple(
        (medoid, tuple(assignments[medoid]))
        for medoid in sorted(medoids, key=lambda item: (normalized[item], item))
    )


def _conservative_cluster_outcomes(
    medoid: tuple[FrontierDemandOutcome, ...],
    members: tuple[tuple[FrontierDemandOutcome, ...], ...],
) -> tuple[FrontierDemandOutcome, ...]:
    by_member = tuple(
        {item.invocation_id: item for item in outcomes}
        for outcomes in members
    )
    conservative = []
    for base in medoid:
        variants = tuple(item[base.invocation_id] for item in by_member)
        segments = tuple(
            replace(
                segment,
                residual_delay_ms=min(
                    variant.external_segments[index].residual_delay_ms
                    for variant in variants
                    if len(variant.external_segments) > index
                ),
            )
            for index, segment in enumerate(base.external_segments)
        )
        conservative.append(
            replace(
                base,
                current_sequence_tokens=max(
                    item.current_sequence_tokens for item in variants
                ),
                remaining_decode_tokens=max(
                    item.remaining_decode_tokens for item in variants
                ),
                prompt_growth_tokens=max(
                    item.prompt_growth_tokens for item in variants
                ),
                next_output_tokens=max(item.next_output_tokens for item in variants),
                completion_floor_ms=min(
                    item.completion_floor_ms for item in variants
                ),
                external_segments=segments,
            )
        )
    return tuple(conservative)


def _scenario_key(outcomes: Iterable[FrontierDemandOutcome]) -> tuple[Any, ...]:
    return tuple(
        (
            item.invocation_id,
            item.boundary_event.value,
            item.dependency_mode.value,
            item.phase.value,
            item.current_sequence_tokens,
            item.remaining_decode_tokens,
            item.prompt_growth_tokens,
            item.next_output_tokens,
            round(item.completion_floor_ms, 3),
            tuple(
                (
                    segment.segment_kind,
                    segment.service_family,
                    round(segment.residual_delay_ms, 3),
                    segment.terminal_status,
                )
                for segment in item.external_segments
            ),
            item.dependency_invocation_ids,
            item.join_id,
        )
        for item in outcomes
    )
