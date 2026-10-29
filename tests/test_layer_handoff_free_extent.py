"""Real native capacity/ownership; copy events are CPU stand-ins."""

from array import array
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import torch

from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.layer_handoff import try_handoff
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.run_causal_comparison import preflight_runtime
from tests.test_full_only_native_lifecycle import full_native
from tests.test_layer_handoff import native_handoff, split_prefix
from tests.test_tool_offload_before_admission import configuration
from tests.test_tool_policy_adaptation import demand_runtime


def private_suffix(f):
    split_prefix(f, range(1, 19))
    generation = f.cache.ensure_session_generation("shared-owner")
    f.cache.session_refs.register_session_ref(NS(
        session_id="shared-owner", session_generation=generation, session=None,
        last_node=f.donor.parent.id, origin_input_ids=array("q", range(1, 19)),
        output_ids=array("q"), extra_key=None,
    ))
    assert len(f.donor.component_data[0].value) == 14
    assert f.donor.parent.component_data[0].session_ref == 2
    assert f.allocator.available_size() == 4


def settle(f, *, consumer_first=False):
    transaction = f.cache._beliefkv_layer_handoff
    if consumer_first:
        transaction.completion.consumer.finish_event.done = True
        f.cache.loading_check(finish_count=1)
        f.native.reap(f.cache)
        assert f.cache._beliefkv_layer_handoff is transaction
        assert f.native.blocks_load(f.cache, f.donor.id)
    transaction.completion.donor.finish_event.done = True
    f.cache.writing_check(finish_count=1)
    if not consumer_first:
        assert f.cache._beliefkv_layer_handoff is transaction
        transaction.completion.consumer.finish_event.done = True
        f.cache.loading_check(finish_count=1)
    f.native.reap(f.cache)
    assert f.cache._beliefkv_layer_handoff is None
    f.cache.sanity_check()


@pytest.mark.parametrize("consumer_first", (False, True))
def test_free_extent_supports_a_private_donor_smaller_than_complete_restore(
    native_handoff, consumer_first,
):
    f = native_handoff
    private_suffix(f)
    assert f.native.begin(f.cache, **f.args) is None
    old_prefix = f.donor.parent.component_data[0].value.clone()
    donor_indices = f.donor.component_data[0].value.clone()
    result = f.native.begin(f.cache, **f.args, allow_free_extent=True)
    assert result["donor_tokens"] == result["reused_tokens"] == 14
    assert result["restored_tokens"] == 16 and result["free_extent_tokens"] == 2
    assert result["reserved_tail_tokens"] == 0
    assert f.allocator.available_size() == f.args["reserve_tokens"] == 2
    restored = f.consumer.component_data[0].value
    assert torch.equal(restored[:14], donor_indices)
    assert torch.equal(f.donor.parent.component_data[0].value, old_prefix)
    assert not set(restored[14:].tolist()) & set(donor_indices.tolist())
    growth = f.allocator.alloc(2)
    assert growth is not None and not set(restored.tolist()) & set(growth.tolist())
    f.allocator.free(growth)
    f.cache.sanity_check()
    settle(f, consumer_first=consumer_first)
    assert f.allocator.available_size() == 2
    assert len(f.donor.component_data[0].host_value) == 14
    assert f.donor.component_data[0].host_lock_ref == 0
    assert f.consumer.component_data[0].lock_ref == 0
    assert f.consumer.component_data[0].host_lock_ref == 0


@pytest.mark.parametrize("cause", (
    "reserve", "authorization", "allocator", "host", "shared",
    "consumer_lock", "donor_lock", "stale_session",
))
def test_free_extent_refusal_keeps_all_native_capacity_and_ownership(native_handoff, cause):
    f = native_handoff
    private_suffix(f)
    if cause == "reserve":
        f.args["reserve_tokens"] = 3
    elif cause == "authorization":
        f.args["authorize"] = lambda *_: False
    elif cause == "allocator":
        f.allocator.alloc = Mock(return_value=None)
    elif cause == "host":
        f.cache.cache_controller.mem_pool_host.available_size = lambda: 13
    elif cause == "shared":
        f.donor.component_data[0].session_ref += 1
    elif cause == "consumer_lock":
        f.consumer.component_data[0].lock_ref += 1
    elif cause == "donor_lock":
        f.donor.component_data[0].lock_ref += 1
    else:
        f.args["donor_session_generation"] += 1
    old_donor, old_consumer = f.donor.component_data[0].value, f.consumer.component_data[0].value
    assert f.native.begin(f.cache, **f.args, allow_free_extent=True) is None
    assert f.donor.component_data[0].value is old_donor
    assert f.consumer.component_data[0].value is old_consumer
    assert f.allocator.available_size() == 4
    assert len(f.host_owned) == len(f.host_released)
    assert getattr(f.cache, "_beliefkv_layer_handoff", None) is None
    f.cache.cache_controller.l2_transfer_engine.submit_layer_handoff.assert_not_called()


def test_already_fitting_consumer_does_not_create_a_donor_copy(native_handoff):
    f = native_handoff
    f.args["reserve_tokens"] = 2
    f.cache.cache_controller.mem_pool_device_allocator.available_size = lambda: 18
    assert f.native.begin(f.cache, **f.args, allow_free_extent=True) is None
    assert not f.host_owned
    f.cache.cache_controller.l2_transfer_engine.submit_layer_handoff.assert_not_called()


def test_runtime_host_budget_matches_only_the_smaller_private_donor(native_handoff):
    f = native_handoff
    private_suffix(f)
    f.runtime.layer_handoff_free_extent = True
    f.runtime._host_demand_capacity = NS(ensure=Mock(return_value=True))
    assert try_handoff(
        f.offloader, f.runtime.context_sessions["ctx-tool"],
        NS(node_id=f.donor.id, creation_time=f.donor.creation_time),
    ), dict(f.runtime.counts)
    f.runtime._host_demand_capacity.ensure.assert_called_once_with(
        14, protected=(f.donor.id, f.consumer.id),
    )
    assert f.runtime.counts["layer_handoff_reused_tokens"] == 14
    assert f.runtime.counts["layer_handoff_restored_tokens"] == 16
    assert f.runtime.counts["layer_handoff_free_extent_tokens"] == 2
    assert f.offloader.action_detail["backup_tokens_budgeted"] == 14
    assert f.offloader.action_detail["restore_tokens_budgeted"] == 14
    assert f.allocator.available_size() == 2
    settle(f)


def test_runtime_rejects_unqualified_native_capability_and_binds_preflight(monkeypatch, tmp_path):
    settings = arm_environment({
        **configuration(), "LAYER_HANDOFF": "1", "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT": "1",
    }, "full")
    result = preflight_runtime(settings, str(tmp_path / "preflight"))
    assert result["verified_configuration"]["layer_handoff_free_extent"] is True
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "old-native.sock"))
    try:
        with pytest.raises(ValueError, match="free extent requires its dedicated native engine"):
            runtime.attach_native_cache(NS(begin_beliefkv_layer_handoff=Mock()))
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", MODES)
def test_free_extent_is_full_only(mode):
    settings = arm_environment({
        **configuration(), "LAYER_HANDOFF": "1", "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT": "1",
    }, mode)
    assert settings["BELIEFKV_LAYER_HANDOFF_FREE_EXTENT"] == str(int(mode == "full"))


@pytest.mark.parametrize("method", (
    "thunderagent", "thunderagent_acting_decay", "kvflow_observed_graph",
))
def test_paper_baselines_never_inherit_the_free_extent_extension(method):
    assert paper_environment({
        **configuration(), "LAYER_HANDOFF": "1", "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT": "1",
    }, method)["BELIEFKV_LAYER_HANDOFF_FREE_EXTENT"] == "0"
