"""Read-only oracle local-choice audit; does not counterfactually predict JCT."""

import argparse
from collections import Counter
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet, ThunderProgram
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal

RANK = "oracle_remaining_model_tool_critical_path_seconds"


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def selection(row, ranks=None, *, reference=False):
    ordered = row["ordered"]
    if row["capacity_tokens"] <= 100:
        if ordered or row["selected_in_priority_order"] or row["selected_tokens"]:
            raise ValueError("oracle empty-budget fast path changed")
        return []
    groups = {item[0]: item[5][2] for item in ordered if item[5] is not None}
    scores = ranks if ranks is not None else {item[0]: item[2] for item in ordered}
    policy = ThunderActingDecayWorkingSet(row["capacity_tokens"]) if reference else WorkflowSizeWorkingSet(
        row["capacity_tokens"], rank=lambda owner, _now: scores[owner], sibling_group=groups.get,
    )
    policy.programs = {
        owner: ThunderProgram(
            owner, tokens, steps=1 if category == 1 else 2, acting=category == 2,
            queue_sequence=sequence,
        )
        for owner, category, _score, tokens, sequence, _group in ordered
    }
    policy._resume_now = row["monotonic_s"]
    policy._resume()
    if reference:
        return [item[0] for item in sorted(ordered, key=lambda item: (item[1], item[3], item[4]))
                if policy.programs[item[0]].active]
    if ranks is None:
        replayed = policy.last_selection
        if (
            list(replayed["selected_in_priority_order"]) != row["selected_in_priority_order"]
            or replayed["selected_tokens"] != row["selected_tokens"]
            or [list(item[:5]) + [list(item[5]) if item[5] else None]
                for item in replayed["ordered"]] != ordered
        ):
            raise ValueError("oracle recorded choice differs from its frozen local policy")
    return list(policy.last_selection["selected_in_priority_order"])


def inspect(row):
    if (
        row.get("event") != "workflow_size_resume_selection" or row.get("rank_kind") != RANK
        or row.get("physical_capacity_credit") is not False
        or not finite(row.get("monotonic_s"))
        or type(row.get("capacity_tokens")) is not int
        or type(row.get("selected_tokens")) is not int or row["selected_tokens"] < 0
    ):
        raise ValueError("require an explicitly declared oracle local decision")
    metadata = {item["key"]["invocation_id"]: item for item in row["candidates"]}
    if len(metadata) != len(row["candidates"]) or len({item[0] for item in row["ordered"]}) != len(row["ordered"]):
        raise ValueError("duplicate oracle candidate identity")
    for owner, category, score, tokens, sequence, group in row["ordered"]:
        if (
            owner not in metadata or category not in (0, 1, 2) or not finite(score)
            or type(tokens) is not int or tokens <= 0 or type(sequence) is not int or sequence < 0
            or group is not None and (
                len(group) != 3 or group[:2] != [category, score] or not group[2]
            )
        ):
            raise ValueError("invalid oracle rank, category, group or complete budget")
        prediction = metadata[owner]["prediction"]
        if (
            prediction["evidence"] != "oracle"
            or not all(finite(prediction.get(name)) for name in (
                "remaining_path_s", "model_only_path_s", "tool_and_fixed_delay_path_s",
                "remaining_work", "remaining_work_units",
            ))
            or not math.isclose(score, prediction["remaining_path_s"], abs_tol=1e-9)
            or prediction["remaining_path_s"] + 1e-7 < max(
                prediction["model_only_path_s"], prediction["tool_and_fixed_delay_path_s"],
            )
        ):
            raise ValueError("oracle decision is not bound to its model/tool path prediction")
    actual = selection(row)
    return {
        "actual": actual, "reference": selection(row, reference=True),
        "without_external_delays": selection(row, {
            owner: metadata[owner]["prediction"]["model_only_path_s"]
            for owner, *_rest in row["ordered"]
        }),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve previous oracle reviews")
    if complete_work(args.registration) is None:
        raise ValueError("oracle choice review requires complete work and actual owned exit")
    plan = causal.load(args.registration)
    for name in ("beliefkv/policy/workflow_size_resume.py", "beliefkv/policy/paper_baselines.py"):
        if sha256_file(ROOT / name) != plan["source_sha256"][name]:
            raise ValueError("local replay policy differs from the frozen GPU policy")
    arm = Path(plan["run_root"]) / "full"
    reader, counts, decisions = SealedRecords(), Counter(), []
    cpu, wall, control_cpu, control_wall = [], [], [], []
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "workflow_size_free_dispatch_order":
            if row.get("rank_kind") != RANK:
                raise ValueError("free dispatch did not use the declared oracle rank")
            counts["changed_dispatch_orders"] += 1
            for item in row["ordered"]:
                if not math.isclose(item["priority"][2], item["prediction"]["remaining_path_s"], abs_tol=1e-9):
                    raise ValueError("free dispatch rank differs from its actual oracle prediction")
            continue
        if row.get("event") != "workflow_size_resume_selection":
            continue
        values = inspect(row)
        counts["controls"] += 1
        counts["nonempty_controls"] += bool(row["ordered"])
        counts["multi_candidate_controls"] += len(row["ordered"]) > 1
        changed = set(values["actual"]) != set(values["reference"])
        tools = set(values["actual"]) != set(values["without_external_delays"])
        counts["changed_local_selected_sets_from_reference"] += changed
        counts["external_delay_changed_local_selected_sets"] += tools
        counts["selected_episodes"] += len(values["actual"])
        for field, target in (
            ("thread_cpu_us", cpu), ("wall_us", wall),
            ("control_thread_cpu_us", control_cpu), ("control_wall_us", control_wall),
        ):
            if not finite(row.get(field)):
                raise ValueError("oracle control cost is not observed")
            target.append(row[field])
        if changed or tools:
            decisions.append({"monotonic_s": row["monotonic_s"], **values})
    latest = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    predictor = latest["agent_admission"]["workflow_work_predictor"]
    if (
        predictor["future_tool_durations_used"] is not True or predictor["error"]
        or any(predictor["remaining_work_units"].values())
    ):
        raise ValueError("complete runtime oracle did not consume the declared model/tool work")
    for path, prefix in reader.prefixes.items():
        if Path(path).stat().st_size != prefix["complete_prefix_bytes"]:
            raise ValueError("complete oracle journal changed")
    causal.reservation.write_json(args.output, {
        "counts": dict(counts), "changed_local_choices": decisions,
        "resume_sort_CPU_us": distribution(cpu), "resume_sort_wall_us": distribution(wall),
        "complete_control_CPU_us": distribution(control_cpu),
        "complete_control_wall_us": distribution(control_wall),
        "complete_control_CPU_s": sum(control_cpu) / 1e6,
        "complete_control_wall_s": sum(control_wall) / 1e6,
        "runtime_predictor": predictor,
        "scope": (
            "Exact local choices hold observed candidates, categories, sibling membership and "
            "decayed budget fixed. Removing external delays removes both tools and fixed replay "
            "gaps. This does not simulate subsequent pressure, physical capacity or counterfactual JCT."
        ),
        "input_jsonl_prefixes": reader.prefixes,
        "registration_sha256": sha256_file(args.registration),
        "operator_sha256": sha256_file(Path(__file__)),
        "GPU_action": False, "performance_accepted": False,
    })
    print(dict(counts))


if __name__ == "__main__":
    main()
