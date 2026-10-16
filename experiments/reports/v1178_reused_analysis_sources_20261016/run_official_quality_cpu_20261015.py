#!/usr/bin/env python3
"""Grade frozen patches with bounded CPU resources before the V61 launch."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


ROOT = Path("/home/longhao/experiment/BeliefKV")
OPS = Path("/home/longhao/experiment/ops/quality_eval")
DEV = Path("/home/longhao/experiment/.worktrees/beliefkv-v25-followup-20261011")
HARNESS = OPS / "SWE-bench-f7bbbb"
HARNESS_COMMIT = "f7bbbb2ccdf479001d6467c9e34af59e44a840f9"
DATASET = ROOT / "workloads/raw/swebench_verified-91aa3ed"
ARROW_SHA = "0d119efe73413554335bd410a04d82fd4a586bfd312cee677ee40af5de2ac46e"
WORKLOAD_SHA = "3dffa3f8a10867d342fad8184b02364f91a9e4a92fac8b9e7d42c75fa45e82a0"
DEADLINE = datetime.fromisoformat("2026-10-15T19:45:00+08:00")
ADMISSION_DEADLINE = DEADLINE - timedelta(seconds=1920)
GRADER_RUN_ID = "beliefkv-quality-cpu-20261015"
LABEL = "beliefkv.quality_cpu_run"
SNAPSHOT = ROOT / "experiments/reports/v758_quality_cpu_environment_20261015.json"
STATE = OPS / "quality_cpu_20261015.state.json"
ARMS = {
    "v59_predictive": {
        "evaluation": ROOT / "experiments/reports/v755_v59_quality_inputs_20261015",
        "client": ROOT / "experiments/raw/qwen35_native_predictive_replenished_108plus48_20261014_v59/predictive_h2d/client_156",
    },
    "v58_native": {
        "evaluation": ROOT / "experiments/reports/v756_v58_native_quality_inputs_20261015",
        "client": ROOT / "experiments/raw/qwen35_native_predictive_replenished_108plus48_20261014_v58/native/client_156",
    },
}


def now() -> datetime:
    return datetime.now().astimezone()


def load(path: Path):
    return json.loads(path.read_text())


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path: Path, value) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def check_harness() -> None:
    head = subprocess.check_output(["git", "-C", str(HARNESS), "rev-parse", "HEAD"], text=True).strip()
    if head != HARNESS_COMMIT:
        raise RuntimeError("official evaluator revision changed")
    subprocess.run(["git", "-C", str(HARNESS), "diff", "--quiet", "HEAD"], check=True)
    import swebench
    if not Path(swebench.__file__).resolve().is_relative_to(HARNESS):
        raise RuntimeError("a different official evaluator was imported")
    if sha(Path(__file__)) != load(SNAPSHOT)["runner_sha256"]:
        raise RuntimeError("CPU runner differs from its environment snapshot")


def prepare() -> dict:
    import docker
    from swebench.harness.test_spec.test_spec import make_test_spec
    from swebench.harness.utils import load_swebench_dataset

    arrow = DATASET / "test/data-00000-of-00001.arrow"
    if sha(arrow) != ARROW_SHA:
        raise RuntimeError("frozen dataset changed")
    arms = {}
    ids = None
    for name, arm in ARMS.items():
        evaluation = arm["evaluation"]
        manifest = load(evaluation / "manifest.json")
        predictions = load(evaluation / "preds.json")
        if manifest["source_workload_manifest_sha256"] != WORKLOAD_SHA:
            raise RuntimeError("workload binding changed")
        if manifest["evaluation_denominator"] != 156 or set(predictions) != set(manifest["instance_ids"]):
            raise RuntimeError("quality denominator changed")
        if ids is not None and ids != manifest["instance_ids"]:
            raise RuntimeError("arms have different task order or identities")
        ids = manifest["instance_ids"]
        for filename, field in (
            ("summary.json", "source_summary_sha256"),
            ("manifest.json", "source_client_manifest_sha256"),
        ):
            if sha(arm["client"] / filename) != manifest[field]:
                raise RuntimeError("source client binding changed")
        if sha(evaluation / "preds.json") != manifest["predictions_sha256"]:
            raise RuntimeError("exported predictions changed")
        for instance_id, digest in manifest["patch_sha256_by_instance"].items():
            patch = arm["client"] / "workflows" / instance_id / "model.patch"
            if sha(patch) != digest or predictions[instance_id]["model_patch"] != patch.read_text():
                raise RuntimeError(f"saved patch changed: {instance_id}")
        arms[name] = {
            "manifest_sha256": sha(evaluation / "manifest.json"),
            "predictions_sha256": manifest["predictions_sha256"],
            "source_summary_sha256": manifest["source_summary_sha256"],
            "workflow_outcomes": dict(Counter(manifest["workflow_outcome_by_instance"].values())),
        }
    dataset = load_swebench_dataset(str(DATASET), "test", ids)
    if len(dataset) != 156:
        raise RuntimeError("dataset selection changed")
    client = docker.from_env()
    images = {}
    for row in dataset:
        spec = make_test_spec(row, namespace="swebench")
        image = client.images.get(spec.instance_image_key)
        images[spec.instance_image_key] = {
            "id": image.id,
            "repo_digests": image.attrs.get("RepoDigests", []),
        }
    snapshot = {
        "created_at": now().isoformat(),
        "runner_sha256": sha(Path(__file__)),
        "harness_commit": HARNESS_COMMIT,
        "harness_version": importlib.metadata.version("swebench"),
        "dataset_arrow_sha256": ARROW_SHA,
        "instance_ids": ids,
        "arms": arms,
        "cached_images": images,
        "python": sys.executable,
        "package_versions": {
            item.metadata["Name"]: item.version for item in importlib.metadata.distributions()
            if item.metadata.get("Name")
        },
        "limits": {
            "workers": 1,
            "nano_cpus": 1_000_000_000,
            "cpu_shares": 128,
            "memory_bytes": 8 * 1024**3,
            "task_timeout_seconds": 1800,
            "no_new_task_after": ADMISSION_DEADLINE.isoformat(),
            "hard_stop": DEADLINE.isoformat(),
        },
        "official_correctness_evaluated": False,
    }
    with SNAPSHOT.open("x") as stream:
        stream.write(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    check_harness()
    return snapshot


def worker(arm_name: str) -> None:
    import docker
    from docker.models.containers import ContainerCollection
    from docker.models.images import ImageCollection
    from swebench.harness import run_evaluation
    from swebench.harness.test_spec.test_spec import make_test_spec
    from swebench.harness.utils import load_swebench_dataset

    check_harness()
    snapshot = load(SNAPSHOT)
    arm = ARMS[arm_name]
    evaluation = arm["evaluation"]
    if sha(evaluation / "manifest.json") != snapshot["arms"][arm_name]["manifest_sha256"]:
        raise RuntimeError("export manifest changed")
    if sha(evaluation / "preds.json") != snapshot["arms"][arm_name]["predictions_sha256"]:
        raise RuntimeError("predictions changed")
    dataset = load_swebench_dataset(str(DATASET), "test", snapshot["instance_ids"])
    allowed_names = {
        make_test_spec(row, namespace="swebench").get_instance_container_name(GRADER_RUN_ID)
        for row in dataset
    }
    client = docker.from_env()
    original_create = ContainerCollection.create
    original_run = run_evaluation.run_instance

    def no_pull(*args, **kwargs):
        raise RuntimeError("independent quality evaluation uses cached images only")

    def create(collection, *args, **kwargs):
        image_key = kwargs.get("image")
        image = snapshot["cached_images"].get(image_key)
        if kwargs.get("name") not in allowed_names or image is None:
            raise RuntimeError("unexpected evaluation container")
        if now() >= DEADLINE:
            raise RuntimeError("CPU evaluation reservation ended")
        if client.images.get(image_key).id != image["id"]:
            raise RuntimeError("cached image identity changed")
        kwargs.update(
            nano_cpus=1_000_000_000,
            cpu_shares=128,
            mem_limit=8 * 1024**3,
            memswap_limit=8 * 1024**3,
            runtime="runc",
            device_requests=[],
            labels={LABEL: GRADER_RUN_ID},
            environment={
                "CUDA_VISIBLE_DEVICES": "999",
                "NVIDIA_VISIBLE_DEVICES": "void",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
            },
        )
        container = original_create(collection, *args, **kwargs)
        container.reload()
        config = container.attrs["HostConfig"]
        receipt = {
            "at": now().isoformat(),
            "id": container.id,
            "name": container.name,
            "image": container.attrs["Image"],
            "nano_cpus": config["NanoCpus"],
            "memory_bytes": config["Memory"],
            "cpu_shares": config["CpuShares"],
            "runtime": config["Runtime"],
            "device_requests": config.get("DeviceRequests"),
        }
        with (evaluation / "cpu_container_receipts.jsonl").open("a") as stream:
            stream.write(json.dumps(receipt, sort_keys=True) + "\n")
        return container

    def run_one(*args, **kwargs):
        if now() >= ADMISSION_DEADLINE:
            return {"completed": False, "resolved": False}
        spec = args[0] if args else kwargs["test_spec"]
        progress_path = evaluation / "quality_cpu_progress.json"
        write(progress_path, {"status": "grading", "instance_id": spec.instance_id, "at": now().isoformat()})
        result = original_run(*args, **kwargs)
        write(progress_path, {
            "status": "instance_finished", "instance_id": spec.instance_id,
            "result": result, "at": now().isoformat(),
        })
        return result

    ContainerCollection.create = create
    ImageCollection.pull = no_pull
    run_evaluation.run_instance = run_one
    os.chdir(evaluation)
    run_evaluation.main(
        dataset_name=str(DATASET), split="test",
        instance_ids=snapshot["instance_ids"], predictions_path=str(evaluation / "preds.json"),
        max_workers=1, force_rebuild=False, cache_level="instance", clean=False,
        open_file_limit=4096, run_id=GRADER_RUN_ID, timeout=1800,
        namespace="swebench", rewrite_reports=False, modal=False,
    )


def cleanup() -> None:
    import docker

    client = docker.from_env()
    for container in client.containers.list(all=True, filters={"label": f"{LABEL}={GRADER_RUN_ID}"}):
        container.remove(force=True)


def supervise() -> None:
    check_harness()
    with (OPS / "quality_cpu_20261015.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = {
            "status": "running_cpu_quality",
            "pid": os.getpid(),
            "start_ticks": Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()[19],
            "started_at": now().isoformat(),
            "hard_stop": DEADLINE.isoformat(),
            "arms": {},
        }
        write(STATE, state)
        for arm_name, arm in ARMS.items():
            if now() >= ADMISSION_DEADLINE:
                state["status"] = "paused_before_v61"
                break
            state["current_arm"] = arm_name
            write(STATE, state)
            task_env = dict(os.environ)
            task_env.update(CUDA_VISIBLE_DEVICES="999", OMP_NUM_THREADS="1",
                            MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
            with (arm["evaluation"] / "official_cpu_evaluation.log").open("a") as log:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "--worker", arm_name],
                    env=task_env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
                state["worker_pid"] = process.pid
                write(STATE, state)
                try:
                    while process.poll() is None and now() < DEADLINE:
                        time.sleep(5)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=15)
                    cleanup()
            state["arms"][arm_name] = {"returncode": process.returncode, "finished_at": now().isoformat()}
            state.pop("worker_pid", None)
            write(STATE, state)
            if process.returncode != 0:
                state["status"] = "evaluation_stopped"
                break
        else:
            state["status"] = "official_runs_returned_requires_report_audit"
        state["finished_at"] = now().isoformat()
        write(STATE, state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--supervise", action="store_true")
    mode.add_argument("--worker", choices=ARMS)
    args = parser.parse_args()
    os.nice(10)
    if args.prepare:
        result = prepare()
        print(json.dumps({"snapshot": str(SNAPSHOT), "cached_images": len(result["cached_images"])}))
    elif args.supervise:
        supervise()
    else:
        worker(args.worker)


if __name__ == "__main__":
    main()
