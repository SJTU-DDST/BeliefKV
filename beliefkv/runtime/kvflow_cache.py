"""KVFlow fixed-prefix priorities over the native FULL eviction heap."""

from __future__ import annotations

from collections import Counter
import math

from beliefkv.runtime.paper_prefix_transfers import NativePrefixTransfers


class KVFlowCachePriorities:
    def __init__(self, transfers: NativePrefixTransfers, *, max_anchors=512):
        transfers._safe()
        if type(max_anchors) is not int or max_anchors <= 0:
            raise ValueError("KVFlow requires a positive anchor bound")
        self.transfers, self.cache, self.max_anchors = transfers, transfers.cache, max_anchors
        self.full = self.cache.components[0]
        self.tree = self.cache.tree_core
        self._previous_priority = self.full.session_ref_eviction_strategy
        self._previous_observer = self.tree.beliefkv_full_resident_observer
        self._priority_callback = self._eviction_key
        self._observer_callback = self._node_changed
        self.full.session_ref_eviction_strategy = self._priority_callback
        self.tree.beliefkv_full_resident_observer = self._observer_callback
        self._steps = {}
        self._owners = {}
        self._paths = {}
        self._members = {}
        self._priorities = {}
        self._generations = {}
        self.revision = 0
        self.counts = Counter()

    def _refresh(self, identity):
        members = self._members.get(identity)
        if not members:
            self._members.pop(identity, None)
            self._priorities.pop(identity, None)
            self._generations.pop(identity, None)
            return
        self._priorities[identity] = min(self._steps.get(agent, math.inf) for agent in members)

    def _add_member(self, node, agent):
        self._paths[agent].add(node.id)
        self._members.setdefault(node.id, set()).add(agent)
        self._generations[node.id] = node.creation_time
        self._refresh(node.id)

    def _node_changed(self, node):
        self.revision += 1
        if self._previous_observer is not None:
            self._previous_observer(node)
        if self.transfers._node(node.id) is not node:
            for agent in self._members.pop(node.id, ()):
                self._paths[agent].discard(node.id)
            self._priorities.pop(node.id, None)
            self._generations.pop(node.id, None)
            return
        # A split adds a shared prefix above the original anchored suffix.
        inherited = set()
        for child in node.children.values():
            inherited.update(self._members.get(child.id, ()))
        for agent in inherited - self._members.get(node.id, set()):
            self._add_member(node, agent)
            self.counts["split_prefix_memberships"] += 1

    def _eviction_key(self, node):
        self.counts["priority_lookups"] += 1
        distance = (
            self._priorities.get(node.id, math.inf)
            if self._generations.get(node.id) == node.creation_time else math.inf
        )
        previous = self._previous_priority
        fallback = (
            previous(node) if previous is not None
            else self.tree.eviction_strategy.get_priority(node)
        )
        return -distance, fallback

    def set_anchor(self, agent, snapshot, *, owner):
        self.transfers._safe()
        if type(agent) is not str or not agent:
            raise ValueError("KVFlow requires a namespaced agent identity")
        old_owner = self._owners.get(agent)
        if old_owner is not None and old_owner[0] != owner[0]:
            raise ValueError("KVFlow agent identity cannot cross workflows")
        path = self.transfers.resolve(snapshot, owner=owner)
        if path is None:
            return False
        if agent not in self._paths and len(self._paths) >= self.max_anchors:
            raise ValueError("KVFlow live fixed-prefix anchor bound reached")
        self.forget(agent)
        self._owners[agent] = owner
        self._paths[agent] = set()
        for node in path:
            self._add_member(node, agent)
        self.counts["anchor_updates"] += 1
        return True

    def set_steps(self, steps):
        self.transfers._safe()
        if any(
            type(value) not in (int, float) or math.isnan(value) or value < 0
            for value in steps.values()
        ):
            raise ValueError("invalid KVFlow steps-to-execution")
        changed = {
            agent for agent in self._steps.keys() | steps.keys()
            if self._steps.get(agent, math.inf) != steps.get(agent, math.inf)
        }
        self._steps = dict(steps)
        touched = set()
        for agent in changed:
            touched.update(self._paths.get(agent, ()))
        for identity in touched:
            self._refresh(identity)
        self.counts["step_updates"] += 1
        self.counts["step_update_nodes"] += len(touched)

    def reclaim_for_prefetch(self, tokens, *, max_nodes):
        """Advance native heap eviction without its global write-back barrier."""
        self.transfers._safe()
        if (
            type(tokens) is not int or tokens < 0
            or type(max_nodes) is not int or max_nodes < 0
        ):
            raise ValueError("KVFlow prefetch reclaim requires nonnegative native budgets")
        if not tokens or not max_nodes:
            return 0, 0
        before = self.cache.token_to_kv_pool_allocator.available_size()
        tracker, checked, issued = {0: 0}, 0, False
        self.tree.evict_device_start(0, tokens)
        try:
            while checked < max_nodes:
                node_id, progress = self.cache._evict_device_next_node(0, tracker)
                if node_id is None:
                    if not progress:
                        break
                    continue
                checked += 1
                node = self.tree.node_by_id(node_id)
                if (
                    node.write_through_pending_id is not None
                    or node.load_back_pending_id is not None
                    or node_id in self.cache.ongoing_load_back
                    or node_id in self.cache.ongoing_write_through
                    or any(data.lock_ref for data in node.component_data)
                ):
                    continue
                action = self.cache._evict_device_leaf(node_id, tracker)
                if action is not None:
                    command = self.transfers._command("evict-d2h")
                    count = self.cache._execute_and_commit_kv_backup(
                        action, write_back=False, beliefkv_command_id=command,
                        no_host_reclaim=True,
                        beliefkv_before_enqueue=self.transfers._before_enqueue(node, command, "d2h"),
                        beliefkv_include_mamba=False, beliefkv_defer_writing=True,
                    )
                    issued |= bool(count)
                    self.counts["eviction_d2h_enqueued_tokens"] += count
                if tracker[0] >= tokens:
                    break
        finally:
            self.tree.evict_device_end(0)
            if issued:
                self.cache.cache_controller.start_writing()
        freed = tracker[0]
        if self.cache.token_to_kv_pool_allocator.available_size() - before != freed:
            raise ValueError("KVFlow native heap reclaim disagrees with unique capacity")
        self.counts["prefetch_reclaim_nodes"] += checked
        self.counts["prefetch_reclaimed_tokens"] += freed
        return freed, checked

    def forget(self, agent):
        self.transfers._safe()
        for identity in self._paths.pop(agent, ()):
            members = self._members.get(identity)
            if members is not None:
                members.discard(agent)
                self._refresh(identity)
        self._owners.pop(agent, None)

    def close(self):
        if self.full.session_ref_eviction_strategy is self._priority_callback:
            self.full.session_ref_eviction_strategy = self._previous_priority
        if self.tree.beliefkv_full_resident_observer is self._observer_callback:
            self.tree.beliefkv_full_resident_observer = self._previous_observer
        self._paths.clear()
        self._owners.clear()
        self._members.clear()
        self._priorities.clear()
        self._generations.clear()
