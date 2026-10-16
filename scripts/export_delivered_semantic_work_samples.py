#!/usr/bin/env python3
"""Export recent work labels from sealed, actually delivered semantic inputs."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.replay_semantic_work_prefix import prefix_records

INTERVENTIONS = frozenset({
    "agent_graph_budget_finalization", "agent_guard_finalization_attempt",
    "agent_protocol_repair_attempt",
})


def read_json(path: Path) -> dict:
    payload = path.read_bytes()
    return json.loads(gzip.decompress(payload) if path.suffix == ".gz" else payload)


def normalized_seals(report: dict) -> dict:
    sources = next((
        report[name] for name in (
            "verified_input_prefixes", "source_prefix_seals", "input_jsonl_prefixes",
        ) if name in report
    ), None)
    if sources is None:
        raise ValueError("a sealed source report is required")
    result = {}
    for name, seal in sources.items():
        path = Path(name)
        if not path.is_absolute():
            raise ValueError("source seals require absolute paths")
        size = seal.get("read_complete_prefix_bytes", seal.get("complete_prefix_bytes"))
        digest = seal.get("sha256", seal.get("complete_prefix_sha256"))
        if type(size) is not int or size < 0 or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("invalid source prefix seal")
        resolved = str(path.resolve())
        normalized = {"read_complete_prefix_bytes": size, "sha256": digest}
        if resolved in result and result[resolved] != normalized:
            raise ValueError("conflicting source prefix seals")
        result[resolved] = normalized
    return result


def reconstruct(observation: dict, text: dict, native: dict, return_ts_ms: float,
                clock_offset_ms: float, *, run: Path, task: str) -> dict | None:
    delivery = observation["ts_ms"]
    if (
        text["ts_ms"] > delivery - clock_offset_ms
        or delivery >= native["ts_ms"]
        or text.get("tool_chunk") or text.get("finish_reason")
        or not text.get("semantic_content_tail")
    ):
        return None
    if any(text[name] != observation[name] for name in (
        "request_id", "context_id", "context_epoch", "content_chars",
    )):
        raise ValueError("delivered content identity differs from forecast")
    remaining = native["attributes"]["output_tokens"] - observation["observed_output_tokens"]
    if remaining <= 0 or return_ts_ms + clock_offset_ms <= delivery:
        return None
    per_second = observation.get("sampled_tokens_per_second")
    return {
        "run": str(run), "task": task, "project": task.split("__", 1)[0],
        "observation": {
            **{name: observation[name] for name in (
                "request_id", "context_id", "context_epoch", "content_chars",
                "observed_output_tokens", "notice_active", "estimated_report_tokens",
                "prior_tool_calls", "prior_model_rounds",
            )},
            "invocation_id": text["invocation_id"], "ts_ms": text["ts_ms"],
            "content_tail": text["semantic_content_tail"][-1024:],
        },
        "remaining_tokens": remaining,
        "remaining_client_wall_ms": return_ts_ms + clock_offset_ms - delivery,
        "observed_decode_ms_per_token": 1000. / per_second if per_second else None,
        "online_forecast": observation,
        "lead_to_native_eos_ms_label": native["ts_ms"] - delivery,
        "native_output_tokens_label": native["attributes"]["output_tokens"],
    }


def collect(sealed: Path, arm: Path) -> tuple[list[dict], dict, dict, list[str]]:
    seals = normalized_seals(read_json(sealed))
    verified = {}

    def records(path):
        path = path.resolve()
        if str(path) not in seals:
            raise ValueError(f"required input has no original prefix seal: {path}")
        yield from prefix_records(path, seals)
        verified[str(path)] = seals[str(path)]

    forecasts, offset = [], None
    for row in records(arm / "opportunities/admission_opportunities.jsonl"):
        if row["event"] == "semantic_child_forecast":
            forecasts.append(row)
        elif row["event"] == "safe_point_census" and offset is None:
            offset = row["ts_ms"] - row["monotonic_ms"]
    if offset is None:
        raise ValueError("missing wall/monotonic clock alignment")
    native = {}
    for row in records(arm / "server/runtime_events.sglang.jsonl"):
        attrs = row.get("attributes") or {}
        if row["kind"] == "llm_result" and type(attrs.get("output_tokens")) is int:
            rid = attrs["request_id"]
            if rid in native:
                raise ValueError("duplicate native completion for one request")
            native[rid] = row
    event_paths = sorted(
        Path(name) for name in seals
        if name.endswith("/runtime_events.deepagents.jsonl")
        and Path(name).is_relative_to(arm)
    )
    returns, request_tasks, excluded = {}, {}, set()
    contents = {}
    wanted = {(row["request_id"], row["content_chars"]) for row in forecasts}
    for path in event_paths:
        task = path.parent.name
        last, tools = {}, set()
        for row in records(path):
            attrs = row.get("attributes") or {}
            rid, invocation = attrs.get("request_id"), row.get("invocation_id")
            if row["kind"] == "llm_result" and rid and not attrs.get("runtime_internal"):
                last[invocation] = rid
                request_tasks[rid] = task
                if attrs.get("tool_call_count", 0):
                    tools.add(rid)
            elif (
                row["kind"] == "return" and attrs.get("source") == "deepagents_task"
                and attrs.get("outcome") == "completed" and invocation in last
            ):
                rid = last[invocation]
                if rid not in tools:
                    returns[rid] = row["ts_ms"]
        # Intervention checks are separately sealed at this read, never to live EOF.
        audit = path.parent / "sandbox_audit.jsonl"
        if not audit.is_file():
            excluded.add(task)
        else:
            audit_name = str(audit.resolve())
            for row in prefix_records(audit, seals):
                if row.get("event") in INTERVENTIONS:
                    excluded.add(task)
            verified[audit_name] = seals[audit_name]
        content = path.parent / "child_stream_content.jsonl"
        if str(content.resolve()) not in seals:
            continue
        for row in records(content):
            key = (row.get("request_id"), row.get("content_chars"))
            if (
                key in wanted and row.get("event") == "child_stream_content"
                and not row.get("tool_chunk") and not row.get("finish_reason")
                and row.get("semantic_content_tail")
            ):
                if key in contents and contents[key] != row:
                    raise ValueError("ambiguous delivered content identity")
                contents[key] = row
    samples, omitted, seen = [], Counter(), set()
    for forecast in forecasts:
        rid = forecast["request_id"]
        if rid not in returns or rid not in native:
            omitted["natural_return_or_native_result_unobserved"] += 1
            continue
        task = request_tasks[rid]
        if task in excluded:
            omitted["intervened_or_missing_intervention_audit"] += 1
            continue
        text = contents.get((rid, forecast["content_chars"]))
        if text is None:
            omitted["original_delivered_text_unobserved"] += 1
            continue
        sample = reconstruct(
            forecast, text, native[rid], returns[rid], offset, run=arm, task=task,
        )
        if sample is None:
            omitted["noncausal_or_nonpositive_remaining"] += 1
            continue
        identity = (rid, sample["observation"]["ts_ms"])
        if identity in seen:
            omitted["repeated_delivery_of_same_observation"] += 1
            continue
        seen.add(identity)
        samples.append(sample)
    coverage = {
        "snapshot_policy": "sealed_delivered_online_forecasts",
        "forecast_count": len(forecasts), "sample_count": len(samples),
        "natural_request_count": len({row["observation"]["request_id"] for row in samples}),
        "project_snapshot_counts": dict(Counter(row["project"] for row in samples)),
        "notice_active_snapshot_count": sum(row["observation"]["notice_active"] for row in samples),
        "omitted_snapshot_counts": dict(omitted),
    }
    return samples, coverage, verified, sorted(excluded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sealed", type=Path, required=True)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    arm, source_root = args.arm.resolve(), args.source_root.resolve()
    run = str(arm.relative_to(source_root))
    plan = read_json(args.plan)
    if plan["training_runs"] + plan["calibration_evaluation_runs"] != [run]:
        raise ValueError("export source differs from frozen work plan")
    samples, coverage, verified, excluded = collect(args.sealed, arm)
    if plan["snapshot_policy"] != coverage["snapshot_policy"]:
        raise ValueError("sample policy differs from frozen work plan")
    args.output.mkdir(parents=True)
    sample_path = args.output / "samples.json.gz"
    sample_path.write_bytes(gzip.compress(
        json.dumps({"rows": samples, "coverage": coverage}, allow_nan=False).encode(), mtime=0,
    ))
    report = {
        "plan": plan, "excluded_intervened_tasks_by_run": {run: excluded},
        "coverage": coverage, "verified_source_prefixes": verified,
        "original_sealed_source": str(args.sealed.resolve()),
        "original_sealed_source_sha256": hashlib.sha256(args.sealed.read_bytes()).hexdigest(),
        "exporter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": (
            "Development work-only data, not independent phase validation or H2D benefit. "
            "Features are actual delivered inputs; future native EOS and natural RETURN "
            "are labels only. Log prefixes are independently sealed, not atomic. "
            "Intervention audit prefixes are additionally sealed at export time. "
            "No runtime or model artifact is modified."
        ),
    }
    report_path = args.output / "source_report.json"
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    manifest = {
        "source_root": str(source_root), "source_report": str(report_path.resolve()),
        "source_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "samples": [{
            "run": run, "path": str(sample_path.resolve()),
            "sha256": hashlib.sha256(sample_path.read_bytes()).hexdigest(),
        }],
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "coverage": coverage}), flush=True)


if __name__ == "__main__":
    main()
