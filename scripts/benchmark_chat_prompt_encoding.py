#!/usr/bin/env python3
"""Compare text-only native chat conversion with exact token equality, CPU only."""

from __future__ import annotations

import argparse
import ast
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
from statistics import mean
import sys
import time
from types import MethodType, SimpleNamespace

import numpy as np


def trajectory_messages(rows):
    messages = []
    for row in rows:
        kind = row.get("type")
        if kind not in ("human", "ai", "tool", "system"):
            continue
        message = {
            "role": {"human": "user", "ai": "assistant"}.get(kind, kind),
            "content": row.get("content") or "",
        }
        if kind == "ai" and row.get("tool_calls"):
            message["tool_calls"] = [
                {
                    "id": call["id"], "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call["args"], ensure_ascii=False),
                    },
                }
                for call in row["tool_calls"]
            ]
        if kind == "tool":
            message["tool_call_id"] = row["tool_call_id"]
        messages.append(message)
    return messages


def load_cases(arm: Path, max_workflows: int, max_cases: int):
    cases = []
    for path in sorted(arm.glob("client_*/workflows/*/trajectory.json"))[:max_workflows]:
        messages = trajectory_messages(json.loads(path.read_text()))
        ends = [
            index + 1 for index, message in enumerate(messages)
            if message["role"] in ("user", "tool")
        ]
        for stop in sorted(set(ends[index] for index in (
            0, len(ends) // 2, len(ends) - 1,
        ))) if ends else ():
            cases.append((f"{path.parent.name}:{stop}", {
                "model": "Qwen3.5-35B-A3B", "messages": messages[:stop],
            }))
    cases.extend([
        ("reasoning_disabled", {
            "model": "test", "messages": [{"role": "user", "content": "Explain foo(x)."}],
            "chat_template_kwargs": {"enable_thinking": False},
        }),
        ("continue_final", {
            "model": "test", "messages": [
                {"role": "user", "content": "Explain foo(x)."},
                {"role": "assistant", "content": "The conclusion is "},
            ],
            "continue_final_message": True,
        }),
        ("text_parts_and_tools", {
            "model": "test", "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Inspect foo.py."}]},
            ],
            "tools": [{"type": "function", "function": {
                "name": "read_file", "parameters": {
                    "type": "object", "properties": {"path": {"type": "string"}},
                },
            }}],
        }),
    ])
    return cases[:max_cases]


def make_serving(module, tokenizer):
    serving = object.__new__(module.OpenAIServingChat)
    serving.chat_encoding_spec = None
    serving._chat_template_cache = OrderedDict()
    serving.tokenizer_manager = SimpleNamespace(
        tokenizer=tokenizer, model_config=SimpleNamespace(
            is_multimodal=True, hf_config=SimpleNamespace(model_type="qwen3_5_moe"),
        ),
    )
    serving.template_manager = SimpleNamespace(
        jinja_template_content_format="openai", jinja_template_may_reorder_tool_results=False,
        reasoning_config=None,
    )
    serving._tokenizer_auto_adds_specials = (
        tokenizer.encode("x") != tokenizer.encode("x", add_special_tokens=False)
    )
    return serving


def install_baseline(serving, module, source: Path):
    names = {"_apply_jinja_template", "_engine_prompt", "_render_and_encode_chat_template"}
    parsed = ast.parse(source.read_text())
    owner = next(node for node in parsed.body if isinstance(node, ast.ClassDef)
                 and node.name == "OpenAIServingChat")
    methods = [node for node in owner.body if isinstance(node, ast.FunctionDef)
               and node.name in names]
    if {node.name for node in methods} != names:
        raise ValueError("baseline is missing native chat methods")
    scope = dict(vars(module))
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), "exec"), scope)
    for name in names:
        setattr(serving, name, MethodType(scope[name], serving))


def convert(serving, request):
    tools = [tool.model_dump() for tool in request.tools] if request.tools else None
    processed = serving._apply_jinja_template(request, tools, True)
    key, prompt = serving._engine_prompt(processed, True)
    if key == "text":
        prompt = serving.tokenizer_manager.tokenizer([prompt])["input_ids"][0]
    return list(prompt)


def distribution(values):
    return {
        "count": len(values), "mean": mean(values),
        **{f"p{percentile}": float(np.percentile(values, percentile))
           for percentile in (50, 90, 99)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--baseline-serving", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-workflows", type=int, default=10)
    parser.add_argument("--max-cases", type=int, default=33)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    sys.path.insert(0, str(args.engine_root / "python"))
    from sglang.srt.entrypoints.openai import serving_chat
    from sglang.srt.entrypoints.openai.protocol import ChatCompletionRequest
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    baseline, candidate = [make_serving(serving_chat, tokenizer) for _ in range(2)]
    install_baseline(baseline, serving_chat, args.baseline_serving)
    requests = [
        (identity, ChatCompletionRequest.model_validate(payload))
        for identity, payload in load_cases(args.arm, args.max_workflows, args.max_cases)
    ]
    rows = []
    for identity, request in requests:
        expected, actual = convert(baseline, request), convert(candidate, request)
        if expected != actual:
            raise AssertionError(f"token mismatch in {identity}")
        row = {"case": identity, "prompt_tokens": len(actual), "tokens_equal": True}
        for temperature in ("cold", "warm"):
            for label, serving in (("baseline", baseline), ("candidate", candidate)):
                wall, cpu = [], []
                for _ in range(args.iterations):
                    if temperature == "cold":
                        serving._chat_template_cache.clear()
                    wall_start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
                    ids = convert(serving, request)
                    cpu.append((time.thread_time_ns() - cpu_start) / 1e6)
                    wall.append((time.perf_counter_ns() - wall_start) / 1e6)
                    if ids != expected:
                        raise AssertionError(f"timed token mismatch in {identity}")
                row[f"{temperature}_{label}_wall_ms"] = mean(wall)
                row[f"{temperature}_{label}_thread_cpu_ms"] = mean(cpu)
        rows.append(row)
    candidate_path = Path(serving_chat.__file__).resolve()
    report = {
        "scope": (
            "CPU conversion benchmark with the real tokenizer and native methods; "
            "trajectory-derived text histories, not exact original HTTP inputs. "
            "No GPU, IPC, queueing or end-to-end performance claim."
        ),
        "baseline_source": str(args.baseline_serving.resolve()),
        "baseline_sha256": hashlib.sha256(args.baseline_serving.read_bytes()).hexdigest(),
        "candidate_source": str(candidate_path),
        "candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        "model": str(args.model), "iterations": args.iterations,
        "case_count": len(rows), "all_tokens_equal": True, "rows": rows,
        "summary": {
            key: distribution([row[key] for row in rows])
            for key in rows[0] if key.endswith("_ms")
        },
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
