"""Review complete native-demand Full against the two unchanged historical controls."""

import csv
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
FULL = OPS / "demand_prefill_pipeline_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
PREADMISSION = OPS / "tool_offload_before_admission_20261027_r1.registration.json"
FIGURES = ROOT / "experiments/figures/demand_prefill_pipeline_20261028_r1"
sys.path.insert(0, json.loads(FULL.read_bytes())["execution_checkout"])

from beliefkv.experiments.native_engine_variant import COMMON_TREE, DEMAND_PREFILL_PIPELINE_TREE
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


SPECS = (
    ("Full (demand pipeline)", FULL, "full", DEMAND_PREFILL_PIPELINE_TREE),
    ("ThunderAgent decay", CONTROL, "thunderagent_acting_decay", COMMON_TREE),
    ("Preadmission Full", PREADMISSION, "full", COMMON_TREE),
)
COMMON_FIELDS = (
    "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
    "workflow_ids", "quality_exclusions", "quality_exclusion_details", "sampling_mode",
    "stream_response_metrics", "request_timeout_s", "max_running_requests",
)
PIPELINE_STATE = {
    "physical_disabled": False,
    "profile_shared_path": False,
    "demand_prefill_pipeline": True,
    "demand_prefill_pipeline_installed": True,
    "demand_prefill_pipeline_progress_probe": False,
    "tool_prefetch": False,
    "tool_prefetch_priority_ms": 0.,
    "execution_frontier_restore": False,
    "layer_handoff_enabled": True,
    "layer_handoff_d2h_layers": 8,
    "layer_handoff_h2d_layers": 4,
}


def load_operator(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def completed(plan, registration, label, tree):
    if not causal.predecessor_ready({**plan, "after_registration": str(registration)}):
        raise ValueError("require complete successful work and actual owned process exit")
    causal.comparison.verify_sources(plan)
    if plan["order"] != [label] or plan["engine_tree"] != tree or plan["quality_exclusions"]:
        raise ValueError("arm order, reviewed native engine or complete population differs")


def common_contract(plan):
    overlay = plan["tool_timing_overlay"]
    if sha256_file(Path(overlay["path"])) != overlay["sha256"]:
        raise ValueError("tool timing bytes changed")
    return {
        **{name: plan.get(name) for name in COMMON_FIELDS},
        "tool_timing_overlay": {key: value for key, value in overlay.items() if key != "path"},
        "seeds": {key: plan["reference_environment"].get(key) for key in (
            "SERVER_RANDOM_SEED", "SAMPLING_SEED", "PYTHONHASHSEED",
        )},
    }


def pipeline_state(state):
    if any(type(state.get(name)) is not type(value) or state[name] != value
           for name, value in PIPELINE_STATE.items()):
        raise ValueError("actual demand pipeline, probe, profile or transfer settings differ")


def drain_and_exit(arm):
    drain = causal.load(arm / "telemetry_drain.json")
    cleanup = causal.load(arm / "owned_process_cleanup.json")
    idle = drain["drained_status"]
    if (
        not drain["previous_zero_snapshot_ts_ms"] < idle["snapshot_ts_ms"]
        or any(idle.get(field) != 0 for field in (
            "pending_request_count", "pending_batch_count", "writer_queue_depth",
            "failed_records", "dropped_records",
        ))
        or idle.get("writer_error") is not None
        or cleanup["remaining_processes"]
        or any(causal.reservation.alive(row) for row in cleanup["identities"])
    ):
        raise ValueError("two fresh idle snapshots and actual owned exit are unverified")


def write_csv(path, rows):
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    output = HERE / "complete_review.json"
    if output.exists() or FIGURES.exists():
        raise FileExistsError("preserve prior complete evidence and figures")
    plans = {name: causal.load(path) for name, path, _, _ in SPECS}
    contract, work, clocks, proofs, inputs = None, None, {}, {}, {}
    for name, registration, label, tree in SPECS:
        plan = plans[name]
        completed(plan, registration, label, tree)
        observed = common_contract(plan)
        if contract is not None and observed != contract:
            raise ValueError("work, capacities, tools, seeds or stopping contracts differ")
        contract = observed
        arm = Path(plan["run_root"]) / label
        summary_path = arm / "replay/summary.json"
        summary = causal.load(summary_path)
        actual = causal.validate_result(plan, summary, arm, live_control=True)
        if work is not None and actual != work:
            raise ValueError("exact request work or original stopping differs")
        work = actual
        causal.observed_configuration(plan, label, arm, final=True)
        drain_and_exit(arm)
        info_path = arm / "server/server_info.json"
        info = causal.load(info_path)
        if any(info.get(key) != value for key, value in {
            "enable_deterministic_inference": False,
            "attention_backend": "fa3", "sampling_backend": "flashinfer",
            "enable_custom_logit_processor": True,
        }.items()):
            raise ValueError("ordinary formal math differs from the registered performance mode")
        clock = causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
        clocks[name] = clock
        proofs[name] = {
            "registration": str(registration), "code_commit": plan["code_commit"],
            "engine_tree": tree, "exact_requests": len(actual),
        }
        for path in (
            registration, registration.with_suffix(".state.json"), summary_path, info_path,
            arm / "telemetry_drain.json", arm / "owned_process_cleanup.json",
        ):
            inputs[str(path)] = sha256_file(path)
    if (len(work), sum(row[0] for row in work.values()), sum(row[1] for row in work.values())) != (
        3674, 145565966, 549671,
    ):
        raise ValueError("complete V230/V311 population changed")
    for path in (HERE / "current_model_review.json", HERE / "live_start.json"):
        proof = causal.load(path)
        for source, digest in proof["inputs_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("qualification or startup inputs changed")
            inputs[source] = digest
        inputs[str(path)] = sha256_file(path)
    full_arm = Path(plans[SPECS[0][0]]["run_root"]) / "full"
    pipeline_state(causal.qualified.last_runtime_state(
        full_arm / "opportunities/admission_opportunities.jsonl",
    ))
    helper = ROOT / "experiments/reports/full_only_pressure_index_20261028/complete_review.py"
    historical = load_operator(helper, "demand_pipeline_physical_accounting")
    reader = SealedRecords()
    physical = historical.physical_review(reader(
        full_arm / "opportunities/admission_opportunities.jsonl",
    ))
    if not physical["journal_release_totals_reconciled"]:
        raise ValueError("native release journals and same-prefix counts differ")
    for path, seal in reader.prefixes.items():
        if Path(path).stat().st_size != seal["complete_prefix_bytes"] or (
            sha256_file(Path(path)) != seal["complete_prefix_sha256"]
        ):
            raise ValueError("complete physical-plan evidence changed")
        inputs[path] = seal["complete_prefix_sha256"]
    from scripts import plot_causal_decode_diagnostics as plotting

    arms = {
        name: plotting.extract(name, registration, Path(plans[name]["run_root"]) / label, bin_s=60)
        for name, registration, label, _ in SPECS
    }
    plotting.require_matching({
        name: {"match_contract": {
            key: value for key, value in arm["match_contract"].items() if key != "engine_tree"
        }} for name, arm in arms.items()
    })
    metrics = {}
    for name, arm in arms.items():
        clock = clocks[name]
        if (
            abs(clock["mean_jct_ms"] - arm["mean_jct_s"] * 1000.) > .01
            or abs(clock["workflow_event_makespan_ms"] - arm["makespan_s"] * 1000.) > .01
        ):
            raise ValueError("independent full workflow clocks and worker plots disagree")
        metrics[name] = {key: arm[key] for key in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
        )}
        summary = causal.load(Path(arm["arm"]) / "replay/summary.json")
        metrics[name]["streaming_response_metrics"] = {
            key: summary["streaming_response_metrics"][key] for key in (
                "workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms",
            )
        }
        inputs.update(arm["evidence_sha256"])
    FIGURES.mkdir(parents=True)
    primary = {name: arms[name] for name in (SPECS[0][0], SPECS[1][0])}
    figures = plotting.render(
        primary, FIGURES, bin_s=60,
        title="Same V311 work: demand pipeline Full / ThunderAgent decay (different engines)",
    )
    for name, field in (("time_bins.csv", "bins"), ("geometry.csv", "geometry")):
        write_csv(FIGURES / name, [
            {"arm": label, **row} for label, arm in arms.items() for row in arm[field]
        ])
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arms": arms, "figures": figures,
        "figure_arms": list(primary), "all_normal_tails_retained": True,
        "native_engines": {name: tree for name, _, _, tree in SPECS},
        "profiling_enabled": {name: False for name, _, _, _ in SPECS},
        "registration_sha256": {
            str(path): sha256_file(path) for _, path, _, _ in SPECS
        },
        "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False,
    }
    figure_evidence = FIGURES / "evidence.json.gz"
    with figure_evidence.open("xb") as stream:
        stream.write(gzip.compress(
            json.dumps(evidence, indent=2, allow_nan=False).encode(), mtime=0,
        ))
    inputs[str(figure_evidence)] = sha256_file(figure_evidence)
    for path in FIGURES.iterdir():
        inputs[str(path)] = sha256_file(path)
    for path in (helper, Path(plotting.__file__), Path(__file__)):
        inputs[str(path)] = sha256_file(path)
    full_name, control_name, before_name = (spec[0] for spec in SPECS)
    full = metrics[full_name]
    comparisons = {}
    for name in (control_name, before_name):
        other = metrics[name]
        paired = {
            identity: (value - clocks[name]["workflow_jct_ms"][identity]) / 1000.
            for identity, value in sorted(clocks[full_name]["workflow_jct_ms"].items())
        }
        comparisons[name] = {
            "mean_jct_delta_s": full["mean_jct_s"] - other["mean_jct_s"],
            "mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / other["mean_jct_s"]),
            "end_to_end_output_increase_percent": 100. * (
                full["end_to_end_output_tokens_per_s"] / other["end_to_end_output_tokens_per_s"] - 1.
            ),
            "paired_workflow_difference_s": paired,
            "faster_workflows": sum(value < 0 for value in paired.values()),
            "slower_workflows": sum(value > 0 for value in paired.values()),
        }
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete same-work system comparison with explicit native-engine differences.",
        "requests": len(work), "prompt_tokens": sum(row[0] for row in work.values()),
        "output_tokens": sum(row[1] for row in work.values()),
        "all_normal_tails_retained": True, "exact_work_and_original_stopping_verified": True,
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified": True,
        "metrics": metrics, "proofs": proofs, "comparisons": comparisons,
        "physical_plan_review": physical, "physical_input_jsonl_prefixes": reader.prefixes,
        "actual_pipeline_settings": PIPELINE_STATE, "figures": figures,
        "inputs_sha256": inputs, "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False, "isolated_module_attribution": False,
        "limitations": [
            "All measured work and normal tails remain; no smoothing or post-end zero padding.",
            "Reviewed engines differ; prior Full policies also differ from this combined candidate.",
            "Native staging bytes, subsequent service, numerical modes and saved time are separate claims.",
            "Worker intervals include runtime and dependencies, not pure kernels or exposed control CPU.",
            "The ordinary-math long greedy equivalence diagnostic remains unqualified.",
            "One complete run cannot establish repeatable or all-baseline superiority.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({"metrics": metrics, "comparisons": comparisons}, indent=2))


if __name__ == "__main__":
    main()
