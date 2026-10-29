"""Full-only workflow remaining-work recovery, with observed JOIN siblings."""

from dataclasses import asdict
import os
import time

from beliefkv.core.events import RelationType
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet
from beliefkv.runtime.reference_free_continuation import ReferenceFreeContinuationAdmission
from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker


class WorkflowSizeAdmission(ReferenceFreeContinuationAdmission):
    policy_name = ReferenceFreeContinuationAdmission.policy_name + "_workflow_size"
    join_free_policy_name = ReferenceFreeContinuationAdmission.join_free_policy_name + "_workflow_size"
    sibling_policy_name = ReferenceFreeContinuationAdmission.sibling_policy_name + "_workflow_size"
    join_sibling_policy_name = ReferenceFreeContinuationAdmission.join_sibling_policy_name + "_workflow_size"

    def __init__(self, runtime):
        self._task_seen = set()
        self._output_observed = {}
        self._resume_groups = {}
        self._rank_cache = {}
        self.working_set_type = lambda capacity, **kwargs: WorkflowSizeWorkingSet(
            capacity, rank=self._rank, sibling_group=self._resume_groups.get, **kwargs,
        )
        super().__init__(runtime)
        self._work = WorkflowWorkWorker(
            runtime.workflow_work_model, runtime.workflow_work_model_sha256,
            tokenizer_path=getattr(runtime, "workflow_work_tokenizer_path", None),
            execution_environment=os.environ,
        )

    def _prediction(self, invocation):
        workflow = self.workflows.get(invocation)
        if workflow not in self._rank_cache:
            self._rank_cache[workflow] = self._work.prediction(workflow)
        return self._rank_cache[workflow]

    def _rank(self, invocation, now):
        prediction = self._prediction(invocation)
        return 1 if prediction is None else prediction.bucket

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
                    and prediction is not None and prediction.evidence == "model"
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
        return {
            **super().status(),
            "workflow_size_resume": True,
            "workflow_size_resume_scope": "paused_within_original_category",
            "workflow_work_predictor": self._work.status(),
            "workflow_size_other_admission_paths": "unchanged_join_and_free_continuation",
            "workflow_size_new_preemption": False,
        }

    def close(self):
        self._work.close()
        super().close()
        self._task_seen.clear()
        self._output_observed.clear()
        self._resume_groups.clear()
        self._rank_cache.clear()
