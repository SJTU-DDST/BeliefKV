"""Partition historical outside-member intervals by other native worker phases."""

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
HISTORY = ROOT / "experiments/reports/tool_offload_before_admission_20261027"
sys.path.insert(0, str(HISTORY))
sys.path.insert(0, str(ROOT))

from control_regression_audit import SOURCES, load, require, sealed_rows
from scripts.audit_decode_transfer_envelopes import EnvelopeIndex


FIELDS = (
    "other_prefill_only_ms", "other_decode_only_ms", "other_cross_phase_ms",
    "outside_all_workers_ms",
)


def partition(begin, end, own, phases):
    own = EnvelopeIndex((max(begin, left), min(end, right))
                        for left, right in own if left < end and right > begin)
    gaps, cursor = [], begin
    for left, right in own.spans:
        if cursor < left:
            gaps.append((cursor, left))
        cursor = right
    if cursor < end:
        gaps.append((cursor, end))
    indexes = phases if "all" in phases else {
        **phases, "all": EnvelopeIndex(
            span for index in phases.values() for span in index.spans
        ),
    }
    result = dict.fromkeys(FIELDS, 0.)
    for left, right in gaps:
        prefill = indexes["prefill"].overlap_ms(left, right)
        decode = indexes["decode"].overlap_ms(left, right)
        covered = indexes["all"].overlap_ms(left, right)
        values = (covered - decode, covered - prefill,
                  prefill + decode - covered, right - left - covered)
        require(all(value >= -.001 for value in values), "invalid phase intersection")
        for field, value in zip(FIELDS, values):
            result[field] += max(0., value)
    require(math.isclose(
        math.fsum(result.values()), end - begin - own.prefix_ms[-1], abs_tol=.001,
    ), "outside-member phase partition does not reconstruct lifetime")
    return result


def audit(service, paths):
    arm = Path(service["arm"])
    critical = [
        rid for workflow in paths["workflows"].values()
        for rid in workflow["critical_path_requests"]
    ]
    require(len(critical) == len(set(critical)), "duplicate critical request")
    selected = set(critical)
    spans, own, first = defaultdict(list), defaultdict(list), {}
    clock = service["work"]["clock"]["monotonic_clock_domain"]
    samples = 0
    raw = arm / "server/runtime_audit.jsonl"
    for row in sealed_rows(raw, service["input_jsonl_prefixes"][str(raw)],
                           (b'"gpu_service_sample"',)):
        samples += 1
        require(row["sample_id"] == f"native-service-{samples:09d}", "missing native sample")
        require(row["monotonic_clock_domain"] == clock, "worker clock changed")
        phase = row["phase"]
        require(phase in ("prefill", "decode"), "unknown worker phase")
        span = row["service_start_monotonic_ms"], row["complete_monotonic_ms"]
        spans[phase].append(span)
        for member in row["request_samples"]:
            rid = member["request_id"]
            require(rid in service["requests"], "unknown native worker request")
            if rid in selected:
                own[rid].append(span)
                first[rid] = min(first.get(rid, math.inf), row["launch_monotonic_ms"])
    phases = {name: EnvelopeIndex(spans[name]) for name in ("prefill", "decode")}
    phases["all"] = EnvelopeIndex(span for index in tuple(phases.values()) for span in index.spans)
    rows = {}
    receipts = arm / "replay/native_requests.jsonl"
    for receipt in sealed_rows(receipts, service["input_jsonl_prefixes"][str(receipts)],
                               (b'"native_receipt"',)):
        rid = receipt["request_id"]
        if rid not in selected:
            continue
        require(rid not in rows and rid in first, "missing or duplicate request")
        require(receipt["native_receipt"]["clock_domain"] == clock, "receipt clock changed")
        end, begin = receipt["native_complete_monotonic_ms"], first[rid]
        request = service["requests"][rid]
        require(receipt["exact_tokens_verified"] is True, "unverified request")
        require(math.isclose(
            end - begin, request["first_launch_to_complete_ms"], abs_tol=.001,
        ), "request lifetime changed")
        parts = partition(begin, end, own[rid], phases)
        require(math.isclose(
            math.fsum(parts.values()), request["outside_member_worker_intervals_ms"],
            abs_tol=.001,
        ), "phase gap does not match complete request-service evidence")
        rows[rid] = {"cohort": request["cohort"], **parts}
    require(rows.keys() == selected, "critical request coverage is incomplete")
    populations = {
        "all_critical_requests": critical,
        "critical_tool_successors": [
            rid for rid in critical if rows[rid]["cohort"] == "post_tool"
        ],
    }
    return {
        "arm": str(arm), "native_worker_samples": samples,
        "critical_partitions": {
            name: {
                "requests": len(ids),
                "workflow_equal_weight_s": {
                    field.removesuffix("_ms") + "_s": math.fsum(
                        rows[rid][field] for rid in ids
                    ) / len(paths["workflows"]) / 1000. for field in FIELDS
                },
            } for name, ids in populations.items()
        },
    }


def main():
    output = Path(__file__).with_suffix(".json")
    require(not output.exists(), "preserve original phase audit")
    phases = {"prefill": EnvelopeIndex([(0., 6.)]), "decode": EnvelopeIndex([(4., 11.)])}
    require(partition(0., 12., [(2., 4.), (9., 10.)], phases) == dict(zip(
        FIELDS, (2., 4., 2., 1.),
    )), "overlapping phase fixture failed")
    require(not any(partition(0., 12., [(0., 12.)], phases).values()),
            "fully covered own-worker fixture failed")
    require(partition(0., 12., [(2., 4.), (9., 10.)], {
        "prefill": EnvelopeIndex([]), "decode": EnvelopeIndex([]),
    }) == dict(zip(FIELDS, (0., 0., 0., 9.))), "empty other-worker fixture failed")
    reference = load(HISTORY / "control_regression_audit.json")
    require(reference["same_exact_work_and_same_critical_request_chains_verified"],
            "historical work or critical chains changed")
    comparison = load(HISTORY / "recent_comparison.json")
    arms = {}
    for name, service_file, path_file, key in (
        ("before", "recent_preadmission_service_interleaving.json", "complete_jct_paths.json", "Full"),
        ("grouped", "recent_grouped_and_curve_service_interleaving.json",
         "recent_grouped_and_curve_jct_paths.json", "GroupedFull"),
    ):
        evidence = []
        for filename in (service_file, path_file):
            path = HISTORY / filename
            evidence.append(load(path, comparison["inputs_sha256"][str(path)]))
        arms[name] = audit(evidence[0]["arms"][key], evidence[1]["arms"][key])
    deltas = {
        name: {
            key: arms["grouped"]["critical_partitions"][name]["workflow_equal_weight_s"][key] - value
            for key, value in old["workflow_equal_weight_s"].items()
        } for name, old in arms["before"]["critical_partitions"].items()
    }
    for name, values in deltas.items():
        require(math.isclose(
            math.fsum(values.values()),
            reference["critical_delta_workflow_equal_weight_s"][name]["outside_member_worker_intervals_s"],
            abs_tol=1e-6,
        ), "phase delta does not reproduce the original complete audit")
    sources = (Path(__file__), HISTORY / "control_regression_audit.py",
               ROOT / "scripts/audit_decode_transfer_envelopes.py")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete historical outside-member worker-phase attribution.",
        "arms": arms, "critical_delta_workflow_equal_weight_s": deltas,
        "interval_fixtures_verified": 3,
        "sources_sha256": {
            **SOURCES,
            **{str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
        },
        "gpu_experiment_started": False, "production_code_changed": False,
        "performance_accepted": False, "isolated_control_cost_attribution": False,
        "limitations": [
            "Every original critical request and normal workflow tail remains included.",
            "Exclusive categories describe recorded worker intervals, not CUDA kernel occupancy.",
            "Cross-phase coverage is counted once; runtime and transfer waits can occur in every phase.",
            "An absent request member does not establish preemption, a priority error or a removable delay.",
            "This historical comparison cannot attribute the effects of current implementation repairs.",
        ],
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"critical_deltas_s": deltas}, indent=2))


if __name__ == "__main__":
    main()
