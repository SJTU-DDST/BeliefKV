"""Bounded semantic admission for v0.5.20's native FULL/MAMBA scheduler.

No physical action or capacity certificate is issued by this runtime.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Sequence
import time
from typing import TYPE_CHECKING

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEventKind
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.event_channel import RuntimeEventDatagramServer
from beliefkv.runtime.sglang_v0520_admission import (
    _request_key,
    NativePrefillPlan,
    PrefillCandidateKey,
    compile_native_prefill_plan,
)
from beliefkv.runtime.sglang_v0520_prediction import (
    NativeDemandHint,
    NativeToolWaitHint,
    PREDICTION_ATTRIBUTE,
    parse_native_demand_hint,
    validate_admission_artifact,
)
from beliefkv.runtime.sglang_v0520_physical import (
    ActionLocalShadowCandidate,
    ContextSessionAnchors,
    PhysicalActionCompleted,
    PhysicalActionExpectation,
    PhysicalReceiptError,
    PhysicalTransactionLedger,
    capture_action_local_shadow,
)
from beliefkv.predictor.structured_frontier import LocalFrontierFeatures

if TYPE_CHECKING:
    from beliefkv.core.events import RuntimeEvent


class NativeAdmissionRuntime:
    """Rebind causal order to live request identities at each prefill safe point."""

    def __init__(
        self,
        *,
        event_socket_path: str | None = None,
        predictor_sha256: str | None = None,
        predictor_artifact_path: str | None = None,
        model_path: str | None = None,
        enable_local_predictor: bool = False,
    ) -> None:
        if bool(predictor_sha256) != bool(predictor_artifact_path):
            raise ValueError("predictive admission requires both artifact and SHA-256")
        if predictor_sha256 is not None and (
            type(predictor_sha256) is not str
            or len(predictor_sha256) != 64
            or any(c not in "0123456789abcdef" for c in predictor_sha256)
            or not event_socket_path
        ):
            raise ValueError("predictive admission requires a pinned SHA-256 and event socket")
        if predictor_sha256 is not None:
            if not model_path:
                raise ValueError("predictive admission requires a model path")
            validate_admission_artifact(
                predictor_artifact_path,
                expected_sha256=predictor_sha256,
                model_path=model_path,
            )
        self.semantic_revision = 0
        self.predictor_sha256 = predictor_sha256
        self.demand_hints: dict[str, NativeDemandHint] = {}
        self.tool_wait_hint: NativeToolWaitHint | None = None
        self.shadow_candidate: ActionLocalShadowCandidate | None = None
        self._native_cache: object | None = None
        self._context_tokens: dict[str, tuple[int, int, int, bool]] = {}
        self._last_model_signature: tuple[object, ...] | None = None
        self._model_worker = None
        if enable_local_predictor:
            if predictor_sha256 is None or predictor_artifact_path is None:
                raise ValueError("local admission predictor requires a calibrated artifact")
            from beliefkv.runtime.sglang_v0520_predictor_worker import (
                NativePredictorWorker,
            )

            self._model_worker = NativePredictorWorker(
                predictor_artifact_path, predictor_sha256
            )
        self.graph = RuntimeCausalContextGraph(strict_timestamps=False)
        self.frontier = CausalFrontierScheduler(self.graph)
        self.visible: dict[str, PrefillCandidateKey] = {}
        self.context_sessions: dict[str, PrefillCandidateKey] = {}
        self.physical_ledger = PhysicalTransactionLedger()
        self.completed_physical_actions: deque[PhysicalActionCompleted] = deque(
            maxlen=128
        )
        self.physical_disabled = False
        self.counts: Counter[str] = Counter()
        self.event_server = (
            RuntimeEventDatagramServer(event_socket_path, self.on_events)
            if event_socket_path
            else None
        )

    def close(self) -> None:
        if self._model_worker is not None:
            self._model_worker.close()
            self._model_worker = None
        if self.event_server is not None:
            self.event_server.close()
            self.event_server = None

    def attach_native_cache(self, cache: object) -> None:
        self._native_cache = cache

    def predictor_fileno(self) -> int | None:
        if self._model_worker is None or self._model_worker.disabled:
            return None
        return self._model_worker.fileno()

    def on_events(self, events: tuple[RuntimeEvent, ...]) -> None:
        hints = []
        for event in events:
            raw = event.attributes.get(PREDICTION_ATTRIBUTE)
            if raw is None:
                continue
            if self.predictor_sha256 is None:
                self.counts["unconfigured_prediction_ignored"] += 1
                continue
            hint = parse_native_demand_hint(
                event, raw, expected_sha256=self.predictor_sha256
            )
            if self.visible.get(hint.key.request_id) != hint.key:
                raise ValueError("admission prediction has no matching live request")
            hints.append(hint)
        try:
            self.graph.apply_batch(events, atomic=False)
        except Exception:
            # A partially applied batch cannot remain a scheduling authority.
            self.graph = RuntimeCausalContextGraph(strict_timestamps=False)
            self.frontier = CausalFrontierScheduler(self.graph)
            self.demand_hints.clear()
            self._last_model_signature = None
            self.context_sessions.clear()
            self.tool_wait_hint = None
            self.shadow_candidate = None
            self._context_tokens.clear()
            self.semantic_revision += 1
            self.counts["causal_mirror_discarded"] += 1
            raise
        else:
            for hint in hints:
                if self._terminal(hint.key):
                    self.counts["terminal_prediction_ignored"] += 1
                else:
                    self.demand_hints[hint.key.request_id] = hint
                    self.counts["prediction_accepted"] += 1
            for event in events:
                context_id = event.context_id
                if context_id is None and event.invocation_id is not None:
                    invocation = self.graph.invocations.get(event.invocation_id)
                    context_id = (
                        invocation.context_id if invocation is not None else None
                    )
                if context_id is not None:
                    key = self.context_sessions.get(context_id)
                    if key is not None and self._terminal(key):
                        del self.context_sessions[context_id]
                        self._context_tokens.pop(context_id, None)
                    if event.kind in (
                        RuntimeEventKind.TOOL_END,
                        RuntimeEventKind.RETURN,
                        RuntimeEventKind.INVOCATION_CANCEL,
                        RuntimeEventKind.CONTEXT_ADVANCE,
                    ) and self.tool_wait_hint is not None and (
                        self.tool_wait_hint.key.context_id == context_id
                    ):
                        self.tool_wait_hint = None
                        self.shadow_candidate = None
                if event.kind is RuntimeEventKind.WORKFLOW_END:
                    self.context_sessions = {
                        context: key
                        for context, key in self.context_sessions.items()
                        if key.root_workflow_id != event.workflow_id
                    }
                    self._context_tokens = {
                        context: tokens
                        for context, tokens in self._context_tokens.items()
                        if context in self.context_sessions
                    }
                    if self.tool_wait_hint is not None and (
                        self.tool_wait_hint.key.root_workflow_id == event.workflow_id
                    ):
                        self.tool_wait_hint = None
                        self.shadow_candidate = None
                if event.kind is RuntimeEventKind.TOOL_START and context_id is not None:
                    self._submit_tool_wait(context_id)
            if len(self.demand_hints) > 1024:
                now_ms = time.monotonic() * 1000
                self.demand_hints = {
                    rid: hint for rid, hint in self.demand_hints.items()
                    if hint.expires_monotonic_ms > now_ms
                    and self.visible.get(rid) == hint.key
                }
            self.semantic_revision += 1

    def scheduler_step(self) -> None:
        if self.event_server is not None:
            self.event_server.drain(max_messages=16)
        if self.tool_wait_hint is not None and not self.tool_wait_hint.live(
            self.tool_wait_hint.key, now_ms=time.monotonic() * 1000
        ):
            self.tool_wait_hint = None
            self.shadow_candidate = None
            self.counts["tool_wait_expired"] += 1
        if self._model_worker is not None:
            hints = self._model_worker.poll()
            for hint in hints:
                if isinstance(hint, NativeToolWaitHint):
                    self._accept_tool_wait(hint)
                    continue
                if (
                    hint.predictor_sha256 == self.predictor_sha256
                    and hint.live(hint.key, now_ms=time.monotonic() * 1000)
                    and self.visible.get(hint.key.request_id) == hint.key
                    and not self._terminal(hint.key)
                    and (
                        hint.invocation_revision_ts_ms is None
                        or getattr(
                            self.graph.invocations.get(hint.key.invocation_id),
                            "updated_ts_ms",
                            None,
                        ) == hint.invocation_revision_ts_ms
                    )
                ):
                    self.demand_hints[hint.key.request_id] = hint
                    self.counts["model_prediction_accepted"] += 1
                    self.semantic_revision += 1
            if self._model_worker.disabled:
                self.counts["model_worker_disabled"] = 1
        self.counts["physical_expired"] += len(self.physical_ledger.expire())

    def _submit_tool_wait(self, context_id: str) -> None:
        worker = self._model_worker
        key = self.context_sessions.get(context_id)
        if worker is None or worker.disabled or key is None:
            self.counts["tool_wait_predictor_unavailable"] += 1
            return
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(context_id)
        if (
            invocation is None
            or context is None
            or context.epoch != key.context_epoch
            or context.workflow_id != key.root_workflow_id
            or invocation.workflow_id != key.root_workflow_id
            or invocation.context_id != context_id
            or invocation.state.value != "wait_tool"
            or self._terminal(key)
        ):
            self.counts["tool_wait_context_stale"] += 1
            return
        stored = self._context_tokens.get(context_id)
        prompt, output, is_child = (
            (stored[1], stored[2], stored[3])
            if stored is not None and stored[0] == key.context_epoch
            else (0, 0, False)
        )
        features = LocalFrontierFeatures(
            invocation_id=key.invocation_id,
            state=invocation.state.value,
            agent_definition_id=invocation.agent_definition_id,
            tool_family=invocation.active_tool_family or "unknown",
            generated_tokens=output,
            current_sequence_tokens=prompt + output,
            active_tool_count=1,
            llm_round=invocation.llm_round,
            child_count=len(invocation.child_invocation_ids),
            unfinished_child_count=len(invocation.blocking_child_ids),
            is_child=is_child,
        )
        worker.submit_tool_wait(((key, features, invocation.updated_ts_ms),))
        self.counts["tool_wait_submitted"] += 1

    def _accept_tool_wait(self, hint: NativeToolWaitHint) -> None:
        key = hint.key
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        if (
            hint.predictor_sha256 != self.predictor_sha256
            or self._model_worker is None
            or self.context_sessions.get(key.context_id) != key
            or context is None
            or context.epoch != key.context_epoch
            or context.workflow_id != key.root_workflow_id
            or invocation is None
            or invocation.workflow_id != key.root_workflow_id
            or invocation.context_id != key.context_id
            or invocation.state.value != "wait_tool"
            or invocation.updated_ts_ms != hint.invocation_revision_ts_ms
            or self._terminal(key)
            or not hint.live(key, now_ms=time.monotonic() * 1000)
        ):
            self.counts["tool_wait_result_stale"] += 1
            return
        self.tool_wait_hint = hint
        self.counts["tool_wait_accepted"] += 1
        self.shadow_candidate = (
            self.capture_shadow_candidate(
                self._native_cache,
                context_id=key.context_id,
                context_epoch=key.context_epoch,
            )
            if self._native_cache is not None
            else None
        )
        self.counts[
            "tool_wait_shadow_available"
            if self.shadow_candidate is not None
            else "tool_wait_shadow_unavailable"
        ] += 1

    def register_physical_action(self, expected: PhysicalActionExpectation) -> None:
        """Accept only a live causal identity; this does not issue the transfer."""
        context = self.graph.contexts.get(expected.context_id)
        session_key = self.context_sessions.get(expected.context_id)
        invocation = (
            self.graph.invocations.get(session_key.invocation_id)
            if session_key is not None
            else None
        )
        session_waiting = (
            session_key is not None
            and session_key.context_epoch == expected.context_epoch
            and session_key.session_id is not None
            and session_key.session_id == expected.session_id
            and session_key.session_generation == expected.session_generation
            and not self._terminal(session_key)
            and invocation is not None
            and invocation.state.value in ("wait_tool", "wait_child", "wait_join")
        )
        visible = any(
            key.context_id == expected.context_id
            and key.context_epoch == expected.context_epoch
            and (
                expected.session_id is None
                or (
                    key.session_id == expected.session_id
                    and key.session_generation == expected.session_generation
                )
            )
            and not self._terminal(key)
            for key in self.visible.values()
        )
        if (
            self.physical_disabled
            or context is None
            or context.epoch != expected.context_epoch
            or not (visible or session_waiting)
        ):
            raise PhysicalReceiptError("physical action has no live causal context")
        self.physical_ledger.register(expected)

    def snapshot_session_anchors(
        self, cache: object, *, context_id: str, context_epoch: int
    ) -> ContextSessionAnchors | None:
        """Request-local lookup for a finished tool-waiting context."""
        key = self.context_sessions.get(context_id)
        context = self.graph.contexts.get(context_id)
        if (
            key is None
            or key.context_epoch != context_epoch
            or context is None
            or context.epoch != context_epoch
            or key.session_id is None
            or key.session_generation is None
            or self._terminal(key)
        ):
            return None
        try:
            leaves = cache.session_refs.snapshot_session_leaf_anchors(
                key.session_id, key.session_generation, max_leaves=8
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        if leaves is None or not any(anchors for _, anchors in leaves):
            return None
        return ContextSessionAnchors(
            key=key, component_leaves=leaves,
            captured_monotonic_s=time.monotonic(),
        )

    def capture_shadow_candidate(
        self, cache: object, *, context_id: str, context_epoch: int
    ) -> ActionLocalShadowCandidate | None:
        anchors = self.snapshot_session_anchors(
            cache, context_id=context_id, context_epoch=context_epoch
        )
        if anchors is None:
            return None
        return capture_action_local_shadow(cache, anchors)

    def on_native_transfer_commit(self, commit: object) -> None:
        """Observe synchronized native ACKs, never infer completion from enqueue."""
        if self.physical_disabled:
            return
        live_epochs = {
            context_id: context.epoch
            for context_id in self.physical_ledger.pending_context_ids
            if (context := self.graph.contexts.get(context_id)) is not None
        }
        live_sessions = {
            context_id: (key.session_id, key.session_generation)
            for context_id in self.physical_ledger.pending_context_ids
            if (key := self.context_sessions.get(context_id)) is not None
            and key.session_id is not None
            and key.session_generation is not None
        }
        try:
            completed = self.physical_ledger.observe(
                commit,
                live_context_epochs=live_epochs,
                live_context_sessions=live_sessions,
            )
        except PhysicalReceiptError:
            self.physical_disabled = True
            self.counts["physical_receipt_failed"] += 1
            return
        self.completed_physical_actions.extend(completed)
        self.counts["native_physical_completed"] += len(completed)

    def register_visible_request(self, req: object) -> bool:
        key = _request_key(req)
        if key is None or self._terminal(key):
            self.counts["invalid_request_identity"] += 1
            return False
        if key.request_id in self.visible:
            raise ValueError(f"duplicate visible request: {key.request_id}")
        if (
            self.tool_wait_hint is not None
            and self.tool_wait_hint.key.context_id == key.context_id
            and self.tool_wait_hint.key != key
        ):
            self.tool_wait_hint = None
            self.shadow_candidate = None
            self._context_tokens.pop(key.context_id, None)
        self.visible[key.request_id] = key
        if key.session_id is not None and key.session_generation is not None:
            self.context_sessions[key.context_id] = key
        else:
            self.context_sessions.pop(key.context_id, None)
        self.semantic_revision += 1
        return True

    def _terminal(self, key: PrefillCandidateKey) -> bool:
        workflow = self.graph.workflows.get(key.root_workflow_id)
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        return bool(
            (workflow is not None and workflow.end_ts_ms is not None)
            or (
                invocation is not None
                and invocation.workflow_id == key.root_workflow_id
                and invocation.state.terminal
            )
            or (
                context is not None
                and context.workflow_id == key.root_workflow_id
                and context.epoch > key.context_epoch
            )
        )

    def terminal_waiting_request_ids(self, native_order: Sequence[object]) -> tuple[str, ...]:
        return tuple(
            key.request_id
            for req in native_order
            if (key := _request_key(req)) is not None
            and key.request_id in self.visible
            and self._terminal(key)
        )

    def retire_terminal_request(self, request_id: str) -> None:
        if request_id in self.visible:
            del self.visible[request_id]
            self.demand_hints.pop(request_id, None)
            self._forget_session(request_id)
            self.semantic_revision += 1
            self.counts["terminal_waiting_aborted"] += 1

    def on_requests_requeued(
        self, requests: Sequence[object], *, is_retracted: bool
    ) -> None:
        for req in requests:
            key = _request_key(req)
            if key is None or key.request_id not in self.visible:
                raise ValueError("requeued request has no live tagged identity")
            if (
                self.tool_wait_hint is not None
                and self.tool_wait_hint.key.context_id == key.context_id
                and self.tool_wait_hint.key != key
            ):
                self.tool_wait_hint = None
                self.shadow_candidate = None
                self._context_tokens.pop(key.context_id, None)
            self.visible[key.request_id] = key
            if key.session_id is not None and key.session_generation is not None:
                self.context_sessions[key.context_id] = key
            else:
                self.context_sessions.pop(key.context_id, None)
            self.demand_hints.pop(key.request_id, None)
            self.semantic_revision += 1

    def _forget_session(self, request_id: str) -> None:
        for context_id, key in tuple(self.context_sessions.items()):
            if key.request_id == request_id:
                del self.context_sessions[context_id]
                self._context_tokens.pop(context_id, None)
                if (
                    self.tool_wait_hint is not None
                    and self.tool_wait_hint.key.context_id == context_id
                ):
                    self.tool_wait_hint = None
                    self.shadow_candidate = None

    def _causal_rank(
        self,
        req: object,
        index: int,
        ready_ranks: dict[str, tuple[int, int]],
    ) -> tuple[int, int, int]:
        key = _request_key(req)
        if key is None:
            return (5, 0, index)
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        if (
            invocation is None
            or context is None
            or invocation.workflow_id != key.root_workflow_id
            or context.workflow_id != key.root_workflow_id
            or invocation.context_id != key.context_id
            or context.epoch != key.context_epoch
        ):
            return (5, 0, index)
        causal_class, depth = ready_ranks.get(key.invocation_id, (5, 0))
        return (causal_class, depth, index)

    def plan_native_prefill(
        self, native_order: Sequence[object], *, running_batch: object, adder: object
    ) -> NativePrefillPlan:
        # Only sort tagged slots; PrefillAdder retains all FULL/MAMBA decisions.
        # Unknown or stale causal state keeps native order.
        tagged = []
        for index, req in enumerate(native_order):
            if getattr(req, "beliefkv_metadata", None) is None:
                continue
            if len(tagged) == 512:
                break
            tagged.append((index, req))
        if len(native_order) > 512:
            self.counts["candidate_bound"] += 1
        workflows = {
            key.root_workflow_id
            for _, req in tagged
            if (key := _request_key(req)) is not None
            and self.visible.get(key.request_id) == key
            and key.root_workflow_id in self.graph.workflows
        }
        ready_ranks = {
            item.invocation_id: (item.score[0], -item.unblock_depth)
            for workflow_id in workflows
            for item in self.frontier.candidates(workflow_id)
        }
        self._submit_local_predictions(tagged, ready_ranks)
        now_ms = time.monotonic() * 1000
        ranks = {
            index: self._causal_rank(req, index, ready_ranks)
            for index, req in tagged
        }
        valid_hints: dict[int, NativeDemandHint] = {}
        members = Counter((rank[0], rank[1]) for rank in ranks.values())
        hinted = Counter()
        for index, req in tagged:
            key = _request_key(req)
            if key is None:
                continue
            hint = self.demand_hints.get(key.request_id)
            if (
                hint is not None
                and hint.live(key, now_ms=now_ms)
                and (
                    hint.invocation_revision_ts_ms is None
                    or getattr(
                        self.graph.invocations.get(key.invocation_id),
                        "updated_ts_ms",
                        None,
                    ) == hint.invocation_revision_ts_ms
                )
            ):
                valid_hints[index] = hint
                hinted[ranks[index][:2]] += 1
        ordered = sorted(
            tagged,
            key=lambda pair: (
                *ranks[pair[0]][:2],
                valid_hints[pair[0]].next_output_tokens
                if ranks[pair[0]][0] < 5
                and hinted[ranks[pair[0]][:2]] == members[ranks[pair[0]][:2]]
                else pair[0],
                pair[0],
            ),
        )
        return compile_native_prefill_plan(
            [
                req for _, req in ordered
                if (key := _request_key(req)) is not None
                and self.visible.get(key.request_id) == key
                and not self._terminal(key)
            ],
            semantic_revision=self.semantic_revision,
        )

    def _submit_local_predictions(
        self,
        tagged: list[tuple[int, object]],
        ready_ranks: dict[str, tuple[int, int]],
    ) -> None:
        if self._model_worker is None or self._model_worker.disabled:
            return
        tasks = []
        signatures = []
        for _, req in tagged:
            key = _request_key(req)
            if (
                key is None
                or self.visible.get(key.request_id) != key
                or key.invocation_id not in ready_ranks
                or self._terminal(key)
            ):
                continue
            invocation = self.graph.invocations[key.invocation_id]
            metadata = req.beliefkv_metadata
            prompt_tokens = len(getattr(req, "origin_input_ids", ()))
            output_tokens = len(getattr(req, "output_ids", ()))
            features = LocalFrontierFeatures(
                invocation_id=key.invocation_id,
                state=invocation.state.value,
                agent_definition_id=invocation.agent_definition_id,
                tool_family=invocation.active_tool_family or "unknown",
                generated_tokens=output_tokens,
                current_sequence_tokens=prompt_tokens + output_tokens,
                llm_round=invocation.llm_round,
                child_count=len(invocation.child_invocation_ids),
                unfinished_child_count=len(invocation.blocking_child_ids),
                is_child=metadata.get("parent_invocation_id") is not None,
            )
            signatures.append((key, invocation.updated_ts_ms, prompt_tokens, output_tokens))
            tasks.append((key, features, invocation.updated_ts_ms))
            if len(tasks) == 8:
                break
        signature = tuple(signatures)
        if tasks and signature != self._last_model_signature:
            self._last_model_signature = signature
            self._model_worker.submit(tuple(tasks))

    def on_prefill_selection(self, rejected: tuple[tuple[str, str], ...]) -> None:
        self.counts.update(reason for _, reason in rejected)

    def on_prefill_candidate_result(
        self, req: object, *, admitted: bool, result: str
    ) -> None:
        if getattr(req, "beliefkv_metadata", None) is not None:
            self.counts["native_admitted" if admitted else f"native_{result}"] += 1
            if admitted:
                self.demand_hints.pop(req.rid, None)

    def on_batch_selected(self, batch: object) -> None:
        pass

    def on_batch_completed(self, batch: object) -> None:
        for req in batch.reqs:
            if req.rid in self.visible and req.finished():
                context_id = self.visible[req.rid].context_id
                key = self.visible[req.rid]
                if (
                    self._model_worker is not None
                    and key.session_id is not None
                    and key.session_generation is not None
                ):
                    self._context_tokens[context_id] = (
                        key.context_epoch,
                        len(getattr(req, "origin_input_ids", ())),
                        len(getattr(req, "output_ids", ())),
                        req.beliefkv_metadata.get("parent_invocation_id") is not None,
                    )
                del self.visible[req.rid]
                self.demand_hints.pop(req.rid, None)
                session_key = self.context_sessions.get(context_id)
                if (
                    session_key is not None
                    and session_key.request_id == req.rid
                    and self._terminal(session_key)
                ):
                    self._forget_session(req.rid)
                    self._context_tokens.pop(context_id, None)
                self.semantic_revision += 1

    def on_abort_request(self, abort: object) -> None:
        removed = [
            rid
            for rid in self.visible
            if getattr(abort, "abort_all", False) or rid.startswith(abort.rid)
        ]
        for rid in removed:
            del self.visible[rid]
            self.demand_hints.pop(rid, None)
            self._forget_session(rid)
            self.semantic_revision += 1
        for context_id, key in tuple(self.context_sessions.items()):
            if getattr(abort, "abort_all", False) or key.request_id.startswith(abort.rid):
                del self.context_sessions[context_id]
                self._context_tokens.pop(context_id, None)
                if self.tool_wait_hint is not None and (
                    self.tool_wait_hint.key.context_id == context_id
                ):
                    self.tool_wait_hint = None
                    self.shadow_candidate = None

    def running_batch_retraction_barrier_required(self, batch: object) -> bool:
        return False

    def on_running_batch_retraction_barrier_drained(self, batch: object) -> None:
        pass

    def plan_running_batch_retraction(self, batch: object) -> None:
        return None
