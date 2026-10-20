#!/usr/bin/env python3
"""Measure agent work without changing execution or inferring cache correctness."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics

from scripts.audit_native_system_costs import finite_number


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0}

    def percentile(q):
        position = (len(values) - 1) * q
        lo = int(position)
        hi = min(lo + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (position - lo)

    return {
        "count": len(values), "sum": sum(values),
        "p50": statistics.median(values), "p90": percentile(.9),
        "p99": percentile(.99), "max": values[-1],
    }


def read_source(path, bindings, *, jsonl=False):
    # Bound a live file to its observed size and retain only complete JSONL rows.
    size = path.stat().st_size
    with path.open("rb") as stream:
        data = stream.read(size)
    if jsonl:
        data = data[:data.rfind(b"\n") + 1]
    bindings.append({
        "path": str(path.resolve()), "observed_size_bytes": size,
        "analyzed_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    })
    if jsonl:
        return [json.loads(line) for line in data.splitlines() if line.strip()]
    return json.loads(data)


def tool_pairs(events):
    starts, rows = {}, []
    for event in sorted(events, key=lambda row: (row["ts_ms"], row["sequence"])):
        kind, attrs = event["kind"], event.get("attributes") or {}
        if kind not in ("tool_start", "tool_end"):
            continue
        key = event.get("invocation_id"), attrs.get("tool_call_id")
        if key[1] is None:
            continue
        if kind == "tool_start":
            if key in starts:
                raise ValueError(f"duplicate tool start: {key}")
            starts[key] = event
        elif key in starts:
            start = starts.pop(key)
            before = start.get("attributes") or {}
            if event["ts_ms"] < start["ts_ms"]:
                raise ValueError("negative tool interval")
            rows.append({
                "workflow": event["workflow_id"], "invocation": key[0],
                "call_id": key[1], "tool": before.get("tool_name"),
                "input_sha256": before.get("input_sha256"),
                "output_sha256": attrs.get("output_sha256"),
                "output_chars": attrs.get("output_chars"),
                "status": attrs.get("status"),
                "shape": before.get("observed_command_shape"),
                "start_ms": start["ts_ms"], "end_ms": event["ts_ms"],
                "duration_ms": event["ts_ms"] - start["ts_ms"],
            })
    return rows, len(starts)


def repeated_results(rows, *, require_equal_output=True):
    # Equality uses a future result and is an opportunity audit, not an online hit.
    by_key = defaultdict(list)
    within, across = [], []
    for row in sorted(rows, key=lambda item: item["start_ms"]):
        if not row["input_sha256"] or (
            require_equal_output and not row["output_sha256"]
        ):
            continue
        key = (
            row["workflow"], row["tool"], row["input_sha256"],
        )
        if require_equal_output:
            key += (row["output_sha256"], row["status"])
        earlier = [
            old for old in by_key[key] if old["end_ms"] <= row["start_ms"]
        ]
        if any(old["invocation"] == row["invocation"] for old in earlier):
            within.append(row)
        if any(old["invocation"] != row["invocation"] for old in earlier):
            across.append(row)
        by_key[key].append(row)
    return within, across


def root_evidence(messages, pairs):
    by_call = {row["call_id"]: row for row in pairs}
    call_requests = {}
    for message in messages:
        if message.get("type") != "ai":
            continue
        identity = str(message.get("id") or "")
        request = (
            "beliefkv:" + identity[len("lc_run--"):]
            if identity.startswith("lc_run--") else None
        )
        for call in message.get("tool_calls") or []:
            call_requests[call.get("id")] = request
    eligible, repeats, missing, seen = [], [], 0, {}
    for message in messages:
        if message.get("type") != "tool":
            continue
        attrs = message.get("additional_kwargs") or {}
        row = by_call.get(message.get("tool_call_id"))
        if row is None:
            missing += 1
            continue
        before = attrs.get("beliefkv_workspace_epoch_before")
        after = attrs.get("beliefkv_workspace_epoch_after")
        signature = attrs.get("beliefkv_tool_signature")
        output = attrs.get("beliefkv_source_result_sha256")
        if (
            attrs.get("beliefkv_physical_execution") is not True
            or before is None or before != after or not signature or not output
            or attrs.get("beliefkv_workspace_changed") is not False
        ):
            continue
        key = (row["tool"], signature, before, output)
        enriched = {
            **row, "observed_workspace_epoch": before,
            "request_id": call_requests.get(row["call_id"]),
        }
        eligible.append(enriched)
        if key in seen and seen[key] <= row["start_ms"]:
            repeats.append(enriched)
        seen[key] = min(seen.get(key, row["end_ms"]), row["end_ms"])
    return {
        "eligible": eligible, "repeated": repeats,
        "tool_messages_without_completed_event": missing,
    }


def token_totals(rows, native_results):
    known = [
        native_results[row["request_id"]]
        for row in rows if row["request_id"] in native_results
    ]
    return {
        "requests": len(rows), "matched_native_results": len(known),
        "output_tokens": sum(known),
        "note": "Output tokens include any reasoning; no token category is inferred.",
    }


def prompt_repetitions(requests, native_submits=None, native_results=None):
    native_submits, native_results = native_submits or {}, native_results or {}
    by_digest, missing = defaultdict(list), 0
    for row in requests.values():
        if not row.get("prompt_sha256"):
            missing += 1
            continue
        by_digest[row["prompt_sha256"]].append(row)
    repeated = [rows for rows in by_digest.values() if len(rows) > 1]
    overlap_pairs, unclosed, length_mismatch, length_unknown, empty_first = 0, 0, 0, 0, 0
    for rows in repeated:
        ordered = sorted(rows, key=lambda row: row["submit_ms"])
        lengths = [
            native_submits.get(row["request_id"], {}).get("prompt_tokens")
            for row in rows
        ]
        known = [value for value in lengths if type(value) is int and value >= 0]
        length_unknown += len(known) != len(lengths)
        length_mismatch += len(set(known)) > 1
        first = ordered[0]
        empty_first += (
            first.get("finish_reason") in ("stop", "length")
            and first.get("output_chars") == 0 and first.get("tool_calls") == 0
        )
        for index, row in enumerate(ordered):
            if "span_ms" not in row:
                unclosed += 1
                continue
            end = row["submit_ms"] + row["span_ms"]
            overlap_pairs += sum(
                other["submit_ms"] < end for other in ordered[index + 1:]
            )
    return {
        "requests": len(requests), "requests_without_digest": missing,
        "distinct_semantic_digests": len(by_digest),
        "repeated_digest_groups": len(repeated),
        "requests_in_repeated_groups": sum(map(len, repeated)),
        "additional_requests_after_first": sum(len(rows) - 1 for rows in repeated),
        "repeated_groups_crossing_invocations": sum(
            len({(row["workflow"], row.get("invocation")) for row in rows}) > 1
            for rows in repeated
        ),
        "repeated_groups_crossing_workflows": sum(
            len({row["workflow"] for row in rows}) > 1 for rows in repeated
        ),
        "overlapping_client_span_pairs": overlap_pairs,
        "unclosed_requests_in_repeated_groups": unclosed,
        "groups_with_different_native_input_lengths": length_mismatch,
        "groups_without_complete_native_input_lengths": length_unknown,
        "groups_with_callback_empty_first_result": empty_first,
        "groups": [{
            "digest": rows[0]["prompt_sha256"],
            "requests": [{
                **{
                    key: row.get(key)
                    for key in (
                        "request_id", "workflow", "invocation", "role",
                        "submit_ms", "span_ms", "finish_reason", "output_chars",
                        "tool_calls",
                    )
                },
                "native_input_tokens": native_submits.get(
                    row["request_id"], {}
                ).get("prompt_tokens"),
                "native_output_tokens": native_results.get(row["request_id"]),
            } for row in rows],
        } for rows in repeated],
        "scope": (
            "Callback semantic-message/model-contract digests, not native token "
            "arrays or a proof of equal physical inference state. Different "
            "digests may render identically; equal digests still require engine "
            "configuration/token checks. Overlapping client spans include queue "
            "and delivery delay, not proven concurrent GPU work. Repeated inputs "
            "do not authorize sharing one sampled response among independent "
            "samples; only equal-state forward could be shared before each "
            "request samples independently."
        ),
    }


def dependency_topology(events):
    """Count declared consumers without inferring shared prompt or tensor values."""
    creates, joins, spawn_edges = {}, {}, set()
    for event in events:
        workflow = event["workflow_id"]
        invocation = event.get("invocation_id")
        kind = event["kind"]
        if kind == "invocation_create":
            key = workflow, invocation
            if not invocation or key in creates:
                raise ValueError(f"missing or duplicate invocation identity: {key}")
            creates[key] = event
        elif kind == "spawn":
            target = event.get("target_invocation_id")
            if invocation and target:
                spawn_edges.add((workflow, invocation, target))
        if kind not in ("join_create", "join_wait", "join_satisfied"):
            continue
        identity = event.get("join_id")
        if not identity:
            raise ValueError("join event without identity")
        group = joins.setdefault((workflow, identity), {
            "members": None, "waiters": set(), "satisfied": False,
        })
        if kind == "join_create":
            if group["members"] is not None:
                raise ValueError(f"duplicate join creation: {(workflow, identity)}")
            group["members"] = frozenset(event.get("member_invocation_ids") or [])
        elif kind == "join_wait" and invocation:
            group["waiters"].add(invocation)
        elif kind == "join_satisfied":
            group["satisfied"] = True

    depths = {}

    def depth(key, visiting):
        if key in depths:
            return depths[key]
        create = creates.get(key)
        if create is None:
            return None
        if key in visiting:
            raise ValueError(f"cycle in invocation parent chain: {key}")
        parent = create.get("parent_invocation_id")
        if parent is None:
            result = 0 if create.get("relation_type") == "root" else None
        else:
            before = depth((key[0], parent), visiting | {key})
            result = (
                before + int(create.get("relation_type") == "spawn")
                if before is not None else None
            )
        depths[key] = result
        return result

    counts, child_depths = Counter(), Counter()
    workflows = {event["workflow_id"] for event in events}
    by_workflow = {workflow: Counter() for workflow in workflows}
    for key, create in creates.items():
        attrs = create.get("attributes") or {}
        if attrs.get("runtime_internal"):
            counts["runtime_internal_invocations"] += 1
        elif create.get("relation_type") == "spawn":
            counts["dynamic_subagents"] += 1
            by_workflow[key[0]]["dynamic_subagents"] += 1
            value = depth(key, set())
            child_depths["unknown" if value is None else str(value)] += 1
            if value is not None and value > 1:
                counts["nested_dynamic_subagents"] += 1
    counts["observed_spawn_edges"] = len(spawn_edges)
    counts["spawn_edges_without_target_creation"] = sum(
        (workflow, target) not in creates
        for workflow, _, target in spawn_edges
    )
    member_sets = defaultdict(list)
    for (workflow, _), group in joins.items():
        members, waiters = group["members"], group["waiters"]
        counts["join_groups"] += 1
        counts["satisfied_join_groups"] += int(group["satisfied"])
        by_workflow[workflow]["join_groups"] += 1
        if members is None:
            counts["join_groups_without_observed_creation"] += 1
        elif not members:
            counts["join_groups_without_observed_members"] += 1
        else:
            member_sets[(workflow, members)].append(group)
        if not waiters:
            counts["join_groups_without_observed_waiter"] += 1
        elif len(waiters) == 1:
            counts["join_groups_with_one_declared_waiter"] += 1
        else:
            counts["join_groups_with_multiple_declared_waiters"] += 1
    counts["nonempty_producer_sets"] = len(member_sets)
    counts["producer_sets_with_multiple_declared_consumers"] = sum(
        len(set().union(*(group["waiters"] for group in groups))) > 1
        for groups in member_sets.values()
    )
    return {
        "counts": dict(counts),
        "dynamic_child_depths": dict(child_depths),
        "join_groups_per_workflow": dict(Counter(
            str(row["join_groups"]) for row in by_workflow.values()
        )),
        "semantics": (
            "Declared invocation/SPAWN/JOIN identities, scoped by workflow. "
            "Internal summaries are separate from dynamic subagents. Missing "
            "creation, parent or waiter evidence stays unknown. Multiple consumers "
            "of a producer set describe dependency topology only: they do not prove "
            "identical rendered tokens, equal KV/Mamba values, compatible batch "
            "geometry, or an opportunity beyond native exact-prefix sharing. "
            "A single JOIN waiter does not exclude repeated tool-result text or "
            "shared prefixes elsewhere in the application."
        ),
    }


def prefill_service(path, requests, after_compaction, bindings):
    if not path.exists():
        return {"status": "source_absent"}
    size = path.stat().st_size
    digest, analyzed_bytes = hashlib.sha256(), 0
    tokens, batches, unproven = Counter(), Counter(), Counter()
    stage_tokens, stage_batches = Counter(), Counter()
    phase_events, invalid_timing, replay = Counter(), Counter(), Counter()
    phase_durations, role_durations, stage_durations = (
        defaultdict(list), defaultdict(list), defaultdict(list)
    )
    after_compaction = set(after_compaction)
    seen = set()
    with path.open("rb") as stream:
        while stream.tell() < size:
            line = stream.readline(size - stream.tell())
            if not line.endswith(b"\n"):
                break
            digest.update(line)
            analyzed_bytes += len(line)
            if b'"gpu_service_sample"' not in line:
                continue
            event = json.loads(line)
            if event.get("event") != "gpu_service_sample":
                continue
            identity = event["sample_id"]
            if identity in seen:
                raise ValueError(f"duplicate service sample: {identity}")
            seen.add(identity)
            phase = str(event.get("phase") or "unknown")
            phase_events[phase] += 1
            elapsed = event.get("service_elapsed_ms")
            valid_timing = finite_number(elapsed) and elapsed >= 0
            if valid_timing:
                phase_durations[phase].append(elapsed)
            else:
                invalid_timing[phase] += 1
            replay[(phase, str(event.get("cuda_graph_replayed")))] += 1
            if phase != "prefill":
                continue
            roles, stages = set(), set()
            for sample in event["request_samples"]:
                request = sample.get("request_id")
                role = requests.get(request, {}).get("role", "unknown")
                roles.add(role)
                stage = (
                    "unknown" if role == "unknown" else
                    "summary" if role == "summary" else
                    "post_compaction" if request in after_compaction else
                    "ordinary"
                )
                stages.add(stage)
                delta = sample.get("token_delta")
                if (
                    sample.get("token_delta_semantics") != "prefill_extend_input_len"
                    or not isinstance(delta, int) or delta < 0
                ):
                    unproven[role] += 1
                    continue
                tokens[role] += delta
                stage_tokens[stage] += delta
                if request in after_compaction:
                    tokens["next_ordinary_after_compaction"] += delta
            role_set = "+".join(sorted(roles)) or "unknown"
            stage_set = "+".join(sorted(stages)) or "unknown"
            batches[role_set] += 1
            stage_batches[stage_set] += 1
            if valid_timing:
                role_durations[role_set].append(elapsed)
                stage_durations[stage_set].append(elapsed)
    bindings.append({
        "path": str(path.resolve()), "observed_size_bytes": size,
        "analyzed_bytes": analyzed_bytes, "sha256": digest.hexdigest(),
    })
    return {
        "status": "observed_complete_line_prefix",
        "prefill_batches": phase_events["prefill"],
        "batches_by_role_set": dict(batches),
        "extend_tokens_by_role": dict(tokens),
        "samples_without_proven_extend_geometry": dict(unproven),
        "worker_phase_intervals_ms": {
            phase: {
                **distribution(phase_durations[phase]),
                "observed_batches": count,
                "batches_without_valid_timing": invalid_timing[phase],
            } for phase, count in sorted(phase_events.items())
        },
        "prefill_batch_role_set_worker_intervals_ms": {
            role_set: distribution(values)
            for role_set, values in sorted(role_durations.items())
        },
        "compaction_stage_service": {
            "extend_tokens_by_stage": dict(stage_tokens),
            "batches_by_stage_set": dict(stage_batches),
            "worker_intervals_ms_by_stage_set": {
                stage_set: distribution(values)
                for stage_set, values in sorted(stage_durations.items())
            },
            "scope": (
                "Post-compaction identifies the first ordinary request after an "
                "observed compaction, not its exact old-token overlap. Stage sets "
                "partition recorded prefill batches. A mixed interval is retained "
                "whole, never split by request/token fractions. Pure stage sets "
                "describe recorded rows, not exclusive GPU execution or removable "
                "runtime; intervals retain queue/launch/restore dependencies."
            ),
        },
        "cuda_graph_replay_observations": {
            phase + ":" + state: count
            for (phase, state), count in sorted(replay.items())
        },
        "scope": (
            "Recorded native batch extend geometry, including chunking/re-execution. "
            "Mixed prefill batches may include one-token decode rows. No exclusive "
            "kernel time or counterfactual saving is allocated by token count. "
            "Worker intervals include launch/queue/dependency effects; cumulative "
            "intervals are not isolated kernels or removable JCT. Mixed-role batch "
            "intervals are kept whole and are never apportioned to their requests."
        ),
    }


def analyze(client, *, with_prefill_service=False):
    bindings, requests, tools, compactions = [], {}, [], []
    root_rows, root_repeats, sandbox_rows = [], [], []
    workflow_counts, missing, workflow_ids = Counter(), Counter(), set()
    after_compaction = []
    topology_events = []
    client = client.resolve()
    workflow_paths = sorted(client.glob("workflows/*/runtime_events.deepagents.jsonl"))
    for path in workflow_paths:
        events = read_source(path, bindings, jsonl=True)
        events.sort(key=lambda row: (row["ts_ms"], row["sequence"]))
        local_workflows = {row["workflow_id"] for row in events}
        if len(local_workflows) != 1:
            raise ValueError(f"expected one workflow per source: {path}")
        workflow_ids.update(local_workflows)
        topology_events.extend(
            event for event in events if event["kind"] in (
                "invocation_create", "spawn", "join_create", "join_wait",
                "join_satisfied", "workflow_start",
            )
        )
        creates = {
            row.get("invocation_id"): row for row in events
            if row["kind"] == "invocation_create"
        }
        local_tools, unclosed = tool_pairs(events)
        tools.extend(local_tools)
        missing["unclosed_tools"] += unclosed
        compacted_invocations = set()
        for event in events:
            kind, attrs = event["kind"], event.get("attributes") or {}
            if kind == "llm_submit":
                request = attrs["request_id"]
                if request in requests:
                    raise ValueError(f"duplicate request: {request}")
                invocation = creates.get(event.get("invocation_id"), {})
                role = (
                    "summary" if attrs.get("runtime_internal")
                    else "child" if invocation.get("parent_invocation_id")
                    else "root"
                )
                requests[request] = {
                    "request_id": request, "workflow": event["workflow_id"],
                    "invocation": event.get("invocation_id"),
                    "role": role, "submit_ms": event["ts_ms"],
                    "prompt_chars": attrs.get("prompt_chars"),
                    "prompt_sha256": attrs.get("prompt_semantic_sha256"),
                }
                if (
                    not attrs.get("runtime_internal")
                    and event.get("invocation_id") in compacted_invocations
                ):
                    after_compaction.append(request)
                    compacted_invocations.remove(event.get("invocation_id"))
            elif kind == "llm_result":
                request = attrs.get("request_id")
                if request not in requests:
                    raise ValueError(f"result without submit: {request}")
                requests[request].update({
                    "span_ms": event["ts_ms"] - requests[request]["submit_ms"],
                    "finish_reason": attrs.get("finish_reason"),
                    "tool_calls": attrs.get("tool_call_count"),
                    "output_chars": attrs.get("output_chars"),
                })
            elif kind == "context_compact":
                compactions.append({"workflow": event["workflow_id"], **attrs})
                compacted_invocations.add(event.get("invocation_id"))
            elif kind == "workflow_end":
                workflow_counts["ended_in_observed_prefix"] += 1
        trajectory = path.parent / "trajectory.json"
        if trajectory.exists():
            messages = read_source(trajectory, bindings)
            evidence = root_evidence(messages, local_tools)
            root_rows.extend(evidence["eligible"])
            root_repeats.extend(evidence["repeated"])
            missing["root_messages_without_completed_tool"] += evidence[
                "tool_messages_without_completed_event"
            ]
            workflow_counts["root_trajectories"] += 1
        sandbox = path.parent / "sandbox_audit.jsonl"
        if sandbox.exists():
            sandbox_rows.extend(
                row for row in read_source(sandbox, bindings, jsonl=True)
                if row.get("event") == "sandbox_execute"
            )

    native_results, native_submits = {}, {}
    native_path = client.parent / "server/runtime_events.sglang.jsonl"
    if native_path.exists():
        for row in read_source(native_path, bindings, jsonl=True):
            attrs = row.get("attributes") or {}
            if row.get("kind") == "llm_result":
                tokens = attrs.get("output_tokens")
                request = attrs.get("request_id")
                if isinstance(tokens, int) and request in requests:
                    if request in native_results:
                        raise ValueError(f"duplicate native result: {request}")
                    native_results[request] = tokens
            elif row.get("kind") == "llm_submit":
                request = attrs.get("request_id")
                if request in native_submits:
                    raise ValueError(f"duplicate native submit: {request}")
                native_submits[request] = attrs

    within, across = repeated_results(tools)
    same_input_within, same_input_across = repeated_results(
        tools, require_equal_output=False
    )
    repeated_requests = [
        requests[key] for key in sorted({
            row["request_id"] for row in root_repeats if row["request_id"] in requests
        })
    ]
    summary_by_workflow = defaultdict(list)
    for row in requests.values():
        if row["role"] == "summary" and "span_ms" in row:
            summary_by_workflow[row["workflow"]].append(row["span_ms"])
    per_role = {}
    native_fields = (
        "prompt_tokens", "cache_hit_tokens", "cached_tokens_device",
        "cached_tokens_host", "uncached_prompt_tokens", "mamba_host_hit_slots",
    )

    def ingress_metrics(keys):
        observed = [native_submits[key] for key in keys if key in native_submits]
        return {
            "matched_native_submits": len(observed),
            "fields": {
                field: distribution(
                    attrs[field] for attrs in observed
                    if isinstance(attrs.get(field), (int, float))
                ) for field in native_fields
            },
            "scope": (
                "Native registration snapshots, not actual GPU prefill-token work "
                "or cache misses attributable exclusively to compaction."
            ),
        }

    for role in ("root", "child", "summary"):
        rows = [row for row in requests.values() if row["role"] == role]
        per_role[role] = {
            **token_totals(rows, native_results),
            "request_span_ms": distribution(
                row["span_ms"] for row in rows if "span_ms" in row
            ),
            "prompt_chars": distribution(
                row["prompt_chars"] for row in rows
                if isinstance(row["prompt_chars"], (int, float))
            ),
            "finish_reasons": dict(Counter(
                row.get("finish_reason", "unclosed") for row in rows
            )),
            "tool_call_counts": dict(Counter(
                row.get("tool_calls") for row in rows if "span_ms" in row
            )),
            "native_ingress": ingress_metrics(row["request_id"] for row in rows),
        }

    def tool_group(rows):
        return {
            "count": len(rows),
            "workflows": len({row["workflow"] for row in rows}),
            "duration_ms": distribution(row["duration_ms"] for row in rows),
            "tools": dict(Counter(row["tool"] for row in rows)),
            "top_workflows": Counter(
                row["workflow"] for row in rows
            ).most_common(12),
        }

    observed_outputs = [
        row["output_chars"] for row in tools
        if isinstance(row["output_chars"], (int, float))
    ]
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "client": str(client), "sources": bindings,
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": (
            "Per-file sealed complete-line prefixes; native may still be running. "
            "Tool/LLM spans include waiting and overlap across agents; their sums "
            "are not run time or counterfactual JCT savings. Post-hoc equal tool "
            "results do not establish state equivalence, determinism, or safe hits. "
            "Root workspace epochs are runtime observations, not read-set/env proofs. "
            "Callback output hashes include ToolMessage metadata/IDs and feedback, "
            "so equality counts cannot estimate all duplicate result content. "
            "Repeated-round decode is already spent before a tool-cache lookup; "
            "memoizing a tool alone does not remove those model requests."
        ),
        "workflow_sources": len(workflow_paths),
        "workflow_counts": dict(workflow_counts), "missing": dict(missing),
        "dependency_topology": dependency_topology(topology_events),
        "requests_by_role": per_role,
        "prompt_identity_repetition": prompt_repetitions(
            requests, native_submits, native_results
        ),
        "summary_span_sum_per_workflow_ms": distribution(
            sum(summary_by_workflow.get(workflow, ()))
            for workflow in workflow_ids
        ),
        "context_compaction": {
            "count": len(compactions),
            "next_ordinary_request_native_ingress": ingress_metrics(after_compaction),
            "source_message_count": distribution(
                row["source_message_count"] for row in compactions
                if isinstance(row.get("source_message_count"), int)
            ),
            "summary_chars": distribution(
                row["summary_chars"] for row in compactions
                if isinstance(row.get("summary_chars"), int)
            ),
        },
        "all_completed_tools": tool_group(tools),
        "tool_output_chars": distribution(observed_outputs),
        "posthoc_repeated_equal_results_within_agent": tool_group(within),
        "posthoc_repeated_equal_results_across_agents": tool_group(across),
        "repeated_inputs_with_prior_completed_call_within_agent": tool_group(
            same_input_within
        ),
        "repeated_inputs_with_prior_completed_call_across_agents": tool_group(
            same_input_across
        ),
        "root_observed_unchanged_epoch_tools": tool_group(root_rows),
        "root_repeated_same_result_in_observed_unchanged_epoch": tool_group(root_repeats),
        "root_repeat_issuing_model_requests": token_totals(
            repeated_requests, native_results
        ),
        "sandbox": {
            "execute_count": len(sandbox_rows),
            "duration_ms": distribution(
                row["duration_ms"] for row in sandbox_rows
                if isinstance(row.get("duration_ms"), (int, float))
            ),
            "lock_wait_ms": distribution(
                row["lock_wait_ms"] for row in sandbox_rows
                if isinstance(row.get("lock_wait_ms"), (int, float))
            ),
        },
        "native_submit_attribute_keys": sorted({
            key for attrs in native_submits.values() for key in attrs
        }),
        "actual_prefill_service": (
            prefill_service(
                client.parent / "server/runtime_audit.jsonl", requests,
                after_compaction, bindings,
            ) if with_prefill_service else {"status": "not_requested"}
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--with-prefill-service", action="store_true")
    args = parser.parse_args()
    result = analyze(args.client, with_prefill_service=args.with_prefill_service)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    summary = {key: value for key, value in result.items() if key != "sources"}
    summary["prompt_identity_repetition"] = {
        key: value for key, value in result["prompt_identity_repetition"].items()
        if key != "groups"
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
