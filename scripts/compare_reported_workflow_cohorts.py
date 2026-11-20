"""Compare full workflow JCT and fixed-window output from sealed CPU readouts."""

import argparse
from collections import Counter
from itertools import combinations
import json
import math
from pathlib import Path
from statistics import fmean
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import summarize_cohort, validate_cohort
from beliefkv.experiments.workflow_metrics import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.stop_after_measured_cohort import boundaries
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_measured_execution


PARTITIONS = ("measured", "warmup", "background")
RAW_FILES = (
    "replay/runtime_events.replay.jsonl",
    "server/runtime_audit.jsonl",
    "replay/native_requests.jsonl",
)


def load(path):
    return json.loads(path.read_bytes())


def membership(cohort):
    return {name: cohort[name + "_workflow_ids"] for name in PARTITIONS}


def cohort_contract(cohort):
    # The complete-run and fixed-stop schemas have the same membership contract.
    return (
        cohort["arrival_plan_sha256"], cohort["throughput_window_offset_seconds"],
        membership(cohort),
    )


def frozen_declaration(report, arrivals_path, supplied_path):
    pins = report["inputs_sha256"]
    candidates = [Path(name) for name in pins if (
        Path(name).suffix == ".json" and Path(name) != arrivals_path
        and Path(name).name != "expected.json"
    )]
    if supplied_path is not None:
        candidates.insert(0, supplied_path)
    for path in candidates:
        if path.is_file() and load(path) == report["cohort"]:
            digest = sha256_file(path)
            if digest in pins.values():
                return path, digest
    raise ValueError("cohort report has no matching sealed declaration")


def registered_arm(registration, report):
    run_root = Path(registration["run_root"])
    candidates = (run_root / label for label in registration["order"])
    paths = {
        arm for arm in candidates if str(arm / RAW_FILES[0]) in report["input_jsonl_prefixes"]
    }
    if len(paths) != 1:
        raise ValueError("cohort seal must identify one replay arm in the registration")
    return paths.pop()


def audit_arm(registration_path, registration, report_path, report, arm, supplied_path):
    arrivals_path = Path(registration["workflow_arrivals_path"])
    arrivals = load(arrivals_path)
    arrivals_digest = sha256_file(arrivals_path)
    if (
        arrivals != registration["workflow_arrivals"]
        or report["inputs_sha256"].get(str(arrivals_path.resolve())) != arrivals_digest
    ):
        raise ValueError("cohort report and registration must bind the same sealed arrivals")
    cohort_path, cohort_digest = frozen_declaration(report, arrivals_path, supplied_path)
    cohort = report["cohort"]
    validate_cohort(cohort, arrivals)
    members = membership(cohort)
    scheduled = {row["workflow_id"]: row["offset_seconds"] for row in arrivals["arrival_schedule"]}
    if set(registration["workflow_ids"]) != scheduled.keys():
        raise ValueError("registration has a different workflow population")

    events_path, samples_path, receipts_path = (arm / name for name in RAW_FILES)
    prefixes = {str(path): report["input_jsonl_prefixes"][str(path)]
                for path in (events_path, samples_path, receipts_path)}
    reader = SealedRecords()
    reader.prefixes.update(prefixes)

    def check_sizes():
        for name, seal in prefixes.items():
            size = seal["complete_prefix_bytes"]
            if not 0 < size == seal["first_read_limit_bytes"] == Path(name).stat().st_size:
                raise ValueError(f"cohort requires a complete unchanged JSONL seal: {name}")

    check_sizes()
    pins = {
        str(path.resolve()): sha256_file(path) for path in (registration_path, report_path)
    }
    pins.update({str(arrivals_path.resolve()): arrivals_digest, str(cohort_path.resolve()): cohort_digest})
    certificate = None
    if cohort["schema_version"] == 2:
        evidence = verify_measured_execution(registration_path, cohort_path)
        certificate_path = Path(evidence["certificate"])
        certificate = load(certificate_path)
        pins[str(certificate_path)] = evidence["certificate_sha256"]
        if (
            report.get("measured_service_output_matches_complete_receipts") is not True
            or report["measured_exact_receipt_requests"] != evidence["measured_requests"]
            or certificate["input_jsonl_prefixes"][str(receipts_path)] != prefixes[str(receipts_path)]
        ):
            raise ValueError("cohort report differs from certified measurable work")
    elif (
        report.get("service_output_matches_complete_receipts") is not True
        or type(report.get("exact_receipt_requests")) is not int
        or report["exact_receipt_requests"] <= 0
        or sha256_file(receipts_path) != prefixes[str(receipts_path)]["complete_prefix_sha256"]
    ):
        raise ValueError("complete cohort requires sealed exact native receipts")

    events = [row for row in reader(events_path) if row.get("kind") in (
        "workflow_start", "workflow_end",
    )]
    starts, ends = {}, {}
    boundaries(events, starts, ends, scheduled.keys())
    first = next(iter(scheduled))
    origin_ms = starts[first] - scheduled[first] * 1000.
    window_start, window_end = cohort["throughput_window_offset_seconds"]
    output = Counter()

    def samples():
        for row in reader(samples_path):
            if row.get("event") == "gpu_service_sample":
                # Count the same half-open window after the auditor validates the batch.
                yield row
                offset = (row["complete_monotonic_ms"] - origin_ms) / 1000.
                if window_start <= offset < window_end:
                    for request in row["request_samples"]:
                        output[request["workflow_id"]] += request["output_token_delta"]

    audited = summarize_cohort(cohort, arrivals, events, samples())
    check_sizes()
    fields = (
        "measured_complete_workflow_JCT_ms", "all_workflows_completed", "normal_tails_removed",
        "all_service_output_tokens", "all_service_output_tokens_by_workflow",
        "output_accounting_field", "token_window_assignment",
        "common_window_all_workflows_output_tokens", "common_window_output_tokens_per_second",
        *(name + "_workflow_JCT_ms" for name in PARTITIONS),
    )
    if certificate is not None:
        fields += (
            "all_measured_completed", "measured_cohort_completion_offset_s",
            "nonmeasured_workflows_right_censored", "unarrived_workflows_at_observation_end",
        )
        if (
            report["measured_workflow_JCT_ms"] != certificate["measured_JCT_ms"]
            or certificate["cohort_completion_monotonic_ms"] != max(
                ends[key] for key in members["measured"]
            )
            or sorted(ends) != evidence["completed_workflow_ids"]
        ):
            raise ValueError("cohort report differs from certified workflow boundaries")
        expected_path = Path(registration["source_arm"]) / "expected.json"
        expected_digest = sha256_file(expected_path)
        if report["inputs_sha256"].get(str(expected_path.resolve())) != expected_digest:
            raise ValueError("the warmup receipt population lacks a pinned source")
        pins[str(expected_path.resolve())] = expected_digest
        completed_receipts = set(evidence["completed_receipt_requests"])
        completed_warmup = set(members["warmup"]) & ends.keys()
        expected_output = Counter()
        for request_id, row in load(expected_path).items():
            workflow_id = row["llm_submit"][0][0]
            if workflow_id not in completed_warmup:
                continue
            if request_id not in completed_receipts:
                raise ValueError("a completed warmup workflow lacks an exact receipt")
            expected_output[workflow_id] += row["llm_result"][1]
        if any(
            audited["all_service_output_tokens_by_workflow"].get(key, 0) != expected_output[key]
            for key in completed_warmup
        ):
            raise ValueError("completed warmup output differs from its exact receipt population")
    if any(report[key] != audited[key] for key in fields):
        raise ValueError("cohort report differs from the sealed workflow and service audit")

    system_tokens = report["common_window_all_workflows_output_tokens"]
    partition_tokens = {name: sum(output[key] for key in ids) for name, ids in members.items()}
    if (
        type(system_tokens) is not int
        or sum(partition_tokens.values()) != system_tokens
        or sum(row["output_tokens"] for row in report["common_window_throughput_bins"]) != system_tokens
    ):
        raise ValueError("group output and sealed system window totals do not reconcile")

    # The measured cohort's completion time bounds observation follow-up.
    end_ms = certificate["cohort_completion_monotonic_ms"] if certificate else max(ends.values())
    return {
        "natural_jct_s": {
            key: elapsed_ms / 1000. for name in PARTITIONS
            for key, elapsed_ms in audited[name + "_workflow_JCT_ms"].items()
        },
        "arrival_offsets_s": {key: (start - origin_ms) / 1000. for key, start in starts.items()},
        "observation_end_offset_s": (end_ms - origin_ms) / 1000.,
        "termination_rule": cohort.get("termination_rule", "all_workflows_completed"),
        "partition_tokens": partition_tokens,
        "system_window_output": {
            "output_tokens": system_tokens,
            "output_tokens_per_second": report["common_window_output_tokens_per_second"],
        },
        "inputs_sha256": pins,
        "input_jsonl_prefixes": prefixes,
    }


def report_group(observations, members, partitions, window_seconds):
    arms, completed = {}, {}
    for label, arm in observations.items():
        jct, arrivals = arm["natural_jct_s"], arm["arrival_offsets_s"]
        completed[label] = set(members) & jct.keys()
        natural = None
        if len(completed[label]) == len(members):
            stats = distribution(jct[key] for key in members)
            natural = {key: stats[key] for key in ("mean", "p50", "p95")}
        tokens = sum(arm["partition_tokens"][name] for name in partitions)
        arms[label] = {
            "completed_workflow_count": len(completed[label]),
            "right_censored_workflow_ids": [key for key in members if key in arrivals and key not in jct],
            "unarrived_workflow_ids": [key for key in members if key not in arrivals],
            "natural_complete_JCT_s": natural,
            "restricted_mean_JCT_s": None,
            "window_output_tokens": tokens,
            "window_output_tokens_per_second": tokens / window_seconds,
        }

    paired = []
    for left, right in combinations(completed, 2):
        first, second = completed[left], completed[right]
        paired.append({
            "arms": [left, right],
            "both_completed": len(first & second),
            "first_only_completed": len(first - second),
            "second_only_completed": len(second - first),
            "neither_completed": len(members) - len(first | second),
        })

    complete_count = len(set.intersection(*completed.values()))
    restriction = None
    if complete_count != len(members):
        cutoff = min(arm["observation_end_offset_s"] for arm in observations.values())
        restriction = {"common_wall_cutoff_offset_s": cutoff, "common_followup_s": None}
        if any(row["unarrived_workflow_ids"] for row in arms.values()):
            restriction["unavailable_reason"] = "a fixed member did not arrive in every arm"
        else:
            latest_arrival = max(
                arm["arrival_offsets_s"][key] for arm in observations.values() for key in members
            )
            tau = cutoff - latest_arrival
            if not math.isfinite(tau) or tau <= 0:
                restriction["unavailable_reason"] = "no positive follow-up for the entire fixed group"
            else:
                restriction["common_followup_s"] = tau
                for label, arm in observations.items():
                    jct = arm["natural_jct_s"]
                    arms[label]["restricted_mean_JCT_s"] = fmean(
                        min(jct.get(key, tau), tau) for key in members
                    )
    return {
        "member_count": len(members), "workflow_ids": members,
        "completed_in_all_arms_count": complete_count,
        "arms": arms, "paired_completion_counts": paired, "restriction": restriction,
    }


def compare_arms(arm_specs, cohort_plan=None):
    if len(arm_specs) < 2 or len({label for label, *_ in arm_specs}) != len(arm_specs):
        raise ValueError("supply at least two distinctly named arms")
    cohort_path = Path(cohort_plan).resolve() if cohort_plan else None
    reference, token_source, tool_timing = None, None, None
    if cohort_path is not None:
        reference = load(cohort_path)
        if isinstance(reference.get("cohort"), str):
            cohort_path = (cohort_path.parent / reference["cohort"]).resolve()
            reference = load(cohort_path)

    inputs, seen = {}, set()
    for label, registration_name, report_name in arm_specs:
        registration_path, report_path = (Path(name).resolve() for name in (registration_name, report_name))
        registration, report = load(registration_path), load(report_path)
        cohort = report["cohort"]
        if reference is None:
            reference = cohort
        if cohort_contract(cohort) != cohort_contract(reference):
            raise ValueError("arms must use identical frozen arrivals, membership and throughput window")
        if token_source is None:
            token_source = registration["token_source_sha256"]
        if not token_source or registration["token_source_sha256"] != token_source:
            raise ValueError("arms must replay the same frozen token source")
        overlay = (registration.get("tool_timing_overlay") or {}).get("sha256")
        if inputs and overlay != tool_timing:
            raise ValueError("arms must replay the same tool timing overlay")
        tool_timing = overlay
        arm = registered_arm(registration, report)
        if arm in seen:
            raise ValueError("each named arm must refer to a distinct replay")
        seen.add(arm)
        inputs[label] = registration_path, registration, report_path, report, arm

    members = membership(reference)
    observations = {}
    for label, args in inputs.items():
        try:
            observations[label] = audit_arm(*args, cohort_path)
        except (OSError, ValueError, KeyError) as error:
            raise ValueError(f"{label}: {error}") from error

    start, end = reference["throughput_window_offset_seconds"]
    duration = end - start
    return {
        "schema_version": 1,
        "arrival_plan_sha256": reference["arrival_plan_sha256"],
        "token_source_sha256": token_source,
        "tool_timing_overlay_sha256": tool_timing,
        "throughput_window": {
            "offset_seconds": [start, end], "duration_seconds": duration,
            "bounds": "[start, end)", "clock": "native_batch_completion",
            "timestamp_field": "complete_monotonic_ms",
            "output_accounting_field": "output_token_delta",
        },
        "groups": {
            f"fixed_{len(members['measured'])}": report_group(
                observations, members["measured"], ("measured",), duration,
            ),
            "fixed_measured_plus_warmup": report_group(
                observations, members["warmup"] + members["measured"], ("measured", "warmup"), duration,
            ),
        },
        "arms": {
            label: {
                **{key: arm[key] for key in (
                    "system_window_output", "termination_rule", "observation_end_offset_s",
                    "inputs_sha256", "input_jsonl_prefixes",
                )},
                "partition_window_output": {
                    name: {"output_tokens": count, "output_tokens_per_second": count / duration}
                    for name, count in arm["partition_tokens"].items()
                },
            }
            for label, arm in observations.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Run from the repository root.

  CUDA_VISIBLE_DEVICES=99999 taskset -c 0 nice -n 19 python \\
    scripts/compare_reported_workflow_cohorts.py --cohort path/to/analysis_cohort.json \\
    --arm unranked unranked.registration.json unranked/pressure_review/cohort.json \\
    --arm ranked ranked.registration.json ranked/pressure_review/cohort.json \\
    --output comparison.json

The registration is the serving replay registration. Each cohort.json must
contain the auditor's complete JSONL seals and the raw logs must still exist.
The optional common cohort path can be a frozen campaign plan that points to
the declaration. Omit it to use the declarations pinned by the sealed reports.
Complete schema-1 reports also work with matching members, arrivals and window.

For a censored group, a restricted mean averages min(JCT, common_followup)
over all fixed members. Follow-up runs from each arrival and ends no later
than the earliest certified observation end across arms.""",
    )
    parser.add_argument("--arm", action="append", nargs=3, required=True,
                        metavar=("NAME", "REGISTRATION", "COHORT_REPORT"))
    parser.add_argument("--cohort", type=Path, help="common frozen declaration or campaign plan")
    parser.add_argument("--output", type=Path, required=True, help="new comparison JSON")
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"comparison already exists: {args.output}")
    try:
        write(args.output, compare_arms(args.arm, args.cohort))
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(args.output)


if __name__ == "__main__":
    main()
