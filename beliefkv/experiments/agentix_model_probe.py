"""Bounded unforced model probes, never a substitute for the complete workload."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import math
import time

from beliefkv.core.events import (
    ContextMode, ExecutionMode, RelationType, RuntimeEvent, RuntimeEventKind as Kind,
)
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


@dataclass(frozen=True)
class ModelFixture:
    prompt_tokens: int
    generated_tokens: int
    context_limit: int
    device_tokens: int
    host_gb: int
    require_backing_drain: bool = False


FIXTURES = {
    "roomy": ModelFixture(24576, 64, 262144, 805198, 200),
    "pressure": ModelFixture(24000, 512, 32768, 32768, 8, True),
}


def fixture_policy(document, fixture):
    result = deepcopy(document)
    result["parameters"]["context_limit"] = fixture.context_limit
    return result


def compare_unforced_outputs(rows):
    expected = {f"{phase}-{index}" for phase in ("actual", "reference") for index in range(3)}
    if set(rows) != expected:
        raise ValueError("model comparison requires three preempted calls and three serial references")
    deltas = {}
    for index in range(3):
        actual, reference = rows[f"actual-{index}"], rows[f"reference-{index}"]
        if actual["output_token_ids"] != reference["output_token_ids"]:
            raise ValueError("Agentix changed actual greedy model output")
        differences = [
            abs(left - right) for left, right in zip(
                actual["output_logprobs"], reference["output_logprobs"], strict=True,
            )
        ]
        if not differences or any(not math.isfinite(value) or value > .05 for value in differences):
            raise ValueError("Agentix model logprobs exceed the declared 0.05 tolerance")
        deltas[str(index)] = max(differences)
    return {"actual_greedy_tokens_equal": True, "maximum_logprob_absolute_delta": deltas}


async def unforced_probe(
    prompts, fixture, *, generate, emit, session_for, observe_body, record_response,
):
    """Only the qualification harness varies concurrency; model output is unforced."""
    if len(prompts) != 3 or any(len(prompt) != fixture.prompt_tokens for prompt in prompts):
        raise ValueError("Agentix model probe needs three explicitly bounded source prefixes")
    rows, sessions, sequence = {}, set(), 0

    async def send(kind, workflow, invocation=None, **kwargs):
        nonlocal sequence
        sequence += 1
        await emit(RuntimeEvent(
            f"agentix-model:{sequence}", time.monotonic() * 1000., kind, workflow,
            invocation_id=invocation, context_id=invocation, context_epoch=0, **kwargs,
        ))

    async def call(phase, index):
        label = f"{phase}-{index}"
        workflow = "agentix-model-actual" if phase == "actual" else f"agentix-model-reference-{index}"
        invocation = f"agentix-model-{label}"
        if phase == "reference":
            await send(Kind.WORKFLOW_START, workflow)
        await send(
            Kind.INVOCATION_CREATE, workflow, invocation,
            relation_type=RelationType.ROOT, context_mode=ContextMode.FRESH,
            execution_mode=ExecutionMode.FOREGROUND,
            agent_definition_id="agentix_model_qualification", agent_instance_id=invocation,
        )
        metadata = BeliefKVRequestMetadata(
            workflow, invocation, invocation, 0,
            agent_definition_id="agentix_model_qualification", agent_instance_id=invocation,
            relation_type="root", execution_mode="foreground", full_prompt_replay_guaranteed=True,
        )
        request = "agentix-model:" + label
        body = {
            "rid": request, "input_ids": list(prompts[index]),
            "beliefkv_metadata": metadata.to_wire(),
            "extra_key": invocation, "cache_salt": invocation,
            "session_id": session_for(metadata),
            "sampling_params": {
                "temperature": 0., "top_k": 1,
                "max_new_tokens": fixture.generated_tokens,
                "min_new_tokens": fixture.generated_tokens,
                "ignore_eos": True, "sampling_seed": 21,
            },
            "stream": False, "return_logprob": True, "logprob_start_len": -1,
            "return_text_in_logprobs": False,
        }
        sessions.add(body["session_id"])
        observe_body(label, body)
        await send(Kind.LLM_SUBMIT, workflow, invocation, attributes={"request_id": request})
        result = await generate(body)
        rows[label] = record_response(label, result, request, len(prompts[index]))
        await send(Kind.LLM_RESULT, workflow, invocation, attributes={"request_id": request})
        await send(Kind.RETURN, workflow, invocation, attributes={"context_retired": False})
        if phase == "reference":
            await send(Kind.WORKFLOW_END, workflow)

    await send(Kind.WORKFLOW_START, "agentix-model-actual")
    tasks = [asyncio.create_task(call("actual", index)) for index in range(3)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    await send(Kind.WORKFLOW_END, "agentix-model-actual")
    for index in range(3):
        await call("reference", index)
    return rows, sessions
