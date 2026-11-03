from copy import deepcopy

import pytest

from scripts.audit_free_dispatch_service import identity
from scripts.audit_selected_workflow_service_order import summarize


def inputs(*, source="periodic_decay_resume"):
    candidates, services = [], []
    for owner, work, wait in (("short", 10., 100.), ("long", 100., 200.)):
        key = dict(
            request_id="req-" + owner, root_workflow_id="wf-" + owner, invocation_id=owner,
            context_id="ctx-" + owner, context_epoch=0, attempt_id=0,
            session_id="session-" + owner, session_generation=1,
        )
        candidates.append({"key": key, "remaining_model_work": work})
        services.append({
            "invocation_id": owner, "key": identity(key, grant=True),
            "actual_grant_verified": True, "separate_tool_replacement": False,
            "episode": {
                "selection_ms": 1001., "active_after_pressure": True,
                "first_new_useful_service_ms": wait - 1.,
                "new_service_before_next_control_or_intervention": True,
            },
        })
    audit = {"decisions": [{
        "raw_journal_sequence": 3, "monotonic_s": 1., "reference": ["long"],
        "required_JOIN_members": [], "candidates": candidates,
        "selections": {"cached": {"ratio_selected": ["short", "long"]}},
    }]}
    choice = dict(
        journal_sequence=3, monotonic_s=1., selected=["short", "long"],
        source=source, search_replayed=True, search_complete=True,
        following_periodic_grant_verified=True,
        all_periodic_service=services, all_free_service=services,
    )
    return audit, {"decisions": [choice]}


def test_exact_bounded_workflow_service_order_and_wait_populations():
    audit, choices = inputs()
    before = deepcopy((audit, choices))
    result = summarize(audit, choices, {"wf-short"})
    assert result["counts"]["shortest_selected_workflow_in_first_wave"] == 1
    assert result["selected_member_wait_ms"]["new_to_reference"]["p50"] == 100.
    assert result["selected_member_wait_ms"]["retained_reference"]["p50"] == 200.
    assert result["decisions"][0]["touches_measured"]
    assert (audit, choices) == before


@pytest.mark.parametrize("missing", ("later_window", "not_retained", "no_grant", "separate_tool"))
def test_later_or_indirect_service_never_becomes_a_bounded_size_order_claim(missing):
    audit, choices = inputs(source="undiscounted_free_dispatch")
    service = choices["decisions"][0]["all_free_service"][0]
    if missing == "later_window":
        service["episode"]["new_service_before_next_control_or_intervention"] = False
    elif missing == "not_retained":
        service["episode"]["active_after_pressure"] = False
    elif missing == "no_grant":
        service["actual_grant_verified"] = False
    else:
        service["separate_tool_replacement"] = True
    result = summarize(audit, choices, set())
    assert result["counts"]["multi_workflow_windows_with_unknown_bounded_service"] == 1
    assert result["shortest_selected_workflow_delay_ms"]["count"] == 0
    assert "shortest_selected_workflow_served_later" not in result["counts"]


def test_protected_join_workflows_are_excluded_from_short_first_comparison():
    audit, choices = inputs()
    audit["decisions"][0]["required_JOIN_members"] = ["long"]
    result = summarize(audit, choices, set())
    assert not result["decisions"]


@pytest.mark.parametrize("changed", ("identity", "selection", "work"))
def test_mismatched_attempt_selection_or_workflow_work_is_rejected(changed):
    audit, choices = inputs()
    if changed == "identity":
        services = choices["decisions"][0]["all_periodic_service"]
        services[0]["key"] = (*services[0]["key"][:5], 99, *services[0]["key"][6:])
    elif changed == "selection":
        choices["decisions"][0]["selected"] = ["long"]
    else:
        audit["decisions"][0]["candidates"][0]["remaining_model_work"] = float("nan")
    with pytest.raises(ValueError):
        summarize(audit, choices, set())
