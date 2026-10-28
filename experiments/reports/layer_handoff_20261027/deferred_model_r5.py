"""Run the frozen model retry only after the exact current Full actually exits."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import qualify_free_page_h2d_fence as common
from scripts import run_causal_comparison as causal


OPS = Path("/home/longhao/experiment/ops")
SPEC = OPS / "layer_handoff_model_r5_20261027.registration.json"
STATE = SPEC.with_suffix(".state.json")


def launch():
    if any(path.exists() for path in (SPEC, STATE, SPEC.with_suffix(".log"))):
        raise FileExistsError("preserve the queued model retry")
    common.check_reservation()
    checkout = ROOT.parent / ".worktrees/beliefkv-layer-model-269b6423"
    engine = ROOT.parent / ".worktrees/sglang-layer-model-2a927b88939c-r5"
    proof = checkout / "experiments/reports/layer_handoff_20261027"
    cpu_sources = proof / "decode_tail_shared_cpu.sources.json"
    components = proof / "gpu_components_r1.json"
    after = OPS / "tool_online_refresh_20261027_r1.registration.json"
    output = ROOT / "experiments/reports/layer_handoff_20261027/model_r5"
    model_log = output.with_suffix(".log")
    if output.exists() or model_log.exists():
        raise FileExistsError("preserve the prior model fixture output")
    causal.comparison.verify_cpu_snapshot(checkout, causal.load(cpu_sources))
    native = git_source_state(engine)
    if native != causal.load(components)["native_engine"] or native["dirty"]:
        raise ValueError("frozen model engine differs from the real component proof")
    inputs = [
        Path(__file__), after, cpu_sources, proof / "decode_tail_shared_cpu.xml",
        proof / "decode_tail_shared_cpu.evidence.json", components,
        checkout / "scripts/qualify_layer_handoff_model.py",
        checkout / "patches/sglang-layer-handoff-runtime-20261027.patch",
        checkout / "configs/migration/predictive_layer_handoff_20261027.json",
        *(checkout / path for path in (
            "beliefkv/runtime/layer_handoff.py",
            "beliefkv/runtime/predicted_tool_offload.py",
            "beliefkv/runtime/sglang_v0520_runtime.py",
        )),
    ]
    command = [
        sys.executable, str(checkout / "scripts/qualify_layer_handoff_model.py"),
        "--output-dir", str(output), "--predecessor", str(after),
        "--components", str(components), "--port", "18673",
    ]
    plan = {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "execution_checkout": str(checkout), "engine_checkout": str(engine),
        "application_commit": "269b6423",
        "native_engine": native, "cpu_sources": str(cpu_sources),
        "after_registration": str(after), "output_dir": str(output),
        "model_log": str(model_log), "port": 18673, "command": command,
        "inputs_sha256": {str(path): sha256_file(path) for path in inputs},
        "performance_accepted": False,
        "scope": "Queued numerical qualification only; no Full performance comparison.",
    }
    causal.reservation.write_json(SPEC, plan)
    with SPEC.with_suffix(".log").open("x") as log:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())], cwd=ROOT,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    result = {
        "registration": str(SPEC), "registration_sha256": sha256_file(SPEC),
        "watcher": causal.reservation.process(child.pid),
        "performance_accepted": False,
    }
    causal.reservation.write_json(SPEC.with_suffix(".launch.json"), result)
    print(json.dumps(result, indent=2))


def main():
    plan = causal.load(SPEC)
    pinned = sha256_file(SPEC)
    if STATE.exists():
        raise FileExistsError("preserve the previous model retry state")
    started = time.monotonic()
    child = None

    def stamp(status, **extra):
        causal.reservation.write_json(STATE, {
            "status": status, "observed_at": datetime.now(timezone.utc).isoformat(),
            "registration_sha256": pinned,
            "watcher": causal.reservation.process(os.getpid()),
            "performance_accepted": False, **extra,
        })

    def verify():
        common.check_reservation()
        if sha256_file(SPEC) != pinned:
            raise ValueError("queued model retry registration changed")
        if any(sha256_file(Path(path)) != value for path, value in plan["inputs_sha256"].items()):
            raise ValueError("queued model retry input changed")
        if time.monotonic() - started > 4 * 3600:
            raise TimeoutError("queued model retry exceeded four hours")
        state = git_source_state(Path(plan["engine_checkout"]))
        if state != plan["native_engine"]:
            raise ValueError("queued native engine differs from its component qualification")

    try:
        if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
            raise ValueError("GPU model supervisor must retain nice0")
        checkout = Path(plan["execution_checkout"])
        causal.comparison.verify_cpu_snapshot(checkout, causal.load(Path(plan["cpu_sources"])))
        previous = causal.load(Path(plan["after_registration"]))
        while True:
            verify()
            if causal.predecessor_ready({
                **previous, "after_registration": plan["after_registration"],
            }):
                resources = causal.qualified.resources(plan["port"])
                if resources["ready"]:
                    common.guard_gpu()
                    break
                stamp("waiting_idle_gpu", resources=resources)
            else:
                stamp("waiting_complete_full", after_registration=plan["after_registration"])
            time.sleep(10.)
        verify()
        causal.comparison.verify_cpu_snapshot(checkout, causal.load(Path(plan["cpu_sources"])))
        with Path(plan["model_log"]).open("x") as log:
            child = subprocess.Popen(
                plan["command"], cwd=checkout,
                env={
                    **os.environ, "PYTHONHASHSEED": "21", "CUDA_VISIBLE_DEVICES": "0",
                    "SGLANG_SOURCE_CHECKOUT": plan["engine_checkout"],
                },
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            identity = causal.reservation.process(child.pid)
            stamp("running_model_qualification", model_supervisor=identity)
            while child.poll() is None:
                verify()
                time.sleep(1.)
        result = causal.load(Path(plan["output_dir"]) / "report.json")
        if child.returncode != 0 or result.get("status") != "unforced_layer_handoff_model_qualified":
            raise ValueError("actual model retry failed; preserve its report")
        stamp(
            "model_qualified_performance_pending", model_exit_code=child.returncode,
            model_report_sha256=sha256_file(Path(plan["output_dir"]) / "report.json"),
        )
    except BaseException as error:
        if child is not None and child.poll() is None:
            owned = causal.owned_processes([identity])
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=45.)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10.)
            if owned:
                cleanup_dir = Path(plan["output_dir"]) / "queue_supervisor_cleanup"
                cleanup_dir.mkdir(parents=True, exist_ok=True)
                causal.cleanup(cleanup_dir, owned, "queued_model_retry_stop")
        stamp("stopped_without_performance_claim", error=repr(error))
        raise


if __name__ == "__main__":
    if sys.argv[1:] == ["--launch"]:
        launch()
    elif sys.argv[1:]:
        raise ValueError("only --launch or the registered worker is supported")
    else:
        main()
