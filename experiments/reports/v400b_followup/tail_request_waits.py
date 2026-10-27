"""Compare exact request waits on the two observed final-workflow paths."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_causal_jct_paths import first_launches, unique
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_tool_reentry_service import tool_reentries


def collect(arm):
    reader = SealedRecords()
    requests = unique(reader(arm / "replay/native_requests.jsonl"), "request_id")
    status = json.loads((arm / "server/native_telemetry_status.json").read_bytes())
    expected = status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
    launches = first_launches(
        reader(arm / "server/runtime_audit.jsonl"), requests, expected_records=expected,
    )
    submits, results = {}, {}
    for event in reader(arm / "server/runtime_events.sglang.jsonl"):
        if event["kind"] in ("llm_submit", "llm_result"):
            target = submits if event["kind"] == "llm_submit" else results
            rid = event["attributes"]["request_id"]
            if rid in target:
                raise ValueError("duplicate native request")
            target[rid] = event
    if submits.keys() != requests.keys() or results.keys() != requests.keys():
        raise ValueError("native request population is incomplete")
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    tools, _, _ = tool_reentries(events)
    durations = {
        row["request_id"]: row.get("tool_round_duration_ms")
        for row in tools if row.get("request_id")
    }
    rows = {}
    for rid, request in requests.items():
        submit, result = submits[rid], results[rid]
        offset = result["ts_ms"] - request["native_complete_monotonic_ms"]
        attrs = submit["attributes"]
        client = request["submitted_monotonic_ms"] + offset
        launch = launches[rid] + offset
        queue = attrs["native_ingress_timing_ms"]["native_queue_registered_ts_ms"]
        if not client <= queue <= launch + .01:
            raise ValueError("request ingress and launch clocks disagree")
        match = (attrs.get("native_host_hit_match_path") or {}).get("observed_ts_ms")
        rows[rid] = {
            "workflow_id": submit["workflow_id"], "invocation_id": submit["invocation_id"],
            "context_id": submit["context_id"], "context_epoch": submit["context_epoch"],
            "prompt_tokens": request["prompt_tokens"], "output_tokens": request["output_tokens"],
            "submit_to_first_launch_ms": launches[rid] - request["submitted_monotonic_ms"],
            "client_to_native_queue_ms": queue - client,
            "native_queue_to_first_launch_ms": launch - queue,
            "first_launch_to_complete_ms": request["native_complete_monotonic_ms"] - launches[rid],
            "request_total_ms": request["native_complete_monotonic_ms"] - request["submitted_monotonic_ms"],
            "native_queue_wall_ms": queue, "first_launch_wall_ms": launch,
            "service_host_hit_tokens": attrs.get("cached_tokens_host"),
            "service_device_hit_tokens": attrs.get("cached_tokens_device"),
            "service_uncached_tokens": attrs.get("uncached_prompt_tokens"),
            "host_path_observation_to_launch_ms": launch - match if match is not None else None,
            "preceding_tool_round_ms": durations.get(rid),
        }
    for source, prefix in reader.prefixes.items():
        path = Path(source)
        if (
            path.stat().st_size != prefix["complete_prefix_bytes"]
            or sha256_file(path) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("complete request observation changed")
    return rows, reader.prefixes


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve tail request evidence")
    paths = json.loads((output.parent / "complete_jct_paths.json").read_bytes())
    workflow = next(
        name for name in paths["arms"]["reference_full"]["workflows"]
        if "locobench__python_mobile_game_medium_096_" in name
    )
    arms, prefixes = {}, {}
    for name, arm in paths["arms"].items():
        arms[name], prefixes[name] = collect(Path(arm["arm"]))
    first = paths["arms"]["reference_full"]["workflows"][workflow]["critical_path_requests"]
    second = paths["arms"]["thunderagent_acting_decay"]["workflows"][workflow]["critical_path_requests"]
    if first != second:
        raise ValueError("the two observed critical paths have different request sequences")
    paired = []
    for index, rid in enumerate(first):
        full, control = arms["reference_full"][rid], arms["thunderagent_acting_decay"][rid]
        if any(full[field] != control[field] for field in (
            "workflow_id", "invocation_id", "context_id", "context_epoch",
            "prompt_tokens", "output_tokens",
        )):
            raise ValueError("paired request identity or work differs")
        paired.append({
            "request_id": rid, "path_index": index, "reference_full": full,
            "thunderagent_acting_decay": control,
            "queue_increase_ms": full["submit_to_first_launch_ms"] - control["submit_to_first_launch_ms"],
            "post_launch_increase_ms": full["first_launch_to_complete_ms"] - control["first_launch_to_complete_ms"],
        })
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "workflow_id": workflow, "same_critical_path_requests": True,
        "requests": len(paired), "pairs": paired,
        "queue_increase_ms": sum(row["queue_increase_ms"] for row in paired),
        "post_launch_increase_ms": sum(row["post_launch_increase_ms"] for row in paired),
        "input_jsonl_prefixes": prefixes, "operator_sha256": sha256_file(Path(__file__)),
        "scope": [
            "Exact same 201 requests on both realized final-workflow paths.",
            "Cache hits describe service accounting, not residency at arrival.",
            "Native queue intervals include eligibility, selection, allocation and recovery work.",
            "No individual eligibility identity is inferred from reference program counts.",
            "Request differences are descriptive and do not isolate a scheduling mechanism.",
        ],
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "requests": len(paired), "queue_increase_ms": result["queue_increase_ms"],
        "largest_queue_increases": sorted(
            paired, key=lambda row: -row["queue_increase_ms"],
        )[:8],
    }, indent=2))


if __name__ == "__main__":
    main()
