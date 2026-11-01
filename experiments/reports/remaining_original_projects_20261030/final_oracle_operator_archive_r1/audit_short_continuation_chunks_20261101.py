"""Measure waiting overlap without inferring past residency from a later hit."""

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import sys

ROOT = Path("/home/longhao/experiment/BeliefKV")
OPS = ROOT.parent / "ops"
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
OUTPUT = REPORTS / "short_continuation_chunk_opportunity_r1"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_decode_transfer_envelopes import EnvelopeIndex
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def inspect(registration, cohort, critical_path):
    verify_execution(registration, cohort)
    plan = causal.load(registration)
    arm = Path(plan["run_root"]) / "full"
    paths = causal.load(critical_path)["critical_path"]["workflows"]
    critical = {
        rid: workflow for workflow, path in paths.items()
        for rid in path["critical_path_requests"]
    }
    reader = SealedRecords()
    requests = {
        row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")
    }
    invocations = defaultdict(list)
    for request in requests.values():
        invocations[tuple(request["native_receipt"]["identity"][:3])].append(request)
    continuations = set()
    for members in invocations.values():
        members.sort(key=lambda row: row["submitted_monotonic_ms"])
        for before, after in zip(members, members[1:]):
            if before["native_complete_monotonic_ms"] > after["submitted_monotonic_ms"]:
                raise ValueError("a serial continuation precedes its actual predecessor")
            continuations.add(after["request_id"])
    first, runs, current = {}, [], []

    def finish():
        if len(current) >= 2 and sum(row["input_tokens"] for row in current) >= 4096:
            runs.append({
                "request_id": current[0]["request_id"], "batches": len(current),
                "intervals": [(row["begin"], row["end"]) for row in current],
                "input_tokens": sum(row["input_tokens"] for row in current),
            })
        current.clear()

    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        members = row["request_samples"]
        for member in members:
            rid = member["request_id"]
            if rid in requests and rid not in first:
                first[rid] = {
                    "launch_ms": row["launch_monotonic_ms"], "member": member,
                }
        if row["phase"] != "prefill" or len(members) != 1:
            finish()
            continue
        member = members[0]
        rid = member["request_id"]
        if current and current[-1]["request_id"] != rid:
            finish()
        current.append({
            "request_id": rid, "input_tokens": member["token_delta"],
            "begin": row["service_start_monotonic_ms"],
            "end": row["complete_monotonic_ms"],
        })
    finish()
    chunk_spans = EnvelopeIndex(span for run in runs for span in run["intervals"])
    # These points prove observed logical readiness at that instant only.
    active_ready = defaultdict(list)
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") != "agent_periodic_admission_decision":
            continue
        for member in row["programs"]:
            key = member.get("key") or {}
            if (
                member["application_ready"] and member["pending_before"]
                and member["active_before"] and member["active_after_pressure"]
                and key.get("request_id") in critical
            ):
                active_ready[key["request_id"]].append(row["monotonic_s"] * 1000.)
    rows, unknown = [], []
    for rid, workflow in critical.items():
        if rid not in continuations:
            continue
        request, observed = requests[rid], first.get(rid)
        if observed is None:
            unknown.append(rid)
            continue
        member = observed["member"]
        if not (
            member["phase"] == "prefill" and 0 < member["token_delta"] <= 256
            and member["sequence_tokens_before"] > 0
            and member["prefill_geometry_source"] in ("native_batch", "native_request_range")
            and member["sequence_tokens_before"] + member["token_delta"] == request["prompt_tokens"]
        ):
            continue
        begin, end = request["submitted_monotonic_ms"], observed["launch_ms"]
        overlap = chunk_spans.overlap_ms(begin, end)
        if overlap <= 0:
            continue
        points = active_ready.get(rid, [])
        left, right = bisect_left(points, begin), bisect_left(points, end)
        during = points[left:right]
        logical_inside_chunk = [
            point for point in during if chunk_spans.overlap_ms(point, point + .001) > 0
        ]
        rows.append({
            "request_id": rid, "workflow_id": workflow,
            "incremental_tokens_at_first_service": member["token_delta"],
            "prefix_tokens_at_first_service": member["sequence_tokens_before"],
            "submit_to_first_launch_ms": end - begin,
            "same_owner_multi_chunk_worker_overlap_ms": overlap,
            "active_ready_periodic_points_during_wait": len(during),
            "active_ready_periodic_points_inside_chunk_worker": len(logical_inside_chunk),
            "physical_residency_during_wait": "unknown",
        })
    workflow_exposure = {
        workflow: sum(
            row["same_owner_multi_chunk_worker_overlap_ms"]
            for row in rows if row["workflow_id"] == workflow
        ) for workflow in paths
    }
    logical_exposure = {
        workflow: sum(
            row["same_owner_multi_chunk_worker_overlap_ms"]
            for row in rows if row["workflow_id"] == workflow
            and row["active_ready_periodic_points_inside_chunk_worker"] > 0
        ) for workflow in paths
    }
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed source changed during read-only analysis")
    return {
        "registration": str(registration), "complete_measured_execution_verified": True,
        "same_owner_multi_chunk_runs": len(runs),
        "short_critical_continuations_overlapping_runs": len(rows),
        "with_observed_active_ready_point_inside_chunk": sum(
            row["active_ready_periodic_points_inside_chunk_worker"] > 0 for row in rows
        ),
        "missing_first_service_evidence": unknown,
        "workflow_equal_weight_mean_overlap_s": statistics.mean(workflow_exposure.values()) / 1000.,
        "workflow_mean_overlap_for_requests_with_some_logical_ready_evidence_s": (
            statistics.mean(logical_exposure.values()) / 1000.
        ),
        "waiting_worker_overlap_ms": distribution(
            row["same_owner_multi_chunk_worker_overlap_ms"] for row in rows
        ),
        "rows": rows, "workflow_overlap_ms": workflow_exposure,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            registration, cohort, critical_path, Path(__file__),
        )},
        "scope": [
            "Only complete fixed-cohort critical-path same-invocation continuations are included.",
            "A short continuation fits its observed remaining prompt in at most256 tokens.",
            "A run is at least two consecutive singleton prefills of one owner totalling4096 inputs.",
            "First-service cache geometry does not prove earlier physical HBM residency.",
            "Periodic observations prove logical readiness only at recorded instants.",
            "Overlap is optimistic mechanism exposure, not removable JCT or a global scheduling bound.",
            "No cost of added short prefills, decode or delayed competing work has been deducted.",
            "No candidate is admitted, prefetched or granted capacity by this analysis.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }


def main():
    if (
        OUTPUT.exists()
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve reports and use nice19 hidden-CUDA analysis")
    definitions = {
        "best_original56": (
            OPS / "workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json",
            REPORTS / "sustained56_initial12_mean70_r1/analysis_cohort.json",
            REPORTS / "measured_prefill_opportunity_complete_controls_r1/best_original56.json",
        ),
        "final_original82": (
            OPS / "workflow_final82_initial12_mean70_predictive_control_20261031_r1.registration.json",
            REPORTS / "final_oracle_original82_traffic_r1/analysis_cohort.json",
            REPORTS / "final82_complete_control_opportunities_r1/critical_path.json",
        ),
    }
    for label, values in definitions.items():
        result = inspect(*values)
        write(OUTPUT / (label + ".json"), result)
        print(json.dumps({
            "label": label, **{key: value for key, value in result.items()
                              if key not in ("rows", "input_jsonl_prefixes", "inputs_sha256", "scope",
                                             "workflow_overlap_ms", "registration")},
        }), flush=True)
    write(OUTPUT / "audit.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "analyzer_sha256": sha256_file(Path(__file__)),
        "outputs_sha256": {
            label: sha256_file(OUTPUT / (label + ".json")) for label in definitions
        },
        "GPU_action": False, "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
