"""Read-only completion-intent to child RETURN timing, separate from long JOIN."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CompletionLead:
    p10_ms: float
    p50_ms: float
    p90_ms: float
    training_children: int

    @classmethod
    def fit(cls, records: Iterable[Mapping[str, Any]]) -> "CompletionLead":
        values = sorted(
            float(record["lead_ms"])
            for record in records if record.get("returned") is True
            and record.get("lead_ms") is not None
        )
        if not values or any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("completion lead requires finite observed RETURN labels")

        def quantile(q: float) -> float:
            return values[math.ceil(q * len(values)) - 1]

        return cls(quantile(.1), quantile(.5), quantile(.9), len(values))

    def to_dict(self) -> dict[str, float | int]:
        return {
            "p10_ms": self.p10_ms, "p50_ms": self.p50_ms,
            "p90_ms": self.p90_ms, "training_children": self.training_children,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CompletionLead":
        model = cls(
            float(raw["p10_ms"]), float(raw["p50_ms"]),
            float(raw["p90_ms"]), int(raw["training_children"]),
        )
        if (
            not 0 <= model.p10_ms <= model.p50_ms <= model.p90_ms
            or not all(
                math.isfinite(value) for value in
                (model.p10_ms, model.p50_ms, model.p90_ms)
            )
            or model.training_children < 1
        ):
            raise ValueError("invalid completion lead")
        return model


def load_pinned_completion_lead(
    path: str | Path, expected_sha256: str
) -> CompletionLead:
    if (
        len(expected_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_sha256)
    ):
        raise ValueError("completion lead requires a lowercase SHA-256")
    payload = Path(path).read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("completion lead SHA-256 mismatch")
    artifact = json.loads(payload)
    if artifact.get("status") != "offline_conditional_signal_diagnostic_only":
        raise ValueError("completion lead must be a diagnostic-only artifact")
    return CompletionLead.from_dict(artifact["model"])


def completion_signal_records(
    reentries: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Audit all spawned children; fit only completed, eligible JOIN members."""
    joined_children = {}
    last_children = set()
    for entry in reentries:
        if (
            entry.get("reentry_kind") != "join"
            or entry.get("terminal_status") != "satisfied"
            or entry.get("training_eligible") is not True
        ):
            continue
        members = entry.get("member_outcomes") or ()
        if not members or any(
            member.get("return_ts_ms") is None for member in members
        ):
            continue
        workflow = str(entry.get("workflow_id") or "")
        last = max(members, key=lambda member: float(member["return_ts_ms"]))
        last_children.add((workflow, str(last["invocation_id"])))
        for member in members:
            joined_children[(workflow, str(member["invocation_id"]))] = (
                float(member["return_ts_ms"])
            )
    by_child: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    spawned = set()
    for event in events:
        key = (
            str(event.get("workflow_id") or ""),
            str(event.get("invocation_id") or ""),
        )
        if event.get("kind") == "invocation_create" and event.get(
            "relation_type"
        ) == "spawn":
            spawned.add(key)
        if key[0] and key[1]:
            by_child[key].append(event)

    records = []
    for child in spawned:
        child_events = by_child[child]
        child_events.sort(key=lambda event: float(event.get("ts_ms") or 0))
        for index, event in enumerate(child_events):
            if event.get("kind") != "llm_result":
                continue
            attrs = event.get("attributes") or {}
            if (
                attrs.get("runtime_internal") is True
                or attrs.get("invalid_tool_call_count", 0) != 0
                or attrs.get("finish_reason") not in (None, "stop")
            ):
                continue
            names = attrs.get("structured_action_names")
            if names != ["ChildCompletion"] and not (
                attrs.get("tool_call_count") == 0
                and not names
                and isinstance(attrs.get("output_chars"), int)
                and attrs["output_chars"] > 0
            ):
                continue
            next_event = next((
                later for later in child_events[index + 1:]
                if later.get("kind") in {
                    "llm_submit", "tool_start", "return", "invocation_cancel"
                }
            ), None)
            eligible = child in joined_children
            actual_return = bool(
                next_event is not None and next_event.get("kind") == "return"
            )
            returned = bool(
                eligible and actual_return
                and math.isclose(
                    float(next_event["ts_ms"]), joined_children[child], abs_tol=1
                )
            )
            lead_ms = (
                float(next_event["ts_ms"]) - float(event["ts_ms"])
                if returned else None
            )
            records.append({
                "workflow_id": child[0],
                "child_id": child[1],
                "last_child": child in last_children,
                "returned": returned,
                "lead_ms": lead_ms,
                "eligible": eligible,
                "next_event_kind": (
                    next_event.get("kind") if next_event is not None else None
                ),
            })
    return records, {
        "joined_child_count": len(joined_children),
        "spawn_count_in_trace": len(spawned),
        "last_child_count": len(last_children),
        "signal_count": len(records),
        "confirmed_return_signals": sum(record["returned"] for record in records),
        "false_or_unknown_signals": sum(not record["returned"] for record in records),
        "confirmed_nonreturn_signals": sum(
            record["next_event_kind"] in {
                "llm_submit", "tool_start", "invocation_cancel"
            } for record in records
        ),
        "unlabeled_signals": sum(
            record["next_event_kind"] is None for record in records
        ),
        "ineligible_return_signals": sum(
            record["next_event_kind"] == "return" and not record["eligible"]
            for record in records
        ),
    }


def evaluate_completion_lead(
    model: CompletionLead, records: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    values = [
        float(record["lead_ms"]) for record in records
        if record.get("returned") is True and record.get("last_child") is True
        and record.get("lead_ms") is not None
    ]
    errors = sorted(abs(value - model.p50_ms) for value in values)
    return {
        "last_child_signals": len(values),
        "median_absolute_error_ms": errors[(len(errors) - 1) // 2]
        if errors else None,
        "p90_absolute_error_ms": errors[math.ceil(.9 * len(errors)) - 1]
        if errors else None,
        "within_500ms_rate": sum(error <= 500 for error in errors) / len(errors)
        if errors else None,
        "p10_p90_coverage": sum(
            model.p10_ms <= value <= model.p90_ms for value in values
        ) / len(values) if values else None,
        "lead_at_least_500ms": sum(value >= 500 for value in values),
    }
