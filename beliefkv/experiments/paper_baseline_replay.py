"""Run the original causal workload through the adapted author engines."""

from __future__ import annotations

import asyncio
import hashlib
from itertools import groupby
import json
import math
import time

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import NativeReplayCompletion
from beliefkv.experiments.causal_replay_sampling import CapturedOutputLogitsProcessor, PARAMETER_KEY
from beliefkv.experiments.paper_baseline_native import (
    MARS_OUTPUT_KEY, MORI_RECEIPT_KEY, token_sha256,
)
from beliefkv.experiments.paper_baseline_sglang import ENGINE, EMIT_KEY, IDENTITY_FIELDS, TERMINAL_KEY
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain


def program_id(identity, session_id):
    workflow, _, context, _ = identity
    return hashlib.sha256(json.dumps((workflow, context, session_id)).encode()).hexdigest()


def baseline_payload(arm, model, captured, processor=None, *, engine="legacy"):
    stopping = captured.sampling_parameters
    generation = captured.generation_parameters
    if generation is None:
        raise ValueError("paper replay requires recorded native sampler parameters")
    if captured.extra_key is not None or captured.cache_salt is not None:
        raise ValueError("paper replay has no cross-engine translation for a salted prefix")
    if arm == "mars" and engine != ENGINE:
        if stopping["stop_regex"] or stopping["stop"]:
            raise ValueError("MARS paper replay is qualified only for token stopping")
        params = dict(generation)
        params["seed"] = params.pop("sampling_seed")
        if params["top_k"] == -1:
            params["top_k"] = 0
        return {
            **params, "model": model, "prompt": list(captured.prompt_tokens),
            "request_id": captured.request_id, "job_id": captured.identity[0],
            "max_tokens": stopping["max_new_tokens"], "min_tokens": stopping["min_new_tokens"],
            "ignore_eos": stopping["ignore_eos"], "stop_token_ids": stopping["stop_token_ids"],
            "add_special_tokens": False, "stream": True, "return_token_ids": True,
            "stream_options": {"include_usage": True},
            "vllm_xargs": {MARS_OUTPUT_KEY: json.dumps(list(captured.output_tokens), separators=(",", ":"))},
        }
    if not processor or (arm != "mori" and (arm != "mars" or engine != ENGINE)):
        raise ValueError("unknown baseline or missing SGLang replay processor")
    payload = {
        "rid": captured.request_id, "input_ids": list(captured.prompt_tokens),
        "custom_logit_processor": processor, "stream": True, "return_logprob": False,
        "sampling_params": {
            **stopping, **generation, "custom_params": {PARAMETER_KEY: {
                "request_id": captured.request_id, "force_after_sample": True,
                "output_token_ids": list(captured.output_tokens),
            }},
        },
    }
    if arm == "mori":
        payload["program_id"] = program_id(captured.identity, captured.session_id)
    if engine == ENGINE:
        if stopping["stop_regex"] or stopping["stop"]:
            raise ValueError("common paper replay is qualified only for token stopping")
        # The native MARS Request adapter reads root_workflow_id as job_id.
        payload["beliefkv_metadata"] = dict(zip(IDENTITY_FIELDS, captured.identity, strict=True))
    return payload


class NativeOutput:
    def __init__(self, arm, captured, pid, submitted_ms, *, engine="legacy"):
        self.arm, self.capture, self.pid = arm, captured, pid
        self.engine = engine
        self.submitted_ms = submitted_ms
        self.clock = local_monotonic_clock_domain()
        self.output = []
        self.prompt_sha256 = None
        self.finish = None
        self.completed_ms = None
        self.cached_tokens = 0
        self.usage = None
        self.last_emit_ms = submitted_ms

    def _sglang_output(self, chunk):
        meta = chunk.get("meta_info")
        if type(meta) is not dict or meta.get("id") != self.capture.request_id:
            raise ValueError("SGLang output changes request identity")
        ids = chunk.get("output_ids")
        emitted = meta.get(EMIT_KEY)
        if (
            type(ids) is not list or meta.get("completion_tokens") != len(self.output) + len(ids)
            or type(emitted) is not list or len(emitted) != len(ids)
            or any(type(row) is not list or len(row) != 2 or row[1] != self.clock for row in emitted)
        ):
            raise ValueError("SGLang output lacks aligned native token or clock evidence")
        deltas = [(stamp[0], sum(1 for _ in rows)) for stamp, rows in groupby(emitted)]
        reason = meta.get("finish_reason")
        receipt = meta.get(TERMINAL_KEY)
        if reason is not None:
            captured = self.capture
            required = {
                "schema_version": 1, "request_id": captured.request_id,
                "identity": list(captured.identity), "clock_domain": self.clock,
                "prompt_tokens": len(captured.prompt_tokens), "native_finish_reason": reason,
                "output_token_ids": list(captured.output_tokens), "sampling_path": "position",
                "sampling_parameters": captured.sampling_parameters,
                "generation_parameters": captured.generation_parameters,
                "native_sampler_preserved": True, "completion_boundary": "native_terminal_output_emit",
                "program_id": self.pid,
                "job_id": captured.identity[0] if self.arm == "mars" else None,
            }
            if (
                type(receipt) is not dict or receipt.get("native_sampler_preserved") is not True
                or any(receipt.get(key) != value for key, value in required.items())
            ):
                raise ValueError("SGLang terminal receipt changes captured work, sampler, or grouping")
            complete = receipt.get("complete_monotonic_ms")
            if not deltas or deltas[-1][0] != complete:
                deltas.append((complete, 0))
            self.usage = meta
            self.cached_tokens = meta["cached_tokens"]
            self.prompt_sha256 = receipt.get("prompt_sha256")
        elif receipt is not None:
            raise ValueError("SGLang attached terminal evidence to a nonterminal output")
        return ids, reason, {"clock_domain": self.clock, "deltas": deltas}

    def consume(self, chunk, now_ms):
        if self.engine == ENGINE:
            ids, reason, evidence = self._sglang_output(chunk)
        elif self.arm == "mars":
            if chunk.get("id") != "cmpl-" + self.capture.request_id:
                raise ValueError("MARS output changes request identity")
            choices = chunk.get("choices")
            if choices == [] and self.finish is not None:
                self.usage = chunk["usage"]
                details = self.usage.get("prompt_tokens_details") or {}
                self.cached_tokens = details.get("cached_tokens", 0)
                return None
            if type(choices) is not list or len(choices) != 1 or choices[0].get("index") != 0:
                raise ValueError("MARS replay requires one native completion")
            choice = choices[0]
            ids = choice["token_ids"]
            prompt = choice.get("prompt_token_ids")
            if prompt is not None:
                self.prompt_sha256 = token_sha256(prompt)
            finish = choice.get("finish_reason")
            stop = choice.get("stop_reason")
            reason = None
            if finish == "stop" and stop is None:
                # vLLM leaves stop_reason empty for a model EOS.
                if not ids and not self.output:
                    raise ValueError("MARS EOS lacks an emitted token")
                stop = (ids or self.output)[-1]
            if finish == "stop":
                reason = {"type": "stop", "matched": stop}
            elif finish == "length":
                reason = {"type": "length", "length": len(self.output) + len(ids)}
            if finish is not None and reason is None:
                raise ValueError("MARS replay finished without native EOS or length")
            evidence = chunk.get("beliefkv_native_output")
        else:
            meta = chunk.get("meta_info")
            if type(meta) is not dict or meta.get("id") != self.capture.request_id:
                raise ValueError("MORI output changes request identity")
            ids = chunk["output_ids"]  # incremental native streaming
            reason = meta.get("finish_reason")
            evidence = meta.get(MORI_RECEIPT_KEY)
            if reason is not None and type(evidence) is dict:
                self.usage = meta
                self.cached_tokens = meta["cached_tokens"]
                self.prompt_sha256 = evidence["prompt_sha256"]
                if (
                    evidence.get("program_id") != self.pid
                    or evidence.get("sampler_preserved") is not True
                    or evidence.get("write_back_max_nodes") != 1
                ):
                    raise ValueError("MORI native program or replay settings differ from the run")
            if meta.get("completion_tokens") != len(self.output) + len(ids):
                raise ValueError("MORI replay requires incremental output")
        if (
            self.finish is not None or not self.clock or type(evidence) is not dict
            or evidence.get("clock_domain") != self.clock
            or type(ids) is not list or any(type(token) is not int for token in ids)
            or ids != list(self.capture.output_tokens[len(self.output):len(self.output) + len(ids)])
        ):
            raise ValueError("native output changes captured tokens or clock")
        deltas = evidence.get("deltas")
        if type(deltas) is not list or not deltas and (self.engine != ENGINE or ids or reason is not None):
            raise ValueError("native output lacks its engine timestamp")
        previous, count = self.last_emit_ms, 0
        for instant, delta in deltas:
            if (
                type(instant) not in (int, float) or not math.isfinite(instant)
                or not previous <= instant <= now_ms + 1.
                or type(delta) is not int or delta < 0
            ):
                raise ValueError("native output changes token counts or time order")
            previous, count = instant, count + delta
        if count != len(ids):
            raise ValueError("native output does not conserve coalesced tokens")
        self.output.extend(ids)
        self.last_emit_ms = previous
        if reason is not None:
            self.completed_ms, self.finish = previous, reason
        return deltas

    def verify(self):
        capture = self.capture
        if (
            self.completed_ms is None or self.finish != capture.native_finish_reason
            or self.prompt_sha256 != capture.prompt_sha256
            or len(self.output) != len(capture.output_tokens) or self.usage is None
            or self.usage.get("prompt_tokens") != len(capture.prompt_tokens)
            or self.usage.get("completion_tokens") != len(self.output)
            or type(self.cached_tokens) is not int
            or not 0 <= self.cached_tokens <= len(capture.prompt_tokens)
        ):
            raise ValueError("native terminal output changes captured work or stopping")
        return NativeReplayCompletion(
            capture.request_id, self.completed_ms, self.clock,
            len(capture.prompt_tokens), len(self.output),
        )


class PaperReplayBackend:
    def __init__(self, arm, corpus, client, event_sink, request_sink, output_sink,
                 admission_sink, metrics, *, model, mars=None, mori=None, timeout_s=3600.,
                 engine="legacy"):
        self.arm, self.corpus, self.client, self.model = arm, corpus, client, model
        self.engine = engine
        self.event_sink, self.request_sink = event_sink, request_sink
        self.output_sink, self.admission_sink, self.metrics = output_sink, admission_sink, metrics
        self.mars, self.mori, self.timeout_s = mars, mori, timeout_s
        self.invocations, self.programs, self.closing = {}, {}, {}
        self.active, self.verified_requests = set(), set()
        self.abort_errors = []
        self.processor = CapturedOutputLogitsProcessor.to_str() if mori or engine == ENGINE else None

    async def emit(self, event):
        self.event_sink.emit_batch((event,))
        self.metrics.event(event)
        workflow = event.workflow_id
        context = (workflow, event.context_id)
        if event.kind is Kind.INVOCATION_CREATE:
            self.invocations[(workflow, event.invocation_id)] = event
        elif event.kind is Kind.WORKFLOW_START and self.mars:
            self.mars.arrived(workflow)
        elif event.kind is Kind.WORKFLOW_END:
            if self.mars:
                self.mars.ended(workflow)
            for key in tuple(self.programs):
                if key[0] == workflow:
                    self._retire(key)
        elif (
            event.kind is Kind.CONTEXT_COMPACT
            or event.attributes.get("subagent_context_close") is True
            or event.kind in (Kind.RETURN, Kind.INVOCATION_CANCEL)
            and event.attributes.get("context_retired") is not False
        ):
            self._retire(context)

    def _retire(self, context):
        pid = self.programs.pop(context, None)
        if pid is not None:
            self.closing[pid] = asyncio.create_task(self.mori.release_program(pid))

    async def execute(self, request_id, submit):
        if self.engine == ENGINE:
            return await asyncio.wait_for(self._execute(request_id, submit), self.timeout_s)
        return await self._execute(request_id, submit)

    async def _execute(self, request_id, submit):
        submitted = time.monotonic() * 1000.
        captured = await asyncio.to_thread(self.corpus.get, request_id)
        identity = (submit.workflow_id, submit.invocation_id, submit.context_id, submit.context_epoch)
        if captured.identity != identity or submit.attributes.get("request_id") != request_id:
            raise ValueError("author baseline request changes its causal identity")
        if (
            captured.generation_parameters is None
            or submit.attributes.get("sampling_seed") != captured.generation_parameters["sampling_seed"]
        ):
            raise ValueError("author baseline request changes its sampling seed")
        context = (submit.workflow_id, submit.context_id)
        if context in self.active or request_id in self.verified_requests:
            raise ValueError("author baseline request is concurrent or already complete")
        self.active.add(context)
        state, posted, pid = None, False, None
        try:
            payload = baseline_payload(
                self.arm, self.model, captured, self.processor, engine=self.engine,
            )
            if self.mars:
                invocation = self.invocations[(submit.workflow_id, submit.invocation_id)]
                await self.mars.enter(
                    submit.workflow_id, root=invocation.parent_invocation_id is None,
                    prompt_tokens=len(captured.prompt_tokens),
                )
                endpoint = "/generate" if self.engine == ENGINE else "/v1/completions"
            else:
                pid = payload["program_id"]
                if self.programs.setdefault(context, pid) != pid:
                    raise ValueError("MORI context changed program without retiring its session")
                if pid in self.closing:
                    raise ValueError("MORI cannot reuse a retired program")
                state = self.mori.get_or_create_program(pid)
                # Group the common beginning of the tokenized prompt using
                # only this request, since the original chat fields are absent.
                prefix = token_sha256(captured.prompt_tokens[:256])
                ready = await asyncio.wait_for(
                    self.mori.update_program_before_request(
                        pid, state, {}, replay_prompt_tokens=len(captured.prompt_tokens),
                        replay_prefix_group=prefix,
                    ), self.timeout_s,
                )
                if not ready or state.backend_url != str(self.client.base_url).rstrip("/"):
                    raise ValueError("MORI did not grant its registered engine")
                endpoint = "/generate"
            granted = time.monotonic() * 1000.
            self.admission_sink({
                "event": "native_submit", "request_id": request_id,
                "workflow_id": submit.workflow_id, "program_id": pid,
                "submitted_monotonic_ms": submitted, "granted_monotonic_ms": granted,
            })
            output = NativeOutput(self.arm, captured, pid, granted, engine=self.engine)
            posted = True
            done, next_report = False, 20
            async with self.client.stream("POST", endpoint, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    value = line[5:].strip()
                    if value == "[DONE]":
                        done = True
                        break
                    now = time.monotonic() * 1000.
                    deltas = output.consume(json.loads(value), now)
                    if deltas is None:
                        continue
                    self.metrics.progress(request_id, len(output.output), now)
                    for emitted_ms, delta in deltas:
                        if delta:
                            self.output_sink({
                                "request_id": request_id, "workflow_id": submit.workflow_id,
                                "emit_monotonic_ms": emitted_ms, "output_token_delta": delta,
                                "clock_domain": output.clock,
                            })
                    if state is not None and len(output.output) >= next_report:
                        delta_to_report = (len(output.output) - next_report + 20) // 20 * 20
                        self.mori.update_program_tokens_streaming(state, delta_to_report)
                        next_report += delta_to_report
            if not done:
                raise ValueError("author baseline stream ended before its terminator")
            result = output.verify()
            now = time.monotonic() * 1000.
            if state is not None:
                self.mori.update_program_after_request(
                    pid, state, result.prompt_tokens + result.output_tokens, result.prompt_tokens,
                )
                self.mori.record_cached_tokens(pid, state, output.cached_tokens)
            self.metrics.complete(request_id, result.output_tokens, now)
            self.request_sink({
                "request_id": request_id, "submitted_monotonic_ms": submitted,
                "native_complete_monotonic_ms": result.complete_monotonic_ms,
                "clock_domain": result.clock_domain,
                "http_complete_monotonic_ms": now, "identity": list(identity),
                "program_id": pid, "prompt_tokens": result.prompt_tokens,
                "output_tokens": result.output_tokens, "cached_tokens": output.cached_tokens,
                "prompt_sha256": output.prompt_sha256, "output_sha256": token_sha256(output.output),
                "native_finish_reason": output.finish, "exact_tokens_verified": True,
                "response_metrics": self.metrics.request_summary(request_id),
            })
            self.verified_requests.add(request_id)
            return result
        except BaseException:
            if (self.mori or self.engine == ENGINE) and posted:
                try:
                    response = await self.client.post("/abort_request", json={"rid": request_id}, timeout=10.)
                    response.raise_for_status()
                except Exception as error:
                    self.abort_errors.append((request_id, repr(error)))
            raise
        finally:
            self.active.remove(context)

    async def drain(self):
        if self.active:
            raise ValueError("cannot drain author baseline replay with active requests")
        for context in tuple(self.programs):
            self._retire(context)
        if self.closing and not all(await asyncio.gather(*self.closing.values())):
            raise ValueError("MORI did not release every tracked program")
        if self.mars:
            await self.mars.close()
        if self.abort_errors:
            raise ValueError(f"author replay abort failed for {self.abort_errors!r}")
