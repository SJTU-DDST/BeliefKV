#!/usr/bin/env python3
"""Measure full initial root requests through the real agent construction path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, convert_to_openai_messages
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

from beliefkv.experiments import deepagents_swebench as runner
from beliefkv.experiments.delegation_policy import (
    REUSABLE_NESTED_IN_GRAPH_PROFILE,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.context_lifecycle import ContextLifecyclePolicy
from beliefkv.runtime.context_token_counter import ChatTemplateTokenCounter
from beliefkv.runtime.replay_token_capture import _wire
from beliefkv.runtime.reusable_subagents import ReusableSubagentRegistry


class InitialInputCaptured(Exception):
    pass


class InputProbeModel(BaseChatModel):
    _tools: list = PrivateAttr(default_factory=list)
    _requests: list = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self):
        return "cpu-initial-input-probe"

    def bind_tools(self, tools, **kwargs):
        self._tools = [convert_to_openai_tool(item) for item in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._requests.append((list(messages), list(self._tools)))
        raise InitialInputCaptured


def root_request(config, workload, directory):
    model = InputProbeModel()
    audit = runner.JsonlAudit(directory / "audit.jsonl")
    backend = runner.DockerWorkspaceBackend(
        workload.source_repo, image=workload.docker_image, audit=audit, support_dir=None,
    )
    trace = runner.JsonlRuntimeEventSink(directory / "events.jsonl")
    adapter = runner.DeepAgentsRuntimeAdapter(
        trace, runner.BeliefKVRequestMetadata("cpu-probe", "root", "root-context", 0),
        reusable_subagents=ReusableSubagentRegistry("cpu-probe"),
    )
    deadline = SimpleNamespace(register_model=lambda _: None, deadline=None)
    try:
        with patch.object(runner, "_model", return_value=model):
            agent = runner._build_autonomous_agent(
                config, workload, backend, adapter, deadline,
            )
            prompt = runner._task_prompt(workload, workspace=workload.source_repo)
            prompt += "\n\n" + runner.DYNAMIC_COLLABORATIVE_TASK_INSTRUCTION.strip()
            try:
                agent.invoke({"messages": [HumanMessage(content=prompt)]})
            except InitialInputCaptured:
                pass
    finally:
        audit.close()
        trace.close()
    if len(model._requests) != 1:
        raise ValueError("initial input probe must execute exactly one local fake model call")
    return model._requests[0]


def prefix_union(requests):
    ordered = sorted(requests)
    total, previous = 0, []
    common_lengths = []
    for tokens in ordered:
        common = 0
        for left, right in zip(previous, tokens):
            if left != right:
                break
            common += 1
        total += len(tokens) - common
        if previous:
            common_lengths.append(common)
        previous = tokens
    return total, common_lengths


def audit_inputs(manifest, tokenizer_path):
    bundle = runner.load_workload_bundle(manifest)
    workloads = bundle.workloads
    if not workloads or len({row.repo for row in workloads}) != len(workloads):
        raise ValueError("project-disjoint input audit requires one root per project")
    counter = ChatTemplateTokenCounter(str(tokenizer_path))
    rows, requests = [], []
    with tempfile.TemporaryDirectory(prefix="beliefkv-root-input-probe-") as temporary:
        directory = Path(temporary)
        for index, workload in enumerate(workloads):
            output = directory / str(index)
            output.mkdir()
            config = runner.DeepAgentsExperimentConfig(
                mode="autonomous", base_url="http://127.0.0.1:1/v1", model="cpu-probe",
                output_dir=output, workload_manifest=manifest,
                docker_image=workload.docker_image, completion_gate_enabled=False,
                native_radix_sessions=True, native_policy_baseline=True,
                max_completion_tokens=16384, sampling_seed=21,
                subagent_fanout_profile=REUSABLE_NESTED_IN_GRAPH_PROFILE,
                context_lifecycle=ContextLifecyclePolicy(
                    window_tokens=262144, model_context_tokens=262144,
                    compression_trigger_ratio=.95,
                    tokenizer_path=str(tokenizer_path),
                ),
            )
            messages, tools = root_request(config, workload, output)
            wire = convert_to_openai_messages(messages)
            rendered = counter.tokenizer.apply_chat_template(
                wire, tools=tools, tokenize=False,
                add_generation_prompt=True, enable_thinking=True,
            )
            tokens = counter.tokenizer.encode(rendered, add_special_tokens=False)
            cached_count = counter(messages, tools=tools)
            if cached_count != len(tokens):
                raise ValueError(
                    f"cached runtime counter differs from exact initial input: "
                    f"{cached_count} != {len(tokens)} for {workload.instance_id}"
                )
            if len(tokens) + config.max_completion_tokens > 262144:
                raise ValueError("initial input plus completion budget exceeds model capacity")
            if len(tokens) >= int(.95 * 262144):
                raise ValueError("initial input already reaches the compression threshold")
            requests.append(tokens)
            rows.append({
                "instance_id": workload.instance_id, "repo": workload.repo,
                "source_context_tokens": workload.frozen_repository_context.tokens,
                "prompt_tokens": len(tokens), "prompt_sha256": hashlib.sha256(_wire(tokens)).hexdigest(),
                "message_count": len(messages), "tool_count": len(tools),
                "context_sha256": workload.frozen_repository_context.sha256,
            })
    union, common = prefix_union(requests)
    return {
        "manifest": str(manifest.resolve()), "manifest_sha256": bundle.manifest_sha256,
        "auditor_sha256": sha256_file(Path(__file__)),
        "tokenizer_sha256": sha256_file(tokenizer_path / "tokenizer.json"),
        "tokenizer_config_sha256": sha256_file(tokenizer_path / "tokenizer_config.json"),
        "profile": REUSABLE_NESTED_IN_GRAPH_PROFILE,
        "roots": len(rows), "distinct_projects": len({row["repo"] for row in rows}),
        "minimum_prompt_tokens": min(map(len, requests)),
        "maximum_prompt_tokens": max(map(len, requests)),
        "initial_prefix_union_tokens": union,
        "initial_prefix_union_full_bytes": union * 20480,
        "adjacent_sorted_common_prefix_tokens": common,
        "all_initial_inputs_over_100k": all(len(tokens) > 100000 for tokens in requests),
        "rows": rows, "cpu_only": True, "gpu_performance_accepted": False,
        "scope": [
            "Real root construction, middleware, task schema and local model chat template.",
            "One fake model call per root; no LLM generation, GPU service or tool execution.",
            "Initial prefix union is a content working-set proxy, not instantaneous physical residency.",
            "No JOIN count, true handle reuse, migration volume or speedup is inferred.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        parser.error("input audit requires CUDA_VISIBLE_DEVICES=99999")
    if args.output.exists():
        parser.error("preserve existing input evidence")
    result = audit_inputs(args.manifest.resolve(), args.tokenizer.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in (
        "roots", "minimum_prompt_tokens", "maximum_prompt_tokens",
        "initial_prefix_union_full_bytes", "all_initial_inputs_over_100k",
    )}, indent=2))


if __name__ == "__main__":
    main()
