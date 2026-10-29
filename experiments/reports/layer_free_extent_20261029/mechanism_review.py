"""Summarize complete warm-start work and distinguish actual handoff coverage."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    output = HERE / "warm_start_mechanism_review_r1.json"
    if output.exists():
        raise FileExistsError("preserve previous complete mechanism evidence")
    review_path = HERE / "warm_start_complete_r2/review.json"
    complete = causal.load(review_path)
    inputs = {str(review_path): sha256_file(review_path), str(Path(__file__)): sha256_file(Path(__file__))}
    result = {}
    registrations = {
        "ThunderAgent decay": ROOT.parent / "ops/warm_start_thunder_20261029_r1.registration.json",
        "FULL": ROOT.parent / "ops/warm_start_free_extent_20261029_r1.registration.json",
    }
    for label, path in registrations.items():
        if complete_work(path) is None:
            raise ValueError("mechanism review requires complete successful owned exit")
        plan = causal.load(path)
        arm = Path(plan["run_root"]) / plan["order"][0]
        status_path = arm / "server/native_telemetry_status.json"
        status = causal.load(status_path)
        server_info = causal.load(arm / "server/server_info.json")
        if server_info["enable_mixed_chunk"]:
            raise ValueError("this reader explicitly handles the unchanged non-mixed profile")
        reader, counters, iterations = SealedRecords(), Counter(), set()
        for row in reader(arm / "server/runtime_audit.jsonl"):
            if row.get("event") != "gpu_service_sample":
                continue
            if row["forward_iter"] in iterations or row["phase"] not in ("prefill", "decode"):
                raise ValueError("duplicate or unsupported native batch")
            iterations.add(row["forward_iter"])
            phase = row["phase"]
            counters[phase + "_batches"] += 1
            counters[phase + "_worker_ms"] += row["service_elapsed_ms"]
            if phase == "prefill":
                for sample in row["request_samples"]:
                    if sample["token_delta_semantics"] != "prefill_extend_input_len":
                        raise ValueError("prefill work lacks actual native extend lengths")
                    counters["actual_prefill_input_tokens"] += sample["token_delta"]
        if len(iterations) != status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]:
            raise ValueError("native worker observations have not fully drained")
        native_path = HERE / (
            "warm_start_native_demand_r1.json" if label == "FULL"
            else "warm_start_thunder_native_demand_r1.json"
        )
        demand = causal.load(native_path)
        native = demand["native_legacy_accounting_scope"]
        if demand["byte_balance"]["exact_physical_bytes_reconciled"] is not True:
            raise ValueError("native copy bytes are not reconciled")
        row = {
            "actual_native_work": dict(counters),
            "actual_transfers": native["transfers"],
            "previously_served_prefix_evidence": status["context_prefix_reuse_evidence"],
            "host_eviction_reaccess_attribution": status["host_block_eviction_attribution"],
            "restore_wait_probe_coverage": status["gpu_restore_wait_probe"],
            "verified_staged_demand": demand["staged_native_demand"]["counts"],
            "verified_staged_members": demand["staged_native_service"]["counts"],
            "handoffs": native["handoffs"],
        }
        if label == "FULL":
            runtime = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
            counts = runtime["counts"]
            row["last_recorded_handoff_counters_not_shutdown_final"] = {
                name: value for name, value in counts.items() if name.startswith("layer_handoff")
            }
            row["last_recorded_tool_offload_and_host_counters_not_shutdown_final"] = {
                name: value for name, value in counts.items()
                if name.startswith(("predicted_tool_offload_", "host_demand_", "idle_prepare_host_demand_"))
            }
        result[label] = row
        for source in (path, status_path, native_path):
            inputs[str(source)] = sha256_file(source)
        for source, seal in reader.prefixes.items():
            if Path(source).stat().st_size != seal["complete_prefix_bytes"]:
                raise ValueError("worker observations grew after completion")
            inputs[source] = seal["complete_prefix_sha256"]
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("complete mechanism evidence changed")
    left, right = result["ThunderAgent decay"], result["FULL"]
    changes = {
        "actual_prefill_input_tokens_percent": 100 * (
            right["actual_native_work"]["actual_prefill_input_tokens"]
            / left["actual_native_work"]["actual_prefill_input_tokens"] - 1
        ),
        "h2d_bytes_percent": 100 * (
            right["actual_transfers"]["h2d_bytes"] / left["actual_transfers"]["h2d_bytes"] - 1
        ),
        "d2h_bytes_percent": 100 * (
            right["actual_transfers"]["d2h_bytes"] / left["actual_transfers"]["d2h_bytes"] - 1
        ),
    }
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete_warm_start_mechanism_reviewed", "arms": result,
        "full_relative_to_thunder": changes, "inputs_sha256": inputs,
        "complete_performance": complete["full_relative_to_thunder"],
        "handoff_isolated_performance_proven": False, "performance_accepted": False,
        "limitations": [
            "Native prefill work includes real retries; worker intervals are not exclusive GPU kernels.",
            "Copy-stream time includes dependencies and cannot be subtracted from workflow JCT.",
            "Restore probes are sampled, with 19/2 observations; no whole-run wait extrapolation.",
            "Prefix-loss counters and exact revisits of evicted FULL nodes measure different boundaries.",
            "Staged member counts are episode associations, not distinct requests or proof of hidden DMA.",
            "One tiny handoff with no new free extent cannot establish the new path's performance.",
        ],
    }
    causal.reservation.write_json(output, report)
    print(json.dumps({
        "full_relative_to_thunder": changes,
        "actual_prefill_input_tokens": {
            label: row["actual_native_work"]["actual_prefill_input_tokens"] for label, row in result.items()
        },
        "actual_transfer_GB": {
            label: {direction: row["actual_transfers"][direction + "_bytes"] / 1e9 for direction in ("h2d", "d2h")}
            for label, row in result.items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
