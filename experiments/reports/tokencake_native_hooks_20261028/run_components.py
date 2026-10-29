"""Run only the frozen native-loop GPU scenes after the failed arm's cleanup."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
APPLICATION = ROOT.parent / ".worktrees/beliefkv-tokencake-hooks-components-20261028"
ENGINE = ROOT.parent / ".worktrees/sglang-tokencake-hooks-prepared-20261028"
FAILED = ROOT.parent / "ops/tokencake_locobench_v230_v311_20261028_r1.registration.json"
OUTPUT = HERE / "gpu_components_r1.entry.json"
RUN = HERE / "gpu_components_r1"
sys.path.insert(0, str(APPLICATION))

from beliefkv.experiments.native_engine_variant import TOKENCAKE_TREE, engine_patch
from beliefkv.experiments.prepare_ack_engine import prepare_ack_engine_compatibility
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as controls
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.qualify_paper_prefix_transfers import gpu_processes
from scripts.qualify_tokencake_runtime import validate_report
from scripts.run_agentx_baseline import prefix_qualification_environment


def main():
    if OUTPUT.exists() or RUN.exists():
        raise FileExistsError("preserve earlier native-loop GPU attempts")
    cpu_path = HERE / "bound_cpu.json"
    cpu = controls.load(cpu_path)
    for field in ("source_manifest", "report"):
        if sha256_file(Path(cpu[field]["path"])) != cpu[field]["sha256"]:
            raise ValueError("native-loop CPU evidence changed")
    if (
        cpu["totals"] != {"tests": 812, "errors": 0, "failures": 0, "skipped": 0}
        or cpu["all_prior_759_identities_retained"] is not True
    ):
        raise ValueError("current complete native-loop CPU regression is missing")
    application = git_source_state(APPLICATION)
    if application["dirty"] or sha256_file(
        APPLICATION / Path(__file__).relative_to(ROOT)
    ) != sha256_file(Path(__file__)):
        raise ValueError("component operator or frozen application differs")
    controls.comparison.verify_cpu_snapshot(
        APPLICATION, controls.load(Path(cpu["source_manifest"]["path"])),
    )
    patch = engine_patch(APPLICATION, TOKENCAKE_TREE)
    native = git_source_state(ENGINE, canonical_patch=patch)
    if (
        not native["canonical_patch"]["matches_worktree"]
        or native["canonical_patch"]["actual_tree"] != cpu["native_tree"]
    ):
        raise ValueError("component engine differs from CPU-tested hook tree")
    source_path = ROOT.parent / "ops/v230_locobench_parallel_retry_source_20261026.registration.json"
    compatibility = prepare_ack_engine_compatibility(
        ENGINE, controls.load(source_path), candidate_tree=TOKENCAKE_TREE,
    )
    previous, state = controls.load(FAILED), controls.load(FAILED.with_suffix(".state.json"))
    cleanup_path = Path(previous["run_root"]) / "tokencake_paper_observed/owned_process_cleanup.json"
    cleanup = controls.load(cleanup_path)
    if (
        state["status"] != "stopped_for_investigation"
        or cleanup.get("remaining_processes") != [] or not cleanup.get("finished_at")
        or any(controls.reservation.alive(row) for row in (
            state["watcher"], state["server"], state["driver"], *cleanup["identities"],
        ))
    ):
        raise ValueError("failed predecessor has not actually exited and cleaned")
    plan = {
        "hold_file": previous["hold_file"], "port": 18704,
        "reference_environment": {
            "CUDA_HOME": "/home/longhao/experiment/toolchains/cuda130_sampling_20261024/nvidia/cu13",
            "CUDA_VISIBLE_DEVICES": "0",
        },
    }
    check_reservation()
    for ordinal in range(2):
        controls.fresh.check_hold(plan, released=True)
        if not controls.qualified.resources(plan["port"])["ready"]:
            raise ValueError("GPU component resources are unavailable")
        if ordinal == 0:
            time.sleep(10.)
    inputs = {
        str(path): sha256_file(path) for path in (
            Path(__file__), cpu_path, Path(cpu["report"]["path"]),
            Path(cpu["source_manifest"]["path"]), FAILED, FAILED.with_suffix(".state.json"),
            cleanup_path, patch, source_path,
        )
    }
    command = [
        sys.executable, str(APPLICATION / "scripts/qualify_tokencake_runtime.py"), "run",
        "--engine", str(ENGINE), "--native-patch", str(patch), "--output-dir", str(RUN),
    ]
    record = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "application": application, "native_engine": native, "engine_checkout": str(ENGINE),
        "native_patch": str(patch), "engine_tree": TOKENCAKE_TREE,
        "engine_compatibility": compatibility,
        "command": command, "inputs_sha256": inputs, "performance_accepted": False,
    }
    child, identities = None, []
    try:
        with HERE.joinpath("gpu_components_r1.log").open("x") as log:
            child = subprocess.Popen(
                command, cwd=APPLICATION, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                env=prefix_qualification_environment(plan, os.environ),
            )
            identity = controls.reservation.process(child.pid)
            if identity is None:
                raise ValueError("actual native-loop GPU process identity is missing")
            identities.append(identity)
            record["component_process"] = identity
            deadline = time.monotonic() + 180.
            while child.poll() is None:
                controls.fresh.check_hold(plan, released=True)
                identities[:] = controls.owned_processes(identities)
                if time.monotonic() >= deadline:
                    raise TimeoutError("native-loop GPU scenes exceeded 180 seconds")
                time.sleep(.5)
        record["returncode"] = child.wait()
        if record["returncode"]:
            raise ValueError("actual native-loop GPU scenes failed; preserve original evidence")
        report_path = RUN / "summary.json"
        report = controls.load(report_path)
        validate_report(
            report, engine_tree=TOKENCAKE_TREE, patch_sha256=sha256_file(patch), root=APPLICATION,
        )
        if report["owned_gpu_pid"] != child.pid or child.pid in gpu_processes():
            raise ValueError("native-loop GPU process or actual report identity differs")
        if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
            raise ValueError("qualification input changed during execution")
        if (
            application != git_source_state(APPLICATION)
            or native != git_source_state(ENGINE, canonical_patch=patch)
        ):
            raise ValueError("actual native-loop sources changed during GPU scenes")
        record.update(
            status="native_scheduler_loop_components_qualified",
            actual_component_exit_verified=True, owned_gpu_process_absent=True,
            summary={"path": str(report_path), "sha256": sha256_file(report_path)},
        )
    except BaseException as error:
        record.update(status="failed", error=repr(error))
    finally:
        if child is not None:
            controls.cleanup(HERE / "gpu_components_r1_cleanup", identities, record["status"])
            child.wait(timeout=10.)
        controls.reservation.write_json(OUTPUT, record)
    print(json.dumps({
        name: record.get(name) for name in (
            "status", "returncode", "actual_component_exit_verified", "error",
        )
    }, indent=2))
    if record["status"] != "native_scheduler_loop_components_qualified":
        raise ValueError("native-loop components are not qualified")


if __name__ == "__main__":
    main()
