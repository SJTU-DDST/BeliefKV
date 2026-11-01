"""Compare the sealed final82 common window without estimating unfinished JCT."""

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-final-followup-a013dbd7")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "final82_oracle_live_common_r3"
OUTPUT = HERE / "closed_common_window.json"
CONTROL = OPS / "workflow_final82_initial12_mean70_predictive_control_20261031_r1.registration.json"
ORACLE = OPS / "workflow_final82_initial12_mean70_capacity_oracle_20261031_r1.registration.json"
COHORT = REPORTS / "final_oracle_original82_traffic_r1/analysis_cohort.json"
CONTROL_REVIEW = REPORTS / "measured_cohort_workflow_final82_initial12_mean70_predictive_control_r1/cohort.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.review_matched_work_ranking import verify_calibrated_combination_pair
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution
from audit_original56_common_window_service_20261031 import measure


def main():
    if (
        OUTPUT.exists()
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve observations and use nice19 hidden-CUDA analysis")
    paths = (
        CONTROL, ORACLE, COHORT, CONTROL_REVIEW, HERE / "periodic.json",
        OPS / "audit_original56_common_window_service_20261031.py", Path(__file__),
    )
    pins = {str(path): sha256_file(path) for path in paths}
    control, oracle = causal.load(CONTROL), causal.load(ORACLE)
    for plan in (control, oracle):
        causal.comparison.verify_sources(plan)
    artifacts = verify_calibrated_combination_pair(*(
        causal.load(Path(plan["experiment_configuration"])) for plan in (control, oracle)
    ))
    for name in (
        "source_plan", "token_source_sha256", "source_capacity", "engine_tree",
        "sampling_mode", "workflow_ids", "workflow_arrivals",
    ):
        if control[name] != oracle[name]:
            raise ValueError("final pair changed work, data plane or arrivals: " + name)
    execution = verify_execution(CONTROL, COHORT)
    state = causal.load(ORACLE.with_suffix(".state.json"))
    handles = {name: state[name] for name in ("watcher", "server", "driver")}
    if state["status"] != "running_full" or not all(
        causal.reservation.alive(row) for row in handles.values()
    ):
        raise ValueError("the declared Oracle owners must actually be running")
    periodic = causal.load(HERE / "periodic.json")
    phase = periodic["observation_phases"]["phases"]["fixed_common_throughput_window"]
    if not phase["complete_phase_observed"]:
        raise ValueError("the fixed common window has not fully elapsed")
    cohort, reference = causal.load(COHORT), causal.load(CONTROL_REVIEW)
    if reference["cohort"] != cohort:
        raise ValueError("completed control review uses a different cohort")
    reader = SealedRecords()
    reader.prefixes.update(periodic["input_jsonl_prefixes"])
    before, after = (measure(plan, cohort, reader) for plan in (control, oracle))
    if not math.isclose(
        before["output_tokens_per_second"], reference["common_window_output_tokens_per_second"],
        rel_tol=1e-12, abs_tol=1e-12,
    ):
        raise ValueError("completed control output differs from its independent review")
    for path in artifacts:
        pins[str(path)] = sha256_file(Path(path))
    if any(sha256_file(Path(filename)) != digest for filename, digest in pins.items()):
        raise ValueError("closed-window analysis inputs changed")
    ratio = after["output_tokens_per_second"] / before["output_tokens_per_second"]
    write(OUTPUT, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "control": before, "oracle": after,
        "common_window_output_throughput_gain_percent": 100. * (ratio - 1.),
        "common_window_output_noninferior_at_frozen_98_percent_gate": ratio >= .98,
        "control_complete_measured_requests": execution["measured_requests"],
        "live_Oracle_handles": handles,
        "actual_Oracle_handles_alive_at_exit": {
            name: causal.reservation.alive(row) for name, row in handles.items()
        },
        "input_jsonl_prefixes": reader.prefixes, "inputs_sha256": pins,
        "scope": [
            "Same fixed complete wall window, including all background output and prefill outputs.",
            "The Oracle uses exactly the independently sealed periodic-r3 native/event prefixes.",
            "The completed control reproduces its prior complete-cohort output accounting.",
            "Mean complete Oracle JCT is still unavailable and not estimated from finished tasks.",
            "Worker intervals are not exclusive CUDA kernel time.",
            "This closes one frozen throughput gate, not the full Oracle/JCT acceptance decision.",
        ],
        "GPU_action": False, "performance_accepted": False,
    })
    print(json.dumps({
        "control_output_token_s": before["output_tokens_per_second"],
        "oracle_output_token_s": after["output_tokens_per_second"],
        "output_gain_percent": 100. * (ratio - 1.),
        "throughput_gate_passed": ratio >= .98,
        "control_declared_decode_width": before["decode_worker_weighted_declared_width"],
        "oracle_declared_decode_width": after["decode_worker_weighted_declared_width"],
    }), flush=True)


if __name__ == "__main__":
    main()
