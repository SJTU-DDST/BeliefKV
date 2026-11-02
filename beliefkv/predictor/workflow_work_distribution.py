"""Observed R5 features and explicit support for conditional work distributions."""

from dataclasses import dataclass, field
import math

from beliefkv.core.events import (
    EventConfidence, RelationType, RuntimeEventKind as Kind, is_unobserved_join_event,
)
from beliefkv.predictor.workflow_rank_input import WorkflowRankInput
from beliefkv.predictor.workflow_work import R5_FEATURES, R5_SCHEMA, WorkflowProgress


CONTRACT_KIND = "conditional_remaining_logical_model_work_v1"
STRUCTURAL_EVENTS = frozenset((
    Kind.INVOCATION_CREATE, Kind.JOIN_CREATE, Kind.JOIN_WAIT,
    Kind.JOIN_SATISFIED, Kind.RETURN, Kind.INVOCATION_CANCEL,
))
SNAPSHOT_EVENTS = STRUCTURAL_EVENTS | frozenset((
    Kind.WORKFLOW_START, Kind.LLM_SUBMIT, Kind.LLM_RESULT, Kind.TOOL_START, Kind.TOOL_END,
))


def role_class(role):
    role = role.lower()
    if any(word in role for word in ("explor", "research", "analyst", "investigat")):
        return "explore"
    if any(word in role for word in ("verif", "validat", "test")):
        return "verify"
    if any(word in role for word in ("review", "critic", "compatib")):
        return "review"
    if any(word in role for word in ("implement", "develop", "coder", "engineer")):
        return "implement"
    return "other"


@dataclass
class DistributionProgress(WorkflowProgress):
    roles: dict = field(default_factory=dict)
    charged_inputs: dict = field(default_factory=dict)
    summary_inputs: dict = field(default_factory=dict)
    blocked_joins: dict = field(default_factory=dict)

    def _blocked_state(self, invocation):
        if self.blocked_joins.get(invocation):
            self._state(invocation, "waiting_join")
        elif any(owner == invocation for owner, _tool in self.tools):
            self._state(invocation, "tools")

    def started(self, request, invocation, context, prompt):
        super().started(request, invocation, context, prompt)
        self._blocked_state(invocation)

    def event(self, event):
        if is_unobserved_join_event(event) or event.confidence is EventConfidence.INFERRED:
            return
        fresh = event.kind is Kind.INVOCATION_CREATE and event.invocation_id not in self.invocations
        super().event(event)
        if event.kind is Kind.JOIN_WAIT:
            self.blocked_joins.setdefault(event.invocation_id, set()).add(event.join_id)
        elif event.kind is Kind.JOIN_SATISFIED:
            self.blocked_joins.get(event.invocation_id, set()).discard(event.join_id)
            pending = any(call[0] == event.invocation_id for call in self.active_calls.values())
            self._state(event.invocation_id, "ready" if pending else "idle")
        elif event.kind in (Kind.RETURN, Kind.INVOCATION_CANCEL):
            self.blocked_joins.pop(event.invocation_id, None)
        elif event.kind is Kind.TOOL_END:
            pending = any(call[0] == event.invocation_id for call in self.active_calls.values())
            self._state(event.invocation_id, "ready" if pending else "idle")
        self._blocked_state(event.invocation_id)
        if event.kind is Kind.WORKFLOW_START:
            raw = event.attributes.get("workflow_ranking_input")
            if raw is not None:
                metadata = WorkflowRankInput.from_dict(raw)
                self.text = metadata.task_instructions
        if fresh and event.relation_type is not RelationType.ROOT:
            raw = event.attributes.get("workflow_ranking_input")
            metadata = WorkflowRankInput.from_dict(raw) if raw is not None else None
            role = metadata.agent_role if metadata is not None else (event.agent_definition_id or "")
            self.roles[event.invocation_id] = role_class(role)
            self.counts["role_" + self.roles[event.invocation_id]] += 1
            if metadata is not None:
                self.counts["delegation_chars"] += len(metadata.delegation_instructions)

    def generated(self, request, count):
        call = self.active_calls.get(request)
        super().generated(request, count)
        if call is not None:
            if count and request not in self.charged_inputs:
                import numpy as np

                if request in self.summary_inputs:
                    necessary = self.summary_inputs[request]
                else:
                    current = np.asarray(call[2], dtype=np.int64)
                    previous = np.asarray(self.histories.get(call[1], ()), dtype=np.int64)
                    size = min(len(current), len(previous))
                    common = size
                    if size:
                        changed = current[:size] != previous[:size]
                        if changed.any():
                            common = int(changed.argmax())
                    necessary = len(current) - common
                self.charged_inputs[request] = necessary
                self.counts["input_tokens"] += necessary
                self.counts[
                    "root_input_tokens" if call[0] == self.root else "child_input_tokens"
                ] += necessary
            self.counts[
                "root_output_tokens" if call[0] == self.root else "child_output_tokens"
            ] += count - call[3]

    def completed(self, request, invocation, context, prompt, output):
        import numpy as np

        if request in self.seen_calls:
            return 0
        self.started(request, invocation, context, prompt)
        self.generated(request, len(output))
        if not output:
            raise ValueError("R5 completed model service requires observed output")
        necessary = self.charged_inputs.pop(request)
        self.histories[context] = np.concatenate((
            np.asarray(prompt, dtype=np.int64), np.asarray(output, dtype=np.int64),
        ))
        self.active_calls.pop(request)
        self.seen_calls.add(request)
        self.counts["completed_calls"] += 1
        owner = "root" if invocation == self.root else "child"
        self.counts[owner + "_completed_calls"] += 1
        self._state(invocation, "idle")
        return necessary

    def started_summary(self, request, invocation, context, prompt_length, necessary):
        if any(type(value) is not int or value < 0 for value in (prompt_length, necessary)) or necessary > prompt_length:
            raise ValueError("invalid captured logical input")
        self.summary_inputs[request] = necessary
        self.started(request, invocation, context, range(prompt_length))

    def completed_summary(self, request, invocation, context, prompt_length, output_length, necessary):
        """Offline-only exact logical increments from the source-bound label artifact."""
        if request in self.seen_calls:
            raise ValueError("duplicate completed label request")
        if any(type(value) is not int or value < 0 for value in (prompt_length, output_length, necessary)):
            raise ValueError("invalid captured logical work")
        if necessary > prompt_length:
            raise ValueError("logical input increment exceeds captured input")
        self.started_summary(request, invocation, context, prompt_length, necessary)
        self.generated(request, output_length)
        if self.charged_inputs.pop(request, None) != necessary:
            raise ValueError("source input increment differs from observed logical work")
        self.summary_inputs.pop(request, None)
        self.active_calls.pop(request)
        self.seen_calls.add(request)
        self.counts["completed_calls"] += 1
        owner = "root" if invocation == self.root else "child"
        self.counts[owner + "_completed_calls"] += 1
        self._state(invocation, "idle")

    def features(self, *, feature_schema=R5_SCHEMA):
        values = dict(zip(R5_FEATURES, super().features(feature_schema=R5_SCHEMA)))
        for identity, (state, _depth) in self.invocations.items():
            if identity == self.root or state == "done":
                continue
            values["unfinished_children"] += 1
            name = {
                "ready": "ready_children", "running": "ready_children",
                "tools": "tool_blocked_children", "waiting_join": "join_blocked_children",
            }.get(state)
            if name:
                values[name] += 1
        if feature_schema != R5_SCHEMA:
            raise ValueError("conditional work features require their separate R5 schema")
        return tuple(values[name] for name in R5_FEATURES)


def training_contract(rows):
    import numpy as np

    values = np.log1p(np.asarray([row["features"] for row in rows], dtype=float))
    return {
        "kind": CONTRACT_KIND, "target": "remaining_logical_model_work",
        "minimum_refresh_s": 1., "maximum_cache_age_s": 3.,
        "maximum_observation_lag_s": 2., "minimum_task_coverage": .2,
        "feature_log_min": values.min(axis=0).tolist(),
        "feature_log_max": values.max(axis=0).tolist(),
        "feature_log_margin": math.log(2.),
        "minimum_class_workflows": 2, "neutral_weight": .5,
        "full_prompt_features_allowed": False, "future_tools_are_work_labels": False,
    }


def validate_contract(contract):
    if not isinstance(contract, dict) or contract.get("kind") != CONTRACT_KIND or (
        contract.get("target") != "remaining_logical_model_work"
        or contract.get("full_prompt_features_allowed") is not False
        or contract.get("future_tools_are_work_labels") is not False
        or contract.get("neutral_weight") != .5
        or contract.get("minimum_class_workflows") != 2
    ):
        raise ValueError("R5 requires its explicit conditional-work support contract")
    for name in (
        "minimum_refresh_s", "maximum_cache_age_s", "maximum_observation_lag_s",
        "minimum_task_coverage", "feature_log_margin",
    ):
        value = contract.get(name)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("invalid R5 distribution refresh/support limits")
    if not 1 <= contract["minimum_refresh_s"] <= 10 or (
        contract["maximum_cache_age_s"] < contract["minimum_refresh_s"]
        or contract["minimum_task_coverage"] > 1
    ):
        raise ValueError("inconsistent R5 distribution refresh/support limits")
    bounds = [contract.get(name) for name in ("feature_log_min", "feature_log_max")]
    if any(not isinstance(values, list) or len(values) != len(R5_FEATURES) for values in bounds):
        raise ValueError("R5 distribution feature support dimensions differ")
    if any(
        type(low) not in (int, float) or type(high) not in (int, float)
        or not math.isfinite(low) or not math.isfinite(high) or not 0 <= low <= high
        for low, high in zip(*bounds)
    ):
        raise ValueError("invalid R5 distribution feature support")
    return dict(contract)


def support_status(model, features, task_coverage):
    contract, metadata = model.distribution_contract, model.metadata
    if metadata.get("decision_eligible") is not True or metadata.get("calibration_status") not in (
        "project_heldout_temperature", "capture_heldout_temperature", "workflow_heldout_temperature",
    ):
        return "calibration_not_eligible"
    support = metadata.get("calibration_class_workflows", ())
    if len(support) != 3 or min(support) < contract["minimum_class_workflows"]:
        return "missing_class_execution_support"
    if task_coverage < contract["minimum_task_coverage"]:
        return "task_vocabulary_unsupported"
    if any(
        not low - contract["feature_log_margin"] <= math.log1p(value) <= high + contract["feature_log_margin"]
        for value, low, high in zip(features, contract["feature_log_min"], contract["feature_log_max"])
    ):
        return "observed_progress_outside_training_support"
    return "calibrated_distribution"
