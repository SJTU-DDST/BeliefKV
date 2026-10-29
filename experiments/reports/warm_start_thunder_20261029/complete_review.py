"""Verify the complete new-arrival control; do not compare it to burst JCT."""

from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
REGISTRATION = ROOT.parent / "ops/warm_start_thunder_20261029_r1.registration.json"
LABEL = "thunderagent_acting_decay"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.paper_baselines import validate_state
from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts import plot_causal_decode_diagnostics as plotting


def main():
    output, extracted = HERE / "complete_review.json", HERE / "complete_decode_evidence.json.gz"
    if output.exists() or extracted.exists():
        raise FileExistsError("preserve original complete control evidence")
    plan = causal.load(REGISTRATION)
    if not causal.predecessor_ready({**plan, "after_registration": str(REGISTRATION)}):
        raise ValueError("require complete successful work and actual owned process exit")
    causal.comparison.verify_sources(plan)
    arm = Path(plan["run_root"]) / LABEL
    summary_path = arm / "replay/summary.json"
    summary = causal.load(summary_path)
    work = causal.validate_result(plan, summary, arm, live_control=True)
    if (len(work), sum(row[0] for row in work.values()), sum(row[1] for row in work.values())) != (
        3674, 145565966, 549671,
    ):
        raise ValueError("complete work differs from V230/V311")
    if plan["order"] != [LABEL] or plan["workflow_arrivals"]["mode"] != "warm_start_poisson":
        raise ValueError("completed control differs from the declared arrival arm")
    runtime = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    validate_state(LABEL, runtime)
    drain = causal.load(arm / "telemetry_drain.json")
    cleanup = causal.load(arm / "owned_process_cleanup.json")
    idle = drain["drained_status"]
    if (
        not drain["previous_zero_snapshot_ts_ms"] < idle["snapshot_ts_ms"]
        or any(idle.get(field) != 0 for field in (
            "pending_request_count", "pending_batch_count", "writer_queue_depth",
            "failed_records", "dropped_records",
        ))
        or idle.get("writer_error") is not None or cleanup["remaining_processes"]
        or any(causal.reservation.alive(row) for row in cleanup["identities"])
    ):
        raise ValueError("fresh idle drain or actual owned cleanup is incomplete")
    observer = subprocess.check_output([
        sys.executable, "-c",
        "import json,sys; from pathlib import Path; p=Path(sys.argv[1]); "
        "x=json.loads(p.read_bytes()); sys.path.insert(0,x['execution_checkout']); "
        "from scripts import run_causal_comparison as c; "
        "c.comparison.verify_sources(x); "
        "c.observed_configuration(x,sys.argv[2],Path(x['run_root'])/sys.argv[2],final=True); "
        "print(json.dumps({'source':str(Path(c.__file__).resolve()),'sha256':c.sha256_file(Path(c.__file__))}))",
        str(REGISTRATION), LABEL,
    ], text=True)
    observed = json.loads(observer)
    if observed["sha256"] != plan["source_sha256"]["scripts/run_causal_comparison.py"]:
        raise ValueError("configuration was not checked by the frozen registered observer")
    row = plotting.extract("ThunderAgent decay warm start", REGISTRATION, arm, bin_s=60)
    plotting.require_complete_prefixes(row["input_jsonl_prefixes"])
    clock = causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
    if (
        abs(clock["mean_jct_ms"] - row["mean_jct_s"] * 1000.) > .01
        or abs(clock["workflow_event_makespan_ms"] - row["makespan_s"] * 1000.) > .01
    ):
        raise ValueError("complete JCT clocks differ")
    inputs = dict(row["evidence_sha256"])
    for path in (
        REGISTRATION, REGISTRATION.with_suffix(".state.json"), summary_path,
        arm / "server/server_info.json", arm / "telemetry_drain.json",
        arm / "owned_process_cleanup.json", Path(plan["workflow_arrivals_path"]),
        Path(__file__), Path(plotting.__file__),
    ):
        inputs[str(path)] = sha256_file(path)
    with extracted.open("xb") as stream:
        stream.write(gzip.compress(json.dumps(row, allow_nan=False).encode(), mtime=0))
    inputs[str(extracted)] = sha256_file(extracted)
    result = {
        "status": "completed_new_arrival_control_verified",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(REGISTRATION), "code_commit": plan["code_commit"],
        "engine_tree": plan["engine_tree"],
        "requests": len(work), "prompt_tokens": 145565966, "output_tokens": 549671,
        "exact_complete_work_and_stopping_verified": True,
        "all_workflows_and_normal_tails_retained": True,
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified": True,
        "actual_registered_configuration_observer": observed,
        "workflow_arrivals": plan["workflow_arrivals"],
        "metrics": {key: row[key] for key in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
        )},
        "streaming_response_metrics": {
            key: summary["streaming_response_metrics"][key] for key in (
                "workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms",
            )
        },
        "paper_state": runtime["paper_baseline"], "inputs_sha256": inputs,
        "matched_predictive_result_available": False, "performance_accepted": False,
        "limitations": [
            "Warm start is finite and does not establish stationary system capacity.",
            "Historical burst results do not share the arrival/JCT contract.",
            "V311 tool timing is the declared synthetic observable-category overlay.",
            "Worker intervals include runtime and waits, not exclusive GPU kernels.",
        ],
    }
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("complete control inputs changed during review")
    causal.reservation.write_json(output, result)
    print(json.dumps({"status": result["status"], "metrics": result["metrics"]}, indent=2))


if __name__ == "__main__":
    main()
