import asyncio
from copy import deepcopy
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import NativeReplayCompletion, execute_causal_replay
from beliefkv.experiments.replay_arrivals import (
    arrival_offsets_ms,
    permuted_poisson_arrival_plan,
    poisson_arrival_plan,
    substitute_background_arrivals,
    validate_arrival_plan,
)
from beliefkv.experiments.workflow_cohort import declare_cohort, validate_cohort
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from tests.test_causal_replay_arrivals import two_workflows


def source():
    return permuted_poisson_arrival_plan(
        [str(index) for index in range(10)],
        rate_per_second=1 / 70, seed=35, permutation_seed=148, initial_workflows=3,
    )


def replacement(plan, index=7, new="new-background"):
    return {
        "old_workflow_id": plan["arrival_schedule"][index]["workflow_id"],
        "new_workflow_id": new,
    }


def test_background_identity_changes_keep_exact_foreground_and_all_poisson_slots():
    original = source()
    saved = deepcopy(original)
    replacements = [replacement(original, 9, "second"), replacement(original)]
    plan = substitute_background_arrivals(
        original, replacements, preserved_prefix_workflows=6,
    )
    assert original == saved
    assert plan["arrival_schedule"][:6] == original["arrival_schedule"][:6]
    assert [row["offset_seconds"] for row in plan["arrival_schedule"]] == [
        row["offset_seconds"] for row in original["arrival_schedule"]
    ]
    assert plan["original_plan"] == original
    assert plan["replacements"] == list(reversed(replacements))
    assert plan["arrival_schedule_sha256"] != original["arrival_schedule_sha256"]
    assert validate_arrival_plan(
        plan, list(reversed([row["workflow_id"] for row in plan["arrival_schedule"]])),
    ) == plan
    original["arrival_schedule"][0]["workflow_id"] = "changed-later"
    assert plan["original_plan"] == saved


@pytest.mark.parametrize("initial", (0, 3))
def test_plain_poisson_identity_order_is_retained_without_reshuffling(initial):
    original = poisson_arrival_plan(
        ["z", "y", "x", "w", "v"], rate_per_second=.1, seed=9, initial_workflows=initial,
    )
    plan = substitute_background_arrivals(
        original, [{"old_workflow_id": "v", "new_workflow_id": "new"}],
        preserved_prefix_workflows=3,
    )
    assert [row["workflow_id"] for row in plan["arrival_schedule"]] == ["z", "y", "x", "w", "new"]
    assert validate_arrival_plan(plan, ["new", "w", "x", "y", "z"]) == plan


@pytest.mark.parametrize("prefix", (None, True, 0, 2, 10, 11, 6.0))
def test_invalid_or_initial_population_truncating_prefixes_are_rejected(prefix):
    original = source()
    with pytest.raises(ValueError):
        substitute_background_arrivals(
            original, [replacement(original)], preserved_prefix_workflows=prefix,
        )


@pytest.mark.parametrize("bad", (
    "empty", "protected", "unknown-old", "existing-new", "same-new",
    "duplicate-old", "duplicate-new", "empty-new", "extra-field", "not-a-record",
))
def test_replacements_cannot_move_foreground_or_reuse_original_identity(bad):
    original = source()
    item = replacement(original)
    replacements = [item]
    if bad == "empty":
        replacements = []
    elif bad == "protected":
        replacements = [replacement(original, 5)]
    elif bad == "unknown-old":
        item["old_workflow_id"] = "absent"
    elif bad == "existing-new":
        item["new_workflow_id"] = original["arrival_schedule"][8]["workflow_id"]
    elif bad == "same-new":
        item["new_workflow_id"] = item["old_workflow_id"]
    elif bad == "duplicate-old":
        replacements.append({**item, "new_workflow_id": "another"})
    elif bad == "duplicate-new":
        replacements.append(replacement(original, 9))
    elif bad == "empty-new":
        item["new_workflow_id"] = ""
    elif bad == "extra-field":
        item["delay"] = 1
    else:
        replacements = ["old-to-new"]
    with pytest.raises(ValueError):
        substitute_background_arrivals(
            original, replacements, preserved_prefix_workflows=6,
        )


@pytest.mark.parametrize("tamper", ("slot", "foreground", "seed", "original", "digest", "order"))
def test_registration_rejects_changed_times_foreground_or_provenance(tamper):
    original = source()
    plan = substitute_background_arrivals(
        original, [replacement(original, 9, "second"), replacement(original)],
        preserved_prefix_workflows=6,
    )
    ids = [row["workflow_id"] for row in plan["arrival_schedule"]]
    if tamper == "slot":
        plan["arrival_schedule"][-1]["offset_seconds"] += 1
    elif tamper == "foreground":
        plan["arrival_schedule"][5]["workflow_id"] = "other"
    elif tamper == "seed":
        plan["seed"] += 1
    elif tamper == "original":
        plan["original_plan"]["arrival_schedule"][0]["workflow_id"] = "other"
    elif tamper == "digest":
        plan["arrival_schedule_sha256"] = "0" * 64
    else:
        plan["replacements"].reverse()
    with pytest.raises(ValueError):
        validate_arrival_plan(plan, ids)


def test_substituted_population_must_match_the_executable_source():
    original = source()
    plan = substitute_background_arrivals(
        original, [replacement(original)], preserved_prefix_workflows=6,
    )
    with pytest.raises(ValueError):
        validate_arrival_plan(plan, [row["workflow_id"] for row in original["arrival_schedule"]])


def test_fixed_cohort_and_complete_tail_protocol_survive_background_substitution():
    original = source()
    start, end = (original["arrival_schedule"][index]["offset_seconds"] for index in (3, 5))
    before = declare_cohort(
        original, warmup=3, measured=3, throughput_start_s=start, throughput_end_s=end,
        stop_after_measured=True,
    )
    changed = substitute_background_arrivals(
        original, [replacement(original)], preserved_prefix_workflows=6,
    )
    after = declare_cohort(
        changed, warmup=3, measured=3, throughput_start_s=start, throughput_end_s=end,
        stop_after_measured=True,
    )
    validate_cohort(after, changed)
    for key in (
        "warmup_workflow_ids", "measured_workflow_ids", "throughput_window_offset_seconds",
        "termination_rule", "complete_JCT_including_normal_tails",
    ):
        assert before[key] == after[key]
    assert before["arrival_plan_sha256"] != after["arrival_plan_sha256"]
    assert after["background_workflow_ids"] == [
        "new-background" if key == replacement(original)["old_workflow_id"] else key
        for key in before["background_workflow_ids"]
    ]
    with pytest.raises(ValueError):
        validate_cohort(before, changed)


def test_actual_replay_delivers_replacement_without_removed_workflow():
    original = poisson_arrival_plan(
        ["w", "removed"], rate_per_second=1e9, seed=35, initial_workflows=1,
    )
    arrivals = substitute_background_arrivals(
        original, [{"old_workflow_id": "removed", "new_workflow_id": "b"}],
        preserved_prefix_workflows=1,
    )
    validate_arrival_plan(arrivals, ["w", "b"])
    observed = []
    plan = two_workflows()

    async def emit(event):
        observed.append(event)

    async def serve(request, _submit):
        return NativeReplayCompletion(
            request, time.monotonic() * 1000., local_monotonic_clock_domain(),
            *plan.request_usage[request],
        )

    result = asyncio.run(execute_causal_replay(
        plan, serve, emit, workflow_arrival_offsets_ms=arrival_offsets_ms(arrivals),
        workflow_release_order=["w", "b"],
    ))
    starts = [event for event in observed if event.kind is Kind.WORKFLOW_START]
    assert [event.workflow_id for event in starts] == ["w", "b"]
    assert starts[1].ts_ms - starts[0].ts_ms == pytest.approx(
        arrivals["arrival_schedule"][1]["offset_seconds"] * 1000., abs=1e-5,
    )
    assert result["completed_requests"] == len(plan.request_usage)
    assert result["replay_timing"]["shifts"] == []
