"""Review complete same-work Full and the reused ThunderAgent decay control."""

from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
REPORTS = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
FULL = OPS / "sibling_service_capacity_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
FIGURES = ROOT / "experiments/figures/sibling_service_capacity_20261028_r1"
sys.path.insert(0, json.loads(FULL.read_bytes())["execution_checkout"])

from beliefkv.experiments.native_engine_variant import COMMON_TREE, SIBLING_CAPACITY_TREE
from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.summarize_semantic_h2d_ab import capacity_pressure_watch


def settled(plan, registration, label, tree):
    causal.comparison.verify_sources(plan)
    if (
        plan["order"] != [label] or plan["engine_tree"] != tree
        or plan["quality_exclusions"]
        or not causal.predecessor_ready({**plan, "after_registration": str(registration)})
    ):
        raise ValueError("arm is incomplete, changed, excluded or still owns processes")


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve earlier complete review")
    plans = {name: causal.load(path) for name, path in (("Full", FULL), ("ThunderAgent decay", CONTROL))}
    for name, path, label, tree in (
        ("Full", FULL, "full", SIBLING_CAPACITY_TREE),
        ("ThunderAgent decay", CONTROL, "thunderagent_acting_decay", COMMON_TREE),
    ):
        settled(plans[name], path, label, tree)
    plot_state = causal.load(OPS / "sibling_service_capacity_20261028_r1.plots.state.json")
    if plot_state["status"] != "complete":
        raise ValueError("complete matched figures have not finished")
    evidence_path = FIGURES / "evidence.json.gz"
    plotted = json.loads(gzip.decompress(evidence_path.read_bytes()))
    if plotted.get("native_engines") != {
        "Full (sibling capacity)": SIBLING_CAPACITY_TREE,
        "ThunderAgent decay": COMMON_TREE,
    }:
        raise ValueError("figure native-engine pair differs")
    for path in (FULL, CONTROL):
        if plotted["registration_sha256"][str(path)] != sha256_file(path):
            raise ValueError("figure registration changed")
    inputs = {str(evidence_path): sha256_file(evidence_path)}
    work, common, metrics, durations, proofs = None, None, {}, {}, {}
    for name, registration, label, plot_label in (
        ("Full", FULL, "full", "Full (sibling capacity)"),
        ("ThunderAgent decay", CONTROL, "thunderagent_acting_decay", "ThunderAgent decay"),
    ):
        plan = plans[name]
        contract = {field: plan.get(field) for field in (
            "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
            "sampling_mode", "stream_response_metrics", "request_timeout_s",
            "max_running_requests", "workflow_ids", "quality_exclusion_details",
        )}
        overlay = plan["tool_timing_overlay"]
        if sha256_file(Path(overlay["path"])) != overlay["sha256"]:
            raise ValueError("tool timing artifact changed")
        contract["tool_timing_overlay"] = {
            key: value for key, value in overlay.items() if key != "path"
        }
        if common is not None and common != contract:
            raise ValueError("work, timing, capacity or serving contract differs")
        common = contract
        arm = Path(plan["run_root"]) / label
        summary_path = arm / "replay/summary.json"
        summary = causal.load(summary_path)
        actual = causal.validate_result(plan, summary, arm, live_control=True)
        if work is not None and work != actual:
            raise ValueError("exact request work or original stopping differs")
        work = actual
        causal.observed_configuration(plan, label, arm, final=True)
        drain_path, cleanup_path = arm / "telemetry_drain.json", arm / "owned_process_cleanup.json"
        drain, cleanup = causal.load(drain_path), causal.load(cleanup_path)
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
            raise ValueError("fresh idle drain and actual owned cleanup are unverified")
        clock = causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
        figure_arm = plotted["arms"][plot_label]
        if (
            abs(figure_arm["mean_jct_s"] * 1000. - clock["mean_jct_ms"]) > .01
            or abs(figure_arm["makespan_s"] * 1000. - clock["workflow_event_makespan_ms"]) > .01
        ):
            raise ValueError("figure and independent workflow clocks differ")
        for source, digest in figure_arm["evidence_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("completed figure source changed")
        inputs.update(figure_arm["evidence_sha256"])
        metrics[name] = {field: figure_arm[field] for field in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
        )}
        metrics[name]["capacity_pressure"] = capacity_pressure_watch(
            causal.load(arm / "server/native_telemetry_status.json")
        )
        metrics[name]["streaming_response_metrics"] = {
            field: summary["streaming_response_metrics"][field]
            for field in ("workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms")
        }
        durations[name] = clock["workflow_jct_ms"]
        proofs[name] = {
            "registration": str(registration), "code_commit": plan["code_commit"],
            "engine_tree": plan["engine_tree"], "exact_requests": len(actual),
        }
        for path in (
            registration, registration.with_suffix(".state.json"), summary_path, drain_path,
            cleanup_path, arm / "server/native_telemetry_status.json",
        ):
            inputs[str(path)] = sha256_file(path)
    arm = Path(plans["Full"]["run_root"]) / "full"
    sibling_path = REPORTS / "complete_sibling_service.json"
    auditor = ROOT / "scripts/audit_sibling_native_service.py"
    if not sibling_path.exists():
        subprocess.run([
            sys.executable, str(auditor), "--arm", str(arm), "--output", str(sibling_path),
        ], check=True)
    siblings = causal.load(sibling_path)
    if siblings["arm"] != str(arm) or siblings["operator_sha256"] != sha256_file(auditor):
        raise ValueError("complete sibling service auditor changed")
    for source, seal in siblings["input_jsonl_prefixes"].items():
        path = Path(source)
        if (
            path.stat().st_size != seal["complete_prefix_bytes"]
            or sha256_file(path) != seal["complete_prefix_sha256"]
        ):
            raise ValueError("sibling evidence is not a complete stable input")
        inputs[source] = seal["complete_prefix_sha256"]
    inputs[str(sibling_path)] = sha256_file(sibling_path)
    reader, physical = SealedRecords(), []
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "agent_join_planned_physical_release":
            physical.append(row)
    physical_summary = {
        "journal_events": len(physical),
        "native_free_list_released_tokens": sum(
            donor["released_tokens"] for row in physical for donor in row["donors"]
        ),
        "handoff_reserved_tokens": sum(
            donor["reserved_tokens"] for row in physical for donor in row["reserved_handoff_donors"]
        ),
        "not_additive_free_capacity": True,
        "not_verified_consumer_allocation": True,
    }
    for source, seal in reader.prefixes.items():
        if sha256_file(Path(source)) != seal["complete_prefix_sha256"]:
            raise ValueError("physical-plan audit input changed")
    full, control = metrics["Full"], metrics["ThunderAgent decay"]
    paired = [{
        "workflow_id": identity, "full_jct_s": value / 1000.,
        "control_jct_s": durations["ThunderAgent decay"][identity] / 1000.,
        "difference_s": (value - durations["ThunderAgent decay"][identity]) / 1000.,
    } for identity, value in sorted(durations["Full"].items())]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete same-work system comparison, with explicitly different reviewed native engines.",
        "requests": len(work), "prompt_tokens": sum(row[0] for row in work.values()),
        "output_tokens": sum(row[1] for row in work.values()),
        "all_normal_tails_retained": True, "exact_work_and_original_stopping_verified": True,
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified": True,
        "proofs": proofs, "metrics": metrics, "paired_workflow_jct": paired,
        "faster_workflows": sum(row["difference_s"] < 0 for row in paired),
        "slower_workflows": sum(row["difference_s"] > 0 for row in paired),
        "mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / control["mean_jct_s"]),
        "end_to_end_output_increase_percent": 100. * (
            full["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1.
        ),
        "sibling_native_service_summary": siblings["summary"],
        "join_physical_plan_summary": physical_summary,
        "figures": plotted["figures"], "figure_directory": str(FIGURES),
        "inputs_sha256": inputs, "operator_sha256": sha256_file(Path(__file__)),
        "isolated_module_attribution": False, "performance_accepted": False,
        "limitations": [
            "A single combined-system run does not establish repeatability or all-baseline superiority.",
            "Logical JOIN grants and later service are not proof of physical free capacity or hidden DMA.",
            "Sibling request-admission records are not independent JOINs or service-opportunity denominators.",
            "Worker intervals include runtime and dependencies; complete control CPU remains unmeasured.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({field: result[field] for field in (
        "requests", "prompt_tokens", "output_tokens", "mean_jct_reduction_percent",
        "end_to_end_output_increase_percent", "faster_workflows", "slower_workflows",
    )}, indent=2))


if __name__ == "__main__":
    main()
