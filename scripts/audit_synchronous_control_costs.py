"""Audit existing scheduler CPU counters and sampled phases without GPU work."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.analyze_prefill_turnover import key, request_ids, validate_phase
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.stop_after_measured_cohort import replay_arm
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


SOURCES = (
    "beliefkv/runtime/reference_tool_replacement.py",
    "beliefkv/runtime/predicted_tool_offload.py",
    "beliefkv/runtime/reference_free_continuation.py",
    "beliefkv/runtime/reference_thunder_admission.py",
    "beliefkv/runtime/reference_residency_pause.py",
    "beliefkv/runtime/workflow_size_admission.py",
    "beliefkv/runtime/workflow_combination.py",
    "beliefkv/runtime/sglang_v0520_runtime.py",
    "beliefkv/runtime/prefill_turnover_probe.py",
    "beliefkv/runtime/hotpath_timing.py",
)
EVENT_TIMERS = {
    "workflow_combination_selection": (
        "metadata_and_selection_cpu_ns", "metadata_and_selection_wall_ns",
    ),
    "agent_periodic_admission_decision": ("control_and_observation_cpu_ns_before_write",),
    "workflow_size_resume_selection": (
        "thread_cpu_us", "wall_us", "control_thread_cpu_us", "control_wall_us",
    ),
}
ACTION_EVENTS = (
    "agent_predicted_tool_replacement", "agent_dispatch_free_capacity_admitted",
    "agent_join_blocked_capacity_admitted", "predicted_tool_offload_backup",
    "predicted_tool_kv_evicted",
)


def seconds(name, value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("timing counters must be finite and nonnegative")
    unit_name = name.removesuffix("_before_write")
    for suffix, scale in (("_ns", 1e9), ("_us", 1e6), ("_ms", 1e3)):
        if unit_name.endswith(suffix):
            return value / scale
    raise ValueError("timing counter lacks a declared unit")


def timing_counters(state):
    admission = state.get("agent_admission") or {}
    predictor = admission.get("workflow_work_predictor") or {}
    roots = {
        "runtime": state.get("counts", {}),
        "admission": admission.get("counts", {}),
        "workflow_predictor": predictor.get("counts", {}),
        "paper": (state.get("paper_baseline") or {}).get("counts", {}),
    }
    return {
        group + "." + name: seconds(name, value)
        for group, values in roots.items() for name, value in values.items()
        if any(token in name for token in ("cpu", "wall"))
        and name.endswith(("_ns", "_us", "_ms"))
    }


def bracket_counters(snapshots, window):
    """Bound an interval from monotonic cumulative snapshots; never interpolate."""
    if not snapshots or not 0 <= window[0] < window[1]:
        raise ValueError("counter audit needs snapshots and a nonempty window")
    previous, values = -math.inf, {}
    for row in snapshots:
        if not math.isfinite(row["monotonic_ms"]) or row["monotonic_ms"] < previous:
            raise ValueError("counter snapshots are not chronologically ordered")
        previous = row["monotonic_ms"]
        for name, value in row["counters_seconds"].items():
            if not math.isfinite(value) or value < values.get(name, 0.) - 1e-9:
                raise ValueError("a cumulative timing counter decreased")
            values[name] = value
    brackets = {}
    for label, boundary in zip(("start", "end"), window):
        before = [row for row in snapshots if row["monotonic_ms"] <= boundary]
        after = [row for row in snapshots if row["monotonic_ms"] >= boundary]
        brackets[label] = {
            "before": before[-1] if before else None,
            "after": after[0] if after else None,
        }
    complete = all(row is not None for part in brackets.values() for row in part.values())
    totals = {}
    for name, latest in values.items():
        value = {"latest_snapshot_cumulative_seconds": latest}
        if complete:
            lo = (
                brackets["end"]["before"]["counters_seconds"].get(name, 0.)
                - brackets["start"]["after"]["counters_seconds"].get(name, 0.)
            )
            hi = (
                brackets["end"]["after"]["counters_seconds"].get(name, 0.)
                - brackets["start"]["before"]["counters_seconds"].get(name, 0.)
            )
            value["common_window_accounted_counter_seconds"] = {
                "lower": max(0., lo), "upper": max(0., hi),
            }
        totals[name] = value
    return {
        "counters": totals, "complete_common_window_brackets": complete,
        "boundary_snapshots_ms": {
            label: {
                side: row["monotonic_ms"] if row is not None else None
                for side, row in part.items()
            } for label, part in brackets.items()
        },
        "counter_count": len(totals),
    }


def inspect_opportunities(records, window):
    snapshots, event_values = [], defaultdict(lambda: defaultdict(list))
    unknown_action_clocks = Counter()
    event_counts = {"whole": Counter(), "common_window": Counter()}
    last_state = None
    for sequence, row in enumerate(records):
        event = row.get("event")
        if event == "admission_runtime_state":
            last_state = row
            snapshots.append({
                "monotonic_ms": row["monotonic_ms"],
                "counters_seconds": timing_counters(row),
                "journal_sequence": sequence, "final": row.get("final"),
            })
        when = row.get("monotonic_s")
        in_window = type(when) in (int, float) and window[0] <= when * 1000. < window[1]
        if event in ACTION_EVENTS:
            event_counts["whole"][event] += 1
            if in_window:
                event_counts["common_window"][event] += 1
            elif type(when) not in (int, float):
                unknown_action_clocks[event] += 1
        if event not in EVENT_TIMERS:
            continue
        for name in EVENT_TIMERS[event]:
            if name not in row:
                continue
            value = seconds(name, row[name])
            event_values["whole"][event + "." + name].append(value)
            if in_window:
                event_values["common_window"][event + "." + name].append(value)
    if last_state is None:
        raise ValueError("no runtime state in the sealed opportunity journal")
    admission = last_state.get("agent_admission") or {}
    counts = admission.get("counts", {})
    replacements = {
        name: counts.get(name) for name in (
            "tool_event_replacement_choices", "tool_event_replacement_no_complete_pair",
            "tool_event_replacement_grants", "tool_event_replacement_backup_attempts",
            "tool_event_replacement_cpu_ns", "tool_event_replacement_logical_window_skips",
            "streaming_progress_checks",
            "streaming_progress_updates",
        )
    }
    choices = replacements["tool_event_replacement_choices"]
    if choices:
        replacements["grants_per_nonempty_choice"] = (
            (replacements["tool_event_replacement_grants"] or 0) / choices
        )
        replacements["no_complete_pair_per_nonempty_choice"] = (
            (replacements["tool_event_replacement_no_complete_pair"] or 0) / choices
        )
    return {
        **bracket_counters(snapshots, window),
        "runtime_snapshots": len(snapshots),
        "latest_snapshot": {
            name: last_state.get(name) for name in (
                "ts_ms", "monotonic_ms", "final", "profile_shared_path", "shared_path_timing",
            )
        },
        "replacement_counters_at_latest_snapshot": replacements,
        "observed_action_records": {scope: dict(values) for scope, values in event_counts.items()},
        "action_records_without_monotonic_clock_not_assigned_to_common_window": dict(unknown_action_clocks),
        "individual_event_timing_seconds": {
            scope: {name: distribution(values) for name, values in fields.items()}
            for scope, fields in event_values.items()
        },
        "background_writer_sampled_timing": last_state.get("opportunity_writer_timing"),
    }


def summarize_probe_rows(rows, window):
    identities = [key(row) for row in rows]
    if None in identities or len(identities) != len(set(identities)):
        raise ValueError("sampled scheduler phases have missing or duplicate batch identities")
    summaries = {}
    for scope in ("whole", "common_window"):
        phases, counts = defaultdict(list), Counter()
        adjacent = defaultdict(list)
        for row in rows:
            if row.get("event") != "prefill_turnover_sample":
                counts["incomplete_samples_excluded"] += 1
                continue
            if key(row) is None or not row.get("thread_cpu_clock_domain"):
                raise ValueError("sampled scheduler phases lack their exact clock identity")
            run = row.get("phases", {}).get("run_batch")
            if not validate_phase(run):
                raise ValueError("sampled scheduler phases lack a valid run boundary")
            if scope == "common_window" and not (
                window[0] <= run["start_monotonic_ms"] < window[1]
            ):
                continue
            counts["complete_prefill_samples"] += 1
            for name, phase in row["phases"].items():
                if not validate_phase(phase):
                    raise ValueError("invalid recorded scheduler phase")
                phases[name].append(phase)
            transition = row.get("adjacent_transition", {})
            attribution = transition.get("attribution", {})
            if attribution.get("status") == "observed":
                if any(not math.isclose(
                    sum(part[metric] for part in attribution["segments"]),
                    attribution[metric], abs_tol=1e-6,
                ) for metric in ("wall_ms", "thread_cpu_ms")):
                    raise ValueError("adjacent phase partition does not conserve its interval")
                counts["complete_adjacent_attributions"] += 1
                for segment in attribution["segments"]:
                    adjacent[
                        segment.get("name") or "between:" + segment["from"] + ":" + segment["to"]
                    ].append(segment)
        summaries[scope] = {
            "counts": dict(counts),
            "phases": {
                name: {
                    metric: distribution(phase[metric] for phase in values)
                    for metric in ("thread_cpu_ms", "wall_ms")
                } for name, values in phases.items()
            },
            "adjacent_phase_segments": {
                name: {
                    metric: distribution(phase[metric] for phase in values)
                    for metric in ("thread_cpu_ms", "wall_ms")
                } for name, values in adjacent.items()
            },
        }
    return summaries


def match_probe_work(probes, services):
    matched, excluded = [], []
    for probe in probes:
        service = services.get(key(probe))
        evidence = {
            "identity": key(probe), "probe_request_ids": request_ids(probe),
            "probe_extend_tokens": probe.get("extend_tokens"),
            "probe_prefix_tokens": probe.get("prefix_tokens"),
        }
        if service is None:
            excluded.append({**evidence, "reason": "no_exact_native_prefill_service"})
            continue
        evidence.update(
            service_request_ids=request_ids(service, service=True),
            service_extend_tokens=sum(row["token_delta"] for row in service["request_samples"]),
            service_prefix_tokens=sum(
                row["sequence_tokens_before"] for row in service["request_samples"]
            ),
        )
        if (
            evidence["probe_request_ids"] != evidence["service_request_ids"]
            or evidence["probe_extend_tokens"] != evidence["service_extend_tokens"]
            or evidence["probe_prefix_tokens"] != evidence["service_prefix_tokens"]
        ):
            excluded.append({**evidence, "reason": "native_identity_or_geometry_disagreement"})
        else:
            matched.append(probe)
    return matched, excluded


def inspect_run(registration, cohort_path):
    plan, cohort = (causal.load(path) for path in (registration, cohort_path))
    causal.comparison.verify_sources(plan)
    execution = verify_execution(registration, cohort_path)
    arm, reader = replay_arm(plan), SealedRecords()
    offsets = {
        row["workflow_id"]: row["offset_seconds"]
        for row in plan["workflow_arrivals"]["arrival_schedule"]
    }
    origins = [
        row["ts_ms"] - offsets[row["workflow_id"]] * 1000.
        for row in reader(arm / "replay/runtime_events.replay.jsonl")
        if row["kind"] == "workflow_start"
    ]
    if not origins or max(origins) - min(origins) > .00001:
        raise ValueError("the replay's arrival clock origin changed")
    window = [origins[0] + value * 1000. for value in cohort["throughput_window_offset_seconds"]]
    opportunity = inspect_opportunities(
        reader(arm / "opportunities/admission_opportunities.jsonl"), window,
    )
    native_status_path = arm / "server/native_telemetry_status.json"
    native_status = causal.load(native_status_path)
    audit_path = arm / "server/runtime_audit.jsonl"
    probes, services, consumed, digest = [], {}, 0, hashlib.sha256()
    limit = audit_path.stat().st_size
    with audit_path.open("rb") as stream:
        while consumed < limit:
            line = stream.readline(limit - consumed)
            if not line.endswith(b"\n"):
                raise ValueError("completed native journal has an incomplete tail")
            consumed += len(line)
            digest.update(line)
            if any(marker in line for marker in (
                b'"prefill_turnover_sample"', b'"prefill_turnover_incomplete"', b'"gpu_service_sample"',
            )):
                row = json.loads(line)
                if row.get("event") in ("prefill_turnover_sample", "prefill_turnover_incomplete"):
                    probes.append(row)
                elif row.get("event") == "gpu_service_sample" and row.get("phase") == "prefill":
                    identity = key(row)
                    if identity is None or identity in services:
                        raise ValueError("native prefill services have missing or duplicate identities")
                    services[identity] = row
    prefix = {
        "complete_prefix_bytes": consumed, "complete_prefix_sha256": digest.hexdigest(),
        "first_read_limit_bytes": limit,
    }
    reader.prefixes[str(audit_path)] = prefix
    if sum(row["event"] == "prefill_turnover_sample" for row in probes) != (
        native_status.get("prefill_turnover_probe", {}).get("counts", {}).get("completed_samples", 0)
    ):
        raise ValueError("sampled scheduler phases differ from the drained telemetry status")
    matched_probes, excluded_probes = match_probe_work(probes, services)
    source_root = Path(plan["execution_checkout"])
    frozen_sources = {
        str((source_root / name).resolve()): sha256_file(source_root / name) for name in SOURCES
    }
    return {
        "registration": str(registration.resolve()), "arm": arm.name,
        "complete_measured_work": execution["work"],
        "common_window_monotonic_ms": window,
        "common_window_seconds": (window[1] - window[0]) / 1000.,
        "opportunities": opportunity,
        "sampled_native_scheduler_phases": summarize_probe_rows(matched_probes, window),
        "sampled_native_scheduler_phase_exclusions": excluded_probes,
        "native_probe_configuration": native_status.get("prefill_turnover_probe"),
        "native_background_writer_sampled_timing": native_status.get("background_writer_timing"),
        "input_jsonl_prefixes": reader.prefixes,
        "frozen_control_sources_sha256": frozen_sources,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                registration, cohort_path, Path(plan["experiment_configuration"]), native_status_path,
            )
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, action="append", required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve earlier audits and use nice19 with hidden CUDA")
    if len({path.resolve() for path in args.registration}) != len(args.registration):
        raise ValueError("do not count the same execution twice")
    result = {
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "runs": [inspect_run(path, args.cohort) for path in args.registration],
        "semantics": [
            "Latest cumulative snapshots are observed execution prefixes, not exact shutdown totals when final is false.",
            "Common-window bounds bracket counter accounting updates, not the execution interval of each timed function.",
            "A counter update can include work that started before its publication boundary.",
            "Inclusive counters overlap; periodic control includes resume and periodic combination selection.",
            "Tool replacement includes event drain, capacity/progress observation, ranking, checks and any actions.",
            "Workflow predictor worker and record-writer CPU belong to other threads, not synchronous scheduler time.",
            "Sampled phase and writer costs are measured samples only; do not multiply by the sampling interval.",
            "Run-batch thread CPU includes model submission and is not solely scheduling overhead.",
            "Post-launch synchronous CPU can overlap GPU work; none of these counters proves exposed GPU idle or removable JCT.",
            "Disabled shared-path timing leaves unmeasured synchronous controls unknown.",
            "No policy, workload, queued source, GPU component or performance acceptance is changed.",
        ],
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                Path(__file__), ROOT / "scripts/verify_measured_execution.py",
                ROOT / "scripts/audit_sealed_wait_prefetch.py",
                ROOT / "scripts/analyze_prefill_turnover.py",
            )
        },
        "GPU_action": False, "performance_accepted": False,
    }
    write(args.output, result)
    for run in result["runs"]:
        print(json.dumps({
            "registration": run["registration"],
            "timing_counters": run["opportunities"]["counters"],
            "replacement": run["opportunities"]["replacement_counters_at_latest_snapshot"],
        }), flush=True)


if __name__ == "__main__":
    main()
