"""Freeze original whole workflows from several completed native captures."""

import argparse
from collections import defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent
from beliefkv.experiments.causal_replay import compile_causal_replay
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.composite_workload import (
    KIND, SERVING_FIELDS, merge_plans, plan_dict, load_composed_source,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import inventory_capture, population_summary, summary
from beliefkv.experiments.workload_requirements import assess
from beliefkv.runtime.replay_token_capture import native_request_expectations
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_parallel_workstreams import audit as audit_workstreams
from scripts.prepare_reused_workflow_mix import original_source_proof
from scripts.train_workflow_work import write


def project_representatives(inventories):
    """Keep the first declared original per project, never a lucky short rerun."""
    selected, retained = {}, []
    for inventory in inventories:
        for row in inventory["workflows"]:
            if not row["eligible"]:
                raise ValueError("composition requires complete naturally eligible workflows")
            if row["project"] not in selected:
                selected[row["project"]] = row
            else:
                retained.append({
                    "project": row["project"], "task_id": row["task_id"],
                    "source_capture": row["source_capture"],
                    "reason": "keep_first_declared_project_representative",
                })
    if not selected:
        raise ValueError("no complete original workflows to compose")
    return list(selected.values()), retained


def select_diverse_projects(rows, *, minimum_ratio, minimum_projects):
    if (
        type(minimum_ratio) not in (int, float) or not math.isfinite(minimum_ratio) or minimum_ratio < 1
        or type(minimum_projects) is not int or not 1 <= minimum_projects <= len(rows)
    ):
        raise ValueError("declare the intrinsic work ratio and minimum full-project population")
    selected, excluded = list(rows), []
    while summary([row["model_work_units"] for row in selected])["p80_p20"] < minimum_ratio:
        if len(selected) == minimum_projects:
            raise ValueError("declared whole-project pool cannot reach diversity by median removal")
        middle = statistics.median(row["model_work_units"] for row in selected)
        row = min(selected, key=lambda item: (
            abs(item["model_work_units"] - middle), item["project"], item["task_id"],
        ))
        selected.remove(row)
        excluded.append({
            **row, "selection_reason": "nearest_intrinsic_work_median_removed_from_primary_population",
        })
    return selected, excluded


def serving_artifacts(by_source, proofs, selected):
    """Retain common serving resources while disclosing heterogeneous prompts."""
    common, plans, originals = None, [], {}
    for source in by_source:
        arm = Path(source)
        paths = (arm / "server/server_info.json", arm / "server/native_capacity_census.json",
                 arm.parent / "capture_plan.json")
        info, capacity, plan = (json.loads(path.read_bytes()) for path in paths)
        values = ({name: info[name] for name in SERVING_FIELDS}, capacity["capacity"], plan["engine_tree"])
        if common is not None and values != common:
            raise ValueError("composed traces need the same model and declared native resources")
        if plans and plan["hold_file"] != plans[0]["hold_file"]:
            raise ValueError("composed sources have different resource authorization files")
        common = values
        plans.append(plan)
        originals.update({str(path.resolve()): sha256_file(path) for path in paths})
    tasks = {}
    manifest = None
    for proof in proofs:
        path = Path(proof["workload"])
        source = json.loads(path.read_bytes())
        manifest = source if manifest is None else manifest
        if source["archive_sha256"] != manifest["archive_sha256"]:
            raise ValueError("original projects must come from the same pinned dataset")
        capture = str(Path(proof["client_manifest"]).resolve().parent.parent)
        for task in source["workloads"]:
            tasks[capture, task["instance_id"]] = task
        originals[str(path.resolve())] = sha256_file(path)
    combined = deepcopy(manifest)
    for key in ("environment_qualification", "tool_environment_qualification"):
        combined.pop(key, None)
    combined.update(
        workloads=[tasks[row["source_capture"], row["task_id"]] for row in selected],
        requested_workflows=len(selected), max_tasks_per_project=1,
        purpose=KIND, source_capture_required=False,
        source_capture_protocols=sorted({row["protocol_sha256"] for row in selected}),
        selection_policy="first complete original project representative across declared sources",
    )
    combined["selected_projects"] = [row["project"].split("/", 1)[1] for row in selected]
    combined["selected_scenarios"] = [
        task["dataset_provenance"]["original_scenario"] for task in combined["workloads"]
    ]
    return {
        "server/server_info.json": common[0],
        "server/native_capacity_census.json": {"capacity": common[1]},
        "workload_manifest.json": combined,
        "serving_source.json": {
            "engine_tree": common[2],
            "original_capture_plans": [str(Path(source).parent / "capture_plan.json") for source in by_source],
            "hold_file": plans[0]["hold_file"],
        },
    }, originals


def prepare(
    inventories, output, *, inventory_paths=(), seconds_per_model_unit=None, audit_shape=False,
    minimum_work_ratio=None, minimum_projects=36,
):
    if output.exists():
        raise FileExistsError("preserve every composed workload preparation")
    refreshed = {}
    if seconds_per_model_unit is not None:
        for inventory in inventories:
            source = inventory["capture"]
            current = inventory_capture(Path(source), seconds_per_model_unit=seconds_per_model_unit)
            before = {
                row["workflow_id"]: tuple(row[key] for key in (
                    "task_id", "project", "event_sha256", "token_source_sha256",
                    "requests", "generated_tokens", "model_work_units",
                )) for row in inventory["workflows"]
            }
            after = {
                row["workflow_id"]: tuple(row[key] for key in (
                    "task_id", "project", "event_sha256", "token_source_sha256",
                    "requests", "generated_tokens", "model_work_units",
                )) for row in current["workflows"]
            }
            if before != after:
                raise ValueError("metric refresh changed whole-workflow work or source identity")
            refreshed[source] = current
        inventories = [refreshed[inventory["capture"]] for inventory in inventories]
    selected, retained = project_representatives(inventories)
    coefficients = {inventory.get("model_seconds_per_unit") for inventory in inventories}
    common_coefficient = next(iter(coefficients)) if len(coefficients) == 1 else None
    candidate_count = len(selected)
    excluded = []
    if minimum_work_ratio is not None:
        selected, excluded = select_diverse_projects(
            selected, minimum_ratio=minimum_work_ratio, minimum_projects=minimum_projects,
        )
    proofs = [original_source_proof(inventory) for inventory in inventories]
    by_source = defaultdict(list)
    for row in selected:
        by_source[row["source_capture"]].append(row)
    originals = {str(path.resolve()): sha256_file(path) for path in inventory_paths}
    plans, expected, sources = [], {}, []
    for source, workflows in sorted(by_source.items()):
        arm, sealed = Path(source), SealedRecords()
        manifests = list(arm.glob("client_*/manifest.json"))
        if len(manifests) != 1:
            raise ValueError("composition requires one complete original capture client")
        client = manifests[0].parent
        summary = json.loads((client / "summary.json").read_bytes())
        if summary.get("workflow_count") != summary.get("completed_workflows"):
            raise ValueError("preserve unfinished source before selecting complete workflows")
        ids = {row["workflow_id"] for row in workflows}
        event_paths = [client / "workflows" / row["task_id"] / "runtime_events.deepagents.jsonl"
                       for row in workflows]
        events = [RuntimeEvent.from_dict(row) for path in event_paths for row in sealed(path)]
        native_path = arm / "server/runtime_events.sglang.jsonl"
        native = [row for row in sealed(native_path) if row.get("workflow_id") in ids]
        if {event.workflow_id for event in events} != ids:
            raise ValueError("selected original event identity changed")
        tokens = arm / "server/request_tokens.jsonl"
        token_digest = sha256_file(tokens)
        if any(
            sha256_file(path) != row["event_sha256"] or token_digest != row["token_source_sha256"]
            for row, path in zip(workflows, event_paths, strict=True)
        ):
            raise ValueError("original event/token source changed since its complete inventory")
        graph = compile_causal_replay(events, native)
        calls = native_request_expectations(native)
        if expected.keys() & calls.keys():
            raise ValueError("composed request identity collision")
        expected.update(calls)
        plans.append(graph)
        inputs = [manifests[0], client / "summary.json", native_path, tokens, *event_paths]
        for path in inputs:
            originals[str(path.resolve())] = sha256_file(path)
        sources.append({
            "source_capture": source, "workflow_ids": sorted(ids),
            "native_clock_compiled_independently": True,
            "protocol_sha256": sorted({row["protocol_sha256"] for row in workflows}),
        })
    graph = merge_plans(plans)
    supporting, supporting_inputs = serving_artifacts(by_source, proofs, selected)
    originals.update(supporting_inputs)
    for index, inventory in enumerate(refreshed.values()):
        supporting[f"inventories/source_{index + 1}.json"] = inventory
    if audit_shape:
        combined = {"source": str(output.resolve()), "children": [], "joins": []}
        for index, (source, workflows) in enumerate(sorted(by_source.items())):
            shape = audit_workstreams(Path(source))
            supporting[f"workstreams/source_{index + 1}.json"] = shape
            ids = {row["workflow_id"] for row in workflows}
            for key in ("children", "joins"):
                combined[key].extend(row for row in shape[key] if row["workflow_id"] in ids)
        supporting["workload_assessment.json"] = assess(
            {"capture": combined["source"], "workflows": selected}, combined,
            allow_multiple_capture_protocols=True,
        )
    output.mkdir(parents=True, exist_ok=False)
    (output / "server").mkdir()
    tokens = output / "server/request_tokens.jsonl"
    with tokens.open("xb") as destination:
        for source, workflows in sorted(by_source.items()):
            ids = {row["workflow_id"] for row in workflows}
            with (Path(source) / "server/request_tokens.jsonl").open("rb") as stream:
                for raw in stream:
                    if not raw.endswith(b"\n"):
                        raise ValueError("incomplete original token capture")
                    if json.loads(raw).get("workflow_id") in ids:
                        destination.write(raw)
    write(output / "plan.json", plan_dict(graph))
    write(output / "expected.json", expected)
    with CapturedTokenCorpus(tokens, expected) as corpus:
        token_evidence = corpus.evidence
    write(output / "token_evidence.json", token_evidence)
    for name, data in supporting.items():
        write(output / name, data)
    write(output / "composite_source.json", {
        "schema_version": 1, "kind": KIND, "selected_workflows": selected,
        "retained_alternatives": retained, "sources": sources, "original_source_proofs": proofs,
        "candidate_projects": candidate_count, "excluded_whole_workflows_retained": excluded,
        "minimum_model_work_p80_p20": minimum_work_ratio,
        "primary_selection_rule": (
            "first project representatives; remove nearest intrinsic-work median until declared ratio"
            if minimum_work_ratio is not None else "all first project representatives"
        ),
        "population": population_summary(selected), "plan_summary": graph.summary(),
        "protocols": sorted({row["protocol_sha256"] for row in selected}),
        "heterogeneous_capture_protocols_disclosed": True,
        "common_model_seconds_per_work_unit": common_coefficient,
        "original_inputs_sha256": originals,
        "artifacts_sha256": {
            name: sha256_file(output / name)
            for name in ("plan.json", "expected.json", "server/request_tokens.jsonl", "token_evidence.json",
                         *supporting)
        },
        "external_workflow_arrivals_required": True,
        "matched_natural_native_baseline_available": False,
        "complete_original_tasks_only": True, "original_token_record_bytes_preserved": True,
        "raw_native_clock_streams_concatenated": False,
        "all_selected_workflows_and_normal_tails_preserved": True,
        "formal_comparison_ready": False, "GPU_action": False, "performance_accepted": False,
    })
    load_composed_source(output)
    return {"projects": len(selected), "source_captures": len(by_source), **graph.summary()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seconds-per-model-unit", type=float)
    parser.add_argument("--audit-shape", action="store_true")
    parser.add_argument("--minimum-work-ratio", type=float)
    parser.add_argument("--minimum-projects", type=int, default=36)
    args = parser.parse_args()
    print(prepare(
        [json.loads(path.read_bytes()) for path in args.inventory], args.output_dir.resolve(),
        inventory_paths=args.inventory, seconds_per_model_unit=args.seconds_per_model_unit,
        audit_shape=args.audit_shape,
        minimum_work_ratio=args.minimum_work_ratio, minimum_projects=args.minimum_projects,
    ))


if __name__ == "__main__":
    main()
