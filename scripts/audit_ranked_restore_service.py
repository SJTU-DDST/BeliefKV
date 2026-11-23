"""Link changed admission choices to staged native demand restores and service."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_free_dispatch_service import identity
from scripts.audit_host_block_prefill import OWNER_FIELDS, inspect as inspect_host_loss
from scripts.audit_native_system_costs import finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_staged_native_restore import inspect, transfer_key
from scripts.plot_paused_agent_timeline import replay_origin
from scripts.stop_after_measured_cohort import replay_arm
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def selected_episodes(decisions):
    episodes = []
    for decision in decisions:
        periodic = decision["source"] == "periodic_decay_resume"
        services = {
            tuple(row["key"]): row for row in decision[
                "periodic_service" if periodic else "free_service"
            ]
        }
        for key in map(tuple, decision.get("newly_selected_keys", ())):
            service = services.get(key)
            grant = service and service["episode"]
            verified = service and (
                decision.get("following_periodic_grant_verified") if periodic else
                service["actual_grant_verified"] and not service["separate_tool_replacement"]
            )
            row = {
                "key": key, "source": decision["source"], "decision_s": decision["monotonic_s"],
                "grant_ms": None, "first_launch_ms": None, "staged_transfers": [],
                "request_lifetime_host_loss": None,
            }
            if verified and grant and grant["new_service_before_next_control_or_intervention"] is True:
                begin, launch, end = (
                    grant["selection_ms"], grant["first_new_useful_launch_ms"],
                    grant["observation_window_end_ms"],
                )
                if (
                    tuple(grant["key"]) != key
                    or not all(map(finite_number, (begin, launch, end)))
                    or not 0 <= begin <= begin + launch <= end
                ):
                    raise ValueError("invalid exact grant-to-launch window")
                row.update(grant_ms=begin, first_launch_ms=begin + launch)
            episodes.append(row)
    return episodes


def staged_transfers(episodes, journals, transfers, *, bytes_per_token, clock_domain):
    """Attach exact staged batches inside each verified, bounded grant episode."""
    indexed = defaultdict(list)
    for episode in episodes:
        if episode["grant_ms"] is not None:
            indexed[episode["key"]].append(episode)
    claims, targets = [], {}
    for row in journals:
        if row.get("event") != "native_demand_restore_staged":
            continue
        at = row["monotonic_ms"]
        associated = []
        for member in row.get("requests", [row]):
            for episode in indexed.get(identity(member), ()):
                if episode["grant_ms"] <= at <= episode["first_launch_ms"]:
                    associated.append(episode)
        if not associated:
            continue
        if row["monotonic_clock_domain"] != clock_domain:
            raise ValueError("staged restore uses a different native service clock")
        transaction = row["native_submit_ts_ms"], tuple(row["native_node_ids"]), row["native_bytes"]
        if transaction in targets:
            raise ValueError("duplicate staged restore transaction")
        targets[transaction] = associated
        claims.append(row)

    matched = inspect(claims, (
        row for row in transfers if row.get("direction") == "h2d"
        and row.get("telemetry_origin") == "native_hicache_ack_v0520"
        and row.get("submit_ts_ms") is not None and row.get("node_ids")
        and transfer_key(row) in targets
    ), bytes_per_token=bytes_per_token)
    for batch in matched["episodes"]:
        transaction = (
            batch["native_submit_ts_ms"], tuple(batch["native_node_ids"]), batch["native_h2d_bytes"],
        )
        for episode in targets[transaction]:
            episode["staged_transfers"].append({
                "transaction": transaction, "native_batch_size": batch["native_batch_size"],
                "staged_monotonic_ms": batch["staged_monotonic_ms"],
                "request_H2D_tokens": (
                    batch["native_full_tokens"] if batch["native_batch_size"] == 1 else None
                ),
            })
    return matched["counts"]


def attach_host_loss(episodes, cases):
    by_request = {case["request_id"]: case for case in cases}
    for row in episodes:
        if row["first_launch_ms"] is None:
            continue
        key = row["key"]
        case = by_request.get(key[0])
        if case is None:
            continue
        if tuple(case[field] for field in OWNER_FIELDS) != key[1:5]:
            raise ValueError("selected request and native Host loss have different owners")
        supported = (
            case["identity_and_geometry_supported"] is True
            and [tuple(attempt) for attempt in case["observed_attempts"]] == [key[5:]]
        )
        row["request_lifetime_host_loss"] = {
            "exact_attempt_verified": supported,
            "observed_prefill_tokens": case["sampled_prefill_extend_tokens"] if supported else None,
            "executed_host_loss_tokens": case["sampled_lost_prefix_tokens"] if supported else None,
        }


def selected_host_losses(records, requests, *, expected_tokens):
    # One native input can emit multiple attribution rows.
    total = 0
    for row in records:
        if row.get("event") != "host_block_reaccess_attributed" or row.get("pool") != "full":
            continue
        recomputed = row["full_recomputed_units"]
        if type(recomputed) is not int or recomputed < 0:
            raise ValueError("invalid native Host loss token count")
        total += recomputed
        if row["request_id"] in requests:
            yield row
    if total != expected_tokens:
        raise ValueError("native Host loss tokens differ from the drained writer")


def summarize(episodes):
    episodes = list(episodes)
    transactions = {
        transfer["transaction"]
        for episode in episodes for transfer in episode["staged_transfers"]
    }
    host_loss = {
        row["key"]: row["request_lifetime_host_loss"]
        for row in episodes if row["request_lifetime_host_loss"] is not None
    }
    exact_losses = [loss for loss in host_loss.values() if loss["exact_attempt_verified"]]
    return {
        "selected_episodes": len(episodes),
        "episodes_with_verified_bounded_launch": sum(row["first_launch_ms"] is not None for row in episodes),
        "episodes_with_staged_H2D": sum(bool(row["staged_transfers"]) for row in episodes),
        "distinct_staged_native_batches": len(transactions),
        "staged_native_batch_bytes": sum(size for _, _, size in transactions),
        "attempts_with_exact_request_lifetime_host_loss": len(exact_losses),
        "attempts_with_unverified_host_loss": len(host_loss) - len(exact_losses),
        "request_lifetime_executed_host_loss_tokens": sum(
            loss["executed_host_loss_tokens"] for loss in exact_losses
        ),
    }


def read(choice_path, cohort_path):
    report = json.loads(choice_path.read_bytes())
    registration = Path(report["registration"]).resolve()
    pins = report["inputs_sha256"]
    for path in (registration, cohort_path):
        if pins.get(str(path)) != sha256_file(path):
            raise ValueError("choice service audit used a different registration or cohort")
    if report["complete_measured_execution_verified"] is not True:
        raise ValueError("require the completed choice service audit")
    proof = verify_execution(registration, cohort_path)
    plan = json.loads(registration.read_bytes())
    arm = replay_arm(plan)
    journal = arm / "opportunities/admission_opportunities.jsonl"
    audit = arm / "server/runtime_audit.jsonl"
    events = arm / "replay/runtime_events.replay.jsonl"
    native_transfers = arm / "server/transfer_telemetry.jsonl"
    evictions = arm / "server/eviction_attribution.jsonl"
    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_bytes())
    if status.get("writer_error") or any(status.get(name) != 0 for name in (
        "writer_queue_depth", "pending_batch_count", "pending_request_count",
        "failed_records", "dropped_records",
    )):
        raise ValueError("native writer did not drain completely")
    capacity_path = arm / "server/native_capacity_census.json"
    capacity = json.loads(capacity_path.read_bytes())["capacity"]
    bytes_per_token, remainder = divmod(capacity["host_full_bytes"], capacity["host_full_tokens"])
    if (
        capacity["pool_layout"] != "static_full_only" or not bytes_per_token or remainder
        or plan["source_server_parameters"].get("enable_mixed_chunk") is not False
    ):
        raise ValueError("require FULL-only geometry and separately observed prefill")
    seals = report["input_jsonl_prefixes"]
    for path in (journal, audit, events):
        if path.stat().st_size != seals[str(path)]["complete_prefix_bytes"]:
            raise ValueError(f"choice service audit did not cover the complete log: {path}")
    with audit.open("rb") as stream:
        first_service = next(row for row in map(json.loads, stream) if row["event"] == "gpu_service_sample")
    reader = SealedRecords()
    reader.prefixes.update({str(path): seals[str(path)] for path in (journal, events, audit)})
    origin = replay_origin(reader(events), plan["workflow_arrivals"])
    episodes = selected_episodes(report["decisions"])
    transfer_count = 0

    def completed_transfer_log():
        nonlocal transfer_count
        for row in reader(native_transfers):
            transfer_count += 1
            yield row

    matched = staged_transfers(
        episodes, reader(journal), completed_transfer_log(), bytes_per_token=bytes_per_token,
        clock_domain=first_service["monotonic_clock_domain"],
    )
    if transfer_count != status["record_counts"].get("transfer", 0):
        raise ValueError("native transfer log differs from the drained writer")
    target_requests = {row["key"][0] for row in episodes if row["first_launch_ms"] is not None}
    host_loss = inspect_host_loss(selected_host_losses(
        reader(evictions), target_requests,
        expected_tokens=status["host_block_eviction_attribution"]["recomputed_full_units"],
    ), reader(audit))
    attach_host_loss(episodes, host_loss["cases"])
    if any(
        path.stat().st_size != reader.prefixes[str(path)]["complete_prefix_bytes"]
        for path in (journal, events, audit, native_transfers, evictions)
    ):
        raise ValueError("readout requires complete sealed logs")
    cohort = proof["cohort"]
    fixed18 = set(cohort["measured_workflow_ids"])
    fixed38 = fixed18 | set(cohort["warmup_workflow_ids"])
    start, end = cohort["throughput_window_offset_seconds"]
    common = [row for row in episodes if start <= (row["decision_s"] * 1000. - origin) / 1000. < end]
    return {
        "registration": str(registration), "policy_kind": report["policy_kind"],
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (
                choice_path, cohort_path, registration, capacity_path, status_path, Path(proof["certificate"]),
                Path(__file__), ROOT / "scripts/audit_staged_native_restore.py",
                ROOT / "scripts/audit_host_block_prefill.py",
            )
        },
        "input_jsonl_prefixes": reader.prefixes,
        "counts": matched,
        "scopes": {
            window: {
                "all": summarize(rows),
                "measured": summarize(row for row in rows if row["key"][1] in fixed18),
                "measured_plus_warmup": summarize(row for row in rows if row["key"][1] in fixed38),
            }
            for window, rows in (("whole_observation", episodes), ("common_throughput_window", common))
        },
        "episodes": episodes,
        "scope": {
            "staged_batches_between_actual_grant_and_first_bounded_launch": True,
            "shared_transfers_counted_once_per_scope_and_reported_at_batch_granularity": True,
            "host_loss_counts_observed_reexecution_over_the_exact_request_attempt_lifetime": True,
            "covers_unstaged_H2D": False,
            "counterfactual_KV_savings_or_JCT_proven": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("choice-service", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier ranked restore readouts")
    result = read(args.choice_service.resolve(), args.cohort.resolve())
    write(args.output, result)
    print(json.dumps(result["scopes"]))


if __name__ == "__main__":
    main()
