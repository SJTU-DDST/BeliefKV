"""Verify normal root/child tools and original source access for remaining Node tasks."""

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
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
PREPARED = REPORTS / "remaining_node_original19_preparation_r1"
HERE = REPORTS / "remaining_node_original19_tools_r1"
IMAGE = "sha256:d575c45c64ab3e213cc41620b11d5cab81d9047cacac0d5178b1f706615c174b"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import qualify_locobench_tools as tools
from scripts import run_causal_comparison as causal
from scripts.prepare_original_task_mix import source_integrity, validate_population


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve evidence and use nice19 CPU-only execution")
    original = causal.load(PREPARED / "manifest.json")
    preparation = causal.load(PREPARED / "preparation.json")
    for name, digest in preparation["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("preparation input changed: " + name)
    image = subprocess.check_output(
        ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"], text=True,
    ).strip()
    if image != IMAGE:
        raise ValueError("the compiler image changed")
    manifest = deepcopy(original)
    for task in manifest["workloads"]:
        task["docker_image"] = IMAGE
    HERE.mkdir(parents=True, exist_ok=False)
    input_path = HERE / "input_manifest.json"
    causal.reservation.write_json(input_path, manifest)
    results, integrity = {}, {}
    baseline_command = tools.tool_command
    with zipfile.ZipFile(manifest["archive"]) as archive:
        validate_population(manifest["workloads"], archive)
        for task in manifest["workloads"]:
            source = Path(task["source_repo"])
            integrity[task["repo"]] = source_integrity(task, archive)
            candidates = [
                path for path in source.rglob("*") if path.is_file()
                and path.suffix in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")
                and not path.name.startswith(".") and not path.name.endswith(".d.ts")
                and "config" not in path.name.lower()
                and not any(part in (".git", "node_modules", "test", "tests", "__tests__", "fixtures")
                            for part in path.relative_to(source).parts)
            ]
            if not candidates:
                raise ValueError("original task has no implementation source to read")
            target = min(candidates, key=lambda path: (
                "src" not in path.relative_to(source).parts,
                path.relative_to(source).as_posix(),
            ))
            relative, digest = target.relative_to(source).as_posix(), sha256_file(target)
            probe = (
                "const fs=require('node:fs'),c=require('node:crypto');"
                "const [name,expected]=" + json.dumps([relative, digest]) + ";"
                "const actual=c.createHash('sha256').update(fs.readFileSync(name)).digest('hex');"
                "if(actual!==expected)throw Error('original source changed');"
                "console.log(JSON.stringify({original_source_read:true,path:name,sha256:actual}));"
                "const p='/usr/local/lib/node_modules/';"
                "console.log(JSON.stringify({node:process.version,"
                "typescript:require(p+'typescript/package.json').version,"
                "ts_node:require(p+'ts-node/package.json').version}));"
            )
            fixture = (
                "from pathlib import Path; "
                "Path('.beliefkv-tool-check/runtime.cts').write_text("
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
            tools.tool_command = lambda role, suffix=suffix: baseline_command(role) + suffix
            try:
                result = tools.qualify(task, IMAGE)
                for role in ("root", "child"):
                    lines = result["roles"][role]["output"].splitlines()
                    observed = [
                        json.loads(line) for line in lines
                        if line.startswith('{"original_source_read":')
                    ]
                    if observed != [{"original_source_read": True, "path": relative, "sha256": digest}]:
                        raise ValueError("root/child original implementation read was not observed")
                    if lines.count("ts-runtime-ok") != 2:
                        raise ValueError("real TypeScript compilation and execution were not observed")
                result["original_source_read_verified"] = True
                result["private_TypeScript_compilation_and_execution_verified"] = True
                result["source_probe"] = {"path": relative, "sha256": digest}
                if source_integrity(task, archive) != integrity[task["repo"]]:
                    raise ValueError("original source changed during private qualification")
            except Exception as error:
                result = {
                    "tool_execution_passed": False, "original_source_read_verified": False,
                    "private_TypeScript_compilation_and_execution_verified": False,
                    "error": repr(error), "project_source_tests_or_requirements_repaired": False,
                }
            finally:
                tools.tool_command = baseline_command
            result["application_dependencies_prepared_for_this_project"] = False
            result["application_execution_or_project_tests_qualified"] = False
            results[task["source_repo"]] = result
            causal.reservation.write_json(HERE / (task["repo"].split("/", 1)[1] + ".json"), result)
            print(json.dumps({
                "project": task["repo"], "real_tools_and_source_read": result["tool_execution_passed"],
                "private_TypeScript_run": result["private_TypeScript_compilation_and_execution_verified"],
            }), flush=True)
    selected = [task for task in manifest["workloads"] if results[task["source_repo"]]["tool_execution_passed"]]
    all_pass = len(selected) == len(manifest["workloads"]) and bool(selected)
    report_path = HERE / "toolchain_live_root_child.json"
    report = {
        "schema_version": 1, "cpu_only": True, "qualification_scope": "live_root_child_tools",
        "manifest": str(input_path), "manifest_sha256": sha256_file(input_path),
        "projects": results, "tool_execution_passed": all_pass, "task_tests_required": False,
        "task_correctness": "agent_responsibility",
        "attempted_projects": len(manifest["workloads"]), "qualified_projects": len(selected),
        "rejected_projects": {name: result for name, result in results.items() if not result["tool_execution_passed"]},
        "original_source_integrity": integrity,
        "application_dependencies_and_project_test_success_not_claimed": True,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            PREPARED / "manifest.json", PREPARED / "preparation.json",
            Path(__file__), Path(tools.__file__),
            Path("/home/longhao/experiment/ops/typescript_toolchain_20261031/Dockerfile"),
            Path("/home/longhao/experiment/ops/typescript_toolchain_20261031/toolchain_probe.ts"),
        )},
        "GPU_action": False, "performance_accepted": False,
    }
    causal.reservation.write_json(report_path, report)
    qualified = deepcopy(manifest)
    for task in qualified["workloads"]:
        if results[task["source_repo"]]["tool_execution_passed"]:
            task["sandbox_environment"] = results[task["source_repo"]]["sandbox_environment"]
    qualified["tool_environment_qualification"] = {
        "path": str(report_path), "sha256": sha256_file(report_path),
        "base_manifest": str(input_path), "base_manifest_sha256": sha256_file(input_path),
        "qualification_scope": "live_root_child_tools", "task_tests_required": False,
    }
    qualified["capture_ready"] = False
    causal.reservation.write_json(HERE / "qualified_manifest.json", qualified)
    if not all_pass:
        raise RuntimeError("normal tool qualification failed; preserve rejected project evidence")
    print({"qualified": len(selected), "GPU_action": False, "capture_GPU_qualification_pending": True})


if __name__ == "__main__":
    main()
