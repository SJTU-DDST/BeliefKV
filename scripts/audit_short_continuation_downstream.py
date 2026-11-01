"""Check whether earlier short prefill can reach completion under actual cadence."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def earliest_completion(early_prefill_index, output_tokens, forwards):
    if type(output_tokens) is not int or output_tokens < 1:
        raise ValueError("require a real positive complete output")
    prefill = forwards[early_prefill_index]
    if prefill["phase"] != "prefill":
        raise ValueError("early admission must occur in a prefill forward")
    if output_tokens == 1:
        return prefill["complete_ms"], 0
    decoded = 0
    for row in forwards[early_prefill_index + 1:]:
        if row["phase"] == "decode":
            decoded += 1
            if decoded + 1 == output_tokens:
                return row["complete_ms"], decoded
    return None, decoded


def inspect(registration, short_path):
    plan = json.loads(registration.read_bytes())
    previous = json.loads(short_path.read_bytes())
    if previous["registration"] != str(registration):
        raise ValueError("short-continuation audit belongs to another registration")
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    requests = {row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")}
    targets = {row["request_id"]: row for row in previous["rows"]}
    ready = defaultdict(list)
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") != "agent_periodic_admission_decision":
            continue
        for item in row["programs"]:
            rid = (item.get("key") or {}).get("request_id")
            if (
                rid in targets and item["application_ready"] and item["pending_before"]
                and item["active_before"] and item["active_after_pressure"]
            ):
                ready[rid].append(row["monotonic_s"] * 1000.)
    forwards, first, current, chunk_indices = [], {}, [], set()

    def finish_run():
        if len(current) >= 2 and sum(forwards[index]["input_tokens"] for index in current) >= 4096:
            chunk_indices.update(current)
        current.clear()

    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        if row["phase"] not in ("prefill", "decode"):
            raise ValueError("downstream screen requires non-mixed selected cadence")
        index = len(forwards)
        members = row["request_samples"]
        forwards.append({
            "phase": row["phase"], "launch_ms": row["launch_monotonic_ms"],
            "complete_ms": row["complete_monotonic_ms"],
            "request_id": members[0]["request_id"] if len(members) == 1 else None,
            "input_tokens": sum(member["token_delta"] for member in members) if row["phase"] == "prefill" else 0,
        })
        for member in members:
            rid = member["request_id"]
            if rid in targets:
                first.setdefault(rid, (index, member))
        if row["phase"] != "prefill" or len(members) != 1:
            finish_run()
        else:
            if current and forwards[current[-1]]["request_id"] != members[0]["request_id"]:
                finish_run()
            current.append(index)
    finish_run()
    starts = [row["launch_ms"] for row in forwards]
    if any(left > right for left, right in zip(starts, starts[1:])):
        raise ValueError("native forwards are not monotone")
    episodes = []
    for rid, target in targets.items():
        request = requests[rid]
        original_index, sample = first[rid]
        original = forwards[original_index]
        if sample["phase"] != "prefill" or original["launch_ms"] < request["submitted_monotonic_ms"]:
            raise ValueError("target first service differs from the observed short continuation")
        points = [
            point for point in ready[rid]
            if request["submitted_monotonic_ms"] <= point < original["launch_ms"]
        ]
        if not points:
            continue
        candidate = None
        for index in range(bisect_left(starts, points[0]), original_index):
            if index in chunk_indices and forwards[index]["request_id"] != rid:
                candidate = index
                break
        if candidate is None:
            continue
        when, outputs = earliest_completion(candidate, request["output_tokens"], forwards)
        before = request["native_complete_monotonic_ms"]
        next_decode = next(
            (row["launch_ms"] for row in forwards[candidate + 1:] if row["phase"] == "decode"), None,
        )
        episodes.append({
            "request_id": rid, "workflow_id": target["workflow_id"],
            "original_first_service_ms": original["launch_ms"],
            "original_complete_ms": before, "early_prefill_launch_ms": forwards[candidate]["launch_ms"],
            "true_output_tokens": request["output_tokens"],
            "first_actual_decode_after_early_prefill_ms": next_decode,
            "decode_can_start_before_original_prefill": bool(
                next_decode is not None and next_decode < original["launch_ms"]
            ),
            "optimistic_complete_at_unchanged_decode_cadence_ms": when,
            "optimistic_completion_advance_ms": max(0., before - when) if when is not None else None,
            "prefill_advance_ms": original["launch_ms"] - forwards[candidate]["launch_ms"],
            "decode_steps_available_in_observed_tail": outputs,
            "physical_prefix_ready_at_early_boundary": "unknown",
            "extra_prefill_decode_cost_charged": False,
        })
    workflows = {
        workflow: sum(
            row["optimistic_completion_advance_ms"] or 0. for row in episodes
            if row["workflow_id"] == workflow
        ) for workflow in previous["workflow_overlap_ms"]
    }
    for filename, prefix in reader.prefixes.items():
        if (
            Path(filename).stat().st_size != prefix["complete_prefix_bytes"]
            or sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed downstream inputs changed")
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "old_short_continuation_count": len(targets), "logical_ready_boundary_episodes": len(episodes),
        "single_token_outputs": sum(row["true_output_tokens"] == 1 for row in episodes),
        "decode_before_original_prefill": sum(row["decode_can_start_before_original_prefill"] for row in episodes),
        "output_tokens": distribution(row["true_output_tokens"] for row in episodes),
        "prefill_advance_ms": distribution(row["prefill_advance_ms"] for row in episodes),
        "optimistic_unchanged_cadence_completion_advance_ms": distribution(
            row["optimistic_completion_advance_ms"] for row in episodes
            if row["optimistic_completion_advance_ms"] is not None
        ),
        "summed_local_advances_per_measured_workflow_mean_s": statistics.mean(workflows.values()) / 1000.,
        "episodes": episodes, "local_advance_ms_by_workflow": workflows,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                registration, short_path, Path(__file__),
                ROOT / "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        "input_jsonl_prefixes": reader.prefixes,
        "scope": [
            "Only earlier chunk boundaries following an actual active/ready logical observation are considered.",
            "Earlier physical prefix residency, request slots and joint native allocation remain unknown.",
            "Earlier prefill is granted a free first output and every later existing decode a free extra member.",
            "No extra compute, graph shape, growth, displaced long input, or control cost is charged.",
            "Future output lengths are used only in this optimistic offline screen.",
            "Earlier returns can change subsequent tools, JOINs, arrivals and other batches; none are replayed.",
            "Local advances can repeat related requests; their sum is not achievable JCT or a rigorous global upper bound.",
            "This is an opportunity scale check, not a GPU comparison or a workload acceptance.",
        ],
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "short-audit", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use nice19 CPU-only analysis")
    result = inspect(args.registration, args.short_audit)
    write(args.output, result)
    print(json.dumps({key: result[key] for key in (
        "old_short_continuation_count", "logical_ready_boundary_episodes", "single_token_outputs",
        "decode_before_original_prefill", "output_tokens", "prefill_advance_ms",
        "optimistic_unchanged_cadence_completion_advance_ms",
        "summed_local_advances_per_measured_workflow_mean_s",
    )}))


if __name__ == "__main__":
    main()
