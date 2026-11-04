from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from tests import test_workflow_combination_runtime as combination
from tests.test_internal_comparison import observed
from tests.test_workflow_work_distribution_runtime import distribution_configuration


def queued(monkeypatch, tmp_path, *, ranking, window_ms):
    values = distribution_configuration(tmp_path)
    path = Path(values["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw.update(
        remaining_work_ranking_enabled=ranking,
        free_dispatch_coalescing_window_ms=window_ms,
    )
    path.write_text(json.dumps(raw))
    values["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(combination, "configured", lambda _path: values)
    runtime, admission, _env = combination.runtime_with_queue(monkeypatch, tmp_path)
    admission._work._predictions.clear()
    return runtime, admission


@pytest.mark.parametrize("ranking", (False, True))
def test_learned_window_deadline_reconciles_decay_before_raw_budget_dispatch(
    monkeypatch, tmp_path, ranking,
):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime, admission = queued(
        monkeypatch, tmp_path, ranking=ranking, window_ms=2000,
    )
    try:
        active = admission.policy.programs["a"]
        assert admission._admit_from_free_capacity(
            (active,), event="agent_dispatch_free_capacity_admitted", join_id=None,
        )
        admission.completed(
            NS(reqs=[admission._live_requests["a-request"]]), ("a-request",),
        )
        runtime.on_events((replace(observed(
            20, Kind.TOOL_START, invocation_id="a",
            attributes={"tool_run_id": "slow-tool"},
        ), workflow_id="a"),))
        admission.next_tick = 100.5
        clock[0] = 100.5
        admission.tick(now=clock[0])
        assert active.active and active.acting
        assert admission.policy.remaining_capacity() == 100
        assert not admission.policy.programs["b"].active
        assert admission.next_tick == 102.5
        assert admission._deferred_dispatch.next_dispatch == 102.
        controls = admission.counts["control_ticks"]

        clock[0] = 102.
        assert admission.policy.remaining_capacity_with_decay(now=clock[0]) >= 250
        admission.tick(now=clock[0])

        assert admission.policy.programs["b"].active
        assert not active.active
        assert admission.policy.remaining_capacity() >= 0
        assert admission.counts["control_ticks"] == controls + 1
        assert admission.counts["workflow_coalescing_reconciliation_ticks"] == 1
        assert admission.next_tick == 104.
        assert admission._deferred_dispatch.next_dispatch == 104.
        assert admission.status()["workflow_coalescing_reconciles_decay_at_window_deadline"]
    finally:
        runtime.close()


@pytest.mark.parametrize("case", ("before_deadline", "empty_index", "untimed"))
def test_reconciliation_does_not_add_control_outside_a_pending_expired_window(
    monkeypatch, tmp_path, case,
):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime, admission = queued(
        monkeypatch, tmp_path, ranking=False,
        window_ms=0 if case == "untimed" else 2000,
    )
    try:
        admission.next_tick = 110.
        if case == "empty_index":
            admission._dispatch_pending.clear()
        clock[0] = 101. if case == "before_deadline" else 102.
        admission.tick(now=clock[0])
        assert admission.next_tick == 110.
        assert not admission.counts["control_ticks"]
        assert not admission.counts["workflow_coalescing_reconciliation_ticks"]
        assert admission.status()["workflow_coalescing_reconciles_decay_at_window_deadline"] is (
            case != "untimed"
        )
    finally:
        runtime.close()
