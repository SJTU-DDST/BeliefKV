#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any, Iterable


def _records(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _first(
    rows: Iterable[dict[str, Any]],
    *,
    kind: str,
    predicate: Any = None,
) -> dict[str, Any] | None:
    for row in rows:
        if row.get("kind") != kind:
            continue
        if predicate is None or predicate(row):
            return row
    return None


def _analyze_workflow(
    workflow_dir: Path,
    physical_start_by_request: dict[str, dict[str, Any]],
    *,
    minimum_prefix_reuse: float,
    require_controlled_stop: bool,
) -> dict[str, Any]:
    events = _records(workflow_dir / "runtime_events.deepagents.jsonl")
    events.sort(key=lambda item: (int(item.get("sequence", 0)), float(item["ts_ms"])))
    result_path = workflow_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    trajectory_path = workflow_dir / "trajectory.json"
    trajectory = (
        json.loads(trajectory_path.read_text(encoding="utf-8"))
        if trajectory_path.exists()
        else []
    )

    root = _first(
        events,
        kind="invocation_create",
        predicate=lambda item: item.get("relation_type") == "root",
    )
    native_joins = [
        item
        for item in events
        if item.get("kind") == "join_create"
        and (item.get("attributes") or {}).get("source") == "deepagents_task"
    ]
    join = native_joins[0] if native_joins else None
    join_id = str(join.get("join_id")) if join is not None else None
    join_wait = _first(
        events,
        kind="join_wait",
        predicate=lambda item: item.get("join_id") == join_id,
    )
    join_satisfied = _first(
        events,
        kind="join_satisfied",
        predicate=lambda item: item.get("join_id") == join_id,
    )
    parent_id = str(join_wait.get("invocation_id")) if join_wait is not None else None
    parent_context = str(root.get("context_id")) if root is not None else None
    members = tuple(str(item) for item in (join or {}).get("member_invocation_ids", ()))
    child_creates = {
        str(item.get("invocation_id")): item
        for item in events
        if item.get("kind") == "invocation_create"
        and str(item.get("invocation_id")) in members
    }
    child_spawns = {
        str(item.get("target_invocation_id")): item
        for item in events
        if item.get("kind") == "spawn"
        and str(item.get("target_invocation_id")) in members
        and (item.get("attributes") or {}).get("source") == "deepagents_task"
    }
    expected_task_call_ids = {
        str((item.get("attributes") or {}).get("tool_call_id"))
        for item in child_spawns.values()
        if (item.get("attributes") or {}).get("tool_call_id") is not None
    }
    returned_task_call_ids = {
        str(item.get("tool_call_id"))
        for item in trajectory
        if isinstance(item, dict)
        and item.get("message_type") == "tool"
        and item.get("tool_call_id") is not None
    }
    matched_task_report_ids = expected_task_call_ids & returned_task_call_ids

    parent_submits = [
        item
        for item in events
        if item.get("kind") == "llm_submit"
        and item.get("invocation_id") == parent_id
        and not bool((item.get("attributes") or {}).get("runtime_internal"))
    ]
    pre_submit = None
    post_submit = None
    if join_wait is not None:
        before = [
            item
            for item in parent_submits
            if int(item.get("sequence", 0)) < int(join_wait.get("sequence", 0))
        ]
        pre_submit = before[-1] if before else None
    if join_satisfied is not None:
        post_submit = next(
            (
                item
                for item in parent_submits
                if int(item.get("sequence", 0))
                > int(join_satisfied.get("sequence", 0))
            ),
            None,
        )

    def physical(submit: dict[str, Any] | None) -> dict[str, Any] | None:
        if submit is None:
            return None
        request_id = str((submit.get("attributes") or {}).get("request_id") or "")
        return physical_start_by_request.get(request_id)

    pre_physical = physical(pre_submit)
    post_physical = physical(post_submit)
    pre_prompt_tokens = int((pre_physical or {}).get("prompt_tokens") or 0)
    post_prompt_tokens = int((post_physical or {}).get("prompt_tokens") or 0)
    post_hit_tokens = int((post_physical or {}).get("cache_hit_tokens") or 0)
    retained_parent_prefix_ratio = (
        min(1.0, post_hit_tokens / pre_prompt_tokens) if pre_prompt_tokens else 0.0
    )
    total_post_prompt_hit_ratio = (
        post_hit_tokens / post_prompt_tokens if post_prompt_tokens else 0.0
    )

    pre_epoch = pre_submit.get("context_epoch") if pre_submit is not None else None
    post_epoch = post_submit.get("context_epoch") if post_submit is not None else None
    child_contexts = [
        str(item.get("context_id"))
        for item in child_creates.values()
        if item.get("context_id") is not None
    ]
    direct_parked_samples = []
    if join_wait is not None and join_satisfied is not None and parent_context is not None:
        for row in physical_start_by_request.values():
            if (
                row.get("context_id") == parent_context
                and float(join_wait["ts_ms"]) <= float(row.get("ts_ms", -1.0))
                <= float(join_satisfied["ts_ms"])
                and int(row.get("gpu_bytes") or 0) > 0
            ):
                direct_parked_samples.append(row)

    checks = {
        "one_native_join_observed": len(native_joins) >= 1,
        "native_child_count_2to3": 2 <= len(members) <= 3,
        "all_members_created": len(child_creates) == len(members) and bool(members),
        "all_members_spawned_with_task_call_ids": len(child_spawns) == len(members)
        and len(expected_task_call_ids) == len(members),
        "all_child_reports_appended_as_tool_messages": bool(members)
        and len(matched_task_report_ids) == len(members),
        "children_are_fresh": bool(members)
        and all(item.get("context_mode") == "fresh" for item in child_creates.values()),
        "child_contexts_are_distinct": len(child_contexts) == len(set(child_contexts))
        == len(members)
        and parent_context not in child_contexts,
        "join_wait_and_satisfied": join_wait is not None and join_satisfied is not None,
        "same_parent_context_continues": pre_submit is not None
        and post_submit is not None
        and pre_submit.get("context_id") == parent_context
        and post_submit.get("context_id") == parent_context,
        "parent_epoch_is_contiguous": isinstance(pre_epoch, int)
        and isinstance(post_epoch, int)
        and post_epoch == pre_epoch + 1,
        "post_join_request_has_physical_start": post_physical is not None,
        "child_reports_increase_parent_prompt": post_prompt_tokens
        > pre_prompt_tokens,
        "parent_prefix_reuse_at_least_threshold": (
            retained_parent_prefix_ratio >= minimum_prefix_reuse
        ),
        "parent_kv_has_future_physical_value": post_hit_tokens > 0,
        "controlled_stop_observed": (
            bool(result.get("semantic_gate_controlled_stop"))
            if require_controlled_stop
            else True
        ),
    }
    return {
        "instance_id": workflow_dir.name,
        "passed": all(checks.values()),
        "checks": checks,
        "join_id": join_id,
        "parent_invocation_id": parent_id,
        "parent_context_id": parent_context,
        "child_invocation_ids": list(members),
        "child_context_ids": child_contexts,
        "expected_task_call_ids": sorted(expected_task_call_ids),
        "returned_task_call_ids": sorted(returned_task_call_ids),
        "matched_task_report_count": len(matched_task_report_ids),
        "pre_join_parent_epoch": pre_epoch,
        "post_join_parent_epoch": post_epoch,
        "pre_join_parent_prompt_tokens": pre_prompt_tokens,
        "post_join_parent_prompt_tokens": post_prompt_tokens,
        "post_join_cache_hit_tokens": post_hit_tokens,
        "retained_parent_prefix_ratio": retained_parent_prefix_ratio,
        "total_post_prompt_hit_ratio": total_post_prompt_hit_ratio,
        "parked_gpu_residency_direct_sample_count": len(direct_parked_samples),
        "controlled_stop": bool(result.get("semantic_gate_controlled_stop")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate native subagent parent continuation and physical prefix reuse."
    )
    parser.add_argument("--workloads-dir", type=Path, required=True)
    parser.add_argument("--server-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-prefix-reuse", type=float, default=0.90)
    parser.add_argument(
        "--require-controlled-stop",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    args = parser.parse_args()
    if not 0.0 <= args.minimum_prefix_reuse <= 1.0:
        parser.error("--minimum-prefix-reuse must be between zero and one")

    audit = _records(args.server_audit)
    physical_start_by_request = {
        str(item.get("request_id")): item
        for item in audit
        if item.get("event") == "request_physical_start"
        and item.get("request_id") is not None
    }
    workflow_dirs = sorted(
        path.parent
        for path in args.workloads_dir.glob("workflows/*/runtime_events.deepagents.jsonl")
    )
    if not workflow_dirs:
        raise FileNotFoundError("no Deep Agents workflow traces found")
    rows = [
        _analyze_workflow(
            path,
            physical_start_by_request,
            minimum_prefix_reuse=args.minimum_prefix_reuse,
            require_controlled_stop=args.require_controlled_stop,
        )
        for path in workflow_dirs
    ]
    report = {
        "schema_version": 1,
        "evidence_role": "native_subagent_semantic_gate_not_performance",
        "workload_count": len(rows),
        "passed_workloads": sum(bool(item["passed"]) for item in rows),
        "minimum_prefix_reuse": args.minimum_prefix_reuse,
        "require_controlled_stop": args.require_controlled_stop,
        "passed": all(bool(item["passed"]) for item in rows),
        "workflows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
