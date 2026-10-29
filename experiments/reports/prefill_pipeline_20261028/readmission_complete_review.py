"""Review the repaired Full only after complete work, drain and actual owned exit."""

import argparse
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "readmission_complete_base", HERE / "complete_review.py",
)
BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)
REGISTRATION = BASE.OPS / "demand_prefill_ack_retry_20261028_r1.registration.json"
OUTPUT = HERE / "readmission_complete"
FIGURES = BASE.ROOT / "experiments/figures/demand_prefill_ack_retry_20261028_r1"
PRIOR_INDEX = HERE / "repeat_r2_complete/final_index.json"


def same_work(plan):
    prior = BASE.causal.load(BASE.FULL)
    if (
        plan["code_commit"] != "5cf3bb8bf40daf48ee001988e60f46e517bdd345"
        or plan["engine_tree"] != BASE.DEMAND_PREFILL_PIPELINE_TREE
        or plan["passing_cpu_cases"] != 4102
        or BASE.common_contract(plan) != BASE.common_contract(prior)
        or BASE.sha256_file(Path(plan["experiment_configuration"]))
        != BASE.sha256_file(Path(prior["experiment_configuration"]))
    ):
        raise ValueError("repair source or complete work/configuration changed")
    changed = subprocess.check_output([
        "git", "diff", "--name-only", prior["code_commit"], plan["code_commit"],
        "--", "beliefkv", "patches",
    ], cwd=BASE.ROOT, text=True).splitlines()
    if changed != ["beliefkv/runtime/demand_prefill_pipeline.py"]:
        raise ValueError("repair changes other runtime or native policy files")
    return changed


def precheck():
    output = HERE / "readmission_complete_precheck.json"
    if output.exists():
        raise FileExistsError("preserve earlier repaired completion precheck")
    plan = BASE.causal.load(REGISTRATION)
    changed = same_work(plan)
    state = BASE.causal.load(REGISTRATION.with_suffix(".state.json"))
    if state["status"] != "running_full" or not BASE.causal.reservation.alive(state["watcher"]):
        raise ValueError("precheck requires the actual live registered Full watcher")
    try:
        BASE.completed(plan, REGISTRATION, "full", BASE.DEMAND_PREFILL_PIPELINE_TREE)
    except ValueError as error:
        if str(error) != "require complete successful work and actual owned process exit":
            raise
        refusal = str(error)
    else:
        raise AssertionError("live repaired Full was accepted as complete")
    for registration in (BASE.FULL, BASE.OPS / "demand_prefill_pipeline_20261028_r2.registration.json"):
        BASE.completed(
            BASE.causal.load(registration), registration, "full", BASE.DEMAND_PREFILL_PIPELINE_TREE,
        )
    if OUTPUT.exists() or FIGURES.exists():
        raise AssertionError("live precheck encountered completed repair artifacts")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "actual_live_repair_rejected": refusal,
        "actual_live_watcher": BASE.causal.reservation.process(state["watcher"]["pid"]),
        "actual_complete_R1_R2_and_owned_exit_verified": True,
        "same_work_and_formal_configuration_verified": True,
        "changed_runtime_files": changed,
        "inputs_sha256": {
            str(path): BASE.sha256_file(path) for path in (
                Path(__file__), HERE / "complete_review.py", REGISTRATION, BASE.FULL,
            )
        },
        "complete_performance_result_written": False, "performance_accepted": False,
    }
    BASE.causal.reservation.write_json(output, result)
    print({
        "actual_live_repair_rejected": refusal,
        "actual_complete_R1_R2_and_owned_exit_verified": True,
        "complete_performance_result_written": False,
    })


def main():
    if OUTPUT.exists() or FIGURES.exists():
        raise FileExistsError("preserve earlier complete repaired Full evidence")
    plan = BASE.causal.load(REGISTRATION)
    changed = same_work(plan)
    BASE.completed(plan, REGISTRATION, "full", BASE.DEMAND_PREFILL_PIPELINE_TREE)
    arm = Path(plan["run_root"]) / "full"
    summary_path = arm / "replay/summary.json"
    summary = BASE.causal.load(summary_path)
    work = BASE.causal.validate_result(plan, summary, arm, live_control=True)
    if (len(work), sum(row[0] for row in work.values()), sum(row[1] for row in work.values())) != (
        3674, 145565966, 549671,
    ):
        raise ValueError("complete repaired workload population changed")
    observer = BASE.observe_registered(plan, REGISTRATION, "full", arm)
    BASE.drain_and_exit(arm)
    runtime = BASE.causal.qualified.last_runtime_state(
        arm / "opportunities/admission_opportunities.jsonl",
    )
    BASE.pipeline_state(runtime)
    info = BASE.causal.load(arm / "server/server_info.json")
    if any(info.get(name) != value for name, value in {
        "enable_deterministic_inference": False, "attention_backend": "fa3",
        "sampling_backend": "flashinfer", "enable_custom_logit_processor": True,
    }.items()):
        raise ValueError("repaired Full changed formal numerical or stopping mode")
    clock = BASE.causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
    prior = BASE.causal.load(PRIOR_INDEX)
    inputs = {}
    for source, digest in prior["inputs_sha256"].items():
        if BASE.sha256_file(Path(source)) != digest:
            raise ValueError("complete original/control evidence changed: " + source)
        inputs[source] = digest
    for name in ("readmission_position_entry.json", "readmission_gpu_review.json", "readmission_live_start.json"):
        path = HERE / name
        evidence = BASE.causal.load(path)
        for source, digest in evidence["inputs_sha256"].items():
            if BASE.sha256_file(Path(source)) != digest:
                raise ValueError("repaired source/model/startup evidence changed")
            inputs[source] = digest
        inputs[str(path)] = BASE.sha256_file(path)
    from scripts import plot_causal_decode_diagnostics as plotting

    current = plotting.extract("Full ACK repair", REGISTRATION, arm, bin_s=60)
    old_figures = BASE.ROOT / "experiments/figures/demand_prefill_pipeline_20261028_r1/evidence.json.gz"
    cached = json.loads(gzip.decompress(old_figures.read_bytes()))
    control = cached["arms"]["ThunderAgent decay"]
    if (
        current["match_contract"]["engine_tree"] != BASE.DEMAND_PREFILL_PIPELINE_TREE
        or control["match_contract"]["engine_tree"] != BASE.COMMON_TREE
    ):
        raise ValueError("unreviewed native engine pair")
    plotting.require_matching({
        name: {"match_contract": {
            key: value for key, value in row["match_contract"].items() if key != "engine_tree"
        }} for name, row in {"Full ACK repair": current, "ThunderAgent decay": control}.items()
    })
    if (
        abs(clock["mean_jct_ms"] - current["mean_jct_s"] * 1000.) > .01
        or abs(clock["workflow_event_makespan_ms"] - current["makespan_s"] * 1000.) > .01
    ):
        raise ValueError("complete workflow and worker clocks disagree")
    metrics = {name: current[name] for name in (
        "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
    )}
    metrics["streaming_response_metrics"] = {
        name: summary["streaming_response_metrics"][name] for name in (
            "workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms",
        )
    }
    reader = BASE.SealedRecords()
    ready = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "native_demand_restore_ready"
    ]
    if any(type(row.get("retry_native_capacity_check")) is not bool for row in ready):
        raise ValueError("repaired restore has no explicit ACK retry observation")
    retried = [row for row in ready if row["retry_native_capacity_check"]]
    recorded = runtime["counts"].get("demand_restore_native_capacity_retries", 0)
    if type(recorded) is not int or not 0 <= recorded <= len(retried):
        raise ValueError("recorded capacity-retry count exceeds the complete ready journals")
    comparisons = {}
    historical_path = HERE / "complete_review.json"
    baselines = {
        **prior["metrics"],
        "Preadmission Full": BASE.causal.load(historical_path)["metrics"]["Preadmission Full"],
    }
    for label, before in baselines.items():
        comparisons[label] = {
            "mean_jct_delta_s": metrics["mean_jct_s"] - before["mean_jct_s"],
            "mean_jct_reduction_percent": 100. * (1. - metrics["mean_jct_s"] / before["mean_jct_s"]),
            "end_to_end_output_increase_percent": 100. * (
                metrics["end_to_end_output_tokens_per_s"] / before["end_to_end_output_tokens_per_s"] - 1.
            ),
        }
    FIGURES.mkdir(parents=True)
    arms = {"Full ACK repair": current, "ThunderAgent decay": control}
    figures = plotting.render(
        arms, FIGURES, bin_s=60,
        title="Same V311 work: repaired Full / ThunderAgent decay (different engines)",
    )
    for name, field in (("time_bins.csv", "bins"), ("geometry.csv", "geometry")):
        BASE.write_csv(FIGURES / name, [
            {"arm": label, **row} for label, arm in arms.items() for row in arm[field]
        ])
    with (FIGURES / "evidence.json.gz").open("xb") as stream:
        stream.write(gzip.compress(json.dumps({
            "arms": arms, "figures": figures, "all_normal_tails_retained": True,
            "operator_sha256": BASE.sha256_file(Path(__file__)),
            "reviewed_engine_pair": [BASE.DEMAND_PREFILL_PIPELINE_TREE, BASE.COMMON_TREE],
            "performance_accepted": False,
        }, indent=2, allow_nan=False).encode(), mtime=0))
    inputs.update(current["evidence_sha256"])
    for source, seal in reader.prefixes.items():
        if Path(source).stat().st_size != seal["complete_prefix_bytes"] or (
            BASE.sha256_file(Path(source)) != seal["complete_prefix_sha256"]
        ):
            raise ValueError("complete ACK-ready journal changed")
        inputs[source] = seal["complete_prefix_sha256"]
    for path in (
        *FIGURES.iterdir(), Path(__file__), HERE / "complete_review.py", REGISTRATION,
        REGISTRATION.with_suffix(".state.json"), PRIOR_INDEX, old_figures,
        historical_path,
        arm / "telemetry_drain.json", arm / "owned_process_cleanup.json",
    ):
        inputs[str(path)] = BASE.sha256_file(path)
    OUTPUT.mkdir(parents=True)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "requests": len(work), "prompt_tokens": 145565966, "output_tokens": 549671,
        "exact_work_and_original_stopping_verified": True,
        "all_normal_tails_retained": True,
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified": True,
        "registered_configuration_observer": observer, "changed_runtime_files": changed,
        "metrics": metrics, "comparisons": comparisons,
        "ack_restore_ready_events": len(ready), "ack_native_capacity_retry_events": len(retried),
        "last_recorded_retry_counter": recorded,
        "retry_events": retried, "main_admission_retry_fix_included": True,
        "ordinary_math_long_greedy_equivalence_verified": False,
        "figures": figures, "inputs_sha256": inputs, "performance_accepted": False,
        "limitations": [
            "A retry records a fresh native capacity check, not guaranteed admission or a causal gain.",
            "No observed retries would leave performance changes unattributed to this inactive repair.",
            "The last recorded counter may precede final journals; raw complete events remain authoritative.",
            "Worker spans include runtime and dependency waits, not pure kernels or exclusive control CPU.",
            "Current demand ACK/service and JOIN/EOS/sibling mechanism audits remain separately required.",
            "The single reused V345 has a disclosed different engine; no all-baseline acceptance is claimed.",
        ],
    }
    BASE.causal.reservation.write_json(OUTPUT / "complete_review.json", result)
    print(json.dumps({
        "metrics": metrics, "comparisons": comparisons,
        "ack_native_capacity_retry_events": len(retried), "performance_accepted": False,
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--precheck", action="store_true")
    if parser.parse_args().precheck:
        precheck()
    else:
        main()
