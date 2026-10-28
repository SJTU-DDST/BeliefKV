"""Match complete decode shapes and retain sparse prefill CPU measurements."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal


def main():
    output = HERE / "batch_cost_review.json"
    if output.exists():
        raise FileExistsError("preserve original complete batch-cost evidence")
    complete_path = HERE / "complete_review.json"
    service_path = HERE / "complete_service_interleaving.json"
    demand_path = HERE / "complete_demand_h2d_service.json"
    complete, services, demand = (
        causal.load(path) for path in (complete_path, service_path, demand_path)
    )
    if (
        not complete["exact_work_and_original_stopping_verified"]
        or not services["same_exact_request_work_verified"]
        or not complete["two_fresh_idle_snapshots_and_actual_owned_exit_verified"]
    ):
        raise ValueError("complete exact work and actual owned exit are required")
    helper = (
        ROOT / "experiments/reports/tool_offload_before_admission_20261027/"
        "control_regression_audit.py"
    )
    spec = importlib.util.spec_from_file_location("complete_demand_batch_cost", helper)
    prior = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prior)
    raw, groups = {}, {}
    for name, service in services["arms"].items():
        raw[name], groups[name] = prior.raw_worker_review(service)
    matched = {
        name: prior.balance(groups[name], groups["Full"])
        for name in ("Preadmission", "ThunderAgent")
    }
    full = demand["native_legacy_accounting_scope"]
    inputs = {
        str(path): sha256_file(path) for path in (
            Path(__file__), helper, complete_path, service_path, demand_path,
        )
    }
    inputs.update(prior.SOURCES)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete raw worker shapes and actual sparse prefill turnover CPU samples.",
        "raw_worker_review": raw,
        "balanced_decode_worker_intervals": matched,
        "current_worker_totals": full["workers"],
        "current_staging_counts": demand["staged_native_demand"]["counts"],
        "inputs_sha256": inputs,
        "historical_incremental_control_cpu_known": False,
        "exposed_control_delay_measured": False,
        "performance_accepted": False,
        "limitations": [
            "Bins match batch size, 2048-token mean context and graph mode; they are not identical batches.",
            "Each common bin has equal min-support weights on both arms; unmatched data remain in full metrics.",
            "Worker intervals include runtime and transfer interference, not pure CUDA kernels.",
            "Prefill CPU samples have different populations and phases may nest; no whole-run extrapolation.",
            "Staging can move selection and restoration outside an eventual prefill sample.",
            "No wall/thread measurement here establishes incremental exposed control CPU or causal saved JCT.",
        ],
    }
    causal.reservation.write_json(output, result)
    print({
        "balanced_decode_worker_intervals": matched,
        "current_worker_totals": full["workers"],
    })


if __name__ == "__main__":
    main()
