"""Bind each completed arm's configuration audit to its own immutable source."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path


HERE = Path(__file__).resolve().parent


def main():
    output = HERE / "observer_compatibility_review.json"
    if output.exists():
        raise FileExistsError("preserve original observer compatibility evidence")
    helper = HERE / "complete_review.py"
    spec = importlib.util.spec_from_file_location("demand_registered_observers", helper)
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    cases, observations = [], {}
    for name, registration, label, tree in review.SPECS:
        plan = review.causal.load(registration)
        arm = Path(plan["run_root"]) / label
        review.completed(plan, registration, label, tree)
        review.drain_and_exit(arm)
        observations[name] = review.observe_registered(plan, registration, label, arm)
        cases.append({
            "case": "exact_frozen_observer_" + name, "passed": True,
            "native_engine": tree,
        })
    plan = review.causal.load(review.PREADMISSION)
    arm = Path(plan["run_root"]) / "full"
    try:
        review.causal.observed_configuration(plan, "full", arm, final=True)
    except ValueError as error:
        if str(error) != "reference ThunderAgent admission differs from declared Full composition":
            raise
        cases.append({
            "case": "new_sibling_validator_still_rejects_old_policy",
            "passed": True, "original_rejection": str(error),
        })
    else:
        raise AssertionError("new reference validator was unexpectedly weakened")
    current = review.causal.load(review.FULL)
    state = review.causal.qualified.last_runtime_state(
        Path(current["run_root"]) / "full/opportunities/admission_opportunities.jsonl",
    )
    review.pipeline_state(state)
    cases.append({"case": "current_pipeline_settings_still_required", "passed": True})
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual completed arms and exact content-bound configuration observers.",
        "passing_cases": len(cases), "cases": cases, "observations": observations,
        "inputs_sha256": {
            str(path): review.sha256_file(path) for path in (
                Path(__file__), helper, HERE / "initial_review_failures.json",
                review.FULL, review.CONTROL, review.PREADMISSION,
            )
        },
        "current_validator_changed": False,
        "gpu_experiment_changed": False,
        "performance_accepted": False,
    }
    review.causal.reservation.write_json(output, result)
    print({"passing_cases": len(cases), "observers": observations})


if __name__ == "__main__":
    main()
