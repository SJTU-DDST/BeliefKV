"""Prepare a separate dispatch candidate without changing the live mean120 pair."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.layer_free_extent_20261029.run_bound_cpu import write


def main():
    base = ROOT / "experiments/reports/arrival_mean120_20261029/full.json"
    candidate = json.loads(base.read_bytes())
    environment = candidate["common_environment"]
    environment.update({
        "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
        "BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
    })
    effective = arm_environment(environment, "full")
    if effective["BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS"] != "500":
        raise ValueError("candidate did not enable bounded Full dispatch")
    candidate["status"] = "separate_free_dispatch_cpu_qualification_pending"
    candidate["comparison_contract"].update({
        "free_dispatch_interval_ms": 500,
        "free_dispatch_ordinary_checks_per_pass": 8,
        "free_dispatch_join_checks_per_pass": 24,
        "free_dispatch_max_grants_per_pass": 4,
        "free_dispatch_new_programs": True,
        "free_dispatch_capacity": "complete_undiscounted_unused_logical_budget",
        "five_second_pressure_reconciliation_preserved": True,
        "free_dispatch_can_pause_or_copy": False,
        "donor_preflight": "exclude_shared_locked_and_other_resident_branches_before_host_budget_check",
    })
    candidate["candidate_provenance"] = {
        "base": str(base), "base_sha256": sha256_file(base),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "changed_policy": "bounded_event_and_500ms_free_budget_dispatch",
        "telemetry_delta": "bounded_selected_consumer_and_private_donor_geometry",
        "work_tokens_dependencies_tools_arrivals_capacity_seeds_and_native_engine_unchanged": True,
        "live_and_pending_original_pair_changed": False,
    }
    candidate["arrival_experiment"]["only_changed_workload_dimension"] = (
        "none_relative_to_the_original_mean120_pair"
    )
    write(HERE / "full.json", candidate)
    print(HERE / "full.json")


if __name__ == "__main__":
    main()
