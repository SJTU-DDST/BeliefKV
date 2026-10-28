"""Review the complete pre-admission Full against its exact frozen controls."""

from collections import Counter
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_join_reentry_service import identity
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_subagent_retention_capacity import recompute_proxy
from scripts import run_causal_comparison as causal
from scripts.summarize_semantic_h2d_ab import capacity_pressure_watch

OPS = Path("/home/longhao/experiment/ops")
REGISTRATIONS = {
    "full": (
        OPS / "tool_offload_before_admission_20261027_r1.registration.json",
        "full", "Full",
    ),
    "thunderagent_acting_decay": (
        OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json",
        "thunderagent_acting_decay", "ThunderAgent decay",
    ),
}
AUDITORS = {
    "complete_restore_churn": "scripts/audit_agent_restore_churn.py",
    "complete_jct_paths": "scripts/audit_causal_jct_paths.py",
    "complete_sampled_restore_waits": "scripts/audit_restore_wait_cohorts.py",
    "complete_tool_reentries": "scripts/audit_tool_reentry_service.py",
    "complete_tool_admission_wait": "scripts/audit_agent_admission_wait.py",
    "complete_capacity_waits": "scripts/audit_reference_capacity_waits.py",
    "complete_grant_service": "scripts/audit_reference_join_grants.py",
    "complete_guard_cases": "scripts/audit_tool_eos_guard_cases.py",
    "complete_eos_trigger_lifecycle": "scripts/audit_eos_trigger_lifecycle.py",
}


def verify_seals(value, inputs, allowed_arms):
    if isinstance(value, list):
        for child in value:
            verify_seals(child, inputs, allowed_arms)
    elif isinstance(value, dict):
        for source, prefix in value.get("input_jsonl_prefixes", {}).items():
            path = Path(source).resolve()
            if not any(path.is_relative_to(arm) for arm in allowed_arms):
                raise ValueError(f"sealed audit input belongs to another arm: {source}")
            if (
                path.stat().st_size != prefix["complete_prefix_bytes"]
                or sha256_file(path) != prefix["complete_prefix_sha256"]
            ):
                raise ValueError(f"audit did not seal the complete immutable file: {source}")
            inputs[str(path)] = prefix["complete_prefix_sha256"]
        for child in value.values():
            verify_seals(child, inputs, allowed_arms)


def early_service(arm, unit):
    """Preserve unknown release-time identity and actual freed-page consumers."""
    reader = SealedRecords()
    releases = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "predicted_tool_kv_evicted"
        and row.get("dispatch_stage") == "before_native_admission"
    ]
    receipts = {
        row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")
    }
    submits = {}
    for row in reader(arm / "server/runtime_events.sglang.jsonl"):
        if row.get("kind") == "llm_submit":
            rid = row["attributes"]["request_id"]
            if rid in submits:
                raise ValueError("ambiguous native submission")
            submits[rid] = row
    consumers = {}
    for release in releases:
        if (
            release["evidence"] != "current_prediction;settled_host;native_unlocked_private_leaf"
            or type(release["freed_bytes"]) is not int or release["freed_bytes"] <= 0
            or release["freed_bytes"] % unit
            or release["freed_units"] != {"0": release["freed_bytes"] // unit}
        ):
            raise ValueError("early release native evidence or bytes do not reconcile")
        rid = release["beneficiary_request_id"]
        row = consumers.setdefault(rid, {
            "request_id": rid, "context_id": release["beneficiary_context_id"],
            "release_records": 0, "cumulative_associated_released_bytes": 0,
            "first_release_ts_ms": release["ts_ms"], "last_release_ts_ms": release["ts_ms"],
            "first_native_service_ts_ms": None,
            "status": "completion_or_service_unproven",
            "attempt_identity_at_release_verified": False,
            "freed_page_allocation_to_consumer_verified": False,
        })
        if row["context_id"] != release["beneficiary_context_id"]:
            raise ValueError("beneficiary context changed")
        row["release_records"] += 1
        row["cumulative_associated_released_bytes"] += release["freed_bytes"]
        row["first_release_ts_ms"] = min(row["first_release_ts_ms"], release["ts_ms"])
        row["last_release_ts_ms"] = max(row["last_release_ts_ms"], release["ts_ms"])
    for batch in reader(arm / "server/runtime_audit.jsonl"):
        if batch.get("event") != "gpu_service_sample":
            continue
        for sample in batch.get("request_samples", ()):
            rid = sample.get("request_id")
            row, submit = consumers.get(rid), submits.get(rid)
            if (
                row is None or submit is None or identity(submit) is None
                or identity(sample) != identity(submit)
            ):
                continue
            if (
                submit["context_id"] != row["context_id"]
                or submit["ts_ms"] > row["first_release_ts_ms"]
            ):
                raise ValueError("early release consumer was not already submitted")
            started = batch["service_start_ts_ms"]
            previous = row["first_native_service_ts_ms"]
            row["first_native_service_ts_ms"] = (
                started if previous is None else min(started, previous)
            )
            row["native_service_identity"] = list(identity(sample))
            row["native_service_clock_domain"] = batch["monotonic_clock_domain"]
    for rid, row in consumers.items():
        receipt, submit = receipts.get(rid), submits.get(rid)
        native = (receipt or {}).get("native_receipt") or {}
        started = row["first_native_service_ts_ms"]
        if (
            receipt is None or submit is None or started is None
            or native.get("request_id") != rid
            or native.get("identity") != row["native_service_identity"]
            or native.get("clock_domain") != row["native_service_clock_domain"]
            or type(native.get("session_id")) is not str or not native["session_id"]
            or type(native.get("session_generation")) is not int
            or native["session_generation"] < 0
        ):
            continue
        if started < row["last_release_ts_ms"]:
            raise ValueError("claimed early release followed first actual service")
        row.update(
            status="completed_request_native_service_after_early_release",
            last_release_to_native_run_batch_entry_ms=started - row["last_release_ts_ms"],
            native_session_id=native["session_id"],
            native_session_generation=native["session_generation"],
            native_host_hit_tokens=submit["attributes"].get("cached_tokens_host"),
        )
    return {
        "summary": {
            "early_node_release_events": len(releases),
            "early_node_released_bytes": sum(row["freed_bytes"] for row in releases),
            "distinct_consumer_requests": len(consumers),
            "consumer_statuses": dict(Counter(row["status"] for row in consumers.values())),
        },
        "consumers": list(consumers.values()),
        "input_jsonl_prefixes": reader.prefixes,
        "limitations": [
            "The journal lacks full release-time consumer attempt/session identity.",
            "No freed-index list binds those exact pages to consumer allocation.",
            "Run-batch entry is not CUDA kernel start or time saved.",
            "Released bytes are cumulative associations, not simultaneous free capacity.",
        ],
    }


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve complete pre-admission comparison evidence")
    directory = output.parent
    figures = ROOT / "experiments/figures/tool_offload_before_admission_20261027_r1"
    figure_path = figures / "evidence.json.gz"
    plot_state = OPS / "tool_offload_before_admission_20261027_r1.plots.state.json"
    if causal.load(plot_state).get("status") != "complete":
        raise ValueError("complete matched figures are not available")
    plotted = json.loads(gzip.decompress(figure_path.read_bytes()))
    inputs = {str(path): sha256_file(path) for path in (figure_path, plot_state)}
    work, contract = None, None
    metrics, elapsed, proofs, snapshots, arms = {}, {}, {}, {}, {}
    for name, (registration, label, figure_label) in REGISTRATIONS.items():
        plan = causal.load(registration)
        causal.comparison.verify_sources(plan)
        if not causal.predecessor_ready({**plan, "after_registration": str(registration)}):
            raise ValueError("complete comparison requires actual owned exit")
        if plan["quality_exclusions"]:
            raise ValueError("normal workflow tails were excluded")
        arm = (Path(plan["run_root"]) / label).resolve()
        arms[name] = arm
        summary_path = arm / "replay/summary.json"
        summary = causal.load(summary_path)
        current_work = causal.validate_result(plan, summary, arm, live_control=True)
        if work is not None and current_work != work:
            raise ValueError("comparison changed exact requests, tokens or stopping")
        work = current_work
        causal.observed_configuration(plan, label, arm, final=True)
        drain_path, cleanup_path = arm / "telemetry_drain.json", arm / "owned_process_cleanup.json"
        drain, cleanup = causal.load(drain_path), causal.load(cleanup_path)
        idle = drain["drained_status"]
        if (
            not drain["previous_zero_snapshot_ts_ms"] < idle["snapshot_ts_ms"]
            or any(idle.get(key) != 0 for key in (
                "pending_request_count", "pending_batch_count", "writer_queue_depth",
                "failed_records", "dropped_records",
            ))
            or idle.get("writer_error") is not None or cleanup["remaining_processes"]
            or any(causal.reservation.alive(item) for item in cleanup["identities"])
        ):
            raise ValueError("complete drain or actual cleanup is missing")
        figure = plotted["arms"][figure_label]
        if (
            figure["code_commit"] != plan["code_commit"]
            or contract is not None and figure["match_contract"] != contract
        ):
            raise ValueError("figure contract changed common work or serving")
        contract = figure["match_contract"]
        for source, digest in figure["evidence_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("plotted input changed")
            inputs[source] = digest
        verify_seals(figure, inputs, (arm,))
        observed = causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
        if (
            abs(figure["mean_jct_s"] * 1000. - observed["mean_jct_ms"]) > .01
            or abs(figure["makespan_s"] * 1000. - observed["workflow_event_makespan_ms"]) > .01
        ):
            raise ValueError("complete JCT or makespan clocks differ")
        metrics[name] = {
            key: figure[key] for key in (
                "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
            )
        }
        for key in ("workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms"):
            metrics[name][key] = summary["streaming_response_metrics"][key]
        pressure = capacity_pressure_watch(causal.load(arm / "server/native_telemetry_status.json"))
        reader = SealedRecords()
        reuse = recompute_proxy(reader(arm / "server/eviction_attribution.jsonl"))
        common = pressure["same_session_common_input"]
        if (
            reuse["common_previously_served_input_tokens"] != common["common_tokens"]
            or reuse["missing_previously_served_prefix_proxy_tokens"] != common["missing_from_cache_tokens"]
        ):
            raise ValueError("complete prefix probes disagree with native counters")
        verify_seals({"input_jsonl_prefixes": reader.prefixes}, inputs, (arm,))
        metrics[name]["capacity_pressure"] = pressure
        metrics[name]["same_session_prefix_probe"] = reuse
        elapsed[name] = summary["workflow_elapsed_ms"]
        snapshots[name] = causal.qualified.last_runtime_state(
            arm / "opportunities/admission_opportunities.jsonl",
        )
        proofs[name] = {
            "registration": str(registration), "code_commit": plan["code_commit"],
            "engine_tree": plan["engine_tree"], "exact_completed_requests": len(work),
        }
        for path in (
            registration, registration.with_suffix(".state.json"), summary_path,
            drain_path, cleanup_path, arm / "server/native_telemetry_status.json",
        ):
            inputs[str(path)] = sha256_file(path)
    full_registration = REGISTRATIONS["full"][0]
    full_plan = causal.load(full_registration)
    if any(full_plan["reference_environment"].get(key) != "1" for key in (
        "TOOL_OFFLOAD_BEFORE_ADMISSION", "BELIEFKV_TOOL_OFFLOAD_BEFORE_ADMISSION",
    )):
        raise ValueError("review is not bound to pre-admission offload")
    supplemental, auditor_sources = {}, {}
    for name, auditor in AUDITORS.items():
        source = directory / f"{name}.json"
        report = causal.load(source)
        auditor_sources[auditor] = sha256_file(ROOT / auditor)
        recorded = report.get("auditor_sha256")
        if recorded is not None and recorded != auditor_sources[auditor]:
            raise ValueError(f"auditor source changed: {name}")
        for script, digest in report.get("auditor_source_sha256", {}).items():
            if sha256_file(ROOT / script) != digest:
                raise ValueError(f"audit helper source changed: {script}")
        sealed_report = report
        if name == "complete_jct_paths":
            source_arm = Path(full_plan["source_arm"]).resolve()
            if (
                Path(report["source_evidence"]["source_arm"]).resolve() != source_arm
                or report["source_graph"] != full_plan["source_plan"]
            ):
                raise ValueError("JCT audit changed the registered original source graph")
            verify_seals(report["source_evidence"], inputs, (source_arm,))
            sealed_report = {
                key: value for key, value in report.items() if key != "source_evidence"
            }
        verify_seals(sealed_report, inputs, tuple(arms.values()))
        if "arms" in report:
            if set(report["arms"]) != {"Full", "ThunderAgent decay"}:
                raise ValueError(f"incomplete arm population: {name}")
            for label, method in (
                ("Full", "full"), ("ThunderAgent decay", "thunderagent_acting_decay"),
            ):
                observed_arm = report["arms"][label].get("arm")
                if observed_arm is not None and Path(observed_arm).resolve() != arms[method]:
                    raise ValueError(f"audit belongs to another arm: {name}")
                verify_seals(report["arms"][label], inputs, (arms[method],))
        elif "arm" in report and Path(report["arm"]).resolve() != arms["full"]:
            raise ValueError(f"audit belongs to another Full: {name}")
        for path_field, digest_field in (
            ("report", "report_sha256"), ("reentries", "reentries_sha256"),
        ):
            if path_field in report:
                dependency = directory / "complete_tool_reentries.json"
                if (
                    Path(report[path_field]).resolve() != dependency.resolve()
                    or report[digest_field] != sha256_file(dependency)
                ):
                    raise ValueError(f"tool-wait audit used different evidence: {name}")
        supplemental[name] = report
        inputs[str(source)] = sha256_file(source)
    for name in ("complete_guard_cases", "complete_eos_trigger_lifecycle"):
        report = supplemental[name]
        if (
            report["registration_sha256"] != sha256_file(full_registration)
            or report["code_commit"] != full_plan["code_commit"]
            or report["exact_completed_receipts"] != len(work)
        ):
            raise ValueError("complete EOS audit belongs to different work")
    guard = supplemental["complete_guard_cases"]
    if guard["summary"]["known_cases"] != 4 or guard["summary"]["all_cases_verified"] is not True:
        raise ValueError("known tool-EOS cases do not all verify")
    prefetch_path = directory / "complete_prefetch.json.gz"
    prefetch = json.loads(gzip.decompress(prefetch_path.read_bytes()))
    if (
        Path(prefetch["arm"]).resolve() != arms["full"]
        or prefetch["collection_code_commit"] != full_plan["code_commit"]
    ):
        raise ValueError("prefetch audit belongs to another frozen arm")
    verify_seals(prefetch, inputs, (arms["full"],))
    for script, digest in prefetch["analysis_source_sha256"].items():
        if sha256_file(ROOT / script) != digest:
            raise ValueError("prefetch audit source changed")
        auditor_sources[script] = digest
    inputs[str(prefetch_path)] = sha256_file(prefetch_path)
    grants = supplemental["complete_grant_service"]
    if grants["report_inputs_sha256"].get(str(prefetch_path.resolve())) != inputs[str(prefetch_path)]:
        raise ValueError("grant audit used a different prefetch report")
    grant_counts = Counter((row["grant_event"], row["status"]) for row in grants["programs"])
    if (
        sum(grant_counts.values()) != grants["summary"]["journal_programs"]
        or any(row["status"] != "service_after_grant_verified" for row in grants["programs"])
    ):
        raise ValueError("not all complete-run grants bind to native service")
    previous_path = ROOT / "experiments/reports/reference_free_continuation_20261027/complete_review.json"
    previous = causal.load(previous_path)
    if (
        previous["requests"] != len(work)
        or previous["proofs"]["thunderagent_acting_decay"] != proofs["thunderagent_acting_decay"]
    ):
        raise ValueError("previous Full used different work or control")
    inputs[str(previous_path)] = sha256_file(previous_path)
    unit, remainder = divmod(
        full_plan["source_capacity"]["host_full_bytes"],
        full_plan["source_capacity"]["host_full_tokens"],
    )
    if remainder or unit != 98304:
        raise ValueError("unexpected FULL-only native geometry")
    early = early_service(arms["full"], unit)
    verify_seals(early, inputs, (arms["full"],))
    full, control = metrics["full"], metrics["thunderagent_acting_decay"]
    paired = [{
        "workflow_id": rid, "full_jct_s": elapsed["full"][rid] / 1000.,
        "thunderagent_jct_s": when / 1000.,
        "difference_s": (elapsed["full"][rid] - when) / 1000.,
    } for rid, when in sorted(elapsed["thunderagent_acting_decay"].items())]
    previous_full = previous["metrics"]["full"]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "complete_matched_pre_admission_offload_full_vs_existing_v345_decay_control",
        "requests": len(work), "prompt_tokens": sum(row[0] for row in work.values()),
        "output_tokens": sum(row[1] for row in work.values()),
        "same_exact_tokens_and_native_stopping_verified": True,
        "all_normal_tails_retained": True, "two_fresh_idle_snapshots_verified": True,
        "all_owned_processes_exited": True, "proofs": proofs, "metrics": metrics,
        "mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / control["mean_jct_s"]),
        "end_to_end_throughput_increase_percent": 100. * (
            full["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1.
        ),
        "paired_workflow_jct": paired,
        "faster_workflows": sum(row["difference_s"] < 0 for row in paired),
        "slower_workflows": sum(row["difference_s"] > 0 for row in paired),
        "previous_free_continuation_full": {
            "metrics": previous_full,
            "mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / previous_full["mean_jct_s"]),
            "end_to_end_throughput_increase_percent": 100. * (
                full["end_to_end_output_tokens_per_s"] / previous_full["end_to_end_output_tokens_per_s"] - 1.
            ),
        },
        "transfers": {
            name: row["transfers"]
            for name, row in supplemental["complete_restore_churn"]["arms"].items()
        },
        "known_counterexample_summary": guard["summary"],
        "all_observed_eos_trigger_summary": supplemental["complete_eos_trigger_lifecycle"]["summary"],
        "prefetch_summary": {
            "copies_by_source": {
                name: {key: row[key] for key in (
                    "issued_commands", "acknowledged_commands", "actual_bytes",
                    "full_verified_reused_bytes_known", "full_identity_not_verified_bytes_known",
                )}
                for name, row in prefetch["summary"]["by_source"].items()
            },
            "wait_events_by_source": {
                name: {key: row[key] for key in (
                    "observed_events", "events_with_observed_full_plan",
                    "events_with_early_reused_full", "early_started_and_reused_full_bytes",
                    "completion_boundary_unknown_events",
                )}
                for name, row in prefetch["wait_event_attribution"]["summary"]["by_source"].items()
            },
        },
        "grant_service_summary": grants["summary"],
        "grant_service_statuses_by_event": [
            {"event": event, "status": status, "programs": count}
            for (event, status), count in sorted(grant_counts.items())
        ],
        "early_offload_service": early,
        "sampled_tool_wait_summary": supplemental["complete_tool_admission_wait"]["summary"],
        "sampled_capacity_wait_summary": supplemental["complete_capacity_waits"]["summary"],
        "final_runtime_snapshots": snapshots, "figures_directory": str(figures),
        "inputs_sha256": inputs, "auditor_sources_sha256": auditor_sources,
        "capacity_helper_sources_sha256": {
            script: sha256_file(ROOT / script) for script in (
                "scripts/audit_subagent_retention_capacity.py",
                "scripts/summarize_semantic_h2d_ab.py",
                "scripts/audit_join_reentry_service.py",
            )
        },
        "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False, "repeated_run_acceptance": False,
        "all_baseline_goal_complete": False,
        "limitations": [
            "Service and release associations do not establish a causal module speedup.",
            "Changed Full configurations are not independent repeated trials.",
            "Worker intervals include runtime and waits; fixed-work output throughput depends on makespan.",
            "Sampled GPU/admission waits are not expanded or subtracted from JCT.",
            "Verified copied/reused bytes do not establish hidden GPU time or removable JCT.",
            "No progressive layer-capacity handoff ran in this experiment.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({key: result[key] for key in (
        "requests", "prompt_tokens", "output_tokens", "mean_jct_reduction_percent",
        "end_to_end_throughput_increase_percent", "faster_workflows", "slower_workflows",
    )}, indent=2))


if __name__ == "__main__":
    main()
