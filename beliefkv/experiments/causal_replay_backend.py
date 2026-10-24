"""HTTP data plane and visible-event delivery for auxiliary causal replay."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import math
import time
from typing import Callable

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import NativeReplayCompletion
from beliefkv.experiments.causal_replay_native import RECEIPT_KEY
from beliefkv.experiments.causal_replay_sampling import (
    CapturedOutputLogitsProcessor, PARAMETER_KEY,
)
from beliefkv.experiments.causal_replay_tokens import (
    CapturedReplayRequest, CapturedTokenCorpus,
)
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


def _same_wire(left, right) -> bool:
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False,
    )


def verify_native_receipt(
    captured: CapturedReplayRequest, payload: dict, *, submitted_ms: float,
    now_ms: float, clock_domain: str, expected_session_generation: int | None = None,
) -> NativeReplayCompletion:
    meta = payload.get("meta_info") if type(payload) is dict else None
    entries = meta.get(RECEIPT_KEY) if type(meta) is dict else None
    if type(entries) is not list or len(entries) != 1 or type(entries[0]) is not dict:
        raise ValueError("native response lacks exactly one terminal replay receipt")
    receipt = entries[0]
    complete = receipt.get("complete_monotonic_ms")
    if (
        type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1
        or receipt.get("completion_boundary") != "native_terminal_output_emit"
        or receipt.get("request_id") != captured.request_id
        or meta.get("id") != captured.request_id
        or not _same_wire(receipt.get("identity"), list(captured.identity))
        or not clock_domain or receipt.get("clock_domain") != clock_domain
        or type(complete) not in (int, float) or not math.isfinite(complete)
        or not submitted_ms <= complete <= now_ms + 1.
    ):
        raise ValueError("native replay receipt changes request, identity or clock")
    if (
        type(receipt.get("prompt_tokens")) is not int
        or receipt["prompt_tokens"] != len(captured.prompt_tokens)
        or receipt.get("prompt_sha256") != captured.prompt_sha256
        or type(meta.get("prompt_tokens")) is not int
        or meta["prompt_tokens"] != len(captured.prompt_tokens)
        or type(meta.get("completion_tokens")) is not int
        or meta["completion_tokens"] != len(captured.output_tokens)
        or receipt.get("output_token_ids") != list(captured.output_tokens)
        or any(type(token) is not int for token in receipt["output_token_ids"])
    ):
        raise ValueError("native replay executed different input or output tokens")
    if (
        not _same_wire(receipt.get("sampling_parameters"), captured.sampling_parameters)
        or not _same_wire(receipt.get("native_finish_reason"), captured.native_finish_reason)
        or not _same_wire(meta.get("finish_reason"), captured.native_finish_reason)
    ):
        raise ValueError("native replay changed original stopping or finish reason")
    if captured.generation_parameters is not None and (
        not _same_wire(receipt.get("generation_parameters"), captured.generation_parameters)
        or receipt.get("native_sampler_preserved") is not True
    ):
        raise ValueError("native replay changed original sampler or generation parameters")
    generation = (
        captured.session_generation if expected_session_generation is None
        else expected_session_generation
    )
    if captured.session_id is not None and (
        type(captured.session_generation) is not int or captured.session_generation <= 0
        or type(generation) is not int or generation <= 0
    ):
        raise ValueError("native replay lacks a valid session generation binding")
    if any(
        not _same_wire(receipt.get(name), getattr(captured, name))
        for name in ("session_id", "extra_key", "cache_salt")
    ) or not _same_wire(receipt.get("session_generation"), generation):
        raise ValueError("native replay changed session or cache namespace")
    return NativeReplayCompletion(
        captured.request_id, complete, clock_domain,
        len(captured.prompt_tokens), len(captured.output_tokens),
    )


def request_payload(
    captured: CapturedReplayRequest, submit: RuntimeEvent,
    metadata: BeliefKVRequestMetadata, *, processor: str,
) -> dict:
    if (
        submit.kind is not Kind.LLM_SUBMIT
        or submit.attributes.get("request_id") != captured.request_id
        or (
            submit.workflow_id, submit.invocation_id,
            submit.context_id, submit.context_epoch,
        ) != captured.identity
        or (
            metadata.root_workflow_id, metadata.invocation_id,
            metadata.context_id, metadata.context_epoch,
        ) != captured.identity
    ):
        raise ValueError("ready request differs from its captured identity")
    seed = submit.attributes.get("sampling_seed")
    if type(seed) is not int or not 0 <= seed < 2 ** 32:
        raise ValueError("ready request lacks its original sampling seed")
    parameters = deepcopy(captured.sampling_parameters)
    generation = captured.generation_parameters
    if generation is not None:
        if generation["sampling_seed"] != seed:
            raise ValueError("ready request changes its captured sampling seed")
        parameters.update(generation)
    else:
        parameters.update(temperature=0., sampling_seed=seed)
    parameters.update(
        custom_params={
            PARAMETER_KEY: {
                "request_id": captured.request_id,
                "output_token_ids": list(captured.output_tokens),
                "force_after_sample": generation is not None,
            },
        },
    )
    return {
        "rid": captured.request_id, "input_ids": list(captured.prompt_tokens),
        "sampling_params": parameters, "custom_logit_processor": processor,
        "beliefkv_metadata": metadata.to_wire(),
        "session_id": captured.session_id,
        "extra_key": captured.extra_key, "cache_salt": captured.cache_salt,
        "stream": False, "return_logprob": False,
    }


class CausalHTTPBackend:
    def __init__(
        self, corpus: CapturedTokenCorpus, client, emit_batch: Callable, *,
        observe_request: Callable[[dict], None] | None = None,
    ) -> None:
        self.corpus, self.client, self._emit_batch = corpus, client, emit_batch
        self._observe_request = observe_request
        self._processor = CapturedOutputLogitsProcessor.to_str()
        self._invocations: dict[tuple[str, str], RuntimeEvent] = {}
        self._sessions: dict[tuple[str, str], tuple[str | None, int | None]] = {}
        self._session_generations: dict[tuple[str, int], int] = {}
        self._dormant: dict[tuple[str, str], RuntimeEvent] = {}
        self._resume_rebind: set[tuple[str, str]] = set()
        self.session_generation_rebindings: list[dict] = []
        self._closed_contexts: set[tuple[str, str]] = set()
        self._closed_workflows: set[str] = set()
        self._active: set[tuple[str, str]] = set()
        self._closing: dict[str, asyncio.Task] = {}
        self._event_locks: dict[str, asyncio.Lock] = {}
        self.verified_requests: set[str] = set()
        self.abort_errors: list[dict] = []
        self.session_closes: list[dict] = []

    async def _close_session(self, session: str) -> None:
        response = await self.client.post("/close_session", json={"session_id": session})
        response.raise_for_status()
        self.session_closes.append({
            "session_id": session, "http_close_accepted": True,
            "physical_release_acknowledged": False,
        })

    def _retire(self, context: tuple[str, str]) -> None:
        session = self._sessions.pop(context, (None, None))[0]
        if session is not None and session not in self._closing:
            self._closing[session] = asyncio.create_task(
                self._close_session(session), name="causal_session_close:" + session,
            )

    async def emit(self, event: RuntimeEvent) -> None:
        lock = self._event_locks.setdefault(event.workflow_id, asyncio.Lock())
        async with lock:
            owner = event.workflow_id, event.invocation_id
            context = event.workflow_id, event.context_id
            resumed = (
                event.kind is Kind.INVOCATION_CREATE
                and event.attributes.get("reusable_subagent") is True
                and event.attributes.get("resumed") is True
            )
            closing = event.attributes.get("subagent_context_close") is True
            previous = self._dormant.get(context)
            if resumed or closing:
                if (
                    previous is None or context in self._active
                    or context in self._closed_contexts or event.workflow_id in self._closed_workflows
                    or previous.attributes.get("agent_handle") != event.attributes.get("agent_handle")
                    or event.context_epoch < previous.context_epoch
                    or closing and (
                        event.invocation_id != previous.invocation_id
                        or event.context_epoch != previous.context_epoch
                    )
                    or resumed and event.invocation_id == previous.invocation_id
                ):
                    raise ValueError("resume or close lacks its dormant native context")
            await asyncio.to_thread(self._emit_batch, (event,))
            if event.kind is Kind.INVOCATION_CREATE:
                if owner in self._invocations:
                    raise ValueError("duplicate visible invocation")
                self._invocations[owner] = event
                if resumed:
                    del self._dormant[context]
                    self._resume_rebind.add(context)
            elif event.kind is Kind.CONTEXT_COMPACT:
                if context in self._active:
                    raise ValueError("context compacted during an active native request")
                session = self._sessions.get(context, (None, None))[0]
                self._retire(context)
                if session is not None:
                    await self._closing[session]
            elif event.kind in (Kind.RETURN, Kind.INVOCATION_CANCEL):
                if event.context_id is not None and event.attributes.get("context_retired") is not False:
                    if context in self._active:
                        raise ValueError("context retired during an active native request")
                    self._closed_contexts.add(context)
                    self._retire(context)
                elif (
                    event.kind is Kind.RETURN
                    and event.attributes.get("reusable_subagent") is True
                ):
                    self._dormant[context] = event
            elif closing:
                self._closed_contexts.add(context)
                del self._dormant[context]
                self._resume_rebind.discard(context)
                self._retire(context)
            elif event.kind is Kind.WORKFLOW_END:
                self._closed_workflows.add(event.workflow_id)
                if any(key[0] == event.workflow_id for key in self._active):
                    raise ValueError("workflow ended with an active native request")
                for key in tuple(self._sessions):
                    if key[0] == event.workflow_id:
                        self._retire(key)
                for key in tuple(self._dormant):
                    if key[0] == event.workflow_id:
                        del self._dormant[key]
                self._resume_rebind.difference_update(
                    key for key in tuple(self._resume_rebind) if key[0] == event.workflow_id
                )

    def _metadata(self, submit: RuntimeEvent) -> BeliefKVRequestMetadata:
        created = self._invocations.get((submit.workflow_id, submit.invocation_id))
        if created is None or created.context_id != submit.context_id:
            raise ValueError("request has no visible physical invocation")
        return BeliefKVRequestMetadata(
            root_workflow_id=submit.workflow_id, invocation_id=submit.invocation_id,
            context_id=submit.context_id, context_epoch=submit.context_epoch,
            agent_definition_id=created.agent_definition_id or "unknown",
            agent_instance_id=created.agent_instance_id or submit.invocation_id,
            parent_invocation_id=created.parent_invocation_id,
            parent_context_id=created.parent_context_id,
            relation_type=created.relation_type.value if created.relation_type else "root",
            context_mode=created.context_mode.value if created.context_mode else "fresh",
            execution_mode=created.execution_mode.value if created.execution_mode else "foreground",
            return_target_id=created.return_target_id, join_id=created.join_id,
            full_prompt_replay_guaranteed=True,
        )

    async def execute(self, request: str, submit: RuntimeEvent) -> NativeReplayCompletion:
        context = submit.workflow_id, submit.context_id
        if (
            context in self._active or context in self._closed_contexts
            or submit.workflow_id in self._closed_workflows
            or request in self.verified_requests
        ):
            raise ValueError("native context is concurrent, terminal or already completed")
        self._active.add(context)
        started, posted = time.monotonic() * 1000., False
        try:
            captured = await asyncio.to_thread(self.corpus.get, request)
            metadata = self._metadata(submit)
            payload = request_payload(captured, submit, metadata, processor=self._processor)
            session = captured.session_id, captured.session_generation
            if context in self._sessions and self._sessions[context] != session:
                raise ValueError("native session changed without visible compaction")
            self._sessions[context] = session
            posted = True
            response = await self.client.post("/generate", json=payload)
            response.raise_for_status()
            now = time.monotonic() * 1000.
            native_payload = response.json()
            meta = native_payload.get("meta_info") if type(native_payload) is dict else None
            entries = meta.get(RECEIPT_KEY) if type(meta) is dict else None
            observed_generation = (
                entries[0].get("session_generation")
                if type(entries) is list and len(entries) == 1 and type(entries[0]) is dict
                else None
            )
            source_session = captured.session_id, captured.session_generation
            previous_generation = self._session_generations.get(source_session)
            rebind = context in self._resume_rebind
            generation = (
                observed_generation if rebind else
                self._session_generations.get(source_session, observed_generation)
            )
            if (
                rebind and previous_generation is not None
                and type(generation) is int and generation < previous_generation
            ):
                raise ValueError("native session generation regressed on resume")
            receipt = verify_native_receipt(
                captured, native_payload, submitted_ms=started, now_ms=now,
                clock_domain=local_monotonic_clock_domain(),
                expected_session_generation=generation,
            )
            if captured.session_id is not None:
                self._session_generations[source_session] = generation
                if rebind:
                    self.session_generation_rebindings.append({
                        "workflow_id": submit.workflow_id, "context_id": submit.context_id,
                        "invocation_id": submit.invocation_id, "request_id": request,
                        "session_id": captured.session_id,
                        "source_generation": captured.session_generation,
                        "previous_native_generation": previous_generation,
                        "native_generation": generation,
                        "reason": "first_request_of_observed_reusable_activation",
                    })
            self._resume_rebind.discard(context)
            self.verified_requests.add(request)
            if self._observe_request is not None:
                self._observe_request({
                    "request_id": request, "submitted_monotonic_ms": started,
                    "http_complete_monotonic_ms": now,
                    "native_complete_monotonic_ms": receipt.complete_monotonic_ms,
                    "prompt_tokens": receipt.prompt_tokens,
                    "output_tokens": receipt.output_tokens,
                    "prompt_sha256": captured.prompt_sha256,
                    "output_sha256": captured.output_sha256,
                    "native_finish_reason": captured.native_finish_reason,
                    "native_receipt": native_payload["meta_info"][RECEIPT_KEY][0],
                    "native_http_usage": {
                        key: native_payload["meta_info"][key] for key in (
                            "id", "prompt_tokens", "completion_tokens", "finish_reason",
                        )
                    },
                    "session_generation_binding": {
                        "session_id": captured.session_id,
                        "source_generation": captured.session_generation,
                        "native_generation": generation,
                    },
                    "exact_tokens_verified": True,
                    "scope": "one native request only; not full replay qualification",
                })
            return receipt
        except BaseException:
            if posted:
                try:
                    aborted = await self.client.post(
                        "/abort_request", json={"rid": request}, timeout=10.,
                    )
                    aborted.raise_for_status()
                except Exception as error:
                    self.abort_errors.append({"request_id": request, "error": repr(error)})
            raise
        finally:
            self._active.remove(context)

    @property
    def session_generation_bindings(self) -> list[dict]:
        return [
            {
                "session_id": session, "source_generation": source,
                "native_generation": native,
            }
            for (session, source), native in sorted(self._session_generations.items())
        ]

    async def drain(self) -> None:
        if self._active:
            raise ValueError("cannot drain while native requests are active")
        for context in tuple(self._sessions):
            self._retire(context)
        outcomes = await asyncio.gather(*self._closing.values(), return_exceptions=True)
        errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        if errors:
            raise errors[0]
        if self.abort_errors:
            raise ValueError("one or more owned native request aborts failed")
