"""Review a complete mixed-workload control without fixed historical work counts."""

import argparse
from datetime import datetime, timezone
import gzip
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import plot_causal_decode_diagnostics as plotting
from scripts import run_causal_comparison as causal


def complete_work(registration):
    plan = causal.load(registration)
    dependency = {**plan, "after_registration": str(registration)}
    if not causal.predecessor_ready(dependency):
        return None
    work = causal.predecessor_work(dependency)
    expected = plan["source_plan"]
    if (
        len(work), sum(row[0] for row in work.values()), sum(row[1] for row in work.values())
    ) != (expected["requests"], expected["prompt_tokens"], expected["output_tokens"]):
        raise ValueError("complete receipts differ from this workload's declared work")
    if expected["workflows"] != len(plan["workflow_ids"]):
        raise ValueError("declared workflow population is inconsistent")
    for label in plan["order"]:
        arm = Path(plan["run_root"]) / label
        cleanup = causal.load(arm / "owned_process_cleanup.json")
        drain = causal.load(arm / "telemetry_drain.json")
        if (
            cleanup["remaining_processes"] or not cleanup.get("finished_at")
            or not cleanup["identities"]
            or any(causal.reservation.alive(row) for row in cleanup["identities"])
            or not drain["previous_zero_snapshot_ts_ms"] < drain["drained_status"]["snapshot_ts_ms"]
        ):
            raise ValueError("fresh idle drain and actual owned-process exit are required")
    return work


def model_cost(decode):
    steps, milliseconds = decode["request_steps"], decode["request_step_ms"]
    if (
        type(steps) is not int or steps <= 0
        or type(milliseconds) not in (int, float)
        or not math.isfinite(milliseconds) or milliseconds <= 0
    ):
        raise ValueError("model calibration requires complete positive decode service")
    return milliseconds / steps / 1000.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("the CPU reviewer must hide CUDA")
    if args.output_dir.exists():
        raise FileExistsError("preserve the original control review")
    if args.wait:
        started = time.monotonic()
        while complete_work(args.registration) is None:
            if time.monotonic() - started > 4 * 3600:
                raise TimeoutError("control review exceeded four hours")
            time.sleep(10.)
    work = complete_work(args.registration)
    if work is None:
        raise ValueError("the control has not completed all work and cleanup")
    plan = causal.load(args.registration)
    if plan["order"] != ["full"]:
        raise ValueError("expected the separate retained Full control")
    arm = Path(plan["run_root"]) / "full"
    row = plotting.extract("retained Full", args.registration, arm, bin_s=60)
    cost = model_cost(row["decode"])
    args.output_dir.mkdir(parents=True, exist_ok=False)
    data = args.output_dir / "decode_evidence.json.gz"
    data.write_bytes(gzip.compress(json.dumps(row, allow_nan=False).encode(), mtime=0))
    inputs = {
        str(path): sha256_file(path) for path in (
            args.registration, args.registration.with_suffix(".state.json"),
            arm / "telemetry_drain.json", arm / "owned_process_cleanup.json", Path(__file__),
        )
    }
    inputs.update(row["evidence_sha256"])
    plotting.require_complete_prefixes(row["input_jsonl_prefixes"])
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("completed control evidence changed during review")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(args.registration.resolve()),
        "registration_sha256": sha256_file(args.registration),
        "complete_work_and_actual_exit_verified": True,
        "requests": len(work), "workflows": len(plan["workflow_ids"]),
        "prompt_tokens": plan["source_plan"]["prompt_tokens"],
        "output_tokens": plan["source_plan"]["output_tokens"],
        "metrics": {name: row[name] for name in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
        )},
        "match_contract": row["match_contract"],
        "model_cost_calibration": {
            "model_seconds_per_work_token": cost,
            "definition": "complete_retained_Full_decode_request_step_ms/request_steps/1000",
            "prefill_equivalent_weight": .01,
            "limitations": "context/batch-averaged proxy, not future exact GPU service seconds",
        },
        "decode_evidence": {"path": str(data.resolve()), "sha256": sha256_file(data)},
        "input_jsonl_prefixes": row["input_jsonl_prefixes"], "inputs_sha256": inputs,
        "GPU_followup_registered": False, "performance_accepted": False,
    }
    causal.reservation.write_json(args.output_dir / "review.json", result)
    print(json.dumps({
        "requests": len(work), "metrics": result["metrics"],
        "model_seconds_per_work_token": cost,
    }))


if __name__ == "__main__":
    main()
