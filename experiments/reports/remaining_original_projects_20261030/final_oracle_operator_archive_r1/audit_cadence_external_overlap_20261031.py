"""Check whether local early returns can survive a delayed prefill frontier."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/BeliefKV")
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
BURSTS = REPORTS / "prefill_decode_burst_opportunity_r1"
PATHS = REPORTS / "measured_prefill_opportunity_complete_controls_r1"
HERE = REPORTS / "cadence_external_overlap_opportunity_r1"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def inspect(label):
    burst_path, paths_path = BURSTS / (label + ".json"), PATHS / (label + ".json")
    bursts, paths = causal.load(burst_path), causal.load(paths_path)
    plan = causal.load(Path(paths["registration"]))
    arm = Path(plan["run_root"]) / "full"
    reader = SealedRecords()
    requests = {row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")}
    following, terminal = {}, set()
    for workflow in paths["critical_path"]["workflows"].values():
        ids = workflow["critical_path_requests"]
        terminal.add(ids[-1])
        following.update(zip(ids, ids[1:]))
    rows, unknown = [], []
    for burst in bursts["episodes"]:
        prefill_ms = burst["prefill_worker_ms"]
        delay = burst["added_decode_service_before_last_prefill_proxy_ms"]
        for rid, candidate in burst["possible_critical_returns_before_end_of_prefill"].items():
            if rid in terminal:
                kind, gap, retained = "last_model_request_on_observed_workflow_path", None, candidate["local_advance_proxy_ms"]
            elif rid in following and (
                requests[rid]["native_receipt"]["identity"][1]
                == requests[following[rid]]["native_receipt"]["identity"][1]
            ):
                next_request = requests[following[rid]]
                gap = next_request["submitted_monotonic_ms"] - requests[rid]["native_complete_monotonic_ms"]
                if gap < 0:
                    raise ValueError("serial next request precedes current completion")
                # Even with earlier external completion, the next request
                # cannot pass the still occupying prefill chunk owner here.
                old_ready = candidate["original_earliest_return_proxy_ms"] + gap
                new_ready = candidate["interleaved_return_proxy_ms"] + gap
                retained = max(0., old_ready - max(prefill_ms + delay, new_ready))
                kind = "same_invocation_serial_successor"
            else:
                unknown.append(rid)
                continue
            rows.append({
                "request_id": rid, "kind": kind, "external_dispatch_gap_ms": gap,
                "local_return_advance_ms": candidate["local_advance_proxy_ms"],
                "advance_after_chunk_frontier_proxy_ms": retained,
                "prefill_frontier_delay_proxy_ms": delay,
            })
    kinds = sorted({row["kind"] for row in rows})
    result = {
        "label": label, "classified_observations": len(rows),
        "cross_invocation_JOIN_or_other_unknown_observations": len(unknown),
        "unknown_request_ids": unknown,
        "by_kind": {
            kind: {
                "observations": sum(row["kind"] == kind for row in rows),
                "positive_after_frontier_observations": sum(
                    row["kind"] == kind and row["advance_after_chunk_frontier_proxy_ms"] > 0 for row in rows),
                "advance_after_frontier_proxy_ms": distribution(
                    row["advance_after_chunk_frontier_proxy_ms"] for row in rows if row["kind"] == kind),
                "external_dispatch_gap_ms": distribution(
                    row["external_dispatch_gap_ms"] for row in rows
                    if row["kind"] == kind and row["external_dispatch_gap_ms"] is not None),
            } for kind in kinds
        },
        "rows": rows, "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in (burst_path, paths_path, Path(__file__))},
        "scope": [
            "A fixed-cost local sensitivity model, not a global scheduling simulation.",
            "Early nonterminal return alone need not improve JCT when its next prefill waits for the chunk owner.",
            "Same-invocation gaps are observed external-plus-client-dispatch time, not isolated tool GPU-free time.",
            "Cross-invocation JOIN paths remain unknown rather than credited as independent tool delay.",
            "Competing workflows also pay the prefill delay; local positive advances are not net mean JCT gain.",
            "Last model request does not assert that every terminal runtime event is instantaneous.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed request evidence changed")
    write(HERE / (label + ".json"), result)
    print(json.dumps({key: result[key] for key in (
        "label", "classified_observations", "cross_invocation_JOIN_or_other_unknown_observations", "by_kind",
    )}), flush=True)
    return result


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve reports and use nice19 CPU-only settings")
    write(HERE / "audit.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arms": {label: inspect(label) for label in ("best_original56", "latest_original69")},
        "GPU_action": False,
    })


if __name__ == "__main__":
    main()
