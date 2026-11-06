"""Screen workflow-size choices before prefill, without claiming physical fit."""

from bisect import bisect_left, bisect_right
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
from beliefkv.runtime.workflow_work_oracle import (
    WorkflowWorkOracleArtifact, cache_remaining_paths,
)
from scripts.audit_causal_jct_paths import first_launches, request_cohorts
from scripts.audit_native_system_costs import distribution
from scripts.audit_periodic_admission_decisions import PublishedGrantInterventions
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


class ObservedWork:
    """Future labels with only progress completed before the inspected boundary."""

    def __init__(self, artifact):
        self.artifact = artifact
        self.remaining = {
            workflow: item["total_work_units"]
            for workflow, item in artifact.workflows.items()
        }
        self.done, self.outputs, self.completed, self.cache = {}, {}, set(), {}
        self.event_nodes, self.request_nodes = {}, {}
        for workflow, graph in artifact.graphs.items():
            for node in graph:
                if node["request_id"] is not None:
                    self.request_nodes[node["request_id"]] = node["node_id"]
                else:
                    self.event_nodes[node["event_id"]] = workflow, node["node_id"]

    def event(self, identity, when):
        found = self.event_nodes.get(identity)
        if found is not None:
            workflow, node = found
            self.done[node] = when / 1000.
            self.cache.pop(workflow, None)

    def progress(self, request, outputs):
        item = self.artifact.requests[request]
        if type(outputs) is not int or not 0 <= outputs <= item["output_tokens"]:
            raise ValueError("observed native output exceeds the bound logical request")
        if request in self.completed:
            return
        previous = self.outputs.get(request, 0)
        if outputs < previous:
            raise ValueError("logical output progress moved backwards")
        if outputs != previous:
            self.remaining[item["workflow_id"]] -= 100 * (outputs - previous)
            self.outputs[request] = outputs
            self.cache.pop(item["workflow_id"], None)

    def complete(self, request, when):
        if request in self.completed:
            raise ValueError("duplicate exact native completion")
        item = self.artifact.requests[request]
        self.progress(request, item["output_tokens"])
        self.remaining[item["workflow_id"]] -= item["input_increment"]
        if self.remaining[item["workflow_id"]] < 0:
            raise ValueError("observed work exceeds the complete workflow label")
        self.completed.add(request)
        self.outputs.pop(request, None)
        self.done[self.request_nodes[request]] = when / 1000.
        self.cache.pop(item["workflow_id"], None)

    def score(self, workflow, when):
        if workflow not in self.cache:
            self.cache[workflow] = cache_remaining_paths(
                self.artifact.graphs[workflow], self.artifact.requests,
                self.done, self.outputs, self.artifact.model_seconds_per_work_token,
                prefill_seconds_per_token=self.artifact.model_prefill_seconds_per_token,
                prefill_done_after_output=(
                    self.artifact.model_prefill_progress == "first_observed_output"
                ),
            )
        path, model, external = self.cache[workflow].at(when / 1000.)
        return {
            "model_work_tokens": self.remaining[workflow] / 100.,
            "model_tool_path_s": path,
            "model_only_path_s": model,
            "external_delay_path_s": external,
        }


def observed_eligibility(request, row, snapshot_ms, now_ms, interventions, *, maximum_age_ms):
    if row is None:
        return "no_preceding_exact_request_state"
    if not 0 <= now_ms - snapshot_ms <= maximum_age_ms:
        return "preceding_state_too_old"
    key = row.get("key") or {}
    receipt = request["native_receipt"]
    expected = (*receipt["identity"], receipt["session_id"], receipt["session_generation"])
    actual = tuple(key.get(name) for name in (
        "root_workflow_id", "invocation_id", "context_id", "context_epoch",
        "session_id", "session_generation",
    ))
    if key.get("request_id") != request["request_id"] or actual != expected:
        return "preceding_request_identity_changed"
    if not row["application_ready"] or not row["pending_before"]:
        return "not_observed_application_ready"
    if not row["active_after_pressure"] or row["marked_after_pressure"]:
        return "not_observed_active_after_pressure"
    points = interventions.get(key["invocation_id"], ())
    index = bisect_right(points, (now_ms, math.inf)) - 1
    if index >= 0 and points[index][0] > snapshot_ms:
        return "intervening_admission_change"
    return "recent_observed_active_ready"


def rank_waiting(owner, candidates, work, now_ms, *, field, minimum_ratio):
    owner_score = work.score(owner, now_ms)
    ordered = []
    for request_id, workflow in candidates:
        if workflow != owner:
            score = work.score(workflow, now_ms)
            if score[field] * minimum_ratio <= owner_score[field] and score[field] < owner_score[field]:
                ordered.append((score[field], request_id, workflow, score))
    if not ordered:
        return None
    _value, request_id, workflow, score = min(ordered, key=lambda item: item[:2])
    return {
        "request_id": request_id, "workflow_id": workflow,
        "candidate_score": score, "executing_owner_score": owner_score,
    }


def unchanged_cadence_return(prefill_complete, launch_ms, output_tokens, decode_launches, decode_completes):
    if type(output_tokens) is not int or output_tokens < 1:
        raise ValueError("a complete positive output length is required")
    if output_tokens == 1:
        return prefill_complete
    index = bisect_right(decode_launches, launch_ms) + output_tokens - 2
    return decode_completes[index] if index < len(decode_completes) else None


def summarize(rows, measured, critical):
    by_request = {}
    for row in rows:
        previous = by_request.get(row["request_id"])
        if previous is None or row["remaining_first_service_wait_ms"] > previous["remaining_first_service_wait_ms"]:
            by_request[row["request_id"]] = row
    critical_rows = [row for row in by_request.values() if row["request_id"] in critical]
    return {
        "repeated_prefill_boundary_choices": len(rows),
        "distinct_waiting_requests": len(by_request),
        "distinct_waiting_workflows": len({row["workflow_id"] for row in rows}),
        "distinct_measured_workflows": len({
            row["workflow_id"] for row in rows if row["workflow_id"] in measured
        }),
        "distinct_measured_critical_requests": len(critical_rows),
        "remaining_first_service_wait_ms": distribution(
            row["remaining_first_service_wait_ms"] for row in by_request.values()
        ),
        "measured_critical_first_service_wait_ms": distribution(
            row["remaining_first_service_wait_ms"] for row in critical_rows
        ),
        "unique_request_optimistic_advance_ms": distribution(
            row["unchanged_cadence_advance_ms"] for row in by_request.values()
            if row["unchanged_cadence_advance_ms"] is not None
        ),
        "measured_critical_optimistic_advance_ms": distribution(
            row["unchanged_cadence_advance_ms"] for row in critical_rows
            if row["unchanged_cadence_advance_ms"] is not None
        ),
        "future_first_prefill_extend_le_512_requests": sum(
            row["future_first_prefill_extend_tokens"] <= 512 for row in by_request.values()
        ),
    }


def inspect(registration_path, completed_review_path, path_report_path, *, maximum_age_ms, minimum_ratio):
    registration = json.loads(registration_path.read_bytes())
    review = json.loads(completed_review_path.read_bytes())
    if review.get("status") != "complete_Full_independently_verified":
        raise ValueError("require an independently completed Full execution")
    for filename, digest in review["inputs_sha256"].items():
        if sha256_file(Path(filename)) != digest:
            raise ValueError("the independently completed execution evidence changed")
    if review["inputs_sha256"].get(str(registration_path.resolve())) != sha256_file(registration_path):
        raise ValueError("completed review belongs to another registration")
    path_report = json.loads(path_report_path.read_bytes())
    if path_report["registration"] != str(registration_path.resolve()):
        raise ValueError("critical-path report belongs to another execution")
    for filename, digest in path_report["inputs_sha256"].items():
        if sha256_file(Path(filename)) != digest:
            raise ValueError("the complete critical-path evidence changed")
    common = registration["reference_environment"]
    artifact_path = Path(common["WORKFLOW_WORK_MODEL"])
    artifact = WorkflowWorkOracleArtifact.load(artifact_path, common["WORKFLOW_WORK_MODEL_SHA256"])
    arm, reader = Path(registration["run_root"]) / "full", SealedRecords()
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    cohorts = request_cohorts(events)
    requests = {row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")}
    if any(
        rid not in artifact.requests or row.get("exact_tokens_verified") is not True
        or (row["prompt_tokens"], row["output_tokens"]) != (
            artifact.requests[rid]["prompt_tokens"], artifact.requests[rid]["output_tokens"],
        )
        for rid, row in requests.items()
    ):
        raise ValueError("complete native requests differ from the fixed oracle labels")
    timeline = [(row["ts_ms"], 0, row["event_id"]) for row in events]
    interventions = defaultdict(list)
    tracker = PublishedGrantInterventions()
    for sequence, row in enumerate(reader(arm / "opportunities/admission_opportunities.jsonl")):
        tracker.observe(row, sequence, interventions)
        if row.get("event") == "agent_periodic_admission_decision":
            timeline.append((row["monotonic_s"] * 1000., 3, {
                item["key"]["request_id"]: item for item in row["programs"] if item.get("key")
            }))
    for points in interventions.values():
        points.sort()
    first_prefill, queries, decode_launches, decode_completes = {}, [], [], []
    forward_count = 0
    reported_outputs = {}

    def samples():
        nonlocal forward_count
        for row in reader(arm / "server/runtime_audit.jsonl"):
            if row.get("event") != "gpu_service_sample":
                continue
            forward_count += 1
            if row["phase"] not in ("prefill", "decode"):
                raise ValueError("the prefill screen does not support mixed forwards")
            if row["phase"] == "decode":
                decode_launches.append(row["launch_monotonic_ms"])
                decode_completes.append(row["complete_monotonic_ms"])
            else:
                query = {
                    "launch_ms": row["launch_monotonic_ms"],
                    "complete_ms": row["complete_monotonic_ms"],
                    "worker_ms": row["service_elapsed_ms"],
                    "members": row["request_samples"],
                }
                queries.append(query)
                timeline.append((query["launch_ms"], 6, query))
            for member in row["request_samples"]:
                rid = member["request_id"]
                if rid not in artifact.requests:
                    raise ValueError("native service contains a request outside the source")
                if row["phase"] == "prefill" and rid not in first_prefill:
                    first_prefill[rid] = member
                output = member.get("output_tokens_after")
                if type(output) is not int or output < 0:
                    raise ValueError("native service lacks observed cumulative output")
                # Match the runtime's bounded output observation; completion is exact.
                if output - reported_outputs.get(rid, 0) >= 128:
                    reported_outputs[rid] = output
                    timeline.append((row["complete_monotonic_ms"], 1, (rid, output)))
            yield row

    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_bytes())
    expected_count = status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
    first = first_launches(samples(), requests, expected_records=expected_count)
    if any(left > right for left, right in zip(decode_launches, decode_launches[1:])):
        raise ValueError("native decode launches are not monotonic")
    for rid, request in requests.items():
        timeline.extend((
            (request["submitted_monotonic_ms"], 4, rid),
            (first[rid], 5, rid),
            (request["native_complete_monotonic_ms"], 2, rid),
        ))
    measured = set(path_report["critical_path"]["workflows"])
    critical = {
        rid for row in path_report["critical_path"]["workflows"].values()
        for rid in row["critical_path_requests"]
    }
    origin = min(row["ts_ms"] for row in events if row["kind"] == "workflow_start")
    cohort = json.loads(Path(registration["analysis_cohort"]).read_bytes()) if (
        "analysis_cohort" in registration
    ) else None
    if cohort is None:
        cohort = next(
            json.loads(Path(filename).read_bytes()) for filename in review["inputs_sha256"]
            if filename.endswith("/analysis_cohort.json")
        )
    lo, hi = cohort["throughput_window_offset_seconds"]
    work, waiting, state, state_ms = ObservedWork(artifact), set(), {}, -math.inf
    counts, reasons = Counter(), Counter()
    choices = {"model_work_tokens": [], "model_tool_path_s": []}
    for when, action, value in sorted(timeline, key=lambda item: item[:2]):
        if action == 0:
            work.event(value, when)
        elif action == 1:
            work.progress(*value)
        elif action == 2:
            work.complete(value, when)
        elif action == 3:
            state, state_ms = value, when
        elif action == 4:
            waiting.add(value)
        elif action == 5:
            waiting.discard(value)
        else:
            counts["prefill_boundaries"] += 1
            if len(value["members"]) != 1:
                counts["multi_member_prefill_boundaries"] += 1
                continue
            member = value["members"][0]
            if member["output_tokens_after"] or cohorts.get(member["request_id"]) == "post_join":
                counts["final_or_post_join_prefill_boundaries_excluded"] += 1
                continue
            counts["single_owner_mid_chunk_boundaries"] += 1
            counts["boundaries_with_waiting_completed_requests"] += bool(waiting)
            candidates = []
            for rid in sorted(waiting):
                request = requests[rid]
                reason = observed_eligibility(
                    request, state.get(rid), state_ms, when, interventions,
                    maximum_age_ms=maximum_age_ms,
                )
                reasons[reason] += 1
                if reason == "recent_observed_active_ready" and cohorts.get(rid) != "post_join":
                    candidates.append((rid, artifact.requests[rid]["workflow_id"]))
            counts["boundaries_with_recent_active_ready_candidates"] += bool(candidates)
            for field in choices:
                selected = rank_waiting(
                    member["workflow_id"], candidates, work, when,
                    field=field, minimum_ratio=minimum_ratio,
                )
                if selected is None:
                    continue
                rid = selected["request_id"]
                optimistic = unchanged_cadence_return(
                    value["complete_ms"], when, requests[rid]["output_tokens"],
                    decode_launches, decode_completes,
                )
                choices[field].append({
                    **selected, "executing_request_id": member["request_id"],
                    "executing_workflow_id": member["workflow_id"],
                    "boundary_ms": when, "boundary_offset_s": (when - origin) / 1000.,
                    "in_common_window": lo <= (when - origin) / 1000. < hi,
                    "preceding_logical_state_age_ms": when - state_ms,
                    "remaining_first_service_wait_ms": first[rid] - when,
                    "unchanged_cadence_advance_ms": (
                        max(0., requests[rid]["native_complete_monotonic_ms"] - optimistic)
                        if optimistic is not None else None
                    ),
                    "future_first_prefill_extend_tokens": first_prefill[rid]["token_delta"],
                    "future_first_prefill_prefix_tokens": first_prefill[rid]["sequence_tokens_before"],
                    "necessary_input_increment_tokens": artifact.requests[rid]["input_increment"],
                    "output_tokens": requests[rid]["output_tokens"],
                    "earlier_physical_fit_proven": False,
                    "prefill_and_extra_decode_cost_charged": False,
                })
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("a completed raw input changed during this read-only screen")
    bound = (
        registration_path, completed_review_path, path_report_path, artifact_path, status_path,
        Path(__file__), ROOT / "beliefkv/runtime/workflow_work_oracle.py",
        ROOT / "scripts/audit_causal_jct_paths.py",
        ROOT / "scripts/audit_periodic_admission_decisions.py",
        ROOT / "scripts/audit_sealed_wait_prefetch.py",
    )
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(registration_path.resolve()),
        "maximum_preceding_eligibility_age_ms": maximum_age_ms,
        "minimum_owner_to_candidate_remaining_work_ratio": minimum_ratio,
        "native_forwards_checked": forward_count, "counts": dict(counts),
        "preceding_eligibility_observations": dict(reasons),
        "population_completed_requests": len(requests),
        "source_requests_without_complete_native_receipts": len(artifact.requests) - len(requests),
        "summary": {
            field: {
                "whole_execution": summarize(rows, measured, critical),
                "common_window": summarize(
                    [row for row in rows if row["in_common_window"]], measured, critical,
                ),
            } for field, rows in choices.items()
        },
        "choices": choices, "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in bound},
        "scope": [
            "No workload, source task, tool time, arrival, policy or GPU process was changed.",
            "Rank all observed candidates without measured-cohort or completion-order preference.",
            "Future model/tool labels use only native completions, 128-output progress and past events.",
            "Eligibility is a recent preceding exact logical observation with no recorded intervening grant/pause.",
            "A preceding active observation is not a current physical prefix, slot, lock or allocation proof.",
            "Only exact completed requests have observed submit times; unfinished background is not treated as absent work.",
            "Exclude post-JOIN owners and contenders to avoid counting an existing protected priority as a new size choice.",
            "Small input at later actual prefill does not prove the prefix was resident at the earlier boundary.",
            "The optimistic return adds a free prefill token and free membership in subsequent existing decode batches.",
            "Displaced chunks, extra decode cost, changed dependencies and subsequent scheduling are not simulated.",
            "Repeated/local waiting or advance sums are neither achievable mean JCT nor a global upper bound.",
        ],
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "completed-review", "critical-path-report", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--maximum-age-ms", type=float, default=2500.)
    parser.add_argument("--minimum-ratio", type=float, default=2.)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
        or not math.isfinite(args.maximum_age_ms) or args.maximum_age_ms <= 0
        or not math.isfinite(args.minimum_ratio) or args.minimum_ratio <= 1
    ):
        parser.error("preserve attempts and use nice19 CPU-only analysis with finite positive limits")
    result = inspect(
        args.registration, args.completed_review, args.critical_path_report,
        maximum_age_ms=args.maximum_age_ms, minimum_ratio=args.minimum_ratio,
    )
    write(args.output, result)
    print(json.dumps({
        "counts": result["counts"], "summary": result["summary"], "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
