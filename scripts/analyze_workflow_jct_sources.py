#!/usr/bin/env python3
"""Describe paired workflow JCT sources without claiming causal attribution."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
from statistics import mean, median


def read_json(path: Path, inputs: list[dict]) -> dict:
    data = path.read_bytes()
    inputs.append({
        "path": str(path), "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    })
    return json.loads(data)


def records(path: Path, inputs: list[dict]):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for line in stream:
            digest.update(line)
            size += len(line)
            if not line.endswith(b"\n"):
                raise ValueError(f"Incomplete input line: {path}")
            if line.strip():
                yield json.loads(line)
    inputs.append({"path": str(path), "bytes": size, "sha256": digest.hexdigest()})


def first_services(path: Path, inputs: list[dict]) -> dict:
    result = {}
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for line in stream:
            digest.update(line)
            size += len(line)
            if b'"gpu_service_sample"' not in line or b'"prefill"' not in line:
                continue
            if not line.endswith(b"\n"):
                raise ValueError(f"Incomplete service record: {path}")
            row = json.loads(line)
            if row["phase"] != "prefill":
                continue
            launch = row["launch_monotonic_ms"]
            offset = row["service_start_ts_ms"] - row["service_start_monotonic_ms"]
            for request in row["request_samples"]:
                key = request["request_id"]
                if key not in result or launch < result[key]["first_service_mono_ms"]:
                    result[key] = {
                        "first_service_mono_ms": launch,
                        "service_clock_offset_ms": offset,
                    }
    inputs.append({"path": str(path), "bytes": size, "sha256": digest.hexdigest()})
    return result


def partition(begin: float, end: float, groups: dict[str, list]) -> dict[str, float]:
    """Count wall time once, using insertion order to resolve nested intervals."""
    changes = defaultdict(Counter)
    changes[begin]
    changes[end]
    for category, intervals in groups.items():
        for start, stop in intervals:
            start, stop = max(begin, start), min(end, stop)
            if stop > start:
                changes[start][category] += 1
                changes[stop][category] -= 1
    active = Counter()
    result = Counter()
    previous = begin
    for timestamp, delta in sorted(changes.items()):
        category = next((name for name in groups if active[name] > 0), "other")
        result[category] += (timestamp - previous) / 1000.
        active.update(delta)
        previous = timestamp
    assert abs(sum(result.values()) - (end - begin) / 1000.) < 1e-6
    return dict(result)


def analyze_workflow(path: Path, inputs: list[dict], server: dict) -> dict:
    events = list(records(path, inputs))
    roots = {
        row["invocation_id"] for row in events
        if row["kind"] == "invocation_create" and row["relation_type"] == "root"
    }
    assert len(roots) == 1, (path, roots)
    root = roots.pop()
    starts = [row["ts_ms"] for row in events if row["kind"] == "workflow_start"]
    ends = [row["ts_ms"] for row in events if row["kind"] == "workflow_end"]
    assert len(starts) == len(ends) == 1, path
    begin, end = starts[0], ends[0]
    llm, tools = defaultdict(list), defaultdict(list)
    llm_parts = defaultdict(lambda: defaultdict(list))
    requests, pending_llm, pending_tool = [], {}, {}
    joins, returns = {}, {}
    for row in events:
        kind, timestamp, invocation = row["kind"], row["ts_ms"], row["invocation_id"]
        attrs = row.get("attributes") or {}
        if kind == "llm_submit":
            key = attrs["request_id"]
            assert key not in pending_llm, (path, key)
            pending_llm[key] = (timestamp, invocation)
        elif kind == "llm_result":
            key = attrs["request_id"]
            start, owner = pending_llm.pop(key)
            assert owner == invocation and timestamp >= start, (path, key)
            llm[owner].append((start, timestamp))
            native = server.get(key, {})
            service = native.get("first_service_mono_ms")
            arrival = (
                native.get("queue_registered_ts_ms", 0.) - native["service_clock_offset_ms"]
                if "service_clock_offset_ms" in native else None
            )
            if service is not None and arrival is not None and start <= arrival <= service <= timestamp:
                for phase, lower, upper in (
                    ("ingress", start, arrival), ("queue", arrival, service),
                    ("after_first_service", service, timestamp),
                ):
                    llm_parts[owner][phase].append((lower, upper))
            else:
                llm_parts[owner]["unmeasured"].append((start, timestamp))
            requests.append({
                "request_id": key, "invocation": owner,
                "seconds": (timestamp - start) / 1000.,
                **server.get(key, {}),
            })
        elif kind == "tool_start":
            key = row.get("tool_call_id") or attrs["tool_call_id"]
            assert key not in pending_tool, (path, key)
            pending_tool[key] = (timestamp, invocation)
        elif kind == "tool_end":
            key = row.get("tool_call_id") or attrs["tool_call_id"]
            start, owner = pending_tool.pop(key)
            assert owner == invocation and timestamp >= start, (path, key)
            tools[owner].append((start, timestamp))
        elif kind == "join_create":
            joins[row["join_id"]] = {"members": row["member_invocation_ids"]}
        elif kind == "join_wait":
            joins[row["join_id"]].update(begin=timestamp, waiter=invocation)
        elif kind == "join_satisfied":
            joins[row["join_id"]]["end"] = timestamp
        elif kind == "return":
            returns[invocation] = timestamp
    assert not pending_llm and not pending_tool, path
    root_joins = [item for item in joins.values() if item.get("waiter") == root]
    assert all("end" in item and item["members"] for item in root_joins), path
    ordered_joins = sorted(root_joins, key=lambda item: item["begin"])
    assert all(
        left["end"] <= right["begin"] for left, right in zip(ordered_joins, ordered_joins[1:])
    ), path
    parts = partition(begin, end, {
        "root_join_wait": [(item["begin"], item["end"]) for item in root_joins],
        "root_llm": llm[root], "root_tool": tools[root],
    })
    parts["root_other"] = parts.pop("other", 0.)
    root_details = partition(begin, end, {
        "join_excluded": [(item["begin"], item["end"]) for item in root_joins],
        **{f"root_llm_{phase}": intervals for phase, intervals in llm_parts[root].items()},
    })
    parts.update({key: value for key, value in root_details.items() if key.startswith("root_llm_")})
    critical_parts = Counter()
    critical_invocations = set()
    for item in root_joins:
        assert all(member in returns for member in item["members"]), path
        child = max(item["members"], key=returns.__getitem__)
        assert returns[child] <= item["end"] + 1e-6, path
        critical_invocations.add(child)
        split = partition(item["begin"], item["end"], {
            "last_child_llm": llm[child], "last_child_tool": tools[child],
        })
        split["last_child_other"] = split.pop("other", 0.)
        critical_parts.update(split)
        details = partition(item["begin"], item["end"], {
            f"last_child_llm_{phase}": intervals for phase, intervals in llm_parts[child].items()
        })
        for key, value in details.items():
            if key.startswith("last_child_llm_"):
                parts[key] = parts.get(key, 0.) + value
    assert abs(sum(critical_parts.values()) - parts.get("root_join_wait", 0.)) < 1e-5
    parts.update(critical_parts)
    for role in ("root", "last_child"):
        assert abs(
            sum(parts.get(f"{role}_llm_{phase}", 0.) for phase in
                ("ingress", "queue", "after_first_service", "unmeasured"))
            - parts.get(f"{role}_llm", 0.)
        ) < 1e-5, path
    first_root_submit = min(start for start, _ in llm[root])
    result = {
        "trace_jct_seconds": (end - begin) / 1000.,
        "client_start_ts_ms": begin, "root_join_count": len(root_joins),
        "root_initialization_seconds": (first_root_submit - begin) / 1000.,
        "parts_seconds": parts,
        "all_agent_tool_sum_seconds": sum(
            (stop - start) / 1000. for intervals in tools.values() for start, stop in intervals
        ),
        "all_agent_llm_sum_seconds": sum(item["seconds"] for item in requests),
        "requests_without_server_output": sum("output_tokens" not in item for item in requests),
    }
    for name, selected in {
        "root": [item for item in requests if item["invocation"] == root],
        "last_children": [item for item in requests if item["invocation"] in critical_invocations],
        "all": requests,
    }.items():
        result[f"{name}_requests"] = len(selected)
        result[f"{name}_output_tokens"] = sum(item.get("output_tokens", 0) for item in selected)
        result[f"{name}_input_tokens"] = sum(item.get("prompt_tokens", 0) for item in selected)
    return result


def analyze_arm(run: Path, name: str, inputs: list[dict], *, with_service: bool) -> tuple[dict, dict]:
    arm = run / name
    client = arm / "client_156"
    summary = read_json(client / "summary.json", inputs)
    manifest = read_json(client / "manifest.json", inputs)
    server = defaultdict(dict)
    for row in records(arm / "server/runtime_events.sglang.jsonl", inputs):
        attrs = row.get("attributes") or {}
        key = attrs.get("request_id")
        if key is None:
            continue
        if row["kind"] == "llm_submit":
            server[key]["prompt_tokens"] = attrs.get("prompt_tokens") or 0
            server[key]["queue_registered_ts_ms"] = (
                attrs.get("native_ingress_timing_ms") or {}
            ).get("native_queue_registered_ts_ms") or row["ts_ms"]
        elif row["kind"] == "llm_result":
            server[key]["output_tokens"] = attrs.get("output_tokens") or 0
    if with_service:
        for key, sample in first_services(arm / "server/runtime_audit.jsonl", inputs).items():
            server[key].update(sample)
    workflows = {}
    wave_size = manifest["config"]["workflow_arrival_batch_size"]
    ordinals = {key: index for index, key in enumerate(manifest["instance_ids"])}
    for row in summary["workflows"]:
        assert row["outcome"] == "completed", (name, row["instance_id"], row["outcome"])
        key = row["instance_id"]
        analysis = analyze_workflow(
            client / "workflows" / key / "runtime_events.deepagents.jsonl", inputs, server,
        )
        workflows[key] = {
            "jct_seconds": row["duration_seconds"], "wave": ordinals[key] // wave_size + 1,
            "llm_requests": row["trace"]["llm_request_count"],
            "tool_calls": row["trace"]["tool_call_count"],
            "join_rounds": row["trace"]["delegation_round_count"],
            "summary_minus_trace_seconds": row["duration_seconds"] - analysis["trace_jct_seconds"],
            **analysis,
        }
    return workflows, {
        "code_commit": manifest.get("code_commit"), "config": manifest["config"],
        "collection_duration_seconds": summary["duration_seconds"],
        "workflows": len(workflows),
        "parts_mean_seconds": {
            part: mean(row["parts_seconds"].get(part, 0.) for row in workflows.values())
            for part in (
                "root_join_wait", "root_llm", "root_tool", "root_other",
                "last_child_llm", "last_child_tool", "last_child_other",
                *(f"{role}_llm_{phase}" for role in ("root", "last_child")
                  for phase in ("ingress", "queue", "after_first_service", "unmeasured")),
            )
        },
        "work_totals": {
            key: sum(row[key] for row in workflows.values())
            for key in (
                "root_requests", "root_output_tokens", "last_children_requests",
                "last_children_output_tokens", "all_requests", "all_output_tokens",
            )
        },
        "summary_minus_trace_mean_seconds": mean(
            row["summary_minus_trace_seconds"] for row in workflows.values()
        ),
        "requests_without_server_output": sum(
            row["requests_without_server_output"] for row in workflows.values()
        ),
    }


def cohort(rows: list[dict]) -> dict:
    return {
        "workflows": len(rows),
        "native_mean_jct_seconds": mean(row["native"]["jct_seconds"] for row in rows),
        "predictive_mean_jct_seconds": mean(row["predictive"]["jct_seconds"] for row in rows),
        "paired_saved_mean_seconds": mean(row["saved_seconds"] for row in rows),
        "paired_saved_median_seconds": median(row["saved_seconds"] for row in rows),
        "net_saved_workflow_seconds": sum(row["saved_seconds"] for row in rows),
        "predictive_faster": sum(row["saved_seconds"] > 0 for row in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--with-service", action="store_true")
    args = parser.parse_args()
    inputs = []
    data, arms = {}, {}
    for name in ("native", "predictive_h2d"):
        data[name], arms[name] = analyze_arm(
            args.run, name, inputs, with_service=args.with_service,
        )
    assert data["native"].keys() == data["predictive_h2d"].keys()
    trajectory = read_json(args.run / "workflow_trajectory_comparison.json", inputs)
    demands = {row["task"]: row for row in trajectory["workflows"]}
    rows = []
    for key, native in data["native"].items():
        predictive = data["predictive_h2d"][key]
        assert native["wave"] == predictive["wave"], key
        rows.append({
            "instance_id": key, "wave": native["wave"],
            "saved_seconds": native["jct_seconds"] - predictive["jct_seconds"],
            "native": native, "predictive": predictive,
            "native_server_output_tokens": demands[key]["native"]["output_tokens"],
            "predictive_server_output_tokens": demands[key]["predictive"]["output_tokens"],
        })
        for label, observed in (("native", native), ("predictive", predictive)):
            assert observed["all_output_tokens"] == demands[key][label]["output_tokens"], key
            assert abs(
                sum(observed["parts_seconds"].get(part, 0.) for part in
                    ("root_join_wait", "root_llm", "root_tool", "root_other"))
                + observed["summary_minus_trace_seconds"] - observed["jct_seconds"]
            ) < 1e-5, key
    by_saving = sorted(rows, key=lambda row: row["saved_seconds"], reverse=True)
    positive = sum(max(0., row["saved_seconds"]) for row in rows)
    net = sum(row["saved_seconds"] for row in rows)

    def brief(row: dict) -> dict:
        return {
            "instance_id": row["instance_id"], "wave": row["wave"],
            "saved_seconds": row["saved_seconds"],
            **{f"{arm}_{field}": row[arm][field]
               for arm in ("native", "predictive")
               for field in ("jct_seconds", "llm_requests", "tool_calls", "join_rounds")},
            "native_output_tokens": row["native_server_output_tokens"],
            "predictive_output_tokens": row["predictive_server_output_tokens"],
        }

    system = {}
    policy_path = args.run / "native_policy_comparison.json"
    if policy_path.exists():
        policy = read_json(policy_path, inputs)
        system["policy"] = {
            arm: {key: value[key] for key in (
                "milestones", "physical_transfer_times", "transfers", "h2d_sources",
                "full_eviction_recompute_tokens", "old_prefix_missing_proxy_ratio",
            )}
            for arm, value in policy["runs"].items()
        }
    reuse_path = args.run / "predictive_h2d/prefetch_lifecycle.json"
    if reuse_path.exists():
        reuse = read_json(reuse_path, inputs)["summary"]
        system["predictive_wait_events"] = {
            source: {key: value[key] for key in (
                "observed_events", "events_with_observed_full_plan",
                "events_with_early_reused_full", "early_started_and_reused_full_bytes",
                "remaining_native_full_host_hit_bytes_known",
            )}
            for source, value in reuse["wait_events"]["by_source"].items()
        }
        system["prepare"] = {key: reuse[key] for key in (
            "prepare_issued_commands", "prepare_acknowledged_commands",
            "prepare_commands_with_observed_restore",
        )}
    report = {
        "schema_version": 1, "created_at": datetime.now().astimezone().isoformat(),
        "scope": "Complete paired JCT, all tasks retained; descriptive partition, not causal savings.",
        "run": str(args.run), "summary": cohort(rows), "arms": arms,
        "waves": {str(wave): cohort([row for row in rows if row["wave"] == wave])
                  for wave in sorted({row["wave"] for row in rows})},
        "output_work_groups": {
            label: cohort(group) for label, group in (
                ("predictive_less_output", [row for row in rows if
                 row["predictive_server_output_tokens"] < row["native_server_output_tokens"]]),
                ("predictive_equal_or_more_output", [row for row in rows if
                 row["predictive_server_output_tokens"] >= row["native_server_output_tokens"]]),
            ) if group
        },
        "parts_saved_mean_seconds": {
            part: arms["native"]["parts_mean_seconds"][part]
            - arms["predictive_h2d"]["parts_mean_seconds"][part]
            for part in arms["native"]["parts_mean_seconds"]
        },
        "summary_minus_trace_saved_mean_seconds": (
            arms["native"]["summary_minus_trace_mean_seconds"]
            - arms["predictive_h2d"]["summary_minus_trace_mean_seconds"]
        ),
        "concentration": {
            "gross_saved_workflow_seconds": positive,
            "gross_added_workflow_seconds": positive - net,
            "top_10_saved_workflow_seconds": sum(row["saved_seconds"] for row in by_saving[:10]),
            "top_10_share_of_gross_saving": (
                sum(row["saved_seconds"] for row in by_saving[:10]) / positive
                if positive else None
            ),
            "top_10_share_of_net_saving": (
                sum(row["saved_seconds"] for row in by_saving[:10]) / net if net else None
            ),
        },
        "largest_improvements": [brief(row) for row in by_saving[:10]],
        "largest_regressions": [brief(row) for row in by_saving[-10:][::-1]],
        "system_evidence": system,
        "validation": {
            "all_tasks_retained": True,
            "all_terminal_requests_and_tools_paired": True,
            "all_workflow_time_partitions_reconcile": True,
            "all_request_output_counts_match_trajectory_report": True,
        },
        "semantics": [
            "Positive saved_seconds means native minus predictive; no divergent or slow task is removed.",
            "Root JOIN/LLM/tool/other intervals partition client workflow time exactly. "
            "JOIN has precedence over overlapping task-tool callbacks; root LLM has precedence over other tools.",
            "last_child_* decomposes root_join_wait; never add it again to the root partition. "
            "The last returning member is identified retrospectively, not an online feature or counterfactual.",
            "LLM spans are client submit through result: ingress, queue, GPU service, interleaving and delivery. "
            "They are not pure GPU service and do not isolate admission benefit.",
            "Optional LLM subpartitions use native queue registration and the first completed "
            "prefill's recorded run_batch entry, not CUDA launch. After-first-service time "
            "still includes interleaving, remaining prefill/decode and result delivery. "
            "Missing or non-ordered service anchors retain the entire request as unmeasured.",
            "Other contains client setup, callbacks, internal invocations and unclassified/nested waits. "
            "A difference is not isolated removable overhead.",
            "All-agent sums overlap across siblings and are not additive workflow JCT.",
            "Output-based groups are post-treatment descriptions, not trajectory-matched causal cohorts.",
            "Completed workflows are not independently graded successful solutions. "
            "Different realized work, shared-resource interference and different policy prevent isolated causal attribution.",
        ],
        "inputs": inputs, "workflows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    csv_path = args.output.with_suffix(".csv")
    flat = [brief(row) for row in rows]
    with csv_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(flat[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(flat)
    print(json.dumps({
        "output": str(args.output), "csv": str(csv_path), "summary": report["summary"],
        "waves": report["waves"], "parts_saved_mean_seconds": report["parts_saved_mean_seconds"],
        "concentration": report["concentration"],
    }, indent=2))


if __name__ == "__main__":
    main()
