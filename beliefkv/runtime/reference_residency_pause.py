"""Revoke continuation eligibility when an agent loses reusable device KV."""

from dataclasses import asdict
import time

from beliefkv.core.events import RuntimeEventKind as Kind


class ReferenceResidencyPause:
    interval_s = .2
    max_checks = 8

    def __init__(self, admission):
        self.admission = admission
        self.paused = {}
        self.compared_requests = set()
        self.next_check = 0.
        self.cursor = 0

    def registered(self, key):
        if key.invocation_id in self.paused:
            self.compared_requests.add(key)
        self.inspect(key)

    def ready(self, item):
        a = self.admission
        key = a.latest.get(item.identity)
        return key is not None and a._candidate(
            key.request_id, key, include_first=True,
        ) is not None

    def _waiting(self, req, key):
        runtime = self.admission.runtime
        kv = getattr(req, "kv", None)
        return bool(
            kv is not None and getattr(kv, "req_pool_idx", False) is None
            and all(type(value := getattr(kv, name, None)) is int and value == 0
                    for name in ("kv_allocated_len", "kv_committed_len"))
            and not getattr(req, "output_ids", ())
            and callable(getattr(req, "finished", None)) and not req.finished()
            and runtime._executable_waiting_key(req, key=key) == key
            and not any(req is running for running in
                        getattr(runtime._native_running_batch, "reqs", ()))
        )

    def _pause(self, key, *, source, missing_tokens):
        a, runtime = self.admission, self.admission.runtime
        item = a.policy.programs.get(key.invocation_id) if a.policy is not None else None
        if (
            item is None or a.latest.get(key.invocation_id) != key
            or runtime.context_sessions.get(key.context_id) != key or runtime._terminal(key)
        ):
            return False
        req = a._live_requests.get(key.request_id)
        if item.pending and (req is None or not self._waiting(req, key)):
            return False
        self.paused[key.invocation_id] = key
        if item.pending:
            self.compared_requests.add(key)
        if not item.active:
            return False
        before = a.policy.remaining_capacity()
        a.policy._pause(item, time.monotonic())
        item.marked_for_pause = False
        runtime.semantic_revision += 1
        remember = getattr(a, "_remember_dispatch", None)
        if remember is not None:
            remember(key)
        a.counts["residency_pauses"] += 1
        a.counts["residency_pause_observed_missing_full_tokens"] += missing_tokens
        writer = getattr(runtime, "_opportunity_writer", None)
        if writer is not None:
            writer.record({
                "event": "agent_full_offload_paused",
                "ts_ms": time.time() * 1000., "monotonic_s": time.monotonic(),
                "key": asdict(key), "source": source,
                "observed_missing_full_tokens": missing_tokens,
                "released_logical_tokens": a.policy.remaining_capacity() - before,
                "pending_request": item.pending, "physical_capacity_credit": False,
            })
        return True

    def offloaded(self, key, freed, *, source):
        if type(freed.get(0)) is not int or freed[0] <= 0:
            return False
        a, runtime = self.admission, self.admission.runtime
        item = a.policy.programs.get(key.invocation_id) if a.policy is not None else None
        if item is None or item.pending or not runtime._agent_observed_blocked_budget_key(key):
            return False
        return self._pause(key, source=source, missing_tokens=freed[0])

    def inspect(self, key, *, cheap_first=False):
        a, runtime = self.admission, self.admission.runtime
        item = a.policy.programs.get(key.invocation_id) if a.policy is not None else None
        req = a._live_requests.get(key.request_id)
        if (
            item is None or not item.active or not item.pending or item.steps <= 1
            or req is None or not self._waiting(req, key)
            or key in self.compared_requests
        ):
            return False
        if cheap_first:
            hint = runtime._read_request_residency_hint(req, key=key)
            a.counts["residency_continuation_hint_checks"] += 1
            if hint is None or not (
                type(hint.get("missing_full_tokens")) is int and hint["missing_full_tokens"] > 0
            ):
                return False
        observed = runtime._read_request_reentry(req, key=key, refresh=True)
        a.counts["residency_continuation_checks"] += 1
        if observed is None:
            a.counts["residency_continuation_unknown"] += 1
            return False
        missing = observed.get("missing_full_tokens")
        if type(missing) is not int or missing <= 0:
            return False
        return self._pause(key, source="native_successor_host_checkpoint", missing_tokens=missing)

    def synchronize(self, native_order):
        now = time.monotonic()
        if now < self.next_check:
            return
        self.next_check = now + self.interval_s
        count = len(native_order)
        if not count:
            return
        for offset in range(min(count, self.max_checks)):
            req = native_order[(self.cursor + offset) % count]
            key = self.admission.runtime.visible.get(getattr(req, "rid", None))
            if key is not None:
                self.inspect(key, cheap_first=True)
        self.cursor = (self.cursor + self.max_checks) % count

    def events(self, events):
        a = self.admission
        for event in events:
            if event.kind not in (Kind.TOOL_END, Kind.LLM_SUBMIT):
                continue
            key = a.latest.get(event.invocation_id)
            if key is not None:
                self.inspect(key)

    def serviced(self, batch):
        a = self.admission
        for req in getattr(batch, "reqs", ()):
            record = a.requests.get(req.rid)
            if record is None:
                continue
            key = record[0]
            item = a.policy.programs.get(key.invocation_id)
            if item is not None and item.active:
                self.paused.pop(key.invocation_id, None)
                self.compared_requests.discard(key)

    def forget(self, invocation):
        self.paused.pop(invocation, None)
        self.compared_requests = {
            key for key in self.compared_requests if key.invocation_id != invocation
        }

    def close(self):
        self.paused.clear()
        self.compared_requests.clear()
