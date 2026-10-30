"""R5 selection at existing admission boundaries, with bounded native summaries."""

from dataclasses import asdict
from itertools import islice
import hashlib
import json
from pathlib import Path
import time

from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources,
    choose_combination, probability_weight,
)
from beliefkv.policy.workflow_size_resume import resume_category
from beliefkv.runtime.sglang_v0520_observer import (
    observe_static_full_mamba_headroom, observe_unified_node_closure,
)


class WorkflowCombinationController:
    max_candidates = 6
    max_sibling_candidates = 8
    max_cold_nodes = 32

    def __init__(self, admission):
        self.admission = admission
        content = Path(admission.runtime.workflow_work_model).read_bytes()
        if hashlib.sha256(content).hexdigest() != admission.runtime.workflow_work_model_sha256:
            raise ValueError("R5 cost profile and prediction artifact differ")
        raw = json.loads(content)
        profile = raw.get("admission_cost_profile")
        if not isinstance(profile, dict) or profile.get("kind") != "r5_frozen_batch_cost_proxy":
            raise ValueError("R5 requires its frozen, fingerprinted admission cost profile")
        self.costs = CombinationCosts(**profile["costs"])
        self.cost_identity = profile["identity"]
        self.oracle_reference_path_s = profile.get("oracle_reference_path_s", 600.)
        if type(self.oracle_reference_path_s) not in (int, float) or not 0 < self.oracle_reference_path_s < float("inf"):
            raise ValueError("invalid fixed oracle path normalization")
        self.dirty = False
        self.next_dispatch = 0.
        self.cold_summary = ()

    def defer(self, preferred):
        if preferred is not None:
            self.admission._remember_dispatch(preferred)
        self.dirty = True
        return False

    def dispatch(self, now):
        if not self.dirty and now < self.next_dispatch:
            return
        self.dirty = False
        self.next_dispatch = now + self.admission.dispatch_interval_ms / 1000.
        self.admission._dispatch_free_capacity()

    def siblings_pending(self, join_id):
        a = self.admission
        join = a._siblings.join(join_id) if a._siblings is not None else None
        if join is None:
            return
        for identity in join.member_invocation_ids - join.completed_member_ids:
            if a._ready_sibling(identity, join) is not None:
                a._remember_dispatch(a.latest[identity])
        self.dirty = True

    def _resources(self, capacity, maximum):
        a, runtime = self.admission, self.admission.runtime
        headroom = observe_static_full_mamba_headroom(runtime._native_cache)
        running = getattr(runtime._native_running_batch, "reqs", ())
        native_maximum = runtime._native_max_running
        input_limit = runtime._native_input_reserve
        rows = getattr(getattr(runtime._native_cache, "req_to_token_pool", None), "available_size", None)
        if (
            not headroom.observable or headroom.device_mamba_free_slots not in (None, 0)
            or type(native_maximum) is not int or type(input_limit) is not int
            or input_limit <= 0 or not callable(rows)
        ):
            return None
        slots = min(maximum, native_maximum - len(running), rows())
        if slots <= 0:
            return None
        self.cold_summary = self._cold_capacity()
        return CombinationResources(
            capacity, headroom.device_full_free_tokens, input_limit, slots,
            growth_tokens=runtime._native_page_size * (len(running) + slots),
            active_width=len(running),
            active_context_tokens=sum(
                len(req.origin_input_ids) + len(req.output_ids) for req in running
            ),
            chunked_input=bool(runtime._native_chunk_input_reserve),
            input_page_tokens=runtime._native_page_size,
            cold_device_tokens=sum(row[2] for row in self.cold_summary),
        )

    def _cold_capacity(self):
        """Lower bound from settled private blocked leaves; native still rechecks."""
        runtime = self.admission.runtime
        cache = runtime._native_cache
        candidates = getattr(runtime, "_parent_pressure_candidates", {})
        tree = getattr(cache, "tree_core", None)
        leaves = getattr(tree, "evictable_device_leaves", ())
        sessions = getattr(cache, "session_refs", None)
        generations = getattr(sessions, "_session_generations", {})
        result = []
        for node_id, (key, created) in islice(candidates.items(), self.max_cold_nodes):
            try:
                node = tree.node_by_id(node_id)
                full = node.component_data[0]
                if (
                    node.creation_time != created or node not in leaves or not node.backuped
                    or full.value is None or full.host_value is None
                    or full.session_ref != 1 or full.session_ids != {key.session_id}
                    or generations.get(key.session_id) != key.session_generation
                    or len(full.value) != len(full.host_value)
                    or len(full.value) != len(node.key)
                    or node_id in getattr(cache, "ongoing_write_through", {})
                    or node.write_through_pending_id is not None
                    or node.load_back_pending_id is not None
                    or not runtime._live_parent_pressure_node(node_id, created)
                    or any(
                        data.lock_ref or data.host_lock_ref or data.session_ref > 1
                        for data in (
                            node.component_data.values() if isinstance(node.component_data, dict)
                            else node.component_data
                        )
                    )
                ):
                    continue
                result.append((node_id, created, len(full.value)))
            except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
                continue
        return tuple(result)

    def _prefix(self, key, item):
        a, runtime = self.admission, self.admission.runtime
        req = a._live_requests[key.request_id]
        observation = runtime._read_request_reentry(req, key=key)
        if observation is None:
            if item.steps != 1:
                return None
            checkpoint, missing, extents = 0, 0, ()
        else:
            checkpoint = observation.get("checkpoint_tokens")
            missing = observation.get("missing_full_tokens")
            if (
                type(checkpoint) is not int or not 0 <= checkpoint < len(req.origin_input_ids)
                or type(missing) is not int or missing < 0
                or observation.get("missing_mamba_slots") != 0
            ):
                return None
            extents = []
            if missing:
                anchors = dict(observation["component_leaves"]).get(0, ())
                if len(anchors) != 1:
                    return None
                closure = observe_unified_node_closure(
                    runtime._native_cache, anchors[0][0], max_nodes=64, reject_pending=True,
                )
                if not closure.observable or not closure.nodes or (
                    closure.nodes[0].creation_time != anchors[0][1]
                ):
                    return None
                prefix = 0
                for node in reversed(closure.nodes):
                    prefix += node.key_tokens or 0
                    if prefix > checkpoint:
                        break
                    if node.full_host_tokens and not node.full_device_tokens:
                        extents.append(((node.node_id, node.creation_time), node.full_host_tokens))
                if sum(tokens for _identity, tokens in extents) != missing:
                    return None
        return tuple(extents), len(req.origin_input_ids) - checkpoint

    def _group(self, key, groups):
        a, runtime = self.admission, self.admission.runtime
        choices = []
        if a._siblings is not None:
            for join_id in islice(runtime._join_by_invocation.get(key.invocation_id, ()), 8):
                if join_id not in groups:
                    join = a._siblings.join(join_id)
                    if join is None:
                        groups[join_id] = None
                    else:
                        members = join.member_invocation_ids - join.completed_member_ids
                        active = sum(
                            a.policy.programs[identity].active
                            for identity in members
                            if a._ready_sibling(identity, join) is not None
                        )
                        groups[join_id] = len(members), active, members
                if groups[join_id] is not None and key.invocation_id in groups[join_id][2]:
                    choices.append((groups[join_id][0], join_id, groups[join_id][1]))
        if choices:
            total, identity, active = min(choices)
            return identity, total, active
        return "single:" + key.invocation_id, 1, 0

    def select(self, keys, reference, *, capacity, maximum, source):
        a, runtime = self.admission, self.admission.runtime
        started, cpu = time.perf_counter_ns(), time.thread_time_ns()
        result = {
            "selected": tuple(key.invocation_id for key in reference),
            "reason": "metadata_unavailable_reference_fallback",
        }
        summaries, candidates, predictions, resources = {}, [], {}, None
        self.cold_summary = ()
        try:
            if not reference or len(reference) > self.max_sibling_candidates:
                return list(reference)
            window = list(reference)
            for key in islice(keys, self.max_sibling_candidates):
                if len(window) >= self.max_candidates:
                    break
                if key not in window:
                    window.append(key)
            if len(window) == len(reference):
                result["reason"] = "no_competing_choice"
                return list(reference)
            groups = {}
            # Complete a visible sibling window without scanning the queue or graph.
            for key in tuple(window):
                group, total, _active = self._group(key, groups)
                if total > 1:
                    for peer in islice(keys, self.max_sibling_candidates):
                        if len(window) >= self.max_sibling_candidates:
                            break
                        if peer not in window and self._group(peer, groups)[0] == group:
                            window.append(peer)
            resources = self._resources(capacity, maximum)
            if resources is None:
                return list(reference)
            for key in window:
                found = a._candidate(key.request_id, key, include_first=True)
                if found is None:
                    return list(reference)
                item, _req = found
                prefix = self._prefix(key, item)
                if prefix is None:
                    return list(reference)
                if key.root_workflow_id not in predictions:
                    predictions[key.root_workflow_id] = a._work.prediction(key.root_workflow_id)
                prediction = predictions[key.root_workflow_id]
                if a._supported(prediction):
                    weight = (
                        1. / (1. + prediction.remaining_path_s / self.oracle_reference_path_s)
                        if prediction.evidence == "oracle" else probability_weight(prediction.probabilities)
                    )
                else:
                    weight = .5
                group, total, active = self._group(key, groups)
                candidate = CombinationCandidate(
                    key.invocation_id, key.root_workflow_id, item.tokens + a.policy.buffer_tokens,
                    resume_category(item), item.queue_sequence, weight, group, total, active,
                    prefix[0], prefix[1], item.tokens,
                )
                candidates.append(candidate)
                summaries[key.invocation_id] = key
            if not any(a._supported(value) for value in predictions.values()):
                result["reason"] = "predictions_unavailable_reference_fallback"
                return list(reference)
            result = choose_combination(
                candidates, [key.invocation_id for key in reference], resources, self.costs,
                required=[
                    key.invocation_id for key in reference
                    if key.invocation_id in runtime._join_priority_pending
                ],
            )
            return [summaries[identity] for identity in result["selected"]]
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, RuntimeError) as error:
            result.update(reason="invalid_metadata_reference_fallback", error=str(error))
            return list(reference)
        finally:
            try:
                writer = getattr(runtime, "_opportunity_writer", None)
                if writer is not None:
                    writer.record({
                        "event": "workflow_combination_selection", "source": source,
                        "ts_ms": time.time() * 1000., "monotonic_s": time.monotonic(),
                        "decision": result, "candidates": [asdict(row) for row in candidates],
                        "keys": {identity: asdict(key) for identity, key in summaries.items()},
                        "predictions": {
                            workflow: asdict(value) if value is not None else None
                            for workflow, value in predictions.items()
                        },
                        "cost_identity": self.cost_identity,
                        "resources": asdict(resources) if resources is not None else None,
                        "cold_device_extents": self.cold_summary,
                        "semantic_revision": runtime.semantic_revision,
                        "metadata_and_selection_cpu_ns": time.thread_time_ns() - cpu,
                        "metadata_and_selection_wall_ns": time.perf_counter_ns() - started,
                        "physical_capacity_credit": False,
                    })
            finally:
                a.counts["workflow_combination_passes"] += 1
                a.counts["workflow_combination_" + result["reason"]] += 1
                a.counts["workflow_combination_total_cpu_ns"] += time.thread_time_ns() - cpu
                a.counts["workflow_combination_total_wall_ns"] += time.perf_counter_ns() - started

    def periodic(self, ordered, reference, capacity):
        a = self.admission
        if any(not a._logical_ready_paused(item) for item in reference):
            a.counts["workflow_combination_unready_reference_fallback"] += 1
            return reference
        keys = [
            a.latest[item.identity] for item in ordered
            if a._logical_ready_paused(item)
        ]
        selected = self.select(
            keys, [a.latest[item.identity] for item in reference],
            capacity=capacity, maximum=len(keys), source="periodic_decay_resume",
        )
        return [a.policy.programs[key.invocation_id] for key in selected]

    def dispatch_keys(self, keys):
        a = self.admission
        capacity = a.policy.remaining_capacity()
        ready = [
            key for key in keys if a._candidate(key.request_id, key, include_first=True) is not None
        ]
        reference, used = [], 0
        for key in ready:
            required = a.policy.programs[key.invocation_id].tokens + a.policy.buffer_tokens
            if used + required <= capacity and len(reference) < 4:
                reference.append(key)
                used += required
        return self.select(
            ready, reference, capacity=capacity, maximum=4, source="undiscounted_free_dispatch",
        )
