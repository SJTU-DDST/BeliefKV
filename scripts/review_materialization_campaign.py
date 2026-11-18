"""Compare completed matching materialization arms without re-running service."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.plot_workflow_cohort_comparison import render
from scripts.review_sustained_oracle import compare_cohorts
from scripts.run_materialization_campaign import LABELS, canonical_work, load
from scripts.train_workflow_work import write


def review(plan_path, output, *, baselines=False):
    if output.exists():
        raise FileExistsError("preserve earlier result reviews")
    plan = load(plan_path)
    proofs, reports, capacities, pins = {}, {}, {}, {}
    labels = LABELS if baselines else LABELS[:2]
    for label in labels:
        stage = plan["stages"][label]
        proof_path = plan_path.parent / (label + "_execution_proof.json")
        report_path = Path(stage["review"]) / "cohort.json"
        proofs[label], reports[label] = load(proof_path), load(report_path)
        if proofs[label].get("actual_owned_exit_verified") is not True:
            raise ValueError("wait for actual measured completion and owned exit")
        capacity_path = Path(stage["run_root"]) / stage["arm"] / "server/native_capacity_census.json"
        capacities[label] = load(capacity_path)["capacity"]
        pins.update({str(path): sha256_file(path) for path in (proof_path, report_path, capacity_path)})
    for label in labels:
        if canonical_work(proofs["control"]["work"]) != canonical_work(proofs[label]["work"]):
            raise ValueError("measured native work changed")
        if capacities["control"] != capacities[label]:
            raise ValueError("actual resource capacities changed")
    names = {
        "control": "Full control", "pressure": "Full + materialization",
        "thunderagent_acting_decay": "ThunderAgent acting-decay",
        "native_matched": "Native SGLang",
    }
    result = compare_cohorts(reports["control"], reports["pressure"], minimum_jct_gain=.01)
    result.update(
        scope="first_child_KV_materialization_only_matched_pair",
        inputs_sha256={**pins, str(plan_path): sha256_file(plan_path),
                       str(Path(__file__)): sha256_file(Path(__file__))},
        actual_capacities_equal=True, complete_measured_work_equal=True,
        recomputation_and_decode_width_acceptance_still_require_runtime_analysis=True,
        performance_accepted=False, goal_complete=False,
    )
    write(output / "comparison.json", result)
    if baselines:
        for label in LABELS[2:]:
            for candidate in LABELS[:2]:
                baseline = compare_cohorts(
                    reports[label], reports[candidate], minimum_jct_gain=.01,
                )
                baseline.update(
                    reference=label, candidate=names[candidate],
                    reference_arm=label, candidate_arm=candidate,
                    inputs_sha256=result["inputs_sha256"],
                    actual_capacities_equal=True, complete_measured_work_equal=True,
                    performance_accepted=False, goal_complete=False,
                )
                suffix = (
                    "_control_comparison.json" if candidate == "control" else "_comparison.json"
                )
                write(output / (label + suffix), baseline)
    render(
        {names[label]: reports[label] for label in labels},
        [Path(plan["stages"][label]["review"]) / "cohort.json" for label in labels],
        output / "figures",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baselines", action="store_true")
    args = parser.parse_args()
    review(args.plan.resolve(), args.output.resolve(), baselines=args.baselines)


if __name__ == "__main__":
    main()
