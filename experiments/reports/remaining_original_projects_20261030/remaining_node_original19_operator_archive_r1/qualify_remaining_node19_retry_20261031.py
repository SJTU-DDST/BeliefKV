"""Requalify only the Node18 project, preserving eighteen prior live proofs."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import zipfile

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-device-plan-operator-75414c7e")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
PRIOR = REPORTS / "remaining_node_original19_tools_r1"
HERE = REPORTS / "remaining_node_original19_tools_r2"
PROJECT = "locobench/javascript_system_networking_hard_099"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import qualify_locobench_tools as tools
from scripts import run_causal_comparison as causal
from scripts.prepare_original_task_mix import source_integrity, validate_population


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve attempts and use nice19 CPU-only execution")
    previous = causal.load(PRIOR / "toolchain_live_root_child.json")
    for filename, digest in previous["inputs_sha256"].items():
        if sha256_file(Path(filename)) != digest:
            raise ValueError("prior qualification input changed: " + filename)
    prior_input = Path(previous["manifest"])
    if sha256_file(prior_input) != previous["manifest_sha256"]:
        raise ValueError("prior tool manifest changed")
    manifest = deepcopy(causal.load(prior_input))
    rows = manifest["workloads"]
    old_results = previous["projects"]
    if len(rows) != 19 or previous["qualified_projects"] != 18 or (
        [task["repo"] for task in rows if not old_results[task["source_repo"]]["tool_execution_passed"]]
        != [PROJECT]
    ):
        raise ValueError("retry only the one preserved Node engine rejection")
    task = next(row for row in rows if row["repo"] == PROJECT)
    image = subprocess.check_output([
        "docker", "image", "inspect", "beliefkv-node18-typescript-tools:20261031",
        "--format", "{{.Id}}",
    ], text=True).strip()
    base = subprocess.check_output([
        "docker", "image", "inspect", "beliefkv-typescript-tools:20261031",
        "--format", "{{.Id}}",
    ], text=True).strip()
    if base != "sha256:d575c45c64ab3e213cc41620b11d5cab81d9047cacac0d5178b1f706615c174b":
        raise ValueError("Node18 runtime base changed")
    task["docker_image"] = image
    source = Path(task["source_repo"])
    candidates = [
        path for path in source.rglob("*") if path.is_file()
        and path.suffix in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")
        and not path.name.startswith(".") and not path.name.endswith(".d.ts")
        and "config" not in path.name.lower()
        and not any(part in (".git", "node_modules", "test", "tests", "__tests__", "fixtures")
                    for part in path.relative_to(source).parts)
    ]
    target = min(candidates, key=lambda path: (
        "src" not in path.relative_to(source).parts, path.relative_to(source).as_posix(),
    ))
    relative, digest = target.relative_to(source).as_posix(), sha256_file(target)
    probe = (
        "const fs=require('node:fs'),c=require('node:crypto');"
        "const [name,expected]=" + json.dumps([relative, digest]) + ";"
        "const actual=c.createHash('sha256').update(fs.readFileSync(name)).digest('hex');"
        "if(actual!==expected)throw Error('original source changed');"
        "if(process.version!=='v18.20.8')throw Error(process.version);"
        "console.log(JSON.stringify({original_source_read:true,path:name,sha256:actual}));"
        "console.log(JSON.stringify({node:process.version}));"
    )
    fixture = (
        "from pathlib import Path; Path('.beliefkv-tool-check/runtime.cts').write_text("
        "\"const value: string = 'ts-runtime-ok'; console.log(value);\\n\")"
    )
    suffix = (
        " && node -e " + shlex.quote(probe)
        + " && python -c " + shlex.quote(fixture)
        + " && tsc --strict --target ES2020 --module commonjs"
        + " --outDir .beliefkv-tool-check/compiled .beliefkv-tool-check/runtime.cts"
        + " && node .beliefkv-tool-check/compiled/runtime.cjs"
        + " && ts-node --skip-project --compiler-options "
        + shlex.quote('{"module":"CommonJS","target":"ES2020"}')
        + " .beliefkv-tool-check/runtime.cts"
    )
    HERE.mkdir(parents=True, exist_ok=False)
    input_path = HERE / "input_manifest.json"
    causal.reservation.write_json(input_path, manifest)
    inputs = [PRIOR / "toolchain_live_root_child.json", prior_input, Path(__file__),
              OPS / "node18_toolchain_20261031/Dockerfile", Path(tools.__file__)]
    for row in rows:
        if row["repo"] != PROJECT:
            report_path = PRIOR / (row["repo"].split("/", 1)[1] + ".json")
            if causal.load(report_path) != old_results[row["source_repo"]]:
                raise ValueError("a retained live result changed")
            inputs.append(report_path)
    baseline = tools.tool_command
    result = None
    with zipfile.ZipFile(manifest["archive"]) as archive:
        validate_population(rows, archive)
        integrity = {row["repo"]: source_integrity(row, archive) for row in rows}
        if integrity != previous["original_source_integrity"]:
            raise ValueError("the original project files changed")
        tools.tool_command = lambda role: baseline(role) + suffix
        try:
            result = tools.qualify(task, image)
            causal.reservation.write_json(HERE / "retry_live_raw.json", result)
            for role in ("root", "child"):
                lines = result["roles"][role]["output"].splitlines()
                observed = [
                    json.loads(line) for line in lines
                    if line.startswith('{"original_source_read":')
                ]
                if observed != [{"original_source_read": True, "path": relative, "sha256": digest}] or (
                    lines.count("ts-runtime-ok") != 2
                ):
                    raise ValueError("real source access or TypeScript execution failed")
            if not result["tool_execution_passed"]:
                raise ValueError("the normal tool execution failed")
            result.update(
                original_source_read_verified=True,
                private_TypeScript_compilation_and_execution_verified=True,
                source_probe={"path": relative, "sha256": digest},
                application_dependencies_prepared_for_this_project=False,
                application_execution_or_project_tests_qualified=False,
            )
            if source_integrity(task, archive) != integrity[PROJECT]:
                raise ValueError("the source changed during private tool qualification")
        except Exception as error:
            causal.reservation.write_json(HERE / "retry_failure.json", {
                "error": repr(error), "partial_live_result": result, "GPU_action": False,
            })
            raise
        finally:
            tools.tool_command = baseline
    results = deepcopy(old_results)
    results[task["source_repo"]] = result
    report_path = HERE / "toolchain_live_root_child.json"
    report = {
        "schema_version": 1, "cpu_only": True, "qualification_scope": "live_root_child_tools",
        "manifest": str(input_path), "manifest_sha256": sha256_file(input_path),
        "projects": results, "tool_execution_passed": True, "task_tests_required": False,
        "task_correctness": "agent_responsibility", "attempted_projects": 19,
        "qualified_projects": 19, "rejected_projects": {}, "original_source_integrity": integrity,
        "qualification_executions": {"retained_r1": 18, "fresh_Node18_r2": 1},
        "prior_failure_preserved": str(PRIOR / "javascript_system_networking_hard_099.json"),
        "application_dependencies_and_project_test_success_not_claimed": True,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "GPU_action": False, "performance_accepted": False,
    }
    causal.reservation.write_json(report_path, report)
    qualified = deepcopy(manifest)
    for row in qualified["workloads"]:
        row["sandbox_environment"] = results[row["source_repo"]]["sandbox_environment"]
    qualified["runtime_image_pin_and_live_root_child_tools_pending"] = False
    qualified["inherited_candidates_metadata_scope"] = "legacy Python pool; use workloads for this selected population"
    qualified["tool_environment_qualification"] = {
        "path": str(report_path), "sha256": sha256_file(report_path),
        "base_manifest": str(input_path), "base_manifest_sha256": sha256_file(input_path),
        "qualification_scope": "live_root_child_tools", "task_tests_required": False,
    }
    qualified["capture_ready"] = False
    causal.reservation.write_json(HERE / "qualified_manifest.json", qualified)
    print(json.dumps({
        "qualified_projects": 19, "retained_proofs": 18, "fresh_Node18": 1,
        "new_image": image, "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
