"""Separate predictive release stages from native pressure and Host backups."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve complete offload-path evidence")
    directory = output.parent
    review_path = directory / "complete_review.json"
    review = json.loads(review_path.read_bytes())
    registration = Path(review["proofs"]["full"]["registration"])
    plan = json.loads(registration.read_bytes())
    arm = (Path(plan["run_root"]) / "full").resolve()
    census = arm / "server/native_capacity_census.json"
    capacity = json.loads(census.read_bytes())["capacity"]
    if capacity["pool_layout"] != "static_full_only" or capacity["host_mamba_slots"] != 0:
        raise ValueError("release audit requires exact FULL-only geometry")
    unit, remainder = divmod(capacity["host_full_bytes"], capacity["host_full_tokens"])
    if remainder or unit != 98304:
        raise ValueError("unexpected FULL bytes per token")
    reader = SealedRecords()
    groups, stages = defaultdict(Counter), defaultdict(Counter)
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        event = row.get("event")
        if event not in ("parent_pressure_demoted", "predicted_tool_kv_evicted"):
            continue
        predicted = event == "predicted_tool_kv_evicted"
        source = (
            "additional_predicted_tool_offload" if predicted
            else "allocation_pressure_" + row["source"]
        )
        units = row["freed_units"]
        if any(
            type(n) is not int or n < 0 or str(component) != "0"
            for component, n in units.items()
        ):
            raise ValueError("invalid FULL-only release units")
        freed = units.get("0", units.get(0, 0))
        size = freed * unit
        if predicted and row.get("freed_bytes") != size:
            raise ValueError("predicted release bytes do not reconcile")
        groups[source].update(events=1, full_tokens=freed, full_bytes=size)
        if predicted:
            stage = row.get("dispatch_stage")
            if stage not in ("before_native_admission", "maintenance"):
                raise ValueError("new candidate omitted the release dispatch stage")
            stages[stage].update(events=1, full_tokens=freed, full_bytes=size)
    groups.setdefault(
        "additional_predicted_tool_offload",
        Counter(events=0, full_tokens=0, full_bytes=0),
    )
    for path, prefix in reader.prefixes.items():
        if (
            Path(path).stat().st_size != prefix["complete_prefix_bytes"]
            or sha256_file(Path(path)) != prefix["complete_prefix_sha256"]
            or review["inputs_sha256"].get(path) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("offload paths and complete review use different complete input")
    early = review["early_offload_service"]["summary"]
    before = stages["before_native_admission"]
    if (
        early["early_node_release_events"] != before["events"]
        or early["early_node_released_bytes"] != before["full_bytes"]
    ):
        raise ValueError("independent early-service and release-stage audits disagree")
    wait_path = directory / "complete_wait_offload.json"
    wait = json.loads(wait_path.read_bytes())
    for source, values in wait["summary"]["hbm_demotion_by_source"].items():
        pressure = groups["allocation_pressure_" + source]
        if (
            values["released_full_tokens"] != pressure["full_tokens"]
            or values["released_full_bytes"] != pressure["full_bytes"]
            or values["node_demotion_events"] != pressure["events"]
        ):
            raise ValueError("independent waiting-agent release audit disagrees")
    state = review["final_runtime_snapshots"]["full"]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arm": str(arm), "code_commit": plan["code_commit"],
        "complete_review_sha256": sha256_file(review_path),
        "independent_wait_offload_audit_sha256": sha256_file(wait_path),
        "full_bytes_per_token": unit,
        "release_paths": {name: dict(values) for name, values in sorted(groups.items())},
        "predicted_release_dispatch_stages": {
            name: dict(values) for name, values in sorted(stages.items())
        },
        "complete_early_consumer_service": early,
        "final_recorded_counts": {
            name: value for name, value in state["counts"].items()
            if name.startswith(("predicted_tool_offload_", "tool_offload_"))
        },
        "final_recorded_idle_prepare": state["idle_full_prepare"],
        "input_jsonl_prefixes": reader.prefixes,
        "capacity_census_sha256": sha256_file(census),
        "operator_sha256": sha256_file(Path(__file__)),
        "runtime_sources_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "beliefkv/runtime/predicted_tool_offload.py",
                "scripts/audit_wait_offload.py",
            )
        },
        "performance_accepted": False,
        "limitations": [
            "Release totals are cumulative, not simultaneous capacity or globally unique bytes.",
            "Host backup ACKs are not physical HBM release.",
            "Native untagged eviction and terminal child discard remain separate.",
            "Gate counts are not continuous opportunity recall or a unique performance cause.",
            "No future tool-end labels enter the online policy.",
            "The mechanism is not progressive layer-capacity handoff.",
        ],
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "release_paths": result["release_paths"],
        "predicted_release_dispatch_stages": result["predicted_release_dispatch_stages"],
    }, indent=2))


if __name__ == "__main__":
    main()
