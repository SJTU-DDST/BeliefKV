"""Observed workflow progress and a portable, CPU-only remaining-work MLP."""

from __future__ import annotations

from array import array
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path

from beliefkv.core.events import (
    EventConfidence, RelationType, RuntimeEventKind as Kind, is_unobserved_join_event,
)


FEATURES = (
    "input_tokens", "output_tokens", "completed_calls", "tool_calls", "completed_tools",
    "children", "returned_children", "joins", "completed_joins", "max_fanout",
    "max_depth", "ready", "running", "tools", "waiting_join", "root_waiting_join",
    "completion_intents",
)
SCHEMA = "workflow_remaining_model_work_v1"
EXECUTION_INPUT_KIND = "task_plus_declared_execution_instructions_v1"
EXECUTION_CONTROL_FIELDS = frozenset((
    "profile", "minimum_parallel_join", "child_soft_target",
    "root_phase_target", "maximum_spawn_depth",
))


def execution_controls(values):
    """Only declared instructions are accepted, never measured future work."""
    from beliefkv.experiments.delegation_policy import SUBAGENT_FANOUT_PROFILES

    if not isinstance(values, dict) or values.keys() != EXECUTION_CONTROL_FIELDS:
        raise ValueError("workflow execution controls require exactly the declared fields")
    result = dict(values)
    if result["profile"] not in SUBAGENT_FANOUT_PROFILES:
        raise ValueError("unknown workflow execution profile")
    for name in ("child_soft_target", "root_phase_target"):
        value = result[name]
        if value == "not_configured":
            continue
        if (
            type(value) is not str or not value.isascii() or not value.isdigit()
            or len(value) > 5 or int(value) > 10000
        ):
            raise ValueError("invalid declared workflow soft budget")
        result[name] = str(int(value))
    for name in ("minimum_parallel_join", "maximum_spawn_depth"):
        value = result[name]
        if name == "minimum_parallel_join" and value == "not_required":
            continue
        if type(value) is not int or not 0 <= value <= 64:
            raise ValueError("invalid declared workflow delegation limit")
    return result


def execution_control_text(values):
    values = execution_controls(values)
    return "Declared execution instructions: " + "; ".join(
        f"{name}={value}" for name, value in sorted(values.items())
    ) + "\n\n"


def execution_controls_from_environment(environment):
    from beliefkv.experiments.delegation_policy import delegation_contract

    if environment is None:
        raise ValueError("workflow work model requires declared runtime execution controls")
    contract = delegation_contract(environment.get("FANOUT_PROFILE", ""))
    return execution_controls({
        "profile": contract["profile"],
        "minimum_parallel_join": contract.get("minimum_completed_root_join_members", "not_required"),
        "child_soft_target": environment.get("CHILD_WORK_BUDGET_TARGET_CALLS", "not_configured"),
        "root_phase_target": environment.get("ROOT_WORK_BUDGET_TARGET_CALLS", "not_configured"),
        "maximum_spawn_depth": contract["maximum_spawn_depth"],
    })


def task_requirements(text):
    """Extract the user task, never the large system repository context."""
    if "Task requirements:\n" in text:
        return text.split("Task requirements:\n", 1)[1].split(
            "\n\nThe complete initial project source", 1,
        )[0][:24000]
    return ""


def task_from_prompt_tail(tokens, tokenizer):
    if tokenizer is None:
        return ""
    text = tokenizer.decode(tokens, skip_special_tokens=False)
    marker = "<|im_start|>user\n"
    if marker not in text:
        return ""
    return task_requirements(text.rsplit(marker, 1)[1].split("<|im_end|>", 1)[0])


@dataclass
class WorkflowProgress:
    counts: Counter = field(default_factory=Counter)
    invocations: dict = field(default_factory=dict)
    tools: set = field(default_factory=set)
    joins: set = field(default_factory=set)
    completed_joins: set = field(default_factory=set)
    histories: dict = field(default_factory=dict)
    active_calls: dict = field(default_factory=dict)
    seen_calls: set = field(default_factory=set)
    root: str | None = None
    text: str = ""

    def event(self, event):
        if is_unobserved_join_event(event) or event.confidence is EventConfidence.INFERRED:
            return
        kind, identity = event.kind, event.invocation_id
        if kind is Kind.INVOCATION_CREATE and identity not in self.invocations:
            parent = self.invocations.get(event.parent_invocation_id)
            depth = 0 if parent is None else parent[1] + 1
            self.invocations[identity] = ("idle", depth)
            self.counts["max_depth"] = max(self.counts["max_depth"], depth)
            if event.relation_type is RelationType.ROOT:
                self.root = identity
            elif event.parent_invocation_id is not None:
                self.counts["children"] += 1
        elif kind is Kind.LLM_SUBMIT and identity in self.invocations:
            self._state(identity, "ready")
        elif kind is Kind.TOOL_START:
            tool = (identity, event.attributes.get("tool_run_id") or event.attributes.get("tool_call_id"))
            if tool not in self.tools:
                self.tools.add(tool)
                self.counts["tool_calls"] += 1
            self._state(identity, "tools")
        elif kind is Kind.TOOL_END:
            tool = (identity, event.attributes.get("tool_run_id") or event.attributes.get("tool_call_id"))
            if tool in self.tools:
                self.tools.remove(tool)
                self.counts["completed_tools"] += 1
            if not any(owner == identity for owner, _tool in self.tools):
                self._state(identity, "idle")
        elif kind is Kind.JOIN_CREATE and event.join_id not in self.joins:
            self.joins.add(event.join_id)
            self.counts["joins"] += 1
            self.counts["max_fanout"] = max(self.counts["max_fanout"], len(event.member_invocation_ids))
        elif kind is Kind.JOIN_WAIT:
            self._state(identity, "waiting_join")
        elif kind is Kind.JOIN_SATISFIED and event.join_id not in self.completed_joins:
            self.completed_joins.add(event.join_id)
            self.counts["completed_joins"] += 1
        elif kind in (Kind.RETURN, Kind.INVOCATION_CANCEL):
            item = self.invocations.get(identity)
            if item is not None and item[0] != "done":
                self.counts["returned_children"] += identity != self.root
                self._state(identity, "done")
        elif kind is Kind.STRUCTURED_ACTION and event.attributes.get("beliefkv_child_completion_intent") is True:
            self.counts["completion_intents"] += 1

    def _state(self, identity, state):
        if identity in self.invocations and self.invocations[identity][0] != "done":
            self.invocations[identity] = state, self.invocations[identity][1]

    def started(self, request, invocation, context, prompt):
        if request in self.seen_calls or request in self.active_calls:
            return
        self.active_calls[request] = invocation, context, prompt, 0
        self._state(invocation, "ready")

    def generated(self, request, count):
        call = self.active_calls.get(request)
        if call is None:
            return
        invocation, context, prompt, previous = call
        if count < previous:
            raise ValueError("observed logical output moved backwards")
        self.counts["output_tokens"] += count - previous
        self.active_calls[request] = invocation, context, prompt, count
        self._state(invocation, "running")

    def completed(self, request, invocation, context, prompt, output):
        import numpy as np

        if request in self.seen_calls:
            return 0
        self.started(request, invocation, context, prompt)
        self.generated(request, len(output))
        current = (
            np.asarray(prompt, dtype=np.int64) if isinstance(prompt, (array, np.ndarray, memoryview))
            else np.frombuffer(array("q", prompt), dtype=np.int64)
        )
        previous = np.asarray(self.histories.get(context, ()), dtype=np.int64)
        count = min(len(previous), len(current))
        common = count
        if count:
            different = previous[:count] != current[:count]
            if different.any():
                common = int(different.argmax())
        necessary = len(current) - common
        self.counts["input_tokens"] += necessary
        self.counts["completed_calls"] += 1
        # Keep an owned copy: native request buffers can outlive this callback.
        self.histories[context] = np.concatenate((current, np.asarray(output, dtype=np.int64)))
        self.active_calls.pop(request, None)
        self.seen_calls.add(request)
        self._state(invocation, "idle")
        return necessary

    def features(self):
        values = dict(self.counts)
        for state, _depth in self.invocations.values():
            if state in ("ready", "running", "tools", "waiting_join"):
                values[state] = values.get(state, 0) + 1
        values["root_waiting_join"] = int(
            self.root is not None and self.invocations[self.root][0] == "waiting_join",
        )
        return tuple(float(values.get(name, 0)) for name in FEATURES)


def ngrams(text):
    text = " ".join(text.lower().split())[:24000]
    return Counter(text[i:i + width] for width in (2, 3, 4) for i in range(len(text) - width + 1))


class WorkflowWorkModel:
    """JSON weights avoid pickle execution and runtime training dependencies."""

    def __init__(self, raw):
        import numpy as np

        if raw.get("schema") != SCHEMA or raw.get("features") != list(FEATURES):
            raise ValueError("incompatible workflow work model")
        self.raw = raw
        self.metadata = raw["metadata"]
        contract = self.metadata.get("task_input_contract")
        self.task_input_kind, self.deployment_controls = "task_requirements_v1", None
        if contract is not None:
            if (
                not isinstance(contract, dict)
                or contract.keys() != {"kind", "deployment_controls"}
                or contract["kind"] != EXECUTION_INPUT_KIND
            ):
                raise ValueError("unsupported workflow task input contract")
            self.task_input_kind = contract["kind"]
            self.deployment_controls = execution_controls(contract["deployment_controls"])
        self.thresholds = tuple(raw["work_thresholds"])
        if len(self.thresholds) != 2 or not 0 < self.thresholds[0] < self.thresholds[1]:
            raise ValueError("workflow work classes require two fixed positive thresholds")
        self.vocabulary = {word: i for i, word in enumerate(raw["vocabulary"])}
        if len(self.vocabulary) != len(raw["vocabulary"]) or not self.vocabulary:
            raise ValueError("invalid task vocabulary")
        self.arrays = {name: np.asarray(raw[name], dtype=np.float32) for name in (
            "idf", "center", "scale", "static_weight", "dynamic_weight", "bias",
            "hidden_weight", "hidden_bias", "output_weight", "output_bias",
        )}
        v, f = len(self.vocabulary), len(FEATURES)
        h, k = self.arrays["bias"].size, self.arrays["hidden_bias"].size
        shapes = {
            "idf": (v,), "center": (f,), "scale": (f,),
            "static_weight": (v, h), "dynamic_weight": (f, h), "bias": (h,),
            "hidden_weight": (h, k), "hidden_bias": (k,),
            "output_weight": (k, 3), "output_bias": (3,),
        }
        if any(self.arrays[name].shape != shape or not np.isfinite(self.arrays[name]).all()
               for name, shape in shapes.items()) or (self.arrays["scale"] <= 0).any():
            raise ValueError("invalid workflow model weight dimensions or values")
        self.temperature = float(raw.get("temperature", 1.))
        if not math.isfinite(self.temperature) or self.temperature <= 0:
            raise ValueError("workflow model temperature must be positive")

    @classmethod
    def load(cls, path, digest):
        content = Path(path).read_bytes()
        if len(digest) != 64 or hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("workflow work model fingerprint changed")
        return cls(json.loads(content))

    def task_projection(self, text):
        import numpy as np

        counts = ngrams(text)
        indices = [self.vocabulary[word] for word in counts if word in self.vocabulary]
        if not indices:
            return None
        values = np.asarray([1. + math.log(counts[word]) for word in counts if word in self.vocabulary])
        values *= self.arrays["idf"][indices]
        values /= max(float(np.linalg.norm(values)), 1e-12)
        return np.einsum("i,ij->j", values, self.arrays["static_weight"][indices], optimize=False)

    def task_input_prefix(self, environment=None):
        if self.deployment_controls is None:
            return ""
        controls = execution_controls_from_environment(environment)
        if controls != self.deployment_controls:
            raise ValueError("workflow work model and runtime execution controls differ")
        return execution_control_text(controls)

    def predict(self, projection, features):
        import numpy as np

        a = self.arrays
        features = np.asarray(features)
        if features.shape != (len(FEATURES),) or not np.isfinite(features).all() or (features < 0).any():
            raise ValueError("invalid observed workflow features")
        numeric = (np.log1p(features) - a["center"]) / a["scale"]
        hidden = np.maximum(0., projection + np.einsum(
            "i,ij->j", numeric, a["dynamic_weight"], optimize=False,
        ) + a["bias"])
        hidden = np.maximum(0., np.einsum(
            "i,ij->j", hidden, a["hidden_weight"], optimize=False,
        ) + a["hidden_bias"])
        logits = (np.einsum(
            "i,ij->j", hidden, a["output_weight"], optimize=False,
        ) + a["output_bias"]) / self.temperature
        probabilities = np.exp(logits - logits.max())
        probabilities /= probabilities.sum()
        return tuple(float(value) for value in probabilities)
