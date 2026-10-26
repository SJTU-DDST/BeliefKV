"""Native FULL extents and pending admission slots for TokenCake spatial policy.

The spatial adapter calls its guard before allocation and settles complete
batches afterward. Lifecycle/restore debt remains required for a full baseline.
"""

from collections import Counter
from dataclasses import dataclass, field

from beliefkv.policy.tokencake_capacity import TokenCakeCapacityWindow, take_runs
from beliefkv.runtime.sglang_v0520_admission import _request_key


@dataclass
class _Extent:
    tokens: int
    charges: tuple
    users: set = field(default_factory=set)
    reservations: set = field(default_factory=set)


@dataclass
class _Claim:
    key: object
    agent_type: str
    prefix: set
    private: tuple
    target: int
    leaf: tuple | None
    pending_private: int = 0


@dataclass
class _Reservation:
    owner: tuple
    agent_type: str
    prefix: set
    leaf: tuple
    lock: object


@dataclass(frozen=True)
class NativeCapacityProbe:
    request: object
    key: object
    input_ids: object
    namespace: tuple
    extents: tuple
    target_tokens: int
    host_prefix_tokens: int = 0
    external_prefix_tokens: int = 0


class NativeCapacityNotReady(ValueError):
    """The observed prefix still has an unresolved native loading dependency."""


class TokenCakeNativeCapacity:
    def __init__(self, transfers, *, generation_budget, context_limit):
        transfers._safe()
        if (
            type(generation_budget) is not int or generation_budget < 0
            or type(context_limit) is not int or context_limit <= 0
        ):
            raise ValueError("TokenCake requires an explicit trace-independent generation budget")
        self.transfers, self.cache = transfers, transfers.cache
        capacity = self.cache.token_to_kv_pool_allocator.size_full
        self.window = TokenCakeCapacityWindow(capacity)
        self.generation_budget, self.context_limit = generation_budget, context_limit
        self.extents, self.requests, self.reservations = {}, {}, {}
        self._approved = {}
        self._revision = 0
        self.counts = Counter()
        self._previous_observer = self.cache.tree_core.beliefkv_full_resident_observer
        self._observer = self._node_changed
        self.cache.tree_core.beliefkv_full_resident_observer = self._observer

    def _identity(self, node):
        return node.id, node.creation_time

    def _node_changed(self, node):
        if self._previous_observer is not None:
            self._previous_observer(node)
        identity = self._identity(node)
        if self.transfers._node(node.id) is not node:
            if identity in self.extents:
                raise ValueError("native cache deleted a live TokenCake extent")
            return
        for child in node.children.values():
            child_id = self._identity(child)
            old = self.extents.get(child_id)
            if old is None or old.tokens == len(child.key):
                continue
            prefix_tokens = old.tokens - len(child.key)
            if (
                prefix_tokens != len(node.key) or prefix_tokens <= 0
                or identity in self.extents
            ):
                raise ValueError("native split disagrees with TokenCake extent ownership")
            prefix_runs, suffix_runs = take_runs(old.charges, prefix_tokens)
            self.extents[identity] = _Extent(
                prefix_tokens, prefix_runs, set(old.users), set(old.reservations),
            )
            old.tokens, old.charges = len(child.key), suffix_runs
            for request_id in old.users:
                self.requests[request_id].prefix.add(identity)
            for reservation_id in old.reservations:
                self.reservations[reservation_id].prefix.add(identity)
            self.counts["split_extents"] += 1
            self._revision += 1
        extent = self.extents.get(identity)
        if extent is not None and (
            extent.tokens != len(node.key) or node.component_data[0].value is None
        ):
            raise ValueError("native cache changed a live TokenCake physical extent")

    def _free(self):
        return (
            self.cache.token_to_kv_pool_allocator.available_size()
            + self.cache.evictable_size()
            - sum(claim.pending_private for claim in self.requests.values())
        )

    def refresh(self):
        self.transfers._safe()
        self.window.refresh(self._free())

    def set_plan(self, plan):
        self.transfers._safe()
        self.window.set_plan(plan)
        self._revision += 1
        self.refresh()

    def set_waiting(self, scores):
        self.transfers._safe()
        self.window.set_waiting(scores)
        self._revision += 1
        self.refresh()

    def _target(self, req):
        # The replay sampler's max_new_tokens reveals the recorded final length.
        # Use only the separately configured serving budget and observed state.
        observed = max(
            len(req.origin_input_ids) + len(getattr(req, "output_ids", ())),
            getattr(getattr(req, "kv", None), "kv_allocated_len", 0),
        )
        if observed > self.context_limit:
            raise ValueError("TokenCake observed request exceeds the common context limit")
        return min(self.context_limit, max(
            len(req.origin_input_ids) + self.generation_budget,
            observed,
        ))

    @staticmethod
    def _owner(key):
        return key.root_workflow_id, key.context_id, key.context_epoch

    def _path_extents(self, node_id, key, namespace, *, allow_loading=False):
        if node_id is None or self.cache.is_root(node_id):
            return ()
        snapshot = self.transfers.capture(node_id, owner=self._owner(key))
        if snapshot is None or snapshot.namespace != namespace:
            raise ValueError("TokenCake could not prove its native prefix path")
        path = self.transfers.resolve(snapshot, owner=self._owner(key))
        if path is None:
            raise ValueError("TokenCake could not resolve its native prefix path")
        if any(
            node.component_data[0].value is None
            or not allow_loading and node.load_back_pending_id is not None for node in path
        ):
            raise NativeCapacityNotReady("TokenCake prefix is not ready for native admission")
        return tuple((self._identity(node), len(node.key)) for node in path)

    def probe(self, req):
        from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
        from sglang.srt.mem_cache.radix_cache import RadixKey

        self.transfers._safe()
        key = _request_key(req)
        if key is None:
            raise ValueError("TokenCake requires a live tagged request identity")
        tokens = req.origin_input_ids
        if getattr(req, "output_ids", ()):
            req._refresh_fill_ids()
            tokens = req.full_untruncated_fill_ids
        match = self.cache.match_prefix(MatchPrefixParams(key=RadixKey(
            tokens, req.extra_key, cache_salt=req.cache_salt,
            limit=req._compute_max_prefix_len(len(tokens)),
        )))
        namespace = req.extra_key, req.cache_salt or None
        extents = self._path_extents(match.last_device_node, key, namespace)
        external = sum(
            tokens for identity, tokens in extents
            if identity not in self.extents
            and self.transfers._node(identity[0]).component_data[0].lock_ref > 0
        )
        self.counts["admission_probes"] += 1
        return NativeCapacityProbe(
            req, key, req.origin_input_ids, namespace,
            extents, self._target(req), match.host_hit_length, external,
        )

    def admission_demand(self, probe):
        shared = sum(tokens for identity, tokens in probe.extents if identity in self.extents)
        return max(0, probe.target_tokens - shared)

    def _capacity_view(self, external_prefix_tokens):
        if not external_prefix_tokens:
            return self.window
        view = TokenCakeCapacityWindow(
            self.window.total,
            priority_borrow_score_margin=self.window.priority_borrow_score_margin,
        )
        view.set_plan(self.window.plan)
        view.used = self.window.used.copy()
        view.growth = dict(self.window.growth)
        view.waiting_scores = dict(self.window.waiting_scores)
        view.refresh(self.window.free + external_prefix_tokens)
        return view

    def _reservation_path(self, snapshot, owner):
        self.transfers._safe()
        path = self.transfers.resolve(snapshot, owner=owner)
        if path is None:
            raise ValueError("TokenCake upload reservation lost its observed prefix")
        return path

    def can_reserve_prefix(self, snapshot, *, owner, agent_type, score, new_tokens=0):
        """Check spatial commitments before allocating another upload grant."""
        if type(new_tokens) is not int or new_tokens < 0:
            raise ValueError("TokenCake upload grant must be a nonnegative token count")
        path = self._reservation_path(snapshot, owner)
        demand, external = new_tokens, 0
        for node in path:
            if node.component_data[0].value is None:
                break
            if self._identity(node) not in self.extents:
                demand += len(node.key)
                external += len(node.key) if node.component_data[0].lock_ref > 0 else 0
        self.refresh()
        return self._capacity_view(external).can_admit(agent_type, demand, score=score)

    def reserve_prefix(self, reservation_id, snapshot, *, owner, agent_type, score):
        """Own the actual uploaded prefix; H2D ACK keeps its native source lock."""
        if type(reservation_id) is not str or not reservation_id:
            raise ValueError("TokenCake upload reservation requires a lifecycle identity")
        path = self._reservation_path(snapshot, owner)
        if any(node.component_data[0].value is None for node in path):
            raise ValueError("TokenCake cannot reserve a prefix without real GPU allocation")
        identities = {self._identity(node) for node in path}
        old = self.reservations.get(reservation_id)
        if old is not None and (
            old.owner != owner or old.agent_type != agent_type or not old.prefix <= identities
        ):
            raise ValueError("TokenCake upload reservation changed its ownership or shrank")
        missing = [node for node in path if self._identity(node) not in self.extents]
        external = sum(
            len(node.key) for node in missing if node.component_data[0].lock_ref > 0
        )
        self.refresh()
        view = self._capacity_view(external)
        demand = sum(len(node.key) for node in missing)
        if not view.can_admit(agent_type, demand, score=score):
            raise ValueError("TokenCake physical upload exceeds its approved spatial capacity")
        self.window.free = view.free
        self.window.shared_available = view.shared_available
        self.window.reserved_available = dict(view.reserved_available)
        charges = self.window.charge(agent_type, demand, score=score)
        try:
            lock = self.cache.inc_lock_ref(snapshot.leaf_id).to_dec_params()
        except BaseException:
            self.window.release(charges)
            self.refresh()
            raise
        for node in missing:
            owned, charges = take_runs(charges, len(node.key))
            self.extents[self._identity(node)] = _Extent(len(node.key), owned)
        for identity in identities:
            self.extents[identity].reservations.add(reservation_id)
        self.reservations[reservation_id] = _Reservation(
            owner, agent_type, identities, (snapshot.leaf_id, snapshot.leaf_creation_time), lock,
        )
        if old is not None:
            self.cache.dec_lock_ref(old.leaf[0], old.lock)
        self._revision += 1
        self.refresh()
        self.counts["upload_reservation_updates"] += 1
        self.counts["upload_reservation_new_tokens"] += demand

    def release_reservation(self, reservation_id):
        self.transfers._safe()
        old = self.reservations.pop(reservation_id, None)
        if old is None:
            return False
        for identity in old.prefix:
            extent = self.extents[identity]
            extent.reservations.remove(reservation_id)
            if not extent.users and not extent.reservations:
                self.window.release(extent.charges)
                del self.extents[identity]
        node = self.transfers._node(old.leaf[0])
        if node is None or self._identity(node) != old.leaf:
            raise ValueError("TokenCake native upload lock lost its physical generation")
        self.cache.dec_lock_ref(old.leaf[0], old.lock)
        self._revision += 1
        self.refresh()
        self.counts["upload_reservations_released"] += 1
        return True

    def consume_reservation(self, reservation_id, req):
        from sglang.srt.mem_cache.unified_cache.component_type import ComponentType

        self.transfers._safe()
        reserved, claim = self.reservations.get(reservation_id), self.requests.get(req.rid)
        if reserved is None or claim is None:
            return False
        if claim.key != _request_key(req) or self._owner(claim.key) != reserved.owner:
            return False
        if not reserved.prefix <= claim.prefix:
            return False
        receipt = getattr(req, "lock_receipt", getattr(req, "lock", None))
        if (
            receipt is None or getattr(receipt, "node_id", None) != req.last_node
            or ComponentType.FULL in getattr(receipt, "skipped_lock_components", ())
        ):
            raise ValueError("TokenCake upload handoff requires the real consumer's native lock")
        self.release_reservation(reservation_id)
        self.counts["upload_reservations_consumed"] += 1
        return True

    def allows(self, probe, *, agent_type, score, borrow_reserved=False):
        self._validate_probe(probe)
        old = self.requests.get(probe.key.request_id)
        if old is not None:
            if old.key != probe.key or old.agent_type != agent_type:
                raise ValueError("TokenCake admitted request changed its ownership")
            return True
        self.refresh()
        view = self._capacity_view(probe.external_prefix_tokens)
        allowed = view.can_admit(
            agent_type, self.admission_demand(probe), score=score, borrow_reserved=borrow_reserved,
        )
        if allowed:
            self._approved[probe.key.request_id] = (
                probe, agent_type, score, self._revision,
                view if view is not self.window else None, borrow_reserved,
            )
        else:
            self._approved.pop(probe.key.request_id, None)
        return allowed

    def _validate_probe(self, probe):
        self.transfers._safe()
        req = probe.request
        if (
            _request_key(req) != probe.key or req.origin_input_ids is not probe.input_ids
            or (req.extra_key, req.cache_salt or None) != probe.namespace
            or self._target(req) != probe.target_tokens
        ):
            raise ValueError("TokenCake admission request identity or budget changed")
        for identity, tokens in probe.extents:
            node = self.transfers._node(identity[0])
            if (
                node is None or self._identity(node) != identity
                or len(node.key) != tokens or node.component_data[0].value is None
            ):
                raise ValueError("TokenCake admission prefix changed; a fresh probe is required")

    def stage(self, req, *, agent_type, score, probe, planned_private_tokens):
        """Reserve native-admitted private slots before the batch allocates them."""
        return self._commit(
            req, agent_type=agent_type, score=score, probe=probe,
            planned_private_tokens=planned_private_tokens,
        )

    def commit(self, req, *, agent_type, score, probe=None):
        return self._commit(req, agent_type=agent_type, score=score, probe=probe)

    def _commit(self, req, *, agent_type, score, probe, planned_private_tokens=None):
        self.transfers._safe()
        key = _request_key(req)
        if key is None:
            raise ValueError("TokenCake physical commit lacks a request identity")
        old = self.requests.get(req.rid)
        borrow_reserved = False
        if old is None:
            approval = self._approved.get(req.rid)
            if (
                probe is None or probe.request is not req
                or approval is None or approval[0] is not probe
                or approval[1:4] != (agent_type, score, self._revision)
            ):
                raise ValueError("TokenCake first commit requires its admission probe")
            self._validate_probe(probe)
            borrow_reserved = approval[5]
            if approval[4] is not None:
                view = approval[4]
                self.window.free = view.free
                self.window.shared_available = view.shared_available
                self.window.reserved_available = dict(view.reserved_available)
                self.counts["external_prefix_tokens_adopted"] += probe.external_prefix_tokens
        elif old.key != key or old.agent_type != agent_type:
            raise ValueError("TokenCake physical commit changed request ownership")
        if old is not None and old.pending_private:
            raise ValueError("native batch allocation must settle pending private slots first")
        node_id = getattr(req, "last_node", None)
        node = self.transfers._node(node_id) if node_id is not None else None
        leaf = self._identity(node) if node is not None else None
        extents = (
            tuple((identity, self.extents[identity].tokens) for identity in old.prefix)
            if old is not None and old.leaf == leaf
            else self._path_extents(
                node_id, key, (req.extra_key, req.cache_salt or None), allow_loading=True,
            )
        )
        prefix_units = sum(tokens for _, tokens in extents)
        allocated = getattr(getattr(req, "kv", None), "kv_allocated_len", None)
        if type(allocated) is not int or allocated < 0:
            raise ValueError("TokenCake requires the native allocated KV length")
        pending_private = 0
        if planned_private_tokens is not None:
            if type(planned_private_tokens) is not int or planned_private_tokens < 0:
                raise ValueError("TokenCake private admission plan must be a nonnegative count")
            for identity, _ in extents:
                node = self.transfers._node(identity[0])
                if node is None or node.component_data[0].lock_ref <= 0:
                    raise ValueError("TokenCake staged prefix lacks native admission ownership")
            actual_private = max(0, allocated - prefix_units)
            if planned_private_tokens < actual_private:
                raise ValueError("TokenCake private plan dropped an existing native allocation")
            pending_private = planned_private_tokens - actual_private
            allocated = prefix_units + planned_private_tokens
        if allocated < prefix_units:
            raise ValueError("TokenCake physical extent exceeds the native request allocation")
        private_units = allocated - prefix_units
        target = self._target(req)
        if allocated > target:
            raise ValueError("TokenCake native allocation exceeds the common context limit")
        current_ids = {identity for identity, _ in extents}
        if old is not None and not old.prefix <= current_ids:
            raise ValueError("TokenCake live prefix shrank without native request retraction")
        missing = tuple((identity, units) for identity, units in extents if identity not in self.extents)
        previous_runs = old.private if old is not None else ()
        previous_units = sum(units for _, units in previous_runs)
        required = private_units + sum(units for _, units in missing)
        added = max(0, required - previous_units)
        if added:
            charges = previous_runs + self.window.charge(
                agent_type, added, score=score, already_admitted=old is not None,
                borrow_reserved=borrow_reserved,
            )
        else:
            charges, released = take_runs(previous_runs, required)
            self.window.release(released)
        for identity, units in missing:
            owned, charges = take_runs(charges, units)
            self.extents[identity] = _Extent(units, owned)
        private, charges = take_runs(charges, private_units)
        if charges:
            raise ValueError("TokenCake private extent accounting left unused charges")
        for identity, _ in extents:
            self.extents[identity].users.add(req.rid)
        self.requests[req.rid] = _Claim(
            key, agent_type, current_ids, private, target, leaf, pending_private,
        )
        self._approved.pop(req.rid, None)
        self._revision += 1
        self.window.set_growth(req.rid, target - allocated)
        self.refresh()
        self.counts[
            "admission_stages" if planned_private_tokens is not None else "physical_commits"
        ] += 1
        self.counts["private_admission_slots_staged"] += pending_private

    def mark_allocated(self, requests):
        """Settle the complete selected batch before refreshing native free space."""
        self.transfers._safe()
        settling = []
        for req in requests:
            claim = self.requests.get(req.rid)
            if claim is None:
                continue
            prefix = sum(self.extents[identity].tokens for identity in claim.prefix)
            expected = prefix + sum(count for _, count in claim.private)
            if (
                claim.key != _request_key(req)
                or req.kv.kv_allocated_len < expected
            ):
                raise ValueError("native batch did not materialize its admitted private slots")
            settling.append(claim)
        for claim in settling:
            self.counts["private_admission_slots_materialized"] += claim.pending_private
            claim.pending_private = 0
        self.refresh()

    def release(self, request_id):
        self.transfers._safe()
        self._approved.pop(request_id, None)
        claim = self.requests.pop(request_id, None)
        if claim is None:
            return False
        self.window.release(claim.private)
        for identity in claim.prefix:
            extent = self.extents[identity]
            extent.users.remove(request_id)
            if not extent.users and not extent.reservations:
                self.window.release(extent.charges)
                del self.extents[identity]
        self.window.forget_growth(request_id)
        self.refresh()
        self.counts["requests_released"] += 1
        self._revision += 1
        return True

    def close(self):
        for reservation_id in tuple(self.reservations):
            self.release_reservation(reservation_id)
        for request_id in tuple(self.requests):
            self.release(request_id)
        self._approved.clear()
        if self.cache.tree_core.beliefkv_full_resident_observer is self._observer:
            self.cache.tree_core.beliefkv_full_resident_observer = self._previous_observer
