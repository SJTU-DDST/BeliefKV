"""Compare completed matching materialization arms without re-running service."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.subagent_materialization import PRESSURE_POLICIES
from scripts.plot_workflow_cohort_comparison import render
from scripts.review_predictive_thunder_cohort import matching_serving_traffic
from scripts.review_sustained_oracle import compare_cohorts
from scripts.run_materialization_campaign import LABELS, canonical_work, load
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


D3_RUNTIME_SOURCES = {
    "beliefkv/runtime/subagent_materialization.py",
    "beliefkv/runtime/sglang_v0520_runtime.py",
}


def reused_control_stages(plan, registration, review_dir):
    pressure = plan["stages"]["pressure"]
    control, candidate = load(registration), load(Path(pressure["registration"]))
    if any(row["order"] != ["full"] for row in (control, candidate)):
        raise ValueError("materialization comparison requires Full serving in both arms")
    matching_serving_traffic(control, candidate)
    policy = "BELIEFKV_SUBAGENT_MATERIALIZATION"
    before, after = (row["reference_environment"] for row in (control, candidate))
    if (
        before.get(policy) != "off" or after.get(policy) not in PRESSURE_POLICIES
        or {key: value for key, value in before.items() if key != policy}
        != {key: value for key, value in after.items() if key != policy}
    ):
        raise ValueError("reused control differs in serving settings beyond D3")
    before_sources, after_sources = (row["source_sha256"] for row in (control, candidate))
    runtime_sources = {path for path in before_sources.keys() | after_sources.keys()
                       if path.startswith("beliefkv/")}
    if not D3_RUNTIME_SOURCES <= before_sources.keys() & after_sources.keys() or any(
        before_sources.get(path) != after_sources.get(path)
        for path in runtime_sources - D3_RUNTIME_SOURCES
    ):
        raise ValueError("reused control differs in serving sources beyond D3")
    if pressure["arm"] != "full" or Path(pressure["run_root"]) != Path(candidate["run_root"]):
        raise ValueError("pressure stage does not describe its registered run")
    return {
        "control": {
            "registration": str(registration),
            "review": str(review_dir),
            "run_root": control["run_root"],
            "arm": "full",
        },
        "pressure": pressure,
    }


def review(plan_path, output, *, baselines=False, reuse_control=None):
    if output.exists():
        raise FileExistsError("preserve earlier result reviews")
    plan = load(plan_path)
    stages = plan["stages"]
    if reuse_control is not None:
        if baselines or plan.get("GPU_stage_order") != ["pressure"] or set(stages) != {"pressure"}:
            raise ValueError("reuse a completed control with a pressure-only campaign")
        stages = reused_control_stages(plan, *reuse_control)
    proofs, reports, capacities, pins = {}, {}, {}, {}
    labels = LABELS if baselines else LABELS[:2]
    for label in labels:
        stage = stages[label]
        review_dir = Path(stage["review"])
        report_path = review_dir / "cohort.json"
        reports[label] = load(report_path)
        if reuse_control is None:
            proof_path = plan_path.parent / (label + "_execution_proof.json")
            proofs[label] = load(proof_path)
            pins[str(proof_path)] = sha256_file(proof_path)
        else:
            registration, cohort_path = Path(stage["registration"]), Path(plan["cohort"])
            proof = proofs[label] = verify_execution(registration, cohort_path)
            if proof.get("protocol") != "all_fixed_measured_workflows_completed":
                raise ValueError("reuse requires the fixed-cohort completion protocol")
            review_path = review_dir / "review.json"
            review_matches_execution(load(review_path), proof)
            certificate = Path(proof["certificate"])
            if (
                reports[label]["cohort"] != proof["cohort"]
                or reports[label]["measured_workflow_JCT_ms"] != load(certificate)["measured_JCT_ms"]
            ):
                raise ValueError("cohort report differs from the verified completion certificate")
            pins.update({
                str(path): sha256_file(path)
                for path in (registration, review_path, cohort_path, certificate)
            })
        if proofs[label].get("actual_owned_exit_verified") is not True:
            raise ValueError("wait for actual measured completion and owned exit")
        capacity_path = Path(stage["run_root"]) / stage["arm"] / "server/native_capacity_census.json"
        capacities[label] = load(capacity_path)["capacity"]
        pins.update({str(path): sha256_file(path) for path in (report_path, capacity_path)})
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
    if reuse_control is not None:
        result["reused_control_registration"] = stages["control"]["registration"]
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
        [Path(stages[label]["review"]) / "cohort.json" for label in labels],
        output / "figures",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baselines", action="store_true")
    parser.add_argument("--reuse-control", nargs=2, type=Path, metavar=("REGISTRATION", "REVIEW_DIR"))
    args = parser.parse_args()
    reuse = tuple(path.resolve() for path in args.reuse_control) if args.reuse_control else None
    review(args.plan.resolve(), args.output.resolve(), baselines=args.baselines, reuse_control=reuse)


if __name__ == "__main__":
    main()
