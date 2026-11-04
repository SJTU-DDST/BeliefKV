"""Screen both sides of local compute reordering on an observed dependency DAG."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from graphlib import TopologicalSorter
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.audit_resident_decode_prefill_frontiers import (
    frontier_index, last_outstanding_child, retained_members,
)
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def dependency_model(plan, events, requests):
    """Recover observed lags; preserve unfinished background as a prefix."""
    observed = {row["event_id"]: row for row in events}
    if len(observed) != len(events):
        raise ValueError("duplicate observed event identity")
    nodes, times = {}, {}
    for node in plan.nodes:
        row = observed.get(node.event.event_id) if node.event else requests.get(node.request_id)
        if row is None:
            continue
        when = row["ts_ms"] if node.event else row["native_complete_monotonic_ms"]
        if type(when) not in (int, float) or not math.isfinite(when) or when < 0:
            raise ValueError("invalid observed node clock")
        if node.event and (
            row["workflow_id"] != node.workflow_id or row["kind"] != node.event.kind.value
        ):
            raise ValueError("observed event differs from the source")
        nodes[node.node_id], times[node.node_id] = node, when
    if any(not set(node.dependencies) <= nodes.keys() for node in nodes.values()):
        raise ValueError("the observed prefix is missing a real causal predecessor")
    order = tuple(TopologicalSorter({
        key: node.dependencies for key, node in nodes.items()
    }).static_order())
    index = {key: position for position, key in enumerate(order)}
    dependencies, lags, reference = [], [], []
    starts, ends, request_indices = {}, {}, {}
    for key in order:
        node, when = nodes[key], times[key]
        deps = tuple(index[previous] for previous in node.dependencies)
        before = max((reference[position] for position in deps), default=when)
        if when < before - 1e-5:
            raise ValueError("an observed node completed before its dependency")
        dependencies.append(deps)
        lags.append(max(0., when - before))
        reference.append(when)
        if node.request_id:
            request_indices[node.request_id] = index[key]
        elif node.event.kind is Kind.WORKFLOW_START:
            starts[node.workflow_id] = index[key]
        elif node.event.kind is Kind.WORKFLOW_END:
            ends[node.workflow_id] = index[key]
    return {
        "dependencies": dependencies, "lags": lags, "reference": reference,
        "starts": starts, "ends": ends, "requests": request_indices,
    }


def propagate(model, *, advance=None, delay=None):
    """A local timing projection, not a replay of the shared execution engine."""
    advance, delay = advance or {}, delay or {}
    if advance.keys() & delay.keys() or not (advance.keys() | delay.keys()) <= model["requests"].keys():
        raise ValueError("require disjoint, observed request interventions")
    advances = {model["requests"][rid]: value for rid, value in advance.items()}
    delays = {model["requests"][rid]: value for rid, value in delay.items()}
    if any(
        not math.isfinite(value) or value < 0 or value > model["lags"][position] + 1e-5
        for position, value in advances.items()
    ) or any(not math.isfinite(value) or value < 0 for value in delays.values()):
        raise ValueError("intervention would require a negative request duration")
    observed, current = model["reference"], []
    for position, deps in enumerate(model["dependencies"]):
        when = max((current[previous] for previous in deps), default=observed[position])
        when += model["lags"][position] - advances.get(position, 0.)
        if position in delays:
            # Several chunks/requests from one branch pay one absolute shift,
            # rather than repeatedly adding the same delayed prefill window.
            when = max(when, observed[position] + delays[position])
        current.append(when)
    if any(current[position] != observed[position] for position in model["starts"].values()):
        raise ValueError("projection changed the fixed arrival times")
    return current


def changes(model, current, workflows):
    return {
        workflow: current[model["ends"][workflow]] - model["reference"][model["ends"][workflow]]
        for workflow in workflows
    }


def case_distribution(values):
    return {key: value for key, value in distribution(values).items() if key != "sum"}


def candidate_windows(forwards, requests, closed, marker_ids, joins, returned, *, budget_ms):
    previous, prefills, first, counts = None, [], {}, Counter()
    for row in forwards:
        if row["phase"] == "prefill":
            prefills.append(row)
            continue
        if row["phase"] != "decode":
            raise ValueError("unsupported worker phase")
        if previous is not None and prefills:
            counts["closed_prefill_bursts"] += 1
            retained = tuple(retained_members(previous, prefills, row))
            counts["whole_following_decode_can_move_without_new_members"] += (
                len(retained) == len(row["request_samples"])
            )
            begin, end = prefills[0]["launch_monotonic_ms"], prefills[-1]["complete_monotonic_ms"]
            step = max(previous["service_elapsed_ms"], row["service_elapsed_ms"])
            victims = {
                member["request_id"]: member["workflow_id"]
                for batch in prefills for member in batch["request_samples"]
            }
            for member in retained:
                rid = member["request_id"]
                request = requests.get(rid)
                if request is None:
                    counts["retained_request_without_complete_receipt"] += 1
                    continue
                emitted = member["output_tokens_after"]
                remaining = request["output_tokens"] - emitted
                if remaining < 1 or rid in victims:
                    raise ValueError("invalid retained request")
                cost = remaining * step
                if cost > budget_ms or begin + cost >= end:
                    continue
                counts["local_budget_and_exposure_opportunities"] += 1
                if member["workflow_id"] not in closed or not set(victims.values()) <= closed:
                    counts["opportunities_with_censored_direct_participants"] += 1
                    continue
                counts["opportunities_with_closed_direct_participants"] += 1
                if rid in first:
                    continue
                output = request["native_receipt"]["output_token_ids"]
                visible = output[:emitted]
                first[rid] = {
                    "sample_id": prefills[0]["sample_id"], "request_id": rid,
                    "workflow_id": member["workflow_id"], "invocation_id": member["invocation_id"],
                    "begin_ms": begin, "prefill_end_ms": end, "prefill_batches": len(prefills),
                    "oracle_remaining_outputs": remaining, "nominal_decode_worker_cost_ms": cost,
                    "victim_requests": sorted(victims), "victim_workflows": sorted(set(victims.values())),
                    "tool_open_already_visible": marker_ids["<tool_call>"] in visible,
                    "tool_close_already_visible": marker_ids["</tool_call>"] in visible,
                    "visible_last_outstanding_child": last_outstanding_child(
                        member["invocation_id"], begin, joins, returned,
                    ),
                    "full_state_identity_observed": member["full_state_identity_observed"],
                }
        previous, prefills = row, []
    counts["distinct_earliest_eligible_request_opportunities"] = len(first)
    return tuple(first.values()), counts


def inspect(registration, cohort_path, tokenizer_path, *, maximum_cases, budget_ms):
    execution = verify_execution(registration, cohort_path)
    config = json.loads(registration.read_bytes())
    graph, _expected, _evidence = source_plan(Path(config["source_arm"]))
    measured = set(json.loads(cohort_path.read_bytes())["measured_workflow_ids"])
    arm, reader = Path(config["run_root"]) / "full", SealedRecords()
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    requests = {
        row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")
    }
    model = dependency_model(graph, events, requests)
    baseline = propagate(model)
    if any(abs(when - expected) > 1e-5 for when, expected in zip(baseline, model["reference"])):
        raise ValueError("the unmodified dependency projection does not reproduce observation")
    if not measured <= model["ends"].keys():
        raise ValueError("the fixed complete-JCT cohort is incomplete")
    certificate = json.loads(Path(execution["certificate"]).read_bytes())
    baseline_jct = {
        workflow: baseline[model["ends"][workflow]] - baseline[model["starts"][workflow]]
        for workflow in measured
    }
    if any(
        not math.isclose(value, certificate["measured_JCT_ms"][workflow], abs_tol=1e-5, rel_tol=0.)
        for workflow, value in baseline_jct.items()
    ):
        raise ValueError("baseline projection differs from complete measured JCT")
    tokenizer = json.loads(tokenizer_path.read_bytes())
    markers = {
        value["content"]: int(key)
        for key, value in tokenizer["added_tokens_decoder"].items()
        if value["content"] in ("<tool_call>", "</tool_call>")
    }
    if len(markers) != 2:
        raise ValueError("require the actual serving tool-boundary markers")
    _results, actions, joins, returned = frontier_index(events)
    worker_counts, output_ends = Counter(), {}
    previous_end, domain = -math.inf, None

    def forwards():
        nonlocal previous_end, domain
        for row in reader(arm / "server/runtime_audit.jsonl"):
            if row.get("event") != "gpu_service_sample":
                continue
            worker_counts["worker_samples"] += 1
            begin, end = row["service_start_monotonic_ms"], row["complete_monotonic_ms"]
            clock = row["monotonic_clock_domain"]
            if (
                row["sample_id"] != f"native-service-{worker_counts['worker_samples']:09d}"
                or begin < previous_end - .01 or domain is not None and clock != domain
                or not math.isclose(end - begin, row["service_elapsed_ms"], abs_tol=.01, rel_tol=1e-9)
            ):
                raise ValueError("invalid or overlapping worker interval")
            previous_end, domain = end, clock
            for member in row["request_samples"]:
                rid = member["request_id"]
                before, after = member["output_tokens_reported_before"], member["output_tokens_after"]
                if before != output_ends.get(rid, 0) or after - before != member["output_token_delta"]:
                    raise ValueError("worker output progress is incomplete")
                output_ends[rid] = after
                receipt = requests.get(rid)
                if receipt is not None and tuple(receipt["native_receipt"]["identity"]) != tuple(
                    member[name] for name in ("workflow_id", "invocation_id", "context_id", "context_epoch")
                ):
                    raise ValueError("worker identity differs from the completed request")
            yield row

    cases, counts = candidate_windows(
        forwards(), requests, set(model["ends"]), markers, joins, returned, budget_ms=budget_ms,
    )
    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_bytes())
    if (
        status.get("writer_error") is not None or status["dropped_records"] or status["failed_records"]
        or worker_counts["worker_samples"] != (
            status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
        )
        or any(output_ends.get(rid) != row["output_tokens"] for rid, row in requests.items())
    ):
        raise ValueError("native telemetry or complete output coverage is incomplete")
    # Sampling does not use projected benefit or the private measured population.
    cases = sorted(cases, key=lambda row: hashlib.sha256(
        (row["sample_id"] + "\n" + row["request_id"]).encode("ascii")
    ).digest())[:maximum_cases]
    results = []
    for case_number, case in enumerate(cases, 1):
        rid = case["request_id"]
        row = {**case, "next_boundary_offline": actions.get(rid), "cost_sensitivity": {}}
        for factor in (.75, 1., 1.5, 2.):
            cost = case["nominal_decode_worker_cost_ms"] * factor
            finish = case["begin_ms"] + cost
            advance = requests[rid]["native_complete_monotonic_ms"] - finish
            if advance <= 0:
                row["cost_sensitivity"][str(factor)] = {"applicable": False}
                continue
            current = propagate(
                model, advance={rid: advance},
                delay={request: cost for request in case["victim_requests"]},
            )
            delta = changes(model, current, model["ends"])
            row["cost_sensitivity"][str(factor)] = {
                "applicable": True, "decode_worker_cost_ms": cost,
                "accelerated_request_completion_ms": advance,
                "all_completed_workflow_sum_JCT_reduction_s": -sum(delta.values()) / 1000.,
                "measured_workflow_mean_JCT_reduction_s": -sum(delta[name] for name in measured) / len(measured) / 1000.,
                "affected_workflow_JCT_change_s": {
                    name: value / 1000. for name, value in delta.items() if abs(value) > 1e-5
                },
            }
        results.append(row)
        if case_number % 128 == 0:
            print(json.dumps({
                "projected_local_opportunities": case_number, "total": len(cases),
            }), file=sys.stderr, flush=True)
    summaries = {}
    for name, predicate in (
        ("all_sampled_earliest_opportunities", lambda row: True),
        ("tool_close_visible", lambda row: row["tool_close_already_visible"]),
        ("tool_open_visible", lambda row: row["tool_open_already_visible"]),
        ("visible_last_outstanding_child", lambda row: row["visible_last_outstanding_child"]),
    ):
        population = [row for row in results if predicate(row)]
        summaries[name] = {
            "opportunities": len(population),
            "positive_all_completed_at_all_four_costs": sum(
                all(
                    row["cost_sensitivity"][str(factor)]["applicable"]
                    and row["cost_sensitivity"][str(factor)][
                        "all_completed_workflow_sum_JCT_reduction_s"
                    ] > 0
                    for factor in (.75, 1., 1.5, 2.)
                )
                for row in population
            ),
            "factors": {},
        }
        for factor in (.75, 1., 1.5, 2.):
            values = [row["cost_sensitivity"][str(factor)] for row in population]
            values = [row for row in values if row["applicable"]]
            summaries[name]["factors"][str(factor)] = {
                "applicable_opportunities": len(values),
                "positive_all_completed_workflow_sum": sum(
                    row["all_completed_workflow_sum_JCT_reduction_s"] > 0 for row in values
                ),
                "positive_sum_and_positive_measured_mean": sum(
                    row["all_completed_workflow_sum_JCT_reduction_s"] > 0
                    and row["measured_workflow_mean_JCT_reduction_s"] > 0 for row in values
                ),
                "all_completed_workflow_sum_JCT_reduction_s": case_distribution(
                    row["all_completed_workflow_sum_JCT_reduction_s"] for row in values
                ),
                "measured_mean_JCT_reduction_s": case_distribution(
                    row["measured_workflow_mean_JCT_reduction_s"] for row in values
                ),
            }
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(registration), "fixed_complete_measured_workflows": len(measured),
        "fixed_complete_measured_requests": execution["measured_requests"],
        "baseline_measured_mean_JCT_s": statistics.mean(baseline_jct.values()) / 1000.,
        "observed_closed_workflows": len(model["ends"]), "observed_DAG_nodes": len(baseline),
        "baseline_exactly_reproduced": True, "nominal_local_decode_budget_ms": budget_ms,
        "maximum_cases": maximum_cases, "counts": {**dict(counts), **dict(worker_counts)},
        "summaries": summaries, "local_projections": results,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            registration, cohort_path, tokenizer_path, status_path, Path(__file__),
            ROOT / "scripts/audit_resident_decode_prefill_frontiers.py",
            ROOT / "scripts/verify_measured_execution.py",
            ROOT / "beliefkv/experiments/causal_replay.py",
        )},
        "scope": [
            "Every local projection starts from the same observed execution; their gains must not be added.",
            "Recorded dependency lags and tool waits stay fixed; JOIN completion is recomputed from all predecessors.",
            "The targeted request uses future output length, so these actions are offline oracle screens.",
            "All direct participants must have observed complete workflows; censored cases are counted and excluded.",
            "Delayed prefill requests pay the whole projected decode interval; same-branch shifts are not added repeatedly.",
            "The rest of the shared GPU schedule, admission, batch shapes, KV growth and queues are not resimulated.",
            "Flanking decode worker costs are proxies, not calibrated costs for counterfactual batches.",
            "Missing attempt/session evidence remains unknown; this screen grants no physical execution authority.",
            "The projection has neither a throughput prediction nor a global JCT bound or measured performance claim.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "tokenizer-config", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--maximum-cases", type=int, default=256)
    parser.add_argument("--decode-budget-ms", type=float, default=500.)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19 or args.maximum_cases < 1
        or not 0 < args.decode_budget_ms < math.inf
    ):
        parser.error("preserve evidence and use nice19 CPU-only execution with positive limits")
    result = inspect(
        args.registration, args.cohort, args.tokenizer_config,
        maximum_cases=args.maximum_cases, budget_ms=args.decode_budget_ms,
    )
    write(args.output, result)
    print(json.dumps({
        "counts": result["counts"], "summaries": result["summaries"],
        "baseline_measured_mean_JCT_s": result["baseline_measured_mean_JCT_s"],
    }), flush=True)


if __name__ == "__main__":
    main()
