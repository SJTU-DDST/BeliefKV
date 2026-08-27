from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ACTION_TARGET_SCHEMA_VERSION = 4


@dataclass(frozen=True)
class TransferAnchor:
    direction: str
    actual_bytes: int
    duration_p95_ms: float
    extent_count: int
    source_run: str

    def __post_init__(self) -> None:
        if self.direction not in {"d2h", "h2d"}:
            raise ValueError("transfer anchor direction must be d2h or h2d")
        if self.actual_bytes <= 0 or self.extent_count <= 0:
            raise ValueError("transfer anchor shape must be positive")
        if not math.isfinite(self.duration_p95_ms) or self.duration_p95_ms <= 0:
            raise ValueError("transfer anchor duration must be positive")
        if not self.source_run:
            raise ValueError("transfer anchor source run is required")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TransferAnchor":
        return cls(
            direction=str(raw["direction"]),
            actual_bytes=int(raw["actual_bytes"]),
            duration_p95_ms=float(raw["duration_p95_ms"]),
            extent_count=int(raw["extent_count"]),
            source_run=str(raw["source_run"]),
        )


@dataclass(frozen=True)
class OperationalActionTargetContract:
    contract_id: str
    deployment_profile_id: str
    kv_bytes_per_token: int
    commit_guard_ms: float
    anchors: tuple[TransferAnchor, ...]
    minimum_transfer_ms: float = 1.0

    def __post_init__(self) -> None:
        if not self.contract_id or not self.deployment_profile_id:
            raise ValueError("action-target contract identity is required")
        if self.kv_bytes_per_token <= 0:
            raise ValueError("kv_bytes_per_token must be positive")
        if min(self.commit_guard_ms, self.minimum_transfer_ms) < 0:
            raise ValueError("action-target timing must be non-negative")
        if {item.direction for item in self.anchors} != {"d2h", "h2d"}:
            raise ValueError("action-target contract requires D2H and H2D anchors")

    @classmethod
    def load(cls, path: str | Path) -> "OperationalActionTargetContract":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if int(raw.get("schema_version", -1)) != ACTION_TARGET_SCHEMA_VERSION:
            raise ValueError("unsupported action-target contract schema")
        return cls(
            contract_id=str(raw["contract_id"]),
            deployment_profile_id=str(raw["deployment_profile_id"]),
            kv_bytes_per_token=int(raw["kv_bytes_per_token"]),
            commit_guard_ms=float(raw["commit_guard_ms"]),
            minimum_transfer_ms=float(raw.get("minimum_transfer_ms", 1.0)),
            anchors=tuple(
                TransferAnchor.from_dict(item) for item in raw.get("anchors", ())
            ),
        )

    def estimate_p95_ms(self, direction: str, actual_bytes: int) -> float:
        if actual_bytes <= 0:
            raise ValueError("operational tau requires positive KV bytes")
        candidates = [item for item in self.anchors if item.direction == direction]
        if not candidates:
            raise ValueError(f"no {direction} transfer anchor")
        anchor = min(
            candidates,
            key=lambda item: abs(math.log(actual_bytes / item.actual_bytes)),
        )
        return max(
            self.minimum_transfer_ms,
            anchor.duration_p95_ms * actual_bytes / anchor.actual_bytes,
        )


def build_action_target_rows(
    decision_rows: Sequence[Mapping[str, Any]],
    external_wait_rows: Sequence[Mapping[str, Any]],
    contract: OperationalActionTargetContract,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build action-aligned labels without mutating frozen semantic rows.

    A WAIT_TOOL release is the completion of every tool call active for the
    invocation at the decision timestamp. This avoids treating parallel tool
    calls as independent parent reentry events.
    """

    waits_by_invocation: defaultdict[tuple[str, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for raw in external_wait_rows:
        row = dict(raw)
        workflow_id = str(row.get("workflow_id") or "")
        invocation_id = str(row.get("invocation_id") or "")
        start = row.get("start_ts_ms")
        if not workflow_id or not invocation_id or start is None:
            continue
        terminal = row.get("terminal_ts_ms")
        observed = row.get("observed_duration_ms")
        censor_ts = (
            float(start) + float(observed)
            if terminal is None and observed is not None
            else None
        )
        row["_terminal_ts_ms"] = (
            float(terminal) if terminal is not None else None
        )
        row["_censor_ts_ms"] = censor_ts
        row["_effective_end_ts_ms"] = (
            float(terminal) if terminal is not None else censor_ts
        )
        waits_by_invocation[(workflow_id, invocation_id)].append(row)
    for waits in waits_by_invocation.values():
        waits.sort(key=lambda item: float(item["start_ts_ms"]))

    latest_context_tokens: dict[tuple[str, str], int] = {}
    output: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    support_by_command: defaultdict[str, Counter[str]] = defaultdict(Counter)
    tau_values: defaultdict[str, list[float]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()

    for raw_row in decision_rows:
        row = dict(raw_row)
        workflow_id = str(row.get("workflow_id") or "")
        decision_id = str(row.get("decision_id") or "")
        timestamp_ms = float(row.get("timestamp_ms") or 0.0)
        labels = {
            str(item.get("invocation_id") or ""): item
            for item in row.get("labels", ())
        }
        for features in row.get("invocations", ()):
            invocation_id = str(features.get("invocation_id") or "")
            if not invocation_id:
                continue
            observed_tokens = int(
                features.get("current_sequence_tokens")
                or features.get("context_tokens")
                or 0
            )
            context_key = (workflow_id, invocation_id)
            if observed_tokens > 0:
                latest_context_tokens[context_key] = observed_tokens
            if str(features.get("state") or "") != "wait_tool":
                continue
            label = labels.get(invocation_id)
            if label is None:
                continue
            eligibility = label.get("target_training_eligible") or {}
            if not bool(eligibility.get("external_wait", False)):
                counters["target_ineligible"] += 1
                continue
            identity = (decision_id, invocation_id)
            if identity in seen:
                raise ValueError(f"duplicate action-target identity: {identity}")
            seen.add(identity)
            active = [
                item
                for item in waits_by_invocation.get(context_key, ())
                if float(item["start_ts_ms"]) <= timestamp_ms
                and (
                    item.get("_effective_end_ts_ms") is None
                    or timestamp_ms <= float(item["_effective_end_ts_ms"]) + 1e-6
                )
            ]
            if not active:
                counters["active_tool_identity_unavailable"] += 1
                continue
            release_observed = all(
                item.get("_terminal_ts_ms") is not None for item in active
            )
            release_ts = (
                max(float(item["_terminal_ts_ms"]) for item in active)
                if release_observed
                else None
            )
            residual_wait_ms = (
                max(0.0, release_ts - timestamp_ms)
                if release_ts is not None
                else None
            )
            right_censored = any(bool(item.get("censored")) for item in active)
            context_tokens = latest_context_tokens.get(context_key, 0)
            if context_tokens <= 0:
                counters["context_tokens_unavailable"] += 1
                continue
            actual_bytes = context_tokens * contract.kv_bytes_per_token
            command_classes = sorted(
                {
                    str(
                        item.get("command_class")
                        or item.get("tool_name")
                        or item.get("backend_class")
                        or "unknown"
                    )
                    for item in active
                }
            )
            tool_families = sorted(
                {str(item.get("tool_family") or "unknown") for item in active}
            )
            backend_classes = sorted(
                {str(item.get("backend_class") or "unknown") for item in active}
            )
            command_class = (
                command_classes[0]
                if len(command_classes) == 1
                else "multi_tool"
            )
            tool_family = (
                tool_families[0] if len(tool_families) == 1 else "mixed"
            )
            backend_class = (
                backend_classes[0]
                if len(backend_classes) == 1
                else "mixed"
            )
            actions: dict[str, dict[str, Any]] = {}
            for action, direction, outcome_kind in (
                ("prepare_host", "d2h", "release_after_tau"),
                ("prefetch_gpu", "h2d", "release_within_tau"),
            ):
                transfer_ms = contract.estimate_p95_ms(direction, actual_bytes)
                tau_ms = transfer_ms + contract.commit_guard_ms
                tau_values[action].append(tau_ms)
                known = release_observed
                outcome = None
                if release_observed:
                    outcome = (
                        residual_wait_ms > tau_ms
                        if outcome_kind == "release_after_tau"
                        else residual_wait_ms <= tau_ms
                    )
                elif any(
                    item.get("_effective_end_ts_ms") is not None
                    and float(item["_effective_end_ts_ms"]) - timestamp_ms
                    > tau_ms
                    for item in active
                ):
                    # A tool survived past tau before censoring. That is enough
                    # to establish release-after=true/release-within=false.
                    known = True
                    outcome = outcome_kind == "release_after_tau"
                actions[action] = {
                    "direction": direction,
                    "transfer_p95_ms": transfer_ms,
                    "commit_guard_ms": contract.commit_guard_ms,
                    "operational_tau_ms": tau_ms,
                    "outcome_kind": outcome_kind,
                    "outcome_known": known,
                    "outcome": outcome,
                }
                counters[f"{action}_rows"] += 1
                counters[f"{action}_known"] += int(known)
                support_by_command[command_class][f"{action}_rows"] += 1
                support_by_command[command_class][f"{action}_known"] += int(known)
            output.append(
                {
                    "schema_version": ACTION_TARGET_SCHEMA_VERSION,
                    "row_type": "operational_action_target",
                    "contract_id": contract.contract_id,
                    "deployment_profile_id": contract.deployment_profile_id,
                    "decision_id": decision_id,
                    "workflow_id": workflow_id,
                    "instance_id": row.get("instance_id"),
                    "project": row.get("project"),
                    "split": row.get("split"),
                    "invocation_id": invocation_id,
                    "tool_wait_episode_id": "+".join(
                        sorted(str(item.get("tool_call_id") or "unknown") for item in active)
                    ),
                    "timestamp_ms": timestamp_ms,
                    "elapsed_wait_ms": max(
                        0.0,
                        timestamp_ms
                        - min(float(item["start_ts_ms"]) for item in active),
                    ),
                    "residual_wait_ms": residual_wait_ms,
                    "right_censored": right_censored,
                    "active_tool_count": len(active),
                    "tool_family": tool_family,
                    "backend_class": backend_class,
                    "command_class": command_class,
                    "agent_definition_id": str(
                        features.get("agent_definition_id") or "unknown"
                    ),
                    "boundary_history": list(features.get("boundary_history", ())),
                    "current_sequence_tokens": context_tokens,
                    "actual_kv_bytes": actual_bytes,
                    "tau_evidence": "byte_scaled_from_current_patch_anchor",
                    "physical_shape_available": False,
                    "actions": actions,
                }
            )
            counters["rows"] += 1
            counters["right_censored_rows"] += int(right_censored)
            counters["multi_tool_rows"] += int(len(active) > 1)

    report = {
        "schema_version": ACTION_TARGET_SCHEMA_VERSION,
        "contract_id": contract.contract_id,
        "deployment_profile_id": contract.deployment_profile_id,
        "counts": dict(sorted(counters.items())),
        "support_by_command_class": {
            name: dict(sorted(values.items()))
            for name, values in sorted(support_by_command.items())
        },
        "operational_tau_ms": {
            action: _distribution_summary(values)
            for action, values in sorted(tau_values.items())
        },
        "evidence_grade": (
            "estimated_from_current_patch_anchor; physical extent morphology is "
            "unavailable in the frozen semantic decision rows"
        ),
        "online_eligibility": False,
    }
    return output, report


def load_action_target_rows(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in paths:
        with Path(path).open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                if int(row.get("schema_version", -1)) != ACTION_TARGET_SCHEMA_VERSION:
                    raise ValueError("unsupported action-target row schema")
                identity = (
                    str(row.get("decision_id") or ""),
                    str(row.get("invocation_id") or ""),
                )
                if not all(identity) or identity in seen:
                    raise ValueError(f"duplicate or invalid action-target row: {identity}")
                seen.add(identity)
                rows.append(row)
    return rows


def _distribution_summary(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "p50": None, "p95": None, "max": None}
    ordered = sorted(float(item) for item in values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p50": ordered[(len(ordered) - 1) // 2],
        "p95": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "max": ordered[-1],
    }
