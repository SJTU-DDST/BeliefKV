"""Whole-workflow inventory and reproducible size/context workload declarations."""

from collections import Counter, defaultdict
from graphlib import TopologicalSorter
import json
import math
from pathlib import Path
import random

from beliefkv.core.events import RelationType, RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.arrival_schedule import arrival_schedule_digest
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.replay_arrivals import permuted_poisson_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from beliefkv.predictor.workflow_work import WorkflowProgress


PROTOCOL_FIELDS = (
    "model", "mode", "subagent_fanout_profile", "context_lifecycle",
    "child_work_budget_warning_calls", "child_work_budget_target_calls",
    "root_work_budget_warning_calls", "root_work_budget_target_calls",
    "loop_guard", "recursion_limit", "max_completion_tokens",
    "sampling_profile", "sampling_seed", "native_radix_sessions",
    "completion_gate_enabled", "completion_repair_attempts",
    "tool_circuit_breaker_enabled", "tool_observation_budget",
    "sandbox_command_timeout_s",
)


def quantile(values, probability):
    """Linear empirical quantiles, including small populations."""
    ordered = sorted(values)
    if not ordered or not 0 <= probability <= 1:
        raise ValueError("quantiles require a nonempty population and valid probability")
    index = (len(ordered) - 1) * probability
    left = math.floor(index)
    return ordered[left] + (ordered[math.ceil(index)] - ordered[left]) * (index - left)


def summary(values):
    if not values:
        return {"count": 0}
    low, high = quantile(values, .1), quantile(values, .9)
    return {
        "count": len(values), "min": min(values), "p10": low,
        "p50": quantile(values, .5), "p90": high, "max": max(values),
        "p90_p10": high / low if low > 0 else None,
    }


def agent_work_breakdown(calls, root, creates):
    """Attribute intrinsic model work once, independently of scheduling wall time."""
    agents = {}
    for call in calls:
        owner = call["invocation_id"]
        if owner not in creates:
            raise ValueError("model work has no observed invocation owner")
        item = agents.setdefault(owner, {
            "invocation_id": owner,
            "kind": (
                "root" if owner == root else
                "runtime_internal" if creates[owner].attributes.get("runtime_internal") else "child"
            ),
            "role": creates[owner].agent_definition_id,
            "model_calls": 0, "generated_tokens": 0, "necessary_input_tokens": 0,
        })
        item["model_calls"] += 1
        item["generated_tokens"] += call["output_tokens"]
        item["necessary_input_tokens"] += call["necessary_input_tokens"]
    for item in agents.values():
        item["model_work_units"] = .01 * item["necessary_input_tokens"] + item["generated_tokens"]
    rows = sorted(agents.values(), key=lambda item: item["invocation_id"])
    root_work = sum(row["model_work_units"] for row in rows if row["kind"] == "root")
    child_work = sum(row["model_work_units"] for row in rows if row["kind"] == "child")
    task_work = root_work + child_work
    return {
        "agents": rows,
        "root_model_work_units": root_work, "child_model_work_units": child_work,
        "runtime_internal_model_work_units": sum(
            row["model_work_units"] for row in rows if row["kind"] == "runtime_internal"
        ),
        "child_model_work_fraction": child_work / task_work if task_work else None,
        "largest_child_model_work_fraction": max(
            (row["model_work_units"] for row in rows if row["kind"] == "child"), default=0.,
        ) / task_work if task_work else None,
        "work_proxy": ".01 * necessary_new_input + generated_output; excludes queueing and tool waits",
    }


def protocol(manifest):
    config = manifest["config"]
    values = {
        "config": {name: config.get(name) for name in PROTOCOL_FIELDS},
        "delegation_contract": manifest.get("delegation_contract"),
        "dynamic_subagent_policy": manifest.get("dynamic_subagent_policy"),
    }
    return {"sha256": fingerprint(values), "values": values}


def _paths(plan, increments, *, seconds_per_model_unit):
    nodes = {node.node_id: node for node in plan.nodes}
    paths = defaultdict(dict)
    for identity in TopologicalSorter(plan.dependency_graph()).static_order():
        node = nodes[identity]
        state = paths[node.workflow_id]
        model = increments.get(node.request_id, 0.)
        tool = (
            node.delay_ms / 1000.
            if node.event is not None and node.event.kind is Kind.TOOL_END else 0.
        )
        external = node.delay_ms / 1000.
        costs = (
            model, tool, external, model * seconds_per_model_unit + external,
            model * seconds_per_model_unit + tool,
        )
        state[identity] = tuple(
            cost + max((state[dep][index] for dep in node.dependencies), default=0.)
            for index, cost in enumerate(costs)
        )
    return {
        workflow: tuple(max(row[index] for row in state.values()) for index in range(5))
        for workflow, state in paths.items()
    }


def inventory_capture(capture, *, seconds_per_model_unit, plan_loader=None):
    """Read exact tokens and complete causal graphs; do not edit a source."""
    if (
        type(seconds_per_model_unit) not in (int, float)
        or not math.isfinite(seconds_per_model_unit) or seconds_per_model_unit <= 0
    ):
        raise ValueError("declare a finite positive model-time proxy before profiling")
    if plan_loader is None:
        from scripts.run_causal_gpu_replay import source_plan

        plan_loader = source_plan
    capture = Path(capture).resolve()
    manifests = list(capture.glob("client_*/manifest.json"))
    if len(manifests) != 1:
        raise ValueError("inventory requires one original capture manifest")
    manifest_path = manifests[0]
    manifest = json.loads(manifest_path.read_bytes())
    contract = protocol(manifest)
    plan, expected, evidence = plan_loader(capture)
    populations, events, results, bindings = {}, defaultdict(list), {}, {}
    for directory in sorted((manifest_path.parent / "workflows").iterdir()):
        event_path = directory / "runtime_events.deepagents.jsonl"
        if not event_path.exists():
            continue
        result_path, trajectory_path = directory / "result.json", directory / "trajectory.json"
        messages = json.loads(trajectory_path.read_bytes())
        task = next((row.get("content", "") for row in messages if row.get("type") == "human"), "")
        if not isinstance(task, str) or "Project: " not in task:
            raise ValueError("workflow has no original project identity")
        project = task.split("Project: ", 1)[1].split("\n", 1)[0].strip()
        workflow_events = [
            RuntimeEvent.from_dict(json.loads(line)) for line in event_path.open()
            if json.loads(line)["kind"] == Kind.WORKFLOW_START.value
        ]
        if len(workflow_events) != 1:
            raise ValueError("workflow directory has no unique causal start")
        workflow = workflow_events[0].workflow_id
        if workflow in populations:
            raise ValueError("duplicate original workflow")
        populations[workflow] = {
            "project": project, "task_id": directory.name,
            "source_capture": str(capture), "workflow_id": workflow,
            "event_sha256": sha256_file(event_path),
            "protocol_sha256": contract["sha256"],
        }
        results[workflow] = json.loads(result_path.read_bytes())
        for path in (event_path, result_path, trajectory_path):
            bindings[str(path)] = sha256_file(path)
    for node in plan.nodes:
        if node.event is not None:
            events[node.workflow_id].append(node.event)
    if populations.keys() != events.keys():
        raise ValueError("inventory and causal workflow populations differ")
    increments, tokens, states = {}, defaultdict(list), {}
    with CapturedTokenCorpus(capture / "server/request_tokens.jsonl", expected) as corpus:
        for event in sorted(
            (event for rows in events.values() for event in rows), key=lambda event: event.ts_ms,
        ):
            if event.kind is not Kind.LLM_RESULT:
                continue
            request = event.attributes["request_id"]
            value = corpus.get(request)
            progress = states.setdefault(event.workflow_id, WorkflowProgress())
            added = progress.completed(
                request, event.invocation_id, event.context_id,
                value.prompt_tokens, value.output_tokens,
            )
            increments[request] = added * .01 + len(value.output_tokens)
            tokens[event.workflow_id].append({
                "request_id": request, "invocation_id": event.invocation_id,
                "context_id": event.context_id, "context_epoch": event.context_epoch,
                "prompt_tokens": len(value.prompt_tokens), "output_tokens": len(value.output_tokens),
                "necessary_input_tokens": added,
            })
        token_evidence = corpus.evidence
    paths = _paths(plan, increments, seconds_per_model_unit=seconds_per_model_unit)
    rows = []
    for workflow, base in sorted(populations.items()):
        observed, calls, result = events[workflow], tokens[workflow], results[workflow]
        by_kind = defaultdict(list)
        for event in observed:
            by_kind[event.kind].append(event)
        control = result.get("agent_control", {})
        interventions = sum(control.get(name, 0) for name in (
            "guard_intervened_completions", "guard_intervened_natural_language_returns",
            "forced_semantic_completions",
        ))
        rejected = []
        if (
            result.get("natural_terminal") is not True or result.get("error")
            or interventions or result.get("semantic_gate_controlled_stop")
        ):
            rejected.append("not_a_complete_natural_return")
        roots = {
            event.invocation_id for event in by_kind[Kind.INVOCATION_CREATE]
            if event.relation_type is RelationType.ROOT
        }
        if len(roots) != 1:
            raise ValueError("workflow needs exactly one original root")
        root = next(iter(roots))
        served = {call["invocation_id"] for call in calls}
        returned = {event.invocation_id for event in by_kind[Kind.RETURN]}
        satisfied = {event.join_id for event in by_kind[Kind.JOIN_SATISFIED]}
        joins = by_kind[Kind.JOIN_CREATE]
        join_parents = {
            event.join_id: event.invocation_id for event in by_kind[Kind.JOIN_WAIT]
        }
        parallel = [
            join for join in joins if join_parents.get(join.join_id) == root
            and len(join.member_invocation_ids) >= 2
            and set(join.member_invocation_ids) <= served & returned
            and join.join_id in satisfied
        ]
        if not parallel:
            rejected.append("no_completed_parallel_root_join_with_two_served_children")
        creates = {event.invocation_id: event for event in by_kind[Kind.INVOCATION_CREATE]}
        depths = {}
        def depth(owner):
            if owner not in depths:
                parent = creates[owner].parent_invocation_id
                depths[owner] = 0 if parent is None else depth(parent) + 1
            return depths[owner]
        for owner in creates:
            depth(owner)
        tool_durations = [
            node.delay_ms / 1000. for node in plan.nodes if node.workflow_id == workflow
            and node.event is not None and node.event.kind is Kind.TOOL_END
        ]
        starts, ends = by_kind[Kind.WORKFLOW_START], by_kind[Kind.WORKFLOW_END]
        if len(starts) != 1 or len(ends) != 1:
            raise ValueError("inventory requires exact workflow start/end boundaries")
        model_work = sum(increments[call["request_id"]] for call in calls)
        rows.append({
            **base, "eligible": not rejected, "rejection_reasons": rejected,
            "token_source_sha256": token_evidence["token_source_sha256"],
            "requests": len(calls), "generated_tokens": sum(call["output_tokens"] for call in calls),
            "necessary_input_tokens": sum(call["necessary_input_tokens"] for call in calls),
            "model_work_units": model_work, "initial_prompt_tokens": next(
                call["prompt_tokens"] for call in calls if call["invocation_id"] == root
            ),
            "agent_work_breakdown": agent_work_breakdown(calls, root, creates),
            "prompt_tokens": summary([call["prompt_tokens"] for call in calls]),
            "max_context_tokens": max(call["prompt_tokens"] for call in calls),
            "tools": len(tool_durations), "tool_total_s": sum(tool_durations),
            "tool_duration_s": summary(tool_durations),
            "critical_model_work_units": paths[workflow][0],
            "critical_tool_only_s": paths[workflow][1],
            "critical_external_only_s": paths[workflow][2],
            "critical_model_tool_proxy_s": paths[workflow][3],
            "critical_model_observed_tool_proxy_s": paths[workflow][4],
            "source_jct_s": (ends[0].ts_ms - starts[0].ts_ms) / 1000.,
            "joins": len(joins),
            "root_join_rounds": sum(join_parents.get(join.join_id) == root for join in joins),
            "completed_parallel_root_joins": len(parallel),
            "join_fanout_counts": dict(sorted(Counter(len(join.member_invocation_ids) for join in joins).items())),
            "nested_joins": sum(join_parents.get(join.join_id) != root for join in joins),
            "maximum_spawn_depth": max(depths.values()),
            "child_roles": dict(Counter(
                event.agent_definition_id for owner, event in creates.items() if owner != root
            )),
            "environment_test_diagnostics": {
                name: control.get(name) for name in (
                    "executed_test_calls", "test_command_count", "test_environment_failures",
                    "test_execution_status_counts",
                )
            },
            "semantic_return_not_independent_task_correctness": True,
        })
    return {
        "schema_version": 1, "kind": "whole_natural_workflow_inventory",
        "capture": str(capture), "protocol": contract, "workflows": rows,
        "source_evidence": evidence, "token_evidence": token_evidence,
        "files_sha256": {
            **bindings, str(manifest_path): sha256_file(manifest_path),
            str(capture / "server/request_tokens.jsonl"): sha256_file(capture / "server/request_tokens.jsonl"),
        },
        "model_seconds_per_unit": seconds_per_model_unit,
        "scope": {
            "whole_workflows_only": True, "workflows_truncated": False,
            "source_jct_used_as_size_label": False,
            "model_work": ".01 * necessary_new_input + generated_output; not exact GPU time",
            "critical_paths_use_parallel_max": True,
            "model_observed_tool_path_excludes_runtime_and_source_queue_gaps": True,
            "logical_prompt_lengths_are_not_physical_KV_occupancy": True,
            "tool_timing": "original_natural_capture_no_synthetic_overlay",
            "test_assertions_repaired": False, "performance_accepted": False,
        },
    }


def population_summary(rows):
    return {
        "executions": len(rows), "projects": len({row["project"] for row in rows}),
        **{
            name: summary([row[name] for row in rows])
            for name in (
                "model_work_units", "generated_tokens", "necessary_input_tokens", "requests",
                "initial_prompt_tokens", "max_context_tokens", "tool_total_s",
                "critical_model_tool_proxy_s",
            )
        },
        "joins": sum(row["joins"] for row in rows),
        "nested_joins": sum(row["nested_joins"] for row in rows),
    }


def stratify(rows, *, each=6, include_tools=False):
    """Keep the historical rank, or explicitly rank model/tool critical paths."""
    if type(each) is not int or each < 1 or len(rows) != each * 3:
        raise ValueError("stratification requires exactly three equal whole-workflow tiers")
    if (
        len({row["project"] for row in rows}) != len(rows)
        or len({row["protocol_sha256"] for row in rows}) != 1
        or any(row.get("eligible") is not True for row in rows)
    ):
        raise ValueError("mix requires eligible, project-disjoint, protocol-matched workflows")
    if include_tools:
        if any(
            type(row.get("critical_model_observed_tool_proxy_s")) not in (int, float)
            or not math.isfinite(row["critical_model_observed_tool_proxy_s"])
            or row["critical_model_observed_tool_proxy_s"] <= 0
            for row in rows
        ):
            raise ValueError("tool-aware tiers require refreshed model/observed-tool causal paths")
        ordered = sorted(rows, key=lambda row: (
            row["critical_model_observed_tool_proxy_s"],
            row["model_work_units"], row["workflow_id"],
        ))
        return [
            {
                **row, "size_tier": ("short", "medium", "long")[index // each],
                "size_ranking_policy": "model_and_observed_tool_critical_path",
                "size_score_seconds": row["critical_model_observed_tool_proxy_s"],
            }
            for index, row in enumerate(ordered)
        ]
    ranks = defaultdict(float)
    for name in ("generated_tokens", "necessary_input_tokens", "requests"):
        ordered = sorted(rows, key=lambda row: row[name])
        index = 0
        while index < len(ordered):
            end = index + 1
            while end < len(ordered) and ordered[end][name] == ordered[index][name]:
                end += 1
            for row in ordered[index:end]:
                ranks[row["workflow_id"]] += (index + end - 1) / 2.
            index = end
    ordered = sorted(rows, key=lambda row: (ranks[row["workflow_id"]], row["workflow_id"]))
    return [
        {**row, "size_tier": ("short", "medium", "long")[index // each],
         "composite_size_rank": ranks[row["workflow_id"]] / 3.}
        for index, row in enumerate(ordered)
    ]


def context_crossing(rows):
    """Report context overlap without interpreting logical sizes as HBM occupancy."""
    tiers = {name: [row for row in rows if row["size_tier"] == name] for name in ("short", "medium", "long")}
    if any(not values for values in tiers.values()):
        raise ValueError("context crossing needs all three size tiers")
    pairs = [(short, long) for short in tiers["short"] for long in tiers["long"]]
    return {
        "initial_prompt_by_tier": {
            name: summary([row["initial_prompt_tokens"] for row in values])
            for name, values in tiers.items()
        },
        "maximum_context_by_tier": {
            name: summary([row["max_context_tokens"] for row in values])
            for name, values in tiers.items()
        },
        "short_long_pairs": len(pairs),
        "short_has_larger_initial_prompt_pairs": sum(
            short["initial_prompt_tokens"] > long["initial_prompt_tokens"] for short, long in pairs
        ),
        "short_has_larger_maximum_context_pairs": sum(
            short["max_context_tokens"] > long["max_context_tokens"] for short, long in pairs
        ),
    }


def shuffled_workflows(rows, *, seed):
    if type(seed) is not int or seed < 0:
        raise ValueError("declare a nonnegative population permutation seed")
    ordered = sorted(rows, key=lambda row: row["workflow_id"])
    if len({row["workflow_id"] for row in ordered}) != len(ordered):
        raise ValueError("population repeats a workflow")
    random.Random(seed).shuffle(ordered)
    return ordered


def population_fingerprint(rows):
    """Stable workflow IDs alone cannot distinguish independent native captures."""
    if not rows or len({row["workflow_id"] for row in rows}) != len(rows):
        raise ValueError("population needs distinct whole workflow identities")
    keys = []
    for row in rows:
        if any(
            not isinstance(row.get(name), str) or not row[name]
            for name in (
                "workflow_id", "project", "source_capture", "event_sha256", "token_source_sha256",
            )
        ):
            raise ValueError("population identity requires bound source events and tokens")
        keys.append(tuple(row[name] for name in (
            "workflow_id", "project", "source_capture", "event_sha256", "token_source_sha256",
        )))
    return fingerprint(sorted(keys))


def burst_arrivals(workflow_ids):
    identities = list(workflow_ids)
    if not identities or len(set(identities)) != len(identities):
        raise ValueError("burst needs a unique, nonempty workflow population")
    rows = [{"workflow_id": identity, "offset_seconds": 0.} for identity in identities]
    return {
        "schema_version": 3, "mode": "simultaneous",
        "arrival_schedule": rows, "arrival_schedule_sha256": arrival_schedule_digest(rows),
        "jct_boundary": "scheduled_workflow_arrival_to_workflow_end",
    }


def calibrated_arrivals(
    rows, *, completed_control, permutation_seed, arrival_seed, initial_workflows=5,
    offered_load=.9,
):
    """Use a complete, same-population control's measured workflow capacity."""
    if type(offered_load) not in (float, int) or not .0 < offered_load < 1.:
        raise ValueError("calibration load must be between zero and one")
    if type(initial_workflows) is not int or not 4 <= initial_workflows <= 6:
        raise ValueError("declare four to six warm-start workflows")
    identities = {row["workflow_id"] for row in rows}
    required = {
        "workflow_ids", "completed_workflows", "makespan_seconds", "all_work_complete",
        "source_population_sha256", "registration_sha256", "mode",
    }
    if completed_control.keys() != required:
        raise ValueError("capacity calibration requires the explicit completed-control contract")
    seconds = completed_control["makespan_seconds"]
    expected = population_fingerprint(rows)
    if (
        completed_control["all_work_complete"] is not True
        or completed_control["mode"] != "simultaneous"
        or completed_control["completed_workflows"] != len(rows)
        or set(completed_control["workflow_ids"]) != identities
        or len(completed_control["workflow_ids"]) != len(rows)
        or completed_control["source_population_sha256"] != expected
        or not isinstance(completed_control["registration_sha256"], str)
        or len(completed_control["registration_sha256"]) != 64
        or type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0
    ):
        raise ValueError("require a complete matched burst control before rate calibration")
    ordered = shuffled_workflows(rows, seed=permutation_seed)
    rate = len(rows) / seconds * offered_load
    return {
        "arrivals": permuted_poisson_arrival_plan(
            [row["workflow_id"] for row in ordered],
            rate_per_second=rate, seed=arrival_seed, permutation_seed=permutation_seed,
            initial_workflows=initial_workflows,
        ),
        "calibration": completed_control, "offered_load": offered_load,
        "permutation_seed": permutation_seed,
        "scope": "finite-population calibration proxy; verify sustained pressure and idle fraction on GPU",
    }


def capacity_competition(candidates, *, capacity, buffer_tokens=0):
    """Local logical competition after higher categories; not native fit proof."""
    if (
        type(capacity) is not int or capacity < 0
        or type(buffer_tokens) is not int or buffer_tokens < 0
    ):
        raise ValueError("capacity and buffer must be nonnegative token counts")
    owners, categories = set(), defaultdict(list)
    for item in candidates:
        if (
            not isinstance(item, dict) or not item.get("owner") or item["owner"] in owners
            or not item.get("workflow_id") or item.get("category") not in (0, 1, 2)
            or type(item.get("tokens")) is not int or item["tokens"] <= 0
            or type(item.get("sequence")) is not int
            or type(item.get("ready_paused")) is not bool
            or item.get("size_tier") not in ("short", "medium", "long")
        ):
            raise ValueError("invalid capacity-competition candidate")
        owners.add(item["owner"])
        categories[item["category"]].append(item)
    remaining, opportunities = capacity, []
    for category in (0, 1, 2):
        peers = sorted(categories[category], key=lambda item: (item["tokens"], item["sequence"]))
        fitting = [
            item for item in peers
            if item["ready_paused"] and item["tokens"] + buffer_tokens <= remaining
        ]
        # Competition must contain a short/long cross-workflow pair; merely
        # having a medium member or an unready acting owner is insufficient.
        cross = any(
            left["workflow_id"] != right["workflow_id"]
            and {left["size_tier"], right["size_tier"]} == {"short", "long"}
            for left in fitting for right in fitting
        )
        total = sum(item["tokens"] + buffer_tokens for item in fitting)
        if cross and total > remaining:
            opportunities.append({
                "category": category, "remaining_tokens": remaining,
                "individually_fitting_owners": [item["owner"] for item in fitting],
                "workflows": sorted({item["workflow_id"] for item in fitting}),
                "aggregate_tokens": total,
                "logical_only": True, "native_ready_and_physical_fit_verified": False,
            })
        for item in peers:
            cost = item["tokens"] + buffer_tokens
            if cost <= remaining:
                remaining -= cost
    return opportunities
