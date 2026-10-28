"""Bind the complete reuse/wait audit and the new CPU-only diagnostic checks."""

from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEvent
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_reference_join_grants import validate_grant
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def load(path):
    return json.loads(path.read_bytes())


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve earlier follow-up evidence")
    review_path = HERE / "complete_review.json"
    review = load(review_path)
    registration = load(Path(review["proofs"]["Full"]["registration"]))
    causal.comparison.verify_sources(registration)
    arm = Path(registration["run_root"]) / "full"
    digests = {}

    def verify(path, digest=None):
        path = Path(path).resolve()
        actual = digests.setdefault(str(path), sha256_file(path))
        if digest is not None and actual != digest:
            raise ValueError("evidence changed: " + str(path))
        return actual

    verify(review_path)
    verify(
        Path(registration["engine_checkout"])
        / "python/sglang/srt/mem_cache/beliefkv_layer_handoff.py"
    )
    for source, digest in review["inputs_sha256"].items():
        verify(source, digest)
    warm_path = HERE / "complete_wait_prefetch_bound_summary.json"
    warm = load(warm_path)
    payload = gzip.decompress((HERE / warm["detail_file"]).read_bytes())
    if hashlib.sha256(payload).hexdigest() != warm["detail_uncompressed_sha256"]:
        raise ValueError("compressed prefetch detail changed")
    detail = json.loads(payload)
    if (
        warm["collection_code_commit"] != registration["code_commit"]
        or warm["summary"] != detail["summary"]
        or warm["input_jsonl_prefixes"] != detail["input_jsonl_prefixes"]
    ):
        raise ValueError("prefetch evidence changed source or summary")
    initial_path = HERE / "complete_wait_prefetch_summary.json"
    initial = load(initial_path)
    if (
        initial["summary"] != warm["summary"]
        or initial["input_jsonl_prefixes"] != warm["input_jsonl_prefixes"]
    ):
        raise ValueError("binding-only rerun changed measured reuse")
    waits_path = HERE / "complete_sampled_restore_waits.json"
    waits = load(waits_path)
    expected_arms = {
        "Full": arm,
        "ThunderAgent-decay": Path(review["proofs"]["ThunderAgent decay"]["registration"]),
    }
    control_registration = load(expected_arms["ThunderAgent-decay"])
    expected_arms["ThunderAgent-decay"] = (
        Path(control_registration["run_root"]) / "thunderagent_acting_decay"
    )
    for label, data in waits["arms"].items():
        if Path(data["arm"]).resolve() != expected_arms[label].resolve():
            raise ValueError("restore wait evidence belongs to another arm")
    for data in (warm, *waits["arms"].values()):
        for source, seal in data["input_jsonl_prefixes"].items():
            path = Path(source).resolve()
            if (
                not any(path.is_relative_to(value.resolve()) for value in expected_arms.values())
                or path.stat().st_size != seal["complete_prefix_bytes"]
            ):
                raise ValueError("unrelated or partial audit seal")
            verify(path, seal["complete_prefix_sha256"])
    for source, digest in {
        **warm["analysis_source_sha256"], **waits["auditor_source_sha256"],
    }.items():
        verify(ROOT / source, digest)
    sealed = SealedRecords()
    grants, refusals, attempts, releases = [], [], None, 0
    for row in sealed(arm / "opportunities/admission_opportunities.jsonl"):
        event = row.get("event")
        if event == "agent_join_blocked_capacity_admitted":
            validate_grant(row)
            grants.append(row)
        elif event == "layer_kv_handoff_refused":
            refusals.append({
                field: row[field] for field in (
                    "beneficiary_request_id", "device_free_tokens", "required_reserve_tokens",
                )
            })
        elif event == "agent_join_planned_physical_release":
            releases += 1
        elif event == "admission_runtime_state":
            attempts = row["counts"].get("join_physical_plan_attempts", 0)
    events = sorted(
        (RuntimeEvent.from_dict(row) for row in sealed(arm / "replay/runtime_events.replay.jsonl")),
        key=lambda event: event.ts_ms,
    )
    graph, position, states, donor_rows = RuntimeCausalContextGraph(), 0, Counter(), []
    for grant in grants:
        emitted_by = grant["monotonic_s"] * 1000.
        while position < len(events) and events[position].ts_ms <= emitted_by:
            graph.apply(events[position])
            position += 1
        for donor in grant["donors"]:
            key = donor["key"]
            invocation = graph.invocations.get(key["invocation_id"])
            if invocation is None or (
                invocation.workflow_id, invocation.context_id,
            ) != (key["root_workflow_id"], key["context_id"]):
                raise ValueError("donor has no matching emitted context")
            states[invocation.state.value] += 1
            donor_rows.append({
                "join_id": grant["join_id"], "key": key,
                "emitted_event_state": invocation.state.value,
                "runtime_delivery_state_independently_verified": False,
            })
    for source, seal in sealed.prefixes.items():
        verify(source, seal["complete_prefix_sha256"])
    cpu_path = HERE / "control_diagnostics_shared_cpu_r1.evidence.json"
    cpu = load(cpu_path)
    verify(cpu["source_manifest"]["path"], cpu["source_manifest"]["sha256"])
    for source, digest in load(Path(cpu["source_manifest"]["path"])).items():
        verify(ROOT / source, digest)
    xml_path = Path(cpu["report"]["path"])
    verify(xml_path, cpu["report"]["sha256"])
    xml = ET.parse(xml_path).getroot()
    cases = list(xml.iter("testcase"))
    if any(item.find(status) is not None for item in cases for status in ("failure", "error", "skipped")):
        raise ValueError("shared CPU checks were not all passing")
    measured_path = HERE / "control_diagnostics_cpu_r1.json"
    measured = load(measured_path)
    for source, digest in measured["sources_sha256"].items():
        verify(ROOT / source, digest)
    for path in (
        warm_path, waits_path, cpu_path, measured_path, initial_path,
        HERE / warm["detail_file"], Path(__file__),
    ):
        verify(path)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete frozen-run evidence plus new CPU diagnostics; no new GPU run.",
        "prefetch": {
            "commands": warm["summary"]["join_commands"],
            **{name: warm["summary"]["by_source"]["join_ticket"][name] for name in (
                "full_transferred_bytes_known", "full_verified_reused_bytes_known",
                "full_use_unknown_commands",
            )},
            "early": {
                name: warm["summary"]["wait_events"]["by_source"]["join_ticket"][name]
                for name in (
                    "observed_events", "events_with_early_reused_full",
                    "early_started_and_reused_full_bytes",
                )
            },
        },
        "recorded_plan_attempts": attempts,
        "plan_physical_release_journals": releases,
        "logical_grant_donor_emitted_states": dict(states),
        "logical_grant_donor_rows": donor_rows,
        "native_refusals": refusals,
        "native_refusals_with_free_below_reserve": sum(
            row["device_free_tokens"] < row["required_reserve_tokens"] for row in refusals
        ),
        "sampled_restore_waits": {label: data["summary"] for label, data in waits["arms"].items()},
        "shared_cpu": {
            "unique_testcases": len({(row.get("classname"), row.get("name")) for row in cases}),
            "subtests": sum(int(suite.get("tests", 0)) for suite in xml.iter("testsuite")) - len(cases),
            "constructors": sorted(cpu["constructors"]),
        },
        "control_cpu_fixture": measured["cases"], "inputs_sha256": digests,
        "performance_accepted": False, "gpu_used": False,
        "limitations": [
            "The frozen run did not record individual physical-plan early-return reasons.",
            "Emitted event state is not independently verified scheduler delivery state or physical occupancy.",
            "Logical-grant donor rows do not cover plans established without new logical grants.",
            "Free below reserve suffices for refusal, without excluding other simultaneous guard failures.",
            "Reuse proves use, not timely full restoration, hidden DMA, saved JCT or net migration benefit.",
            "Restore-wait cohorts differ; sampled waits are never expanded to a complete-run estimate.",
            "Initial prefetch audit used the analyst commit as collection metadata; the bound rerun preserves identical data.",
            "The new profiler is opt-in and does not retroactively measure the old run.",
        ],
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        name: result[name] for name in (
            "prefetch", "recorded_plan_attempts", "plan_physical_release_journals",
            "logical_grant_donor_emitted_states", "native_refusals_with_free_below_reserve", "shared_cpu",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
