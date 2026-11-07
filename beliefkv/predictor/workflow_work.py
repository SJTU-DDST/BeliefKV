"""Observed workflow progress and a portable, CPU-only remaining-work MLP."""

from __future__ import annotations

from array import array
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import re

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
CONTEXT_SCHEMA = "workflow_remaining_model_work_context_v2"
CONTEXT_FEATURES = FEATURES + (
    "initial_prompt_tokens", "active_prompt_tokens", "max_prompt_tokens",
)
R5_SCHEMA = "workflow_conditional_remaining_work_distribution_v3"
R5_FEATURES = CONTEXT_FEATURES + (
    "root_completed_calls", "child_completed_calls", "root_input_tokens", "child_input_tokens",
    "root_output_tokens", "child_output_tokens", "unfinished_children", "ready_children",
    "tool_blocked_children", "join_blocked_children", "delegation_chars",
    "role_explore", "role_implement", "role_verify", "role_review", "role_other",
)
EXECUTION_INPUT_KIND = "task_plus_declared_execution_instructions_v1"
EXECUTION_CONTROL_FIELDS = frozenset((
    "profile", "minimum_parallel_join", "child_soft_target",
    "root_phase_target", "maximum_spawn_depth",
))
OBSERVED_RATIOS = "observed_work_and_completion_ratios_v1"
SERVICE_PROGRESS = "observed_service_without_scheduler_states_v1"
SCHEDULER_STATE_FEATURES = ("ready", "running", "active_prompt_tokens")
RATIO_FEATURES = (
    "attained_model_work", "output_per_call", "input_per_call",
    "root_output_per_call", "child_output_per_call",
    "completed_join_fraction", "returned_child_fraction", "completed_tool_fraction",
    "unfinished_child_fraction", "child_model_work_fraction",
    "root_calls_per_join_phase", "delegation_chars_per_child",
)


def feature_names(schema):
    if schema == SCHEMA:
        return FEATURES
    if schema == CONTEXT_SCHEMA:
        return CONTEXT_FEATURES
    if schema == R5_SCHEMA:
        return R5_FEATURES
    raise ValueError("incompatible workflow work feature schema")


def work_prediction_features(values, *, task_only_before_work=False):
    """Keep a root's estimate stable until coarse observed work provides progress."""
    import numpy as np

    values = np.asarray(values, dtype=float)
    if task_only_before_work and (
        values.shape != (len(R5_FEATURES),) or not np.isfinite(values).all() or (values < 0).any()
    ):
        raise ValueError("initial task-only prediction requires finite R5 observations")
    if (
        task_only_before_work and values[0] == 0 and values[1] == 0
        and values[R5_FEATURES.index("children")] == 0
    ):
        return np.zeros_like(values)
    return values


def work_numeric_features(values, transform=None):
    import numpy as np

    values = np.asarray(values)
    if transform is None:
        return values
    if transform not in (OBSERVED_RATIOS, SERVICE_PROGRESS):
        raise ValueError("unknown workflow numeric feature transform")
    if (
        values.shape != (len(R5_FEATURES),)
        or not np.isfinite(values).all() or (values < 0).any()
    ):
        raise ValueError("observed ratios require finite nonnegative R5 features")
    if transform == SERVICE_PROGRESS:
        values = values.copy()
        values[[R5_FEATURES.index(name) for name in SCHEDULER_STATE_FEATURES]] = 0.
        return values
    counts = dict(zip(R5_FEATURES, values))
    attained = .01 * counts["input_tokens"] + counts["output_tokens"]
    child_work = .01 * counts["child_input_tokens"] + counts["child_output_tokens"]
    ratios = (
        attained,
        counts["output_tokens"] / max(1., counts["completed_calls"]),
        counts["input_tokens"] / max(1., counts["completed_calls"]),
        counts["root_output_tokens"] / max(1., counts["root_completed_calls"]),
        counts["child_output_tokens"] / max(1., counts["child_completed_calls"]),
        counts["completed_joins"] / max(1., counts["joins"]),
        counts["returned_children"] / max(1., counts["children"]),
        counts["completed_tools"] / max(1., counts["tool_calls"]),
        counts["unfinished_children"] / max(1., counts["children"]),
        child_work / max(1., attained),
        counts["root_completed_calls"] / max(1., counts["completed_joins"] + 1.),
        counts["delegation_chars"] / max(1., counts["children"]),
    )
    return np.concatenate((values, ratios))


def before_observed_work(features):
    return (
        features[R5_FEATURES.index("input_tokens")] == 0
        and features[R5_FEATURES.index("output_tokens")] == 0
        and features[R5_FEATURES.index("children")] == 0
    )


@dataclass(frozen=True)
class InitialWorkProjection:
    progress_projection: object
    initial_projection: object
    initial_coverage: float


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
        self.counts["max_prompt_tokens"] = max(self.counts["max_prompt_tokens"], len(prompt))
        if invocation == self.root and "initial_prompt_tokens" not in self.counts:
            self.counts["initial_prompt_tokens"] = len(prompt)
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

    def features(self, *, feature_schema=SCHEMA):
        names = feature_names(feature_schema)
        values = dict(self.counts)
        if feature_schema in (CONTEXT_SCHEMA, R5_SCHEMA):
            # Logical observed prompt lengths, never physical KV occupancy.
            values["active_prompt_tokens"] = sum(len(call[2]) for call in self.active_calls.values())
        for state, _depth in self.invocations.values():
            if state in ("ready", "running", "tools", "waiting_join"):
                values[state] = values.get(state, 0) + 1
        values["root_waiting_join"] = int(
            self.root is not None and self.invocations[self.root][0] == "waiting_join",
        )
        return tuple(float(values.get(name, 0)) for name in names)


def ngrams(text):
    text = " ".join(text.lower().split())[:24000]
    return Counter(text[i:i + width] for width in (2, 3, 4) for i in range(len(text) - width + 1))


WORD_ENCODING = "task_words_title_and_structure_v1"


def task_terms(text, encoding=None):
    if encoding is None:
        return ngrams(text)
    if encoding != WORD_ENCODING:
        raise ValueError("unknown workflow task text encoding")
    from beliefkv.predictor.workflow_task_encoder import TASK_FEATURES, task_features

    text = text[:4096]
    words = re.findall(r"[a-z]+", text.lower())
    title = re.findall(r"[a-z]+", text.split("\n", 1)[0].lower())
    stop = {"a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "is",
            "it", "of", "on", "or", "that", "the", "this", "to", "with", "you", "your"}
    counts = Counter("word:" + word for word in words if word not in stop)
    counts.update("pair:" + left + " " + right for left, right in zip(words, words[1:]))
    counts.update(("word:" + word) for word in title for _ in range(2) if word not in stop)
    for name, value in zip(TASK_FEATURES, task_features(text)):
        if value:
            counts["structure:" + name] = math.expm1(8. * value)
    return counts


class WorkflowWorkModel:
    """JSON weights avoid pickle execution and runtime training dependencies."""

    def __init__(self, raw):
        import numpy as np

        if "frozen_work_readout_experimental" in raw:
            raise ValueError("offline frozen workflow readouts require their experimental adapter")
        self.schema = raw.get("schema")
        self.numeric_transform = raw.get("numeric_feature_transform")
        if self.numeric_transform not in (None, OBSERVED_RATIOS, SERVICE_PROGRESS) or (
            self.numeric_transform is not None and self.schema != R5_SCHEMA
        ):
            raise ValueError("unsupported workflow numeric feature transform")
        self.task_only_before_work = raw.get("task_only_before_observed_work", False)
        if type(self.task_only_before_work) is not bool or (
            self.task_only_before_work and self.schema != R5_SCHEMA
        ):
            raise ValueError("initial task-only prediction requires its R5 observation contract")
        self.observed_delegations = raw.get("observed_delegation_inputs", False)
        if type(self.observed_delegations) is not bool or self.observed_delegations and (
            self.schema != R5_SCHEMA or raw.get("task_encoder") is not None
            or raw.get("total_work_prior") is not None
        ):
            raise ValueError("observed delegation text requires its R5 progress-input contract")
        self.text_encoding = raw.get("task_text_encoding")
        if self.text_encoding not in (None, WORD_ENCODING):
            raise ValueError("unknown workflow task text encoding")
        self.feature_names = feature_names(self.schema)
        if raw.get("features") != list(self.feature_names):
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
        self.ordered_cutpoints = raw.get("ordered_work_cutpoints")
        if self.ordered_cutpoints is not None and (
            self.schema != R5_SCHEMA
            or not isinstance(self.ordered_cutpoints, list) or len(self.ordered_cutpoints) != 2
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in self.ordered_cutpoints)
            or not self.ordered_cutpoints[0] < self.ordered_cutpoints[1]
        ):
            raise ValueError("ordered workflow probabilities require two finite increasing cutpoints")
        self.task_encoder = None
        self.work_prior = None
        self.initial_head = None
        self.conditional_tree = None
        self.initial_blend = 0.
        initial = raw.get("initial_state_head")
        if initial is not None:
            if (
                self.schema != R5_SCHEMA or self.observed_delegations
                or raw.get("task_encoder") is not None or raw.get("total_work_prior") is not None
                or not isinstance(initial, dict)
                or initial.keys() != {"kind", "blend", "model"}
                or initial["kind"] != "before_observed_work_and_children_v1"
                or type(initial["blend"]) not in (int, float)
                or not math.isfinite(initial["blend"]) or not 0 < initial["blend"] <= 1
                or not isinstance(initial["model"], dict)
                or any(initial["model"].get(name) is not None for name in (
                    "initial_state_head", "total_work_prior", "task_encoder", "conditional_progress_tree_head",
                ))
            ):
                raise ValueError("invalid initial-state workflow head")
            head = WorkflowWorkModel(initial["model"])
            if (
                head.schema != R5_SCHEMA or head.observed_delegations
                or head.thresholds != self.thresholds
                or head.feature_names != self.feature_names
                or head.metadata.get("decision_eligible") is not True
            ):
                raise ValueError("initial-state workflow head differs from the observed-work contract")
            self.initial_head, self.initial_blend = head, initial["blend"]
        if raw.get("total_work_prior") is not None:
            from beliefkv.predictor.workflow_work_prior import TotalWorkPrior

            if self.schema != R5_SCHEMA or raw.get("task_encoder") is not None:
                raise ValueError("total-work priors require the legacy R5 progress model")
            self.work_prior = TotalWorkPrior(raw["total_work_prior"])
        if raw.get("task_encoder") is not None:
            from beliefkv.predictor.workflow_task_encoder import WorkflowTaskEncoder

            if self.schema != R5_SCHEMA or raw["vocabulary"] or raw["idf"]:
                raise ValueError("semantic task encoders require a separate R5 text contract")
            self.task_encoder = WorkflowTaskEncoder(raw["task_encoder"])
        self.vocabulary = {word: i for i, word in enumerate(raw["vocabulary"])}
        if len(self.vocabulary) != len(raw["vocabulary"]) or (
            not self.vocabulary and self.task_encoder is None
        ):
            raise ValueError("invalid task vocabulary")
        self.arrays = {name: np.asarray(raw[name], dtype=np.float32) for name in (
            "idf", "center", "scale", "static_weight", "dynamic_weight", "bias",
            "hidden_weight", "hidden_bias", "output_weight", "output_bias",
        )}
        v = len(self.vocabulary)
        f = len(self.feature_names) + (
            len(RATIO_FEATURES) if self.numeric_transform == OBSERVED_RATIOS else 0
        )
        text_dimension = v if self.task_encoder is None else self.task_encoder.dimension
        h, k = self.arrays["bias"].size, self.arrays["hidden_bias"].size
        shapes = {
            "idf": (v,), "center": (f,), "scale": (f,),
            "static_weight": (text_dimension, h), "dynamic_weight": (f, h), "bias": (h,),
            "hidden_weight": (h, k), "hidden_bias": (k,),
            "output_weight": (k, 3 if self.ordered_cutpoints is None else 1),
            "output_bias": (3 if self.ordered_cutpoints is None else 1,),
        }
        if any(self.arrays[name].shape != shape or not np.isfinite(self.arrays[name]).all()
               for name, shape in shapes.items()) or (self.arrays["scale"] <= 0).any():
            raise ValueError("invalid workflow model weight dimensions or values")
        self.temperature = float(raw.get("temperature", 1.))
        if not math.isfinite(self.temperature) or self.temperature <= 0:
            raise ValueError("workflow model temperature must be positive")
        self.distribution_contract = None
        if self.schema == R5_SCHEMA:
            from beliefkv.predictor.workflow_work_distribution import validate_contract

            if contract is not None:
                raise ValueError("R5 cannot apply one global execution profile to heterogeneous workflows")
            self.distribution_contract = validate_contract(raw.get("distribution_contract"))
            if raw.get("future_information_used") is True or any(
                field in raw for field in ("workflow_graphs", "requests", "workflows")
            ):
                raise ValueError("learned R5 artifacts cannot contain future workflow graphs or labels")
            self.task_input_kind = "workflow_task_and_delegation_metadata_v1"
            if self.observed_delegations:
                self.task_input_kind = "workflow_task_and_observed_unfinished_delegations_v2"
        if raw.get("conditional_progress_tree_head") is not None:
            from beliefkv.predictor.workflow_conditional_tree import ConditionalProgressTreeHead

            self.conditional_tree = ConditionalProgressTreeHead(raw["conditional_progress_tree_head"], self)
        self.continuous_readout = None
        if "continuous_work_readout" in raw:
            from beliefkv.predictor.workflow_continuous_work import (
                SERVING_KIND, ContinuousWorkReadout, forecast_fingerprint,
            )

            continuous = raw["continuous_work_readout"]
            if (
                not isinstance(continuous, dict) or continuous.get("serving_kind") != SERVING_KIND
                or continuous.get("forecast_sha256") != forecast_fingerprint(raw)
            ):
                raise ValueError("continuous workflow work requires its source-bound forecast contract")
            self.continuous_readout = ContinuousWorkReadout(self, continuous)

    @classmethod
    def load(cls, path, digest):
        content = Path(path).read_bytes()
        if len(digest) != 64 or hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("workflow work model fingerprint changed")
        return cls(json.loads(content))

    def task_projection(self, text):
        return self.task_encoding(text)[0]

    def warm_task_encoder(self):
        encoder = self.task_encoder or (
            self.work_prior.encoder if self.work_prior is not None else None
        )
        if encoder is None:
            return False
        if encoder._model is None:
            encoder._load()
        return True

    def task_encoding(self, text):
        projection, coverage = self._task_encoding(text)
        if self.work_prior is not None and projection is not None:
            from beliefkv.predictor.workflow_work_prior import PriorProjection

            mean, prior_coverage = self.work_prior.task_mean(text)
            return PriorProjection(projection, mean), min(coverage, prior_coverage)
        if self.initial_head is not None and projection is not None:
            initial, initial_coverage = self.initial_head.task_encoding(text)
            projection = InitialWorkProjection(projection, initial, initial_coverage)
        if self.conditional_tree is not None and projection is not None:
            from beliefkv.predictor.workflow_progress_tree import project

            projection = project(projection, text)
        return projection, coverage

    def _task_encoding(self, text):
        import numpy as np

        if self.task_encoder is not None:
            vector, coverage = self.task_encoder.encode(text)
            if vector is None:
                return None, coverage
            return np.einsum(
                "i,ij->j", vector, self.arrays["static_weight"], optimize=False,
            ), coverage
        counts = task_terms(text, self.text_encoding)
        indices = [self.vocabulary[word] for word in counts if word in self.vocabulary]
        if not indices:
            return None, 0.
        values = np.asarray([1. + math.log(counts[word]) for word in counts if word in self.vocabulary])
        values *= self.arrays["idf"][indices]
        values /= max(float(np.linalg.norm(values)), 1e-12)
        projection = np.einsum("i,ij->j", values, self.arrays["static_weight"][indices], optimize=False)
        coverage = sum(counts[word] for word in counts if word in self.vocabulary) / max(1, counts.total())
        return projection, coverage

    def task_input_prefix(self, environment=None):
        if self.deployment_controls is None:
            return ""
        controls = execution_controls_from_environment(environment)
        if controls != self.deployment_controls:
            raise ValueError("workflow work model and runtime execution controls differ")
        return execution_control_text(controls)

    def predict(self, projection, features, *, with_work=False):
        import numpy as np

        if with_work and self.continuous_readout is None:
            raise ValueError("continuous inference requires an explicit continuous-work artifact")
        tree_projection = None
        if self.conditional_tree is not None:
            from beliefkv.predictor.workflow_progress_tree import TreeProjection

            if not isinstance(projection, TreeProjection):
                raise ValueError("conditional workflow trees require their cached task structure")
            tree_projection, projection = projection, projection.progress_projection
        mean = None
        initial_projection = None
        if self.initial_head is not None:
            if not isinstance(projection, InitialWorkProjection):
                raise ValueError("initial-state workflow prediction requires its cached projection")
            initial_projection, projection = projection, projection.progress_projection
        if self.work_prior is not None:
            from beliefkv.predictor.workflow_work_prior import PriorProjection

            if not isinstance(projection, PriorProjection):
                raise ValueError("total-work prediction requires its cached task prior")
            mean, projection = projection.log_total_mean, projection.progress_projection
        a = self.arrays
        features = np.asarray(features)
        if (
            features.shape != (len(self.feature_names),)
            or not np.isfinite(features).all() or (features < 0).any()
        ):
            raise ValueError("invalid observed workflow features")
        initial_probabilities = None
        if initial_projection is not None and before_observed_work(features):
            from beliefkv.predictor.workflow_work_distribution import support_status

            if (
                initial_projection.initial_projection is not None
                and support_status(self.initial_head, features, initial_projection.initial_coverage)
                == "calibrated_distribution"
            ):
                initial_probabilities = self.initial_head.predict(
                    initial_projection.initial_projection, features,
                )
                if self.initial_blend == 1.:
                    return (
                        (initial_probabilities, self.continuous_readout.estimate(initial_probabilities))
                        if with_work else initial_probabilities
                    )
        if self.task_only_before_work:
            features = work_prediction_features(features, task_only_before_work=True)
        numeric = (
            np.log1p(work_numeric_features(features, self.numeric_transform)) - a["center"]
        ) / a["scale"]
        hidden = np.maximum(0., projection + np.einsum(
            "i,ij->j", numeric, a["dynamic_weight"], optimize=False,
        ) + a["bias"])
        hidden = np.maximum(0., np.einsum(
            "i,ij->j", hidden, a["hidden_weight"], optimize=False,
        ) + a["hidden_bias"])
        logits = np.einsum(
            "i,ij->j", hidden, a["output_weight"], optimize=False,
        ) + a["output_bias"]
        if self.ordered_cutpoints is not None:
            scores = np.clip(np.asarray(self.ordered_cutpoints) - float(logits[0]), -700., 700.)
            cumulative = 1. / (1. + np.exp(-scores))
            logits = np.log(np.maximum(1e-12, np.asarray((
                cumulative[0], cumulative[1] - cumulative[0], 1. - cumulative[1],
            ))))
        logits = logits / self.temperature
        probabilities = np.exp(logits - logits.max())
        probabilities /= probabilities.sum()
        probabilities = tuple(float(value) for value in probabilities)
        if initial_probabilities is not None:
            probabilities = tuple(
                (1. - self.initial_blend) * base + self.initial_blend * initial
                for base, initial in zip(probabilities, initial_probabilities)
            )
        probabilities = (
            probabilities if self.work_prior is None
            else self.work_prior.combine(probabilities, mean, features, self.thresholds)
        )
        if tree_projection is not None:
            if with_work:
                probabilities, distribution = self.conditional_tree.forecast(
                    probabilities, features, tree_projection.task_structure, hidden, self.thresholds,
                )
                return probabilities, self.continuous_readout.estimate(
                    probabilities, **(
                        {"mean": distribution[0], "sigma": distribution[1]} if distribution is not None else {}
                    ),
                )
            probabilities = self.conditional_tree.combine(
                probabilities, features, tree_projection.task_structure, hidden, self.thresholds,
            )
        return probabilities
