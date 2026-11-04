"""Label observed JOIN alternatives with future-work branch paths, offline only."""

import argparse
from collections import Counter
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact, scan_remaining_paths
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def target_ancestors(graph, target_node_id):
    positions = {node["node_id"]: index for index, node in enumerate(graph)}
    if target_node_id not in positions:
        raise ValueError("branch return target is absent from the bound workflow")
    pending, needed = [positions[target_node_id]], set()
    while pending:
        index = pending.pop()
        if index in needed:
            continue
        needed.add(index)
        pending.extend(graph[index]["dependencies"])
    ordered = sorted(needed)
    remap = {old: new for new, old in enumerate(ordered)}
    return [
        {**graph[index], "dependencies": [remap[dep] for dep in graph[index]["dependencies"]]}
        for index in ordered
    ]


class BranchOracle:
    def __init__(self, artifact, plan):
        self.artifact = artifact
        self.events = {
            node["event_id"]: (workflow, node)
            for workflow, graph in artifact.graphs.items() for node in graph
            if node["event_id"] is not None
        }
        self.request_nodes = {
            node["request_id"]: node["node_id"]
            for graph in artifact.graphs.values() for node in graph
            if node["request_id"] is not None
        }
        self.returns, self.joins, self.tool_durations = {}, {}, {}
        for source in plan.nodes:
            event = source.event
            if event is None:
                continue
            workflow, node = self.events[event.event_id]
            if workflow != event.workflow_id:
                raise ValueError("source event differs from oracle workflow ownership")
            if event.kind is Kind.RETURN:
                if not event.invocation_id or event.invocation_id in self.returns:
                    raise ValueError("branch needs one explicit invocation RETURN")
                self.returns[event.invocation_id] = workflow, node["node_id"]
            elif event.kind is Kind.JOIN_CREATE:
                self.joins[event.join_id] = tuple(event.member_invocation_ids)
            elif event.kind is Kind.TOOL_END:
                self.tool_durations[self.tool_key(event)] = node["delay_s"]
        self.prefixes, self.done_at, self.generated = {}, {}, {}
        self.active_tools, self.returned, self.observed_joins = {}, set(), set()
        self.counts = Counter()

    @staticmethod
    def tool_key(event):
        identity = event.attributes.get("tool_run_id") or event.attributes.get("tool_call_id")
        if not identity or not event.invocation_id:
            raise ValueError("tool needs its observed invocation and run identity")
        return event.workflow_id, event.invocation_id, identity

    def observe(self, event):
        bound = self.events.get(event.event_id)
        if bound is not None:
            workflow, node = bound
            if workflow != event.workflow_id:
                raise ValueError("observed event changed workflow")
            self.done_at[node["node_id"]] = event.ts_ms / 1000.
        if event.kind is Kind.LLM_RESULT:
            request = event.attributes["request_id"]
            item = self.artifact.requests[request]
            if any(getattr(event, name) != item[name] for name in (
                "workflow_id", "invocation_id", "context_id",
            )):
                raise ValueError("completion changed bound request ownership")
            self.done_at[self.request_nodes[request]] = event.ts_ms / 1000.
        elif event.kind is Kind.TOOL_START:
            self.active_tools[self.tool_key(event)] = event.ts_ms / 1000.
        elif event.kind is Kind.TOOL_END:
            self.active_tools.pop(self.tool_key(event), None)
        elif event.kind is Kind.RETURN:
            self.returned.add(event.invocation_id)
        elif event.kind is Kind.JOIN_WAIT:
            self.observed_joins.add(event.join_id)

    def output(self, batch):
        when = batch["complete_monotonic_ms"] / 1000.
        for member in batch["request_samples"]:
            request = member["request_id"]
            item = self.artifact.requests.get(request)
            if item is None:
                continue
            if any(member.get(name) != item[name] for name in (
                "workflow_id", "invocation_id", "context_id",
            )):
                raise ValueError("native output changed bound request ownership")
            count = member.get("output_tokens_after")
            if type(count) is not int or not self.generated.get(request, 0) <= count <= item["output_tokens"]:
                raise ValueError("invalid cumulative observed output")
            self.generated[request] = count
        return when

    def branch(self, invocation, now):
        if invocation in self.returned:
            return None
        target = self.returns.get(invocation)
        if target is None:
            return None
        if invocation not in self.prefixes:
            workflow, node_id = target
            self.prefixes[invocation] = target_ancestors(self.artifact.graphs[workflow], node_id)
        path, model, external = scan_remaining_paths(
            self.prefixes[invocation], self.artifact.requests, self.done_at, self.generated,
            self.artifact.model_seconds_per_work_token, now,
            prefill_seconds_per_token=self.artifact.model_prefill_seconds_per_token,
            prefill_done_after_output=True,
        )
        tools = [
            max(0., self.tool_durations[key] - (now - began))
            for key, began in self.active_tools.items()
            if key[1] == invocation and key in self.tool_durations
        ]
        return {
            "remaining_path_proxy_s": path, "model_only_path_proxy_s": model,
            "external_only_path_s": external,
            "currently_active_tool_remaining_lower_bound_s": max(tools, default=0.),
        }


def single_cross_workflow_swaps(actual, alternatives, member, workflows):
    actual = set(actual)
    if member not in actual:
        return []
    result = []
    for alternative in alternatives:
        alternative = set(alternative)
        added = alternative - actual
        if actual - alternative != {member} or len(added) != 1:
            continue
        other = next(iter(added))
        if workflows[other] != workflows[member]:
            result.append(other)
    return sorted(set(result))


def inspect(choices, oracle, event_rows, native_batches):
    events = iter(
        RuntimeEvent.from_dict(row)
        for _index, row in sorted(
            enumerate(event_rows),
            key=lambda item: (item[1]["ts_ms"], item[1].get("sequence", item[0])),
        )
    )
    native = iter(native_batches)
    event, batch = next(events, None), next(native, None)
    counts, details = Counter(), []
    previous = -math.inf
    for choice in choices:
        now = choice["monotonic_s"]
        if now < previous:
            raise ValueError("JOIN choices are not in chronological order")
        previous = now
        while batch is not None and batch["complete_monotonic_ms"] / 1000. <= now:
            oracle.output(batch)
            batch = next(native, None)
        while event is not None and event.ts_ms / 1000. <= now:
            oracle.observe(event)
            event = next(events, None)
        if not choice["changed_JOIN_groups"]:
            continue
        counts["capacity_choices_with_JOIN_alternatives"] += 1
        candidate_workflows = {
            item["identity"]: item["workflow"] for item in choice["candidate_metadata"]
        }
        candidate_paths = {
            member: oracle.branch(member, now) for member in candidate_workflows
        }
        for group in choice["changed_JOIN_groups"]:
            identity = group["group"]
            if identity not in oracle.observed_joins:
                counts["JOIN_not_yet_observed_in_client_publication"] += 1
                continue
            members = oracle.joins[identity]
            unfinished = [member for member in members if member not in oracle.returned]
            if len(unfinished) != group["unfinished_members"]:
                counts["JOIN_member_snapshot_differs_from_client_publication"] += 1
                continue
            labels = {member: oracle.branch(member, now) for member in unfinished}
            if any(value is None for value in labels.values()):
                counts["missing_explicit_branch_RETURN_target"] += 1
                continue
            candidate_labels = {}
            for member in group["candidate_members"]:
                own = labels[member]
                other_path = max(
                    value["remaining_path_proxy_s"]
                    for identity, value in labels.items() if identity != member
                )
                other_tools = max(
                    (value["currently_active_tool_remaining_lower_bound_s"]
                     for identity, value in labels.items() if identity != member),
                    default=0.,
                )
                candidate_labels[member] = {
                    **own, "other_active_tool_remaining_lower_bound_s": other_tools,
                    "tool_only_intrinsic_slack_proxy_s": other_tools - own["remaining_path_proxy_s"],
                    "other_branch_path_proxy_s": other_path,
                    "full_branch_intrinsic_slack_proxy_s": other_path - own["remaining_path_proxy_s"],
                    "actually_selected": member in choice["actual_selected"],
                }
                replacements = []
                for competitor in single_cross_workflow_swaps(
                    choice["actual_selected"], choice["maximal_feasible_sets"],
                    member, candidate_workflows,
                ):
                    competitor_path = candidate_paths[competitor]
                    counts["single_cross_workflow_swap_observations"] += 1
                    if competitor_path is None:
                        counts["single_swap_without_explicit_competitor_RETURN_label"] += 1
                        continue
                    fits = (
                        competitor_path["remaining_path_proxy_s"]
                        <= candidate_labels[member]["full_branch_intrinsic_slack_proxy_s"]
                    )
                    counts["single_swap_fits_full_branch_intrinsic_slack_proxy"] += fits
                    counts["measured_JOIN_single_swap_fits_intrinsic_slack_proxy"] += (
                        fits and group["measured_workflow"]
                    )
                    replacements.append({
                        "competitor": competitor,
                        "competitor_workflow": candidate_workflows[competitor],
                        "competitor_branch_path_proxy_s": competitor_path["remaining_path_proxy_s"],
                        "fits_full_branch_intrinsic_slack_proxy": fits,
                    })
                candidate_labels[member]["single_cross_workflow_replacements"] = replacements
            positive = [
                member for member, value in candidate_labels.items()
                if value["tool_only_intrinsic_slack_proxy_s"] > 0
            ]
            counts["supported_JOIN_group_observations"] += 1
            counts["JOIN_group_observations_with_positive_intrinsic_tool_slack"] += bool(positive)
            counts["actually_selected_candidates_with_positive_intrinsic_tool_slack"] += sum(
                candidate_labels[member]["actually_selected"] for member in positive
            )
            path_positive = [
                member for member, value in candidate_labels.items()
                if value["full_branch_intrinsic_slack_proxy_s"] > 0
            ]
            counts["candidate_observations_with_positive_full_branch_intrinsic_slack"] += len(path_positive)
            counts["selected_candidates_with_positive_full_branch_intrinsic_slack"] += sum(
                candidate_labels[member]["actually_selected"] for member in path_positive
            )
            details.append({
                "selection_sequence": choice["selection_sequence"], "monotonic_s": now,
                "group": identity, "workflow_id": group["workflow_id"],
                "measured_workflow": group["measured_workflow"],
                "unfinished_members": unfinished, "branch_labels": labels,
                "candidate_labels": candidate_labels,
                "maximal_feasible_sets": choice["maximal_feasible_sets"],
            })
    # Exhaust the complete source so SealedRecords publishes its final digest.
    for _batch in native:
        pass
    return {
        "counts": dict(counts), "group_observations": details,
        "positive_intrinsic_tool_slack_s": distribution(
            value["tool_only_intrinsic_slack_proxy_s"]
            for row in details for value in row["candidate_labels"].values()
            if value["tool_only_intrinsic_slack_proxy_s"] > 0
        ),
        "positive_full_branch_intrinsic_slack_s": distribution(
            value["full_branch_intrinsic_slack_proxy_s"]
            for row in details for value in row["candidate_labels"].values()
            if value["full_branch_intrinsic_slack_proxy_s"] > 0
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "capacity-audit", "oracle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use nice19 CPU-only execution")
    registration = json.loads(args.registration.read_bytes())
    capacity = json.loads(args.capacity_audit.read_bytes())
    if capacity["inputs_sha256"].get(str(args.registration.resolve())) != sha256_file(args.registration):
        raise ValueError("capacity choices belong to another registration")
    if any(sha256_file(Path(path)) != digest for path, digest in capacity["inputs_sha256"].items()):
        raise ValueError("capacity audit source changed")
    graph, _, source_evidence = source_plan(Path(registration["source_arm"]))
    artifact = WorkflowWorkOracleArtifact.load(args.oracle, sha256_file(args.oracle))
    artifact.validate_source(Path(registration["source_arm"]), graph, source_evidence)
    oracle = BranchOracle(artifact, graph)
    reader = SealedRecords()
    arm = Path(registration["run_root"]) / "full"
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    native = (
        row for row in reader(arm / "server/runtime_audit.jsonl")
        if row.get("event") == "gpu_service_sample"
    )
    result = inspect(capacity["capacity_choices"], oracle, events, native)
    result.update(
        observed_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        input_jsonl_prefixes=reader.prefixes, source_evidence=source_evidence,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.capacity_audit, args.oracle, Path(__file__),
            ROOT / "beliefkv/runtime/workflow_work_oracle.py",
            ROOT / "scripts/audit_join_combination_opportunities.py",
        )},
        future_information_used=True, deployable=False, GPU_action=False,
        automatic_GPU_followup=False, performance_accepted=False,
        scope=[
            "Future fixed branch work and exact tool durations are offline oracle labels.",
            "Branch targets are explicit invocation RETURNs, not the whole workflow's finish.",
            "Only ancestors of each RETURN contribute; parallel unrelated siblings are excluded.",
            "Progress uses observed client publication and cumulative native outputs, not source service time.",
            "Client publication does not prove native event ingestion at the same instant; mismatched JOIN snapshots stay unknown.",
            "Model seconds reuse the bound historical token-work calibration, not a new per-context or batch execution predictor.",
            "Input is considered prefilling complete after its first reported output; partial input progress is not estimated.",
            "Active-tool remaining time is conditional on observed starts using the oracle duration; future tools grant no current slack lower bound.",
            "Intrinsic slack omits capacity waiting, migration and compute interference; it is not a safe online deferral allowance.",
            "Full-branch slack includes unknown future model and tool phases; the current-tool-only screen is reported separately.",
            "Competitor RETURN time is an intrinsic point proxy, not a high-side prediction of natural eligibility release.",
            "One-for-one swaps preserve the recorded maximal set and all required JOIN rights; other changes are excluded.",
            "Alternative sets retain the prior capacity audit's planning constraints, not immediate native feasibility.",
            "No alternative is selected or given a simulated JCT benefit; this is a branch-label and opportunity screen.",
        ],
    )
    write(args.output, result)
    print(json.dumps({
        "counts": result["counts"], "positive_intrinsic_tool_slack_s": result["positive_intrinsic_tool_slack_s"],
        "positive_full_branch_intrinsic_slack_s": result["positive_full_branch_intrinsic_slack_s"],
        "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
