from copy import deepcopy
import json

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts.prepare_sustained_oracle_admission import (
    DIAGNOSTIC_KIND, diagnostic_waivers, load_diagnostic_request,
    validate_decision, validate_workload,
)
from scripts.review_sustained_oracle import validate_declared_decision
from tests.test_sustained_oracle_preparation import reviews


def request():
    return {
        "kind": DIAGNOSTIC_KIND, "requested_by": "user", "coalescing_window_ms": 1000,
        "preserve_original_workload_rejection": True, "formal_workload_accepted": False,
        "waived_workload_checks": [
            "background_arrivals_cover_measured_tails",
            "ready_paused_supply_in_at_least_quarter_of_window_samples",
        ],
    }


def test_explicit_request_preserves_rejection_and_checks_the_same_review_bytes(tmp_path):
    path = tmp_path / "review.json"
    path.write_text("{}")
    diagnostic = request()
    decision = {
        "accepted_for_oracle_diagnostic": False,
        "failed_checks": list(diagnostic["waived_workload_checks"]),
        "checks": {
            **{name: False for name in diagnostic["waived_workload_checks"]},
            "fixed_complete_measured_JCT": True,
        },
        "inputs_sha256": {str(path.resolve()): sha256_file(path)},
    }
    before = deepcopy(decision)
    with pytest.raises(ValueError, match="phase-one"):
        validate_decision(decision, [path])
    validate_decision(decision, [path], diagnostic=diagnostic)
    assert decision == before and not decision["accepted_for_oracle_diagnostic"]
    path.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="different complete reviews"):
        validate_decision(decision, [path], diagnostic=diagnostic)


@pytest.mark.parametrize("change", ("receipt", "tail", "cohort", "diversity", "supply"))
def test_diagnostic_request_cannot_waive_complete_work_or_intrinsic_task_identity(change):
    cohort, supply, periodic, population = deepcopy(reviews())
    cohort["background_submission_coverage"]["covers_all_measured_completions"] = False
    if change == "receipt":
        cohort["service_output_matches_complete_receipts"] = False
    elif change == "tail":
        cohort["normal_tails_removed"] = True
    elif change == "cohort":
        cohort["measured_complete_workflow_JCT_ms"]["count"] = 17
    elif change == "diversity":
        population["population"]["model_work_units"]["p80_p20"] = 2.
    else:
        supply["all_measured_completed"] = False
    with pytest.raises(ValueError, match="unqualified"):
        validate_workload(cohort, supply, periodic, population, diagnostic=request())


def test_diagnostic_accepts_only_the_named_background_shortfall():
    values = deepcopy(reviews())
    values[0]["background_submission_coverage"]["covers_all_measured_completions"] = False
    result = validate_workload(*values, diagnostic=request())
    assert result["explicit_user_diagnostic"]
    assert not result["formal_workload_accepted"]


@pytest.mark.parametrize("change", ("window", "source", "accepted", "waiver", "duplicate", "preserve"))
def test_unknown_or_broader_exceptions_are_rejected(change):
    item = request()
    if change == "window":
        item["coalescing_window_ms"] = 500
    elif change == "source":
        item["requested_by"] = "automatic"
    elif change == "accepted":
        item["formal_workload_accepted"] = True
    elif change == "waiver":
        item["waived_workload_checks"].append("exact_output_receipt_conservation")
    elif change == "duplicate":
        item["waived_workload_checks"] *= 2
    else:
        item["preserve_original_workload_rejection"] = False
    with pytest.raises(ValueError, match="exception"):
        diagnostic_waivers(item)


def test_exception_is_pinned_to_the_actual_completed_control_and_rejected_decision(tmp_path):
    control, decision, declaration = (tmp_path / name for name in ("control.json", "decision.json", "request.json"))
    control.write_text("{}")
    decision.write_text("{}")
    item = {
        **request(),
        "control_registration": str(control.resolve()),
        "control_registration_sha256": sha256_file(control),
        "workload_decision": str(decision.resolve()),
        "workload_decision_sha256": sha256_file(decision),
    }
    declaration.write_text(json.dumps(item))
    assert load_diagnostic_request(declaration, control, decision) == item
    decision.write_text('{"different":true}')
    with pytest.raises(ValueError, match="another control"):
        load_diagnostic_request(declaration, control, decision)


def test_complete_comparison_accepts_only_the_unchanged_declared_diagnostic(tmp_path):
    control, decision, declaration = (tmp_path / name for name in ("control.json", "decision.json", "request.json"))
    control.write_text("{}")
    item = request()
    decision.write_text(json.dumps({
        "accepted_for_oracle_diagnostic": False,
        "failed_checks": item["waived_workload_checks"],
        "checks": {name: False for name in item["waived_workload_checks"]},
        "inputs_sha256": {},
    }))
    item.update(
        control_registration=str(control.resolve()),
        control_registration_sha256=sha256_file(control),
        workload_decision=str(decision.resolve()),
        workload_decision_sha256=sha256_file(decision),
    )
    declaration.write_text(json.dumps(item))
    provenance = {
        "workload_review": str(decision), "workload_review_sha256": sha256_file(decision),
        "diagnostic_request": {"path": str(declaration), "sha256": sha256_file(declaration)},
    }
    assert validate_declared_decision(provenance, control) == (decision, item)
    without_request = {name: value for name, value in provenance.items() if name != "diagnostic_request"}
    with pytest.raises(ValueError, match="phase-one"):
        validate_declared_decision(without_request, control)
    declaration.write_text("{}")
    with pytest.raises(ValueError, match="request changed"):
        validate_declared_decision(provenance, control)
