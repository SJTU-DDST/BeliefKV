"""Observed native FULL prefix unions for Agentix batch selection.

This logical batch budget does not allocate or release KV. Native admission
still decides whether current physical capacity and loading dependencies permit
execution. Decode growth is an explicit serving parameter, never a trace label.
"""

from collections import Counter
from dataclasses import dataclass

from beliefkv.runtime.sglang_v0520_admission import _request_key
from beliefkv.runtime.agentix_swap import validate_request_transport


@dataclass(frozen=True)
class _Footprint:
    request: object
    key: object
    input_ids: object
    input_count: int
    output_ids: object
    output_count: int
    known_tokens: int
    target: int
    extents: tuple
    resident: bool


class AgentixNativeCapacity:
    def __init__(self, transfers, *, context_limit, decode_growth_tokens):
        if (
            type(context_limit) is not int or context_limit <= 0
            or type(decode_growth_tokens) is not int or decode_growth_tokens <= 0
        ):
            raise ValueError("Agentix capacity requires explicit context and decode growth limits")
        self.transfers, self.cache = transfers, transfers.cache
        self.total_tokens = self.cache.token_to_kv_pool_allocator.size_full
        self.context_limit, self.decode_growth_tokens = context_limit, decode_growth_tokens
        self.probes, self.counts = {}, Counter()

    def prepare(self, requests):
        from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
        from sglang.srt.mem_cache.radix_cache import RadixKey

        staged = []
        identities = set()
        for req in requests:
            validate_request_transport(self.cache, req)
            key = _request_key(req)
            if key is None or req.rid in identities:
                raise ValueError("Agentix capacity requires unique observed native requests")
            identities.add(req.rid)
            observed = len(req.origin_input_ids) + len(req.output_ids)
            allocated = req.kv.kv_allocated_len
            if max(observed, allocated) > self.context_limit:
                raise ValueError("Agentix observed context exceeds the shared serving limit")
            req._refresh_fill_ids()
            tokens = req.full_untruncated_fill_ids
            limit = req._compute_max_prefix_len(len(tokens))
            matched = self.cache.match_prefix(MatchPrefixParams(key=RadixKey(
                tokens, req.extra_key, cache_salt=req.cache_salt, limit=limit,
            )))
            owner = key.root_workflow_id, key.context_id, key.context_epoch
            snapshot = self.transfers.capture(matched.best_match_node, owner=owner)
            target = min(
                self.context_limit, max(observed, allocated) + self.decode_growth_tokens,
            )
            staged.append((req, key, owner, snapshot, target, limit))
        probes = {}
        # A later match can split an earlier matched node. Resolve paths only
        # after all matches, so shared prefix lengths use the final tree.
        for req, key, owner, snapshot, target, limit in staged:
            path = () if snapshot is None else self.transfers.resolve(snapshot, owner=owner)
            if path is None:
                raise ValueError("Agentix native prefix changed during capacity preparation")
            extents = tuple(
                ((node.id, node.creation_time), len(node.key)) for node in path
            )
            prefix = sum(tokens for _, tokens in extents)
            if prefix > target:
                raise ValueError("Agentix native prefix exceeds its observed target")
            live = req.kv.holds_kv and req.effective_kv_committed_len() >= limit
            resident = bool(live or (
                prefix >= limit and prefix > 0
                and all(
                    node.component_data[0].value is not None
                    and node.load_back_pending_id is None for node in path
                )
            ))
            probes[req.rid] = _Footprint(
                req, key, req.origin_input_ids, len(req.origin_input_ids),
                req.output_ids, len(req.output_ids),
                max(len(req.origin_input_ids) + len(req.output_ids), req.kv.kv_allocated_len),
                target, extents, resident,
            )
        self.probes = probes
        self.counts["prepared_rounds"] += 1
        self.counts["prepared_requests"] += len(probes)

    def _probe(self, req):
        validate_request_transport(self.cache, req)
        probe = self.probes.get(req.rid)
        if probe is None or (
            probe.request is not req or _request_key(req) != probe.key
            or req.origin_input_ids is not probe.input_ids or req.output_ids is not probe.output_ids
            or len(req.origin_input_ids) != probe.input_count
            or len(req.output_ids) != probe.output_count
        ):
            raise ValueError("Agentix capacity snapshot does not describe this native request")
        for identity, tokens in probe.extents:
            node = self.cache.tree_core.node_by_id(identity[0])
            if node is None or node.creation_time != identity[1] or len(node.key) != tokens:
                raise ValueError("Agentix capacity snapshot predates a native prefix change")
        return probe

    def resident(self, req):
        return self._probe(req).resident

    def required_tokens(self, requests, *, include_growth=True):
        if type(include_growth) is not bool:
            raise ValueError("Agentix growth accounting requires an explicit boolean")
        shared, private = {}, 0
        identities = set()
        for req in requests:
            if req.rid in identities:
                raise ValueError("Agentix cannot budget the same native call twice")
            identities.add(req.rid)
            probe = self._probe(req)
            for identity, tokens in probe.extents:
                if identity in shared and shared[identity] != tokens:
                    raise ValueError("Agentix shared prefix has inconsistent native lengths")
                shared[identity] = tokens
            target = probe.target if include_growth else probe.known_tokens
            private += target - sum(tokens for _, tokens in probe.extents)
        return sum(shared.values()) + private

    def can_fit(self, req, selected):
        self.counts["fit_checks"] += 1
        return self.required_tokens((*selected, req)) <= self.total_tokens

    def status(self):
        return {
            "capacity_tokens": self.total_tokens, "context_limit": self.context_limit,
            "decode_growth_tokens": self.decode_growth_tokens,
            "prepared_requests": len(self.probes), "counts": dict(self.counts),
            "budget": "union_of_observed_native_prefix_extents_plus_private_input_and_decode_growth",
            "recorded_final_output_length_used": False,
            "native_physical_admission_authorized": False,
        }


class AgentixHostBacking:
    """Finite backing-space admission, separate from paper queue priorities."""

    def __init__(self, capacity, swap):
        self.capacity, self.swap = capacity, swap
        self.total_tokens = capacity.cache.host_pool_group.anchor_entry.host_pool.size
        self.reserve_tokens = capacity.context_limit
        if (
            type(self.total_tokens) is not int
            or self.total_tokens < 2 * self.reserve_tokens
            or capacity.total_tokens < self.reserve_tokens
        ):
            raise ValueError("Agentix backing requires two maximum Host contexts and one GPU context")
        self.normal_limit = self.total_tokens - self.reserve_tokens
        self.native, self.live = {}, ()
        self.single = None
        self.draining = False
        self.counts = Counter()

    def prepare(self, requests):
        requests = tuple(requests)
        self.capacity.prepare(requests)
        self.native = {req.rid: req for req in requests}
        self.live = tuple(
            req for req in requests if req.kv.holds_kv or req.rid in self.swap.paused
        )
        live_ids = {req.rid for req in self.live}
        if self.single not in live_ids:
            self.single = None
        demand = self.capacity.required_tokens(self.live)
        self.draining = self.single is not None or demand > self.normal_limit
        if self.draining and not any(self._finishes_within_backing(req) for req in self.live):
            raise ValueError("Agentix backing reserve was exhausted before a live call can finish")
        self.counts["prepared_rounds"] += 1
        self.counts["drain_rounds"] += self.draining

    def _finishes_within_backing(self, req):
        known = self.capacity._probe(req).known_tokens
        current = self.capacity.required_tokens(self.live, include_growth=False)
        return current + self.reserve_tokens - known <= self.total_tokens

    def _prospective(self, requests):
        union = {req.rid: req for req in self.live}
        union.update((req.rid, req) for req in requests)
        return tuple(union.values())

    def eligible(self, req):
        if self.draining:
            return (
                any(req is live for live in self.live)
                and (self.single is None or self.single == req.rid)
                and self._finishes_within_backing(req)
            )
        return self.capacity.required_tokens(self._prospective((req,))) <= self.normal_limit

    def can_fit(self, req, selected):
        if not self.capacity.can_fit(req, selected):
            return False
        if self.draining:
            return not selected and self.eligible(req)
        return (
            self.capacity.required_tokens(self._prospective((*selected, req)))
            <= self.normal_limit
        )

    def selected(self, identities):
        if self.draining and identities:
            if len(identities) != 1 or (
                self.single is not None and self.single != identities[0]
            ):
                raise ValueError("Agentix backing drain changed its selected finite call")
            self.single = identities[0]
            self.counts["selected_drain_rounds"] += 1

    def status(self):
        return {
            "host_capacity_tokens": self.total_tokens,
            "normal_live_budget_tokens": self.normal_limit,
            "emergency_context_tokens": self.reserve_tokens,
            "draining": self.draining, "drain_call_id": self.single,
            "live_calls": len(self.live), "counts": dict(self.counts),
            "priority_source": "paper_queue_order_with_finite_backing_eligibility",
            "future_output_length_used": False,
            "host_references_released_to_fake_capacity": False,
        }
