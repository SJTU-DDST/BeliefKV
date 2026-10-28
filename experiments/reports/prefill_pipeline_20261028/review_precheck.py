"""Check actual completion gates before demand-pipeline performance review."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path


HERE = Path(__file__).resolve().parent


def main():
    output = HERE / "review_precheck.json"
    if output.exists():
        raise FileExistsError("preserve original completion-boundary evidence")
    helper = HERE / "complete_review.py"
    spec = importlib.util.spec_from_file_location("demand_pipeline_complete_precheck", helper)
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    cases = []

    def reject(name, action, reason):
        try:
            action()
        except ValueError as error:
            if reason not in str(error):
                raise
            cases.append({"case": name, "passed": True, "rejection": str(error)})
        else:
            raise AssertionError(name + ": invalid evidence accepted")

    full = review.causal.load(review.FULL)
    reject(
        "actual_live_full_is_not_complete",
        lambda: review.completed(
            full, review.FULL, "full", review.DEMAND_PREFILL_PIPELINE_TREE,
        ),
        "complete successful work",
    )
    contracts = []
    for name, registration, label, tree in review.SPECS:
        plan = review.causal.load(registration)
        contracts.append(review.common_contract(plan))
        if registration == review.FULL:
            continue
        review.completed(plan, registration, label, tree)
        cases.append({"case": "actual_complete_owned_exit_" + label, "passed": True})
    if any(contract != contracts[0] for contract in contracts[1:]):
        raise AssertionError("declared work, tool timing, capacity or seed contracts differ")
    cases.append({"case": "all_three_registered_common_work_contracts_match", "passed": True})
    control = review.causal.load(review.CONTROL)
    reject(
        "control_cannot_be_relabelled_pipeline",
        lambda: review.completed(
            control, review.CONTROL, "full", review.DEMAND_PREFILL_PIPELINE_TREE,
        ),
        "reviewed native engine",
    )
    state = review.causal.qualified.last_runtime_state(
        Path(full["run_root"]) / "full/opportunities/admission_opportunities.jsonl",
    )
    review.pipeline_state(state)
    cases.append({"case": "actual_installed_pipeline_and_disabled_probe", "passed": True})
    for name, update in (
        ("missing_installed_helper", {"demand_prefill_pipeline_installed": False}),
        ("progress_probe_enabled", {"demand_prefill_pipeline_progress_probe": True}),
        ("tool_prefetch_enabled", {"tool_prefetch": True}),
        ("frontier_restore_enabled", {"execution_frontier_restore": True}),
        ("profile_enabled", {"profile_shared_path": True}),
        ("changed_layer_group", {"layer_handoff_h2d_layers": 1}),
    ):
        reject(
            name, lambda update=update: review.pipeline_state({**state, **update}),
            "actual demand pipeline",
        )
    reject(
        "missing_progress_probe_observation",
        lambda: review.pipeline_state({
            key: value for key, value in state.items()
            if key != "demand_prefill_pipeline_progress_probe"
        }),
        "actual demand pipeline",
    )
    if helper.with_suffix(".json").exists() or review.FIGURES.exists():
        raise AssertionError("precheck unexpectedly created performance artifacts")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "CPU review boundaries, actual live rejection and historical complete controls.",
        "passing_cases": len(cases), "cases": cases,
        "inputs_sha256": {
            str(path): review.sha256_file(path)
            for path in (Path(__file__), helper, review.FULL, review.CONTROL, review.PREADMISSION)
        },
        "complete_performance_result_written": False,
        "performance_accepted": False,
    }
    review.causal.reservation.write_json(output, result)
    print({"passing_cases": len(cases), "complete_performance_result_written": False})


if __name__ == "__main__":
    main()
