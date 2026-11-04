"""Screen sibling adjacency on the same recorded, disabled-R5 resume choices."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import (
    InvocationState, JoinMode, RuntimeCausalContextGraph,
)
from beliefkv.core.events import RuntimeEvent, is_unobserved_join_event
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.paper_baselines import ThunderProgram
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet
from beliefkv.runtime.reference_thunder_admission import ReferenceThunderAdmission
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_size_selection import EVENT
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def replay(row, groups):
    if row.get("rank_before_category") or any(
        entry[2] != 1 or entry[5] is not None for entry in row["ordered"]
    ):
        raise ValueError("require the constant-rank R5 reference before group bias")
    policy = WorkflowSizeWorkingSet(
        max(1, row["capacity_tokens"]), rank=lambda identity, now: 1,
        sibling_group=groups.get, observe_ready=lambda item: True,
    )
    policy.programs = {
        owner: ThunderProgram(
            owner, tokens, steps=1 if category == 1 else 2,
            acting=category == 2, pending=True, queue_sequence=sequence,
        )
        for owner, category, _rank, tokens, sequence, _group in row["ordered"]
    }
    policy._resume_now = row["monotonic_s"]
    policy._resume()
    choice = policy.last_selection
    return {
        "ordered": [entry[0] for entry in choice["ordered"]],
        "selected": list(choice["selected_in_priority_order"]),
        "selected_tokens": choice["selected_tokens"],
    }


def memberships(graph, row):
    metadata = {
        item["key"]["invocation_id"]: item["key"]
        for item in row.get("candidates", ())
    }
    ready = row.get("logical_ready_paused_before_resume", {})
    groups = {}
    for join in sorted(
        graph.joins.values(), key=lambda item: (len(item.member_invocation_ids), item.join_id),
    ):
        if (
            join.mode is not JoinMode.ALL
            or not 2 <= len(join.member_invocation_ids) <= 24
            or not any(
                ReferenceThunderAdmission._waiting_on_join(
                    None, graph.invocations.get(owner), join,
                )
                for owner in join.waiter_invocation_ids
            )
        ):
            continue
        for owner in join.member_invocation_ids - join.completed_member_ids:
            key = metadata.get(owner)
            child = graph.invocations.get(owner)
            context = graph.contexts.get(key["context_id"]) if key is not None else None
            if (
                owner not in groups and ready.get(owner) is True
                and key is not None and child is not None and context is not None
                and child.workflow_id == key["root_workflow_id"] == join.workflow_id
                and child.context_id == key["context_id"]
                and context.epoch == key["context_epoch"]
                and child.state in (InvocationState.READY, InvocationState.RUNNING_LLM)
                and not child.active_tool_calls and not child.blocking_child_ids
            ):
                groups[owner] = join.join_id
    return groups


def inspect(choices, events, measured):
    graph = RuntimeCausalContextGraph(strict_timestamps=False)
    events = sorted(events, key=lambda item: (item.ts_ms, item.attributes.get("sequence", 0)))
    position, counts, decisions = 0, Counter(), []
    for row in choices:
        when = row["monotonic_s"] * 1000.
        while position < len(events) and events[position].ts_ms <= when:
            event = events[position]
            if is_unobserved_join_event(event):
                raise ValueError("an inferred JOIN cannot support the observed sibling screen")
            graph.apply(event)
            position += 1
        reference = replay(row, {})
        if (
            reference["ordered"] != [entry[0] for entry in row["ordered"]]
            or reference["selected"] != row["selected_in_priority_order"]
            or reference["selected_tokens"] != row["selected_tokens"]
        ):
            raise ValueError("the recorded reference cannot be reproduced exactly")
        groups = memberships(graph, row)
        alternative = replay(row, groups)
        changed = set(reference["selected"]) ^ set(alternative["selected"])
        metadata = {
            item["key"]["invocation_id"]: item["key"]
            for item in row.get("candidates", ())
        }
        counts["reproduced_periodic_choices"] += 1
        counts["choices_with_multiple_ready_members_of_a_join"] += any(
            list(groups.values()).count(join) >= 2 for join in set(groups.values())
        )
        counts["sibling_order_changes"] += alternative["ordered"] != reference["ordered"]
        counts["sibling_selected_set_changes"] += bool(changed)
        counts["set_changes_touching_measured_workflows"] += any(
            metadata[owner]["root_workflow_id"] in measured for owner in changed
        )
        if groups:
            decisions.append({
                "monotonic_s": row["monotonic_s"], "capacity_tokens": row["capacity_tokens"],
                "reference": reference, "sibling_adjacency": alternative,
                "groups": groups, "changed_invocations": sorted(changed),
                "keys": metadata,
            })
    return {"counts": dict(counts), "decisions_with_join_members": decisions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use nice19 CPU-only execution")
    execution = verify_execution(args.registration, args.cohort)
    plan = json.loads(args.registration.read_bytes())
    if sha256_file(ROOT / "beliefkv/policy/workflow_size_resume.py") != (
        plan["source_sha256"].get("beliefkv/policy/workflow_size_resume.py")
    ):
        raise ValueError("the replay core differs from the frozen policy")
    cohort = json.loads(args.cohort.read_bytes())
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    choices = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == EVENT
    ]
    result = inspect(
        choices, (
            RuntimeEvent.from_dict(row)
            for row in reader(arm / "replay/runtime_events.replay.jsonl")
        ), set(cohort["measured_workflow_ids"]),
    )
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        registration=str(args.registration.resolve()),
        complete_measured_workflows=len(cohort["measured_workflow_ids"]),
        complete_measured_requests=execution["measured_requests"],
        input_jsonl_prefixes=reader.prefixes,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.cohort, Path(__file__),
            ROOT / "beliefkv/policy/workflow_size_resume.py",
            ROOT / "beliefkv/control/causal_graph.py",
            ROOT / "beliefkv/runtime/reference_thunder_admission.py",
        )},
        scope=[
            "The actual periodic candidate order, selection and logical budget are first reproduced exactly.",
            "Only sibling adjacency changes; work ranks, candidate population and budget stay fixed.",
            "JOINs come from the published observed-event timeline, not the server's unrecorded apply clock.",
            "Exact ingress timing, observed-group support, post-resume pressure and physical fit are not reconstructed.",
            "A changed reference is not an achieved JOIN advance, JCT saving or independent performance upper bound.",
            "The complete execution includes warmup and background; measurement membership only annotates changes.",
        ],
        GPU_action=False, performance_accepted=False,
    )
    write(args.output, result)
    print(json.dumps(result["counts"]), flush=True)


if __name__ == "__main__":
    main()
