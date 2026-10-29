"""Run the frozen session-aware paper prefix component after resource guards."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
APPLICATION = ROOT.parent / ".worktrees/beliefkv-kvflow-radix-session-f9f105bd-20261028"
NATIVE = ROOT.parent / ".worktrees/sglang-causal-11b84f7c3198-0b1ca5de0933"
PREDECESSOR = OPS / "demand_prefill_ack_retry_20261028_r1.registration.json"
sys.path.insert(0, str(APPLICATION))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.qualify_paper_prefix_transfers import gpu_processes, validate_report
from scripts.run_agentx_baseline import prefix_qualification_environment


def main():
    output = HERE / "gpu_components_r1"
    entry = HERE / "gpu_components_r1.entry.json"
    log = HERE / "gpu_components_r1.log"
    if any(path.exists() for path in (output, entry, log)):
        raise FileExistsError("preserve previous GPU component attempts")
    cpu_path = HERE / "bound_paper_cpu.json"
    cpu = json.loads(cpu_path.read_bytes())
    manifest_path = Path(cpu["source_manifest"]["path"])
    report_path = Path(cpu["report"]["path"])
    manifest = json.loads(manifest_path.read_bytes())
    sources = {str(path): sha256_file(path) for path in (
        Path(__file__), cpu_path, manifest_path, report_path, PREDECESSOR,
    )}
    if (
        sources[str(manifest_path)] != cpu["source_manifest"]["sha256"]
        or sources[str(report_path)] != cpu["report"]["sha256"]
        or cpu["totals"] != {"tests": 379, "errors": 0, "failures": 0, "skipped": 0}
        or cpu["unique_test_identities"] != 379
        or len(manifest) != 1868
    ):
        raise ValueError("source-bound CPU evidence is incomplete or changed")
    causal.comparison.verify_cpu_snapshot(APPLICATION, manifest)
    application = git_source_state(APPLICATION)
    if application["dirty"] or not application["commit"].startswith("f9f105bd"):
        raise ValueError("component application is not its frozen clean source")
    patch = APPLICATION / "patches/sglang-v0.5.20-beliefkv-staging.patch"
    native = git_source_state(NATIVE, canonical_patch=patch)
    if native["canonical_patch"]["actual_tree"] != cpu["native_tree"]:
        raise ValueError("prepared component engine differs from the CPU-tested native tree")
    previous = json.loads(PREDECESSOR.read_bytes())
    if not causal.predecessor_ready({**previous, "after_registration": str(PREDECESSOR)}):
        raise ValueError("previous Full supervisor or owned child is still live")
    causal.comparison.verify_sources(previous)
    authorization = check_reservation()
    for _ in range(2):
        if not causal.qualified.resources(18700)["ready"] or gpu_processes():
            raise ValueError("GPU or Host resource is unavailable before the bounded component")
    env = prefix_qualification_environment({
        "reference_environment": {
            "CUDA_HOME": "/home/longhao/experiment/toolchains/cuda130_sampling_20261024/nvidia/cu13",
            "CUDA_VISIBLE_DEVICES": "0",
        },
    }, os.environ)
    command = [
        sys.executable, str(APPLICATION / "scripts/qualify_paper_prefix_transfers.py"),
        "--engine-checkout", str(NATIVE), "--canonical-patch", str(patch),
        "--output-dir", str(output),
    ]
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "status": "preflight",
        "scope": "Frozen real GPU/Host prefix components including full-prompt radix sessions; no model or performance claim.",
        "application": application, "native_engine": native,
        "cpu_cases": 379, "matched_cpu_source_files": len(manifest),
        "authorization": authorization, "command": command, "inputs_sha256": sources,
        "whole_llm_scheduler_or_baseline_qualified": False, "performance_accepted": False,
    }
    process = None
    try:
        with log.open("x") as stream:
            process = subprocess.Popen(
                command, cwd=APPLICATION, env=env, stdout=stream, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            evidence["component_process"] = causal.reservation.process(process.pid)
            deadline = time.monotonic() + 300.
            while process.poll() is None:
                check_reservation()
                if time.monotonic() >= deadline:
                    raise TimeoutError("bounded paper-prefix GPU component exceeded 300 seconds")
                time.sleep(.25)
        evidence["returncode"] = process.returncode
        if process.returncode:
            raise ValueError("paper-prefix GPU component failed; preserve its original report and log")
        summary_path = output / "summary.json"
        summary = json.loads(summary_path.read_bytes())
        validate_report(
            summary, engine_tree=cpu["native_tree"], patch_sha256=sha256_file(patch),
        )
        causal.comparison.verify_cpu_snapshot(APPLICATION, manifest)
        if (
            application != git_source_state(APPLICATION)
            or native != git_source_state(NATIVE, canonical_patch=patch)
            or any(sha256_file(Path(path)) != digest for path, digest in sources.items())
        ):
            raise ValueError("component sources or evidence changed during execution")
        evidence.update(
            status="native_session_prefix_components_qualified",
            summary={"path": str(summary_path), "sha256": sha256_file(summary_path)},
            native_radix_session=summary["native_radix_session"],
        )
    except BaseException as error:
        evidence.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=20.)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10.)
        if process is not None:
            evidence["component_returncode"] = process.returncode
            evidence["actual_process_exited"] = process.poll() is not None
            evidence["owned_gpu_process_absent"] = process.pid not in gpu_processes()
        with entry.open("x") as stream:
            json.dump(evidence, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps({
            name: evidence.get(name) for name in (
                "status", "cpu_cases", "actual_process_exited", "owned_gpu_process_absent",
            )
        }, indent=2))


if __name__ == "__main__":
    main()
