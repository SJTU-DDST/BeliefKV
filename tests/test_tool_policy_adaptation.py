"""Causal prediction, real demand gating, and isolated prepare transport."""

from array import array
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.experiments.tool_timing_overlay import (
    apply_overlay, build_categorized_overlay, fingerprint,
)
from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from beliefkv.predictor.tool_timing_prior import (
    ToolTimingPrior, build_public_prior, split_public_sessions,
)
from beliefkv.runtime.agent_working_set import AgentAdmission
from beliefkv.runtime.idle_prepare_backend import idle_prepare_backend
from beliefkv.runtime.native_transfer_service import NativeServiceEstimate
from beliefkv.runtime.predicted_tool_offload import DemandAwareToolOffload
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.run_causal_comparison import validate_timing_prior_contract
from tests.test_full_only_native_lifecycle import full_native
from tests.test_online_workload_timing import tool_event
from tests.test_sglang_v0520_runtime import req
from tests.test_tool_predictive_transfers import event, tool_runtime
from tests.test_tool_timing_overlay import named_plan
from tests.test_causal_replay import sequential_trace


def prior_fixture(tmp_path, *, duration=10_000.):
    samples = [{
        "sample_id": fingerprint(["sample", i]), "source_group": fingerprint(["session", i]),
        "tool_name": "WebSearch", "duration_ms": duration,
    } for i in range(100)]
    train, test = split_public_sessions(samples)
    raw = build_public_prior(train, test, domain="network-v2", public_source={"sha256": "a" * 64})
    path = tmp_path / "prior.json"
    path.write_text(json.dumps(raw))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return ToolTimingPrior.load(path, digest), path, raw, samples


def test_session_split_is_stable_and_independent_of_durations(tmp_path):
    _, _, raw, samples = prior_fixture(tmp_path)
    changed = [{**item, "duration_ms": 1_000_000. - i} for i, item in enumerate(samples)]
    before, after = split_public_sessions(list(reversed(changed)))
    assert {item["source_group"] for item in before} == set(raw["training_groups"])
    assert {item["source_group"] for item in after} == set(raw["evaluation_groups"])
    assert not set(raw["training_groups"]) & set(raw["evaluation_groups"])


@pytest.mark.parametrize("failure", ("sha", "overlap", "censored", "nan"))
def test_offline_prior_rejects_unpinned_or_invalid_evidence(tmp_path, failure):
    _, path, raw, _ = prior_fixture(tmp_path)
    if failure == "overlap":
        raw["evaluation_groups"] = raw["training_groups"]
    elif failure == "censored":
        raw["curves"][0]["curve"]["event_count"] = 0
    elif failure == "nan":
        raw["curves"][0]["curve"]["steps"][0][0] = float("nan")
    path.write_text(json.dumps(raw))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        ToolTimingPrior.load(path, "0" * 64 if failure == "sha" else digest)


def test_domain_prior_bootstraps_then_recent_completions_change_the_distribution(tmp_path):
    prior, _, _, _ = prior_fixture(tmp_path)
    history = OnlineWorkloadToolTiming(prior=prior)

    def observe(kind, call, **attrs):
        history.observe(tool_event(
            kind, call=call, timing_domain="network-v2",
            observed_timing_class="WebSearch", **attrs,
        ))

    observe(Kind.TOOL_START, "cold")
    cold = history.estimate("workflow", "agent", now_ms=0.)
    assert cold.scope == "offline_prior" and cold.p50_ms == 10_000.
    assert history.prefetch_supported("workflow", "agent", now_ms=0., scope=cold.scope)
    observe(Kind.TOOL_END, "cold", duration_ms=100.)
    for i in range(31):
        observe(Kind.TOOL_START, str(i))
        observe(Kind.TOOL_END, str(i), duration_ms=100.)
    observe(Kind.TOOL_START, "active")
    adapted = history.estimate("workflow", "agent", now_ms=0.)
    assert adapted.scope == "hybrid_workflow" and adapted.p50_ms == 100.
    assert adapted.p90_ms == 10_000.
    assert history.estimate("workflow", "agent", now_ms=20_000.) is None
    foreign = deepcopy(tool_event(
        Kind.TOOL_START, call="other", timing_domain="different-domain",
        observed_timing_class="WebSearch",
    ))
    foreign.invocation_id = "new-agent"
    history.observe(foreign)
    assert history.estimate("workflow", "new-agent", now_ms=0.) is None


def test_runtime_loads_domain_prior_and_keeps_legacy_artifact_exclusive(tmp_path, monkeypatch):
    prior, path, _, _ = prior_fixture(tmp_path)
    monkeypatch.setenv("BELIEFKV_ONLINE_TOOL_TIMING", "1")
    monkeypatch.setenv("BELIEFKV_TOOL_TIMING_PRIOR", str(path))
    monkeypatch.setenv("BELIEFKV_TOOL_TIMING_PRIOR_SHA256", prior.sha256)
    runtime = NativeAdmissionRuntime()
    try:
        assert runtime._online_tool_timing.prior.sha256 == prior.sha256
        assert runtime.predictor_sha256 != prior.sha256
    finally:
        runtime.close()
    with pytest.raises(ValueError, match="exclusive"):
        NativeAdmissionRuntime(
            tool_timing_artifact_path=str(path), tool_timing_sha256=prior.sha256,
            model_path="unused",
        )


@pytest.mark.parametrize("change", (None, "domain", "groups", "digest"))
def test_comparison_binds_the_prior_to_the_same_held_out_workload(tmp_path, change):
    prior, path, raw, _ = prior_fixture(tmp_path)
    overlay = {
        "timing_domain": raw["domain"], "evaluation_groups": raw["evaluation_groups"],
        "offline_prior": {"sha256": prior.sha256},
    }
    common = {
        "BELIEFKV_TOOL_TIMING_PRIOR": str(path),
        "BELIEFKV_TOOL_TIMING_PRIOR_SHA256": prior.sha256,
    }
    if change == "domain":
        overlay["timing_domain"] = "different"
    elif change == "groups":
        overlay["evaluation_groups"] = raw["training_groups"]
    elif change == "digest":
        overlay["offline_prior"]["sha256"] = "f" * 64
    if change is None:
        validate_timing_prior_contract(common, overlay)
    else:
        with pytest.raises(ValueError, match="held-out"):
            validate_timing_prior_contract(common, overlay)


def categorized_document():
    plan = named_plan(sequential_trace(original_service=0.))
    samples = [{
        "sample_id": fingerprint([category, index]), "tool_name": category,
        "source_group": fingerprint(["eval", index]), "duration_ms": value,
    } for category in ("WebFetch", "WebSearch") for index, value in enumerate((5., 10., 20.))]
    document = build_categorized_overlay(
        plan, samples, public_source={"sha256": "a" * 64},
        sample_audit={"sample_sha256": fingerprint(sorted(samples, key=lambda row: row["sample_id"]))},
        target_tool_names=["execute"], domain="network-v2",
    )
    return plan, samples, document


def test_category_is_assigned_before_duration_and_both_boundaries_get_only_observable_metadata():
    plan, samples, document = categorized_document()
    changed = apply_overlay(plan, document)
    second = build_categorized_overlay(
        plan, [{**sample, "duration_ms": 1.} for sample in samples],
        public_source={"sha256": "a" * 64},
        sample_audit={"sample_sha256": fingerprint(sorted(
            [{**sample, "duration_ms": 1.} for sample in samples],
            key=lambda row: row["sample_id"],
        ))}, target_tool_names=["execute"], domain="network-v2",
    )
    assert [row["observed_timing_class"] for row in document["entries"]] == [
        row["observed_timing_class"] for row in second["entries"]
    ]
    assert changed.summary() == plan.summary()
    assert changed.dependency_graph() == plan.dependency_graph()
    for node in changed.nodes:
        if node.event is not None and node.event.kind in (Kind.TOOL_START, Kind.TOOL_END):
            attrs = node.event.attributes
            assert attrs["tool_name"] == "execute"
            assert attrs["observed_timing_class"] in ("WebFetch", "WebSearch")
            assert attrs["timing_domain"] == "network-v2"
            assert not {"sample_id", "source_group", "sampled_duration_ms"} & attrs.keys()


@pytest.mark.parametrize("field,value", (
    ("timing_domain", "foreign"), ("observed_timing_class", "foreign"),
))
def test_categorized_overlay_rejects_foreign_classes(field, value):
    plan, _, document = categorized_document()
    document["entries"][0][field] = value
    document["entries_sha256"] = fingerprint(document["entries"])
    with pytest.raises(ValueError, match="category"):
        apply_overlay(plan, document)


@pytest.fixture
def demand_runtime(full_native):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, _, _ = full_native
    donor, _ = insert_session(cache, list(range(1, 33)), "s")
    consumer, generation = insert_session(cache, list(range(101, 117)), "consumer-session")
    insert_session(cache, list(range(201, 213)), "hot")
    for node in (donor, consumer):
        node.component_data[0].host_value = node.component_data[0].value.clone()
        cache.tree_core._update_duplicate_tracking(node)
    cache._demote(consumer.id, {})
    # Occupy capacity independently of the replay's future workload.
    extra = allocator.alloc(16)
    assert extra is not None and allocator.available_size() == 4
    runtime, hint = tool_runtime()
    runtime.enable_prepare_host = True
    runtime.tool_wait_hints[hint.key.context_id] = replace(
        hint, wait_p10_ms=5000., wait_p50_ms=10_000., wait_p90_ms=20_000.,
    )
    runtime._context_tokens[hint.key.context_id] = (0, 32)
    runtime.attach_native_cache(cache)
    request = req("consumer")
    request.origin_input_ids = array("q", range(101, 118))
    request.output_ids = array("q")
    request.session_id, request.session_generation = "consumer-session", generation
    request.extra_key = request.cache_salt = None
    request.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
    request.is_retracted = request.retracted_stain = False
    runtime.on_events((
        event(3, Kind.INVOCATION_CREATE, invocation_id="consumer", context_id="ctx-consumer"),
        event(4, Kind.LLM_SUBMIT, invocation_id="consumer", context_id="ctx-consumer",
              attributes={"request_id": "consumer"}),
    ))
    runtime.register_visible_request(request)
    runtime._capacity_waiting_queue = [request]
    controller = DemandAwareToolOffload(runtime)
    runtime._register_backed_pressure_nodes(hint.key)
    try:
        yield runtime, controller, cache, allocator, donor, request, writes
    finally:
        runtime.close()


def test_demand_gated_offload_frees_real_pages_only_until_the_waiter_fits(demand_runtime):
    runtime, controller, cache, allocator, donor, request, _ = demand_runtime
    controller.dispatch()
    assert donor.component_data[0].value is None, dict(runtime.counts)
    assert allocator.available_size() == 36
    assert donor.component_data[0].host_value is not None
    assert cache.inspect_beliefkv_reentry(request)["missing_full_tokens"] == 16
    assert runtime.counts["predicted_tool_offload_freed_component_0"] == 32
    controller.next_ms = 0.
    controller.dispatch()
    assert allocator.available_size() == 36


def test_offload_can_prepare_a_real_waiter_before_logical_budget_is_lent(demand_runtime):
    runtime, controller, cache, allocator, donor, request, _ = demand_runtime
    agents = AgentAdmission(
        runtime, strategy="thunderagent_retained", blocked_lending="backed_demand_recall",
    )
    agents.attach(cache)
    # Normalize the paper's 100-token buffer to this 64-token CPU allocator.
    agents.policy.buffer_tokens = 0
    agents.policy.capacity = agents.policy.capacity_tokens = 48
    agents.policy.arrive("tool", 32, now=0.)
    agents.policy.completed("tool", 32, now=.1)
    agents.policy.arrive("hot", 12, now=.15)
    agents.policy.begin("hot")
    agents.policy.block("tool", "long_tool", now=.2)
    agents.policy.arrive("consumer", 16, now=.3)
    agents.requests["consumer"] = runtime.visible["consumer"]
    runtime._agent_admission = agents
    assert not agents.allows(runtime.visible["consumer"])
    controller.dispatch()
    assert donor.component_data[0].value is None
    assert allocator.available_size() == 36
    assert controller.action_detail["logical_handover_budget_ms"] == 1000.
    assert agents.policy.agents["tool"].active
    assert not agents.allows(runtime.visible["consumer"])


@pytest.mark.parametrize("reason", (
    "empty", "resident", "finished", "tool_return", "no_capacity_pressure", "no_prediction",
))
def test_no_offload_without_a_live_cold_capacity_stalled_consumer(demand_runtime, reason):
    runtime, controller, cache, allocator, donor, request, _ = demand_runtime
    if reason == "empty":
        runtime._capacity_waiting_queue.clear()
    elif reason == "resident":
        cache.inspect_beliefkv_reentry = Mock(return_value={
            "checkpoint_tokens": 16, "missing_full_tokens": 0, "missing_mamba_slots": 0,
        })
    elif reason == "finished":
        request.finished = lambda: True
    elif reason == "tool_return":
        runtime.on_events((event(
            5, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
        ),))
    elif reason == "no_capacity_pressure":
        request.origin_input_ids = array("q", range(101, 103))
    else:
        runtime.tool_wait_hints.clear()
    initial = allocator.available_size()
    controller.dispatch()
    assert allocator.available_size() == initial
    assert donor.component_data[0].value is not None


def test_cost_threshold_depends_on_missing_backup_and_measured_restore(demand_runtime):
    runtime, controller, _, _, _, request, _ = demand_runtime
    controller.consumer = (request, runtime.visible["consumer"])
    key = runtime.context_sessions["ctx-tool"]
    runtime._estimate_h2d_service = Mock(return_value=NativeServiceEstimate(100., 200., 20., 8, "fixture"))
    runtime._estimate_d2h_service = Mock(return_value=NativeServiceEstimate(5000., 6000., 20., 8, "fixture"))
    assert controller._worth_offloading(key, backup_tokens=0, release_tokens=32)
    assert runtime.counts["tool_offload_threshold_us_last"] == 840_000
    assert not controller._worth_offloading(key, backup_tokens=32, release_tokens=32)
    assert runtime.counts["tool_offload_threshold_us_last"] == 6_860_000


@pytest.mark.parametrize("race", (None, "queue_disappears", "forecast_changes", "return_before_ack"))
def test_actual_prepare_rechecks_demand_and_prediction_before_enqueue_and_after_ack(
    demand_runtime, race,
):
    from tests.test_prepare_ack_allocation import native_write_start

    runtime, controller, cache, allocator, donor, request, writes = demand_runtime
    native_write_start(cache)
    donor.component_data[0].host_value = None
    cache.tree_core._update_duplicate_tracking(donor)
    cache.cache_controller._num_tokens_by_pool = lambda op: {"kv": len(op.device_indices)}
    cache.cache_controller._transfer_num_bytes = lambda op: 64 * len(op.device_indices)
    cache.host_pool_group.alloc = Mock(side_effect=AssertionError("stand-in owns Host indices"))
    cache.host_pool_group.free = Mock()
    cache.on_hicache_transfer_commit = runtime.on_native_transfer_commit
    native_write = cache.cache_controller.write

    def write(*args, **kwargs):
        if race == "queue_disappears":
            runtime._capacity_waiting_queue.clear()
        elif race == "forecast_changes":
            key = runtime.context_sessions["ctx-tool"]
            runtime.tool_wait_hints["ctx-tool"] = replace(
                runtime.tool_wait_hints["ctx-tool"], wait_p10_ms=1., wait_p50_ms=2., wait_p90_ms=3.,
            )
        return native_write(*args, **kwargs)

    cache.cache_controller.write = write
    controller.dispatch()
    assert allocator.available_size() == 4
    if race in ("queue_disappears", "forecast_changes"):
        assert not writes
        assert not runtime.physical_ledger.pending_count
    else:
        assert len(writes) == 1
        assert runtime.physical_ledger.pending_action_count("PREPARE_HOST") == 1
        if race == "return_before_ack":
            runtime.on_events((event(
                5, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
            ),))
        cache.writing_check(finish_count=1)
        assert not runtime.physical_ledger.pending_count
        controller.next_ms = 0.
        controller.dispatch()
        assert allocator.available_size() == (4 if race else 36)
    assert not runtime.physical_disabled


def direct_fixture():
    controller = NS(
        io_backend="kernel", l2_transfer_engine=NS(io_backend="kernel"),
        mem_pool_host=NS(layout="layer_first", entry_map={
            "kv": NS(host_pool=NS(layout="layer_first")),
        }), write_queue=[], load_queue=[], ack_write_queue=[], ack_load_queue=[],
    )
    return NS(tree_components=(0,), cache_controller=controller)


@pytest.mark.parametrize("raises", (False, True))
def test_background_direct_scope_restores_normal_backend_including_exceptions(raises):
    cache, counts = direct_fixture(), Counter()
    try:
        with idle_prepare_backend(cache, "direct", counts) as selected:
            assert selected
            assert cache.cache_controller.io_backend == "direct"
            assert cache.cache_controller.l2_transfer_engine.io_backend == "direct"
            if raises:
                raise RuntimeError("copy failure")
    except RuntimeError:
        assert raises
    assert cache.cache_controller.io_backend == "kernel"
    assert cache.cache_controller.l2_transfer_engine.io_backend == "kernel"


@pytest.mark.parametrize("reason", ("layout", "hybrid", "busy", "storage"))
def test_direct_prepare_keeps_native_backend_for_unsupported_or_busy_paths(reason):
    cache, counts = direct_fixture(), Counter()
    if reason == "layout":
        cache.cache_controller.mem_pool_host.layout = "page_first"
    elif reason == "hybrid":
        cache.tree_components = (0, 2)
    elif reason == "busy":
        cache.cache_controller.ack_write_queue.append(object())
    else:
        cache.cache_controller.enable_storage = True
    with idle_prepare_backend(cache, "direct", counts) as selected:
        assert not selected
        assert cache.cache_controller.io_backend == "kernel"


@pytest.mark.parametrize("arm", ("thunderagent", "thunderagent_acting_decay", "kvflow_observed_graph"))
def test_paper_arms_cannot_inherit_full_prior_dma_watermark_or_tool_cost(arm):
    env = paper_environment({
        "BELIEFKV_TOOL_TIMING_PRIOR": "/a/prior.json",
        "BELIEFKV_TOOL_TIMING_PRIOR_SHA256": "a" * 64,
        "BELIEFKV_IDLE_PREPARE_IO_BACKEND": "direct",
        "BELIEFKV_AGENT_KV_HIGH_WATERMARK": ".95",
        "BELIEFKV_TOOL_OFFLOAD_POLICY": "demand_cost",
    }, arm)
    assert env["BELIEFKV_TOOL_TIMING_PRIOR"] == ""
    assert env["BELIEFKV_TOOL_TIMING_PRIOR_SHA256"] == ""
    assert env["BELIEFKV_IDLE_PREPARE_IO_BACKEND"] == "native"
    assert env["BELIEFKV_AGENT_KV_HIGH_WATERMARK"] == "1"
    assert env["BELIEFKV_TOOL_OFFLOAD_POLICY"] == "fixed"


def test_high_watermark_preserves_author_buffer_and_physical_capacity(monkeypatch):
    monkeypatch.setenv("BELIEFKV_AGENT_KV_HIGH_WATERMARK", ".95")
    admission = AgentAdmission(NS(), strategy="thunderagent_retained")
    admission.attach(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=10_000),
    ))
    assert admission.policy.capacity == admission.policy.capacity_tokens == 10_000
    assert admission.policy.remaining_capacity() == 9500
    assert admission.physical_capacity_tokens == 10_000
    assert admission.policy.buffer_tokens == 100
    admission.policy.arrive("large", 9600, now=0.)
    assert admission.policy.allowed("large")
    assert admission.policy.remaining_capacity() == 0


@pytest.mark.parametrize("level", ("nan", "0", "1.1"))
def test_invalid_watermark_is_rejected(monkeypatch, level):
    monkeypatch.setenv("BELIEFKV_AGENT_KV_HIGH_WATERMARK", level)
    admission = AgentAdmission(NS(), strategy="thunderagent_retained")
    with pytest.raises(ValueError, match="watermark"):
        admission.attach(NS(
            tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=10_000),
        ))
