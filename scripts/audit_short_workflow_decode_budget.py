"""Charge local decode service before claiming an earlier short request return."""

from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
import argparse
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def local_decode_burst(
    prefill_complete_ms, original_complete_ms, output_tokens, *,
    added_prefill_ms, decode_step_ms, decode_cost_multiplier,
):
    if (
        type(output_tokens) is not int or output_tokens < 1
        or any(type(value) not in (int, float) or not math.isfinite(value) for value in (
            prefill_complete_ms, original_complete_ms, added_prefill_ms,
            decode_step_ms, decode_cost_multiplier,
        ))
        or added_prefill_ms < 0 or decode_step_ms <= 0 or decode_cost_multiplier < 1.
    ):
        raise ValueError("local decode service requires complete finite positive costs")
    decode_cost = (output_tokens - 1) * decode_step_ms * decode_cost_multiplier
    added = added_prefill_ms + decode_cost
    returned = prefill_complete_ms + added
    return {
        "hypothetical_return_ms": returned,
        "local_request_advance_ms": max(0., original_complete_ms - returned),
        "extra_worker_cost_before_original_prefill_frontier_ms": added,
        "extra_decode_steps": output_tokens - 1,
        "extra_decode_worker_cost_ms": decode_cost,
        "local_advance_exceeds_twice_extra_cost": (
            original_complete_ms - returned > 2. * added
        ),
    }


def summary(rows, critical):
    # One earliest observed choice per request, not the best future result.
    unique = {}
    for row in sorted(rows, key=lambda item: item["boundary_ms"]):
        unique.setdefault(row["request_id"], row)
    selected = list(unique.values())
    critical_rows = [row for row in selected if row["request_id"] in critical]
    return {
        "repeated_local_choices": len(rows), "distinct_requests": len(selected),
        "requests_with_positive_local_advance": sum(row["local_request_advance_ms"] > 0 for row in selected),
        "requests_with_advance_exceeding_twice_extra_cost": sum(
            row["local_advance_exceeds_twice_extra_cost"] for row in selected
        ),
        "distinct_measured_critical_requests": len(critical_rows),
        "measured_critical_requests_with_positive_local_advance": sum(
            row["local_request_advance_ms"] > 0 for row in critical_rows
        ),
        "measured_critical_advance_ms": distribution(
            row["local_request_advance_ms"] for row in critical_rows
        ),
        "measured_critical_advance_exceeding_twice_cost_ms": distribution(
            row["local_request_advance_ms"] for row in critical_rows
            if row["local_advance_exceeds_twice_extra_cost"]
        ),
        "local_request_advance_ms": distribution(
            row["local_request_advance_ms"] for row in selected
        ),
        "extra_worker_cost_per_request_ms": distribution(
            row["extra_worker_cost_before_original_prefill_frontier_ms"] for row in selected
        ),
        "measured_critical_last_model_calls": sum(row["last_model_call"] for row in critical_rows),
    }


def inspect(screen_path, path_report_path):
    screen = json.loads(screen_path.read_bytes())
    for filename, digest in screen["inputs_sha256"].items():
        if sha256_file(Path(filename)) != digest:
            raise ValueError("the preceding prefill screen changed")
    path_report = json.loads(path_report_path.read_bytes())
    if path_report["registration"] != screen["registration"]:
        raise ValueError("the complete critical paths belong to another execution")
    plan = json.loads(Path(screen["registration"]).read_bytes())
    arm = Path(plan["run_root"]) / "full"
    reader = SealedRecords()
    reader.prefixes = dict(screen["input_jsonl_prefixes"])
    requests = {row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")}
    prefills, first_prefills, decode, decode_times = {}, {}, [], []
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        compact = {name: row[name] for name in (
            "launch_monotonic_ms", "complete_monotonic_ms",
            "service_elapsed_ms", "batch_size",
        )}
        if row["phase"] == "decode":
            decode_times.append(row["launch_monotonic_ms"])
            decode.append(compact)
        elif row["phase"] == "prefill":
            prefills[row["launch_monotonic_ms"]] = compact
            for member in row["request_samples"]:
                first_prefills.setdefault(member["request_id"], (compact, {
                    name: member[name] for name in (
                        "token_delta", "output_token_delta", "sequence_tokens_before",
                    )
                }))
        else:
            raise ValueError("the recorded execution is not separate prefill/decode")
    critical = {
        rid for row in path_report["critical_path"]["workflows"].values()
        for rid in row["critical_path_requests"]
    }
    reasons = Counter()
    rows = {field: [] for field in screen["choices"]}
    for field, choices in screen["choices"].items():
        for choice in choices:
            rid = choice["request_id"]
            first, member = first_prefills[rid]
            if (
                member["token_delta"] > 512 or member["output_token_delta"] <= 0
                or member["sequence_tokens_before"] + member["token_delta"] != requests[rid]["prompt_tokens"]
            ):
                reasons["future_first_prefill_is_not_complete_short_input"] += 1
                continue
            index = bisect_right(decode_times, choice["boundary_ms"])
            if index == len(decode):
                reasons["no_following_native_decode_cost"] += 1
                continue
            owner = prefills[choice["boundary_ms"]]
            for multiplier in (1., 1.5, 2.):
                charged = local_decode_burst(
                    owner["complete_monotonic_ms"],
                    requests[rid]["native_complete_monotonic_ms"],
                    requests[rid]["output_tokens"],
                    added_prefill_ms=first["service_elapsed_ms"],
                    decode_step_ms=decode[index]["service_elapsed_ms"],
                    decode_cost_multiplier=multiplier,
                )
                rows[field].append({
                    **choice, **charged,
                    "decode_cost_multiplier": multiplier,
                    "observed_full_decode_worker_ms": decode[index]["service_elapsed_ms"],
                    "observed_decode_batch_size": decode[index]["batch_size"],
                    "added_prefill_worker_proxy_ms": first["service_elapsed_ms"],
                    "added_prefill_actual_batch_size": first["batch_size"],
                    "last_model_call": math.isclose(
                        choice["candidate_score"]["model_work_tokens"],
                        .01 * choice["necessary_input_increment_tokens"] + choice["output_tokens"],
                        abs_tol=1e-9,
                    ),
                })
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("the sealed native input changed")
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": screen["registration"], "reasons": dict(reasons),
        "summary": {
            field: {
                str(multiplier): {
                    "whole_execution": summary(
                        [row for row in values if row["decode_cost_multiplier"] == multiplier], critical,
                    ),
                    "common_window": summary(
                        [row for row in values if row["decode_cost_multiplier"] == multiplier
                         and row["in_common_window"]], critical,
                    ),
                } for multiplier in (1., 1.5, 2.)
            } for field, values in rows.items()
        },
        "rows": rows, "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            screen_path, path_report_path, Path(__file__),
            ROOT / "scripts/audit_sealed_wait_prefetch.py",
        )},
        "scope": [
            "The earliest preceding size-qualified logical observation is used per request, not the best future boundary.",
            "Hypothetically cofill a short input, then run the existing full decode batch until that request completes.",
            "Charge the whole observed future first-prefill worker as the extra input proxy and all inserted full decode steps.",
            "Multipliers 1/1.5/2 are declared sensitivity cases, not measured costs for a changed batch.",
            "The existing long prefill is delayed by all charged extra work; no free or narrow-batch decode is assumed.",
            "Earlier prefix fit, extra member graph shape, future physical growth and actual cadence remain unqualified.",
            "Other queued workflows and later tools/JOINs can also be delayed; their complete JCT is not simulated.",
            "Correlated local advances must not be divided by the cohort size as achievable JCT or a global bound.",
            "First service geometry is an offline label, not an earlier real-time residency observation.",
        ],
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("screen", "critical-path-report", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve attempts and use nice19 CPU-only analysis")
    result = inspect(args.screen, args.critical_path_report)
    write(args.output, result)
    print(json.dumps({"summary": result["summary"], "GPU_action": False}), flush=True)


if __name__ == "__main__":
    main()
