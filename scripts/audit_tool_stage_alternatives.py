"""Audit published tool-replacement alternatives through their next blocked stage."""

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_free_dispatch_service import identity
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_tool_reentry_service import tool_reentries
from scripts.train_workflow_work import write


def next_stage(stream, request_id, work, rounds, join_ready, coefficient, long_tool_ms):
    if not math.isfinite(coefficient) or coefficient <= 0 or long_tool_ms <= 0:
        raise ValueError("positive model calibration and long-tool threshold are required")
    starts = [
        index for index, row in enumerate(stream)
        if row["kind"] == "llm_submit" and row["attributes"].get("request_id") == request_id
    ]
    if len(starts) != 1:
        return {"supported": False, "reason": "current_request_not_unique"}
    requests, short_tools, tool_ids, external = [], [], set(), 0.
    boundary = None
    for row in stream[starts[0]:]:
        kind, attrs = row["kind"], row.get("attributes") or {}
        if kind == "llm_submit":
            rid = attrs["request_id"]
            if rid not in work:
                return {"supported": False, "reason": "source_request_unknown"}
            requests.append(rid)
        elif kind == "call":
            return {"supported": False, "reason": "foreground_child_path_not_accounted"}
        elif kind == "tool_start":
            if not requests:
                raise ValueError("tool stage has no preceding model request")
            origin = requests[-1]
            episode = rounds.get(origin)
            if episode is None or "client_ready_ts_ms" not in episode:
                return {"supported": False, "reason": "tool_round_not_resolved"}
            if origin in tool_ids:
                continue
            tool_ids.add(origin)
            duration = episode["client_ready_ts_ms"] - episode["tool_wait_start_ts_ms"]
            if duration >= long_tool_ms:
                boundary = "long_tool", episode["tool_wait_start_ts_ms"]
                break
            external += duration
            short_tools.append(origin)
        elif kind == "join_wait":
            when = join_ready.get((row["workflow_id"], row.get("join_id")))
            if when is None:
                return {"supported": False, "reason": "join_completion_unobserved"}
            if when - row["ts_ms"] > 1.:
                boundary = "blocked_join", row["ts_ms"]
                break
        elif kind == "return":
            boundary = "return", row["ts_ms"]
            break
        elif kind == "invocation_cancel":
            return {"supported": False, "reason": "cancelled_not_normal_return"}
    if boundary is None:
        return {"supported": False, "reason": "stage_boundary_censored"}
    model_units = sum(work[rid]["input_increment"] / 100. + work[rid]["output_tokens"] for rid in requests)
    return {
        "supported": True, "boundary_kind": boundary[0], "observed_boundary_ms": boundary[1],
        "requests": requests, "request_count": len(requests), "model_work_units": model_units,
        "model_time_proxy_ms": model_units * coefficient * 1000.,
        "observed_short_tool_wait_ms": external,
        "phase_cost_proxy_ms": model_units * coefficient * 1000. + external,
        "short_tool_rounds": short_tools,
        "boundary_is_actual_physical_release": False,
        "restore_and_backup_cost_accounted": False,
    }


def matching_choice(choices, replacement):
    key = identity(replacement["consumer"], grant=True)
    eligible = [
        row for row in choices.get(key, ())
        if 0 <= replacement["monotonic_s"] - row["grant_monotonic_s"] <= .05
    ]
    if len(eligible) > 1:
        raise ValueError("a tool replacement has ambiguous published grants")
    return eligible[0] if eligible else None


def inspect(registration, cohort_path, prior_path, *, long_tool_ms):
    plan = json.loads(registration.read_bytes())
    cohort = json.loads(cohort_path.read_bytes())
    previous = json.loads(prior_path.read_bytes())
    for filename, digest in previous["inputs_sha256"].items():
        if sha256_file(Path(filename)) != digest:
            raise ValueError("prior completed window evidence changed")
    artifact_path = Path(plan["reference_environment"]["WORKFLOW_WORK_MODEL"])
    artifact = json.loads(artifact_path.read_bytes())
    if sha256_file(artifact_path) != plan["reference_environment"]["WORKFLOW_WORK_MODEL_SHA256"]:
        raise ValueError("frozen work inventory differs from the control declaration")
    if artifact["remaining_work_ranking_enabled"] is not False:
        raise ValueError("use the retained non-ranking control only")
    work = artifact["requests"]
    coefficient = artifact["model_seconds_per_work_token"]
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    measured = set(cohort["measured_workflow_ids"])
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    episodes, _origin, diagnostics = tool_reentries(events)
    rounds = {row["origin_request_id"]: row for row in episodes}
    streams, join_ready = defaultdict(list), {}
    for event in events:
        if event.get("invocation_id"):
            streams[event["workflow_id"], event["invocation_id"]].append(event)
        if event["kind"] == "join_satisfied":
            key = event["workflow_id"], event["join_id"]
            if key in join_ready:
                raise ValueError("duplicate JOIN completion")
            join_ready[key] = event["ts_ms"]
    for stream in streams.values():
        stream.sort(key=lambda row: (row["ts_ms"], row["sequence"]))
    requests = {row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")}
    first = {}
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        for member in row["request_samples"]:
            first.setdefault(member["request_id"], {
                "launch_ms": row["launch_monotonic_ms"],
                "member": member,
            })
    choices, replacements = defaultdict(list), []
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "agent_free_capacity_choice":
            if row.get("grant_published") is not True or row.get("logical_capacity_only") is not True:
                raise ValueError("only actual published logical choices may supply alternatives")
            for candidate in row["candidates"]:
                if candidate["selected"]:
                    choices[identity(candidate["key"], grant=True)].append(row)
        elif row.get("event") == "agent_predicted_tool_replacement":
            replacements.append(row)
    if len(replacements) != previous["counts"]["actual_event_replacements"]:
        raise ValueError("the original event replacement population changed")
    windows = {
        (identity(row["consumer"], grant=True), row["replacement_monotonic_ms"]): row
        for row in previous["rows"]
    }
    counts, rows = Counter(), []
    cached_stages = {}

    def stage(key):
        exact = identity(key, grant=True)
        if exact not in cached_stages:
            rid, workflow, invocation, context, epoch, _attempt, session, generation = exact
            source = work.get(rid)
            if source is None or any(
                source[name] != value for name, value in (
                    ("workflow_id", workflow), ("invocation_id", invocation), ("context_id", context),
                )
            ):
                raise ValueError("published candidate differs from the complete original inventory")
            receipt = requests.get(rid)
            if receipt is not None and (
                receipt.get("exact_tokens_verified") is not True
                or tuple(receipt["native_receipt"]["identity"]) != (workflow, invocation, context, epoch)
                or receipt["native_receipt"]["session_id"] != session
                or receipt["native_receipt"]["session_generation"] != generation
                or (receipt["prompt_tokens"], receipt["output_tokens"]) != (
                    source["prompt_tokens"], source["output_tokens"],
                )
            ):
                raise ValueError("candidate completed receipt changes exact work or ownership")
            result = next_stage(
                streams[workflow, invocation], rid, work, rounds, join_ready,
                coefficient, long_tool_ms,
            )
            result["complete_current_request_receipt_observed"] = receipt is not None
            cached_stages[exact] = result
        return cached_stages[exact]

    for replacement in replacements:
        when = replacement["monotonic_s"] * 1000.
        exact = identity(replacement["consumer"], grant=True)
        prior = windows.get((exact, when))
        if prior is None:
            raise ValueError("replacement lacks the previously resolved exact donor window")
        source_round = rounds[replacement["donor"]["request_id"]]
        donor_next = source_round.get("request_id")
        donor_request, donor_service = requests.get(donor_next), first.get(donor_next)
        donor_queue = None
        if donor_request is not None and donor_service is not None:
            if donor_request["submitted_monotonic_ms"] < source_round["client_ready_ts_ms"]:
                raise ValueError("donor successor submitted before its own tool completed")
            donor_queue = donor_service["launch_ms"] - donor_request["submitted_monotonic_ms"]
            if donor_queue < 0:
                raise ValueError("donor successor first service precedes submission")
        result = {
            "consumer": replacement["consumer"], "donor": replacement["donor"],
            "replacement_monotonic_ms": when,
            "remaining_donor_window_ms": prior["observed_remaining_tool_window_ms"],
            "consumer_stage": stage(replacement["consumer"]),
            "donor_successor_request_id": donor_next,
            "observed_donor_successor_submit_to_first_service_ms": donor_queue,
        }
        counts["actual_event_replacements"] += 1
        counts["donor_successor_service_observed"] += donor_queue is not None
        current = result["consumer_stage"]
        if current["supported"]:
            counts["consumer_stage_resolved"] += 1
            result["observed_consumer_boundary_before_donor_return"] = (
                current["observed_boundary_ms"] <= when + prior["observed_remaining_tool_window_ms"]
            )
            result["optimistic_consumer_phase_proxy_fits_window"] = (
                current["phase_cost_proxy_ms"] <= prior["observed_remaining_tool_window_ms"]
            )
            counts["observed_consumer_boundary_before_donor_return"] += result[
                "observed_consumer_boundary_before_donor_return"
            ]
            counts["optimistic_consumer_phase_proxy_fits_window"] += result[
                "optimistic_consumer_phase_proxy_fits_window"
            ]
        choice = matching_choice(choices, replacement)
        if choice is None:
            result["alternative_status"] = "no_matching_published_choice"
        elif not choice["ready_unprotected_competition"]:
            counts["matching_nonrankable_choice"] += 1
            result["alternative_status"] = "protected_or_noncompetitive_choice"
        else:
            counts["published_ready_unprotected_competitions"] += 1
            candidates = []
            for candidate in choice["candidates"]:
                if (
                    not candidate["application_ready"] or candidate["protected_join"]
                    or candidate["complete_budget_tokens"] > choice["remaining_before_tokens"]
                ):
                    continue
                phase = stage(candidate["key"])
                candidates.append({
                    **candidate, "phase": phase,
                    "measured_workflow": candidate["key"]["root_workflow_id"] in measured,
                    "optimistic_phase_proxy_fits_window": phase["supported"] and (
                        phase["phase_cost_proxy_ms"] <= prior["observed_remaining_tool_window_ms"]
                    ),
                })
            alternatives = [row for row in candidates if not row["selected"]]
            fitting = [row for row in alternatives if row["optimistic_phase_proxy_fits_window"]]
            counts["competitions_with_any_phase_fit_alternative"] += bool(fitting)
            switch = bool(
                current["supported"] and not result["optimistic_consumer_phase_proxy_fits_window"]
                and fitting
            )
            counts["competitions_with_phase_fit_switch"] += switch
            counts["phase_fit_switch_with_observed_donor_queue_over_1s"] += bool(
                switch and donor_queue is not None and donor_queue > 1000.
            )
            result.update(
                alternative_status="bounded_logical_candidates_only",
                candidates=candidates, phase_fit_switch_possible=bool(switch),
                same_original_resume_category_proven=False,
                alternative_physical_fit_proven=False,
            )
        rows.append(result)
    switches = [row for row in rows if row.get("phase_fit_switch_possible")]
    donor_successors = {
        row["donor_successor_request_id"]: row for row in switches
        if row["donor_successor_request_id"] is not None
    }
    for filename, prefix in reader.prefixes.items():
        old = previous["input_jsonl_prefixes"].get(filename)
        if old is not None and old != prefix:
            raise ValueError("the complete original records differ from the prior window audit")
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed stage inputs changed during analysis")
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
        "long_tool_stage_threshold_ms": long_tool_ms,
        "threshold_role": "offline boundary sensitivity, not the live dynamic offload test",
        "frozen_seconds_per_model_work_unit": coefficient,
        "consumer_phase_cost_proxy_ms": distribution(
            row["consumer_stage"]["phase_cost_proxy_ms"] for row in rows
            if row["consumer_stage"]["supported"]
        ),
        "donor_successor_submit_to_first_service_ms": distribution(
            row["observed_donor_successor_submit_to_first_service_ms"] for row in rows
            if row["observed_donor_successor_submit_to_first_service_ms"] is not None
        ),
        "stage_boundary_counts": dict(Counter(
            row["consumer_stage"].get("boundary_kind", row["consumer_stage"].get("reason"))
            for row in rows
        )),
        "tool_round_diagnostics": diagnostics, "rows": rows,
        "phase_fit_switch_scope": {
            "records": len(switches),
            "distinct_donor_successor_requests": len(donor_successors),
            "distinct_donor_workflows": len({row["donor"]["root_workflow_id"] for row in switches}),
            "measured_donor_workflows": len({
                row["donor"]["root_workflow_id"] for row in switches
                if row["donor"]["root_workflow_id"] in measured
            }),
            "distinct_measured_fit_alternative_workflows": len({
                member["key"]["root_workflow_id"] for row in switches
                for member in row["candidates"]
                if not member["selected"] and member["optimistic_phase_proxy_fits_window"]
                and member["measured_workflow"]
            }),
            "deduplicated_observed_donor_submit_wait_ms": sum(
                row["observed_donor_successor_submit_to_first_service_ms"] or 0.
                for row in donor_successors.values()
            ),
            "observed_wait_is_not_removable_JCT_or_a_global_upper_bound": True,
        },
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                registration, cohort_path, prior_path, artifact_path, Path(__file__),
                ROOT / "scripts/audit_tool_reentry_service.py",
                ROOT / "scripts/audit_free_dispatch_service.py",
                ROOT / "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        "input_jsonl_prefixes": reader.prefixes,
        "scope": [
            "Candidates come only from same-request/attempt/session published grants within 50ms of replacement.",
            "Protected JOIN and unready candidates are excluded; remaining categories and physical fit stay unproven.",
            "Exact future source outputs and future observed tools/boundaries are used only offline.",
            "Phase cost is a fixed model-work proxy plus observed short-tool durations, without queue or context-exact GPU calibration.",
            "Restore, backup, early tool return, native safety and eventual physical release costs remain uncharged.",
            "Donor successor queue time is observed contention, not exclusive delay caused by this consumer.",
            "Return/long-tool/JOIN boundary is potential release, not verified settled physical KV capacity.",
            "A phase-fit switch does not prove mean-JCT or throughput improvement.",
        ],
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "prior", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--long-tool-ms", type=float, default=2000.)
    args = parser.parse_args()
    if (
        args.output.exists() or not 1000. <= args.long_tool_ms <= 10000.
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use bounded nice19 CPU-only analysis")
    result = inspect(args.registration, args.cohort, args.prior, long_tool_ms=args.long_tool_ms)
    write(args.output, result)
    print(json.dumps({key: result[key] for key in (
        "counts", "stage_boundary_counts", "phase_fit_switch_scope", "consumer_phase_cost_proxy_ms",
        "donor_successor_submit_to_first_service_ms",
    )}))


if __name__ == "__main__":
    main()
