from copy import deepcopy

import pytest

from scripts.finish_ranking_repetition_metrics import hold_verified, readiness


def complete_service():
    return {
        "status": "complete_repeated_choice_and_path_audits",
        "completed_methods": ["oracle", "learned"],
    }


def comparisons():
    return {label: {
        "complete_measured_work_fresh_drain_and_actual_exit": True,
        "fixed_cohort_performance_target_met": False,
    } for label in ("oracle", "learned")}


def test_complete_negative_results_still_receive_CPU_metrics_and_figures():
    assert readiness(False, False, complete_service(), comparisons(), True) == "ready"


@pytest.mark.parametrize("parent,source", ((True, True), (True, False), (False, True)))
def test_never_postprocess_before_actual_registered_owner_exit(parent, source):
    assert readiness(parent, source, complete_service(), comparisons(), True) == "waiting_registered_owners"


def test_incomplete_service_or_receipts_do_not_satisfy_the_completed_group():
    service = complete_service()
    service["completed_methods"] = ["oracle"]
    assert readiness(False, False, service, comparisons(), True) == "incomplete_repetition_preserved"
    partial = comparisons()
    partial["learned"]["complete_measured_work_fresh_drain_and_actual_exit"] = False
    assert readiness(False, False, complete_service(), partial, True) == "incomplete_repetition_preserved"


def test_completed_results_cannot_replace_actual_GPU_hold_proof():
    assert readiness(False, False, complete_service(), comparisons(), False) == "waiting_GPU_hold_proof"


@pytest.mark.parametrize("change", ("auto_resume", "release", "campaign", "marker", "status"))
def test_GPU_hold_must_match_this_campaign_and_require_explicit_release(change):
    campaign = "/exact/campaign.json"
    marker = {
        "campaign": campaign, "automatic_resume_allowed": False,
        "resume_requires_explicit_user_release": True,
    }
    hold = {
        "status": "user_requested_GPU_hold_after_frozen_repetition", **marker,
        "post_repetition_hold": deepcopy(marker),
    }
    assert hold_verified(hold, marker, campaign)
    if change == "auto_resume":
        hold["automatic_resume_allowed"] = True
    elif change == "release":
        marker["resume_requires_explicit_user_release"] = False
    elif change == "campaign":
        hold["post_repetition_hold"]["campaign"] = "/other/campaign.json"
    elif change == "marker":
        marker.clear()
    else:
        hold["status"] = "released"
    assert not hold_verified(hold, marker, campaign)
