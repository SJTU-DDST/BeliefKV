"""Audit the passing CPU run and disclose the four historical parameter expansions."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.tokencake_radix_session_20261028.run_bound_cpu import case_ids
from scripts import run_causal_comparison as causal


def main():
    output = HERE / "cpu_review.json"
    if output.exists():
        raise FileExistsError("preserve original explicit CPU retention review")
    bound = HERE / "bound_cpu.json"
    evidence = causal.load(bound)
    report, manifest = (Path(evidence[name]["path"]) for name in ("report", "source_manifest"))
    if (
        evidence["totals"] != {"tests": 752, "errors": 0, "failures": 0, "skipped": 0}
        or evidence["returncode"] != 0 or evidence["unique_test_identities"] != 752
        or sha256_file(report) != evidence["report"]["sha256"]
        or sha256_file(manifest) != evidence["source_manifest"]["sha256"]
    ):
        raise ValueError("actual 752-case CPU report or sources differ")
    causal.comparison.verify_cpu_snapshot(ROOT, causal.load(manifest))
    for source, digest in evidence["inputs_sha256"].items():
        if sha256_file(Path(source)) != digest:
            raise ValueError("Agentix CPU input changed")
    native = evidence["native_engine"]
    checkout = ROOT.parent / ".worktrees/sglang-agentix-runtime-20261025"
    if git_source_state(
        checkout, canonical_patch=Path(native["canonical_patch"]["path"]),
    ) != native:
        raise ValueError("CPU-tested Agentix native state changed")
    root = ET.parse(report).getroot()
    if any(list(root.iter(tag)) for tag in ("failure", "error", "skipped")):
        raise ValueError("Agentix session CPU contains unsuccessful cases")
    prior = ROOT / "experiments/reports/agentix_pressure_all_baselines_cpu_20261025.xml"
    old, current = case_ids(prior), case_ids(report)
    mapping = {}
    for module, name, modes in (
        ("tests.test_kvflow_runtime", "test_all_and_any_observed_nested_joins_drive_different_distances", (None,)),
        ("tests.test_tokencake_runtime", "test_observed_join_inherits_live_priority_but_no_future_graph_is_read", (None,)),
        ("tests.test_workflow_agent_service_runtime",
         "test_observed_all_join_coadmits_ready_siblings_but_any_join_does_not", ("all", "any")),
    ):
        for mode in modes:
            previous = (module, name if mode is None else f"{name}[{mode}]")
            mapping[previous] = tuple(
                (module, f"{name}[{confidence if mode is None else confidence + '-' + mode}]")
                for confidence in ("observed_exact", "inferred")
            )
    if old - current != set(mapping) or not all(
        previous in old and set(expanded) <= current for previous, expanded in mapping.items()
    ):
        raise ValueError("historical CPU identity differences exceed the declared confidence expansion")
    record = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "agentix_radix_session_cpu_qualified_with_declared_parameter_expansions",
        "passing_unique_cases": len(current), "literal_prior_identities_retained": len(old & current),
        "all_prior_690_literal_identities_retained": False,
        "historical_parameter_expansions": [
            {"previous": list(previous), "current": [list(item) for item in expanded]}
            for previous, expanded in sorted(mapping.items())
        ],
        "original_strict_retention_refusal_preserved": str(bound),
        "source_manifest_rechecked": True, "actual_native_source_rechecked": True,
        "copy_and_execution_events_are_CPU_standins": True,
        "GPU_qualification": False, "formal_agentix_enabled": False, "performance_accepted": False,
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (Path(__file__), bound, report, manifest, prior)
        },
    }
    causal.reservation.write_json(output, record)
    print(json.dumps({
        name: record[name] for name in (
            "status", "passing_unique_cases", "literal_prior_identities_retained",
            "all_prior_690_literal_identities_retained", "formal_agentix_enabled",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
