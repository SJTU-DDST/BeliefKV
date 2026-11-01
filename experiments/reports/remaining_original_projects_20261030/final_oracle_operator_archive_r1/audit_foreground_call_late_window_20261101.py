"""Check the control-declared CALL mechanism window after complete measured work."""

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-foreground-call-a73fdcb7")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "foreground_call_late_window_r1"
DECLARATION = HERE / "declaration.json"
COHORT = REPORTS / "sustained56_initial12_mean70_r1/analysis_cohort.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.review_predictive_thunder_cohort import TRAFFIC_FIELDS
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution
from audit_original56_common_window_service_20261031 import measure


def main():
    output = HERE / "analysis.json"
    if output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve late-window diagnostics and use hidden-CUDA nice19")
    declared = causal.load(DECLARATION)
    if declared["candidate_has_observed_CALL_effects"] is not False or any(
        sha256_file(Path(path)) != digest for path, digest in declared["inputs_sha256"].items()
    ):
        raise ValueError("the pre-effect diagnostic declaration changed")
    registrations = (
        OPS / "workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json",
        OPS / "workflow_original56_foreground_call_20261031_r1.registration.json",
    )
    executions = [verify_execution(path, COHORT) for path in registrations]
    if executions[0]["work"] != executions[1]["work"]:
        raise ValueError("complete fixed measured work differs")
    plans = [causal.load(path) for path in registrations]
    if any(plans[0][name] != plans[1][name] for name in TRAFFIC_FIELDS):
        raise ValueError("late-window pair changed traffic or resources")
    cohort = causal.load(COHORT)
    if cohort["throughput_window_offset_seconds"] != declared["primary_window_offset_seconds_unchanged"]:
        raise ValueError("the primary throughput cohort changed")
    diagnostic = deepcopy(cohort)
    diagnostic["throughput_window_offset_seconds"] = declared["diagnostic_window_offset_seconds"]
    reader = SealedRecords()
    before, after = (measure(plan, diagnostic, reader) for plan in plans)
    ratio = after["output_tokens_per_second"] / before["output_tokens_per_second"]
    primary_path = REPORTS / "foreground_call_matched_result_r1/comparison.json"
    primary = causal.load(primary_path)
    noninferior = ratio >= declared["minimum_diagnostic_output_throughput_fraction"]
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed diagnostic observations changed")
    write(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "control": before, "candidate": after,
        "output_throughput_gain_percent": 100. * (ratio - 1.),
        "late_window_output_noninferior": noninferior,
        "primary_engineering_signal": primary["engineering_promotion_signal"],
        "combined_engineering_promotion_signal": bool(
            primary["engineering_promotion_signal"] and noninferior),
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            DECLARATION, COHORT, primary_path, *registrations, Path(__file__),
            OPS / "audit_original56_common_window_service_20261031.py",
        )},
        "scope": [
            "Diagnostic window chosen from the completed control before any candidate CALL effects.",
            "All actual background output and prefill first output tokens are included.",
            "The original JCT cohort and primary throughput window are unchanged.",
            "This mechanism diagnostic does not establish steady state or statistical significance.",
            "A diagnostic cannot promote a failed primary comparison.",
        ],
        "GPU_action": False, "performance_accepted": False,
    })
    print({
        "late_window_output_gain_percent": 100. * (ratio - 1.),
        "noninferior": noninferior,
        "combined_promotion_signal": bool(primary["engineering_promotion_signal"] and noninferior),
    }, flush=True)


if __name__ == "__main__":
    main()
