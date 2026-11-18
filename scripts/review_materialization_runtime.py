"""Read completed materialization experiments without changing their service."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_metrics import distribution
from scripts import run_causal_comparison as causal
from scripts.analyze_materialization_ready_supply import request_epochs
from scripts.audit_decode_batch_efficiency import accumulate, summarize
from scripts.audit_host_block_prefill import OWNER_FIELDS, inspect as inspect_host_loss
from scripts.audit_previous_prefix_prefill import inspect as inspect_prefix
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_synchronous_control_costs import bracket_counters, timing_counters
from scripts.plot_paused_agent_timeline import samples as paused_samples
from scripts.run_continuous_matched_campaign import write as atomic_write
from scripts.stop_after_measured_cohort import replay_arm
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


SUPPLY_FIELDS = (
    "ready_paused", "ready_paused_excluding_held", "continuation_ready_paused",
    "continuation_ready_workflows", "first_call_ready_paused", "held_ready_paused",
    "held_initial_requests", "active_ready", "join_paused", "tool_paused",
)


def verified_performance(report, cohort, certificate):
    values = report.get("measured_workflow_JCT_ms") or {}
    if (
        report.get("cohort") != cohort or report.get("all_measured_completed") is not True
        or report.get("normal_tails_removed") is not False
        or set(values) != set(cohort["measured_workflow_ids"])
        or values != certificate["measured_JCT_ms"]
        or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
               for value in values.values())
        or report.get("measured_complete_workflow_JCT_ms") != distribution(values.values())
    ):
        raise ValueError("JCT report differs from the exact complete cohort certificate")
    return {
        "mean_complete_measured_JCT_s": sum(values.values()) / len(values) / 1000.,
        "common_window_output_tokens_per_second": report[
            "common_window_output_tokens_per_second"
        ],
        "common_window_decode_tokens_per_second": report[
            "common_window_decode_tokens_per_second"
        ],
    }


def supply_samples(events, states, arrivals, expected, *, gate_enabled):
    """Check exact first-call identity separately from the logical pause partition."""
    rows, origin = paused_samples(events, states, arrivals)
    states = [state for state in states if state["monotonic_ms"] >= origin]
    first = request_epochs(expected)
    events = sorted(events, key=lambda event: event["ts_ms"])
    pending, tools, joins, completed = {}, set(), {}, set()
    cursor = 0
    for row, state in zip(rows, states, strict=True):
        while cursor < len(events) and events[cursor]["ts_ms"] <= state["monotonic_ms"]:
            event = events[cursor]
            cursor += 1
            kind, owner = event["kind"], event.get("invocation_id")
            attrs = event.get("attributes") or {}
            if kind == "llm_submit":
                pending[owner] = attrs["request_id"]
            elif kind == "llm_result":
                pending.pop(owner, None)
                completed.add(owner)
            elif kind in ("tool_start", "tool_end"):
                key = owner, attrs.get("tool_run_id") or attrs.get("tool_call_id")
                tools.add(key) if kind == "tool_start" else tools.discard(key)
            elif kind == "join_wait":
                joins[event["workflow_id"], event.get("join_id")] = owner
            elif kind in ("join_satisfied", "join_timeout"):
                joins.pop((event["workflow_id"], event.get("join_id")), None)
        blocked = {key[0] for key in tools} | set(joins.values())
        paused = set(state["agent_admission"]["paused_invocation_ids"])
        ready = {owner: rid for owner, rid in pending.items() if owner in paused - blocked}
        if len(ready) != row["ready_paused"]:
            raise ValueError("independent ready identities disagree with the pause partition")
        gate = state.get("subagent_materialization")
        if gate_enabled and not isinstance(gate, dict):
            raise ValueError("enabled materialization gate lacks a status observation")
        held = (gate or {}).get("held_request_ids", [])
        if type(held) is not list or len(set(held)) != len(held):
            raise ValueError("held first-call identities are missing or duplicated")
        held = set(held)
        if not gate_enabled and held:
            raise ValueError("disabled materialization gate reports held requests")
        for rid in held:
            source = expected.get(rid)
            if source is None:
                raise ValueError("held request is absent from the bound source")
            identity = source["llm_submit"][0]
            if identity[3] != first[tuple(identity[:3])] or rid not in pending.values():
                raise ValueError("held request is not a live source first call")
        first_count, continuations, workflows = 0, 0, set()
        for owner, rid in ready.items():
            source = expected.get(rid)
            if source is None:
                raise ValueError("ready request is absent from the bound source")
            identity = source["llm_submit"][0]
            if identity[1] != owner:
                raise ValueError("ready request crosses invocation ownership")
            initial = identity[3] == first[tuple(identity[:3])]
            if initial:
                first_count += 1
            else:
                if owner not in completed or rid in held:
                    raise ValueError("continuation has no prior completion or was incorrectly held")
                continuations += 1
                workflows.add(identity[0])
        held_ready = len(held & set(ready.values()))
        row.update(
            ready_paused_excluding_held=len(ready) - held_ready,
            continuation_ready_paused=continuations,
            continuation_ready_workflows=len(workflows),
            first_call_ready_paused=first_count,
            held_ready_paused=held_ready, held_initial_requests=len(held),
        )
    return rows, origin


def integrate_supply(rows, window, *, maximum_gap_s=1.5):
    begin, end = window
    if not 0 <= begin < end or maximum_gap_s <= 0:
        raise ValueError("require a positive observation window and gap bound")
    covered, area = 0., Counter()
    for left, right in zip(rows, rows[1:]):
        if right["offset_s"] <= left["offset_s"]:
            raise ValueError("supply clock is not strictly increasing")
        lo, hi = max(begin, left["offset_s"]), min(end, right["offset_s"])
        if hi <= lo or right["offset_s"] - left["offset_s"] > maximum_gap_s:
            continue
        duration = hi - lo
        covered += duration
        for name in SUPPLY_FIELDS:
            area[name] += duration * left[name]
        area["held_without_active_ready_s"] += duration * (
            left["held_initial_requests"] > 0 and left["active_ready"] == 0
        )
    return {
        "window_offset_s": list(window), "covered_s": covered,
        "coverage_fraction": covered / (end - begin),
        "time_weighted_mean": {
            name: area[name] / covered if covered else None for name in SUPPLY_FIELDS
        },
        "held_without_active_ready_observation_s": area["held_without_active_ready_s"],
        "held_without_active_ready_is_not_GPU_idle_or_causal_harm": True,
    }


class NativeWindows:
    """Charge each native forward once; merge overlapping worker intervals."""

    def __init__(self, windows, origin_ms, buckets, *, disable_padding):
        self.windows, self.origin, self.buckets = windows, origin_ms, buckets
        self.disable_padding = disable_padding
        self.decode = {name: Counter() for name in windows}
        self.unions = {name: [None, 0.] for name in windows}
        self.forward_ids, self.domains = set(), set()
        self.previous = -math.inf
        self.offsets = []

    def observe(self, row):
        if row.get("event") != "gpu_service_sample":
            return
        start, end = row["service_start_monotonic_ms"], row["complete_monotonic_ms"]
        if (
            any(type(value) not in (int, float) or not math.isfinite(value)
                for value in (start, end))
            or end < start or start < self.previous
            or row["forward_iter"] in self.forward_ids
            or row["phase"] not in ("prefill", "decode")
            or not math.isclose(end - start, row["service_elapsed_ms"], abs_tol=1e-5)
        ):
            raise ValueError("native forward identity, geometry or clock is inconsistent")
        self.forward_ids.add(row["forward_iter"])
        self.previous = start
        self.domains.add(row["monotonic_clock_domain"])
        self.offsets.append(row["complete_ts_ms"] - end)
        start, end = (start - self.origin) / 1000., (end - self.origin) / 1000.
        for name, (begin, finish) in self.windows.items():
            lo, hi = max(begin, start), min(finish, end)
            if hi > lo:
                previous_end, covered = self.unions[name]
                covered += max(0., hi - max(lo, previous_end if previous_end is not None else lo))
                self.unions[name] = [max(hi, previous_end or hi), covered]
            # Decode geometry uses whole forwards whose start lies in the window.
            if begin <= start < finish and row["phase"] == "decode":
                accumulate(self.decode[name], row, self.buckets,
                           disable_padding=self.disable_padding)

    def report(self, expected_forwards):
        if len(self.forward_ids) != expected_forwards or len(self.domains) != 1 or (
            not self.offsets or max(self.offsets) - min(self.offsets) > 5.
        ):
            raise ValueError("incomplete native forwards or incompatible clocks")
        return {
            name: {
                "window_offset_s": list(window), "decode": summarize(self.decode[name]),
                "native_worker_interval_union_s": self.unions[name][1],
                "no_native_worker_interval_s": max(0., window[1] - window[0] - self.unions[name][1]),
            } for name, window in self.windows.items()
        }


def completed_migrations(records, *, expected, bytes_per_token, window_ms, offsets):
    begin, end = window_ms
    offset_low, offset_high = offsets
    result = {
        scope: {direction: Counter(records=0, tokens=0, bytes=0) for direction in ("h2d", "d2h")}
        for scope in ("observed", "common", "ambiguous_boundary")
    }
    seen, statuses = set(), Counter()
    for row in records:
        if row["event"] != "transfer_telemetry":
            raise ValueError("unexpected event in the native migration journal")
        statuses[row["status"]] += 1
        if row["status"] != "completed":
            continue
        command, direction = row["command_id"], row["direction"]
        tokens, size = row["num_tokens_by_pool"].get("kv", 0), row["actual_bytes"]
        complete = row["complete_ts_ms"]
        if (
            not isinstance(command, str) or not command or command in seen
            or direction not in result["observed"] or type(tokens) is not int or tokens < 0
            or type(size) is not int or size != tokens * bytes_per_token
            or type(complete) not in (int, float) or not math.isfinite(complete)
        ):
            raise ValueError("invalid native migration identity, geometry or ACK time")
        seen.add(command)
        amounts = {"records": 1, "tokens": tokens, "bytes": size}
        result["observed"][direction].update(amounts)
        low, high = complete - offset_high, complete - offset_low
        if low >= begin and high < end:
            result["common"][direction].update(amounts)
        elif high >= begin and low < end:
            result["ambiguous_boundary"][direction].update(amounts)
    if statuses.total() != expected:
        raise ValueError("native migration record count differs from the drained writer")
    return {**result, "status_counts": dict(statuses), "clock_offset_spread_ms": offset_high - offset_low}


def host_loss_summary(report, prefill, expected, measured):
    totals = {name: Counter() for name in prefill}
    for case in report["cases"]:
        rid = case["request_id"]
        source = expected.get(rid)
        if source is not None and source["llm_submit"][0] != [case[name] for name in OWNER_FIELDS]:
            raise ValueError("Host-loss request differs from its original source identity")
        if case["workflow_id"] in measured and source is None:
            raise ValueError("measured Host-loss request is absent from the original source")
        scopes = ["observed", "common"]
        if case["workflow_id"] in measured:
            scopes.append("measured")
        for scope in scopes:
            result = totals[scope]
            if not case["identity_and_geometry_supported"]:
                result["unresolved_attributed_tokens"] += case["distinct_attributed_tokens"]
                continue
            suffix = "_in_window" if scope == "common" else ""
            result["distinct_tokens"] += case["distinct_observed_prefill_tokens" + suffix]
            result["executed_tokens"] += case["sampled_lost_prefix_tokens" + suffix]
    result = {}
    for scope, total in totals.items():
        billed = prefill[scope]
        if not 0 <= total["distinct_tokens"] <= total["executed_tokens"] <= billed:
            raise ValueError("executed Host-loss tokens exceed actual prefill")
        result[scope] = {
            "prefill_tokens": billed, "distinct_Host_loss_tokens": total["distinct_tokens"],
            "executed_Host_loss_tokens": total["executed_tokens"],
            "Host_loss_prefill_fraction": total["executed_tokens"] / billed if billed else None,
            "unresolved_attributed_tokens_before_window_assignment": total["unresolved_attributed_tokens"],
        }
    return result


def analyze(registration_path, cohort_path, report_path, output, *, gate_enabled, kv_pressure=False):
    if output.exists():
        raise FileExistsError("preserve previous runtime readouts")
    registration = causal.load(registration_path)
    proof = verify_execution(registration_path, cohort_path)
    arm = replay_arm(registration)
    writer_source = "beliefkv/runtime/v0520_native_telemetry.py"
    writer = Path(registration["execution_checkout"]) / writer_source
    if (
        registration["source_server_parameters"].get("enable_mixed_chunk") is not False
        or registration["source_sha256"].get(writer_source) != sha256_file(writer)
    ):
        raise ValueError("require mixed-off service and the exact executed observation semantics")
    capacity_path = arm / "server/native_capacity_census.json"
    capacity = causal.load(capacity_path)["capacity"]
    if capacity["device_mamba_slots"] or capacity["host_mamba_slots"]:
        raise ValueError("runtime readout currently supports FULL-only service")
    status_path = arm / "server/native_telemetry_status.json"
    status = causal.load(status_path)
    if status.get("writer_error") or any(status.get(name) != 0 for name in (
        "writer_queue_depth", "pending_batch_count", "pending_request_count",
        "failed_records", "dropped_records",
    )):
        raise ValueError("native observations did not drain cleanly")
    report, cohort = causal.load(report_path), causal.load(cohort_path)
    performance = verified_performance(report, cohort, causal.load(Path(proof["certificate"])))
    expected_path = Path(registration["source_arm"]) / "expected.json"
    expected = causal.load(expected_path)
    reader = SealedRecords()
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    states = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "admission_runtime_state"
    ]
    rows, origin = supply_samples(
        events, states, registration["workflow_arrivals"], expected,
        gate_enabled=gate_enabled,
    )
    ends = {event["workflow_id"]: event["ts_ms"] for event in events
            if event["kind"] == "workflow_end"}
    common = cohort["throughput_window_offset_seconds"]
    complete = (max(ends[wid] for wid in cohort["measured_workflow_ids"]) - origin) / 1000.
    windows = {"common": common, "measured_lifetimes": (common[0], complete)}
    if rows[-1]["offset_s"] < complete:
        raise ValueError("observations do not cover complete measured tails")
    info_path = arm / "server/server_info.json"
    info = causal.load(info_path)
    if info["enable_mixed_chunk"] or info["tp_size"] != 1 or info["dp_size"] != 1 or (
        info["speculative_algorithm"] is not None
    ):
        raise ValueError("require single-device ordinary decode")
    native = NativeWindows(
        windows, origin, info["cuda_graph_config"]["decode"]["bs"],
        disable_padding=info["disable_cuda_graph_padding"],
    )

    def batches():
        for row in reader(arm / "server/runtime_audit.jsonl"):
            native.observe(row)
            yield row

    measured = set(cohort["measured_workflow_ids"])
    attribution = arm / "server/eviction_attribution.jsonl"
    pressure = None
    if kv_pressure:
        window_ms = tuple(origin + offset * 1000. for offset in common)
        probes, prefills = [], []
        prefill = dict.fromkeys(("common", "measured", "observed"), 0)

        def attributions():
            for row in reader(attribution):
                if row["event"] == "context_prefix_reuse_probe":
                    probes.append(row)
                yield row

        def prefill_batches():
            for row in batches():
                if row.get("event") != "gpu_service_sample" or row["phase"] != "prefill":
                    continue
                prefills.append(row)
                in_window = window_ms[0] <= row["service_start_monotonic_ms"] < window_ms[1]
                for member in row["request_samples"]:
                    tokens = member["token_delta"]
                    if (
                        member["token_delta_semantics"] != "prefill_extend_input_len"
                        or type(tokens) is not int or tokens < 0
                    ):
                        raise ValueError("invalid native prefill input accounting")
                    prefill["observed"] += tokens
                    prefill["common"] += tokens if in_window else 0
                    prefill["measured"] += tokens if member.get("workflow_id") in measured else 0
                yield row

        host_loss = inspect_host_loss(attributions(), prefill_batches(), window_ms=window_ms)
        attributed = host_loss["counts"].get("attributed_tokens_including_overlaps", 0)
        if attributed != status["host_block_eviction_attribution"]["recomputed_full_units"]:
            raise ValueError("complete Host-loss records differ from the drained writer")
        prefix = inspect_prefix(probes, prefills)
        del prefills, probes
        bytes_per_token, remainder = divmod(capacity["host_full_bytes"], capacity["host_full_tokens"])
        if not bytes_per_token or remainder or not native.offsets:
            raise ValueError("missing FULL pool byte geometry or native clocks")
        pressure = {
            "host_loss": host_loss_summary(host_loss, prefill, expected, measured),
            "native_migrations": completed_migrations(
                reader(arm / "server/transfer_telemetry.jsonl"),
                expected=status["record_counts"].get("transfer", 0),
                bytes_per_token=bytes_per_token, window_ms=window_ms,
                offsets=(min(native.offsets), max(native.offsets)),
            ),
            "native_attributed_Host_loss_tokens_including_overlaps": attributed,
            "last_runtime_snapshot_final": states[-1].get("final", False),
            "materialization_counters": {
                name: value for name, value in states[-1]["counts"].items()
                if name.startswith("subagent_materialization_")
            },
        }
    else:
        prefix = inspect_prefix(reader(attribution), batches())
    for case in prefix["cases"]:
        case["measured_workflow"] = case["workflow_id"] in measured
    prefix["supported_distinct_old_prefix_tokens_measured"] = sum(
        case["distinct_previous_prefix_tokens_in_valid_ranges"]
        for case in prefix["cases"]
        if case["measured_workflow"] and case["identity_and_geometry_supported"]
    )
    timers = [
        {"monotonic_ms": state["monotonic_ms"], "counters_seconds": timing_counters(state)}
        for state in states
    ]
    for path, binding in reader.prefixes.items():
        if Path(path).stat().st_size != binding["complete_prefix_bytes"]:
            raise ValueError("completed journal has a growing or incomplete tail")
    pins = {
        str(path.resolve()): sha256_file(path) for path in (
            registration_path, cohort_path, report_path, expected_path,
            capacity_path, status_path, info_path, writer, Path(__file__),
        )
    }
    pins.update({
        str((ROOT / name).resolve()): sha256_file(ROOT / name) for name in (
            "scripts/plot_paused_agent_timeline.py",
            "scripts/analyze_materialization_ready_supply.py",
            "scripts/audit_decode_batch_efficiency.py",
            "scripts/audit_previous_prefix_prefill.py",
            "scripts/audit_synchronous_control_costs.py",
            "scripts/verify_measured_execution.py",
        )
    })
    if pressure is not None:
        helper = ROOT / "scripts/audit_host_block_prefill.py"
        pins[str(helper)] = sha256_file(helper)
    result = {
        "registration": str(registration_path.resolve()), "inputs_sha256": pins,
        "input_jsonl_prefixes": reader.prefixes,
        "complete_measured_work_fresh_drain_actual_exit_verified": True,
        "measured_requests": proof["measured_requests"],
        "measured_output_tokens": proof["measured_output_tokens"],
        "measured_work_sha256": hashlib.sha256(
            json.dumps(proof["work"], sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest(),
        "actual_capacity": capacity,
        "materialization_gate_enabled": gate_enabled,
        "supply": {name: integrate_supply(rows, window) for name, window in windows.items()},
        "native": native.report(
            status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
        ),
        "prefix_work": prefix,
        "control_timing": {
            name: bracket_counters(timers, [origin + boundary * 1000. for boundary in window])
            for name, window in windows.items()
        },
        "wall_clock_performance": performance,
        "limits": [
            "All fixed measured members retain complete JCT; background may be censored.",
            "Logical supply does not certify physical fit or native service.",
            "Supply gaps over 1.5 seconds remain unobserved, never zero-filled.",
            "Native worker intervals are not CUDA utilization or avoidable JCT.",
            "Decode geometry includes complete forwards beginning inside each window.",
            "Supported prefix intervals are observed input re-execution, not all recomputation.",
            "Incomplete prefix identities remain unknown; Host loss cause is not established.",
            "Inclusive CPU counters overlap and must not be added as independent costs.",
        ],
        "GPU_action": False, "performance_accepted": False, "goal_complete": False,
    }
    if pressure is not None:
        result["kv_pressure"] = pressure
        result["limits"].extend((
            "Common prefill work is assigned by native forward start; migration uses native completion ACK time.",
            "Observed totals include independently censored background workflows.",
            "Materialization counters are direct runtime actions; avoided writes do not measure avoided re-execution.",
        ))
    output.mkdir(parents=True)
    samples_path = output / "supply_samples.jsonl.gz"
    with gzip.open(samples_path, "wt", encoding="ascii") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    result["supply_samples"] = {
        "path": str(samples_path.resolve()), "sha256": sha256_file(samples_path),
        "records": len(rows), "maximum_interpolation_gap_s": 1.5,
    }
    write(output / "runtime.json", result)
    return result


def compare_pressure_readouts(control, pressure):
    both = (control, pressure)
    if any(row.get("complete_measured_work_fresh_drain_actual_exit_verified") is not True
           for row in both):
        raise ValueError("D3 runtime comparison requires both complete verified executions")
    if (control.get("materialization_gate_enabled"), pressure.get("materialization_gate_enabled")) != (False, True):
        raise ValueError("D3 runtime comparison requires control and pressure arms")
    if not control["measured_work_sha256"] or control["measured_work_sha256"] != pressure["measured_work_sha256"]:
        raise ValueError("D3 runtime comparison changed measured work")
    if control["actual_capacity"] != pressure["actual_capacity"]:
        raise ValueError("D3 runtime comparison changed actual native capacity")
    windows = [row["native"]["common"]["window_offset_s"] for row in both]
    if windows[0] != windows[1]:
        raise ValueError("D3 runtime comparison changed the common window")
    reference, candidate = (row["wall_clock_performance"] for row in both)
    loss_before, loss_after = (row["kv_pressure"]["host_loss"] for row in both)
    return {
        "common_window_offset_s": windows[0],
        "actual_native_capacity": control["actual_capacity"],
        "same_complete_measured_work_verified": True,
        "measured_mean_JCT_reduction_fraction": (
            1. - candidate["mean_complete_measured_JCT_s"] / reference["mean_complete_measured_JCT_s"]
        ),
        "common_output_throughput_change_fraction": (
            candidate["common_window_output_tokens_per_second"]
            / reference["common_window_output_tokens_per_second"] - 1.
        ),
        "common_decode_throughput_change_fraction": (
            candidate["common_window_decode_tokens_per_second"]
            / reference["common_window_decode_tokens_per_second"] - 1.
        ),
        "executed_Host_loss_token_changes": {
            scope: loss_after[scope]["executed_Host_loss_tokens"] - loss_before[scope]["executed_Host_loss_tokens"]
            for scope in ("common", "measured")
        },
        "arms": {
            name: {
                "registration": row["registration"],
                "wall_clock_performance": row["wall_clock_performance"],
                "kv_pressure": row["kv_pressure"],
                "same_context_old_prefix_tokens_measured": (
                    row["prefix_work"]["supported_distinct_old_prefix_tokens_measured"]
                ),
                "supply": row["supply"],
                "native": row["native"],
                "control_timing": row["control_timing"],
            }
            for name, row in zip(("control", "pressure"), both, strict=True)
        },
        "observed_totals_include_independently_censored_background": True,
        "performance_accepted": False,
        "GPU_action": False,
    }


def watch_pair(plan_path, output):
    """Run CPU readouts after each original stage exits; never schedule a GPU."""
    if output.exists():
        raise FileExistsError("preserve previous CPU reviewers")
    plan, plan_digest = causal.load(plan_path), sha256_file(plan_path)
    output.mkdir(parents=True)
    state = {
        "status": "waiting_completed_control", "process": causal.reservation.process(os.getpid()),
        "plan": str(plan_path.resolve()), "plan_sha256": plan_digest,
        "completed_readouts": [], "GPU_action": False, "performance_accepted": False,
    }
    reports = {}
    try:
        for label in ("control", "pressure"):
            stage = plan["stages"][label]
            proof_path = plan_path.parent / (label + "_execution_proof.json")
            while not proof_path.exists():
                campaign = causal.load(plan_path.parent / "state.json")
                if campaign.get("status", "").startswith(("failed", "interrupted", "stopped")):
                    raise RuntimeError("original GPU campaign stopped: " + campaign["status"])
                state.update(status="waiting_completed_" + label,
                             checked_at=datetime.now(timezone.utc).isoformat())
                atomic_write(output / "state.json", state)
                time.sleep(2.)
            if sha256_file(plan_path) != plan_digest:
                raise ValueError("the watched GPU plan changed")
            if causal.load(proof_path).get("actual_owned_exit_verified") is not True:
                raise ValueError("stage proof lacks actual owned exit")
            state["status"] = "analyzing_completed_" + label
            atomic_write(output / "state.json", state)
            reports[label] = analyze(
                Path(stage["registration"]), Path(plan["cohort"]),
                Path(stage["review"]) / "cohort.json", output / label,
                gate_enabled=label == "pressure",
            )
            state["completed_readouts"].append(label)
        before, after = (reports[label] for label in ("control", "pressure"))
        if before["measured_work_sha256"] != after["measured_work_sha256"] or (
            before["actual_capacity"] != after["actual_capacity"]
        ):
            raise ValueError("runtime pair changed exact measured native work or actual resources")
        reference, candidate = (row["wall_clock_performance"] for row in (before, after))
        metrics = {
            name: candidate[name] / reference[name] - 1. for name in (
                "common_window_output_tokens_per_second", "common_window_decode_tokens_per_second",
            )
        }
        write(output / "pair.json", {
            "mean_complete_JCT_reduction": 1. - candidate["mean_complete_measured_JCT_s"]
            / reference["mean_complete_measured_JCT_s"],
            "wall_clock_throughput_changes": metrics,
            "measured_supported_old_prefix_extend_tokens": {
                label: reports[label]["prefix_work"]["supported_distinct_old_prefix_tokens_measured"]
                for label in reports
            },
            "inputs_sha256": {
                str(output / label / "runtime.json"): sha256_file(output / label / "runtime.json")
                for label in reports
            },
            "all_fixed_measured_members_complete": True,
            "background_totals_not_a_matched_recomputation_denominator": True,
            "remaining_acceptance": (
                "Review native Host block reaccess, unknown prefix cases, ready supply, "
                "control CPU and exact source/traffic/resource pair proofs together."
            ),
            "GPU_action": False, "performance_accepted": False, "goal_complete": False,
        })
        state["status"] = "complete_CPU_readout"
    except BaseException as exc:
        state.update(status="failed_CPU_readout", error=repr(exc))
        raise
    finally:
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write(output / "state.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "report", "watch-plan"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gate-enabled", action="store_true")
    parser.add_argument("--kv-pressure", action="store_true")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("hide CUDA during post-run analysis")
    if args.watch_plan:
        if any((args.registration, args.cohort, args.report, args.gate_enabled, args.kv_pressure)):
            parser.error("--watch-plan chooses its original registered arms")
        return watch_pair(args.watch_plan.resolve(), args.output.resolve())
    if any(value is None for value in (args.registration, args.cohort, args.report)):
        parser.error("single-arm analysis requires --registration, --cohort and --report")
    started = time.monotonic()
    result = analyze(
        args.registration, args.cohort, args.report, args.output,
        gate_enabled=args.gate_enabled, kv_pressure=args.kv_pressure,
    )
    print(json.dumps({
        "output": str(args.output), "measured_requests": result["measured_requests"],
        "prefix_counts": result["prefix_work"]["counts"],
        "supply": result["supply"], "analysis_seconds": time.monotonic() - started,
        "observed_at": datetime.now(timezone.utc).isoformat(), "GPU_action": False,
    }))


if __name__ == "__main__":
    main()
