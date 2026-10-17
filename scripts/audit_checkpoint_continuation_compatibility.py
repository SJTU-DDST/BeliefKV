#!/usr/bin/env python3
"""Audit observed continuation compatibility and historical checkpoint write roles."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_checkpoint_write_read_balance import copy_balance
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords


def integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid {label}")
    return value


def checkpoint(state, input_tokens):
    node = integer(state.get("node_id"), "checkpoint node")
    prefix = integer(state.get("prefix_tokens"), "checkpoint prefix")
    created = state.get("creation_time")
    role = (
        "input_prefix" if prefix < input_tokens else
        "input_boundary" if prefix == input_tokens else "generated_output_tail"
    )
    if not prefix or not finite_number(created) or created < 0 or state.get("role") != role:
        raise ValueError("invalid checkpoint identity or role")
    return (node, created), prefix, role


def continuation_profile(records, begin, end, *, observation_sink=None):
    counts, observations = Counter(), Counter()
    scopes, nodes, seen, samples = set(), {}, set(), defaultdict(list)
    input_losses, generated_lengths, generated_common = [], [], []
    selections = defaultdict(Counter)
    for row in records:
        if row.get("event") != "generation_continuation_prefix_probe":
            continue
        timestamp = row.get("ts_ms")
        if not finite_number(timestamp):
            raise ValueError("invalid continuation timestamp")
        if not begin <= timestamp < end:
            continue
        identities = tuple(row.get(key) for key in (
            "workflow_id", "context_id", "request_id", "prior_request_id",
        ))
        if any(type(value) is not str or not value for value in identities):
            raise ValueError("invalid continuation identity")
        if identities in seen:
            raise ValueError("duplicate continuation observation")
        seen.add(identities)
        scopes.add(identities[:2])
        prior, output, current, common_input, common_output, common = [
            integer(row.get(key), key) for key in (
                "prior_input_tokens", "prior_generated_output_tokens",
                "current_input_tokens", "common_prior_input_prefix_tokens",
                "common_generated_output_prefix_tokens", "common_prior_transcript_prefix_tokens",
            )
        ]
        if (
            common_input > min(prior, current)
            or common_output > min(output, max(0, current - prior))
            or (common_input < prior and common_output)
            or common != common_input + common_output
        ):
            raise ValueError("inconsistent continuation prefix accounting")
        counts["continuations"] += 1
        counts["prior_input_fully_preserved"] += common_input == prior
        counts["prior_input_last_token_diverges"] += common_input == prior - 1
        counts["positive_generated_prefix_preserved"] += common_output > 0
        counts["prior_generated_tokens"] += output
        counts["preserved_generated_prefix_tokens"] += common_output
        input_losses.append(prior - common_input)
        generated_lengths.append(output)
        generated_common.append(common_output)
        observed = row.get("checkpoint_path_observed")
        if type(observed) is not bool:
            raise ValueError("unknown path availability must be explicit")
        states = row.get("checkpoint_compatibility")
        if not isinstance(states, list) or (not observed and states):
            raise ValueError("invalid continuation checkpoint coverage")
        counts["checkpoint_path_observed"] += observed
        counts["checkpoint_path_unavailable"] += not observed
        counts["observed_paths_without_checkpoints"] += observed and not states
        request_states, candidates = set(), []
        for state in states:
            identity, prefix, role = checkpoint(state, prior)
            if identity in request_states:
                raise ValueError("duplicate checkpoint in one continuation")
            request_states.add(identity)
            expected = (
                "outside_observed_transcript" if prefix > prior + output else
                "exact_prefix_compatible" if prefix <= common else
                "new_input_ends_before_checkpoint" if prefix > current else
                "token_prefix_diverges"
            )
            if state.get("prefix_compatibility") != expected:
                raise ValueError("checkpoint compatibility disagrees with observed lengths")
            observations[(role, expected)] += 1
            candidates.append((prefix, expected == "exact_prefix_compatible"))
            if observation_sink is not None:
                observation_sink.append((
                    timestamp, identity, prefix, expected, identities[:2],
                ))
            node = nodes.setdefault(identity, {
                "prefixes": set(), "roles": set(), "compatibility": set(),
            })
            node["prefixes"].add(prefix)
            node["roles"].add(role)
            node["compatibility"].add(expected)
            key = f"{role}/{expected}"
            if len(samples[key]) < 4:
                samples[key].append({
                    "request_id": identities[2], "prior_request_id": identities[3],
                    **state,
                })
        if observed:
            ordered = sorted(candidates)
            compatible = [prefix for prefix, matches in ordered if matches]
            before_input = [item for item in ordered if item[0] <= prior]
            strict_input = [item for item in ordered if item[0] < prior]
            for name, selected in (
                ("furthest_one", ordered[-1:]),
                ("furthest_two", ordered[-2:]),
                ("latest_at_or_before_input_boundary", before_input[-1:]),
                ("latest_strict_input_prefix", strict_input[-1:]),
            ):
                summary = selections[name]
                summary["observed_paths"] += 1
                summary["paths_with_compatible_checkpoint"] += bool(compatible)
                summary["retained_checkpoint_observations"] += len(selected)
                summary["retains_some_compatible_checkpoint"] += any(matches for _, matches in selected)
                summary["retains_furthest_compatible_checkpoint"] += bool(
                    compatible and (max(compatible), True) in selected
                )
    consistent = [node for node in nodes.values() if len(node["prefixes"]) == 1]
    output_nodes = [node for node in consistent if "generated_output_tail" in node["roles"]]
    return {
        "counts": dict(counts), "logical_contexts": len(scopes),
        "prior_input_suffix_not_preserved_tokens": distribution(input_losses),
        "prior_generated_output_tokens": distribution(generated_lengths),
        "preserved_generated_prefix_tokens": distribution(generated_common),
        "checkpoint_observations_by_role_and_compatibility": {
            f"{role}/{compatibility}": amount
            for (role, compatibility), amount in sorted(observations.items())
        },
        "distinct_logical_checkpoints": len(nodes),
        "logical_checkpoint_prefix_conflicts": len(nodes) - len(consistent),
        "distinct_checkpoints_ever_observed_as_output_tail": len(output_nodes),
        "output_tail_checkpoints_also_observed_compatible": sum(
            "exact_prefix_compatible" in node["compatibility"] for node in output_nodes
        ),
        "illustrative_per_path_selections": dict(selections),
        "samples": dict(samples),
    }


def successor_inputs_before_write(observations, transfers, begin, end, mamba_unit_bytes):
    observations = sorted(observations, key=lambda item: item[0])
    observed_by_status, signatures, conflicts = defaultdict(dict), {}, set()
    cursor, counts, roles, leads = 0, Counter(), Counter(), []
    for transfer in sorted(
        (row for row in transfers if row.get("status") == "completed" and row.get("direction") == "d2h"),
        key=lambda item: item["submit_ts_ms"],
    ):
        timestamp = transfer["submit_ts_ms"]
        while cursor < len(observations) and observations[cursor][0] < timestamp:
            item = observations[cursor]
            node, signature = item[1][0], (item[1][1], item[2])
            if node in signatures and signatures[node] != signature:
                conflicts.add(node)
            signatures[node] = signature
            # A later divergence must not erase a previously compatible use.
            observed_by_status[node][item[3]] = item
            cursor += 1
        if not begin <= timestamp < end:
            continue
        units = integer((transfer.get("num_tokens_by_pool") or {}).get("mamba", 0), "Mamba copy units")
        if not units:
            continue
        counts["completed_mamba_write_units"] += units
        nodes = transfer.get("node_ids") or ()
        if units != 1 or len(nodes) != 1 or type(nodes[0]) is not int:
            role = "unassignable_aggregate_batch"
        elif nodes[0] in conflicts:
            role = "observed_node_identity_conflict"
        else:
            evidence = list(observed_by_status[nodes[0]].values())
            known = {item[3] for item in evidence}
            compatible = "exact_prefix_compatible" in known
            incompatible = bool(known & {"token_prefix_diverges", "new_input_ends_before_checkpoint"})
            unknown = "outside_observed_transcript" in known
            role = (
                "no_comparison_input_in_window_before_write" if not evidence else
                "compatible_and_incompatible_successors_observed" if compatible and incompatible else
                "compatible_successor_observed" if compatible else
                "incompatible_and_unknown_successors_observed" if incompatible and unknown else
                "only_incompatible_successors_observed" if incompatible else
                "only_unknown_comparisons_observed"
            )
            if role == "only_incompatible_successors_observed":
                leads.append(timestamp - max(item[0] for item in evidence))
        roles[role] += units
    return {
        "counts": dict(counts),
        "copy_units_by_observed_successor_status": {
            role: {"copy_units": amount, "mamba_bytes": amount * mamba_unit_bytes}
            for role, amount in sorted(roles.items())
        },
        "incompatible_input_observation_lead_ms": distribution(leads),
        "observation_scope": (
            "Only successor-input observations inside the requested window and strictly "
            "before D2H submit. Earlier comparisons are not credited. Input enqueue time "
            "does not establish asynchronous comparison-completion or action availability."
        ),
    }


def historical_write_roles(events, transfers, begin, end, mamba_unit_bytes):
    observations, seen_requests, counts = [], set(), Counter()
    wall, cpu, completed_through = [], [], None
    for row in events:
        if row.get("kind") != "llm_result":
            continue
        attrs = row.get("attributes") or {}
        request, timestamp = attrs.get("request_id"), row.get("ts_ms")
        if type(request) is not str or not request or not finite_number(timestamp):
            raise ValueError("invalid completed request")
        if request in seen_requests:
            raise ValueError("duplicate completed request")
        seen_requests.add(request)
        completed_through = max(timestamp, completed_through) if completed_through is not None else timestamp
        path = attrs.get("checkpoint_path_evidence") or {}
        selected = begin <= timestamp < end
        if selected:
            counts["completed_requests"] += 1
            counts[f"completion_path_{path.get('status', 'missing')}"] += 1
        if path.get("status") != "observed":
            continue
        input_tokens = integer(path.get("request_input_tokens"), "snapshot input tokens")
        if selected:
            wall.append(integer(path.get("probe_wall_ns"), "probe wall time") / 1e6)
            cpu.append(integer(path.get("probe_thread_cpu_ns"), "probe CPU time") / 1e6)
        request_nodes = set()
        for state in path.get("mamba_states", ()):
            identity, prefix, role = checkpoint(state, input_tokens)
            if identity in request_nodes:
                raise ValueError("duplicate checkpoint in completion path")
            request_nodes.add(identity)
            observations.append((timestamp, identity, prefix, role, request))
    if completed_through is None or completed_through < end:
        raise ValueError("completion observations do not cover the requested window")
    observations.sort(key=lambda item: item[0])
    latest, conflicts, seen_commands = {}, set(), set()
    roles, ages, samples = Counter(), defaultdict(list), defaultdict(list)
    cursor = 0
    for transfer in sorted(
        (row for row in transfers if row.get("status") == "completed" and row.get("direction") == "d2h"),
        key=lambda item: item["submit_ts_ms"],
    ):
        timestamp = transfer["submit_ts_ms"]
        while cursor < len(observations) and observations[cursor][0] < timestamp:
            item = observations[cursor]
            node = item[1][0]
            previous = latest.get(node)
            if previous is not None and previous[1:3] != item[1:3]:
                conflicts.add(node)
            latest[node] = item
            cursor += 1
        if not begin <= timestamp < end:
            continue
        units = integer((transfer.get("num_tokens_by_pool") or {}).get("mamba", 0), "Mamba copy units")
        if not units:
            continue
        command = transfer.get("command_id")
        if type(command) is not str or not command or command in seen_commands:
            raise ValueError("missing or duplicate completed Mamba write")
        seen_commands.add(command)
        counts["completed_mamba_write_units"] += units
        nodes = transfer.get("node_ids") or ()
        source = None
        if units != 1 or len(nodes) != 1 or type(nodes[0]) is not int:
            role = "unassignable_aggregate_batch"
        elif nodes[0] in conflicts:
            role = "observed_node_identity_conflict"
        elif nodes[0] not in latest:
            role = "no_prior_completion_path_observation"
        else:
            source = latest[nodes[0]]
            role = f"last_observed_{source[3]}"
            ages[role].append(timestamp - source[0])
        roles[role] += units
        if len(samples[role]) < 4:
            samples[role].append({
                "command_id": command, "submit_ts_ms": timestamp, "node_ids": list(nodes),
                "last_observation": (
                    {"ts_ms": source[0], "creation_time": source[1][1],
                     "prefix_tokens": source[2], "request_id": source[4]}
                    if source is not None else None
                ),
            })
    return {
        "counts": dict(counts),
        "write_units_by_last_observed_role": {
            role: {"copy_units": amount, "mamba_bytes": amount * mamba_unit_bytes}
            for role, amount in sorted(roles.items())
        },
        "prior_observation_age_ms_by_role": {
            role: distribution(values) for role, values in sorted(ages.items())
        },
        "synchronous_completion_path_probe_wall_ms": distribution(wall),
        "synchronous_completion_path_probe_thread_cpu_ms": distribution(cpu),
        "samples": dict(samples),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-report", type=Path, required=True)
    parser.add_argument("--startup-report", type=Path, required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--start-seconds", type=float, required=True)
    parser.add_argument("--end-seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.start_seconds < args.end_seconds:
        parser.error("fresh output and increasing window boundaries are required")
    raw, startup_raw = args.clock_report.read_bytes(), args.startup_report.read_bytes()
    basis, startup = json.loads(raw), json.loads(startup_raw)
    arm = basis["runs"][args.arm]
    windows = [
        row for row in arm["windows"]
        if row["start_seconds"] == args.start_seconds and row["end_seconds"] == args.end_seconds
    ]
    if (
        len(windows) != 1 or windows[0].get("service_window_observed_complete") is not True
        or arm["clock_alignment"].get("native_client_clock_validated") is not True
        or startup.get("status") != "actual_native_service_and_continuation_probe_verified"
    ):
        raise ValueError("complete clock-aligned diagnostic service is required")
    placement_path = Path(startup["placement"]["path"])
    placement_raw = placement_path.read_bytes()
    placement = json.loads(placement_raw)
    run = Path(arm["run"])
    if (
        hashlib.sha256(placement_raw).hexdigest() != startup["placement"]["sha256"]
        or (Path(placement["run_root"]) / placement["arm"]).resolve() != run.resolve()
        or placement["capacity_census"]["capacity"] != startup["capacity"]
    ):
        raise ValueError("diagnostic startup and arm provenance differ")
    anchor = arm["clock_alignment"]["anchor_unix_ms"]
    begin, end = anchor + args.start_seconds * 1000, anchor + args.end_seconds * 1000
    reader = SealedRecords()
    transfers = list(reader(run / "server/transfer_telemetry.jsonl"))
    balance = copy_balance(transfers, begin, end, windows[0]["pool_bytes_per_unit"])
    writes = historical_write_roles(
        reader(run / "server/runtime_events.sglang.jsonl"), transfers, begin, end,
        windows[0]["pool_bytes_per_unit"]["mamba"],
    )
    if writes["counts"].get("completed_mamba_write_units", 0) != balance["totals"]["cohort_d2h"]["copy_units"].get("mamba", 0):
        raise ValueError("role partition differs from physical write units")
    comparisons = []
    compatibility = continuation_profile(
        reader(run / "server/eviction_attribution.jsonl"), begin, end,
        observation_sink=comparisons,
    )
    successor_status = successor_inputs_before_write(
        comparisons, transfers, begin, end, windows[0]["pool_bytes_per_unit"]["mamba"],
    )
    if successor_status["counts"].get("completed_mamba_write_units", 0) != writes["counts"].get("completed_mamba_write_units", 0):
        raise ValueError("successor-input partition differs from physical write units")
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "run": str(run), "start_seconds": args.start_seconds, "end_seconds": args.end_seconds,
        "clock_source": {"path": str(args.clock_report), "sha256": hashlib.sha256(raw).hexdigest()},
        "startup_source": {"path": str(args.startup_report), "sha256": hashlib.sha256(startup_raw).hexdigest()},
        "continuation_compatibility": compatibility,
        "historical_mamba_write_roles": writes, "physical_copy_balance": balance,
        "successor_inputs_before_write": successor_status,
        "input_jsonl_prefixes": reader.prefixes,
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), ROOT / "scripts/audit_checkpoint_write_read_balance.py",
                ROOT / "scripts/audit_native_system_costs.py",
                ROOT / "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        "semantics": [
            "Continuation cohorts contain only observed same-session successor comparisons, not every completed request. Missing history, new sessions and unfinished successors are not misses.",
            "Checkpoint observations can repeat the same logical node. Distinct node/creation identities are not allocated slots, physical copies or deduplicated bytes.",
            "Compatibility is with one actual next input. Divergence does not prove global uselessness, future non-use, safe discard, its formatting cause or avoidable computation.",
            "Illustrative per-path selections use only prior input length and observed checkpoint positions. Successor compatibility is an offline evaluation label. They do not model native fork/lock protection, global capacity, Host-write admission, eviction or changed execution, and are not native path-cap performance.",
            "Write roles use only strictly earlier completion snapshots and identify the last observed role by node ID. Receipts lack creation/physical-generation identity; roles and ages are historical associations, not write-time references or proof of consumption.",
            "Successor-input status uses observations inside this window before each write. A known incompatibility in observed successors does not establish absent sharing, future non-use, current references or safe discard. Its timestamp is input enqueue time, not asynchronous comparison completion.",
            "The completed-read source bound allows all observed H2D units and is not an online policy or an end-to-end oracle bound.",
            "Probe timings cover only the synchronous path walk, not output snapshots, queueing, asynchronous comparisons/serialization or total diagnostic overhead.",
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output), "compatibility_counts": compatibility["counts"],
        "checkpoint_observations": compatibility["checkpoint_observations_by_role_and_compatibility"],
        "write_roles": writes["write_units_by_last_observed_role"],
        "successor_input_status": successor_status["copy_units_by_observed_successor_status"],
    }, indent=2))


if __name__ == "__main__":
    main()
