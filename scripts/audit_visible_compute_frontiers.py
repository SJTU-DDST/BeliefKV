"""Screen visible tool boundaries after paying a full retained decode batch."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_compute_frontier_projection import (
    case_distribution, changes, dependency_model, propagate,
)
from scripts.audit_resident_decode_prefill_frontiers import (
    closed_bursts, frontier_index, last_outstanding_child, retained_members,
)
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


FACTORS = (.75, 1., 1.5, 2.)


def visible_cases(forwards, requests, closed, markers, joins, returned):
    """Select by visible tokens; keep future length and movement as audit labels."""
    counts, cases = Counter(), []
    for previous, prefills, following in closed_bursts(forwards):
        counts["closed_prefill_bursts"] += 1
        members, signals, unknown = [], [], 0
        for member in previous["request_samples"]:
            rid = member["request_id"]
            request = requests.get(rid)
            if request is None:
                unknown += 1
                continue
            emitted = member["output_tokens_after"]
            output = request["native_receipt"]["output_token_ids"]
            if not 0 <= emitted <= len(output):
                raise ValueError("visible output exceeds the exact request receipt")
            # Completion is already observable at this worker boundary. Do not
            # treat the previous batch's finished members as runnable decode.
            if request["native_complete_monotonic_ms"] <= previous["complete_monotonic_ms"] + 1e-5:
                continue
            if emitted == len(output):
                raise ValueError("unfinished request has no remaining recorded output")
            members.append(member)
            visible = output[:emitted]
            if markers["close"] in visible and markers["eos"] not in visible:
                signals.append(rid)
        counts["bursts_with_unknown_previous_members"] += bool(unknown)
        if not signals:
            continue
        counts["bursts_with_visible_tool_close"] += 1
        available = previous["complete_monotonic_ms"]
        first = next((
            index for index, batch in enumerate(prefills)
            if batch["launch_monotonic_ms"] >= available
        ), None)
        if first is None:
            counts["signal_arrived_after_all_prefills_were_submitted"] += 1
            continue
        counts["signal_before_unsubmitted_prefill"] += 1
        counts["bursts_with_already_submitted_prefill_prefix"] += first > 0
        suffix = prefills[first:]
        retained = tuple(retained_members(previous, prefills, following))
        retained_ids = {member["request_id"] for member in retained}
        if unknown or not {member["request_id"] for member in members} <= retained_ids:
            counts["incomplete_movement_evidence"] += 1
            continue
        victims = {
            member["request_id"]: member["workflow_id"]
            for batch in suffix for member in batch["request_samples"]
        }
        if not set(victims) <= requests.keys():
            counts["prefill_request_completion_unobserved"] += 1
            continue
        participants = set(victims.values()) | {member["workflow_id"] for member in members}
        if not participants <= closed:
            counts["censored_direct_participants"] += 1
            continue
        begin = suffix[0]["service_start_monotonic_ms"]
        if begin < available or begin < suffix[0]["launch_monotonic_ms"] - 1e-5:
            raise ValueError("candidate worker boundary precedes signal or submission")
        completing = [
            member["request_id"] for member in members
            if requests[member["request_id"]]["output_tokens"] - member["output_tokens_after"] == 1
        ]
        step = previous["service_elapsed_ms"]
        if not math.isfinite(step) or step <= 0:
            raise ValueError("invalid preceding whole-batch worker cost")
        cases.append({
            "sample_id": suffix[0]["sample_id"], "previous_decode_sample_id": previous["sample_id"],
            "begin_ms": begin, "signal_available_ms": available,
            "original_prefill_end_ms": prefills[-1]["complete_monotonic_ms"],
            "already_submitted_prefill_batches": first, "unsubmitted_prefill_batches": len(suffix),
            "previous_decode_members": len(previous["request_samples"]),
            "unfinished_decode_members": len(members),
            "visible_tool_close_requests": signals,
            "visible_last_child_tool_close": any(
                member["request_id"] in signals and last_outstanding_child(
                    member["invocation_id"], available, joins, returned,
                ) for member in members
            ),
            "whole_previous_batch_worker_cost_ms": step,
            "immediately_completing_requests_offline": completing,
            "remaining_outputs_for_signal_offline": {
                member["request_id"]: (
                    requests[member["request_id"]]["output_tokens"] - member["output_tokens_after"]
                )
                for member in members if member["request_id"] in signals
            },
            "victim_requests": sorted(victims), "victim_workflows": sorted(set(victims.values())),
            "full_state_identity_observed": all(
                member["full_state_identity_observed"] for member in retained
                if member["request_id"] in {item["request_id"] for item in members}
            ),
        })
    counts["auditable_visible_signal_cases"] = len(cases)
    return cases, counts


def project_case(case, model, requests, measured):
    """Credit immediate completions only; other batch progress has no credit."""
    result = {**case, "cost_sensitivity": {}}
    for factor in FACTORS:
        cost = case["whole_previous_batch_worker_cost_ms"] * factor
        finish = case["begin_ms"] + cost
        advance = {
            rid: requests[rid]["native_complete_monotonic_ms"] - finish
            for rid in case["immediately_completing_requests_offline"]
        }
        if any(value <= 0 for value in advance.values()):
            result["cost_sensitivity"][str(factor)] = {"applicable": False}
            continue
        current = propagate(
            model, advance=advance, delay={rid: cost for rid in case["victim_requests"]},
        )
        delta = changes(model, current, model["ends"])
        result["cost_sensitivity"][str(factor)] = {
            "applicable": True, "whole_batch_decode_worker_cost_ms": cost,
            "all_completed_workflow_sum_JCT_reduction_s": -sum(delta.values()) / 1000.,
            "measured_workflow_mean_JCT_reduction_s": (
                -sum(delta[name] for name in measured) / len(measured) / 1000.
            ),
            "affected_workflow_JCT_change_s": {
                name: value / 1000. for name, value in delta.items() if abs(value) > 1e-5
            },
        }
    return result


def summaries(cases):
    result = {}
    for label, population in (
        ("visible_tool_close", cases),
        ("visible_tool_close_and_last_child", [
            row for row in cases if row["visible_last_child_tool_close"]
        ]),
    ):
        result[label] = {
            "cases": len(population),
            "full_state_identity_observed": sum(row["full_state_identity_observed"] for row in population),
            "all_signals_one_output_from_completion_offline": sum(
                all(value == 1 for value in row["remaining_outputs_for_signal_offline"].values())
                for row in population
            ),
            "positive_sum_at_all_costs": sum(all(
                row["cost_sensitivity"][str(factor)]["applicable"]
                and row["cost_sensitivity"][str(factor)]["all_completed_workflow_sum_JCT_reduction_s"] > 0
                for factor in FACTORS
            ) for row in population),
            "factors": {},
        }
        for factor in FACTORS:
            values = [row["cost_sensitivity"][str(factor)] for row in population]
            values = [row for row in values if row["applicable"]]
            result[label]["factors"][str(factor)] = {
                "applicable": len(values),
                "positive_all_completed_sum": sum(
                    row["all_completed_workflow_sum_JCT_reduction_s"] > 0 for row in values
                ),
                "positive_sum_and_measured_mean": sum(
                    row["all_completed_workflow_sum_JCT_reduction_s"] > 0
                    and row["measured_workflow_mean_JCT_reduction_s"] > 0 for row in values
                ),
                "all_completed_sum_reduction_s": case_distribution(
                    row["all_completed_workflow_sum_JCT_reduction_s"] for row in values
                ),
                "measured_mean_reduction_s": case_distribution(
                    row["measured_workflow_mean_JCT_reduction_s"] for row in values
                ),
            }
    return result


def inspect(prior_path):
    prior = json.loads(prior_path.read_bytes())
    if not prior["baseline_exactly_reproduced"] or prior["GPU_action"]:
        raise ValueError("require the verified CPU dependency projection")
    for name, digest in prior["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError(f"prior input changed: {name}")
    reader = SealedRecords()
    reader.prefixes.update(prior["input_jsonl_prefixes"])
    registration = Path(prior["registration"])
    config = json.loads(registration.read_bytes())
    arm = Path(config["run_root"]) / "full"
    cohort_path = next(
        Path(name) for name in prior["inputs_sha256"] if name.endswith("/analysis_cohort.json")
    )
    tokenizer_path = next(
        Path(name) for name in prior["inputs_sha256"] if name.endswith("/tokenizer_config.json")
    )
    tokenizer = json.loads(tokenizer_path.read_bytes())
    token_ids = {
        row["content"]: int(key) for key, row in tokenizer["added_tokens_decoder"].items()
    }
    markers = {
        "close": token_ids["</tool_call>"], "eos": token_ids[tokenizer["eos_token"]],
    }
    measured = set(json.loads(cohort_path.read_bytes())["measured_workflow_ids"])
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    requests = {
        row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")
    }
    plan, _expected, _provenance = source_plan(Path(config["source_arm"]))
    model = dependency_model(plan, events, requests)
    if propagate(model) != model["reference"]:
        raise ValueError("unmodified dependency model did not reproduce observation")
    baseline = statistics.mean(
        model["reference"][model["ends"][name]] - model["reference"][model["starts"][name]]
        for name in measured
    ) / 1000.
    if not math.isclose(baseline, prior["baseline_measured_mean_JCT_s"], abs_tol=1e-9):
        raise ValueError("fixed complete measured JCT changed")
    _results, actions, joins, returned = frontier_index(events)
    cases, counts = visible_cases(
        (
            row for row in reader(arm / "server/runtime_audit.jsonl")
            if row.get("event") == "gpu_service_sample"
        ),
        requests, set(model["ends"]), markers, joins, returned,
    )
    projected = []
    for index, case in enumerate(cases, 1):
        case["signal_next_boundaries_offline"] = {
            rid: actions.get(rid) for rid in case["visible_tool_close_requests"]
        }
        projected.append(project_case(case, model, requests, measured))
        if index % 128 == 0:
            print(json.dumps({"projected_cases": index, "total": len(cases)}), file=sys.stderr, flush=True)
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "prior_projection": str(prior_path.resolve()), "registration": str(registration),
        "baseline_measured_mean_JCT_s": baseline, "counts": dict(counts),
        "summaries": summaries(projected), "local_projections": projected,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            prior_path, Path(__file__),
            ROOT / "scripts/audit_compute_frontier_projection.py",
            ROOT / "scripts/audit_resident_decode_prefill_frontiers.py",
        )},
        "scope": [
            "Trigger uses only already emitted tool-close tokens without an emitted EOS.",
            "An action cannot precede visibility of the previous decode outputs or replace submitted prefill.",
            "The complete preceding decode batch pays one worker-cost proxy; no single-request subbatch is assumed.",
            "Future lengths only label immediate completions; no credit is assigned to other decode progress.",
            "Incomplete movement, missing completions and censored direct participants are counted and excluded.",
            "Each local projection starts independently from the same baseline; their gains cannot be added.",
            "Observed dependency lags remain fixed; shared queues, future batches, KV growth and allocation are not resimulated.",
            "Historical worker samples do not prove complete attempt/session identity or physical executability.",
            "Following membership and burst length are audit labels, not available online decision inputs.",
            "This is neither a throughput prediction nor a measured or global JCT improvement.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-projection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use nice19 CPU-only execution")
    result = inspect(args.prior_projection)
    write(args.output, result)
    print(json.dumps({"counts": result["counts"], "summaries": result["summaries"]}), flush=True)


if __name__ == "__main__":
    main()
