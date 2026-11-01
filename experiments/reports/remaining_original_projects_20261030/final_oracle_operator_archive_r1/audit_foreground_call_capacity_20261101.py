"""Locate actual foreground CALL waits intersecting selected-consumer pressure."""

from bisect import bisect_left
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-final-followup-a013dbd7")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
OUTPUT = REPORTS / "foreground_call_capacity_complete_controls_r1"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def inspect(registration, cohort_path):
    execution = verify_execution(registration, cohort_path)
    plan = causal.load(registration)
    arm = Path(plan["run_root"]) / "full"
    measured = set(causal.load(cohort_path)["measured_workflow_ids"])
    reader = SealedRecords()
    calls, pending, completed, cases = {}, {}, {}, []
    next_request_needed = {}
    for row in reader(arm / "replay/runtime_events.replay.jsonl"):
        kind, owner, attrs = row["kind"], row.get("invocation_id"), row.get("attributes") or {}
        if kind == "llm_result":
            completed[owner] = row
        elif kind == "llm_submit" and owner in next_request_needed:
            next_request_needed.pop(owner)["next_caller_request"] = row
        elif kind == "call" and row.get("execution_mode") == "foreground":
            child = row["target_invocation_id"]
            if child in calls or owner in pending:
                raise ValueError("overlapping foreground CALLs need a different audit")
            prior = completed.get(owner)
            if prior is None or prior["workflow_id"] != row["workflow_id"]:
                raise ValueError("foreground caller has no completed prior model request")
            case = {
                "workflow_id": row["workflow_id"], "caller_id": owner, "child_id": child,
                "caller_measured": row["workflow_id"] in measured,
                "call_event": row, "prior_caller_result": prior,
            }
            calls[child] = case
            pending[owner] = child
        elif kind == "return" and owner in calls:
            case = calls.pop(owner)
            if case["workflow_id"] != row["workflow_id"]:
                raise ValueError("foreground child returned in a different workflow")
            case["return_event"] = row
            case["blocked_ms"] = row["ts_ms"] - case["call_event"]["ts_ms"]
            if case["blocked_ms"] <= 0:
                raise ValueError("foreground CALL has invalid chronology")
            pending.pop(case["caller_id"])
            cases.append(case)
            if case["caller_id"] in next_request_needed:
                raise ValueError("foreground caller did not resume between CALLs")
            next_request_needed[case["caller_id"]] = case

    states, shortfalls, periodic, prepares, demotions = [], [], [], [], []
    kinds = {
        "admission_runtime_state": states,
        "selected_consumer_capacity_shortfall": shortfalls,
        "agent_periodic_admission_decision": periodic,
        "prepare_native_issued": prepares,
        "parent_pressure_demoted": demotions,
    }
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        bucket = kinds.get(row.get("event"))
        if bucket is not None:
            bucket.append(row)
    if not states:
        raise ValueError("require actual wall/monotonic clock observations")
    offsets = [row["ts_ms"] - row["monotonic_ms"] for row in states]
    lower, upper = min(offsets), max(offsets)
    if upper - lower >= 1.:
        raise ValueError("wall clock drift exceeds this audit's boundary model")
    wall_times = [row["ts_ms"] for row in states]
    if wall_times != sorted(wall_times):
        raise ValueError("runtime clock observations are not ordered")
    shortfall_times = []
    for row in shortfalls:
        index = min(bisect_left(wall_times, row["ts_ms"]), len(states) - 1)
        if index and abs(wall_times[index - 1] - row["ts_ms"]) < abs(wall_times[index] - row["ts_ms"]):
            index -= 1
        row["audit_monotonic_ms"] = row["ts_ms"] - offsets[index]
        row["audit_clock_sample_distance_ms"] = abs(wall_times[index] - row["ts_ms"])
        if row["required_device_tokens"] - row["device_free_tokens"] != row["deficit_tokens"]:
            raise ValueError("consumer shortfall geometry is inconsistent")
        shortfall_times.append(row["audit_monotonic_ms"])
    observed_native = {}
    wanted = {
        row["prior_caller_result"]["attributes"]["request_id"] for row in cases
    } | {
        row["next_caller_request"]["attributes"]["request_id"] for row in cases
        if "next_caller_request" in row
    }
    for row in reader(arm / "server/runtime_events.sglang.jsonl"):
        attrs = row.get("attributes") or {}
        if row["kind"] == "llm_submit" and attrs.get("request_id") in wanted:
            if attrs["request_id"] in observed_native:
                raise ValueError("duplicate native first-service observation")
            observed_native[attrs["request_id"]] = {
                name: attrs.get(name) for name in (
                    "prompt_tokens", "cached_tokens_device", "cached_tokens_host",
                    "uncached_prompt_tokens",
                )
            }
    unique, measured_unique, workflows = set(), set(), set()
    for case in cases:
        start, end = case["call_event"]["ts_ms"], case["return_event"]["ts_ms"]
        # The margin also excludes network publication uncertainty near boundaries.
        matched = [
            row for row in shortfalls
            if start + 50. + upper - lower < row["audit_monotonic_ms"] < end - 50. - upper + lower
        ]
        case["shortfalls"] = matched
        case["periodic_caller_rows"] = [
            {name: item.get(name) for name in (
                "active_before", "active_after_pressure", "application_ready",
                "complete_budget_tokens", "pending_before",
            )}
            for row in periodic if start < row["monotonic_s"] * 1000. < end
            for item in row["programs"] if item["invocation_id"] == case["caller_id"]
        ]
        context = case["prior_caller_result"]["context_id"]
        case["explicit_shadow_prepares_during_CALL"] = [
            row for row in prepares if row["context_id"] == context
            and start < row["ts_ms"] - lower < end
        ]
        case["registered_parent_demotions_during_CALL"] = [
            row for row in demotions if row.get("context_id") == context
            and start < row["ts_ms"] - lower < end
        ]
        case["native_caller_first_service_observations"] = {}
        for label, event in (
            ("prior", case["prior_caller_result"]),
            ("next", case.get("next_caller_request")),
        ):
            if event is not None:
                rid = event["attributes"]["request_id"]
                if rid not in observed_native:
                    raise ValueError("completed caller request lacks native first-service evidence")
                case["native_caller_first_service_observations"][label] = {
                    "request_id": rid, **observed_native[rid],
                }
        unique.update(row["request_id"] for row in matched)
        measured_unique.update(row["request_id"] for row in matched if row["workflow_id"] in measured)
        workflows.update(row["workflow_id"] for row in matched if row["workflow_id"] in measured)
    all_matched = [item for case in cases for item in case["shortfalls"]]
    runtime_path = Path(plan["execution_checkout"]) / "beliefkv/runtime/sglang_v0520_runtime.py"
    if sha256_file(runtime_path) != plan["source_sha256"]["beliefkv/runtime/sglang_v0520_runtime.py"]:
        raise ValueError("the actual completed runtime source differs from registration")
    result = {
        "registration": str(registration), "complete_measured_execution_verified": True,
        "measured_requests": execution["measured_requests"], "closed_CALLs": len(cases),
        "open_CALLs_at_background_cutoff": len(calls),
        "CALL_wait_ms": distribution(case["blocked_ms"] for case in cases),
        "selected_consumer_shortfall_observations_all": len(shortfalls),
        "CALLs_with_capacity_shortfall": sum(bool(case["shortfalls"]) for case in cases),
        "capacity_shortfall_observations_in_CALLs": len(all_matched),
        "distinct_consumers_in_CALLs": len(unique),
        "measured_shortfall_observations_in_CALLs": sum(row["workflow_id"] in measured for row in all_matched),
        "distinct_measured_consumers_in_CALLs": len(measured_unique),
        "distinct_measured_workflows_in_CALLs": len(workflows),
        "caller_periodic_states": dict(Counter(
            str((row["active_before"], row["active_after_pressure"], row["application_ready"]))
            for case in cases for row in case["periodic_caller_rows"]
        )),
        "wall_minus_monotonic_offset_spread_ms": upper - lower,
        "shortfall_clock_sample_distance_ms": distribution(
            row["audit_clock_sample_distance_ms"] for row in shortfalls),
        "cases": cases, "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            registration, cohort_path, runtime_path, Path(execution["certificate"]), Path(__file__),
        )},
        "scope": [
            "Declared runtime foreground CALL to the actual matching child RETURN.",
            "Repeated pressure observations are not independent scheduling opportunities or wait durations.",
            "Native cache-hit fields were read at first service, not at backdated submit timestamps.",
            "Before/after first-service hits do not establish continuous residency during CALL.",
            "Logical caller budget is not exclusive physical residency or reclaimable bytes.",
            "No complete per-owner Host backing/residency trace is available during these waits.",
            "No avoidable JCT, physical feasibility, or policy win is claimed.",
            "Extending blocked-state coverage is a correctness/coverage repair, not a new switching algorithm.",
            "No original task, trace, tool time, prompt or GPU state is changed.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed CALL evidence changed")
    return result


def main():
    if OUTPUT.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve CALL evidence and use hidden-CUDA nice19")
    definitions = {
        "best_original56": (
            OPS / "workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json",
            REPORTS / "sustained56_initial12_mean70_r1/analysis_cohort.json",
        ),
        "final_original82": (
            OPS / "workflow_final82_initial12_mean70_predictive_control_20261031_r1.registration.json",
            REPORTS / "final_oracle_original82_traffic_r1/analysis_cohort.json",
        ),
    }
    for label, pair in definitions.items():
        result = inspect(*pair)
        write(OUTPUT / (label + ".json"), result)
        print(json.dumps({name: value for name, value in result.items() if name in (
            "closed_CALLs", "CALLs_with_capacity_shortfall",
            "capacity_shortfall_observations_in_CALLs",
            "distinct_measured_consumers_in_CALLs", "distinct_measured_workflows_in_CALLs",
            "caller_periodic_states", "wall_minus_monotonic_offset_spread_ms",
        )} | {"label": label}), flush=True)
    write(OUTPUT / "audit.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "operator_sha256": sha256_file(Path(__file__)),
        "outputs_sha256": {
            label: sha256_file(OUTPUT / (label + ".json")) for label in definitions
        },
        "physical_opportunity_verified": False, "GPU_action": False,
        "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
