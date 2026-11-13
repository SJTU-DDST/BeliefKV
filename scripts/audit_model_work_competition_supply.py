"""Measure model-work diversity at the Full's exact competing resume boundaries."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_free_dispatch_service import identity
from scripts.audit_live_work_prediction import competitions
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def summarize(decisions, measured):
    counts = Counter(boundaries=len(decisions))
    populations, request_sets, participating = [], set(), set()
    for decision in decisions:
        ordinary = []
        for candidate in decision["candidates"]:
            if not candidate["selector_metadata_valid"]:
                counts["unavailable_candidate_labels"] += 1
                continue
            if candidate["protected_JOIN"]:
                counts["protected_JOIN_candidate_observations"] += 1
                continue
            work = candidate["recorded_remaining_model_work"]
            if type(work) not in (float, int) or not math.isfinite(work) or work < 0:
                raise ValueError("require finite nonnegative recorded remaining model work")
            ordinary.append(candidate)
        if len(ordinary) < 2:
            continue
        workflows = {candidate["key"]["root_workflow_id"] for candidate in ordinary}
        if len(workflows) != len(ordinary):
            raise ValueError("model-work diversity counts distinct workflows once")
        counts["ordinary_multi_workflow_boundaries"] += 1
        populations.append(len(ordinary))
        request_sets.add(tuple(sorted(identity(candidate["key"], grant=True) for candidate in ordinary)))
        members = workflows & measured
        participating.update(members)
        counts["boundaries_with_measured_workflow"] += bool(members)
        counts["boundaries_with_two_measured_workflows"] += len(members) >= 2
        work = [candidate["recorded_remaining_model_work"] for candidate in ordinary]
        counts["boundaries_with_zero_remaining_work"] += min(work) == 0
        if min(work) > 0:
            ratio = max(work) / min(work)
            counts["boundaries_with_model_work_ratio_ge_2"] += ratio >= 2.
            counts["boundaries_with_model_work_ratio_ge_5"] += ratio >= 5.
            counts["measured_participates_with_model_work_ratio_ge_2"] += bool(members) and ratio >= 2.
    return {
        **counts,
        "unique_ordinary_candidate_request_sets": len(request_sets),
        "mean_ordinary_workflows": sum(populations) / len(populations) if populations else None,
        "distinct_measured_workflows_participating": len(participating),
        "measured_workflow_ids_participating": sorted(participating),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "periodic-report", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous supply evidence")
    registration, periodic, cohort = (
        json.loads(path.read_bytes()) for path in (args.registration, args.periodic_report, args.cohort)
    )
    if (
        periodic["registration"] != str(args.registration.resolve())
        or periodic["registration_sha256"] != sha256_file(args.registration)
        or periodic["phase_inputs_sha256"].get(str(args.cohort.resolve())) != sha256_file(args.cohort)
    ):
        raise ValueError("supply must bind the same actual periodic report and private cohort")
    sources = (
        ROOT / "beliefkv/runtime/workflow_work_oracle.py",
        ROOT / "beliefkv/runtime/workflow_oracle_admission.py",
        ROOT / "beliefkv/runtime/workflow_size_admission.py",
    )
    if any(
        sha256_file(path) != registration["source_sha256"][str(path.relative_to(ROOT))]
        for path in sources
    ):
        raise ValueError("recorded model-work semantics differ from the serving source")
    reader = SealedRecords()
    reader.prefixes = dict(periodic["input_jsonl_prefixes"])
    journal = Path(registration["run_root"]) / "full/opportunities/admission_opportunities.jsonl"
    decisions, controls = competitions(
        row for row in reader(journal)
        if row.get("event") in ("agent_periodic_admission_decision", "workflow_size_resume_selection")
    )
    if controls != periodic["counts"]["controls"] or {
        row["monotonic_s"] for row in decisions
    } != {row["monotonic_s"] for row in periodic["competition_decisions"]}:
        raise ValueError("model-work labels must cover the exact same competing boundaries")
    origin = periodic["observation_phases"]["origin_monotonic_ms"] / 1000.
    measured = set(cohort["measured_workflow_ids"])
    phases = {}
    for name, phase in periodic["observation_phases"]["phases"].items():
        selected = [
            row for row in decisions if row["monotonic_s"] - origin >= phase["start_s"]
            and (phase["end_s"] is None or row["monotonic_s"] - origin < phase["end_s"])
        ]
        phases[name] = {
            **summarize(selected, measured),
            "complete_phase_observed": phase["complete_phase_observed"],
            "observed_through_offset_s": phase["observed_through_offset_s"],
        }
    write(args.output, {
        "counts": summarize(decisions, measured), "phases": phases, "decisions": decisions,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.periodic_report, args.cohort, Path(__file__),
            ROOT / "scripts/audit_live_work_prediction.py", *sources,
        )},
        "scope": {
            "actual_periodic_logical_competitions_not_all_admission_paths": True,
            "recorded_runtime_remaining_model_work_not_tool_critical_path_seconds": True,
            "distinct_ordinary_workflows_once_per_boundary": True,
            "protected_JOIN_and_unknown_labels_excluded": True,
            "native_physical_feasibility_proven": False,
            "current_R5_combination_selection_counterfactual": False,
            "whole_workflow_service_or_JCT_gain_proven": False,
        },
        "GPU_action": False, "serving_changed": False,
    })
    print(json.dumps({"counts": summarize(decisions, measured), "phases": phases}))


if __name__ == "__main__":
    main()
