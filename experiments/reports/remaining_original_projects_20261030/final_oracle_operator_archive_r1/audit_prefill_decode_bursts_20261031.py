"""Estimate local decode-interleaving opportunity at observed prefill bursts."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/BeliefKV")
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
PRIOR = REPORTS / "measured_prefill_opportunity_complete_controls_r1"
HERE = REPORTS / "prefill_decode_burst_opportunity_r1"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def inspect(label):
    previous_path = PRIOR / (label + ".json")
    previous = causal.load(previous_path)
    plan = causal.load(Path(previous["registration"]))
    arm = Path(plan["run_root"]) / "full"
    reader = SealedRecords()
    requests = {row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")}
    critical = {
        rid for workflow in previous["critical_path"]["workflows"].values()
        for rid in workflow["critical_path_requests"]
    }
    last_decode, burst, candidates, burst_members, episodes = {}, [], {}, set(), []
    burst_total, long_total = 0, 0

    def finish(next_decode):
        nonlocal burst_total, long_total
        if not burst:
            return
        burst_total += 1
        long_total += len(burst) > 1
        if len(burst) < 2 or next_decode is None:
            return
        later = {item["request_id"]: item for item in next_decode["request_samples"]}
        residents = {}
        for rid, before in candidates.items():
            request, after = requests[rid], later.get(rid)
            if rid in burst_members or after is None or before["output_tokens_after"] != after["output_tokens_before"]:
                continue
            if tuple(before.get(key) for key in ("invocation_id", "context_id", "context_epoch", "attempt_id")) != tuple(
                after.get(key) for key in ("invocation_id", "context_id", "context_epoch", "attempt_id")
            ):
                raise ValueError("a supposedly continuing resident changed native identity")
            remaining = request["output_tokens"] - before["output_tokens_after"]
            if remaining > 0:
                residents[rid] = remaining
        if not residents:
            return
        step = next_decode["service_elapsed_ms"]
        total = sum(item["service_elapsed_ms"] for item in burst)
        elapsed, completions = 0., {}
        # Fixed next-decode batch cost is a conservative local service proxy;
        # no new requests or future tools are introduced into this schedule.
        for index, row in enumerate(burst, 1):
            elapsed += row["service_elapsed_ms"]
            elapsed += step
            for rid, remaining in residents.items():
                if remaining == index:
                    completions[rid] = {
                        "remaining_outputs_at_burst_start": remaining,
                        "original_earliest_return_proxy_ms": total + remaining * step,
                        "interleaved_return_proxy_ms": elapsed,
                        "local_advance_proxy_ms": max(0., total + remaining * step - elapsed),
                    }
        episodes.append({
            "prefill_batches": len(burst), "prefill_worker_ms": total,
            "next_decode_batch_ms": step, "previously_and_subsequently_decoded_critical_requests": len(residents),
            "possible_critical_returns_before_end_of_prefill": completions,
            "added_decode_service_before_last_prefill_proxy_ms": (len(burst) - 1) * step,
            "prefill_start_monotonic_ms": burst[0]["service_start_monotonic_ms"],
        })

    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        if row["phase"] == "prefill":
            if not burst:
                begin = row["service_start_monotonic_ms"]
                candidates = {
                    rid: member for rid, member in last_decode.items()
                    if rid in critical and requests[rid]["native_complete_monotonic_ms"] > begin
                }
                burst_members = set()
            burst.append(row)
            burst_members.update(item["request_id"] for item in row["request_samples"])
        else:
            finish(row)
            burst, candidates, burst_members = [], {}, set()
            for member in row["request_samples"]:
                if member["request_id"] in critical:
                    last_decode[member["request_id"]] = member
    finish(None)
    advances = [
        item["local_advance_proxy_ms"] for row in episodes
        for item in row["possible_critical_returns_before_end_of_prefill"].values()
    ]
    # Repeated appearances of a request are retained but never summed into JCT.
    unique_return_ids = {
        rid for row in episodes for rid in row["possible_critical_returns_before_end_of_prefill"]
    }
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "label": label,
        "all_prefill_bursts": burst_total, "multi_batch_prefill_bursts": long_total,
        "continuing_measured_critical_decode_bursts": len(episodes),
        "possible_early_return_observations": len(advances),
        "distinct_critical_requests_with_possible_early_return": len(unique_return_ids),
        "resident_prefill_burst_worker_ms": distribution(row["prefill_worker_ms"] for row in episodes),
        "local_request_advance_proxy_ms": distribution(advances),
        "added_decode_service_before_last_prefill_proxy_ms": distribution(
            row["added_decode_service_before_last_prefill_proxy_ms"] for row in episodes),
        "episodes": episodes, "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in (previous_path, Path(__file__))},
        "scope": [
            "Only observed complete critical-path requests, present before and after a burst, are considered.",
            "Input work, forward shapes and next-decode worker duration are frozen local proxies.",
            "Future output lengths are used only for this offline opportunity audit.",
            "A native admission check is still needed for every additional decode growth allocation.",
            "Earlier returns can overlap tools, but can also delay prefills and change later batches.",
            "Repeated requests and parallel paths make summed local advances unsuitable as a JCT prediction.",
            "This is a mechanism opportunity, not a replay simulator, optimal bound or measured policy gain.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed burst evidence changed")
    write(HERE / (label + ".json"), result)
    print(json.dumps({key: result[key] for key in (
        "label", "all_prefill_bursts", "multi_batch_prefill_bursts",
        "continuing_measured_critical_decode_bursts",
        "distinct_critical_requests_with_possible_early_return",
        "resident_prefill_burst_worker_ms", "local_request_advance_proxy_ms",
    )}), flush=True)
    return result


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve reports and use nice19 CPU-only settings")
    write(HERE / "audit.json", {
        "arms": {label: inspect(label) for label in ("best_original56", "latest_original69")},
        "GPU_action": False, "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
