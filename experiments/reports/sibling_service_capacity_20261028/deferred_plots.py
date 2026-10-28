"""Render complete matched-work diagnostics after the owned Full actually exits."""

from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT.parent / "ops"
FULL = OPS / "sibling_service_capacity_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
STATE = OPS / "sibling_service_capacity_20261028_r1.plots.state.json"
OUTPUT = ROOT / "experiments/figures/sibling_service_capacity_20261028_r1"
PLOT_PYTHON = OPS / "cpu_plotting_20261027/bin/python"

plan = json.loads(FULL.read_bytes())
sys.path.insert(0, plan["execution_checkout"])
from beliefkv.experiments.native_engine_variant import COMMON_TREE, SIBLING_CAPACITY_TREE
from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal


def validate_plans():
    full, control = causal.load(FULL), causal.load(CONTROL)
    if (
        full["order"] != ["full"] or control["order"] != ["thunderagent_acting_decay"]
        or full["engine_tree"] != SIBLING_CAPACITY_TREE
        or control["engine_tree"] != COMMON_TREE
    ):
        raise ValueError("undeclared arm or native engine pair")
    for field in (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details",
        "sampling_mode", "stream_response_metrics", "request_timeout_s", "max_running_requests",
    ):
        if full.get(field) != control.get(field):
            raise ValueError("comparison contract differs: " + field)
    if full["quality_exclusions"]:
        raise ValueError("normal tails were excluded")
    overlays = []
    for current in (full, control):
        causal.comparison.verify_sources(current)
        overlay = current["tool_timing_overlay"]
        if sha256_file(Path(overlay["path"])) != overlay["sha256"]:
            raise ValueError("tool timing artifact changed")
        overlays.append({key: value for key, value in overlay.items() if key != "path"})
    if overlays[0] != overlays[1]:
        raise ValueError("tool timing contents or provenance differ")
    return full, control


def released(current, path):
    return causal.predecessor_ready({**current, "after_registration": str(path)})


def render():
    from scripts import plot_causal_decode_diagnostics as plotting

    full, control = validate_plans()
    if OUTPUT.exists():
        raise FileExistsError("preserve previous figures")
    if not released(full, FULL) or not released(control, CONTROL):
        raise ValueError("figures require complete success and actual owned exit")
    arms = {
        "Full (sibling capacity)": plotting.extract(
            "Full (sibling capacity)", FULL, Path(full["run_root"]) / "full", bin_s=60,
        ),
        "ThunderAgent decay": plotting.extract(
            "ThunderAgent decay", CONTROL,
            Path(control["run_root"]) / "thunderagent_acting_decay", bin_s=60,
        ),
    }
    # Keep both original engine identities in the saved evidence. Only the
    # exact reviewed pair above is omitted from the common-contract comparison.
    plotting.require_matching({
        label: {
            "match_contract": {
                key: value for key, value in arm["match_contract"].items()
                if key != "engine_tree"
            },
        } for label, arm in arms.items()
    })
    OUTPUT.mkdir(parents=True)
    figures = plotting.render(
        arms, OUTPUT, bin_s=60,
        title="Same V311 work: Full (sibling capacity engine) vs ThunderAgent decay (common engine)",
    )
    for name, key in (("time_bins.csv", "bins"), ("geometry.csv", "geometry")):
        plotting.write_csv(OUTPUT / name, [
            {"arm": label, **row} for label, arm in arms.items() for row in arm[key]
        ])
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "complete_same_work_with_explicit_native_engine_difference",
        "native_engines": {
            "Full (sibling capacity)": SIBLING_CAPACITY_TREE,
            "ThunderAgent decay": COMMON_TREE,
        },
        "isolated_module_ablation": False,
        "bin_seconds": 60, "arms": arms, "figures": figures,
        "registration_sha256": {str(path): sha256_file(path) for path in (FULL, CONTROL)},
        "operator_sha256": sha256_file(Path(__file__)),
        "plotting_operator_sha256": sha256_file(Path(plotting.__file__)),
        "performance_accepted": False,
        "limitations": [
            "The native engine and combined Full policy differ; this is not an isolated ablation.",
            "All warmup, idle and normal tails remain, with actual partial-bin widths.",
            "No post-completion zero padding or tail-based workflow selection is applied.",
            "Worker intervals include runtime and transfer dependencies, not exclusive kernels.",
        ],
    }
    with (OUTPUT / "evidence.json.gz").open("xb") as destination:
        with gzip.GzipFile(fileobj=destination, mode="wb", filename="", mtime=0) as compressed:
            compressed.write(json.dumps(evidence, indent=2, allow_nan=False).encode())


def wait():
    if STATE.exists() or OUTPUT.exists():
        raise FileExistsError("preserve previous plotting state")
    pins = {str(path): sha256_file(path) for path in (FULL, CONTROL, Path(__file__))}

    def stamp(status, **extra):
        causal.reservation.write_json(STATE, {
            "status": status, "observed_at": datetime.now(timezone.utc).isoformat(),
            "pins": pins, "gpu_work_authorized": False, "performance_accepted": False,
            **extra,
        })

    try:
        full, control = validate_plans()
        if not released(control, CONTROL):
            raise ValueError("complete reused ThunderAgent has not actually exited")
        started = time.monotonic()
        stamp("waiting_complete_full", process=causal.reservation.process(os.getpid()))
        while not released(full, FULL):
            if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
                raise ValueError("pinned plotting inputs changed")
            if time.monotonic() - started > 4 * 3600:
                raise TimeoutError("Full completion exceeded plotting wait limit")
            time.sleep(10.)
        if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
            raise ValueError("pinned plotting inputs changed before render")
        validate_plans()
        subprocess.run(
            [str(PLOT_PYTHON), str(Path(__file__)), "--render"],
            check=True,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
        )
        stamp("complete", output=str(OUTPUT))
    except BaseException as error:
        stamp("stopped_without_gpu_action", error=repr(error))
        raise


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        validate_plans()
        print("Verified matched work/timing and the exact declared engine pair.")
    elif sys.argv[1:] == ["--wait"]:
        wait()
    elif sys.argv[1:] == ["--render"]:
        render()
    else:
        raise ValueError("use --check, --wait or --render")
