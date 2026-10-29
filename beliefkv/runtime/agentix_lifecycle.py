"""Native call lifetimes and ACK-driven selective preemption for Agentix.

The paper scheduler supplies selected calls. These components preserve native
request ownership and do not implement admission, victim ranking or a Full policy.
"""

from collections import Counter
from dataclasses import dataclass
from functools import wraps
import time

from beliefkv.runtime.sglang_v0520_admission import _request_key, compile_native_prefill_plan


@dataclass
class _Call:
    request: object
    key: object
    arrived_ms: float
    input_ids: object
    output_ids: object
    parameters: object
    namespace: tuple


class AgentixCallLifecycle:
    def __init__(self, policy, execution, swap, *, clock_ms):
        if execution.executor.policy is not policy or not callable(clock_ms):
            raise ValueError("Agentix lifecycle requires its bound executor policy and clock")
        self.policy, self.execution, self.swap, self.clock_ms = policy, execution, swap, clock_ms
        self.requests, self.terminals = {}, {}
        self.program_calls, self.counts = Counter(), Counter()
        self.retiring, self.closed_programs = set(), set()
        self.awaiting_registration = {}

    @staticmethod
    def _namespace(req):
        return req.extra_key, req.cache_salt, req.session_id, req.session_generation

    def _validate(self, req):
        call = self.requests.get(req.rid)
        key = _request_key(req)
        if call is None or key is None or (
            req is not call.request or req.origin_input_ids is not call.input_ids
            or req.output_ids is not call.output_ids
            or req.sampling_params is not call.parameters
            or self._namespace(req) != call.namespace
            or (
                key.request_id, key.root_workflow_id, key.invocation_id, key.context_id,
                key.context_epoch, key.session_id, key.session_generation,
            ) != (
                call.key.request_id, call.key.root_workflow_id, call.key.invocation_id,
                call.key.context_id, call.key.context_epoch,
                call.key.session_id, call.key.session_generation,
            )
            or key.attempt_id < call.key.attempt_id
        ):
            details = {"request_id": req.rid, "registered": call is not None, "key": key}
            if call is not None:
                details.update(
                    same_request=req is call.request,
                    same_input=req.origin_input_ids is call.input_ids,
                    same_output=req.output_ids is call.output_ids,
                    same_sampler=req.sampling_params is call.parameters,
                    namespace=self._namespace(req), expected_namespace=call.namespace,
                    expected_key=call.key,
                )
            raise ValueError(
                "Agentix native request changed call, sampler or program ownership: "
                + repr(details)
            )
        return call, key

    def registered(self, req, key):
        self.poll()
        if (
            key is None or key != _request_key(req) or key.request_id in self.requests
            or key.root_workflow_id in self.closed_programs
        ):
            raise ValueError("Agentix registration requires a unique live native call")
        now = self.clock_ms()
        self.requests[req.rid] = _Call(
            req, key, now, req.origin_input_ids, req.output_ids, req.sampling_params,
            self._namespace(req),
        )
        self.program_calls[key.root_workflow_id] += 1
        self.awaiting_registration[req.rid] = None
        self._register_ready()
        self.counts["registered"] += 1

    def _register_ready(self):
        blocked = {
            self.requests[request].key.root_workflow_id for request in self.terminals
            if request in self.policy.calls
        }
        for request in tuple(self.awaiting_registration):
            call = self.requests[request]
            if call.key.root_workflow_id not in blocked and request not in self.terminals:
                self.policy.arrive(
                    request, call.key.root_workflow_id, now_ms=self.clock_ms(),
                    queued_since_ms=call.arrived_ms,
                )
                del self.awaiting_registration[request]

    def completed(self, batch, completed_ids):
        native = {req.rid: req for req in batch.reqs}
        if len(set(completed_ids)) != len(completed_ids) or any(
            request not in native for request in completed_ids
        ):
            raise ValueError("Agentix completions must belong to the native result batch")
        for request in completed_ids:
            req = native[request]
            if request not in self.requests:
                self.counts["duplicate_completed_results"] += 1
                continue
            self._validate(req)
            if not req.finished() or request in self.awaiting_registration:
                raise ValueError("Agentix cannot complete an unfinished native call")
            self.terminals.setdefault(request, "complete")
        self.poll()

    def aborted(self, request_id, *, all_requests=False):
        for request in self.requests:
            if all_requests or request.startswith(request_id):
                self.terminals[request] = "cancel"
        self.poll()

    def poll(self):
        self.execution.poll()
        if any(proof.rid in self.terminals for proof in self.swap.pending):
            self.swap.cancel_preparation()
        for request, action in tuple(self.terminals.items()):
            if request in self.policy.calls and self.policy.has_pending_executor(request):
                continue
            call = self.requests[request]
            now = self.clock_ms()
            self.swap.release(request)
            if request in self.awaiting_registration:
                del self.awaiting_registration[request]
            elif action == "complete":
                self.policy.complete(request, now_ms=now)
            else:
                self.policy.cancel(request, now_ms=now)
            del self.terminals[request]
            del self.requests[request]
            program = call.key.root_workflow_id
            self.program_calls[program] -= 1
            self.counts["completed" if action == "complete" else "cancelled"] += 1
        self._register_ready()
        for program in tuple(self.retiring):
            if not self.program_calls[program]:
                self.policy.forget(program)
                self.program_calls.pop(program, None)
                self.retiring.remove(program)
                self.counts["programs_retired"] += 1

    def retire(self, program_id):
        self.closed_programs.add(program_id)
        for request, call in self.requests.items():
            if call.key.root_workflow_id == program_id:
                self.terminals.setdefault(request, "cancel")
        self.retiring.add(program_id)
        self.poll()

    def requeued(self, requests, *, is_retracted):
        for req in requests:
            call, key = self._validate(req)
            if req.rid in self.terminals:
                raise ValueError("Agentix cannot requeue a terminal native call")
            call.key = key
            self.counts["native_requeues"] += 1
        self.poll()

    def selected(self, batch):
        if batch is None:
            return
        for req in batch.reqs:
            self._validate(req)
            if req.rid in self.terminals or req.rid in self.awaiting_registration:
                raise ValueError("Agentix cannot select a terminal native call")
            self.swap.resumed(req)

    def drained(self):
        self.poll()
        executor = self.execution.executor
        return (
            executor.open is None and not executor.pending
            and not self.policy.status()["pending_executor_launches"]
        )

    def status(self):
        return {
            "program_identity": "root_workflow_id",
            "native_calls": len(self.requests),
            "terminal_calls_waiting_for_executor": len(self.terminals),
            "arrivals_waiting_for_program_completion": len(self.awaiting_registration),
            "programs_waiting_for_retirement": len(self.retiring),
            "counts": dict(self.counts),
            "future_information_used": False,
            "native_scheduler_hooks_installed": False,
        }


class AgentixNativePreemption:
    """Run a paper-selected swap only after the actual native pipeline drains."""

    def __init__(self, calls, *, drained, enqueue):
        if not callable(drained) or not callable(enqueue):
            raise ValueError("Agentix preemption requires native drain and enqueue callbacks")
        self.calls, self.swap, self.drained, self.enqueue = calls, calls.swap, drained, enqueue
        self.selected = None
        self.victims = ()
        self.counts = Counter()

    def _safe(self):
        return self.drained() is True and self.calls.drained()

    def begin(self, selected, *, running_batch):
        if self.selected is not None:
            raise ValueError("Agentix preemption already has an unfinished selection")
        chosen = self.calls.policy._ids(selected)
        native = tuple(req for req in running_batch.reqs if not req.finished())
        for req in native:
            self.calls._validate(req)
        victims = tuple(req for req in native if req.rid not in chosen)
        if not victims:
            return {"status": "no_preemption", "selected": chosen}
        if not self._safe():
            self.counts["drain_requests"] += 1
            return {"status": "drain_required", "selected": chosen}
        if any(request not in self.calls.policy.calls for request in chosen) or any(
            req.finished() or req.rid not in self.calls.requests for req in victims
        ):
            return {"status": "selection_changed", "selected": chosen}
        self.swap.prepare(victims, running_batch=running_batch)
        # Swapping is queue waiting, never measured executor service. The next
        # real worker call commits actual execution membership again.
        self.calls.policy.running((), now_ms=self.calls.clock_ms())
        self.selected, self.victims = chosen, victims
        self.counts["prepared_rounds"] += 1
        return self.advance(running_batch=running_batch)

    def _clear(self):
        self.selected, self.victims = None, ()

    def advance(self, *, running_batch):
        if self.selected is None:
            raise ValueError("Agentix has no selected native preemption to advance")
        if not self.swap.pending or any(
            request not in self.calls.policy.calls or request in self.calls.terminals
            for request in self.selected
        ):
            self.cancel()
            return {"status": "cancelled", "requests": ()}
        if not self._safe():
            self.counts["drain_requests"] += 1
            return {"status": "drain_required", "selected": self.selected}
        if not self.swap.pending:
            self.cancel()
            return {"status": "cancelled", "requests": ()}
        if any(
            not any(req is running for running in running_batch.reqs)
            for req in self.victims
        ):
            raise ValueError("Agentix preemption lost an actual running victim")
        result = self.swap.commit()
        if result is None:
            self.counts["waiting_native_ack"] += 1
            return {"status": "waiting_native_ack", "selected": self.selected}
        if result["status"] == "backup_declined":
            self.cancel()
            return result
        paused = result["requests"]
        paused_ids = {req.rid for req in paused}
        keep = [index for index, req in enumerate(running_batch.reqs) if req.rid not in paused_ids]
        running_batch.filter_batch(keep_indices=keep)
        running_batch.batch_is_full = False
        self._clear()
        for req in paused:
            self.enqueue(req, is_retracted=True)
        self.counts["committed_rounds"] += 1
        self.counts["requeued_requests"] += len(paused)
        return result

    def cancel(self):
        if self.selected is None:
            return False
        self.swap.cancel_preparation()
        self._clear()
        self.counts["cancelled_rounds"] += 1
        return True

    def status(self):
        return {
            "pending_selection": self.selected,
            "pending_victims": tuple(req.rid for req in self.victims),
            "counts": dict(self.counts),
            "native_scheduler_hooks_installed": False,
        }


class AgentixNativeScheduling:
    """Bind paper selection and native swapping at the existing drain hook.

    Residency and capacity callbacks must inspect the common native data plane.
    The controller supplies no alternative policy or trace-derived capacity.
    """

    def __init__(
        self, calls, scheduler, *, resident, can_fit, maximum_requests, prepare_capacity=None,
        eligible=None, selection_committed=None,
    ):
        if (
            calls.execution.scheduler is not scheduler
            or not callable(resident) or not callable(can_fit)
            or prepare_capacity is not None and not callable(prepare_capacity)
            or eligible is not None and not callable(eligible)
            or selection_committed is not None and not callable(selection_committed)
            or type(maximum_requests) is not int or maximum_requests <= 0
        ):
            raise ValueError("Agentix scheduling requires its native executor and capacity callbacks")
        self.calls, self.scheduler = calls, scheduler
        self.resident, self.can_fit, self.maximum_requests = resident, can_fit, maximum_requests
        self.prepare_capacity = prepare_capacity
        self.eligible, self.selection_committed = eligible, selection_committed
        self.selected_order, self._revision = (), None
        self._host_retry_signature = None
        self._last_batch, self._inside_hook = None, False
        self._original = self._hook = None
        self._had_override = False
        self._previous_override = None
        self._previous_drain = calls.swap.drained
        self.preemption = AgentixNativePreemption(
            calls, drained=self._pipeline_drained, enqueue=scheduler._add_request_to_queue,
        )
        self.counts, self.cpu_ns = Counter(), 0

    def _pipeline_drained(self):
        return (
            self.scheduler.chunked_req is None
            and (
                not self.scheduler.enable_overlap
                or self._last_batch is None and not self.scheduler.result_queue
            )
        )

    def install(self):
        original = self.scheduler._beliefkv_before_batch_selection
        if self._hook is not None or getattr(original, "_agentix_scheduler_owner", None) is not None:
            raise ValueError("Agentix scheduler already has a selection binding")

        @wraps(original)
        def before_selection(running_batch, last_batch):
            if original(running_batch, last_batch):
                return True
            self._last_batch, self._inside_hook = last_batch, True
            started = time.thread_time_ns()
            try:
                return self._step(running_batch)
            finally:
                self.cpu_ns += time.thread_time_ns() - started
                self._inside_hook = False

        before_selection._agentix_scheduler_owner = self
        self._had_override = "_beliefkv_before_batch_selection" in vars(self.scheduler)
        self._previous_override = vars(self.scheduler).get("_beliefkv_before_batch_selection")
        self._original, self._hook = original, before_selection
        self.scheduler._beliefkv_before_batch_selection = before_selection
        self.calls.swap.drained = lambda: (
            self._inside_hook and self._pipeline_drained() and self._previous_drain() is True
        )

    def _revision_key(self):
        return tuple(self.calls.counts[name] for name in (
            "registered", "completed", "cancelled", "native_requeues",
        ))

    def _host_signature(self):
        cache = self.calls.swap.cache
        return (
            cache.cache_controller.mem_pool_host.available_size(),
            len(cache.ongoing_write_through), len(cache.ongoing_load_back),
            self.calls.counts["completed"], self.calls.counts["cancelled"],
            self.calls.swap.transfers.counts["host_leases_released"],
        )

    def _outcome(self, outcome, running_batch):
        status = outcome["status"]
        self.counts[status] += 1
        if status == "backup_declined":
            if not outcome.get("host_capacity_refused"):
                raise RuntimeError("Agentix native Host backup declined without a capacity refusal")
            retained = tuple(
                req.rid for req in running_batch.reqs
                if req.rid in self.calls.policy.calls and req.rid not in self.calls.terminals
                and not req.finished()
            )
            if not retained:
                raise RuntimeError("Agentix Host backpressure has no native running work to retain")
            self.selected_order = retained
            self.calls.policy.retain_observed_selection(retained)
            self._host_retry_signature = self._host_signature()
            self.counts["host_backpressure"] += 1
            return False
        if status in ("cancelled", "selection_changed"):
            self.selected_order = ()
            self.calls.policy.invalidate_plan()
            return True
        return status in ("drain_required", "waiting_native_ack")

    def _step(self, running_batch):
        self.calls.poll()
        if self.preemption.selected is not None:
            return self._outcome(
                self.preemption.advance(running_batch=running_batch), running_batch,
            )
        if self._host_retry_signature is not None:
            if self._host_signature() == self._host_retry_signature:
                self.counts["host_backpressure_retained_without_queue_scan"] += 1
                return False
            self._host_retry_signature = None
            self.calls.policy.invalidate_plan()
        active = tuple(
            identity for identity in self.selected_order
            if identity in self.calls.policy.calls and identity not in self.calls.terminals
        )
        due = self.calls.policy.schedule_due(active)
        revision = self._revision_key()
        if not due and revision == self._revision:
            self.counts["retained_without_queue_scan"] += 1
            return False
        if self.scheduler.chunked_req is not None:
            self.counts["full_schedule_deferred_for_chunk"] += 1
            return False
        if due and (not self._pipeline_drained() or not self.calls.drained()):
            self.counts["selection_drain_requests"] += 1
            return True
        native = {}
        for req in (*self.scheduler.waiting_queue, *running_batch.reqs):
            if (
                req.rid in self.calls.policy.calls and req.rid not in self.calls.terminals
                and not req.finished()
            ):
                self.calls._validate(req)
                native[req.rid] = req
        actual_due = self.calls.policy.schedule_due(tuple(native))
        if actual_due and not due:
            if not self._pipeline_drained() or not self.calls.drained():
                self.counts["selection_drain_requests"] += 1
                return True
            due = True
        if self.prepare_capacity is not None:
            self.prepare_capacity(tuple(native.values()))
        if self.eligible is not None:
            native = {identity: req for identity, req in native.items() if self.eligible(req)}
            actual_due = self.calls.policy.schedule_due(tuple(native))
            if actual_due and not due:
                if not self._pipeline_drained() or not self.calls.drained():
                    self.counts["selection_drain_requests"] += 1
                    return True
                due = True
        residents = tuple(identity for identity, req in native.items() if self.resident(req) is True)

        def fits(identity, chosen):
            if len(chosen) == self.maximum_requests:
                return False
            result = self.can_fit(native[identity], tuple(native[item] for item in chosen))
            if type(result) is not bool:
                raise ValueError("Agentix native capacity callback must return an explicit boolean")
            return result

        self.selected_order = self.calls.policy.plan(
            tuple(native), resident=residents, can_fit=fits, now_ms=self.calls.clock_ms(),
        )
        if self.selection_committed is not None:
            self.selection_committed(self.selected_order)
        self._revision = revision
        self.counts["full_selections" if due else "resident_refill_selections"] += 1
        if due:
            return self._outcome(
                self.preemption.begin(self.selected_order, running_batch=running_batch),
                running_batch,
            )
        return False

    def prefill_plan(self, native_order, *, semantic_revision):
        if self._hook is None:
            raise ValueError("Agentix native selection hook is not installed")
        waiting = {req.rid: req for req in native_order}
        keys = []
        for identity in self.selected_order:
            if identity in waiting and identity not in self.calls.terminals:
                _, key = self.calls._validate(waiting[identity])
                if identity not in self.calls.awaiting_registration:
                    keys.append(key)
        return compile_native_prefill_plan(keys, semantic_revision=semantic_revision)

    def close(self):
        if self._hook is None:
            return
        self.calls.poll()
        self.calls.execution.assert_drained()
        self.preemption.cancel()
        if self.scheduler._beliefkv_before_batch_selection is not self._hook:
            raise ValueError("Agentix cannot overwrite a later native scheduler hook")
        if self._had_override:
            self.scheduler._beliefkv_before_batch_selection = self._previous_override
        else:
            del self.scheduler._beliefkv_before_batch_selection
        self.calls.swap.drained = self._previous_drain
        self._original = self._hook = self._previous_override = None

    def status(self):
        return {
            "native_selection_hook_installed": self._hook is not None,
            "selected_call_ids": self.selected_order,
            "counts": dict(self.counts), "controller_thread_cpu_ms": self.cpu_ns / 1e6,
            "capacity_and_residency_source": "explicit_native_callbacks",
            "host_backpressure": self._host_retry_signature is not None,
            "preemption": self.preemption.status(),
            "full_runtime_adapter_installed": False,
        }
