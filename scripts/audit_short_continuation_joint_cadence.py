"""Screen early short prefill plus one full decode step per remaining chunk."""

from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def local_return(prefills, remaining_outputs, step_ms):
    if remaining_outputs < 1 or step_ms <= 0:
        raise ValueError("positive output work and decode cost are required")
    if remaining_outputs - 1 > len(prefills):
        return None
    if remaining_outputs == 1:
        return prefills[0]["complete_monotonic_ms"], 0.
    index = remaining_outputs - 2
    added = (index + 1) * step_ms
    return prefills[index]["complete_monotonic_ms"] + added, added


def inspect(downstream_path):
    previous = json.loads(downstream_path.read_bytes())
    for filename, digest in previous["inputs_sha256"].items():
        if sha256_file(Path(filename)) != digest:
            raise ValueError("prior downstream analysis inputs changed")
    registration_path = next(
        Path(name) for name in previous["inputs_sha256"] if name.endswith(".registration.json")
    )
    plan = json.loads(registration_path.read_bytes())
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    forwards = [
        row for row in reader(arm / "server/runtime_audit.jsonl")
        if row.get("event") == "gpu_service_sample"
    ]
    indices = {row["launch_monotonic_ms"]: index for index, row in enumerate(forwards)}
    if len(indices) != len(forwards):
        raise ValueError("native launches are not unique")
    details = []
    for item in previous["episodes"]:
        index = indices[item["early_prefill_launch_ms"]]
        prefills = []
        while index < len(forwards) and forwards[index]["phase"] == "prefill":
            prefills.append(forwards[index])
            index += 1
        if not prefills or index == len(forwards):
            raise ValueError("expected closed prefill burst")
        next_decode = forwards[index]
        if next_decode["phase"] != "decode" or next_decode["service_elapsed_ms"] <= 0:
            raise ValueError("expected the actual next decode cost")
        result = local_return(prefills, item["true_output_tokens"], next_decode["service_elapsed_ms"])
        details.append({
            "request_id": item["request_id"], "workflow_id": item["workflow_id"],
            "true_output_tokens": item["true_output_tokens"], "prefill_steps_available": len(prefills),
            "next_actual_decode_worker_ms": next_decode["service_elapsed_ms"],
            "complete_before_last_prefill_proxy": bool(
                result and result[0] < prefills[-1]["complete_monotonic_ms"]
            ),
            "local_complete_proxy_ms": result[0] if result else None,
            "extra_decode_cost_until_return_proxy_ms": result[1] if result else None,
            "local_request_advance_proxy_ms": max(0., item["original_complete_ms"] - result[0])
            if result else None,
            "extra_decode_cost_over_whole_burst_proxy_ms": len(prefills) * next_decode["service_elapsed_ms"],
        })
    advance = sum(row["local_request_advance_proxy_ms"] or 0. for row in details)
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "logical_boundary_episodes": len(details),
        "requests_that_can_finish_within_available_extra_steps": sum(
            row["local_complete_proxy_ms"] is not None for row in details
        ),
        "requests_with_positive_local_advance": sum(
            (row["local_request_advance_proxy_ms"] or 0.) > 0 for row in details
        ),
        "requests_with_complete_before_last_prefill_proxy": sum(
            row["complete_before_last_prefill_proxy"] for row in details
        ),
        "sum_correlated_local_advance_ms": advance,
        "sum_local_advances_divided_by_frozen_18_workflows_s": advance / 18000.,
        "extra_decode_cost_over_burst_proxy_ms": distribution(
            row["extra_decode_cost_over_whole_burst_proxy_ms"] for row in details
        ),
        "rows": details,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                downstream_path, Path(__file__), ROOT / "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        "input_jsonl_prefixes": reader.prefixes,
        "scope": [
            "Start at exactly the logical-ready boundaries chosen by the prior downstream screen.",
            "Earlier prefill and subset admission are still assumed feasible and cost-free.",
            "One full decode forward after every prefill uses the next observed decode duration.",
            "Those extra decode costs delay following prefills; adding a new member can cost more.",
            "Unknown physical growth, changed batching and whole-workflow downstream effects are not simulated.",
            "Only returns within this burst are estimated; later-only decode progress remains unquantified.",
            "Correlated local advances cannot be summed as achievable JCT or a rigorous global bound.",
        ],
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--downstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use nice19 CPU-only analysis")
    result = inspect(args.downstream)
    write(args.output, result)
    print(json.dumps({name: value for name, value in result.items() if name not in (
        "rows", "inputs_sha256", "input_jsonl_prefixes", "scope",
    )}))


if __name__ == "__main__":
    main()
