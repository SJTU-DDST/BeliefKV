#!/usr/bin/env python3
"""Execute a captured causal workload; GPU/source policy qualification is separate."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent
from beliefkv.experiments.agentx_replay import load_agentx_preparation
from beliefkv.experiments.agentx_tokens import AgentXTokenCorpus
from beliefkv.experiments.causal_replay import compile_causal_replay, execute_causal_replay
from beliefkv.experiments.causal_replay_backend import CausalHTTPBackend
from beliefkv.experiments.causal_replay_delivery import ReplayEventDelivery
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_metrics import StreamingWorkflowMetrics
from beliefkv.runtime.event_channel import (
    JsonlRuntimeEventSink, UnixDatagramRuntimeEventSink,
)
from beliefkv.runtime.replay_token_capture import native_request_expectations
from scripts.audit_sealed_wait_prefetch import SealedRecords


def source_plan(arm: Path):
    summaries = list(arm.glob("client_*/summary.json"))
    if len(summaries) != 1:
        raise ValueError("source must have one completed client summary")
    summary = json.loads(summaries[0].read_bytes())
    paths = sorted(summaries[0].parent.glob("workflows/*/runtime_events.deepagents.jsonl"))
    if (
        not paths or len(paths) != summary.get("workflow_count")
        or len(paths) != summary.get("completed_workflows")
    ):
        raise ValueError("source workflow population is incomplete")
    sealed = SealedRecords()
    events = [
        RuntimeEvent.from_dict(row) for path in paths for row in sealed(path)
    ]
    native = tuple(sealed(arm / "server/runtime_events.sglang.jsonl"))
    plan = compile_causal_replay(events, native)
    return plan, native_request_expectations(native), {
        "source_arm": str(arm.resolve()),
        "client_summary_sha256": sha256_file(summaries[0]),
        "input_jsonl_prefixes": sealed.prefixes,
        "client_quality_summary": {
            key: summary.get(key) for key in (
                "workflow_count", "completed_workflows", "failed_workflows",
                "native_agent_jct_eligible_workflow_count",
                "native_agent_jct_ineligible_workflow_count",
            )
        },
        "independent_task_correctness_verified": False,
    }


async def run(args) -> dict:
    if args.output_dir.exists():
        raise FileExistsError("preserve existing replay artifacts")
    agentx = getattr(args, "agentx_prepared", None) is not None
    stream_metrics = agentx or getattr(args, "stream_response_metrics", False)
    if agentx:
        plan, timing, _, preparation = load_agentx_preparation(args.agentx_prepared)
        expected = None
        evidence = {
            "source_kind": "agentx_published_lengths_and_hashes",
            "prepared_source": str(args.agentx_prepared),
            "source_preparation_sha256": sha256_file(args.agentx_prepared / "preparation.json"),
            "source_plan_sha256": preparation["artifacts"]["plan.json"],
            "source_full_sha256": preparation["source_full_sha256"],
            "native_source_capture_required": False,
            "independent_task_correctness_verified": False,
        }
    else:
        plan, expected, evidence = source_plan(args.source_arm)
        timing = None
    if not args.base_url.startswith("http://127.0.0.1:"):
        raise ValueError("native replay requires an explicit local server port")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with ExitStack() as stack:
        corpus = stack.enter_context(
            AgentXTokenCorpus(args.agentx_token_dir, args.agentx_prepared) if agentx
            else CapturedTokenCorpus(args.source_arm / "server/request_tokens.jsonl", expected)
        )
        log = stack.enter_context(JsonlRuntimeEventSink(
            args.output_dir / "runtime_events.replay.jsonl",
        ))
        delivery = stack.enter_context(ReplayEventDelivery(
            log.emit_batch,
            (lambda _workflow: UnixDatagramRuntimeEventSink(args.event_socket))
            if args.event_socket is not None else None,
        ))
        requests = stack.enter_context(
            (args.output_dir / "native_requests.jsonl").open("x"),
        )

        def emit(events):
            delivery.emit_batch(events)

        def record(row):
            requests.write(json.dumps(row, allow_nan=False) + "\n")

        started = time.monotonic() * 1000.
        async with httpx.AsyncClient(
            base_url=args.base_url.rstrip("/"), timeout=httpx.Timeout(args.request_timeout_s),
            limits=httpx.Limits(max_connections=128, max_keepalive_connections=0),
        ) as client:
            response_metrics = StreamingWorkflowMetrics() if stream_metrics else None
            backend = CausalHTTPBackend(
                corpus, client, emit, observe_request=record, streaming=stream_metrics,
                response_metrics=response_metrics,
            )
            failure = None
            try:
                result = await execute_causal_replay(
                    plan, backend.execute, backend.emit, timing_policy=timing,
                )
            except BaseException as error:
                failure = error
                raise
            finally:
                try:
                    await backend.drain()
                    await asyncio.to_thread(delivery.flush)
                except BaseException as cleanup_error:
                    with (args.output_dir / "cleanup_failure.json").open("x") as stream:
                        json.dump({
                            "error_type": type(cleanup_error).__name__,
                            "error": str(cleanup_error),
                            "primary_error": repr(failure) if failure is not None else None,
                            "abort_errors": backend.abort_errors,
                        }, stream, indent=2, allow_nan=False)
                    if failure is None:
                        raise
        if backend.verified_requests != set(plan.request_usage):
            raise ValueError("exact native request population is incomplete")
        requests.flush()
        result.update(
            **evidence, observed_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            source_plan=plan.summary(), token_source=corpus.evidence,
            exact_tokens_verified=True, original_native_stopping_verified=not agentx,
            published_length_stopping_verified=agentx,
            benchmark_content_synthetic=agentx,
            streaming_response_metrics_enabled=stream_metrics,
            streaming_response_metrics=response_metrics.summary() if response_metrics else None,
            native_session_http_lifecycle_completed=True,
            physical_session_release_acknowledged=False,
            session_closes=backend.session_closes,
            session_generation_bindings=backend.session_generation_bindings,
            session_generation_rebindings=backend.session_generation_rebindings,
            native_requests_sha256=sha256_file(args.output_dir / "native_requests.jsonl"),
            started_monotonic_ms=started, finished_monotonic_ms=time.monotonic() * 1000.,
            live_control_events_delivered=args.event_socket is not None,
            control_delivery=delivery.summary(),
            replay_process_python_hash_seed=os.environ.get("PYTHONHASHSEED"),
            server_source_and_gpu_qualified=False,
            gpu_replay_qualified=False, performance_accepted=False,
            analysis_source_sha256={
                relative: sha256_file(ROOT / relative) for relative in (
                    "scripts/run_causal_gpu_replay.py",
                    "beliefkv/experiments/causal_replay.py",
                    "beliefkv/experiments/causal_replay_backend.py",
                    "beliefkv/experiments/causal_replay_delivery.py",
                    "beliefkv/experiments/causal_replay_native.py",
                    "beliefkv/experiments/causal_replay_sampling.py",
                    "beliefkv/experiments/causal_replay_tokens.py",
                    "beliefkv/runtime/replay_token_capture.py",
                    "configs/causal_replay_plugin/pyproject.toml",
                    "beliefkv/experiments/agentx_replay.py",
                    "beliefkv/experiments/agentx_tokens.py",
                    "beliefkv/experiments/replay_timing.py",
                    "beliefkv/experiments/workflow_metrics.py",
                )
            },
            scope=(
                (
                    "Published AgentX lengths, fixed synthetic benchmark tokens and causal delivery. "
                    "Original content, original EOS and original application correctness are unavailable. "
                ) if agentx else
                "Exact native request work, stopping and causal delivery only. "
            ) + (
                "Server source, actual GPU execution, policy configuration and "
                "same-work comparison require a separately qualified operator."
            ),
        )
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-arm", type=Path)
    source.add_argument("--agentx-prepared", type=Path)
    parser.add_argument("--agentx-token-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--event-socket", type=Path)
    parser.add_argument("--request-timeout-s", type=float, default=1800.)
    parser.add_argument("--stream-response-metrics", action="store_true")
    args = parser.parse_args()
    if not 0 < args.request_timeout_s <= 3600.:
        parser.error("request timeout must be in (0, 3600] seconds")
    if (args.agentx_prepared is None) != (args.agentx_token_dir is None):
        parser.error("AgentX replay requires both its prepared plan and offline token bundle")
    if args.source_arm is not None:
        args.source_arm = args.source_arm.resolve()
    if args.agentx_prepared is not None:
        args.agentx_prepared = args.agentx_prepared.resolve()
        args.agentx_token_dir = args.agentx_token_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    if args.output_dir.exists():
        parser.error("preserve existing replay artifacts")
    try:
        result = asyncio.run(run(args))
    except BaseException as error:
        if args.output_dir.is_dir() and not isinstance(error, FileExistsError):
            failure_path = args.output_dir / "execution_failure.json"
            if not failure_path.exists():
                with failure_path.open("x") as stream:
                    json.dump({
                        "error_type": type(error).__name__, "error": str(error),
                        "performance_accepted": False, "gpu_replay_qualified": False,
                    }, stream, indent=2, allow_nan=False)
        raise
    with (args.output_dir / "summary.json").open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: result[key] for key in (
            "completed_requests", "exact_tokens_verified",
            "original_native_stopping_verified", "gpu_replay_qualified",
            "performance_accepted",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
