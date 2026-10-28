"""Run the unchanged known-case classifier with the exact reviewed demand engine."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path


HERE = Path(__file__).resolve().parent


def main():
    output = HERE / "complete_guard_cases.json"
    if output.exists():
        raise FileExistsError("preserve original known-case evidence")
    helper = HERE / "complete_review.py"
    spec = importlib.util.spec_from_file_location("demand_pipeline_guard_contract", helper)
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    from scripts import audit_tool_eos_guard_cases as guard

    case_path = (
        review.ROOT / "experiments/reports/reference_join_siblings_20261027/"
        "tool_closer_trigger_audit.json"
    )
    prior = review.causal.load(case_path)
    previous_path = Path(prior["registration"])
    previous, plan = review.causal.load(previous_path), review.causal.load(review.FULL)
    complete_path = HERE / "complete_review.json"
    complete = review.causal.load(complete_path)
    review.completed(plan, review.FULL, "full", review.DEMAND_PREFILL_PIPELINE_TREE)
    if (
        previous["engine_tree"] != review.COMMON_TREE
        or review.common_contract(previous) != review.common_contract(plan)
        or complete["proofs"]["Full (demand pipeline)"]["code_commit"] != plan["code_commit"]
        or complete["two_fresh_idle_snapshots_and_actual_owned_exit_verified"] is not True
    ):
        raise ValueError("known-case engine pair, complete work or common timing contract differs")
    review.causal.comparison.verify_sources(previous)
    cases = [
        row for row in prior["rows"]
        if row["status"] == "intermediate_request_observed"
        and row["native_tool_delimiter_evidence"] == "closer_without_opener"
    ]
    arm = Path(plan["run_root"]) / "full"
    reader = guard.SealedRecords()
    receipts = list(reader(arm / "replay/native_requests.jsonl"))
    count = guard.validate_receipts(
        receipts, review.causal.receipt_work(
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
        raise ValueError("complete known-case receipt or unchanged guard behavior failed")
    tokenizer = Path(plan["source_server_parameters"]["model_path"]) / "tokenizer_config.json"
    if review.sha256_file(tokenizer) != prior["tokenizer_config_sha256"]:
        raise ValueError("known-case tool delimiter vocabulary changed")
    for source, seal in reader.prefixes.items():
        if Path(source).stat().st_size != seal["complete_prefix_bytes"] or (
            review.sha256_file(Path(source)) != seal["complete_prefix_sha256"]
        ):
            raise ValueError("complete known-case inputs are not stable")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Unchanged four-case classifier with an exact reviewed native-engine pair.",
        **classified, "exact_completed_receipts": count,
        "reviewed_engine_pair": {
            "previous": review.COMMON_TREE, "current": review.DEMAND_PREFILL_PIPELINE_TREE,
        },
        "all_other_common_fields_verified": list(review.COMMON_FIELDS),
        "tool_overlay_path_only_difference_allowed": True,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {
            str(path): review.sha256_file(path) for path in (
                Path(__file__), helper, Path(guard.__file__), review.FULL, previous_path,
                case_path, complete_path, tokenizer,
            )
        },
        "production_guard_or_classifier_changed": False,
        "performance_accepted": False,
    }
    review.causal.reservation.write_json(output, result)
    print({"exact_completed_receipts": count, **classified["summary"]})


if __name__ == "__main__":
    main()
