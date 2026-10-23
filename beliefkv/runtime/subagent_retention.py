"""Revocable, boundary-driven retention of dormant reusable subagent state."""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass
import os
import time


RETENTION_POLICIES = ("off", "selective", "lru", "all", "drop")


@dataclass(frozen=True)
class KVExtent:
    component: int
    node_id: int
    created: int | float
    units: int
    size_bytes: int
    device: bool
    host: bool
    exclusive: bool
    pending: bool

    @property
    def identity(self) -> tuple:
        return self.component, self.node_id, self.created


@dataclass
class DormantState:
    key: object
    handle: str
    features: dict
    returned_at: float
    extents: tuple[KVExtent, ...] = ()
    tier: str = "host"
    retry_anchors: tuple | None = None
    closed: bool = False
    discarded: bool = False
    checkpoint_unavailable: bool = False
    scored: bool = False
    probability: float = .5
    score: float = 0.

    @property
    def private_bytes(self) -> int:
        return sum(item.size_bytes for item in self.extents if item.exclusive)


class ReuseEstimator:
    """Shrunk online boundary-feature bins, trained only on completed episodes."""

    def __init__(self, *, minimum_labels: int = 16) -> None:
        self.minimum_labels = minimum_labels
        self.counts: Counter = Counter()
        self.bins: dict[tuple, Counter] = {}
        self.brier_sum = 0.

    @staticmethod
    def group(features: dict) -> tuple:
        return (
            bool(features.get("unresolved")),
            bool(features.get("implementation")),
            min(2, max(0, int(features.get("reuse_count", 0)))),
            features.get("role", ""),
            bool(features.get("related_delegation")),
        )

    @property
    def ready(self) -> bool:
        return (
            self.counts["labels"] >= self.minimum_labels
            and self.counts["positive"] >= 2 and self.counts["negative"] >= 2
        )

    def predict(self, features: dict) -> float:
        global_p = (self.counts["positive"] + 1.) / (self.counts["labels"] + 2.)
        group = self.bins.get(self.group(features), Counter())
        return (group["positive"] + 8. * global_p) / (group["labels"] + 8.)

    def observe(self, features: dict, reused: bool, *, predicted: float) -> None:
        if features.get("censored"):
            self.counts["censored"] += 1
            return
        self.brier_sum += (float(reused) - predicted) ** 2
        self.counts["labels"] += 1
        self.counts["positive" if reused else "negative"] += 1
        group = self.bins.setdefault(self.group(features), Counter())
        group["labels"] += 1
        group["positive"] += int(reused)


def inspect_dormant_extents(cache: object, key: object, *, max_nodes: int = 256):
    """Read only the latest session path; no tensor copies or historical scan."""
    tracker = cache.session_refs
    anchors = tracker.snapshot_latest_session_leaf_anchors(
        key.session_id, key.session_generation, max_leaves=8,
    )
    if anchors is None:
        return None
    core = cache.tree_core
    entries = cache.cache_controller.mem_pool_host.entry_map
    units = {
        component: entries[name].host_pool.size_per_token
        for component, name in ((0, "kv"), (2, "mamba")) if name in entries
    }
    extents, visited = [], set()
    components = dict(anchors)
    for component, leaves in anchors:
        if component not in units:
            continue
        for node_id, created in leaves:
            try:
                node = core.node_by_id(node_id)
            except (KeyError, RuntimeError, ValueError):
                return None
            if node.creation_time != created:
                return None
            while node is not core.root_node:
                identity = component, node.id
                if identity in visited:
                    break
                visited.add(identity)
                if len(visited) > max_nodes or core.node_by_id(node.id) is not node:
                    return None
                data = node.component_data[component]
                device, host = data.value is not None, data.host_value is not None
                if device or host:
                    count = len(data.value if device else data.host_value)
                    if device and host and len(data.value) != len(data.host_value):
                        return None
                    extents.append(KVExtent(
                        int(component), node.id, node.creation_time, count,
                        count * units[component], device, host,
                        data.session_ref == 1,
                        bool(
                            node.write_through_pending_id is not None
                            or node.load_back_pending_id is not None
                            or node.id in cache.ongoing_write_through
                            or data.lock_ref or data.host_lock_ref
                        ),
                    ))
                # Only the latest necessary state, not all interior Mamba states.
                if component != 0:
                    if not device and not host:
                        return None
                    break
                node = node.parent
                if node is None:
                    return None
    if "mamba" in entries and not components.get(2):
        return None
    return tuple(extents)


def demote_dormant_backed(cache: object, key: object, extents: tuple[KVExtent, ...]) -> dict:
    """Use native tree transitions/allocators after ACK, retaining soft Host KV."""
    anchors = cache.session_refs.snapshot_latest_session_leaf_anchors(
        key.session_id, key.session_generation, max_leaves=8,
    )
    if anchors is None:
        return {}
    freed: Counter = Counter()
    # FULL extents are leaf-to-root; freeing the leaf can make its parent a leaf.
    for extent in extents:
        if not extent.device or not extent.host or not extent.exclusive:
            continue
        try:
            node = cache.tree_core.node_by_id(extent.node_id)
        except (KeyError, RuntimeError, ValueError):
            continue
        if (
            node.creation_time != extent.created
            or node.write_through_pending_id is not None or node.load_back_pending_id is not None
            or node.id in cache.ongoing_write_through
            or any(data.lock_ref or data.host_lock_ref for data in node.component_data)
        ):
            continue
        data = node.component_data[extent.component]
        if (
            data.session_ref != 1 or data.value is None or data.host_value is None
            or len(data.value) != len(data.host_value)
        ):
            continue
        if extent.component == 0:
            if not cache._can_beliefkv_full_demote(node, full_session_ref=1):
                continue
            result = cache.tree_core.demote_backed_full_preserving_mamba(node.id)
        elif extent.component == 2 and (
            (node.id, node.creation_time) in dict(anchors).get(2, ())
        ):
            result = cache.tree_core.demote_backed_mamba_state(node.id)
        else:
            continue
        cache._free_values(result.device_frees, result.host_frees)
        freed.update({int(component): count for component, count in result.tracker.items()})
    return dict(freed)


class DormantSubagentRetention:
    """Logical handles never pin native pages or outrank ready parent demand."""

    def __init__(
        self, policy: str = "selective", *, host_budget_bytes: int = 32 * 1024**3,
        device_budget_bytes: int = 2 * 1024**3, max_contexts: int = 512,
        recompute_tokens_per_s: float = 100_000.,
        transfer_bytes_per_s: float = 8 * 1024**3,
    ) -> None:
        if policy not in RETENTION_POLICIES or min(
            host_budget_bytes, device_budget_bytes, max_contexts,
            recompute_tokens_per_s, transfer_bytes_per_s,
        ) < 0 or max_contexts == 0 or recompute_tokens_per_s == 0 or transfer_bytes_per_s == 0:
            raise ValueError("invalid dormant subagent retention configuration")
        self.policy = policy
        self.host_budget_bytes = host_budget_bytes
        self.device_budget_bytes = device_budget_bytes
        self.max_contexts = max_contexts
        # Explicit cold-start cost priors; these are not measured speedups.
        self.recompute_tokens_per_s = recompute_tokens_per_s
        self.transfer_bytes_per_s = transfer_bytes_per_s
        self.states: OrderedDict[str, DormantState] = OrderedDict()
        self.work: OrderedDict[str, None] = OrderedDict()
        self.pending_resumes: dict[str, DormantState] = {}
        self.estimator = ReuseEstimator()
        self.counts: Counter = Counter()
        self.next_ms = 0.
        self.dirty = False

    @classmethod
    def from_environment(cls):
        policy = os.environ.get("BELIEFKV_SUBAGENT_RETENTION", "off")
        if policy not in RETENTION_POLICIES:
            raise ValueError("unknown subagent retention policy")
        if policy == "off":
            return None
        return cls(
            policy,
            host_budget_bytes=int(os.environ.get("BELIEFKV_DORMANT_HOST_BYTES", 32 * 1024**3)),
            device_budget_bytes=int(os.environ.get("BELIEFKV_DORMANT_HBM_BYTES", 2 * 1024**3)),
        )

    def remember(self, key: object, event: object) -> bool:
        if event.attributes.get("reusable_subagent") is not True:
            return False
        features = dict(event.attributes.get("reuse_features") or {})
        state = DormantState(
            key, str(event.attributes["agent_handle"]), features, time.monotonic(),
            probability=self.estimator.predict(features),
        )
        self.states[key.context_id] = state
        self.work[key.context_id] = None
        # Overflow is retired through the same native safe-point path.
        if len(self.states) > self.max_contexts:
            oldest = next(iter(self.states.values()))
            oldest.closed = True
            self.work[oldest.key.context_id] = None
            self.work.move_to_end(oldest.key.context_id, last=False)
        self.dirty = True
        self.next_ms = 0.
        self.counts["returns"] += 1
        return True

    def activate(self, event: object, *, runtime: object = None) -> None:
        state = self.states.get(event.context_id)
        if state is None:
            return
        if (
            event.workflow_id != state.key.root_workflow_id
            or event.attributes.get("agent_handle") != state.handle
            or event.attributes.get("resumed") is not True
            or event.invocation_id == state.key.invocation_id
        ):
            return
        if runtime is not None and runtime._native_cache is not None:
            try:
                observed = inspect_dormant_extents(runtime._native_cache, state.key)
            except (AttributeError, KeyError, TypeError, ValueError):
                observed = None
            device = sum(item.size_bytes for item in observed or () if item.device)
            host_only = sum(item.size_bytes for item in observed or () if item.host and not item.device)
            source = (
                "physical_drop" if state.discarded else "unknown_checkpoint"
                if observed is None else "host_restore_required" if host_only else "hbm_complete"
            )
            self.counts[f"resume_snapshot:{source}"] += 1
            self._record(
                runtime, state, "resume", next_invocation_id=event.invocation_id,
                device_bytes=device, host_only_bytes=host_only, checkpoint_source=source,
                semantics="pre-resume cache snapshot, not proof of later native cache consumption",
            )
        del self.states[event.context_id]
        self.work.pop(event.context_id, None)
        self.pending_resumes[event.invocation_id] = state
        self.counts["resumes_declared"] += 1
        self.dirty = True

    def started(self, event: object) -> None:
        state = self.pending_resumes.pop(event.invocation_id, None)
        if state is None:
            return
        if event.workflow_id != state.key.root_workflow_id or event.context_id != state.key.context_id:
            self.pending_resumes[event.invocation_id] = state
            return
        # Reuse is an actual replaying model call, not merely a proposed task
        # that may be cancelled before its private graph ever starts.
        self.estimator.observe(state.features, True, predicted=state.probability)
        self.counts["resumes"] += 1
        self.counts["resume_after_physical_drop"] += state.discarded
        self.dirty = True

    def cancel(self, event: object) -> None:
        if self.pending_resumes.pop(event.invocation_id, None) is not None:
            self.counts["censored_resume_before_model"] += 1

    def observe_delegation(self, event: object) -> None:
        if event.attributes.get("reusable_subagent") is not True:
            return
        query = frozenset((event.attributes.get("reuse_features") or {}).get("scope_ids", ()))
        if not query:
            return
        # The actual new assignment is now visible. Inspect only this root's
        # dormant handles at this boundary, never during model decode.
        for context_id, state in self.states.items():
            if state.closed or state.key.root_workflow_id != event.workflow_id:
                continue
            scope = frozenset(state.features.get("scope_ids", ()))
            similarity = len(scope & query) / max(1, len(scope | query))
            related = similarity >= .25
            if related == bool(state.features.get("related_delegation")):
                continue
            state.features["related_delegation"] = related
            state.probability = self.estimator.predict(state.features)
            self.dirty = True
            if not state.discarded:
                self.work[context_id] = None
            self.counts["semantic_boundary_updates"] += 1

    def end(self, workflow_id: str, *, labels_complete: bool) -> None:
        for invocation_id, state in tuple(self.pending_resumes.items()):
            if state.key.root_workflow_id == workflow_id:
                del self.pending_resumes[invocation_id]
                self.counts["censored_resume_before_model"] += 1
        for context_id, state in self.states.items():
            if state.key.root_workflow_id != workflow_id:
                continue
            if labels_complete and not state.closed:
                self.estimator.observe(state.features, False, predicted=state.probability)
            elif not labels_complete:
                self.counts["censored_workflow_episodes"] += 1
            state.closed = True
            if not state.discarded:
                self.work[context_id] = None
        for context_id, state in tuple(self.states.items()):
            if state.key.root_workflow_id == workflow_id and state.discarded:
                del self.states[context_id]
        self.dirty = True
        self.next_ms = 0.

    def close_context(self, event: object) -> None:
        state = self.states.get(event.context_id)
        if (
            state is None or state.closed
            or event.attributes.get("subagent_context_close") is not True
            or event.workflow_id != state.key.root_workflow_id
            or event.invocation_id != state.key.invocation_id
            or event.context_epoch != state.key.context_epoch
            or event.attributes.get("agent_handle") != state.handle
        ):
            return
        # Explicit logical closure ends this dormant episode without needing
        # the rest of its workflow to finish. Physical eviction is not a label.
        self.estimator.observe(state.features, False, predicted=state.probability)
        self.counts["explicit_closes"] += 1
        state.closed = True
        self.work[event.context_id] = None
        self.next_ms = 0.
        self.dirty = True

    def _rank(self, state: DormantState) -> tuple:
        if self.policy == "selective" and self.estimator.ready:
            state.probability = self.estimator.predict(state.features)
            size = max(1, state.private_bytes)
            tokens = sum(x.units for x in state.extents if x.component == 0)
            missing = sum(x.size_bytes for x in state.extents if x.device and not x.host)
            # Charge a conservative whole-copy restore upper bound until
            # exposed-wait samples exist. This proxy is not a JCT prediction.
            state.score = (
                state.probability * tokens / self.recompute_tokens_per_s
                - missing / self.transfer_bytes_per_s
                - state.probability * size / self.transfer_bytes_per_s
            )
            return state.score / size, state.returned_at
        return state.returned_at, 0.

    def _plan(self) -> None:
        host_ids, host_bytes, device_bytes = set(), 0, 0
        for state in sorted(self.states.values(), key=self._rank, reverse=True):
            previous = state.tier
            if state.closed or state.discarded or state.checkpoint_unavailable or self.policy == "drop":
                state.tier = "drop"
            elif not state.scored:
                continue
            elif self.policy == "all":
                state.tier = "hbm"
            else:
                added = [
                    item for item in state.extents
                    if item.identity not in host_ids
                ]
                size = sum(item.size_bytes for item in added)
                useful = not self.estimator.ready or self.policy != "selective" or state.score > 0
                if useful and host_bytes + size <= self.host_budget_bytes:
                    host_bytes += size
                    host_ids.update(item.identity for item in added)
                    near = (
                        self.policy == "selective" and state.features.get("reuse_count", 0) > 0
                        and state.features.get("unresolved")
                        and time.monotonic() - state.returned_at < 1.
                    )
                    device = sum(item.size_bytes for item in state.extents if item.device and item.exclusive)
                    if near and device_bytes + device <= self.device_budget_bytes:
                        state.tier = "hbm"
                        device_bytes += device
                    else:
                        state.tier = "host"
                else:
                    state.tier = "drop"
            if state.tier != previous and not state.discarded:
                self.work[state.key.context_id] = None
        self.counts["planned_unique_host_bytes"] = host_bytes
        self.counts["planned_device_bytes"] = device_bytes
        self.counts["plans"] += 1
        self.dirty = False

    @staticmethod
    def _live(runtime: object, state: DormantState) -> bool:
        key = state.key
        context = runtime.graph.contexts.get(key.context_id)
        workflow = runtime.graph.workflows.get(key.root_workflow_id)
        return bool(
            context is not None and context.workflow_id == key.root_workflow_id
            and context.epoch == key.context_epoch and workflow is not None
            and (state.closed or workflow.end_ts_ms is None)
            and all(runtime.graph.invocations[i].state.terminal for i in context.invocation_ids)
        )

    def _drop(self, runtime, state):
        cache, key = runtime._native_cache, state.key
        result = cache.retire_beliefkv_child_session(
            session_id=key.session_id, session_generation=key.session_generation,
            **({"retry_anchors": state.retry_anchors, "max_nodes": 64}
               if state.retry_anchors is not None else {}),
        )
        state.retry_anchors = result["retry_anchors"]
        self.counts["drop_calls"] += 1
        self.counts["discarded_nodes"] += len(result["discarded_nodes"])
        self.counts["freed_device_full_tokens"] += result["freed_device_units"].get(0, 0)
        self.counts["freed_host_full_tokens"] += result["freed_host_units"].get(0, 0)
        self._record(runtime, state, "drop", result=result)
        if not state.retry_anchors:
            state.discarded = True
            state.extents = ()
            # Keep the episode for a future positive label and exact-history
            # resume. A physical cache decision does not close a logical handle.
            if state.closed:
                self.states.pop(key.context_id, None)
            self.work.pop(key.context_id, None)

    def dispatch(self, runtime: object) -> None:
        if not self.work or runtime.physical_disabled or runtime._native_cache is None:
            return
        now = time.monotonic() * 1000.
        if now < self.next_ms:
            return
        self.next_ms = now + 200.
        critical = runtime._live_critical_parent_demand() is not None
        cache = runtime._native_cache
        self.counts["dispatches"] += 1
        contexts = tuple(self.work)[:4]
        snapshots = {}
        for context_id in contexts:
            state = self.states.get(context_id)
            if state is None:
                self.work.pop(context_id, None)
                continue
            if not self._live(runtime, state):
                # A new activation/compaction invalidates dormant write authority.
                self.states.pop(context_id, None)
                self.work.pop(context_id, None)
                self.counts["stale_context"] += 1
                continue
            if state.closed or state.tier == "drop" or self.policy == "drop":
                continue
            try:
                extents = inspect_dormant_extents(cache, state.key)
            except (AttributeError, KeyError, TypeError, ValueError):
                extents = None
            if extents is None:
                self.counts["unavailable_checkpoint"] += 1
                if self.policy == "all":
                    self.work.pop(context_id, None)
                    continue
                state.checkpoint_unavailable = True
                state.tier = "drop"
                continue
            if state.extents != extents:
                state.extents = extents
                self.dirty = True
                self._record(
                    runtime, state, "snapshot",
                    checkpoint_extents=[
                        (x.component, x.node_id, x.created, x.size_bytes, x.device, x.host)
                        for x in extents
                    ],
                )
            state.scored = True
            snapshots[context_id] = extents
        if self.dirty:
            self._plan()
        for context_id in contexts:
            state = self.states.get(context_id)
            if state is None or context_id not in self.work:
                continue
            if state.closed or state.tier == "drop" or self.policy == "drop":
                self._drop(runtime, state)
                continue
            extents = snapshots.get(context_id)
            if extents is None:
                continue
            if self.policy == "all":
                # Strong retain-all reference: ordinary revocable native cache,
                # with no mandatory eager copy of every returned session.
                self.work.pop(context_id, None)
                continue
            # A one-second HBM grace is soft; critical demand revokes it now.
            if state.tier == "hbm" and self.policy != "all" and (
                critical or time.monotonic() - state.returned_at >= 1.
            ):
                state.tier = "host"
                self.counts["hbm_grace_revoked"] += 1
            if state.tier != "hbm" or critical:
                freed = demote_dormant_backed(cache, state.key, extents)
                if freed:
                    self.counts["demoted_full_tokens"] += freed.get(0, 0)
                    self.counts["demoted_mamba_slots"] += freed.get(2, 0)
                    self._record(runtime, state, "host", freed_device_units=freed)
            if critical or runtime.physical_ledger.pending_count:
                self.counts["backup_yielded_to_demand"] += 1
                self.work.move_to_end(context_id)
                continue
            if any(item.pending for item in extents):
                self.work.move_to_end(context_id)
                continue
            missing = [item for item in extents if item.device and not item.host]
            if not missing:
                if state.tier == "hbm" and self.policy != "all":
                    self.work.move_to_end(context_id)
                else:
                    self.work.pop(context_id, None)
                continue
            controller = cache.cache_controller
            if controller.write_queue or controller.ack_write_queue:
                self.work.move_to_end(context_id)
                continue
            # Existing incremental native D2H locks sources and publishes Host
            # values only at ACK. It never evicts live KV to make Host room.
            full = [item for item in missing if item.component == 0]
            if full:
                result = cache.prepare_beliefkv_spawn_full(
                    session_id=state.key.session_id,
                    session_generation=state.key.session_generation,
                    max_batch_bytes=256 * 1024**2,
                )
                issued = result["issued"]
                self.counts["backup_full_tokens"] += sum(item[2] for item in issued)
                if issued:
                    self.counts["backup_batches"] += 1
                    self._record(runtime, state, "backup", native_nodes=issued)
            else:
                item = next((item for item in missing if item.component == 2), None)
                if item is not None:
                    cache._preserve_mamba_reentry_node(
                        cache.tree_core.node_by_id(item.node_id),
                        no_host_reclaim=True, wait_for_completion=False,
                    )
                    self.counts["state_backup_attempts"] += 1
            self.work.move_to_end(context_id)
            break

    @staticmethod
    def _record(runtime, state, action, **fields):
        if runtime._opportunity_writer is not None:
            runtime._opportunity_writer.record({
                "event": "dormant_subagent_retention", "ts_ms": time.time() * 1000.,
                "workflow_id": state.key.root_workflow_id,
                "context_id": state.key.context_id, "context_epoch": state.key.context_epoch,
                "invocation_id": state.key.invocation_id,
                "session_id": state.key.session_id,
                "session_generation": state.key.session_generation,
                "agent_handle": state.handle, "action": action,
                "private_bytes": state.private_bytes, "probability": state.probability,
                "score": state.score, **fields,
            })

    def describe(self) -> dict:
        return {
            "policy": self.policy, "dormant_contexts": len(self.states),
            "pending_contexts": len(self.work),
            "pending_resume_labels": len(self.pending_resumes),
            "host_budget_bytes": self.host_budget_bytes,
            "device_budget_bytes": self.device_budget_bytes,
            "counts": dict(self.counts),
            "predictor": {
                "ready": self.estimator.ready, "counts": dict(self.estimator.counts),
                "brier_sum": self.estimator.brier_sum,
                "cold_start": "budgeted_mru",
                "cost_priors": {
                    "recompute_tokens_per_s": self.recompute_tokens_per_s,
                    "transfer_bytes_per_s": self.transfer_bytes_per_s,
                },
            },
        }
