from copy import deepcopy
import unittest
import pytest

from beliefkv.experiments.workflow_metrics import distribution
from scripts.review_sustained_oracle import compare_cohorts


def cohort_report(measured=(100., 300.), warmup=50., background=400., throughput=100.):
    values = {
        "measured": {"measured-a": measured[0], "measured-b": measured[1]},
        "warmup": {"warm": warmup},
        "background": {"background": background},
    }
    cohort = {
        **{name + "_workflow_ids": list(rows) for name, rows in values.items()},
        "selection_rule": "fixed_arrival_indices_before_execution",
        "complete_JCT_including_normal_tails": True,
        "background_submission_stops_when_cohort_finishes": False,
        "throughput_window_offset_seconds": [10., 100.],
    }
    return {
        "cohort": cohort, **{name + "_workflow_JCT_ms": rows for name, rows in values.items()},
        "measured_complete_workflow_JCT_ms": distribution(values["measured"].values()),
        "all_service_output_tokens_by_workflow": {key: 10 for rows in values.values() for key in rows},
        "all_service_output_tokens": 40, "exact_receipt_requests": 8,
        "all_workflows_completed": True, "normal_tails_removed": False,
        "service_output_matches_complete_receipts": True,
        "common_window_output_tokens_per_second": throughput,
        "background_submission_coverage": {"covers_all_measured_completions": True},
    }


def censored_report(*, measured=(100., 300.), background_complete=False):
    report = cohort_report(measured=measured)
    report["cohort"].update(
        schema_version=2, background_submission_stops_when_cohort_finishes=True,
        nonmeasured_workflows_may_be_right_censored=True,
    )
    report.update(
        all_measured_completed=True, all_workflows_completed=background_complete,
        measured_service_output_matches_complete_receipts=True,
        measured_exact_receipt_requests=4,
        nonmeasured_workflows_right_censored=[] if background_complete else ["background"],
        service_output_matches_complete_receipts=background_complete,
    )
    if not background_complete:
        report["background_workflow_JCT_ms"] = {}
        report["all_service_output_tokens_by_workflow"]["background"] = 5
        report["all_service_output_tokens"] = 35
    return report


def test_background_completion_differences_do_not_change_complete_measured_comparison():
    control = censored_report()
    candidate = censored_report(measured=(90., 250.), background_complete=True)
    result = compare_cohorts(control, candidate)
    assert result["fixed_cohort_performance_target_met"]
    assert result["groups"]["measured"]["mean_JCT_reduction_percent"] == pytest.approx(15.)
    assert result["groups"]["background"]["paired_completed_workflows"] == 0
    assert result["groups"]["background"]["mean_JCT_reduction_percent"] is None
    assert result["groups"]["background"]["conditional_on_shared_completion"]
    assert result["complete_work_scope"] == "fixed_measured_workflows"


@pytest.mark.parametrize("change", (
    "unfinished_measured", "measured_receipts", "measured_requests", "measured_output",
    "censoring", "zero_background_JCT", "foreign_member",
))
def test_censored_comparison_keeps_measured_work_and_background_scope_strict(change):
    candidate = censored_report(measured=(90., 250.))
    if change == "unfinished_measured":
        candidate["all_measured_completed"] = False
    elif change == "measured_receipts":
        candidate["measured_service_output_matches_complete_receipts"] = False
    elif change == "measured_requests":
        candidate["measured_exact_receipt_requests"] -= 1
    elif change == "measured_output":
        candidate["all_service_output_tokens_by_workflow"]["measured-a"] += 1
        candidate["all_service_output_tokens"] += 1
    elif change == "censoring":
        candidate["nonmeasured_workflows_right_censored"] = []
    elif change == "zero_background_JCT":
        candidate["background_workflow_JCT_ms"] = {"background": 0.}
    else:
        candidate["background_workflow_JCT_ms"] = {"foreign": 10.}
    with pytest.raises(ValueError):
        compare_cohorts(censored_report(), candidate)


class SustainedOracleReviewTests(unittest.TestCase):
    def test_primary_fixed_cohort_not_improved_whole_population(self):
        result = compare_cohorts(
            cohort_report(), cohort_report(measured=(110., 310.), background=20., warmup=10.),
        )
        self.assertFalse(result["fixed_cohort_performance_target_met"])
        self.assertEqual(result["groups"]["measured"]["worsened_workflows"], 2)
        self.assertLess(result["groups"]["measured"]["mean_JCT_reduction_percent"], 0)
        self.assertGreater(result["groups"]["background"]["mean_JCT_reduction_percent"], 0)

    def test_retains_complete_long_tail_and_shared_throughput(self):
        result = compare_cohorts(
            cohort_report(), cohort_report(measured=(90., 250.), throughput=99.),
        )
        self.assertTrue(result["fixed_cohort_performance_target_met"])
        self.assertEqual(result["groups"]["measured"]["oracle_JCT_ms"]["max"], 250.)
        self.assertEqual(result["common_window_output_throughput"]["window_offset_seconds"], [10., 100.])
        self.assertAlmostEqual(result["common_window_output_throughput"]["gain_percent"], -1.)

    def test_throughput_regression_and_background_gaps_are_visible(self):
        candidate = cohort_report(measured=(80., 240.), throughput=95.)
        candidate["background_submission_coverage"]["covers_all_measured_completions"] = False
        result = compare_cohorts(cohort_report(), candidate)
        self.assertTrue(result["checks"]["fixed_measured_mean_JCT_gain"])
        self.assertFalse(result["checks"]["common_window_output_throughput_noninferior"])
        self.assertFalse(result["checks"]["background_covers_both_measured_tails"])

    def test_rejects_changed_members_or_window(self):
        for change in ("membership", "window"):
            with self.subTest(change=change):
                candidate = cohort_report()
                if change == "membership":
                    candidate["cohort"]["measured_workflow_ids"].reverse()
                else:
                    candidate["cohort"]["throughput_window_offset_seconds"] = [0., 90.]
                with self.assertRaises(ValueError):
                    compare_cohorts(cohort_report(), candidate)

    def test_rejects_incomplete_or_different_complete_work(self):
        for change in ("incomplete", "truncated", "requests", "outputs", "per_workflow"):
            with self.subTest(change=change):
                candidate = cohort_report()
                if change == "incomplete":
                    candidate["all_workflows_completed"] = False
                elif change == "truncated":
                    candidate["normal_tails_removed"] = True
                elif change == "requests":
                    candidate["exact_receipt_requests"] -= 1
                elif change == "outputs":
                    candidate["all_service_output_tokens"] -= 1
                else:
                    candidate["all_service_output_tokens_by_workflow"]["measured-a"] += 1
                    candidate["all_service_output_tokens_by_workflow"]["measured-b"] -= 1
                with self.assertRaises(ValueError):
                    compare_cohorts(cohort_report(), candidate)

    def test_rejects_invalid_or_inconsistent_JCT(self):
        for value in (0., -1., float("nan"), float("inf")):
            with self.subTest(value=value):
                candidate = cohort_report()
                candidate["measured_workflow_JCT_ms"]["measured-a"] = value
                with self.assertRaises(ValueError):
                    compare_cohorts(cohort_report(), candidate)
        candidate = deepcopy(cohort_report())
        candidate["measured_complete_workflow_JCT_ms"]["mean"] += 1
        with self.assertRaises(ValueError):
            compare_cohorts(cohort_report(), candidate)


if __name__ == "__main__":
    unittest.main()
