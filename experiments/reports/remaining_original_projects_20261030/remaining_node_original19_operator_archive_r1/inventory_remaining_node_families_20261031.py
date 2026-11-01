"""Read untouched Node projects from unused LoCoBench numbered families."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-device-plan-operator-75414c7e")
MAIN = Path("/home/longhao/experiment/BeliefKV")
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "remaining_node_families_inventory_r1"
POPULATION = REPORTS / "background_extension_source25_timing_requalification_r1/original81_population_r1.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.long_repository_tasks import render_repository_context
from beliefkv.experiments.source_provenance import sha256_file
from scripts.prepare_locobench_agent_manifest import load_project, scenario_prompt
from scripts.prepare_original_task_mix import POOL, TOKENIZER_SOURCE
from scripts.train_workflow_work import write


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve inventory and use nice19 CPU-only execution")
    population = json.loads(POPULATION.read_bytes())["workflows"]
    projects = {row["project"].split("/", 1)[1] for row in population}
    if len(projects) != 81 or any(row.get("eligible") is not True for row in population):
        raise ValueError("require all complete existing original projects")
    covered = {project.rsplit("_", 1)[-1] for project in projects}
    pool, model = (json.loads(path.read_bytes()) for path in (POOL, TOKENIZER_SOURCE))
    archive_path = Path(pool["archive"])
    if sha256_file(archive_path) != pool["archive_sha256"] or any(
        sha256_file(Path(model["tokenizer"]) / filename) != model[field]
        for filename, field in (
            ("tokenizer.json", "tokenizer_sha256"), ("tokenizer_config.json", "tokenizer_config_sha256"),
        )
    ):
        raise ValueError("original archive or actual serving tokenizer changed")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model["tokenizer"], local_files_only=True)
    rows, missing = [], []
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        metadata = [
            path for path in names if path.endswith("/project_metadata.json")
            and path.startswith(("data/generated/javascript_", "data/generated/typescript_"))
            and path.split("/")[-2].rsplit("_", 1)[-1] not in covered
        ]
        for path in sorted(metadata):
            project = path.split("/")[-2]
            family = project.rsplit("_", 1)[-1]
            language = project.split("_", 1)[0]
            matches = [
                name for name in names
                if name.startswith("data/output/scenarios/" + project + "_code_comprehension_")
                and name.endswith(".json")
            ]
            if len(matches) != 1:
                missing.append({"project": project, "reason": "no_unique_complete_comprehension_original"})
                continue
            raw = archive.read(matches[0])
            scenario = json.loads(raw)
            metadata_raw = archive.read(path)
            meta, files, _provenance, errors = load_project(archive, path, python_minor_version=10)
            context = render_repository_context(project, files)
            tokens = len(tokenizer.encode(context, add_special_tokens=False))
            packages = []
            for filename, content in files.items():
                if Path(filename).name == "package.json":
                    try:
                        package = json.loads(content)
                    except ValueError:
                        packages.append({"path": filename, "invalid_original_JSON": True})
                    else:
                        packages.append({
                            "path": filename, "engines": package.get("engines", {}),
                            "dependencies": package.get("dependencies", {}),
                            "devDependencies": package.get("devDependencies", {}),
                            "scripts": package.get("scripts", {}),
                        })
            rows.append({
                "project": project, "family": family, "language": language,
                "name": meta.get("name"), "metadata_path": path,
                "metadata_sha256": hashlib.sha256(metadata_raw).hexdigest(),
                "complete_source_files": len(files), "complete_context_tokens": tokens,
                "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
                "original_scenario": matches[0], "original_scenario_sha256": hashlib.sha256(raw).hexdigest(),
                "original_id": scenario["id"], "original_difficulty": scenario["difficulty"],
                "original_title": scenario["title"],
                "original_problem_sha256": hashlib.sha256(scenario_prompt(scenario).encode()).hexdigest(),
                "incidental_python_syntax_diagnostics_not_repaired": errors,
                "packages": packages,
                "fits_protocol_headroom": 8000 <= tokens <= 235000,
                "context_at_most_96000": tokens <= 96000,
            })
    selected = []
    for family in sorted({row["family"] for row in rows}):
        candidates = [
            row for row in rows if row["family"] == family and row["fits_protocol_headroom"]
            and not row["incidental_python_syntax_diagnostics_not_repaired"]
        ]
        if candidates:
            selected.append(min(candidates, key=lambda row: (
                row["complete_context_tokens"], row["project"],
            )))
    write(HERE / "inventory.json", {
        "kind": "remaining_original_Node_families_CPU_inventory",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "archive": str(archive_path), "archive_sha256": pool["archive_sha256"],
        "tokenizer": model["tokenizer"], "tokenizer_sha256": model["tokenizer_sha256"],
        "already_captured_projects": sorted(projects),
        "already_captured_project_families": sorted(covered),
        "conservative_cross_language_numbered_family_exclusion_preserved": True,
        "all_uncovered_rows": rows, "missing_originals": missing,
        "selected_candidates": selected,
        "selection": "one unchanged comprehension original per unused family; smallest complete Node context",
        "selection_uses_native_or_policy_outcomes": False,
        "original_task_requirements_modified": False, "source_and_tests_modified": False,
        "short_workflows_not_proven_before_natural_capture": True,
        "runtime_image_tools_and_source_capture_qualification_pending": True,
        "capture_ready": False, "GPU_action": False, "GPU_followup_registered": False,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            POPULATION, POOL, TOKENIZER_SOURCE, Path(__file__),
            ROOT / "scripts/prepare_locobench_agent_manifest.py",
            ROOT / "scripts/prepare_original_task_mix.py",
        )},
    })
    print(json.dumps({
        "existing_projects": len(projects), "covered_families": len(covered),
        "uncovered_Node_candidates": len(rows), "selected_new_families": len(selected),
        "languages": dict(Counter(row["language"] for row in selected)),
        "contexts": [row["complete_context_tokens"] for row in selected],
        "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
