#!/usr/bin/env python3
"""CPU benchmark native chat conversion on growing, source-backed trace histories."""

from __future__ import annotations

import argparse
import ast
from collections import OrderedDict
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from types import MethodType, ModuleType, SimpleNamespace
import typing
from unittest.mock import patch

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.long_repository_tasks import parse_repository_context  # noqa: E402
from beliefkv.runtime import chat_token_cache  # noqa: E402
from scripts.benchmark_chat_prompt_encoding import trajectory_messages  # noqa: E402


def source_binding(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def native_converter(source, tokenizer):
    parsed = ast.parse(source.read_text())
    owner = next(node for node in parsed.body if isinstance(node, ast.ClassDef)
                 and node.name == "OpenAIServingChat")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_render_and_encode_chat_template")
    scope = {
        **vars(typing), "os": os, "orjson": orjson,
        "_CHAT_TEMPLATE_CACHE_MAX_SIZE": 32,
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), scope)
    serving = SimpleNamespace(
        tokenizer_manager=SimpleNamespace(tokenizer=tokenizer),
        _chat_template_cache=OrderedDict(),
    )
    return MethodType(scope[method.name], serving), serving


def distribution(values):
    values = list(values)
    ordered = sorted(values)
    return {
        "count": len(values), "mean": statistics.mean(values),
        "p50": statistics.median(values),
        "p90": ordered[min(len(ordered) - 1, int(.9 * len(ordered)))],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline-serving", type=Path, required=True)
    parser.add_argument("--candidate-serving", type=Path, required=True)
    parser.add_argument("--baseline-token-cache-revision")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-workflows", type=int, default=6)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output must be fresh")
    from transformers import AutoTokenizer

    manifest = json.loads(args.manifest.read_text())
    tokenizer = AutoTokenizer.from_pretrained(manifest["tokenizer"], local_files_only=True)
    by_id = {row["instance_id"]: row for row in manifest["workloads"]}
    cases, sources = [], [source_binding(args.manifest)]
    for path in sorted(args.arm.glob("client_*/workflows/*/trajectory.json"))[:args.max_workflows]:
        context = parse_repository_context(by_id[path.parent.name]["frozen_repository_context"])
        messages = [
            {"role": "system", "content": context.text},
            *trajectory_messages(json.loads(path.read_text())),
        ]
        for message in messages:
            for call in message.get("tool_calls", ()):
                if isinstance(call["function"].get("arguments"), str):
                    call["function"]["arguments"] = json.loads(call["function"]["arguments"])
        stops = [
            index + 1 for index, message in enumerate(messages)
            if message["role"] in ("user", "tool")
        ]
        for ordinal in sorted(set((0, len(stops) // 2, len(stops) - 1))):
            cases.append((f"{path.parent.name}:{stops[ordinal]}", messages[:stops[ordinal]]))
        sources.extend([source_binding(path), source_binding(context.path)])
    baseline, baseline_state = native_converter(args.baseline_serving, tokenizer)
    candidate, candidate_state = native_converter(args.candidate_serving, tokenizer)
    baseline_cache = None
    baseline_cache_binding = None
    if args.baseline_token_cache_revision:
        cache_path = "beliefkv/runtime/chat_token_cache.py"
        source = subprocess.check_output(
            ["git", "show", f"{args.baseline_token_cache_revision}:{cache_path}"],
            cwd=ROOT, text=True,
        )
        module = ModuleType("_baseline_chat_token_cache")
        exec(compile(source, f"{args.baseline_token_cache_revision}:{cache_path}", "exec"), vars(module))
        baseline_cache = module.ChatPromptTokenCache
        baseline_cache_binding = {
            "revision": args.baseline_token_cache_revision, "path": cache_path,
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
        }
    rows = {identity: {
        "case": identity, "baseline_wall_ms": [], "candidate_wall_ms": [],
        "baseline_thread_cpu_ms": [], "candidate_thread_cpu_ms": [],
    } for identity, _ in cases}
    os.environ["BELIEFKV_CHAT_TOKEN_BLOCK_CACHE"] = "1"
    for _ in range(args.iterations):
        for state in (baseline_state, candidate_state):
            state._chat_template_cache.clear()
            if hasattr(state, "_beliefkv_chat_token_cache"):
                del state._beliefkv_chat_token_cache
        for identity, messages in cases:
            outputs = []
            for label, converter in (("baseline", baseline), ("candidate", candidate)):
                cache_context = patch.object(
                    chat_token_cache, "ChatPromptTokenCache", baseline_cache,
                ) if label == "baseline" and baseline_cache is not None else nullcontext()
                with cache_context:
                    wall_start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
                    _, ids, _ = converter(
                        messages, tools=None, template_kwargs={"enable_thinking": True},
                        encode_kwargs={}, use_cache=True, decode_prompt=False,
                    )
                    rows[identity][f"{label}_thread_cpu_ms"].append(
                        (time.thread_time_ns() - cpu_start) / 1e6,
                    )
                    rows[identity][f"{label}_wall_ms"].append(
                        (time.perf_counter_ns() - wall_start) / 1e6,
                    )
                outputs.append(ids)
            if outputs[0] != outputs[1]:
                raise AssertionError(f"native token mismatch: {identity}")
            rows[identity]["prompt_tokens"] = len(outputs[0])
            rows[identity]["tokens_equal"] = True
    result_rows = [{
        key: statistics.mean(value) if isinstance(value, list) else value
        for key, value in row.items()
    } for row in rows.values()]
    cache = candidate_state._beliefkv_chat_token_cache
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "CPU only; real Qwen tokenizer and native conversion method. Complete qualified "
            "source plus growing root trajectory histories; reconstructed inputs exclude "
            "original role instructions/tool schemas and are not exact HTTP replay. "
            "No GPU, queueing, JCT, baseline fairness or checkpoint availability claim."
        ),
        "baseline": source_binding(args.baseline_serving),
        "candidate": source_binding(args.candidate_serving),
        "baseline_token_cache": baseline_cache_binding,
        "candidate_token_cache": source_binding(Path(chat_token_cache.__file__)),
        "benchmark": source_binding(Path(__file__)),
        "sources": sources, "iterations": args.iterations, "all_tokens_equal": True,
        "cache": {"bytes": cache.cached_bytes, "hits": cache.hits,
                  "misses": cache.misses, "bypasses": cache.bypasses},
        "summary": {
            name: distribution(row[name] for row in result_rows)
            for name in ("baseline_wall_ms", "candidate_wall_ms",
                         "baseline_thread_cpu_ms", "candidate_thread_cpu_ms")
        },
        "rows": result_rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: report[key] for key in ("summary", "cache", "all_tokens_equal")}, indent=2))


if __name__ == "__main__":
    main()
