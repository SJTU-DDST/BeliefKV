"""Queue only the frozen TokenCake components after actual KVFlow cleanup."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


CODE_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = CODE_ROOT.parent.parent if CODE_ROOT.parent.name == ".worktrees" else CODE_ROOT.parent
ROOT = EXPERIMENT / "BeliefKV"
HERE = ROOT / "experiments/reports/tokencake_radix_session_20261028"
OPS = EXPERIMENT / "ops"
APPLICATION = EXPERIMENT / ".worktrees/beliefkv-tokencake-native-sessions-20261028"
REGISTRATION = OPS / "tokencake_native_sessions_20261028_r1.registration.json"
PREDECESSOR = OPS / "kvflow_locobench_v230_v311_20261028_r1.registration.json"
RUN = ROOT / "experiments/raw/tokencake_native_sessions_20261028_r1"
sys.path.insert(0, str(APPLICATION))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as controls
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.qualify_paper_prefix_transfers import gpu_processes
from scripts.qualify_tokencake_runtime import validate_report
from scripts.run_agentx_baseline import prefix_qualification_environment


def verify_cpu_evidence(path):
    cpu = controls.load(path)
    for field in ("source_manifest", "report"):
        evidence = cpu[field]
        if sha256_file(Path(evidence["path"])) != evidence["sha256"]:
            raise ValueError("TokenCake CPU " + field + " changed")
    for name, digest in cpu["prior_inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("TokenCake prior CPU evidence changed")
    if (
        cpu["totals"] != {"tests": 742, "errors": 0, "failures": 0, "skipped": 0}
        or cpu["unique_test_identities"] != 742
        or cpu["all_prior_726_identities_retained"] is not True
        or cpu["new_native_session_gpu_scenes_executed"] is not False
        or cpu["sources_unchanged_during_execution"] is not True
        or cpu["returncode"] != 0
    ):
        raise ValueError("components require the complete source-bound 742-case CPU run")
    root = ET.parse(cpu["report"]["path"]).getroot()
    cases = [(row.get("classname"), row.get("name")) for row in root.iter("testcase")]
    if len(cases) != 742 or len(set(cases)) != 742 or any(
        list(root.iter(tag)) for tag in ("failure", "error", "skipped")
    ):
        raise ValueError("TokenCake CPU XML does not contain 742 unique passing cases")
    return cpu


def verify(plan):
    if git_source_state(APPLICATION) != plan["application"]:
        raise ValueError("frozen TokenCake component application changed")
    for path, digest in plan["inputs_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("queued component evidence changed: " + path)
    cpu = verify_cpu_evidence(Path(plan["cpu_evidence"]))
    controls.comparison.verify_cpu_snapshot(
        APPLICATION, controls.load(Path(cpu["source_manifest"]["path"])),
    )
    native = git_source_state(
        Path(plan["engine_checkout"]), canonical_patch=Path(plan["native_patch"]),
    )
    if native != plan["native_engine"]:
        raise ValueError("queued component native source changed")


def register():
    if any(path.exists() for path in (
        REGISTRATION, REGISTRATION.with_suffix(".state.json"),
        REGISTRATION.with_suffix(".log"), RUN,
    )):
        raise FileExistsError("preserve previous TokenCake component registrations")
    cpu_path = HERE / "bound_cpu.json"
    cpu, previous = verify_cpu_evidence(cpu_path), controls.load(PREDECESSOR)
    application = git_source_state(APPLICATION)
    if application["dirty"]:
        raise ValueError("component application must be frozen and clean")
    operator = APPLICATION / Path(__file__).resolve().relative_to(CODE_ROOT)
    if sha256_file(operator) != sha256_file(Path(__file__)):
        raise ValueError("registration entry differs from its frozen operator")
    patch = APPLICATION / "patches/sglang-v0.5.20-beliefkv-staging.patch"
    native = git_source_state(Path(previous["engine_checkout"]), canonical_patch=patch)
    if native["canonical_patch"]["actual_tree"] != cpu["native_tree"]:
        raise ValueError("component engine differs from the CPU-tested common native tree")
    inputs = [
        cpu_path, Path(cpu["source_manifest"]["path"]), Path(cpu["report"]["path"]),
        HERE / "component_queue_cpu.xml",
        APPLICATION / "tests/test_tokencake_component_queue.py", PREDECESSOR, patch,
        APPLICATION / Path(__file__).resolve().relative_to(CODE_ROOT),
    ]
    plan = {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Twelve small real TokenCake GPU lifecycle scenes only; no LLM or performance run.",
        "application": application, "execution_checkout": str(APPLICATION),
        "cpu_evidence": str(cpu_path), "engine_checkout": previous["engine_checkout"],
        "native_patch": str(patch), "native_patch_sha256": sha256_file(patch),
        "native_engine": native, "engine_tree": cpu["native_tree"],
        "after_registration": str(PREDECESSOR), "hold_file": previous["hold_file"],
        "reference_environment": {
            "CUDA_HOME": "/home/longhao/experiment/toolchains/cuda130_sampling_20261024/nvidia/cu13",
            "CUDA_VISIBLE_DEVICES": "0",
        },
        "inputs_sha256": {str(path): sha256_file(path) for path in inputs},
        "run_root": str(RUN), "port": 18702,
        "whole_model_or_performance_qualified": False, "performance_accepted": False,
    }
    verify(plan)
    check_reservation()
    controls.reservation.write_json(REGISTRATION, plan)
    with REGISTRATION.with_suffix(".log").open("x") as log:
        child = subprocess.Popen(
            [sys.executable, str(operator), "--watch", str(REGISTRATION)],
            cwd=APPLICATION, env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(json.dumps({
        "registration": str(REGISTRATION), "watcher": controls.reservation.process(child.pid),
        "scope": plan["scope"], "gpu_experiment_started": False,
    }, indent=2))


def verify_predecessor_cleanup(previous):
    controls.comparison.verify_sources(previous)
    for label in previous["order"]:
        cleanup = controls.load(Path(previous["run_root"]) / label / "owned_process_cleanup.json")
        if cleanup.get("remaining_processes") != [] or not cleanup.get("finished_at"):
            raise ValueError("KVFlow predecessor owned cleanup is incomplete")
        if any(controls.reservation.alive(row) for row in cleanup["identities"]):
            raise ValueError("KVFlow predecessor owned process remains alive")


def watch(path):
    plan = controls.load(path)
    state_path = path.with_suffix(".state.json")
    if state_path.exists():
        raise FileExistsError("preserve earlier component execution")
    state = {
        "status": "preflight", "watcher": controls.reservation.process(os.getpid()),
        "owned": [], "whole_model_or_performance_qualified": False, "performance_accepted": False,
    }
    stopped, child, identities = False, None, []

    def stop(*_args):
        nonlocal stopped
        stopped = True

    def pulse():
        if stopped:
            raise ValueError("component waiter received a stop request")
        controls.fresh.check_hold(plan, released=True)
        identities[:] = controls.owned_processes(identities)
        state.update(
            checked_at=controls.reservation.now().isoformat(), owned=list(identities),
        )
        controls.reservation.write_json(state_path, state)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        verify(plan)
        previous = controls.load(Path(plan["after_registration"]))
        state["status"] = "waiting_kvflow_actual_exit_and_cleanup"
        while not controls.predecessor_ready({
            **previous, "after_registration": plan["after_registration"],
        }):
            pulse()
            time.sleep(10.)
        verify_predecessor_cleanup(previous)
        state["status"] = "waiting_idle_gpu_components"
        controls.wait_for_resources(plan, state, pulse=pulse)
        verify(plan)
        pulse()
        RUN.mkdir(parents=True, exist_ok=False)
        env = prefix_qualification_environment(plan, os.environ)
        command = [
            sys.executable, str(APPLICATION / "scripts/qualify_tokencake_runtime.py"), "run",
            "--engine", plan["engine_checkout"], "--native-patch", plan["native_patch"],
            "--output-dir", str(RUN / "components"),
        ]
        with (RUN / "components.log").open("x") as log:
            child = subprocess.Popen(
                command, cwd=APPLICATION, env=env, stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
            identity = controls.reservation.process(child.pid)
            if identity is not None:
                identities.append(identity)
            if identity is None or identity.get("argv") != command:
                raise ValueError("TokenCake component lacks its actual owned process identity")
            state.update(
                status="running_native_session_components",
                owned=list(identities), component_process=identity, command=command,
            )
            deadline = time.monotonic() + 180.
            while child.poll() is None:
                pulse()
                if time.monotonic() >= deadline:
                    raise TimeoutError("TokenCake components exceeded 180 seconds")
                time.sleep(.5)
        state["component_returncode"] = child.wait()
        if state["component_returncode"]:
            raise ValueError("TokenCake component failed; preserve original report and log")
        report_path = RUN / "components/summary.json"
        report = controls.load(report_path)
        validate_report(
            report, engine_tree=plan["engine_tree"], patch_sha256=plan["native_patch_sha256"],
            root=APPLICATION,
        )
        verify(plan)
        if report.get("owned_gpu_pid") != child.pid:
            raise ValueError("TokenCake GPU proof belongs to another component process")
        pulse()
        if identities:
            raise ValueError("TokenCake component exited with live owned descendants")
        if child.pid in gpu_processes():
            raise ValueError("completed TokenCake component still owns a GPU context")
        state.update(
            status="native_session_components_qualified",
            component_report={"path": str(report_path), "sha256": sha256_file(report_path)},
            owned=[], actual_component_exit_verified=True, owned_gpu_process_absent=True,
        )
    except BaseException as error:
        state.update(status="stopped_for_investigation", error=repr(error))
    finally:
        try:
            if child is not None:
                if not identities and child.poll() is None:
                    child.terminate()
                controls.cleanup(RUN, identities, state["status"])
                child.wait(timeout=10.)
        except BaseException as error:
            state.update(status="stopped_for_investigation", cleanup_error=repr(error))
        state["checked_at"] = controls.reservation.now().isoformat()
        controls.reservation.write_json(state_path, state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    args = parser.parse_args()
    watch(args.watch) if args.watch else register()
