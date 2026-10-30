"""Explicit future-work oracle for fixed-trace experiments, never deployment."""

from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import time

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.workflow_work_worker import WorkPrediction


KIND = "workflow_model_tool_critical_path_oracle_v1"


def critical_path_graphs(plan):
    """Keep fixed replay delays and model operations in a topological DAG."""
    from graphlib import TopologicalSorter

    nodes = {node.node_id: node for node in plan.nodes}
    order = TopologicalSorter(plan.dependency_graph()).static_order()
    graphs, positions = defaultdict(list), {}
    for identity in order:
        node = nodes[identity]
        graph = graphs[node.workflow_id]
        if any(nodes[dep].workflow_id != node.workflow_id for dep in node.dependencies):
            raise ValueError("oracle graph has a cross-workflow dependency")
        dependencies = [positions[dep] for dep in node.dependencies]
        positions[identity] = len(graph)
        graph.append({
            "node_id": identity, "dependencies": dependencies,
            "delay_s": node.delay_ms / 1000., "request_id": node.request_id,
            "event_id": node.event.event_id if node.event is not None else None,
            "kind": node.event.kind.value if node.event is not None else "native_request",
        })
    return dict(graphs)


def _count(value):
    return type(value) is int and value >= 0


class WorkflowWorkOracleArtifact:
    def __init__(self, raw):
        if (
            raw.get("kind") != KIND or raw.get("future_information_used") is not True
            or raw.get("deployable") is not False or raw.get("prefill_token_weight") != .01
        ):
            raise ValueError("require an explicitly declared model-work oracle")
        self.raw = raw
        self.requests = raw["requests"]
        self.workflows = raw["workflows"]
        self.thresholds = tuple(raw["work_thresholds"])
        self.graphs = raw["workflow_graphs"]
        self.model_seconds_per_work_token = raw["model_seconds_per_work_token"]
        if (
            not self.workflows or not self.requests or len(self.thresholds) != 2
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in self.thresholds)
            or not 0 < self.thresholds[0] < self.thresholds[1]
        ):
            raise ValueError("invalid oracle population or class thresholds")
        if (
            self.graphs.keys() != self.workflows.keys()
            or type(self.model_seconds_per_work_token) not in (int, float)
            or not math.isfinite(self.model_seconds_per_work_token)
            or self.model_seconds_per_work_token <= 0
        ):
            raise ValueError("oracle requires model-time calibration and whole workflow DAGs")
        totals, members, successors = Counter(), defaultdict(set), defaultdict(dict)
        for request, item in self.requests.items():
            workflow = item["workflow_id"]
            if (
                not request or workflow not in self.workflows
                or any(not isinstance(item.get(name), str) or not item[name]
                       for name in ("invocation_id", "context_id"))
                or any(not _count(item.get(name)) for name in (
                    "prompt_tokens", "output_tokens", "input_increment",
                ))
                or item["input_increment"] > item["prompt_tokens"]
                or item["output_tokens"] == 0
            ):
                raise ValueError("invalid oracle request identity or work")
            previous = item.get("predecessor_request_id")
            if previous is not None and (
                previous not in self.requests
                or any(self.requests[previous][name] != item[name] for name in (
                    "workflow_id", "invocation_id", "context_id",
                ))
            ):
                raise ValueError("oracle predecessor has another owner")
            context = workflow, item["context_id"]
            if previous in successors[context]:
                raise ValueError("oracle serial context has multiple successors")
            successors[context][previous] = request
            totals[workflow] += item["input_increment"] + 100 * item["output_tokens"]
            members[workflow].add(request)
        for workflow, item in self.workflows.items():
            if (
                not isinstance(workflow, str) or not workflow
                or not _count(item.get("total_work_units"))
                or totals[workflow] != item["total_work_units"]
                or item.get("requests") != len(members[workflow])
            ):
                raise ValueError("oracle workflow work is not conserved")
        for chain in successors.values():
            previous, visited = None, set()
            while previous in chain:
                previous = chain[previous]
                if previous in visited:
                    raise ValueError("oracle context contains a cycle")
                visited.add(previous)
            if len(visited) != len(chain):
                raise ValueError("oracle context is not a connected serial history")
        graph_requests, node_ids, event_ids = set(), set(), set()
        for workflow, graph in self.graphs.items():
            if not graph or graph[-1]["kind"] != Kind.WORKFLOW_END.value:
                raise ValueError("oracle graph must end at its workflow completion")
            for index, node in enumerate(graph):
                if (
                    not isinstance(node["node_id"], str) or node["node_id"] in node_ids
                    or any(type(dep) is not int or not 0 <= dep < index
                           for dep in node["dependencies"])
                    or type(node["delay_s"]) not in (int, float)
                    or not math.isfinite(node["delay_s"]) or node["delay_s"] < 0
                ):
                    raise ValueError("oracle graph is not a valid topological DAG")
                node_ids.add(node["node_id"])
                request = node["request_id"]
                event = node["event_id"]
                if request is not None:
                    if (
                        request in graph_requests or request not in self.requests
                        or self.requests[request]["workflow_id"] != workflow
                        or event is not None or node["kind"] != "native_request"
                        or node["delay_s"] != 0
                    ):
                        raise ValueError("oracle DAG request ownership mismatch")
                    graph_requests.add(request)
                elif not isinstance(event, str) or not event or event in event_ids:
                    raise ValueError("oracle DAG event identity mismatch")
                if event is not None:
                    event_ids.add(event)
        if graph_requests != self.requests.keys():
            raise ValueError("oracle DAG omits model work")

    @classmethod
    def load(cls, path, digest):
        data = Path(path).read_bytes()
        if not digest or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("oracle artifact hash mismatch")
        return cls(json.loads(data))

    def validate_source(self, arm, graph, evidence):
        from beliefkv.experiments.source_provenance import sha256_file
        from beliefkv.experiments.tool_timing_overlay import load_overlay

        binding = self.raw["source_binding"]
        with Path(binding["token_path"]).open("rb") as stream:
            token_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if (
            str(Path(arm).resolve()) != binding["source_arm"]
            or evidence != binding["source_evidence"]
            or set(graph.request_usage) != self.requests.keys()
            or {node.workflow_id for node in graph.nodes} != self.workflows.keys()
            or any(
                tuple(graph.request_usage[request]) != (item["prompt_tokens"], item["output_tokens"])
                for request, item in self.requests.items()
            )
            or token_digest != binding["token_sha256"]
        ):
            raise ValueError("oracle artifact belongs to another fixed token execution")
        overlay = Path(binding["tool_overlay_path"])
        if sha256_file(overlay) != binding["tool_overlay_sha256"]:
            raise ValueError("oracle tool timing map changed")
        transformed, _overlay_evidence = load_overlay(graph, overlay)
        if critical_path_graphs(transformed) != self.graphs:
            raise ValueError("oracle DAG differs from the fixed model/tool execution")


@dataclass(frozen=True)
class OracleWorkPrediction(WorkPrediction):
    remaining_work: float
    remaining_work_units: int
    remaining_path_s: float
    model_only_path_s: float
    tool_and_fixed_delay_path_s: float


class WorkflowWorkOracle:
    """Bound future model/tool DAG, updated only by actual observed progress."""

    def __init__(self, path, digest):
        self.artifact = WorkflowWorkOracleArtifact.load(path, digest)
        self.digest = digest
        self.counts = Counter()
        self._remaining = {
            workflow: item["total_work_units"]
            for workflow, item in self.artifact.workflows.items()
        }
        self._predictions = {}
        self._active = {}
        self._completed = set()
        self._closed = set()
        self._revision = Counter()
        self._done_at = {}
        self._event_nodes = {}
        self._request_nodes = {}
        for graph in self.artifact.graphs.values():
            for node in graph:
                if node["event_id"] is not None:
                    self._event_nodes[node["event_id"]] = node["node_id"]
                else:
                    self._request_nodes[node["request_id"]] = node["node_id"]
        self.error = ""
        for workflow in self._remaining:
            self._publish(workflow)

    def _publish(self, workflow):
        remaining = self._remaining[workflow]
        if remaining < 0:
            raise ValueError("oracle observed work exceeds its fixed total")
        self._revision[workflow] += 1
        value = remaining / 100.
        bucket = bisect_right(self.artifact.thresholds, value)
        self._predictions[workflow] = OracleWorkPrediction(
            tuple(float(index == bucket) for index in range(3)), bucket,
            self._revision[workflow], time.monotonic(), "oracle", value, remaining, 0., 0., 0.,
        )

    def submit(self, workflow, action, *args):
        started = time.thread_time()
        try:
            self._submit(workflow, action, *args)
        except Exception as error:
            self.error = type(error).__name__ + ": " + str(error)[:300]
            self._predictions.clear()
            raise
        finally:
            self.counts["observations"] += 1
            self.counts["observation_thread_cpu_us"] += (time.thread_time() - started) * 1e6

    def _submit(self, workflow, action, *args):
        if self.error or workflow not in self._remaining:
            raise ValueError("oracle received an unbound workflow")
        if action == "task":
            return
        if action == "event":
            event = args[0]
            if event.workflow_id != workflow:
                raise ValueError("oracle event workflow mismatch")
            identity = self._event_nodes.get(getattr(event, "event_id", None))
            if identity is not None:
                self._done_at.setdefault(identity, event.ts_ms / 1000.)
                self.counts["matched_graph_event_observations"] += 1
            else:
                self.counts["unmodeled_event_observations"] += 1
                if event.kind is not Kind.TOOL_WAIT_OBSERVATION and not (
                    event.kind is Kind.STRUCTURED_ACTION
                    and getattr(event, "attributes", {}).get("diagnostic_only") is True
                ):
                    self.counts["unmodeled_core_event_observations"] += 1
            if event.kind is Kind.WORKFLOW_END:
                if self._remaining[workflow] != 0:
                    raise ValueError("oracle workflow ended with unconsumed work")
                self._closed.add(workflow)
                self._predictions.pop(workflow, None)
                self.counts["closed_workflows"] = len(self._closed)
            return
        if workflow in self._closed:
            raise ValueError("oracle received work after workflow end")
        request = args[0]
        item = self.artifact.requests.get(request)
        if item is None or item["workflow_id"] != workflow:
            raise ValueError("oracle request is not bound to this workflow")
        if action in ("started", "completed"):
            _request, invocation, context, prompt = args[:4]
            if (
                invocation != item["invocation_id"] or context != item["context_id"]
                or len(prompt) != item["prompt_tokens"]
            ):
                raise ValueError("oracle request owner or prompt length mismatch")
            if action == "completed" and len(args[4]) != item["output_tokens"]:
                raise ValueError("oracle completion output length mismatch")
            if request in self._completed:
                self.counts["duplicate_completed_observations"] += 1
                return
            previous = item.get("predecessor_request_id")
            if previous is not None and previous not in self._completed:
                raise ValueError("oracle serial request arrived before its predecessor completed")
            if request not in self._active:
                self._active[request] = 0
                self.counts["started_requests"] += 1
            if action == "started":
                return
            output = len(args[4])
        elif action == "generated":
            output = args[1]
            if request not in self._active:
                raise ValueError("oracle output has no active request")
        else:
            raise ValueError("unknown oracle observation")
        previous_output = self._active[request]
        if not _count(output) or not previous_output <= output <= item["output_tokens"]:
            raise ValueError("oracle output progress exceeds recorded work or moves backwards")
        self._remaining[workflow] -= 100 * (output - previous_output)
        self._active[request] = output
        self.counts["observed_output_tokens"] += output - previous_output
        if action == "completed":
            # Match the learned labels: charge necessary input at call completion,
            # and count actual partial output between completed calls.
            self._remaining[workflow] -= item["input_increment"]
            self.counts["observed_input_tokens"] += item["input_increment"]
            self._active.pop(request)
            self._completed.add(request)
            self._done_at[self._request_nodes[request]] = time.monotonic()
            self.counts["completed_requests"] += 1
        self._publish(workflow)

    def prediction(self, workflow):
        prediction = self._predictions.get(workflow) if not self.error else None
        if prediction is None:
            return None
        started, now = time.thread_time(), time.monotonic()
        finish, model, tools = [], [], []
        graph = self.artifact.graphs[workflow]
        for node in graph:
            if node["node_id"] in self._done_at:
                finish.append(0.)
                model.append(0.)
                tools.append(0.)
                continue
            request = node["request_id"]
            dependencies = node["dependencies"]
            if request is not None:
                item = self.artifact.requests[request]
                units = item["input_increment"] + 100 * (
                    item["output_tokens"] - self._active.get(request, 0)
                )
                duration = units / 100. * self.artifact.model_seconds_per_work_token
                external = 0.
            else:
                duration, external = 0., node["delay_s"]
                if dependencies and all(
                    graph[dep]["node_id"] in self._done_at for dep in dependencies
                ):
                    ready_at = max(self._done_at[graph[dep]["node_id"]] for dep in dependencies)
                    external = max(0., external - max(0., now - ready_at))
            finish.append(max((finish[dep] for dep in dependencies), default=0.) + duration + external)
            model.append(max((model[dep] for dep in dependencies), default=0.) + duration)
            tools.append(max((tools[dep] for dep in dependencies), default=0.) + external)
        self.counts["path_evaluations"] += 1
        self.counts["path_nodes_evaluated"] += len(graph)
        self.counts["path_thread_cpu_us"] += (time.thread_time() - started) * 1e6
        return OracleWorkPrediction(
            prediction.probabilities, prediction.bucket, prediction.revision, now, "oracle",
            prediction.remaining_work, prediction.remaining_work_units,
            finish[-1], model[-1], tools[-1],
        )

    def status(self):
        return {
            "artifact_sha256": self.digest, "error": self.error,
            "kind": KIND, "deployable": False,
            "future_trace_used_online": True, "scheduler_waits_for_inference": False,
            "rank": "remaining_model_tool_critical_path_seconds",
            "work_definition": "0.01*necessary_input_tokens+output_tokens",
            "input_progress": "completion_only_like_learned_labels",
            "output_progress": "observed_native_output_every_128_tokens_and_completion",
            "future_tool_durations_used": True,
            "future_dependency_graph_used_for_rank_only": True,
            "model_seconds_per_work_token": self.artifact.model_seconds_per_work_token,
            "critical_path_is_optimal_JCT_bound": False,
            "remaining_work_units": dict(self._remaining),
            "counts": dict(self.counts), "cached_workflows": len(self._predictions),
            "queue_depth": 0, "refresh_interval_ms": 0,
        }

    def close(self):
        self._predictions.clear()
