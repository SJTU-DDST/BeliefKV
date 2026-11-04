"""Screen resident decode completion opportunities inside closed prefill bursts."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_decode_transfer_envelopes import EnvelopeIndex
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def sample_key(sample):
    names = (
        "request_id", "workflow_id", "invocation_id", "context_id", "context_epoch",
    )
    if any(sample.get(name) is None for name in names):
        return None
    return tuple(sample[name] for name in names)


def closed_bursts(forwards):
    """Require decode service on both sides; omit startup and censored tails."""
    previous, pending = None, []
    for row in forwards:
        if row["phase"] == "prefill":
            pending.append(row)
        elif row["phase"] == "decode":
            if previous is not None and pending:
                yield previous, tuple(pending), row
            previous, pending = row, []
        else:
            raise ValueError("unsupported forward phase")


def retained_members(previous, prefills, following):
    """Match observed identity and output; do not fill omitted attempt metadata."""
    later = {
        sample_key(row): row for row in following["request_samples"]
        if sample_key(row) is not None
    }
    prefilling = {
        sample_key(row) for batch in prefills for row in batch["request_samples"]
        if sample_key(row) is not None
    }
    for before in previous["request_samples"]:
        key = sample_key(before)
        after = later.get(key)
        if key is None or key in prefilling or after is None:
            continue
        extended = ("attempt_id", "session_id", "session_generation")
        if any(
            before.get(name) is not None and after.get(name) is not None
            and before[name] != after[name] for name in extended
        ):
            continue
        if before["phase"] != "decode" or after["phase"] != "decode":
            raise ValueError("flanking member is not decode")
        reported_before = after.get("output_tokens_reported_before", after["output_tokens_before"])
        if before["output_tokens_after"] != reported_before:
            raise ValueError("unrecorded output progress across a prefill-only burst")
        yield {
            **before,
            "full_state_identity_observed": all(
                before.get(name) is not None and after.get(name) is not None
                for name in extended
            ),
        }


def completion_proxy(begin_ms, end_ms, remaining_tokens, step_ms):
    if not all(math.isfinite(value) for value in (begin_ms, end_ms, step_ms)):
        raise ValueError("nonfinite service time")
    if (
        begin_ms >= end_ms or step_ms <= 0
        or type(remaining_tokens) is not int or remaining_tokens < 1
    ):
        raise ValueError("invalid completion geometry")
    cost = remaining_tokens * step_ms
    return {
        "extra_decode_worker_proxy_ms": cost,
        "local_burst_exposure_removed_proxy_ms": max(0., end_ms - begin_ms - cost),
    }


def frontier_index(events):
    by_invocation, results, joins, returned = defaultdict(list), {}, {}, {}
    for event in events:
        invocation = event.get("invocation_id")
        if invocation:
            by_invocation[invocation].append(event)
        kind = event["kind"]
        if kind == "llm_result":
            rid = event["attributes"]["request_id"]
            if rid in results:
                raise ValueError("duplicate LLM result")
            results[rid] = event
        elif kind == "join_create":
            joins[event["join_id"]] = {
                "members": tuple(event["member_invocation_ids"]),
                "mode": event["attributes"].get("mode"),
                "workflow_id": event["workflow_id"],
                "parent_invocation_id": None,
                "wait_ms": None, "satisfied_ms": None,
            }
        elif kind in ("join_wait", "join_satisfied"):
            name = "wait_ms" if kind == "join_wait" else "satisfied_ms"
            joins[event["join_id"]][name] = event["ts_ms"]
            if kind == "join_wait":
                joins[event["join_id"]]["parent_invocation_id"] = invocation
        elif kind == "return":
            returned[invocation] = event["ts_ms"]
    next_actions = {}
    boundaries = {"tool_start", "return", "spawn", "join_wait", "llm_submit"}
    for rows in by_invocation.values():
        result = None
        for row in sorted(rows, key=lambda item: (item["ts_ms"], item["sequence"])):
            if row["kind"] == "llm_result":
                result = row["attributes"]["request_id"]
            elif result and row["kind"] in boundaries:
                next_actions[result] = row["kind"]
                result = None
    return results, next_actions, joins, returned


def last_outstanding_child(invocation, when_ms, joins, returned):
    for join in joins.values():
        if (
            join["mode"] != "all" or invocation not in join["members"]
            or join["wait_ms"] is None or join["wait_ms"] > when_ms
            or join["satisfied_ms"] is not None and join["satisfied_ms"] <= when_ms
        ):
            continue
        outstanding = [
            member for member in join["members"]
            if returned.get(member, math.inf) > when_ms
        ]
        if outstanding == [invocation]:
            return True
    return False


def join_completion_skew(events, forwards, joins, returned, measured_workflows):
    selected = [
        (key, join) for key, join in joins.items()
        if join["mode"] == "all" and len(join["members"]) >= 2
        and join["wait_ms"] is not None and join["satisfied_ms"] is not None
        and all(member in returned for member in join["members"])
    ]
    needed = {
        max(join["members"], key=returned.__getitem__) for _, join in selected
    }
    worker, tool, starts, nested = defaultdict(list), defaultdict(list), {}, defaultdict(list)
    for event in events:
        invocation, attrs = event.get("invocation_id"), event["attributes"]
        if invocation not in needed or event["kind"] not in ("tool_start", "tool_end"):
            continue
        key = invocation, attrs.get("tool_run_id") or attrs.get("tool_call_id")
        if key[1] is None:
            raise ValueError("tool interval lacks a run identity")
        if event["kind"] == "tool_start":
            starts[key] = event["ts_ms"]
        elif key in starts:
            tool[invocation].append((starts.pop(key), event["ts_ms"]))
    for batch in forwards:
        for invocation in {
            member.get("invocation_id") for member in batch["request_samples"]
        } & needed:
            worker[invocation].append((
                batch["complete_monotonic_ms"] - batch["service_elapsed_ms"],
                batch["complete_monotonic_ms"],
            ))
    for join in joins.values():
        invocation = join["parent_invocation_id"]
        if invocation in needed and join["wait_ms"] is not None and join["satisfied_ms"] is not None:
            nested[invocation].append((join["wait_ms"], join["satisfied_ms"]))
    worker = {key: EnvelopeIndex(value) for key, value in worker.items()}
    tool = {key: EnvelopeIndex(value) for key, value in tool.items()}
    nested = {key: EnvelopeIndex(value) for key, value in nested.items()}
    empty = EnvelopeIndex([])
    details = []
    for key, join in selected:
        last = max(join["members"], key=returned.__getitem__)
        first_return = min(returned[member] for member in join["members"])
        begin, end = max(join["wait_ms"], first_return), returned[last]
        if end < begin or end > join["satisfied_ms"]:
            raise ValueError("JOIN completion chronology is invalid")
        details.append({
            "join_id": key, "workflow_id": join["workflow_id"],
            "measured_workflow": join["workflow_id"] in measured_workflows,
            "members": len(join["members"]), "last_child": last,
            "first_to_last_return_ms": end - first_return,
            "after_first_return_and_wait_ms": end - begin,
            "last_child_own_worker_overlap_ms": worker.get(last, empty).overlap_ms(begin, end),
            "last_child_tool_wait_overlap_ms": tool.get(last, empty).overlap_ms(begin, end),
            "last_child_nested_join_overlap_ms": nested.get(last, empty).overlap_ms(begin, end),
        })
    summaries = {}
    for name, rows in (
        ("all_closed_multi_member", details),
        ("measured_multi_member", [row for row in details if row["measured_workflow"]]),
    ):
        summaries[name] = {
            "groups": len(rows),
            **{key: distribution(row[key] for row in rows) for key in (
                "first_to_last_return_ms", "after_first_return_and_wait_ms",
                "last_child_own_worker_overlap_ms", "last_child_tool_wait_overlap_ms",
                "last_child_nested_join_overlap_ms",
            )},
        }
    return {
        "summaries": summaries, "groups": details,
        "scope": [
            "Only observed, completed ALL-JOINs with at least two direct members are included.",
            "The last-returning child and the return skew are retrospective labels, not online prediction.",
            "Direct-child worker, tool and nested-JOIN overlaps describe exposure and need not be disjoint.",
            "Uncovered time can include queuing, dependencies, dispatch and unknown states.",
            "Return skew is not removable delay; intrinsic branch work and blocking may require it.",
        ],
    }


def inspect(critical_path_report, tokenizer_path):
    report = json.loads(critical_path_report.read_bytes())
    registration = Path(report["registration"])
    for name, digest in report["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("prior complete-cohort evidence changed")
    config = json.loads(registration.read_bytes())
    arm = Path(config["run_root"]) / "full"
    tokenizer = json.loads(tokenizer_path.read_bytes())
    token_ids = {
        value["content"]: int(key)
        for key, value in tokenizer["added_tokens_decoder"].items()
        if value["content"] in ("<tool_call>", "</tool_call>")
    }
    if len(token_ids) != 2:
        raise ValueError("missing observed tool-boundary token definitions")
    reader = SealedRecords()
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    results, actions, joins, returned = frontier_index(events)
    requests = {}
    for request in reader(arm / "replay/native_requests.jsonl"):
        rid = request["request_id"]
        receipt = request["native_receipt"]
        ids = receipt["output_token_ids"]
        if rid in requests or request.get("exact_tokens_verified") is not True:
            raise ValueError("duplicate or unverified native request")
        if len(ids) != request["output_tokens"]:
            raise ValueError("output length differs from its exact native receipt")
        requests[rid] = {
            "total_outputs": len(ids),
            "identity": tuple(receipt["identity"]),
            "session_id": receipt["session_id"],
            "session_generation": receipt["session_generation"],
            "native_complete_ms": request["native_complete_monotonic_ms"],
            "marker_positions": {
                name: next((index + 1 for index, token in enumerate(ids) if token == value), None)
                for name, value in token_ids.items()
            },
        }
    forwards = []
    clock, launch = None, -math.inf
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        if (
            row["sample_id"] != f"native-service-{len(forwards) + 1:09d}"
            or row["launch_monotonic_ms"] < launch
            or clock is not None and row["monotonic_clock_domain"] != clock
            or not math.isclose(
                row["complete_monotonic_ms"] - row["service_start_monotonic_ms"],
                row["service_elapsed_ms"], rel_tol=1e-9, abs_tol=.01,
            )
        ):
            raise ValueError("incomplete or invalid native service timeline")
        launch, clock = row["launch_monotonic_ms"], row["monotonic_clock_domain"]
        forwards.append({
            name: row[name] for name in (
                "sample_id", "phase", "batch_size", "request_samples",
                "launch_monotonic_ms", "complete_monotonic_ms", "service_elapsed_ms",
            )
        })
    if reader.prefixes != report["input_jsonl_prefixes"]:
        raise ValueError("logs differ from the previously verified complete cohort")
    critical = {
        rid for workflow in report["critical_path"]["workflows"].values()
        for rid in workflow["critical_path_requests"]
    }
    cases, burst_rows = [], []
    counts = Counter({
        "native_member_samples_without_request_identity": sum(
            sample_key(member) is None
            for row in forwards for member in row["request_samples"]
        ),
        "native_member_samples_without_full_state_identity": sum(
            any(member.get(name) is None for name in (
                "attempt_id", "session_id", "session_generation",
            ))
            for row in forwards for member in row["request_samples"]
        ),
    })
    for previous, prefills, following in closed_bursts(forwards):
        begin, end = prefills[0]["launch_monotonic_ms"], prefills[-1]["complete_monotonic_ms"]
        step_ms = max(previous["service_elapsed_ms"], following["service_elapsed_ms"])
        if step_ms <= 0:
            raise ValueError("invalid flanking decode cost")
        members = list(retained_members(previous, prefills, following))
        burst = {
            "first_prefill_sample": prefills[0]["sample_id"],
            "launch_ms": begin, "complete_ms": end, "prefill_batches": len(prefills),
            "elapsed_ms": end - begin,
            "worker_sum_ms": sum(row["service_elapsed_ms"] for row in prefills),
            "flanking_decode_worker_proxy_ms": step_ms,
            "retained_decode_members": len(members),
            "prefill_workflows": sorted({
                member["workflow_id"] for row in prefills for member in row["request_samples"]
            }),
        }
        burst_rows.append(burst)
        for member in members:
            rid = member["request_id"]
            request = requests.get(rid)
            if request is None:
                counts["retained_member_without_completed_receipt"] += 1
                continue
            identity = tuple(member[name] for name in (
                "workflow_id", "invocation_id", "context_id", "context_epoch",
            ))
            if (
                identity != request["identity"]
                or member.get("session_id") is not None
                and member["session_id"] != request["session_id"]
                or member.get("session_generation") is not None
                and member["session_generation"] != request["session_generation"]
            ):
                raise ValueError("resident sample differs from completed native receipt")
            emitted = member["output_tokens_after"]
            remaining = request["total_outputs"] - emitted
            if remaining < 1 or request["native_complete_ms"] <= begin:
                raise ValueError("completed request retained as unfinished decode")
            markers = request["marker_positions"]
            case = {
                "first_prefill_sample": burst["first_prefill_sample"],
                "request_id": rid, "workflow_id": member["workflow_id"],
                "critical_path_request": rid in critical,
                "emitted_outputs": emitted, "oracle_remaining_outputs": remaining,
                "full_state_identity_observed": member["full_state_identity_observed"],
                "next_observed_boundary_offline": actions.get(rid),
                "visible_last_outstanding_join_child": last_outstanding_child(
                    member["invocation_id"], begin, joins, returned,
                ),
                "tool_open_marker_already_emitted": (
                    markers["<tool_call>"] is not None and markers["<tool_call>"] <= emitted
                ),
                "tool_close_marker_already_emitted": (
                    markers["</tool_call>"] is not None and markers["</tool_call>"] <= emitted
                ),
                **completion_proxy(begin, end, remaining, step_ms),
            }
            cases.append(case)
    summaries = {}
    for label, predicate in (
        ("all_completed", lambda row: True),
        ("measured_critical_path", lambda row: row["critical_path_request"]),
        ("critical_path_visible_tool_open", lambda row: (
            row["critical_path_request"] and row["tool_open_marker_already_emitted"]
        )),
        ("critical_path_visible_last_child", lambda row: (
            row["critical_path_request"] and row["visible_last_outstanding_join_child"]
        )),
    ):
        population = [row for row in cases if predicate(row)]
        summaries[label] = {
            "observations": len(population),
            "unique_requests": len({row["request_id"] for row in population}),
            "oracle_remaining_outputs": distribution(row["oracle_remaining_outputs"] for row in population),
            "budgets": {},
        }
        for budget_ms in (100., 250., 500., 1000.):
            accepted = [
                row for row in population if row["extra_decode_worker_proxy_ms"] <= budget_ms
                and row["local_burst_exposure_removed_proxy_ms"] > 0
            ]
            best = {}
            for row in accepted:
                rid = row["request_id"]
                best[rid] = max(best.get(rid, 0.), row["local_burst_exposure_removed_proxy_ms"])
            summaries[label]["budgets"][str(int(budget_ms))] = {
                "observations": len(accepted), "unique_requests": len(best),
                "bursts": len({row["first_prefill_sample"] for row in accepted}),
                "deduplicated_local_exposure_proxy_sum_s": sum(best.values()) / 1000.,
                "next_boundary_counts_offline": dict(Counter(
                    row["next_observed_boundary_offline"] for row in accepted
                )),
            }
    return {
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "registration": str(registration), "complete_measured_workflows": report["measured_workflows"],
        "complete_measured_requests": report["measured_requests"], "native_service_samples": len(forwards),
        "closed_prefill_bursts": len(burst_rows),
        "bursts_with_retained_decode": sum(row["retained_decode_members"] > 0 for row in burst_rows),
        "burst_elapsed_ms": distribution(row["elapsed_ms"] for row in burst_rows),
        "retained_decode_burst_elapsed_ms": distribution(
            row["elapsed_ms"] for row in burst_rows if row["retained_decode_members"]
        ),
        "counts": dict(counts), "summaries": summaries, "bursts": burst_rows, "cases": cases,
        "join_completion_skew": join_completion_skew(
            events, forwards, joins, returned, set(report["critical_path"]["workflows"]),
        ),
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            critical_path_report, tokenizer_path, registration, Path(__file__),
            ROOT / "scripts/audit_sealed_wait_prefetch.py",
            ROOT / "scripts/audit_native_system_costs.py",
            ROOT / "scripts/audit_decode_transfer_envelopes.py",
        )},
        "input_jsonl_prefixes": reader.prefixes,
        "scope": [
            "The original fixed cohort and exact complete closed logs are reused without a new GPU run.",
            "Both flanking decodes contain the same request/workflow/invocation/context/epoch and unchanged reported output.",
            "Known attempt/session mismatches are excluded; omitted attempt/session fields stay unknown, not filled from receipts.",
            "Logical continuity screens compute opportunity; it does not establish uninterrupted physical residency or native fit.",
            "Requests prefilling inside the burst are excluded; no shared-prefix or ready-paused feasibility is assumed.",
            "Oracle remaining output and subsequent actions are retrospective opportunity labels, never online policy input.",
            "Already emitted tool markers are token-prefix-visible; the current runtime need not yet publish this hint.",
            "Last-child status uses only an already observed ALL-JOIN wait and other observed returns.",
            "A hypothetical full resident decode block delays all following prefills by its extra worker proxy cost.",
            "The larger flanking worker duration per step is a local proxy, not a calibrated counterfactual GPU cost.",
            "Output emission, changed batching, KV growth, earlier tool effects and downstream critical paths are not simulated.",
            "Per-request maximum local exposure is deduplicated, but correlated sums are not achievable JCT or global upper bounds.",
        ],
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--critical-path-report", type=Path, required=True)
    parser.add_argument("--tokenizer-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use nice19 CPU-only execution")
    result = inspect(args.critical_path_report, args.tokenizer_config)
    write(args.output, result)
    print(json.dumps({
        name: value for name, value in result.items()
        if name not in (
            "bursts", "cases", "join_completion_skew", "inputs_sha256", "input_jsonl_prefixes", "scope",
        )
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
