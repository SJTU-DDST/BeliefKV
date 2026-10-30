"""Full-only workflow remaining-work recovery, with observed JOIN siblings."""

from dataclasses import asdict
import os
import time

from beliefkv.core.events import RelationType
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet, resume_category
from beliefkv.runtime.reference_free_continuation import ReferenceFreeContinuationAdmission
from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker


class WorkflowSizeAdmission(ReferenceFreeContinuationAdmission):
    policy_name = ReferenceFreeContinuationAdmission.policy_name + "_workflow_size"
    join_free_policy_name = ReferenceFreeContinuationAdmission.join_free_policy_name + "_workflow_size"
    sibling_policy_name = ReferenceFreeContinuationAdmission.sibling_policy_name + "_workflow_size"
    join_sibling_policy_name = ReferenceFreeContinuationAdmission.join_sibling_policy_name + "_workflow_size"
    rank_kind = "learned_work_class"

    def __init__(self, runtime):
        self._task_seen = set()
        self._output_observed = {}
        self._resume_groups = {}
        self._rank_cache = {}
        self.working_set_type = lambda capacity, **kwargs: WorkflowSizeWorkingSet(
            capacity, rank=self._rank, sibling_group=self._resume_groups.get,
            observe_ready=self._logical_ready_paused, **kwargs,
        )
        super().__init__(runtime)
        self._work = self._create_work(runtime)

    def _create_work(self, runtime):
        return WorkflowWorkWorker(
            runtime.workflow_work_model, runtime.workflow_work_model_sha256,
            tokenizer_path=getattr(runtime, "workflow_work_tokenizer_path", None),
            execution_environment=os.environ,
        )

    def _priority(self, prediction):
        return 1 if prediction is None else prediction.bucket

    def _supported(self, prediction):
        return prediction is not None and prediction.evidence == "model"

    def _dispatch_prediction_fields(self, prediction):
        return {}

    def _prediction(self, invocation):
        workflow = self.workflows.get(invocation)
        if workflow not in self._rank_cache:
            self._rank_cache[workflow] = self._work.prediction(workflow)
        return self._rank_cache[workflow]

    def _rank(self, invocation, now):
        return self._priority(self._prediction(invocation))

    def _logical_ready_paused(self, item):
        key = self.latest.get(item.identity)
        return (
            key is not None
            and self._candidate(key.request_id, key, include_first=True) is not None
        )

    def _order_dispatch_keys(self, keys, joins):
        if len(keys) < 2:
            return keys
        started = time.thread_time()
        predictions, priorities = {}, {}
        for index, key in enumerate(keys):
            item = self.policy.programs.get(key.invocation_id)
            if item is None:
                continue
            workflow = key.root_workflow_id
            if workflow not in predictions:
                predictions[workflow] = self._work.prediction(workflow)
            prediction = predictions[workflow]
            bucket = self._priority(prediction)
            req = self._live_requests.get(key.request_id)
            join = (
                key.invocation_id in joins and req is not None
                and self.runtime._ready_join_parent(req, key)
            )
            priorities[key] = (
                0 if join else 1, resume_category(item), bucket,
                item.tokens, item.queue_sequence, index,
            )
        supported = any(
            self._supported(prediction)
            for prediction in predictions.values()
        )
        ordered = (
            sorted(keys, key=lambda key: priorities.get(
                key, (2, 0, 1, 0, 0, len(keys)),
            )) if supported else keys
        )
        changed = ordered != keys
        self.counts["workflow_size_dispatch_passes"] += 1
        self.counts["workflow_size_dispatch_order_changes"] += changed
        self.counts["workflow_size_dispatch_cpu_us"] += (time.thread_time() - started) * 1e6
        writer = getattr(self.runtime, "_opportunity_writer", None)
        if changed and writer is not None:
            writer.record({
                "event": "workflow_size_free_dispatch_order",
                "ts_ms": time.time() * 1000., "monotonic_s": time.monotonic(),
                "ordered": [
                    {
                        "key": asdict(key), "priority": priorities.get(key),
                        **self._dispatch_prediction_fields(predictions.get(key.root_workflow_id)),
                    }
                    for key in ordered
                ],
                "scope": "bounded_pending_window_order_only; actual_grants_logged_separately",
                "rank_kind": self.rank_kind,
                "physical_capacity_credit": False,
            })
        return ordered

    def registered(self, req, key):
        super().registered(req, key)
        invocation = self.runtime.graph.invocations.get(key.invocation_id)
        if key.root_workflow_id not in self._task_seen and (
            invocation is not None and invocation.relation_type is RelationType.ROOT
        ):
            self._task_seen.add(key.root_workflow_id)
            self._work.submit(
                key.root_workflow_id, "task", tuple(req.origin_input_ids[-8192:]),
                getattr(req, "tokenizer", None),
            )
        # Input IDs are immutable after submission; retain the source sequence
        # until the background worker computes logical prefix increments.
        self._work.submit(
            key.root_workflow_id, "started", key.request_id, key.invocation_id,
            key.context_id, req.origin_input_ids,
        )

    def events(self, events):
        events = tuple(events)
        for event in events:
            self._work.submit(event.workflow_id, "event", event)
        super().events(events)

    def _index_resume_groups(self):
        self._resume_groups.clear()
        if self._siblings is None:
            return
        # One pass over the already observed JOIN index at the control boundary.
        # The smallest inner group wins if nested membership is ever ambiguous.
        join_ids = {
            join_id for item in self.policy.programs.values()
            if item.pending and not item.active
            for join_id in self.runtime._join_by_invocation.get(item.identity, ())
        }
        joins = []
        for join_id in join_ids:
            join = self._siblings.join(join_id)
            if join is not None:
                joins.append(join)
        for join in sorted(joins, key=lambda item: (len(item.member_invocation_ids), item.join_id)):
            for identity in join.member_invocation_ids - join.completed_member_ids:
                prediction = self._prediction(identity)
                if (
                    identity not in self._resume_groups
                    and self._supported(prediction)
                    and self._ready_sibling(identity, join) is not None
                ):
                    self._resume_groups[identity] = join.join_id

    def tick(self, *, now=None):
        now = time.monotonic() if now is None else now
        due = self.policy is not None and now >= self.next_tick
        if due:
            control_wall, control_cpu = time.perf_counter(), time.thread_time()
            self._rank_cache.clear()
            self._resume_groups.clear()
            # Streaming refresh can only increase reasoning sizes. Preserve
            # the reference empty-budget fast path before building a JOIN index.
            if self.policy.remaining_capacity_with_decay(now=now) > self.policy.buffer_tokens:
                self._index_resume_groups()
        super().tick(now=now)
        if not due:
            return
        choice = self.policy.last_selection
        control_cpu_us = (time.thread_time() - control_cpu) * 1e6
        control_wall_us = (time.perf_counter() - control_wall) * 1e6
        writer = getattr(self.runtime, "_opportunity_writer", None)
        self.counts["workflow_size_resume_passes"] += 1
        self.counts["workflow_size_resume_cpu_us"] += choice["thread_cpu_us"]
        self.counts["workflow_size_resume_wall_us"] += choice["wall_us"]
        self.counts["workflow_size_control_cpu_us"] += control_cpu_us
        self.counts["workflow_size_control_wall_us"] += control_wall_us
        if writer is not None:
            writer.record({
                "event": "workflow_size_resume_selection", "ts_ms": time.time() * 1000.,
                "monotonic_s": now, **choice,
                "control_thread_cpu_us": control_cpu_us, "control_wall_us": control_wall_us,
                "rank_kind": self.rank_kind,
                "candidates": [
                    {
                        "key": asdict(self.latest[identity]),
                        "bucket": bucket,
                        "prediction": (
                            asdict(prediction) if (prediction := self._rank_cache.get(
                                self.workflows.get(identity),
                            )) is not None else None
                        ),
                        "active_after_pressure": self.policy.programs[identity].active,
                        "marked_for_pause": self.policy.programs[identity].marked_for_pause,
                    }
                    for identity, _category, bucket, _tokens, _sequence, _group in choice["ordered"]
                    if identity in self.latest and identity in self.policy.programs
                ],
                "physical_capacity_credit": False,
            })

    def native_service(self, batch, completed_ids):
        super().native_service(batch, completed_ids)
        completed = set(completed_ids)
        for req in getattr(batch, "reqs", ()):
            record = self.requests.get(req.rid)
            if record is None:
                continue
            key = record[0]
            output = len(getattr(req, "output_ids", ()))
            previous = self._output_observed.get(req.rid, 0)
            if req.rid in completed:
                self._work.submit(
                    key.root_workflow_id, "completed", req.rid, key.invocation_id,
                    key.context_id, req.origin_input_ids, tuple(req.output_ids),
                )
                self._output_observed.pop(req.rid, None)
            elif output - previous >= 128:
                self._output_observed[req.rid] = output
                self._work.submit(key.root_workflow_id, "generated", req.rid, output)

    def forget(self, invocation, *, now=None):
        for request, (key, _arrived) in tuple(self.requests.items()):
            if key.invocation_id == invocation:
                self._output_observed.pop(request, None)
        super().forget(invocation, now=now)
        self._resume_groups.pop(invocation, None)

    def status(self):
        dispatch = bool(self.dispatch_interval_ms)
        return {
            **super().status(),
            "workflow_size_resume": True,
            "workflow_size_resume_scope": (
                "paused_periodic_and_bounded_free_dispatch"
                if dispatch else "paused_within_original_category"
            ),
            "workflow_work_predictor": self._work.status(),
            "workflow_size_other_admission_paths": (
                "join_unchanged;bounded_free_dispatch_uses_cached_workflow_classes"
                if dispatch else "unchanged_join_and_free_continuation"
            ),
            "workflow_size_new_preemption": False,
        }

    def close(self):
        self._work.close()
        super().close()
        self._task_seen.clear()
        self._output_observed.clear()
        self._resume_groups.clear()
        self._rank_cache.clear()
