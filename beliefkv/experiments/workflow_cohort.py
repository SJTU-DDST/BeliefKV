"""Arrival-index workflow cohorts and common-wall-window serving statistics."""

from collections import Counter
import math

from beliefkv.experiments.replay_arrivals import validate_arrival_plan
from beliefkv.experiments.tool_timing_overlay import fingerprint
from beliefkv.experiments.workflow_metrics import distribution


def declare_cohort(
    arrivals, *, warmup, measured, throughput_start_s, throughput_end_s,
    stop_after_measured=False,
):
    rows = arrivals["arrival_schedule"]
    if (
        type(warmup) is not int or type(measured) is not int
        or warmup < 1 or measured < 1 or warmup + measured >= len(rows)
        or any(type(value) not in (int, float) or not math.isfinite(value)
               for value in (throughput_start_s, throughput_end_s))
        or not 0 <= throughput_start_s < throughput_end_s <= rows[-1]["offset_seconds"]
        or type(stop_after_measured) is not bool
        or stop_after_measured and throughput_end_s > rows[warmup + measured - 1]["offset_seconds"]
    ):
        raise ValueError("declare warmup, measured members, background and a common finite window")
    ids = [row["workflow_id"] for row in rows]
    validate_arrival_plan(arrivals, sorted(ids))
    return {
        "schema_version": 2 if stop_after_measured else 1,
        "kind": "fixed_arrival_index_workflow_cohort",
        "arrival_plan_sha256": fingerprint(arrivals),
        "warmup_workflow_ids": ids[:warmup],
        "measured_workflow_ids": ids[warmup:warmup + measured],
        "background_workflow_ids": ids[warmup + measured:],
        "throughput_window_offset_seconds": [throughput_start_s, throughput_end_s],
        "selection_rule": "fixed_arrival_indices_before_execution",
        "complete_JCT_including_normal_tails": True,
        "background_submission_stops_when_cohort_finishes": stop_after_measured,
        "cohort_identity_available_to_scheduler": False,
        "steady_state_claimed_without_load_calibration": False,
        **({
            "termination_rule": "all_fixed_measured_workflows_completed",
            "nonmeasured_workflows_may_be_right_censored": True,
            "throughput_window_covered_before_last_measured_arrival": True,
        } if stop_after_measured else {}),
    }


def validate_cohort(cohort, arrivals):
    expected = declare_cohort(
        arrivals, warmup=len(cohort["warmup_workflow_ids"]),
        measured=len(cohort["measured_workflow_ids"]),
        throughput_start_s=cohort["throughput_window_offset_seconds"][0],
        throughput_end_s=cohort["throughput_window_offset_seconds"][1],
        stop_after_measured=cohort.get("schema_version") == 2,
    )
    if cohort != expected:
        raise ValueError("cohort changed frozen arrival membership or measurement semantics")


def _empty_inflight_tail(sample, member, *, start, end):
    launch = sample.get("launch_monotonic_ms")
    after, reported = member.get("output_tokens_after"), member.get("output_tokens_reported_before")
    return (
        sample["phase"] == "decode"
        and type(launch) in (int, float) and math.isfinite(launch) and start <= launch <= end
        and member["output_token_delta"] == 0
        and type(member.get("token_delta")) is int and member["token_delta"] == 0
        and type(after) is int and type(reported) is int and after == reported > 0
    )


def summarize_cohort(cohort, arrivals, events, service_samples, *, bin_seconds=30.):
    """Retain full JCT; assign actual output to its native batch completion time."""
    validate_cohort(cohort, arrivals)
    if type(bin_seconds) not in (int, float) or not math.isfinite(bin_seconds) or bin_seconds <= 0:
        raise ValueError("throughput bin width must be finite and positive")
    starts, ends = {}, {}
    for event in events:
        if event["kind"] not in ("workflow_start", "workflow_end"):
            continue
        mapping = starts if event["kind"] == "workflow_start" else ends
        workflow, when = event["workflow_id"], event["ts_ms"]
        if (
            workflow in mapping or type(when) not in (int, float) or not math.isfinite(when)
        ):
            raise ValueError("workflow measurement has a duplicate or invalid boundary")
        mapping[workflow] = when
    ids = [row["workflow_id"] for row in arrivals["arrival_schedule"]]
    members = cohort["measured_workflow_ids"]
    cohort_stop = cohort["schema_version"] == 2
    if cohort_stop:
        if (
            not set(members) <= ends.keys()
            or not ends.keys() <= starts.keys()
            or set(starts) != set(ids[:len(starts)])
        ):
            raise ValueError("all fixed measured workflows must finish within a legal arrival prefix")
    elif set(starts) != set(ids) or set(ends) != set(ids):
        raise ValueError("all measured, warmup and background workflows must finish")
    origins = [starts[row["workflow_id"]] - row["offset_seconds"] * 1000.
               for row in arrivals["arrival_schedule"] if row["workflow_id"] in starts]
    origin = origins[0]
    if max(origins) - min(origins) > 1e-5 or any(ends[key] < starts[key] for key in ends):
        raise ValueError("observed workflow arrivals differ from the frozen schedule")
    elapsed = {key: ends[key] - starts[key] for key in ends}
    measured_end = max(ends[key] for key in members)
    if cohort_stop and any(
        origin + row["offset_seconds"] * 1000. <= measured_end
        and row["workflow_id"] not in starts
        for row in arrivals["arrival_schedule"]
    ):
        raise ValueError("a scheduled arrival before measured completion is missing")
    submission_end = (
        origin + arrivals["arrival_schedule"][-1]["offset_seconds"] * 1000.
        if cohort_stop else max(starts.values())
    )
    start_s, end_s = cohort["throughput_window_offset_seconds"]
    bins = [{
        "start_s": start_s + index * bin_seconds,
        "end_s": min(start_s + (index + 1) * bin_seconds, end_s),
        "output_tokens": 0, "decode_tokens": 0, "decode_batches": 0,
    } for index in range(math.ceil((end_s - start_s) / bin_seconds))]
    seen, domains, widths = set(), set(), Counter()
    output = decode = observed_output = 0
    empty_tails = []
    output_by_workflow = Counter()
    for sample in service_samples:
        if sample.get("event") != "gpu_service_sample":
            continue
        forward, when = sample["forward_iter"], sample["complete_monotonic_ms"]
        if (
            forward in seen or not math.isfinite(when)
            or sample["phase"] not in ("prefill", "decode")
            or any(row["workflow_id"] not in starts
                   or type(row.get("output_token_delta")) is not int
                   or row["output_token_delta"] < 0
                   or when < starts[row["workflow_id"]]
                   or row["workflow_id"] in ends
                   and when > ends[row["workflow_id"]] and not _empty_inflight_tail(
                       sample, row, start=starts[row["workflow_id"]], end=ends[row["workflow_id"]],
                   )
                   for row in sample["request_samples"])
        ):
            raise ValueError("native service samples are duplicated, invalid or foreign")
        seen.add(forward)
        domains.add(sample["monotonic_clock_domain"])
        # token_delta measures extend input in prefill samples; only this field
        # counts newly generated output in both prefill and decode.
        tokens = sum(row["output_token_delta"] for row in sample["request_samples"])
        observed_output += tokens
        for row in sample["request_samples"]:
            output_by_workflow[row["workflow_id"]] += row["output_token_delta"]
            if row["workflow_id"] in ends and when > ends[row["workflow_id"]]:
                empty_tails.append(when - ends[row["workflow_id"]])
        offset = (when - origin) / 1000.
        if not start_s <= offset < end_s:
            continue
        index = min(int((offset - start_s) / bin_seconds), len(bins) - 1)
        output += tokens
        bins[index]["output_tokens"] += tokens
        if sample["phase"] == "decode":
            decode += tokens
            widths[sample["batch_size"]] += 1
            bins[index]["decode_tokens"] += tokens
            bins[index]["decode_batches"] += 1
    if not seen or len(domains) != 1:
        raise ValueError("complete serving samples require one native observation clock")
    for row in bins:
        duration = row["end_s"] - row["start_s"]
        row["output_tokens_per_second"] = row["output_tokens"] / duration
        row["decode_tokens_per_second"] = row["decode_tokens"] / duration
    return {
        "cohort": cohort, "measured_complete_workflow_JCT_ms": distribution(elapsed[key] for key in members),
        "measured_workflow_JCT_ms": {key: elapsed[key] for key in members},
        "warmup_workflow_JCT_ms": {
            key: elapsed[key] for key in cohort["warmup_workflow_ids"] if key in elapsed
        },
        "background_workflow_JCT_ms": {
            key: elapsed[key] for key in cohort["background_workflow_ids"] if key in elapsed
        },
        "background_submission_coverage": {
            "last_scheduled_arrival_offset_s": (submission_end - origin) / 1000.,
            "last_measured_completion_offset_s": (measured_end - origin) / 1000.,
            "covers_all_measured_completions": submission_end >= measured_end,
            "uncovered_drain_seconds": max(0., (measured_end - submission_end) / 1000.),
            **({
                "coverage_basis": "frozen_background_schedule_continues_until_measured_completion",
                "last_observed_arrival_offset_s": (max(starts.values()) - origin) / 1000.,
            } if cohort_stop else {}),
        },
        "common_window_all_workflows_output_tokens": output,
        "common_window_output_tokens_per_second": output / (end_s - start_s),
        "common_window_decode_tokens_per_second": decode / (end_s - start_s),
        "common_window_decode_batch_counts": dict(sorted(widths.items())),
        "common_window_throughput_bins": bins,
        "all_service_output_tokens": observed_output,
        "all_service_output_tokens_by_workflow": dict(output_by_workflow),
        "output_accounting_field": "output_token_delta",
        "zero_output_inflight_tail_rows": {
            "count": len(empty_tails), "maximum_after_workflow_end_ms": max(empty_tails, default=0.),
            "rule": "decode_launched_within_workflow;zero_delta;reported_output_counter_unchanged",
            "positive_output_lifetime_check_unchanged": True,
            "output_times_clamped_or_JCT_shifted": False,
        },
        "token_window_assignment": "native_batch_completion; includes_background_and_first_prefill_outputs",
        "all_workflows_completed": set(ends) == set(ids),
        "all_measured_completed": True, "normal_tails_removed": False,
        **({
            "measured_cohort_completion_offset_s": (measured_end - origin) / 1000.,
            "nonmeasured_workflows_right_censored": sorted(set(ids) - set(ends)),
            "unarrived_workflows_at_observation_end": sorted(set(ids) - set(starts)),
            "complete_population_work_conserved": set(ends) == set(ids),
            "nonmeasured_tails_not_used_as_JCT_samples": True,
            "normal_tails_removed_scope": "fixed_measured_workflows_only",
            "nonmeasured_completed_JCT_is_conditional": True,
        } if cohort_stop else {}),
        "steady_state_established": False, "performance_accepted": False,
    }
