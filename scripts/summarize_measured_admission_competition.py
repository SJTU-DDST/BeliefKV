"""Summarize decision-time competition and fixed-cohort participation on CPU."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write


def mean(values):
    return sum(values) / len(values) if values else None


def summarize(report, cohort):
    measured = cohort["measured_workflow_ids"]
    if not measured or len(set(measured)) != len(measured):
        raise ValueError("require a nonempty fixed cohort without duplicate members")
    phase_info = report["observation_phases"]
    origin = phase_info["origin_monotonic_ms"] / 1000.
    if not math.isfinite(origin):
        raise ValueError("require an observed finite replay origin")
    phases = phase_info["phases"]
    common = phases["fixed_common_throughput_window"]
    if [common["start_s"], common["end_s"]] != cohort["throughput_window_offset_seconds"]:
        raise ValueError("control phases differ from the fixed cohort window")
    controls = {}
    for row in report["control_observations"]:
        when = row["monotonic_s"]
        ready, fitting, workflows = (
            row[name] for name in ("ready_paused_agents", "fitting_ready_paused_agents", "fitting_workflows")
        )
        if (
            not math.isfinite(when) or when in controls
            or any(type(value) is not int for value in (ready, fitting, workflows))
            or not 0 <= workflows <= fitting <= ready
            or type(row["cross_workflow_logical_set_competition"]) is not bool
        ):
            raise ValueError("invalid or ambiguous decision-time control observation")
        controls[when] = row
    decisions = {}
    for row in report["competition_decisions"]:
        when, candidates = row["monotonic_s"], row["fitting_candidates"]
        owners = [item["invocation_id"] for item in candidates]
        workflows = {item["root_workflow_id"] for item in candidates}
        observed = controls.get(when)
        if (
            when in decisions or len(owners) != len(set(owners)) or len(workflows) < 2
            or observed is None or observed["cross_workflow_logical_set_competition"] is not True
            or len(candidates) != observed["fitting_ready_paused_agents"]
            or len(workflows) != observed["fitting_workflows"]
        ):
            raise ValueError("competition candidates differ from their exact control boundary")
        decisions[when] = row
    if set(decisions) != {
        when for when, row in controls.items() if row["cross_workflow_logical_set_competition"]
    }:
        raise ValueError("a competing control lacks its exact candidate identities")
    summaries = {}
    for name, phase in phases.items():
        left, right = phase["start_s"], phase["end_s"]
        selected = [
            row for when, row in controls.items()
            if when - origin >= left and (right is None or when - origin < right)
        ]
        if len(selected) != phase["actual_periodic_controls"]:
            raise ValueError("phase control count differs from its recorded boundaries")
        competing = [row for row in selected if row["cross_workflow_logical_set_competition"]]
        population = [decisions[row["monotonic_s"]]["fitting_candidates"] for row in competing]
        measured_agents = [
            [item for item in group if item["root_workflow_id"] in measured]
            for group in population
        ]
        measured_workflows = [{item["root_workflow_id"] for item in group} for group in measured_agents]
        participation = Counter(workflow for group in measured_workflows for workflow in group)
        present = [index for index, group in enumerate(measured_agents) if group]
        summaries[name] = {
            "start_s": left, "end_s": right,
            "observed_through_offset_s": phase["observed_through_offset_s"],
            "complete_phase_observed": phase["complete_phase_observed"],
            "controls": len(selected),
            "mean_ready_paused_per_control": mean([row["ready_paused_agents"] for row in selected]),
            "mean_individually_fitting_per_control": mean(
                [row["fitting_ready_paused_agents"] for row in selected]
            ),
            "competition_controls": len(competing),
            "competition_fraction": len(competing) / len(selected) if selected else None,
            "mean_candidates_when_competing": mean([len(group) for group in population]),
            "mean_workflows_when_competing": mean(
                [len({item["root_workflow_id"] for item in group}) for group in population]
            ),
            "competing_candidate_histogram": dict(sorted(Counter(map(len, population)).items())),
            "competition_controls_with_measured_agents": len(present),
            "competition_controls_with_two_measured_agents": sum(len(group) >= 2 for group in measured_agents),
            "competition_controls_with_two_measured_workflows": sum(
                len(group) >= 2 for group in measured_workflows
            ),
            "mean_all_candidates_when_measured_participates": mean(
                [len(population[index]) for index in present]
            ),
            "mean_measured_agents_when_measured_participates": mean(
                [len(measured_agents[index]) for index in present]
            ),
            "competition_controls_per_measured_workflow": {
                workflow: participation[workflow] for workflow in measured
            },
        }
    return {
        "phases": summaries,
        "all_measured_completed": phase_info["all_measured_completed"],
        "scope": {
            "means_weight_actual_decisions_not_wall_time": True,
            "logical_individual_fit_does_not_prove_native_physical_fit": True,
            "repeated_controls_not_unique_requests_or_independent_choices": True,
            "sibling_agents_separate_from_distinct_measured_workflows": True,
            "phase_windows_overlap_and_must_not_be_added": True,
            "unobserved_tails_not_filled_with_zeroes": True,
            "GPU_action": False, "performance_gain_proven": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--periodic-report", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior competition summaries")
    report = json.loads(args.periodic_report.read_bytes())
    cohort = json.loads(args.cohort.read_bytes())
    if report.get("phase_inputs_sha256", {}).get(str(args.cohort.resolve())) != sha256_file(args.cohort):
        raise ValueError("periodic audit did not bind this exact fixed cohort")
    result = summarize(report, cohort)
    result["inputs_sha256"] = {
        str(path.resolve()): sha256_file(path)
        for path in (args.periodic_report, args.cohort, Path(__file__))
    }
    result["input_jsonl_prefixes"] = report["input_jsonl_prefixes"]
    write(args.output, result)
    print(json.dumps(result["phases"]))


if __name__ == "__main__":
    main()
