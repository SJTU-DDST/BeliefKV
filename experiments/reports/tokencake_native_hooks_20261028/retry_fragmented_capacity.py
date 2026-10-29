"""Qualify current TokenCake repairs, then register a distinct complete retry."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
APPLICATION = ROOT.parent / ".worktrees/beliefkv-tokencake-fragmented-entry-20261029"
ENGINE = ROOT.parent / ".worktrees/sglang-tokencake-hooks-prepared-20261028"
REPORT = HERE / "fragmented_retry_20261029"
FAILED = OPS / "tokencake_locobench_native_hooks_20261028_r1.registration.json"
POSITION = OPS / "tokencake_fragmented_capacity_position_20261029_r1.registration.json"
COMPARISON = OPS / "tokencake_fragmented_capacity_20261029_r1.registration.json"
POSITION_RUN = ROOT / "experiments/raw/tokencake_fragmented_capacity_position_20261029_r1"
RUN = ROOT / "experiments/raw/tokencake_fragmented_capacity_20261029_r1"
sys.path.insert(0, str(APPLICATION))

from beliefkv.experiments.native_engine_variant import TOKENCAKE_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as controls
from scripts import run_causal_source_capture as capture
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.qualify_tokencake_runtime import validate_report
from scripts.run_agentx_baseline import prefix_qualification_environment


def main():
    if any(path.exists() for path in (REPORT, POSITION, COMPARISON, POSITION_RUN, RUN)):
        raise FileExistsError("preserve every earlier fragmented-capacity retry attempt")
    cpu_path = HERE / "fragmented_capacity_cpu.json"
    cpu = controls.load(cpu_path)
    if (
        cpu["totals"] != {"tests": 821, "errors": 0, "failures": 0, "skipped": 0}
        or cpu["all_prior_812_identities_retained"] is not True
    ):
        raise ValueError("current complete fragmented-capacity CPU evidence is missing")
    for field in ("source_manifest", "report"):
        if sha256_file(Path(cpu[field]["path"])) != cpu[field]["sha256"]:
            raise ValueError("current CPU evidence changed")
    manifest, xml = Path(cpu["source_manifest"]["path"]), Path(cpu["report"]["path"])
    application = git_source_state(APPLICATION)
    if application["dirty"] or sha256_file(
        APPLICATION / Path(__file__).relative_to(ROOT)
    ) != sha256_file(Path(__file__)):
        raise ValueError("retry entry or frozen application differs")
    controls.comparison.verify_cpu_snapshot(APPLICATION, controls.load(manifest))
    patch = engine_patch(APPLICATION, TOKENCAKE_TREE)
    native = git_source_state(ENGINE, canonical_patch=patch)
    if (
        not native["canonical_patch"]["matches_worktree"]
        or native["canonical_patch"]["actual_tree"] != TOKENCAKE_TREE
    ):
        raise ValueError("retry native engine differs from the reviewed hook tree")
    previous = controls.load(FAILED)
    state = controls.load(FAILED.with_suffix(".state.json"))
    cleanup_path = Path(previous["run_root"]) / "tokencake_paper_observed/owned_process_cleanup.json"
    cleanup = controls.load(cleanup_path)
    if (
        state["status"] != "stopped_for_investigation"
        or cleanup["remaining_processes"] or not cleanup.get("finished_at")
        or any(controls.reservation.alive(row) for row in (
            state["watcher"], state["server"], state["driver"], *state.get("owned", ()),
            *cleanup["identities"],
        ))
    ):
        raise ValueError("failed original TokenCake has not actually exited")
    agentix_run = ROOT / "experiments/reports/agentix_radix_sessions_20261028/gpu_native_consumers_r3"
    agentix = controls.load(agentix_run / "summary.json")
    agentix_cleanup = controls.load(agentix_run / "owned_process_cleanup.json")
    if (
        agentix["status"] != "four_native_consumer_gpu_checks_qualified"
        or set(agentix["reports"]) != {
            "swap_plain", "swap_native_sessions", "pressure_plain", "pressure_native_sessions",
        }
        or agentix_cleanup["remaining_processes"] or not agentix_cleanup.get("finished_at")
        or any(controls.reservation.alive(row) for row in agentix_cleanup["identities"])
    ):
        raise ValueError("independent Agentix components have not completed and cleaned")
    plan = {**previous, "port": 18707}
    authorization = check_reservation()
    for ordinal in range(2):
        controls.fresh.check_hold(plan, released=True)
        if not controls.qualified.resources(plan["port"])["ready"]:
            raise ValueError("new TokenCake qualification resources are unavailable")
        if ordinal == 0:
            time.sleep(10.)
    REPORT.mkdir()
    inputs = {
        str(path): sha256_file(path) for path in (
            Path(__file__), cpu_path, manifest, xml, patch, FAILED,
            FAILED.with_suffix(".state.json"), cleanup_path,
            agentix_run / "summary.json", agentix_run / "owned_process_cleanup.json",
            HERE / "formal_native_hooks_failure.json",
        )
    }
    record = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "application": application, "native_engine": native, "authorization": authorization,
        "cpu_cases": 821, "inputs_sha256": inputs, "performance_accepted": False,
        "same_complete_work_and_all_normal_tails": True,
        "native_and_thunderagent_reruns_registered": False,
    }
    identities = []
    try:
        component_dir = REPORT / "gpu_components"
        command = [
            sys.executable, str(APPLICATION / "scripts/qualify_tokencake_runtime.py"), "run",
            "--engine", str(ENGINE), "--native-patch", str(patch), "--output-dir", str(component_dir),
        ]
        with (REPORT / "gpu_components.log").open("x") as log:
            child = subprocess.Popen(
                command, cwd=APPLICATION, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                env=prefix_qualification_environment(plan, os.environ),
            )
            identity = controls.reservation.process(child.pid)
            if identity is None:
                raise ValueError("new TokenCake component child has no identity")
            identities.append(identity)
            record["component_process"] = identity
            deadline = time.monotonic() + 180.
            while child.poll() is None:
                controls.fresh.check_hold(plan, released=True)
                identities[:] = controls.owned_processes(identities)
                if time.monotonic() >= deadline:
                    raise TimeoutError("new TokenCake components exceeded 180 seconds")
                time.sleep(.5)
            if child.wait():
                raise ValueError("current TokenCake components failed; preserve this new attempt")
        component_path = component_dir / "summary.json"
        component = controls.load(component_path)
        validate_report(
            component, engine_tree=TOKENCAKE_TREE, patch_sha256=sha256_file(patch), root=APPLICATION,
        )
        if component["owned_gpu_pid"] != child.pid:
            raise ValueError("current GPU component belongs to another process")
        record["components"] = {"path": str(component_path), "sha256": sha256_file(component_path)}
        record["status"] = "current_twelve_gpu_scenes_qualified"
    except BaseException as error:
        record.update(status="failed", error=repr(error))
    finally:
        controls.cleanup(REPORT / "component_cleanup", identities, record["status"])
        controls.reservation.write_json(REPORT / "gpu_entry.json", record)
    if record["status"] != "current_twelve_gpu_scenes_qualified":
        raise ValueError("current TokenCake GPU qualification failed")
    if (
        application != git_source_state(APPLICATION)
        or native != git_source_state(ENGINE, canonical_patch=patch)
        or any(sha256_file(Path(path)) != digest for path, digest in inputs.items())
    ):
        raise ValueError("current TokenCake qualification sources changed")
    native_sources = REPORT / "native_sources.json"
    controls.reservation.write_json(native_sources, native)
    engine_evidence = capture.engine_evidence(ENGINE, xml, native_sources)
    config = APPLICATION / "configs/baselines/tokencake_locobench_native_hooks_20261028.json"
    command = [
        sys.executable, str(APPLICATION / "scripts/run_position_replay_calibration.py"),
        "--source-registration", str(OPS / "v230_locobench_parallel_retry_source_20261026.registration.json"),
        "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
        "--cpu-report", str(xml), "--cpu-sources", str(manifest),
        "--engine", str(ENGINE), "--engine-report", str(xml), "--engine-sources", str(native_sources),
        "--registration", str(POSITION), "--run-root", str(POSITION_RUN),
        "--comparison-registration", str(COMPARISON), "--comparison-root", str(RUN),
        "--port", str(plan["port"]), "--source-selection", "complete",
        "--source-selection-reason",
        "Complete unchanged V230/V311 work after native spatial-path and empty-upload control repairs.",
        "--experiment-config", str(config),
    ]
    result = subprocess.run(
        command, cwd=APPLICATION, capture_output=True, text=True, check=False,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
    )
    record.update(
        status="fresh_position_and_complete_retry_registered" if not result.returncode else "failed",
        position_registration=str(POSITION), comparison_registration=str(COMPARISON),
        command=command, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
        engine_evidence=engine_evidence, full_model_and_performance_pending=True,
    )
    controls.reservation.write_json(REPORT / "position_entry.json", record)
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, file=sys.stderr, end="")
        raise ValueError("fresh TokenCake position/retry registration failed")


if __name__ == "__main__":
    main()
