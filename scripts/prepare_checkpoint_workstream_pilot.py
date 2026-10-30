"""Declare source-grounded checkpoints without editing projects or reading results."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.delegation_policy import (
    PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE, delegation_contract,
)
from beliefkv.experiments.long_repository_tasks import parse_repository_context, repository_path
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from scripts.train_workflow_work import write


HERE = ROOT / "experiments/reports/checkpoint_pilot6_20261030"
SOURCE = ROOT / "configs/workloads/locobench_parallel_pilot6_20261030.json"
PREVIOUS_DECLARATION = ROOT / "experiments/reports/workflow_mix_20261029/parallel_pilot6_declaration.json"
CONFIG_SOURCE = ROOT / "configs/migration/workflow_parallel_pilot6_native_20261030.json"
MANIFEST = ROOT / "configs/workloads/locobench_checkpoint_pilot6_20261030.json"
CONFIG = ROOT / "configs/migration/workflow_checkpoint_pilot6_native_20261030.json"

CHECKPOINTS = {
    "locobench/python_game_simulation_medium_033": {
        "paths": [
            "physage_academy/src/physage_academy/networking/manager.py",
            "physage_academy/src/physage_academy/networking/protocol.py",
            "physage_academy/src/physage_academy/engine/scene.py",
        ],
        "deliverable": (
            "Produce a bounded read-only audit of the existing host/client state "
            "boundary under object creation and deletion. Identify the state producer, "
            "the actual transmitted representation, and the client consumer in this "
            "checkout; compare their object-identity and update assumptions. Root owns "
            "the cross-file synthesis. Use complementary independent code checks, "
            "then return a concise supported mismatch or an explicit statement that "
            "the reported desynchronization is not established. No patch, running "
            "multiplayer deployment or whole-project repair is requested. Stop at "
            "this evidence checkpoint; do not presume the public symptom has a "
            "matching implementation or fabricate a reproduction."
        ),
    },
    "locobench/python_mobile_social_medium_022": {
        "paths": [
            "gatherproof_backend/src/adapters/social_sharing.py",
            "gatherproof_backend/src/core/models.py",
            "gatherproof_backend/src/handlers/incidents.py",
        ],
        "deliverable": (
            "Produce a bounded read-only compatibility audit of location data in "
            "the existing incident-to-social-sharing boundary. Establish the actual "
            "payload fields and adapters in this checkout, and compare how the "
            "available adapters represent that payload. Root owns the final "
            "cross-boundary evidence table and uncertainty. Use complementary "
            "independent checks of payload ownership and adapter behavior. Do not "
            "assume the platform names in the original symptom exist: record an "
            "absent name rather than searching indefinitely or adding an adapter. "
            "No patch, live social-network request or whole-project repair is "
            "requested. Return after this compatibility checkpoint."
        ),
    },
    "locobench/python_web_dashboard_medium_039": {
        "paths": ["src/utils.py", "src/config.py", "tests/test_utils.py"],
        "deliverable": (
            "Extract the existing colour helpers RGB, hex_to_rgb, rgb_to_hex, "
            "luminance and contrast_ratio from src/utils.py into src/color_utils.py. "
            "Preserve their public behavior and keep compatible re-exports from "
            "src.utils. Root implements and integrates the extraction and owns "
            "focused equivalence/regression checks; children can inspect distinct "
            "dependency or compatibility properties before and after the change. "
            "Update directly affected production imports if necessary, and report "
            "unrelated failures separately. Do not extract nonexistent email "
            "helpers, reorganize other utilities, or repair the entire suite."
        ),
    },
    "locobench/python_web_cms_hard_074": {
        "paths": [
            "mediverse_cms/apps/cms/models.py",
            "mediverse_cms/apps/cms/services.py",
            "mediverse_cms/apps/core/permissions.py",
        ],
        "deliverable": (
            "Add one bounded review-state milestone to the existing Page model. "
            "Provide an explicit draft-to-review operation and require review "
            "before Page.publish makes a page public; preserve the already-published "
            "idempotent case and report invalid state transitions clearly. Use "
            "the existing Page.Status and PageRevision abstractions. Root implements "
            "the state boundary and integrates focused transition checks; children "
            "review distinct model/revision or compatibility properties. Do not "
            "introduce a parallel Article model, replace missing repository layers, "
            "or implement the full approval API, notifications and permissions "
            "system. Existing unrelated application failures remain separately "
            "reported task issues."
        ),
    },
    "locobench/python_api_microservice_medium_008": {
        "paths": [
            "ledgerlink_gateway/ledgerlink/services/invoice_service.py",
            "ledgerlink_gateway/ledgerlink/services/payment_service.py",
            "ledgerlink_gateway/ledgerlink/models/invoice.py",
            "ledgerlink_gateway/ledgerlink/main.py",
        ],
    },
    "locobench/python_ml_inference_hard_088": {
        "paths": [
            "insightledger_ai/insight_ledger/model/registry/client.py",
            "insightledger_ai/services/api_server/inference/strategy.py",
            "insightledger_ai/insight_ledger/core/events.py",
            "insightledger_ai/tests/unit/test_inference_strategy.py",
        ],
    },
}


def source_paths(task, paths):
    """Verify known entry points against pinned provenance and committed blobs."""
    context = parse_repository_context(task["frozen_repository_context"])
    provenance = task["dataset_provenance"]
    path = Path(provenance["source_provenance_path"])
    if sha256_file(path) != provenance["source_provenance_sha256"]:
        raise ValueError("entry-point source provenance changed")
    entries = json.loads(path.read_bytes())
    by_path = {entry["path"]: entry for entry in entries}
    if len(by_path) != len(entries) or len(entries) != context.source_file_count:
        raise ValueError("entry-point inventory differs from the complete source")
    evidence = []
    for name in paths:
        name = repository_path(name)
        if name not in by_path or f"\n--- FILE {name} ---\n" not in context.text:
            raise ValueError("entry point is absent from the original source snapshot")
        blob = subprocess.run(
            ["git", "-C", task["source_repo"], "show", task["base_commit"] + ":" + name],
            check=True, capture_output=True, timeout=10.,
        ).stdout
        digest = hashlib.sha256(blob).hexdigest()
        if digest != by_path[name]["materialized_sha256"]:
            raise ValueError("entry-point blob differs from the declared source")
        evidence.append({"path": name, "materialized_sha256": digest})
    return evidence


def checkpoint_manifest(source, declaration):
    assignments = {row["instance_id"]: row for row in declaration["assignments"]}
    if (
        len(source["workloads"]) != 6
        or {task["repo"] for task in source["workloads"]} != CHECKPOINTS.keys()
        or len(assignments) != 6
        or {task["instance_id"] for task in source["workloads"]} != assignments.keys()
    ):
        raise ValueError("keep the same six distinct projects and declared scope mix")
    tasks, rows = [], []
    for old in source["workloads"]:
        assignment = assignments[old["instance_id"]]
        if assignment["project"] != old["repo"]:
            raise ValueError("checkpoint assignment changed its original project")
        plan = CHECKPOINTS[old["repo"]]
        paths = source_paths(old, plan["paths"])
        objective = plan.get("deliverable", assignment["requested_deliverable"])
        long_task = assignment["scope_hypothesis"] == "multi_stage"
        if long_task:
            context = old["problem_statement"].split("\n\nOriginal public task context:\n", 1)[1]
            objective += (
                " Root owns the primary implementation, integration and final "
                "verification of every stage. Map stale class or method names to "
                "the actual interfaces only when supported by source; preserve "
                "the requested behavior instead of creating a duplicate architecture."
            )
        else:
            context = (
                "This is an explicit source-grounded derived task. The original "
                "scenario is retained in provenance, not asserted as reproduced "
                "or completed by this narrower deliverable."
            )
        task = deepcopy(old)
        task["instance_id"] = (
            old["dataset_provenance"]["derived_task"]["original_instance_id"]
            + "__source_checkpoint_v2"
        )
        orientation = (
            "Known starting paths, relative to /workspace and verified in the "
            "initial source; these are navigation aids, not findings:\n"
            + "\n".join("- " + row["path"] for row in paths)
            + "\nStart from these paths and the supplied snapshot. Pass relevant "
            "paths and observed evidence to each child. Independently choose "
            "assignments, rounds and nesting from actual remaining work."
        )
        task["problem_statement"] = (
            "Requested deliverable for this derived task:\n" + objective
            + "\n\n" + orientation + "\n\nOriginal public task context:\n" + context
        )
        task["dataset_provenance"]["derived_task"] = {
            **old["dataset_provenance"]["derived_task"],
            "kind": "source_grounded_checkpoint_v2",
            "previous_derived_instance_id": old["instance_id"],
            "requested_deliverable": objective,
            "original_full_task_success_claimed": long_task,
            "known_starting_paths": paths,
            "future_trace_or_reference_solution_used": False,
        }
        tasks.append(task)
        rows.append({
            **assignment, "previous_instance_id": old["instance_id"],
            "instance_id": task["instance_id"], "requested_deliverable": objective,
            "problem_statement_sha256": fingerprint(task["problem_statement"]),
            "known_starting_paths": paths,
            "source_context_sha256": old["frozen_repository_context"]["sha256"],
        })
    manifest = deepcopy(source)
    manifest.update(
        workloads=tasks,
        purpose="source_grounded_checkpoint_shape_pilot_not_performance_comparison",
        selection_policy=(
            "same six declared project/context/scope slots; ground navigation and "
            "bounded deliverables in initial source; no scheduler-win selection"
        ),
    )
    contract = deepcopy(declaration)
    contract.update(
        kind="source_grounded_checkpoint_shape_pilot",
        assignments=rows,
        delegation_contract=delegation_contract(PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE),
        source_shape_qualified=False, serving_pressure_qualified=False,
        formal_comparison_ready=False,
        future_trace_or_reference_solution_used=False,
    )
    return manifest, contract


def main():
    outputs = MANIFEST, CONFIG, HERE / "declaration.json", HERE / "preparation.json"
    if any(path.exists() for path in outputs):
        raise FileExistsError("preserve previous checkpoint declarations")
    manifest, contract = checkpoint_manifest(
        json.loads(SOURCE.read_bytes()), json.loads(PREVIOUS_DECLARATION.read_bytes()),
    )
    configuration = json.loads(CONFIG_SOURCE.read_bytes())
    configuration.update(
        purpose="source_grounded_checkpoint_shape_pilot_not_performance_comparison",
        source_collection_contract_sha256=fingerprint(contract),
    )
    configuration["common_environment"]["WORKLOAD_MANIFEST"] = str(MANIFEST.relative_to(ROOT))
    configuration["training_protocol"]["purpose"] = "natural_source_grounded_checkpoint_shape_pilot"
    for path, value in zip(outputs[:3], (manifest, configuration, contract), strict=True):
        write(path, value)
    write(outputs[3], {
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (
                SOURCE, PREVIOUS_DECLARATION, CONFIG_SOURCE, Path(__file__),
                ROOT / "beliefkv/experiments/delegation_policy.py",
                ROOT / "beliefkv/experiments/deepagents_swebench.py",
            )
        },
        "outputs_sha256": {str(path): sha256_file(path) for path in outputs[:3]},
        "projects": 6, "root_child_budgets_unchanged": True,
        "original_sources_tests_environments_preserved": True,
        "GPU_experiment_started": False, "performance_accepted": False,
    })
    print({"projects": 6, "protocol_version": 2, "GPU_action": False})


if __name__ == "__main__":
    main()
