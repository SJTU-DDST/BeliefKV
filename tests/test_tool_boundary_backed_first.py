from copy import deepcopy
import time

import pytest

from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle
from tests.test_full_only_native_lifecycle import full_native
from tests.test_reference_tool_replacement import run
from tests.test_tool_boundary_schedule import two_donors
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_workflow_work_oracle import add_serial_graphs, oracle_file


@pytest.mark.parametrize("locked", (False, True))
def test_settled_shorter_donor_is_considered_before_slow_cold_backup_planning(
    demand_runtime, tmp_path, monkeypatch, locked,
):
    parts, boundary, hot = two_donors(demand_runtime, tmp_path)
    runtime, admission, controller, _adder = parts
    cold = demand_runtime[4]
    raw = deepcopy(admission._work.artifact.raw)
    raw["requests"]["future-donor"]["output_tokens"] = 4000
    raw["workflows"][admission.latest["tool"].root_workflow_id]["total_work_units"] = 400001
    add_serial_graphs(raw)
    admission._work = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    admission.policy.programs["short"].tokens = 12
    cold.component_data[0].host_value = None
    runtime._native_cache.tree_core._update_duplicate_tracking(cold)
    runtime._native_cache._remember_beliefkv_unbacked_full(cold)
    hot.component_data[0].lock_ref = int(locked)
    assert [key.invocation_id for key in boundary.donor_keys()] == ["tool", "hot"]
    assert not runtime.tool_wait_hints
    clock = [time.monotonic()]
    cold_checks = []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def expensive_unusable_backup(**kwargs):
        cold_checks.append(kwargs["context_id"])
        clock[0] += .003
        return None

    monkeypatch.setattr(runtime, "_refreshed_shadow_backup_selection", expensive_unusable_backup)
    changed = run(parts)
    assert changed is not locked
    assert admission.policy.programs["short"].active is not locked
    assert admission.policy.programs["tool"].active
    assert cold.component_data[0].value is not None
    if locked:
        assert admission.policy.programs["hot"].active
        assert hot.component_data[0].value is not None
        assert cold_checks
    else:
        assert not admission.policy.programs["hot"].active
        assert hot.component_data[0].value is None
        assert not cold_checks
        assert boundary.preempted["hot"]["consumer"] == admission.latest["short"]
        assert boundary.preempted["hot"]["grant_complete"]
    assert not runtime.physical_ledger.pending_count
