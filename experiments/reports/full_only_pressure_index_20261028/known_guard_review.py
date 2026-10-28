"""Reuse the known-case classifier with explicit reviewed native-engine differences."""

from datetime import datetime, timezone
from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import COMMON_TREE, SIBLING_CAPACITY_TREE
from beliefkv.experiments.source_provenance import sha256_file
from scripts import audit_tool_eos_guard_cases as guard
from scripts import run_causal_comparison as causal


def main():
    output = HERE / "complete_guard_cases.json"
    if output.exists():
        raise FileExistsError("preserve original known-case evidence")
    registration = OPS / "full_only_pressure_index_20261028_r1.registration.json"
    case_path = ROOT / "experiments/reports/reference_join_siblings_20261027/tool_closer_trigger_audit.json"
    plan, prior = causal.load(registration), causal.load(case_path)
    previous_path = Path(prior["registration"])
    previous = causal.load(previous_path)
    complete_path = HERE / "complete_review.json"
    complete = causal.load(complete_path)
    if (
        complete["proofs"]["Full (pressure index)"]["code_commit"] != plan["code_commit"]
        or not complete["two_fresh_idle_snapshots_and_actual_owned_exit_verified"]
        or (previous["engine_tree"], plan["engine_tree"]) != (COMMON_TREE, SIBLING_CAPACITY_TREE)
        or not causal.predecessor_ready({**plan, "after_registration": str(registration)})
    ):
        raise ValueError("complete current work or reviewed engine pair is unverified")
    try:
        guard.audit(registration, case_path)
    except ValueError as error:
        if str(error) != "guard audit changed common work, timing or serving parameters":
            raise
        refusal = {"exception": repr(error), "traceback": traceback.format_exc()}
    else:
        raise ValueError("expected the original same-engine auditor to reject this reviewed pair")
    common = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details",
        "sampling_mode", "stream_response_metrics", "request_timeout_s", "max_running_requests",
    )
    if any(plan.get(name) != previous.get(name) for name in common):
        raise ValueError("known-case work, timing, capacity or serving contract changed")
    overlays = []
    for candidate in (plan, previous):
        causal.comparison.verify_sources(candidate)
        overlay = candidate["tool_timing_overlay"]
        if sha256_file(Path(overlay["path"])) != overlay["sha256"]:
            raise ValueError("known-case tool timing artifact changed")
        overlays.append({key: value for key, value in overlay.items() if key != "path"})
    if overlays[0] != overlays[1]:
        raise ValueError("tool timing content or provenance differs")
    cases = [
        row for row in prior["rows"]
        if row["status"] == "intermediate_request_observed"
        and row["native_tool_delimiter_evidence"] == "closer_without_opener"
    ]
    arm = Path(plan["run_root"]) / "full"
    reader = guard.SealedRecords()
    receipts = list(reader(arm / "replay/native_requests.jsonl"))
    count = guard.validate_receipts(
        receipts, causal.receipt_work(
            Path(previous["run_root"]) / "full/replay/native_requests.jsonl",
            sampling_mode="position",
        ),
    )
    classified = guard.inspect(
        cases, receipts, reader(arm / "opportunities/admission_opportunities.jsonl"),
        opener_ids=set(prior["opener_token_ids"]), closer_ids=set(prior["closer_token_ids"]),
        clock_domain=guard.local_monotonic_clock_domain(),
    )
    if count != 3674 or not classified["summary"]["all_cases_verified"]:
        raise ValueError("complete known-case native identity or guard behavior failed")
    tokenizer = Path(plan["source_server_parameters"]["model_path"]) / "tokenizer_config.json"
    if sha256_file(tokenizer) != prior["tokenizer_config_sha256"]:
        raise ValueError("known-case tool-delimiter vocabulary changed")
    for path, seal in reader.prefixes.items():
        if Path(path).stat().st_size != seal["complete_prefix_bytes"] or (
            sha256_file(Path(path)) != seal["complete_prefix_sha256"]
        ):
            raise ValueError("known-case inputs are not complete and stable")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Original four-case classifier on complete current work with a reviewed engine pair.",
        **classified, "exact_completed_receipts": count,
        "original_same_engine_auditor_refusal_preserved": refusal,
        "reviewed_engine_pair": {"previous": COMMON_TREE, "current": SIBLING_CAPACITY_TREE},
        "tool_overlay_path_only_difference_allowed": True,
        "all_other_common_fields_verified": list(common),
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (
                Path(__file__), Path(guard.__file__), registration, previous_path,
                case_path, complete_path, tokenizer,
            )
        },
        "production_guard_or_classifier_changed": False,
        "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print({"exact_completed_receipts": count, **classified["summary"]})


if __name__ == "__main__":
    main()
