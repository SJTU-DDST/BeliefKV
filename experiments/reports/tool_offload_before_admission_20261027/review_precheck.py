"""Challenge the new release/service audit with missing and contradictory evidence."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve original review precheck")
    source = output.with_name("complete_review.py")
    spec = importlib.util.spec_from_file_location("pre_admission_complete_review", source)
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    arm = Path("/fixture/full")
    key = {
        "workflow_id": "workflow", "invocation_id": "agent",
        "context_id": "context", "context_epoch": 3,
    }
    baseline = {
        "admission_opportunities.jsonl": [{
            "event": "predicted_tool_kv_evicted",
            "dispatch_stage": "before_native_admission",
            "evidence": "current_prediction;settled_host;native_unlocked_private_leaf",
            "beneficiary_request_id": "request", "beneficiary_context_id": "context",
            "freed_bytes": 98304, "freed_units": {"0": 1}, "ts_ms": 20.,
        }],
        "native_requests.jsonl": [{
            "request_id": "request", "native_receipt": {
                "request_id": "request", "identity": list(key.values()),
                "session_id": "session", "session_generation": 1, "clock_domain": "clock",
            },
        }],
        "runtime_events.sglang.jsonl": [{
            **key, "kind": "llm_submit", "ts_ms": 10.,
            "attributes": {"request_id": "request", "cached_tokens_host": 1},
        }],
        "runtime_audit.jsonl": [{
            "event": "gpu_service_sample", "service_start_ts_ms": 30.,
            "monotonic_clock_domain": "clock",
            "request_samples": [{**key, "request_id": "request"}],
        }],
    }
    results = {}

    def run(name, mutate, expected):
        records = deepcopy(baseline)
        mutate(records)

        class FixtureRecords:
            prefixes = {}

            def __call__(self, path):
                return iter(records[Path(path).name])

        with patch.object(review, "SealedRecords", FixtureRecords):
            try:
                report = review.early_service(arm, 98304)
            except ValueError:
                if expected != "rejected":
                    raise
                results[name] = "rejected"
                return
        if expected == "rejected":
            raise AssertionError(f"contradictory evidence accepted: {name}")
        statuses = report["summary"]["consumer_statuses"]
        actual = (
            "verified" if statuses == {
                "completed_request_native_service_after_early_release": 1,
            } else "unknown"
        )
        if actual != expected:
            raise AssertionError((name, actual, expected))
        results[name] = actual

    run("complete_service_identity", lambda r: None, "verified")
    run("missing_service", lambda r: r["runtime_audit.jsonl"].clear(), "unknown")
    run("wrong_service_epoch", lambda r: r["runtime_audit.jsonl"][0]["request_samples"][0].update(
        context_epoch=4,
    ), "unknown")
    run("wrong_receipt_clock", lambda r: r["native_requests.jsonl"][0]["native_receipt"].update(
        clock_domain="another_clock",
    ), "unknown")
    run("missing_receipt_session", lambda r: r["native_requests.jsonl"][0]["native_receipt"].update(
        session_id=None,
    ), "unknown")
    run("service_before_release", lambda r: r["runtime_audit.jsonl"][0].update(
        service_start_ts_ms=19.,
    ), "rejected")
    run("submit_after_release", lambda r: r["runtime_events.sglang.jsonl"][0].update(
        ts_ms=21.,
    ), "rejected")
    run("corrupt_release_bytes", lambda r: r["admission_opportunities.jsonl"][0].update(
        freed_bytes=98305,
    ), "rejected")
    run("corrupt_release_units", lambda r: r["admission_opportunities.jsonl"][0].update(
        freed_units={"0": 2},
    ), "rejected")
    run("wrong_beneficiary_context", lambda r: r["admission_opportunities.jsonl"][0].update(
        beneficiary_context_id="another_context",
    ), "rejected")
    run("ambiguous_native_submit", lambda r: r["runtime_events.sglang.jsonl"].append(
        deepcopy(r["runtime_events.sglang.jsonl"][0]),
    ), "rejected")
    prior = (ROOT / "experiments/raw/locobench_reference_free_continuation_20261027_r1/full").resolve()
    prior_report = review.early_service(prior, 98304)
    if prior_report["summary"]["early_node_release_events"] != 0:
        raise AssertionError("earlier configuration unexpectedly claims the new release stage")
    inputs = {}
    review.verify_seals(prior_report, inputs, (prior,))
    results["complete_zero_release_real_arm"] = "verified"
    for name, value, allowed in (
        ("truncated_complete_seal", deepcopy(prior_report), (prior,)),
        ("foreign_complete_seal", prior_report, (arm,)),
    ):
        if name == "truncated_complete_seal":
            first = next(iter(value["input_jsonl_prefixes"].values()))
            first["complete_prefix_bytes"] -= 1
        try:
            review.verify_seals(value, {}, allowed)
        except ValueError:
            results[name] = "rejected"
        else:
            raise AssertionError(f"invalid complete seal accepted: {name}")
    result = {
        "cases": results, "passed_cases": len(results),
        "operator_sha256": sha256_file(Path(__file__)),
        "complete_review_operator_sha256": sha256_file(source),
        "complete_control_inputs_sha256": inputs,
        "scope": "CPU audit evidence boundaries; no runtime/GPU/performance qualification",
    }
    causal.reservation.write_json(output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
