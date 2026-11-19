"""Bounded service protection for continuously ready requests."""

from dataclasses import asdict
from itertools import islice
import os
import time

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.sglang_v0520_admission import _request_key
from beliefkv.runtime.sglang_v0520_observer import observe_static_full_mamba_headroom


def parse_ready_service_fit_after(value):
    if (
        not isinstance(value, str) or not value.isascii() or not value.isdecimal()
        or len(value) > 4 or int(value) >= ReadyServiceGuard.timeout_s
    ):
        raise ValueError("ready service fit threshold requires 0..1799 seconds")
    return int(value)


class ReadyServiceGuard:
    timeout_s = 1800.

    def __init__(self, admission, *, fit_after_s=0):
        self.admission = admission
        self.since = {}
        self.target = None
        self.fit_after_s = fit_after_s
        self._native_growth_clip = (
            int(os.environ.get("SGLANG_CLIP_MAX_NEW_TOKENS_ESTIMATION", "4096"))
            if fit_after_s else 0
        )
        self.fit_target = None
        self.fit_attempted = set()
        self.fit_service_seen = False
        self.fit_available = False

    def _ready(self, key):
        a = self.admission
        found = a._candidate(
            key.request_id, key, include_first=True, include_active=True, include_marked=True,
        )
        return found is not None and a._can_resume(found[0])

    def begin(self, now):
        a, current = self.admission, {}
        gate = getattr(a.runtime, "_subagent_materialization", None)
        held_roots = set()
        for key, _arrived in a.requests.values():
            if not self._ready(key):
                if gate is None or not gate.held_ready_root(key):
                    continue
                # A capacity hold preserves the clock but confers no admission right.
                held_roots.add(key)
            previous = self.since.get(key.invocation_id)
            since = previous[1] if previous is not None and previous[0] == key else now
            current[key.invocation_id] = key, since
        self.since = current
        if self.fit_after_s:
            if self.fit_attempted:
                self.fit_attempted = {
                    key for key in self.fit_attempted
                    if a.latest.get(key.invocation_id) == key
                    and a.requests.get(key.request_id, (None,))[0] == key
                }
            if self.fit_target is not None and (
                a.latest.get(self.fit_target.invocation_id) != self.fit_target
                or not a.allows(self.fit_target)
            ):
                a.counts["ready_service_fit_grants_invalidated"] += 1
                self.fit_target = None
            self.fit_available = any(
                key not in held_roots and now >= since + self.fit_after_s
                and not a.policy.programs[key.invocation_id].active and key not in self.fit_attempted
                for key, since in current.values()
            )
        overdue = {
            key: since for key, since in current.values() if now >= since + self.timeout_s
            and key not in held_roots
        }
        target = self.target if self.target in overdue else min(
            overdue, key=lambda key: (overdue[key], key.request_id), default=None,
        )
        if target != self.target and target is not None:
            a.counts["ready_service_guard_selected"] += 1
            writer = getattr(a.runtime, "_opportunity_writer", None)
            if writer is not None:
                writer.record({
                    "event": "ready_service_guard_selected",
                    "ts_ms": time.time() * 1000., "monotonic_s": now,
                    "key": asdict(target), "ready_without_service_s": now - overdue[target],
                    "timeout_s": self.timeout_s, "physical_capacity_credit": False,
                    "scope": "single_logical_timeout_exception;native_admission_still_required",
                })
        self.target = target

    def elapsed_service_timeout(self, key, now):
        entry = self.since.get(key.invocation_id)
        return entry is not None and entry[0] == key and now >= entry[1] + self.timeout_s

    def _growth_tokens(self, request):
        maximum = getattr(getattr(request, "sampling_params", None), "max_new_tokens", None)
        if type(maximum) is not int or maximum < 0:
            return None
        return min(max(0, maximum - len(request.output_ids)), self._native_growth_clip)

    def _native_fit(self, key, request):
        runtime = self.admission.runtime
        cache = runtime._native_cache
        running = getattr(runtime._native_running_batch, "reqs", ())
        if (
            runtime.physical_disabled or getattr(cache, "tree_components", None) != (0,)
            or self._native_growth_clip < 0
            or not runtime._unallocated_waiting_full_request(key, request)
            or not any(row is request for row in islice(
                runtime._capacity_waiting_queue, 512,
            ))
            or runtime._live_critical_parent_demand() is not None
            or type(runtime._native_max_running) is not int
            or len(running) >= runtime._native_max_running
            or runtime._native_input_reserve <= 0
        ):
            return None
        headroom = observe_static_full_mamba_headroom(cache)
        if not headroom.observable:
            return None
        try:
            rows = cache.req_to_token_pool.available_size()
            total_capacity = cache.token_to_kv_pool_allocator.size_full
            evictable = cache.evictable_size()
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return None
        free = headroom.device_full_free_tokens
        if (
            type(rows) is not int or rows <= 0 or not runtime._native_prefill_slots
            or type(total_capacity) is not int or type(evictable) is not int
            or type(free) is not int or min(free, evictable) < 0
            or free + evictable > total_capacity
        ):
            return None
        prompt_tokens = len(request.origin_input_ids)
        page = runtime._native_page_size
        growth = self._growth_tokens(request)
        # Reserve the native growth estimate at its maximum ratio (1.0).
        running_growth = [self._growth_tokens(active) for active in running]
        if growth is None or None in running_growth:
            return None
        batch_pages = page * (len(running) + 1)
        gross_full_input_and_growth = prompt_tokens + growth + sum(running_growth) + batch_pages
        # PrefillAdder's strict full-input gate runs again after it pins the
        # prefix. Charging every matched device token to the pin cancels its
        # input credit, so this bound also holds if native matches a different prefix.
        if gross_full_input_and_growth >= free + evictable:
            return None
        observed = runtime._read_request_reentry(request, key=key, refresh=True)
        if observed is None and self.admission.policy.programs[key.invocation_id].steps == 1:
            # A new session may have no checkpoint. Bound its entire input.
            checkpoint = resident_prefix = 0
            missing = None
        elif isinstance(observed, dict):
            checkpoint = observed.get("checkpoint_tokens")
            resident_prefix = observed.get("device_checkpoint_tokens")
            missing = observed.get("missing_full_tokens")
            if (
                type(checkpoint) is not int or not 0 <= checkpoint < prompt_tokens
                or type(resident_prefix) is not int or type(missing) is not int
                or min(resident_prefix, missing) < 0 or resident_prefix + missing != checkpoint
                or observed.get("missing_mamba_slots") != 0
            ):
                return None
        else:
            return None
        inputs = prompt_tokens - checkpoint
        if runtime._native_chunk_input_reserve:
            inputs = min(inputs, runtime._native_chunk_input_reserve)
        inputs = ((inputs + page - 1) // page) * page
        if inputs > runtime._native_input_reserve:
            return None
        restore_or_input = (
            ((missing + page - 1) // page) * page + inputs if missing is not None
            else ((prompt_tokens + page - 1) // page) * page
        )
        first_chunk_and_restore = restore_or_input + batch_pages
        available_after_pin = free + evictable - resident_prefix
        if first_chunk_and_restore > available_after_pin:
            return None
        return {
            "device_free_tokens": free,
            "device_evictable_tokens": evictable,
            "device_prefix_pin_debit_tokens": resident_prefix,
            "available_after_prefix_pin_tokens": available_after_pin,
            "first_chunk_and_restore_tokens": first_chunk_and_restore,
            "full_input_and_growth_tokens": gross_full_input_and_growth - resident_prefix,
            "host_restore_tokens": missing,
        }

    def choose_fit(self, keys, selected, *, now, decay_capacity, protected):
        """Fit one overdue request, replacing at most one proposed ordinary grant."""
        if not self.fit_available or self.target is not None or self.fit_target is not None:
            return selected
        a = self.admission
        capacity = min(decay_capacity, a.policy.remaining_capacity())
        if capacity <= a.policy.buffer_tokens:
            return selected
        chosen = set(selected)
        sizes = {
            key: a.policy.programs[key.invocation_id].tokens + a.policy.buffer_tokens
            for key in keys
        }
        ordinary_tokens = sum(sizes[key] for key in selected)
        # Preserve a pending JOIN's existing opportunity even outside the chosen set.
        if any(
            key not in chosen and a._protected_join(key) and sizes[key] <= decay_capacity
            for key in keys
        ):
            return selected
        spare = capacity - ordinary_tokens
        runtime = a.runtime
        replaceable = []
        for key in reversed(selected):
            if protected(key):
                continue
            stage = runtime._final_request_stages.get(key.request_id)
            if (
                stage is not None and not stage.semantic_only
                and stage.child_id == key.invocation_id and stage.child_epoch == key.context_epoch
                and runtime._live_final_stage(stage)
            ):
                continue
            replaceable.append(key)
        limit = spare + max((sizes[key] for key in replaceable), default=0)
        if limit <= a.policy.buffer_tokens:
            return selected
        oldest = None
        for key in keys:
            entry = self.since.get(key.invocation_id)
            if (
                key in chosen or key in self.fit_attempted or entry is None or entry[0] != key
                or now < entry[1] + self.fit_after_s or sizes[key] > limit
                or protected(key)
            ):
                continue
            rank = (entry[1], key.request_id)
            if oldest is None or rank < oldest[0]:
                oldest = rank, key
        if oldest is None:
            return selected
        (fit_since, _), fit = oldest
        found = a._candidate(fit.request_id, fit, include_first=True)
        if found is None or not a._can_resume(found[0]):
            return selected
        deficit = sizes[fit] - spare
        displaced = (
            next(key for key in replaceable if sizes[key] >= deficit) if deficit > 0 else None
        )
        a.counts["ready_service_fit_native_checks"] += 1
        if displaced is not None and displaced != selected[-1]:
            a.counts["ready_service_fit_earlier_grant_native_checks"] += 1
        started = time.thread_time_ns()
        try:
            native = self._native_fit(fit, found[1])
        finally:
            a.counts["ready_service_fit_native_check_cpu_ns"] += time.thread_time_ns() - started
        if native is None:
            a.counts["ready_service_fit_native_skips"] += 1
            return selected
        retained = [key for key in selected if key != displaced] if displaced is not None else selected
        self.fit_target = fit
        self.fit_service_seen = False
        self.fit_attempted.add(fit)
        a.counts["ready_service_fit_grants"] += 1
        a.counts["ready_service_fit_displaced_grants"] += displaced is not None
        writer = getattr(runtime, "_opportunity_writer", None)
        if writer is not None:
            writer.record({
                "event": "ready_service_fit_granted",
                "ts_ms": time.time() * 1000., "monotonic_s": now,
                "key": asdict(fit), "ready_without_service_s": now - fit_since,
                "displaced": asdict(displaced) if displaced is not None else None,
                "logical_capacity_tokens": capacity,
                "selected_tokens": sum(sizes[key] for key in retained) + sizes[fit],
                "physical_capacity_credit": False, **native,
            })
        return [*retained, fit]

    def allows_timeout(self, key):
        allowed = key is not None and key == self.target and self._ready(key)
        if allowed:
            self.admission.counts["ready_service_guard_logical_grants"] += 1
        return allowed

    def order(self, ordered, keys, *, protected_head):
        if self.target is None or not self.admission.allows(self.target):
            return
        floor = int(protected_head)
        for index in range(floor, len(ordered)):
            if keys[ordered[index][0]] == self.target:
                if index != floor:
                    ordered.insert(floor, ordered.pop(index))
                    self.admission.counts["ready_service_guard_native_promotions"] += 1
                return

    def serviced(self, batch, completed_ids=()):
        if not self.since and self.fit_target is None:
            return
        a, workflows = self.admission, set()
        for req in getattr(batch, "reqs", ()):
            record = a.requests.get(req.rid)
            if record is None:
                continue
            key, visible = record[0], a.runtime.visible.get(req.rid)
            # The native completion callback removes visibility before notifying us.
            terminal = (
                visible is None and req.rid in completed_ids
                and a._live_requests.get(req.rid) is req
                and a.latest.get(key.invocation_id) == key and _request_key(req) == key
            )
            if visible == key or terminal:
                workflows.add(key.root_workflow_id)
                if (
                    key == self.fit_target and a._live_requests.get(req.rid) is req
                    and a.latest.get(key.invocation_id) == key and _request_key(req) == key
                ):
                    writer = getattr(a.runtime, "_opportunity_writer", None)
                    if not self.fit_service_seen:
                        a.counts["ready_service_fit_first_native_service"] += 1
                        self.fit_service_seen = True
                        if writer is not None:
                            writer.record({
                                "event": "ready_service_fit_first_native_service",
                                "ts_ms": time.time() * 1000., "monotonic_s": time.monotonic(),
                                "key": asdict(key),
                            })
                    if req.rid in completed_ids:
                        a.counts["ready_service_fit_native_completed"] += 1
                        self.fit_target = None
        if not workflows:
            return
        now = time.monotonic()
        for identity, (key, _since) in tuple(self.since.items()):
            if key.root_workflow_id in workflows:
                self.since[identity] = key, now
        if self.target is not None and self.target.root_workflow_id in workflows:
            a.counts["ready_service_guard_served"] += 1
            self.target = None

    def forget(self, invocation):
        self.since.pop(invocation, None)
        if self.target is not None and self.target.invocation_id == invocation:
            self.target = None
        if self.fit_target is not None and self.fit_target.invocation_id == invocation:
            self.fit_target = None

    def events(self, events):
        for event in events:
            if event.kind in (
                Kind.TOOL_START, Kind.JOIN_WAIT, Kind.RETURN, Kind.INVOCATION_CANCEL,
            ):
                self.forget(event.invocation_id)

    def status(self):
        return {
            "timeout_s": self.timeout_s,
            "clock": "continuously_ready_without_actual_native_workflow_service",
            "target": asdict(self.target) if self.target is not None else None,
            "max_concurrent_exceptions": 1,
            "ordinary_rank_changes": False,
            "logical_overcommit_possible_only_for_timeout_exception": True,
            **({
                "fit_after_s": self.fit_after_s,
                "fit_target": asdict(self.fit_target) if self.fit_target is not None else None,
                "fit_max_replaced_ordinary_grants": 1,
                "fit_logical_capacity": "current_undiscounted",
                "fit_native_capacity": "free_and_native_evictable_after_prefix_pin",
            } if self.fit_after_s else {}),
            "physical_capacity_credit": False,
        }
