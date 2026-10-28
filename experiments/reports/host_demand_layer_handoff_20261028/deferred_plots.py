"""Plot complete joint-policy results with an explicit reviewed engine difference."""

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
FULL = OPS / "host_demand_layer_handoff_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
STATE = OPS / "host_demand_layer_handoff_20261028_r1.plots.state.json"
OUTPUT = ROOT / "experiments/figures/host_demand_layer_handoff_20261028_r1"

# Import the same analysis version as the measured Full.
plan = json.loads(FULL.read_bytes())
sys.path.insert(0, plan["execution_checkout"])
from beliefkv.experiments.native_engine_variant import COMMON_TREE, LAYER_TREE
from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal


def validate_plans():
    full, control = causal.load(FULL), causal.load(CONTROL)
    if full["order"] != ["full"] or control["order"] != ["thunderagent_acting_decay"]:
        raise ValueError("unexpected comparison population")
    if full["engine_tree"] != LAYER_TREE or control["engine_tree"] != COMMON_TREE:
        raise ValueError("unreviewed native-engine pair")
    for field in (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details",
        "tool_timing_overlay", "sampling_mode", "stream_response_metrics",
        "request_timeout_s", "max_running_requests",
    ):
        if full.get(field) != control.get(field):
            raise ValueError("comparison contract differs: " + field)
    if full["quality_exclusions"]:
        raise ValueError("normal tails were excluded")
    for current in (full, control):
        causal.comparison.verify_sources(current)
    return full, control


def render():
    from scripts import plot_causal_decode_diagnostics as plotting

    full, control = validate_plans()
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    for path, current in ((FULL, full), (CONTROL, control)):
        if not causal.predecessor_ready({**current, "after_registration": str(path)}):
            raise ValueError("plots require complete success and actual owned exit")
    plotting.MATCH_FIELDS = tuple(field for field in plotting.MATCH_FIELDS if field != "engine_tree")
    arms = {
        "Joint Full": plotting.extract("Joint Full", FULL, Path(full["run_root"]) / "full", bin_s=60),
        "ThunderAgent decay": plotting.extract(
            "ThunderAgent decay", CONTROL,
            Path(control["run_root"]) / "thunderagent_acting_decay", bin_s=60,
        ),
    }
    plotting.require_matching(arms)
    OUTPUT.mkdir(parents=True)
    figures = plotting.render(
        arms, OUTPUT,
        title="Same V311 work: joint Full (layer engine) vs ThunderAgent decay (common engine)",
        bin_s=60,
    )
    for name, key in (("time_bins.csv", "bins"), ("geometry.csv", "geometry")):
        plotting.write_csv(OUTPUT / name, [
            {"arm": label, **row} for label, arm in arms.items() for row in arm[key]
        ])
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "same_work_timing_capacity_with_declared_reviewed_native_engine_delta",
        "native_engines": {"Joint Full": LAYER_TREE, "ThunderAgent decay": COMMON_TREE},
        "isolated_layer_handoff_ablation": False,
        "bin_seconds": 60, "arms": arms, "figures": figures,
        "operator_sha256": sha256_file(Path(__file__)),
        "registration_sha256": {str(path): sha256_file(path) for path in (FULL, CONTROL)},
        "limitations": [
            "Threshold, Host policy and layer handoff change together; no individual effect attribution.",
            "Native engine changes explicitly; the old control is not an identical-engine ablation.",
            "All normal tails and actual partial-bin widths remain. No warmup removal or zero padding.",
            "Worker intervals include runtime and waits, not pure CUDA kernel time.",
        ],
        "performance_accepted": False,
    }
    with (OUTPUT / "evidence.json.gz").open("xb") as destination:
        with gzip.GzipFile(fileobj=destination, mode="wb", filename="", mtime=0) as compressed:
            compressed.write(json.dumps(result, indent=2, allow_nan=False).encode())


def wait():
    if STATE.exists() or OUTPUT.exists():
        raise FileExistsError("preserve prior plot evidence")
    pins = {str(path): sha256_file(path) for path in (FULL, CONTROL, Path(__file__))}

    def stamp(status, **extra):
        causal.reservation.write_json(STATE, {
            "status": status, "observed_at": datetime.now(timezone.utc).isoformat(),
            "pins": pins, "gpu_work_authorized": False, "performance_accepted": False, **extra,
        })

    try:
        full, control = validate_plans()
        if not causal.predecessor_ready({**control, "after_registration": str(CONTROL)}):
            raise ValueError("reused control is incomplete")
        started = time.monotonic()
        stamp("waiting_complete_joint_full")
        while not causal.predecessor_ready({**full, "after_registration": str(FULL)}):
            if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
                raise ValueError("plot inputs changed")
            if time.monotonic() - started > 4 * 3600:
                raise TimeoutError("Full has not completed")
            time.sleep(10.)
        validate_plans()
        subprocess.run(
            [str(OPS / "cpu_plotting_20261027/bin/python"), str(Path(__file__)), "--render"],
            check=True, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
        )
        stamp("complete", output=str(OUTPUT))
    except BaseException as error:
        stamp("stopped_without_gpu_action", error=repr(error))
        raise


if __name__ == "__main__":
    if sys.argv[1:] == ["--render"]:
        render()
    elif sys.argv[1:] == ["--wait"]:
        wait()
    elif sys.argv[1:] == ["--check"]:
        validate_plans()
    else:
        raise ValueError("use --check, --wait or --render")
