#!/usr/bin/env python3
"""Bounded native token/EOS microcheck, not a causal workflow performance run."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay_backend import CausalHTTPBackend
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.event_channel import JsonlRuntimeEventSink
from scripts.run_causal_gpu_replay import source_plan


def select_requests(plan, *, contexts=8, turns=3):
    if (
        type(contexts) is not int or not 1 <= contexts <= 8
        or type(turns) is not int or not 1 <= turns <= 3
    ):
        raise ValueError("smoke limits are 1-8 contexts and 1-3 turns")
    events = [node.event for node in plan.nodes if node.event is not None]
    roots = sorted(
        (event for event in events if event.kind is Kind.INVOCATION_CREATE
         and event.parent_invocation_id is None),
        key=lambda event: (event.ts_ms, event.event_id),
    )[:contexts]
    if len(roots) != contexts:
        raise ValueError("source lacks the predeclared number of roots")
    result = []
    for root in roots:
        calls = sorted(
            (event for event in events if event.kind is Kind.LLM_SUBMIT
             and (event.workflow_id, event.invocation_id, event.context_id)
             == (root.workflow_id, root.invocation_id, root.context_id)),
            key=lambda event: (event.ts_ms, event.event_id),
        )[:turns]
        if len(calls) != turns:
            raise ValueError("selected root lacks the predeclared number of turns")
        result.append((root, tuple(calls)))
    return tuple(result)


async def run(args):
    if args.output_dir.exists():
        raise FileExistsError("preserve existing smoke artifacts")
    plan, expected, evidence = source_plan(args.source_arm)
    selected = select_requests(plan, contexts=args.contexts, turns=args.turns)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with ExitStack() as stack:
        corpus = stack.enter_context(CapturedTokenCorpus(
            args.source_arm / "server/request_tokens.jsonl", expected,
        ))
        log = stack.enter_context(JsonlRuntimeEventSink(
            args.output_dir / "runtime_events.smoke.jsonl",
        ))
        output = stack.enter_context((args.output_dir / "native_requests.jsonl").open("x"))
        receipts = []

        def record(row):
            receipts.append(row)
            output.write(json.dumps(row, allow_nan=False) + "\n")

        async with httpx.AsyncClient(
            base_url=args.base_url, timeout=httpx.Timeout(300.),
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=0),
        ) as client:
            backend = CausalHTTPBackend(corpus, client, log.emit_batch, observe_request=record)

            async def root_calls(root, calls):
                await backend.emit(replace(root, ts_ms=time.monotonic() * 1000.))
                for call in calls:
                    visible = replace(call, ts_ms=time.monotonic() * 1000.)
                    await backend.emit(visible)
                    await backend.execute(call.attributes["request_id"], visible)

            tasks = [
                asyncio.create_task(root_calls(root, calls)) for root, calls in selected
            ]
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await backend.drain()
        required = {
            call.attributes["request_id"] for _, calls in selected for call in calls
        }
        if backend.verified_requests != required:
            raise ValueError("native smoke request population is incomplete")
        stop_count = sum(
            row["native_finish_reason"]["type"] == "stop" for row in receipts
        )
        if not stop_count:
            raise ValueError("selected requests provided no native stop evidence")
        output.flush()
        return {
            **evidence, "source_plan": plan.summary(), "token_source": corpus.evidence,
            "selected_request_ids": sorted(required),
            "contexts": args.contexts, "turns_per_context": args.turns,
            "verified_requests": len(required), "native_stop_requests": stop_count,
            "native_data_plane_sample_verified": True,
            "causal_workflow_replay": False,
            "gpu_process_verified": False, "gpu_replay_qualified": False,
            "performance_accepted": False,
            "session_closes": backend.session_closes,
            "session_generation_bindings": backend.session_generation_bindings,
            "native_requests_sha256": sha256_file(args.output_dir / "native_requests.jsonl"),
            "scope": (
                "Predeclared root-prefix request microcheck only. This omits "
                "original tools/JOIN waits, has no live policy event receiver, "
                "and is not a full causal replay, JCT comparison or GPU certificate."
            ),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-arm", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--contexts", type=int, default=8)
    parser.add_argument("--turns", type=int, default=3)
    args = parser.parse_args()
    if not args.base_url.startswith("http://127.0.0.1:"):
        parser.error("smoke requires an explicit local server port")
    if args.output_dir.exists():
        parser.error("preserve existing smoke artifacts")
    try:
        result = asyncio.run(run(args))
    except BaseException as error:
        if args.output_dir.is_dir() and not isinstance(error, FileExistsError):
            with (args.output_dir / "execution_failure.json").open("x") as stream:
                json.dump({
                    "error_type": type(error).__name__, "error": str(error),
                    "gpu_replay_qualified": False, "performance_accepted": False,
                }, stream, indent=2, allow_nan=False)
        raise
    with (args.output_dir / "summary.json").open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: result[key] for key in (
            "verified_requests", "native_stop_requests", "native_data_plane_sample_verified",
            "gpu_process_verified", "gpu_replay_qualified", "performance_accepted",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
