"""R5 selection at existing admission boundaries, with bounded native summaries."""

from dataclasses import asdict, replace
from itertools import islice
import hashlib
import json
import math
from pathlib import Path
import time

from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources,
    choose_combination, prediction_weight,
)
from beliefkv.policy.workflow_size_resume import resume_category
from beliefkv.runtime.deferred_free_admission import DeferredFreeAdmission
from beliefkv.runtime.sglang_v0520_observer import (
    normalize_native_creation_time, observe_static_full_mamba_headroom,
    observe_unified_node_closure,
)


class WorkflowCombinationController(DeferredFreeAdmission):
    max_candidates = 6
    max_sibling_candidates = 8
    max_cold_nodes = 32

    def __init__(self, admission):
        content = Path(admission.runtime.workflow_work_model).read_bytes()
        if hashlib.sha256(content).hexdigest() != admission.runtime.workflow_work_model_sha256:
            raise ValueError("R5 cost profile and prediction artifact differ")
        raw = json.loads(content)
        ranking = raw.get("remaining_work_ranking_enabled", True)
        if type(ranking) is not bool:
            raise ValueError("remaining-work ranking switch must be a boolean")
        admission.remaining_work_ranking_enabled = ranking
        super().__init__(
            admission, window_ms=raw.get("free_dispatch_coalescing_window_ms", 0),
        )
        profile = raw.get("admission_cost_profile")
        kinds = (
            "r5_frozen_batch_cost_proxy", "r5_frozen_batch_cost_proxy_capacity_v2",
            "r5_frozen_batch_cost_proxy_capacity_v3", "r5_frozen_batch_cost_proxy_capacity_v4",
            "r5_frozen_batch_cost_proxy_capacity_v5",
            "r5_frozen_batch_cost_proxy_capacity_v6",
            "r5_frozen_batch_cost_proxy_capacity_v7",
        )
        if not isinstance(profile, dict) or profile.get("kind") not in kinds:
            raise ValueError("R5 requires its frozen, fingerprinted admission cost profile")
        self.include_unreferenced_capacity = profile["kind"] in kinds[1:]
        self.allow_deep_prefix_aggregate = profile["kind"] in kinds[2:]
        self.allow_feasible_resource_alternatives = profile["kind"] in kinds[3:]
        self.plan_device_capacity = profile["kind"] in kinds[5:]
        self.costs = CombinationCosts(**profile["costs"])
        from beliefkv.policy.workflow_shortest_frontier import strict_remaining_work_enabled

        self.strict_remaining_work = strict_remaining_work_enabled(raw)
        self.shortest_work_first = raw.get("shortest_work_first_admission", False)
        if type(self.shortest_work_first) is not bool or self.shortest_work_first and (
            profile["kind"] != kinds[6] or not ranking
        ):
            raise ValueError("short-work admission requires the explicit enabled benefit profile")
        self.cost_aware_maximal = raw.get("cost_aware_maximal_admission", False)
        if type(self.cost_aware_maximal) is not bool or self.cost_aware_maximal and not self.shortest_work_first:
            raise ValueError("maximal cost selection requires explicit enabled short-work admission")
        self.maximal_objective = raw.get("maximal_admission_objective", "benefit_per_cost")
        if self.maximal_objective not in ("benefit_per_cost", "benefit") or (
            self.maximal_objective != "benefit_per_cost" and not self.cost_aware_maximal
        ):
            raise ValueError("maximal admission objective requires its explicit maximal selector")
        from beliefkv.runtime.ready_wait_priority import ReadyWaitPriority, ReadyWaitSettings

        ready_wait = ReadyWaitSettings.from_artifact(raw)
        ready_wait_ms = ready_wait.scale_ms
        if ready_wait_ms and not (self.shortest_work_first and self.cost_aware_maximal):
            raise ValueError("ready wait priority requires explicit bounded maximal short-work admission")
        self.ready_wait_priority = None
        if ready_wait_ms:
            self.ready_wait_priority = ReadyWaitPriority(
                admission, ready_wait_ms,
                grace_ms=ready_wait.grace_ms, max_boost=ready_wait.max_boost,
            )
        accumulation_ms = raw.get("ready_capacity_accumulation_ms", 0)
        if type(accumulation_ms) is not int or accumulation_ms not in (0, 1000, 2000) or (
            accumulation_ms and (
                accumulation_ms != self.window_ms or not ready_wait_ms
                or not self.cost_aware_maximal or self.maximal_objective != "benefit"
            )
        ):
            raise ValueError("capacity accumulation requires one ordinary window and maximal wait priority")
        self.capacity_accumulation = None
        if accumulation_ms:
            from beliefkv.runtime.workflow_capacity_accumulation import ReadyCapacityAccumulation

            self.capacity_accumulation = ReadyCapacityAccumulation(self, accumulation_ms)
        if (self.costs.decode_cost_basis == "full_batch") != (profile["kind"] in kinds[4:]):
            raise ValueError("full-batch cost requires its explicit v5/v6/v7 admission profile")
        if (self.costs.selection_objective == "benefit") != (profile["kind"] == kinds[6]):
            raise ValueError("benefit selection requires its explicit v7 admission profile")
        self.neutral_work_weight = .5
        self.continuous_work_weights = False
        reference = raw.get("continuous_work_readout") or raw.get("combination_work_reference")
        if isinstance(reference, dict):
            self.continuous_work_weights = (
                reference.get("kind") == "training_referenced_continuous_model_work_v2"
                or "admission_utility" in reference
            )
        if self.strict_remaining_work and not self.continuous_work_weights:
            raise ValueError("strict remaining-work admission requires a declared continuous work readout")
        if isinstance(reference, dict) and "admission_utility" in reference:
            from beliefkv.predictor.workflow_continuous_work import (
                remaining_work_weight, utility_reference_work,
            )

            self.neutral_work_weight = remaining_work_weight(
                reference["reference_work"], utility_reference_work(reference),
            )
        self.completion_path_profile = raw.get("completion_path_readout")
        self.completion_path_oracle = getattr(
            getattr(getattr(admission, "_work", None), "artifact", None), "path_combination", False,
        )
        if self.completion_path_profile is not None:
            if not self.strict_remaining_work:
                raise ValueError("completion-path admission requires strict shortest-work selection")
        if self.completion_path_profile is not None or self.completion_path_oracle:
            self.neutral_work_weight = .5
        self.cost_identity = profile["identity"]
        self.oracle_reference_path_s = profile.get("oracle_reference_path_s", 600.)
        if type(self.oracle_reference_path_s) not in (int, float) or not 0 < self.oracle_reference_path_s < float("inf"):
            raise ValueError("invalid fixed oracle path normalization")
        self.cold_summary = ()
        self._prefix_protected_anchors = ()
        if "tool_boundary_preemption" in raw:
            from beliefkv.runtime.tool_boundary_preemption import ToolBoundaryPreemption

            admission._tool_boundary_preemption = ToolBoundaryPreemption(admission, raw)

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
        device_capacity = None
        if self.plan_device_capacity:
            device_capacity = getattr(
                getattr(runtime._native_cache, "token_to_kv_pool_allocator", None),
                "size_full", None,
            )
            if type(device_capacity) is not int or not (
                0 <= headroom.device_full_free_tokens <= device_capacity
            ) or device_capacity <= 0:
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
            device_capacity_tokens=device_capacity,
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

    def _unreferenced_capacity(self, protected):
        """Count only settled, unowned duplicate leaves; do not reclaim them."""
        cache = self.admission.runtime._native_cache
        if tuple(getattr(cache, "tree_components", ())) != (0,):
            return ()
        tree = getattr(cache, "tree_core", None)
        leaves = getattr(tree, "evictable_device_leaves", ())
        result, seen = [], set(protected)
        for node in islice(leaves, self.max_cold_nodes):
            self.admission.counts["workflow_combination_unreferenced_leaf_checks"] += 1
            try:
                full = node.component_data[0]
                data = (
                    node.component_data.values() if isinstance(node.component_data, dict)
                    else node.component_data
                )
                if (
                    node.id in seen or node is getattr(tree, "root_node", None)
                    or tree.node_by_id(node.id) is not node
                    or not node.backuped or full.value is None or full.host_value is None
                    or not 0 < len(full.value) == len(full.host_value) == len(node.key)
                    or node.id in getattr(cache, "ongoing_write_through", {})
                    or node.write_through_pending_id is not None
                    or node.load_back_pending_id is not None
                    or any(
                        type(part.session_ref) is not int or part.session_ref != 0
                        or type(part.lock_ref) is not int or part.lock_ref != 0
                        or type(part.host_lock_ref) is not int or part.host_lock_ref != 0
                        or part.session_ids is not None and (
                            not isinstance(part.session_ids, (set, frozenset)) or part.session_ids
                        )
                        for part in data
                    )
                ):
                    continue
                result.append((node.id, node.creation_time, len(full.value)))
                seen.add(node.id)
            except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
                continue
        return tuple(result)

    def _deep_prefix(self, req, key):
        """Use a fresh native checkpoint as a conservative per-request copy bound."""
        a, runtime = self.admission, self.admission.runtime
        a.counts["workflow_combination_deep_prefix_refresh_attempts"] += 1
        try:
            observed = runtime._read_request_reentry(req, key=key, refresh=True)
            if not isinstance(observed, dict) or tuple(runtime._native_cache.tree_components) != (0,):
                raise ValueError("FULL checkpoint unavailable")
            checkpoint = observed.get("checkpoint_tokens")
            missing = observed.get("missing_full_tokens")
            device = observed.get("device_checkpoint_tokens")
            if (
                type(checkpoint) is not int or not 0 <= checkpoint < len(req.origin_input_ids)
                or type(device) is not int or not 0 <= device <= checkpoint
                or type(missing) is not int or not 0 <= missing <= checkpoint - device
                or type(observed.get("reusable_input_tokens")) is not int
                or observed["reusable_input_tokens"] != checkpoint
                or type(observed.get("missing_mamba_slots")) is not int
                or observed["missing_mamba_slots"] != 0
                or type(key.session_id) is not str or not key.session_id
                or type(key.session_generation) is not int or key.session_generation < 0
            ):
                raise ValueError("invalid FULL checkpoint")
            components = dict(observed["component_leaves"])
            if set(components) != {0} or len(components[0]) != 1:
                raise ValueError("non-single FULL checkpoint anchor")
            anchor, = components[0]
            identity, created = anchor
            created = normalize_native_creation_time(created)
            if type(identity) is not int or identity < 0:
                raise ValueError("invalid FULL checkpoint anchor")
            node = runtime._native_cache.tree_core.node_by_id(identity)
            if node.id != identity or node.creation_time != created:
                raise ValueError("FULL checkpoint anchor changed")
        except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError) as error:
            a.counts["workflow_combination_deep_prefix_refresh_rejected"] += 1
            self._prefix_issue_detail["aggregate_rejection"] = str(error)
            return None
        self._prefix_protected_anchors = tuple(dict.fromkeys((
            *self._prefix_protected_anchors, (identity, created),
        )))
        # Separate request identities deliberately overcount any shared missing pages.
        extent = (
            "aggregate_full_checkpoint", key.session_id, key.session_generation,
            key.context_id, key.context_epoch, key.request_id, checkpoint,
        )
        self._prefix_summary_detail = {
            "kind": "fresh_native_aggregate", "checkpoint_tokens": checkpoint,
            "missing_full_tokens": missing, "device_checkpoint_tokens": device,
            "trigger": "node ancestry exceeds bound", "exact_closure_node_limit": 64,
            "native_checkpoint_refreshes": 1, "shared_extent_deduplication": False,
        }
        self._prefix_issue = None
        a.counts["workflow_combination_deep_prefix_aggregates"] += 1
        return (((extent, missing),) if missing else ()), len(req.origin_input_ids) - checkpoint

    def _prefix(self, key, item):
        a, runtime = self.admission, self.admission.runtime
        self._prefix_issue = None
        self._prefix_issue_detail = None
        self._prefix_summary_detail = None
        self._prefix_protected_anchors = ()
        req = a._live_requests[key.request_id]
        observation = runtime._read_request_reentry(req, key=key)
        if observation is None:
            if item.steps != 1:
                self._prefix_issue = "native_checkpoint_unavailable"
                return None
            checkpoint, missing, extents = 0, 0, ()
        else:
            if self.include_unreferenced_capacity:
                self._prefix_protected_anchors = tuple(
                    dict(observation.get("component_leaves", ())).get(0, ())
                )
            checkpoint = observation.get("checkpoint_tokens")
            missing = observation.get("missing_full_tokens")
            if (
                type(checkpoint) is not int or not 0 <= checkpoint < len(req.origin_input_ids)
                or type(missing) is not int or missing < 0
                or observation.get("missing_mamba_slots") != 0
            ):
                self._prefix_issue = "invalid_checkpoint_or_state_pool"
                return None
            extents = []
            if missing:
                anchors = dict(observation["component_leaves"]).get(0, ())
                if len(anchors) != 1:
                    self._prefix_issue = "non_single_full_anchor"
                    return None
                closure = observe_unified_node_closure(
                    runtime._native_cache, anchors[0][0], max_nodes=64, reject_pending=True,
                )
                if not closure.observable or not closure.nodes or (
                    closure.nodes[0].creation_time != anchors[0][1]
                ):
                    self._prefix_issue = "unobservable_missing_closure"
                    self._prefix_issue_detail = {
                        "closure_reason": getattr(closure, "reason", None),
                        "closure_observable": closure.observable,
                        "closure_node_count": len(closure.nodes),
                        "maximum_closure_nodes": 64,
                        "anchor_id": anchors[0][0],
                        "anchor_creation_matches": (
                            closure.nodes[0].creation_time == anchors[0][1]
                            if closure.nodes else None
                        ),
                        "checkpoint_tokens": checkpoint,
                        "missing_full_tokens": missing,
                    }
                    if self.allow_deep_prefix_aggregate and getattr(
                        closure, "reason", None,
                    ) == "node ancestry exceeds bound":
                        return self._deep_prefix(req, key)
                    return None
                prefix = 0
                for node in reversed(closure.nodes):
                    prefix += node.key_tokens or 0
                    if prefix > checkpoint:
                        break
                    if node.full_host_tokens and not node.full_device_tokens:
                        extents.append(((node.node_id, node.creation_time), node.full_host_tokens))
                if sum(tokens for _identity, tokens in extents) != missing:
                    self._prefix_issue = "unaccounted_host_extent"
                    return None
        return tuple(extents), len(req.origin_input_ids) - checkpoint

    def _group(self, key, groups):
        a = self.admission
        choices = []
        if getattr(a, "_siblings", None) is not None:
            runtime = a.runtime
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

    def _supported_work(self, prediction):
        if not self.admission._supported(prediction):
            return False
        if not getattr(self, "continuous_work_weights", False):
            return True
        weight = getattr(prediction, "effective_weight", None)
        return type(weight) in (int, float) and math.isfinite(weight) and 0 <= weight <= 1.

    def _work_weight(self, key, predictions):
        a = self.admission
        if key.root_workflow_id not in predictions:
            predictions[key.root_workflow_id] = self.prediction(key.root_workflow_id)
        prediction = predictions[key.root_workflow_id]
        if not self._supported_work(prediction):
            return getattr(self, "neutral_work_weight", .5)
        profile = getattr(self, "completion_path_profile", None)
        path = getattr(prediction, "remaining_path_s", None)
        if profile is not None:
            if (
                getattr(prediction, "path_source", None) == profile["target"]
                and type(path) in (int, float) and math.isfinite(path) and path >= 0
            ):
                weight = 1. / (1. + path / profile["reference_path_s"])
            else:
                from beliefkv.predictor.workflow_continuous_work import log_work_weight

                # Missing path support retains a declared model-time fallback;
                # work units are never compared directly with seconds.
                weight = log_work_weight(
                    prediction.log1p_remaining_work,
                    profile["reference_path_s"] / profile["model_seconds_per_work_token"],
                )
        elif getattr(prediction, "evidence", None) == "oracle" and not getattr(
            a._work, "model_work_only", getattr(a._work, "combination_only", False),
        ):
            weight = 1. / (1. + prediction.remaining_path_s / self.oracle_reference_path_s)
        else:
            weight = prediction_weight(prediction)
        priority = getattr(self, "ready_wait_priority", None)
        return (
            priority.weight(key.root_workflow_id, weight)
            if priority is not None and not getattr(self, "strict_remaining_work", False) else weight
        )

    def _strict_candidate_window(self, keys, reference, capacity, predictions, *,
                                 complete_free_siblings=False):
        a = self.admission
        required = [key for key in reference if a._protected_join(key)]
        reserve = sum(
            a.policy.programs[key.invocation_id].tokens + a.policy.buffer_tokens for key in required
        )
        ordered = [
            key for key in dict.fromkeys((*keys, *reference))
            if key in required or (
                (item := a.policy.programs.get(key.invocation_id)) is not None
                and item.tokens + a.policy.buffer_tokens <= capacity - reserve
            )
        ]
        priorities = {
            key: (0 if a._protected_join(key) else 1, -self._work_weight(key, predictions))
            for key in ordered
        }
        ordered.sort(key=priorities.__getitem__)
        window = list(required)
        limit = (
            self.max_sibling_candidates if len(reference) >= self.max_candidates
            else self.max_candidates
        )
        groups, peers = {}, {}
        for key in ordered:
            group, total, _active = self._group(key, groups)
            token = (key.root_workflow_id, group) if total > 1 else (key.invocation_id,)
            peers.setdefault(token, []).append(key)
        seen = set(window)
        checks_left = self.max_sibling_candidates if complete_free_siblings else 0
        known = {key.invocation_id for key in ordered} if checks_left else set()
        for token, group in peers.items():
            if len(window) >= limit:
                break
            pending = [key for key in group if key not in seen]
            if checks_left and len(token) == 2:
                join_id = token[1]
                # The rotating free-dispatch window can cut through a live
                # JOIN. Check its indexed peers before pricing native prefixes.
                for identity in sorted(groups[join_id][2] - known):
                    if not checks_left or len(window) + len(pending) >= self.max_sibling_candidates:
                        break
                    checks_left -= 1
                    peer = a.latest.get(identity)
                    if peer is None or peer.root_workflow_id != token[0]:
                        continue
                    found = a._candidate(peer.request_id, peer, include_first=True)
                    if (
                        found is not None
                        and found[0].tokens + a.policy.buffer_tokens <= capacity - reserve
                        and self._group(peer, groups)[0] == join_id
                    ):
                        known.add(identity)
                        pending.append(peer)
            if len(window) + len(pending) > limit and len(window) + len(pending) <= self.max_sibling_candidates:
                limit = self.max_sibling_candidates
            for key in pending:
                if len(window) >= limit:
                    break
                window.append(key)
                seen.add(key)
        a.counts["strict_remaining_work_window_keys"] += len(ordered)
        a.counts["strict_remaining_work_window_reference_replacements"] += len(set(reference) - set(window))
        return window

    def _candidate_window(self, keys, reference, capacity, predictions):
        if getattr(self, "strict_remaining_work", False):
            return self._strict_candidate_window(keys, reference, capacity, predictions)
        a = self.admission
        window = list(reference)
        limit = self.max_candidates
        if len(window) >= self.max_sibling_candidates or (
            len(window) >= limit and not self.shortest_work_first
        ):
            return window
        if self.shortest_work_first:
            # Rank the already collected logical candidates before spending the
            # bounded prefix-check window. Native feasibility is checked later.
            keys = [
                key for key in keys
                if (item := a.policy.programs.get(key.invocation_id)) is not None
                and item.tokens + a.policy.buffer_tokens <= capacity
            ]
            if len(window) >= limit and not any(key not in window for key in keys):
                return window
            priorities = {
                key: (0 if a._protected_join(key) else 1, -self._work_weight(key, predictions))
                for key in keys
            }
            keys.sort(key=priorities.__getitem__)
            a.counts["shortest_work_window_priority_passes"] += 1
            a.counts["shortest_work_window_priority_keys"] += len(keys)
            if len(window) >= limit:
                retained = [priorities[key] for key in window if key in priorities]
                worst = max(retained) if retained else None
                if worst is None or not any(
                    key not in window and priorities[key] < worst for key in keys
                ):
                    return window
                # A full reference is still the fallback. Spend at most the
                # existing eight-member bound to compare a better visible peer.
                limit = self.max_sibling_candidates
                a.counts["shortest_work_competitive_window_expansions"] += 1
        for key in islice(keys, self.max_sibling_candidates):
            if len(window) >= limit:
                break
            if key not in window:
                window.append(key)
        return window

    def select(self, keys, reference, *, capacity, maximum, source):
        a, runtime = self.admission, self.admission.runtime
        started, cpu = time.perf_counter_ns(), time.thread_time_ns()
        result = {
            "selected": tuple(key.invocation_id for key in reference),
            "reference": tuple(key.invocation_id for key in reference),
            "reason": "metadata_unavailable_reference_fallback",
        }
        summaries, candidates, predictions, resources = {}, [], {}, None
        unavailable, protected_prefixes, prefix_summaries = [], set(), {}
        indexed_siblings = ()
        private_blocked_tokens, unreferenced_tokens = None, 0
        self.cold_summary = ()
        accumulation = getattr(self, "capacity_accumulation", None)
        if accumulation is not None:
            accumulation.last = None
        try:
            if (priority := getattr(self, "ready_wait_priority", None)) is not None:
                priority.begin(keys, time.monotonic())
            if not getattr(a, "remaining_work_ranking_enabled", True):
                result["reason"] = "remaining_work_ranking_disabled_reference"
                return list(reference)
            if accumulation is not None:
                held = accumulation.consider(keys, reference, capacity, predictions, source)
                if held is not None:
                    result.update(
                        selected=tuple(key.invocation_id for key in held),
                        reason="ready_capacity_accumulation",
                    )
                    summaries.update({key.invocation_id: key for key in reference})
                    return held
            strict = getattr(self, "strict_remaining_work", False)
            if (not reference and not strict) or len(reference) > self.max_sibling_candidates:
                if strict:
                    result["reason"] = "strict_reference_exceeds_prefix_budget"
                return list(reference)
            if strict and source == "undiscounted_free_dispatch":
                window = self._strict_candidate_window(
                    keys, reference, capacity, predictions, complete_free_siblings=True,
                )
                observed = set(keys) | set(reference)
                indexed_siblings = tuple(key for key in window if key not in observed)
                a.counts["strict_remaining_work_indexed_siblings"] += len(indexed_siblings)
            else:
                window = self._candidate_window(keys, reference, capacity, predictions)
            if not strict and len(window) == len(reference):
                result["reason"] = "no_competing_choice"
                return list(reference)
            if strict:
                # Retain the complete fallback without spending prefix checks
                # on lower-priority reference members outside the shortlist.
                summaries.update({key.invocation_id: key for key in reference})
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
                result["reason"] = "native_resources_unavailable_reference_fallback"
                return list(reference)
            private_blocked_tokens = resources.cold_device_tokens
            reference_keys = set(reference)
            for key in window:
                found = a._candidate(key.request_id, key, include_first=True)
                self._prefix_issue_detail = None
                self._prefix_summary_detail = None
                if found is None:
                    issue = "candidate_no_longer_ready"
                    prefix = None
                else:
                    item, _req = found
                    self._prefix_protected_anchors = ()
                    prefix = self._prefix(key, item)
                    protected_prefixes.update(
                        identity for identity, _created in self._prefix_protected_anchors
                    )
                    issue = self._prefix_issue if prefix is None else None
                if prefix is None:
                    unavailable.append({
                        "key": asdict(key), "reason": issue,
                        "detail": self._prefix_issue_detail,
                    })
                    if key in reference_keys:
                        result["reason"] = "reference_candidate_summary_unavailable"
                        return list(reference)
                    a.counts["workflow_combination_optional_summaries_skipped"] += 1
                    continue
                if self._prefix_summary_detail is not None:
                    prefix_summaries[key.invocation_id] = self._prefix_summary_detail
                weight = self._work_weight(key, predictions)
                group, total, active = self._group(key, groups)
                candidate = CombinationCandidate(
                    key.invocation_id, key.root_workflow_id, item.tokens + a.policy.buffer_tokens,
                    resume_category(item), item.queue_sequence, weight, group, total, active,
                    prefix[0], prefix[1], item.tokens,
                )
                candidates.append(candidate)
                summaries[key.invocation_id] = key
            if not any(self._supported_work(value) for value in predictions.values()):
                result["reason"] = "predictions_unavailable_reference_fallback"
                return list(reference)
            if self.include_unreferenced_capacity:
                self.cold_summary = tuple(
                    row for row in self.cold_summary if row[0] not in protected_prefixes
                )
                protected = protected_prefixes | {row[0] for row in self.cold_summary}
                unreferenced = self._unreferenced_capacity(protected)
                unreferenced_tokens = sum(row[2] for row in unreferenced)
                self.cold_summary += unreferenced
                resources = replace(
                    resources, cold_device_tokens=sum(row[2] for row in self.cold_summary),
                )
            arguments = (
                candidates, [key.invocation_id for key in reference], resources, self.costs,
            )
            required = [
                key.invocation_id for key in reference
                if (
                    a._protected_join(key) if strict
                    else key.invocation_id in runtime._join_priority_pending
                )
            ]
            if getattr(self, "cost_aware_maximal", False):
                from beliefkv.policy.workflow_maximal_admission import choose_maximal_admission

                result = choose_maximal_admission(
                    *arguments, required=required,
                    selection_objective=getattr(self, "maximal_objective", "benefit_per_cost"),
                )
            elif self.shortest_work_first:
                from beliefkv.policy.workflow_shortest_frontier import choose_shortest_work_first

                result = choose_shortest_work_first(
                    *arguments, required=required,
                    **({"strict_remaining_work": True} if strict else {}),
                )
            else:
                result = choose_combination(
                    *arguments, required=required,
                    allow_feasible_resource_alternatives=self.allow_feasible_resource_alternatives,
                )
            return [summaries[identity] for identity in result["selected"]]
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, RuntimeError) as error:
            result.update(reason="invalid_metadata_reference_fallback", error=str(error))
            return list(reference)
        finally:
            try:
                writer = getattr(runtime, "_opportunity_writer", None)
                if writer is not None:
                    candidate_workflows = {row.workflow for row in candidates}
                    reference_window = None
                    if source == "undiscounted_free_dispatch":
                        reference_window = [
                            {
                                "key": asdict(key),
                                "logical_tokens": item.tokens + a.policy.buffer_tokens,
                                "category": resume_category(item),
                                "sequence": item.queue_sequence,
                                "protected_join": a._protected_join(key),
                            }
                            for key in keys
                            if (item := a.policy.programs.get(key.invocation_id)) is not None
                        ]
                    writer.record({
                        "event": "workflow_combination_selection", "source": source,
                        **({
                            "selection_policy": (
                                "strict_remaining_work" if getattr(self, "strict_remaining_work", False) else
                                (
                                    "maximal_benefit" if getattr(self, "maximal_objective", "benefit_per_cost") == "benefit"
                                    else "maximal_cost_aware"
                                ) if getattr(self, "cost_aware_maximal", False)
                                else "shortest_work_first"
                            ),
                            "candidate_window_order": "protected_join_then_work_before_prefix_checks",
                        } if self.shortest_work_first else {}),
                        "ts_ms": time.time() * 1000., "monotonic_s": time.monotonic(),
                        "decision": result, "candidates": [asdict(row) for row in candidates],
                        **({"indexed_sibling_candidates": [asdict(key) for key in indexed_siblings]}
                           if indexed_siblings else {}),
                        "logical_reference_window": reference_window,
                        "logical_reference_capacity_tokens": capacity,
                        "logical_reference_maximum": maximum,
                        "keys": {identity: asdict(key) for identity, key in summaries.items()},
                        "predictions": {
                            workflow: asdict(value) if value is not None else None
                            for workflow, value in predictions.items()
                            if not self.shortest_work_first or workflow in candidate_workflows
                        },
                        **({
                            "ready_wait_priority": self.ready_wait_priority.record(candidate_workflows),
                        } if getattr(self, "ready_wait_priority", None) is not None else {}),
                        **({
                            "ready_capacity_accumulation_ms": accumulation.max_wait_ms,
                            "capacity_accumulation": accumulation.last,
                        } if accumulation is not None else {}),
                        "cost_identity": self.cost_identity,
                        "ranking_objective": (
                            "remaining_model_tool_critical_path_seconds"
                            if (
                                getattr(self, "completion_path_profile", None) is not None
                                or getattr(self, "completion_path_oracle", False)
                            ) else
                            "expected_work_utility"
                        ),
                        "resource_fallback_selection_enabled": self.allow_feasible_resource_alternatives,
                        "device_fit_scope": (
                            "planned_pool_geometry" if self.plan_device_capacity
                            else "current_free_and_safe_cold"
                        ),
                        "unavailable_summaries": unavailable,
                        "prefix_summaries": prefix_summaries,
                        "resources": asdict(resources) if resources is not None else None,
                        "cold_device_extents": self.cold_summary,
                        "capacity_summary_kind": (
                            "private_blocked_and_unreferenced_settled_leaves"
                            if self.include_unreferenced_capacity else "private_blocked_settled_leaves"
                        ),
                        "protected_prefix_anchor_ids": sorted(protected_prefixes),
                        "private_blocked_cold_tokens_before_prefix_protection": private_blocked_tokens,
                        "unreferenced_cold_device_tokens": unreferenced_tokens,
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

    def order_tool_replacement(self, keys):
        """Rank the existing donor-pair window without applying a pre-release budget."""
        a, runtime = self.admission, self.admission.runtime
        if not self.shortest_work_first or len(keys) < 2:
            return keys
        started = time.thread_time_ns()
        predictions, weights, priorities = {}, {}, {}
        a.counts["shortest_work_tool_replacement_rank_passes"] += 1
        try:
            priority = getattr(self, "ready_wait_priority", None)
            if priority is not None:
                priority.begin(keys, time.monotonic())
            for index, key in enumerate(keys):
                weight = self._work_weight(key, predictions)
                weights[key] = weight
                priorities[key] = (0 if a._protected_join(key) else 1, -weight, index)
            supported = any(self._supported_work(value) for value in predictions.values())
            ordered = sorted(keys, key=priorities.__getitem__) if supported else keys
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError, OverflowError):
            a.counts["shortest_work_tool_replacement_metadata_fallbacks"] += 1
            return keys
        finally:
            a.counts["shortest_work_tool_replacement_cpu_ns"] += time.thread_time_ns() - started
        a.counts["shortest_work_tool_replacement_order_changes"] += ordered != keys
        writer = getattr(runtime, "_opportunity_writer", None)
        if ordered != keys and writer is not None:
            writer.record({
                "event": "shortest_work_tool_replacement_order",
                "ts_ms": time.time() * 1000., "monotonic_s": time.monotonic(),
                "original_order": [asdict(key) for key in keys],
                "ordered": [
                    {"key": asdict(key), "weight": weights[key], "protected_JOIN": priorities[key][0] == 0}
                    for key in ordered
                ],
                "scope": "same_bounded_donor_pair_candidates;no_free_budget_filter;no_window_wait",
                **({
                    "ready_wait_priority": priority.record(
                        {key.root_workflow_id for key in keys}
                    ),
                } if priority is not None else {}),
                "physical_capacity_credit": False,
            })
        return ordered

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
        guard = getattr(a, "_ready_service_guard", None)
        if guard is not None and guard.fit_available:
            groups = {}

            def protected(key):
                return (
                    a._protected_join(key) or a.runtime._last_join_child_has_waiting_parent(key)
                    or self._group(key, groups)[1] > 1
                )

            selected = guard.choose_fit(
                keys, selected, now=a.policy._resume_now, decay_capacity=capacity,
                protected=protected,
            )
        return [a.policy.programs[key.invocation_id] for key in selected]

    def dispatch_keys(self, keys):
        a = self.admission
        capacity = a.policy.remaining_capacity()
        ready = [
            key for key in keys if a._candidate(key.request_id, key, include_first=True) is not None
        ]
        if a.rank_before_category or getattr(a, "_conditional_distribution", False):
            ready.sort(key=lambda key: (
                0 if a._protected_join(key) else 1,
                resume_category(a.policy.programs[key.invocation_id]),
                a.policy.programs[key.invocation_id].tokens,
                a.policy.programs[key.invocation_id].queue_sequence,
            ))
        reference, used = [], 0
        for key in ready:
            required = a.policy.programs[key.invocation_id].tokens + a.policy.buffer_tokens
            if used + required <= capacity and len(reference) < 4:
                reference.append(key)
                used += required
        return self.select(
            ready, reference, capacity=capacity, maximum=4, source="undiscounted_free_dispatch",
        )
