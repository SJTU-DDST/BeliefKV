from copy import deepcopy
import unittest

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
