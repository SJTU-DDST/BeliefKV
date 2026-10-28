"""Check completion boundaries and sealed physical accounting on actual evidence."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path


HERE = Path(__file__).resolve().parent


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve original precheck evidence")
    helper = HERE / "complete_review.py"
    spec = importlib.util.spec_from_file_location("pressure_index_complete_review", helper)
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
            raise AssertionError(f"{name}: incomplete or contradictory evidence accepted")

    reject(
        "actual_live_full_is_not_complete",
        lambda: review.completed(
            review.causal.load(review.FULL), review.FULL, "full", review.SIBLING_CAPACITY_TREE,
        ),
        "require complete successful work",
    )
    review.completed(
        review.causal.load(review.CONTROL), review.CONTROL,
        "thunderagent_acting_decay", review.COMMON_TREE,
    )
    cases.append({"case": "actual_complete_control_and_owned_exit", "passed": True})
    reject(
        "completed_control_cannot_be_relabelled_full",
        lambda: review.completed(
            review.causal.load(review.CONTROL), review.CONTROL, "full", review.SIBLING_CAPACITY_TREE,
        ),
        "arm order, native engine",
    )
    prefix_path = HERE / "collecting_after_index_fix.json"
    prefix = review.causal.load(prefix_path)
    reader = review.SealedRecords()
    reader.prefixes = prefix["input_jsonl_prefixes"].copy()
    rows = list(reader(Path(prefix["arm"]) / "opportunities/admission_opportunities.jsonl"))
    actual = review.physical_review(rows)
    if not actual["journal_release_totals_reconciled"]:
        raise AssertionError("actual sealed physical journals do not reconcile")
    cases.append({"case": "actual_sealed_physical_journals_and_same_prefix_counts", "passed": True})
    attempt = next(
        index for index, row in enumerate(rows)
        if row.get("event") == "agent_join_physical_plan_result"
    )
    state = next(
        index for index, row in enumerate(rows)
        if row.get("event") == "admission_runtime_state"
    )

    def replace(index, value):
        return (value if number == index else row for number, row in enumerate(rows))

    reject(
        "missing_physical_attempt",
        lambda: review.physical_review(row for number, row in enumerate(rows) if number != attempt),
        "same-prefix physical events",
    )
    reject(
        "excessive_donor_checks",
        lambda: review.physical_review(replace(attempt, {
            **rows[attempt], "checks": {**rows[attempt]["checks"], "donors_checked": 9},
        })),
        "bounded native accounting",
    )
    for name, update in (
        ("tool_prefetch_enabled", {"tool_prefetch": True}),
        ("ordinary_restore_enabled", {"execution_frontier_restore": True}),
        ("profiling_enabled", {"profile_shared_path": True}),
    ):
        reject(
            name,
            lambda update=update: review.physical_review(replace(state, {**rows[state], **update})),
            "no-profile/no-tool-prefetch",
        )
    reject("missing_snapshots", lambda: review.physical_review([]), "no healthy runtime snapshots")
    if helper.with_suffix(".json").exists():
        raise AssertionError("precheck unexpectedly created a complete performance result")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Read-only CPU completion-boundary checks and sealed physical prefix accounting.",
        "cases": cases, "passing_cases": len(cases),
        "exact_completed_receipts_in_original_prefix": prefix["exact_completed_receipts"],
        "physical_prefix_review": actual,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {
            str(path): review.sha256_file(path)
            for path in (Path(__file__), helper, prefix_path, review.FULL, review.CONTROL)
        },
        "complete_performance_result_written": False,
        "performance_accepted": False,
        "limitations": [
            "The sealed prefix is not a complete or atomic cross-file workload observation.",
            "Cumulative native releases are not simultaneous free capacity or saved JCT.",
            "Rejected contradictory fixtures do not establish GPU or model correctness.",
        ],
    }
    review.causal.reservation.write_json(output, result)
    print({"passing_cases": len(cases), "physical_prefix_review": actual})


if __name__ == "__main__":
    main()
