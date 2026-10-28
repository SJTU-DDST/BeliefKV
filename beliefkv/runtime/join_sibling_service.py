"""Bounded ALL-JOIN service preference on top of native admission authority."""

from collections import OrderedDict
from dataclasses import asdict
from itertools import islice
import time

from beliefkv.control.causal_graph import JoinMode
from beliefkv.runtime.native_transfer_policy import consumer_input_reserve
from beliefkv.runtime.sglang_v0520_observer import observe_static_full_mamba_headroom


class JoinSiblingService:
    max_retry_groups = 8
    max_batch_members = 4
    max_queue_checks = 64
    max_prefix_checks = 8

    def __init__(self, admission):
        self.admission = admission
        self.waiting = OrderedDict()
        self.dirty = False
        self._retry_remaining = 0
        self.adder = None
        self.running = None
        self.admitted_keys = {}
        self.awaiting_service = {}
        self.served = {}
        self._control_ticks = 0

    def join(self, join_id):
        a = self.admission
        join = a.runtime.graph.joins.get(join_id)
        if (
            join is None or join.mode is not JoinMode.ALL
            or not 2 <= len(join.member_invocation_ids) <= a.max_sibling_members
            or not any(
                a._waiting_on_join(a.runtime.graph.invocations.get(identity), join)
                for identity in join.waiter_invocation_ids
            )
        ):
            return None
        return join

    def retry(self):
        if not self.dirty or not self.waiting:
            self.dirty = False
            self._retry_remaining = 0
            return
        a = self.admission
        limit = min(self.max_retry_groups, self._retry_remaining)
        for join_id in tuple(islice(self.waiting, limit)):
            self.waiting.move_to_end(join_id)
            a.counts["sibling_release_retry_groups"] += 1
            a._coadmit_join(join_id)
        # Continue the bounded pass at the next prefill, without rescanning
        # a failed group each decode step.
        self._retry_remaining = max(0, self._retry_remaining - limit)
        self.dirty = self._retry_remaining > 0

    def capacity_changed(self):
        self._retry_remaining = len(self.waiting)
        self.dirty = bool(self.waiting)

    def prepare(self, running, adder):
        ticks = self.admission.counts["control_ticks"]
        if ticks != self._control_ticks:
            self._control_ticks = ticks
            self.capacity_changed()
        self.retry()
        self.running, self.adder = running, adder

    def _membership(self, key):
        if key is None:
            return ()
        a = self.admission
        return tuple(
            join_id for join_id in islice(
                sorted(a.runtime._join_by_invocation.get(key.invocation_id, ())),
                self.max_retry_groups,
            )
            if (join := self.join(join_id)) is not None
            and key.invocation_id in join.member_invocation_ids - join.completed_member_ids
            and a._ready_sibling(key.invocation_id, join) is not None
        )

    def order(self, ordered, keys, *, now):
        a, runtime, adder = self.admission, self.admission.runtime, self.adder
        if len(ordered) < 2 or adder is None:
            return
        selected = getattr(adder, "can_run_list", None)
        input_limit = getattr(adder, "rem_input_tokens", None)
        maximum = getattr(adder, "max_running_requests", None)
        if (
            type(selected) not in (list, tuple)
            or type(input_limit) is not int or input_limit <= 0
            or type(maximum) is not int or maximum <= 0
            or getattr(adder, "dllm_config", None) is not None
        ):
            return
        slots = maximum - len(getattr(self.running, "reqs", ())) - len(selected)
        for name in ("max_prefill_bs", "prefill_max_requests"):
            bound = getattr(adder, name, None)
            if type(bound) is int and bound > 0:
                slots = min(slots, bound - len(selected))
        rows = getattr(getattr(runtime._native_cache, "req_to_token_pool", None), "available_size", None)
        if callable(rows):
            slots = min(slots, rows() - len(selected))
        chunk = getattr(adder, "rem_chunk_tokens", None)
        if type(chunk) is int:
            input_limit = min(input_limit, chunk)
        page = runtime._native_page_size
        if slots < 2 or input_limit < 2 * page:
            return
        # Admission remains native. These scalar lower bounds only avoid an
        # unnecessary group/path inspection when even two input pages cannot fit.
        allocator = getattr(runtime._native_cache, "token_to_kv_pool_allocator", None)
        available = getattr(allocator, "available_size", None)
        if callable(available):
            free = available()
            minimum_growth = page * (len(getattr(self.running, "reqs", ())) + 1)
            if type(free) is int and free < 2 * page + minimum_growth:
                a.counts["sibling_joint_fast_capacity_skips"] += 1
                return
        window = ordered[:self.max_queue_checks]
        memberships = {pair[0]: self._membership(keys[pair[0]]) for pair in window}
        groups = {}
        for pair in window:
            key = keys[pair[0]]
            if key is not None and a.allows(key):
                for join_id in memberships[pair[0]]:
                    groups.setdefault(join_id, []).append(pair)
        choices = [
            (ordered.index(pairs[0]), join_id, pairs)
            for join_id, pairs in groups.items() if len(pairs) >= 2
        ]
        if not choices:
            return
        position, join_id, siblings = min(choices, key=lambda row: row[:2])
        a.counts["sibling_joint_ready_plan_opportunities"] += 1
        if position >= min(slots - 1, self.max_prefix_checks):
            return
        headroom = observe_static_full_mamba_headroom(runtime._native_cache)
        if not headroom.observable:
            return
        prices = {}

        def price(pair):
            if pair[0] not in prices:
                key = keys[pair[0]]
                if key is None:
                    return None
                observation = runtime._read_request_reentry(pair[1], key=key)
                cost = runtime._request_restore_cost(pair[1], observation)
                if (
                    cost is None and observation is not None
                    and observation.get("checkpoint_tokens") == 0
                    and observation.get("missing_full_tokens") == 0
                    and observation.get("missing_mamba_slots") == 0
                ):
                    new_input = len(getattr(pair[1], "origin_input_ids", ()))
                    chunk_bound = getattr(runtime, "_native_chunk_input_reserve", None)
                    if type(chunk_bound) is int and chunk_bound > 0:
                        new_input = min(new_input, chunk_bound)
                    if new_input:
                        cost = 0, new_input
                if cost is None:
                    return None
                page = runtime._native_page_size
                prices[pair[0]] = (
                    observation["missing_full_tokens"],
                    (cost[1] + page - 1) // page * page,
                )
                a.counts["sibling_native_capacity_prices"] += 1
            return prices[pair[0]]

        prefix = ordered[:position + 1]
        if any(price(pair) is None for pair in prefix):
            return
        missing = sum(prices[pair[0]][0] for pair in prefix)
        inputs = sum(prices[pair[0]][1] for pair in prefix)
        budget = runtime._current_residency_budget(headroom=headroom)
        anchor = siblings[0]
        anchor_key = keys[anchor[0]]
        anchor_rank = runtime.frontier.admission_rank(anchor_key.invocation_id)[:2]
        chosen = []
        for peer in siblings[1:self.max_batch_members]:
            key = keys[peer[0]]
            if (
                runtime._ready_join_parent(peer[1], key)
                or runtime.frontier.admission_rank(key.invocation_id)[:2] != anchor_rank
                or len(prefix) + len(chosen) >= slots
            ):
                continue
            cost = price(peer)
            if cost is None:
                continue
            # A cold sibling may not jump over an unrelated resident/unknown
            # request merely to make a wider group.
            between = ordered[position + 1:ordered.index(peer)]
            if any(
                runtime._ready_join_parent(pair[1], keys[pair[0]])
                or now - runtime._visible_since.get(pair[1].rid, now) >= 10.
                for pair in between
            ):
                continue
            if runtime.enable_resident_first and cost[0] and any(
                pair not in siblings[:self.max_batch_members] for pair in between
            ):
                continue
            next_inputs, next_missing = inputs + cost[1], missing + cost[0]
            reserve = consumer_input_reserve(runtime, budget, next_inputs, adder=adder)
            if (
                next_inputs > input_limit
                or next_missing + reserve.total_tokens > headroom.device_full_free_tokens
            ):
                a.counts["sibling_joint_native_capacity_refused"] += 1
                continue
            chosen.append(peer)
            inputs, missing = next_inputs, next_missing
        if not chosen:
            return
        insert = position + 1
        for peer in chosen:
            ordered.remove(peer)
            ordered.insert(insert, peer)
            insert += 1
        a.counts["sibling_joint_batch_plans"] += 1
        a.counts["sibling_joint_batch_planned_members"] += 1 + len(chosen)

    def admitted(self, req):
        key = self.admission.runtime.visible.get(getattr(req, "rid", None))
        if key is None or self.served.get(key.invocation_id) == key:
            return
        joins = self._membership(key)
        if joins:
            self.admitted_keys[key] = joins

    def selected(self, batch):
        if not self.admitted_keys:
            return
        runtime = self.admission.runtime
        selected_at = time.monotonic()
        for req in getattr(batch, "reqs", ()):
            key = runtime.visible.get(req.rid)
            joins = self.admitted_keys.pop(key, ())
            if joins:
                self.awaiting_service[key] = joins, selected_at

    def serviced(self, batch):
        if not self.awaiting_service:
            return
        a, groups, selections = self.admission, {}, {}
        now = time.monotonic()
        for req in getattr(batch, "reqs", ()):
            key = a.runtime.visible.get(req.rid)
            pending = self.awaiting_service.pop(key, None)
            if pending is not None:
                joins, selected_at = pending
                self.served[key.invocation_id] = key
                selections[key] = selected_at
            else:
                joins = ()
            for join_id in joins:
                groups.setdefault(join_id, []).append(key)
        writer = getattr(a.runtime, "_opportunity_writer", None)
        for join_id, keys in groups.items():
            a.counts["sibling_native_first_services"] += len(keys)
            a.counts["sibling_joint_native_service_batches"] += len(keys) >= 2
            if writer is not None:
                writer.record({
                    "event": "agent_join_siblings_native_service",
                    "ts_ms": time.time() * 1000., "monotonic_s": now,
                    "join_id": join_id,
                    "request_ids": [key.request_id for key in keys],
                    "keys": [asdict(key) for key in keys],
                    "ready_to_selected_ms": [
                        max(0., selections[key] - a.requests.get(key.request_id, (None, now))[1]) * 1000.
                        for key in keys
                    ],
                    "ready_to_forward_return_ms": [
                        max(0., now - a.requests.get(key.request_id, (None, now))[1]) * 1000.
                        for key in keys
                    ],
                    "scope": "first completed native forward after admission; not pure kernel time",
                })

    def close(self):
        self.waiting.clear()
        self.admitted_keys.clear()
        self.awaiting_service.clear()
        self.served.clear()
        self.adder = self.running = None

    def forget(self, invocation):
        self.capacity_changed()
        self.served.pop(invocation, None)
        for mapping in (self.admitted_keys, self.awaiting_service):
            for key in tuple(mapping):
                if key.invocation_id == invocation:
                    mapping.pop(key)

