import json
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from collections import Counter, deque
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from beliefkv.control.causal_graph import InvocationState
from beliefkv.control.controller import BeliefKVController
from beliefkv.core.config import BeliefKVConfig
from beliefkv.core.events import (
    ExecutionMode,
    RelationType,
    RuntimeEvent,
    RuntimeEventKind,
)
from beliefkv.policy.admission import (
    AdmissionCompileBudget,
    AdmissionRequest,
    AdmissionSideState,
    ReclaimRequirement,
)
from beliefkv.experiments.policy_replay import load_replay_trace
from beliefkv.policy.joint_scheduler import (
    JointPlannerConfig,
    ObservedJointPlanner,
    SemanticResidencyTarget,
)
from beliefkv.policy.online_joint import (
    ActionGroupAtomicity,
    OnlineJointPlanDecision,
    OnlineJointPlanView,
    compile_bounded_seed_epoch,
)
from beliefkv.policy.predictive_joint import (
    ActionLocalPhysicalOverlay,
    BeneficiaryOpportunityProbe,
    PredictiveActionKind,
)
from beliefkv.policy.risk_shadow import PredictiveIntent
from beliefkv.policy.reference import (
    CapabilityReport,
    ResidencyAction,
    RunnableInvocation,
)
from beliefkv.policy.resource_snapshot import RuntimeResourceObservation
from beliefkv.policy.service_curve import TransferServiceCurve
from beliefkv.runtime.audit import PolicySnapshotLog
from beliefkv.runtime.joint_shadow import (
    IncrementalPolicyInputAssembler,
    coalesce_joint_shadow_deltas,
    ActionLocalPhysicalOverlayBatch,
    JointShadowResult,
    JointShadowStateStamp,
    LatestWinsJointPlanWorker,
    ObservedSeedBeneficiaryHint,
)
from beliefkv.runtime.lock_service import RequestServiceLedger
from beliefkv.runtime.page_index import PageOwnershipIndex
from beliefkv.runtime.protocol import (
    CommandAck,
    CommandKind,
    CommandStatus,
    ControlCommand,
    EnqueueOutcome,
    EnqueueStatus,
    PageHandle,
    PhysicalBundleIntent,
    PhysicalPageAction,
    PhysicalResidency,
    ResolvedCommand,
    ResolvedPageAction,
    TransferBlockerCode,
    TransferDirection,
    TransferTelemetry,
)
from beliefkv.runtime.sglang_adapter import (
    BASE_SGLANG_VERSION,
    BeliefKVRequestMetadata,
    SGLangSourceContract,
    assert_supported_sglang_version,
)
from beliefkv.runtime.sglang_v052rc1 import (
    EmbeddedSGLangRuntime,
    HiCacheNodeCommandBackend,
    SGLangBackendError,
    SGLangNodeRegistry,
    _OnlineJointResidencyTransaction,
    _PERFORMANCE_METRIC_EVENTS,
    close_runtime_with_signal_shield,
    install_scheduler_shutdown_handler,
    _predictive_bundle_envelope_reasons,
)
from beliefkv.runtime.restore_obligation import (
    RestoreAuthorityMode,
    RestoreLeaseState,
    RestoreObligationCause,
    RestoreObligationIndex,
    RestoreObligationState,
    RestoreTransactionStage,
    SafePointPhysicalPhase,
)


class _Node:
    def __init__(self, node_id=1):
        self.id = node_id
        self.value = [1, 2, 3, 4]
        self.host_value = None
        self.lock_ref = 0
        self.loading = False
        self.host_ref_counter = 0
        self.children = {}
        self.parent = None

    @property
    def evicted(self):
        return self.value is None

    @property
    def backuped(self):
        return self.host_value is not None


class _CacheController:
    def __init__(self, allocator):
        self.mem_pool_device_allocator = allocator

    def evict_host(self, _indices):
        return 4


class _TreeCache:
    def __init__(self):
        self.root_node = _Node(0)
        self.ongoing_write_through = {}
        self.ongoing_load_back = {}
        self.beliefkv_transfer_metadata = {"d2h": {}, "h2d": {}}
        self.token_to_kv_pool_host = SimpleNamespace(
            layout="layer_first", pin_memory=True
        )
        self.token_to_kv_pool_allocator = _Allocator(1_000_000)
        self.cache_controller = _CacheController(
            self.token_to_kv_pool_allocator
        )
        self.evictable_tokens = 0
        self.load_ready_calls = 0
        self.load_back_threshold = 10
        self.load_back_calls = []
        self.callback_errors = []
        self.batch_write_calls = []

    def evictable_size(self):
        return self.evictable_tokens

    def inc_lock_ref(self, node):
        current = node
        while current is not None and current is not self.root_node:
            current.lock_ref += 1
            current = current.parent
        return 0

    def dec_lock_ref(self, node):
        current = node
        while current is not None and current is not self.root_node:
            current.lock_ref -= 1
            current = current.parent
        return 0

    def write_backup(self, node, *, beliefkv_source=None):
        _ = beliefkv_source
        node.host_value = [10, 11, 12, 13]
        self.ongoing_write_through[node.id] = node
        return 4

    def write_backup_batch(self, nodes, *, beliefkv_source=None):
        self.batch_write_calls.append([node.id for node in nodes])
        return sum(
            self.write_backup(node, beliefkv_source=beliefkv_source)
            for node in nodes
        )

    def check_hicache_events(self):
        for node in self.ongoing_write_through.values():
            node.lock_ref = 0
        self.ongoing_write_through.clear()
        if self.load_ready_calls:
            for ancestor, node in self.ongoing_load_back.values():
                current = node
                while current is not ancestor:
                    current.loading = False
                    current = current.parent
            self.ongoing_load_back.clear()

    def _evict_backuped(self, node):
        self.token_to_kv_pool_allocator.available_tokens += len(node.value)
        node.value = None
        return 4

    def _evict_regular(self, node):
        self.token_to_kv_pool_allocator.available_tokens += len(node.value)
        node.value = None
        return 4

    def load_back(
        self,
        node,
        *,
        force=False,
        allow_eviction=True,
        beliefkv_source=None,
    ):
        _ = beliefkv_source
        self.load_back_calls.append(
            {
                "node_id": node.id,
                "force": force,
                "allow_eviction": allow_eviction,
            }
        )
        leaf = node
        chain = []
        while node is not self.root_node and node.evicted:
            chain.insert(0, node)
            node = node.parent
        token_count = sum(len(item.host_value) for item in chain)
        if not force and token_count < self.load_back_threshold:
            return None
        if token_count > self.token_to_kv_pool_allocator.available_tokens:
            return None
        self.token_to_kv_pool_allocator.available_tokens -= token_count
        loaded = []
        for item in chain:
            item.value = [20] * len(item.host_value)
            item.loading = True
            loaded.extend(item.value)
        self.ongoing_load_back[leaf.id] = (node, leaf)
        return loaded

    def ready_to_load_host_cache(self):
        self.load_ready_calls += 1
        return self.load_ready_calls

    def take_beliefkv_callback_errors(self):
        errors = self.callback_errors
        self.callback_errors = []
        return errors


class _LockPropagatingTreeCache(_TreeCache):
    def __init__(self):
        super().__init__()
        self.write_order = []
        self.evict_order = []

    def write_backup(self, node, *, beliefkv_source=None):
        self.write_order.append(node.id)
        current = node
        while current is not None and current is not self.root_node:
            current.lock_ref += 1
            current = current.parent
        return super().write_backup(node, beliefkv_source=beliefkv_source)

    def check_hicache_events(self):
        for node in tuple(self.ongoing_write_through.values()):
            current = node
            while current is not None and current is not self.root_node:
                current.lock_ref -= 1
                current = current.parent
        self.ongoing_write_through.clear()
        if self.load_ready_calls:
            for _, node in self.ongoing_load_back.values():
                node.loading = False
            self.ongoing_load_back.clear()

    def _evict_backuped(self, node):
        self.evict_order.append(node.id)
        return super()._evict_backuped(node)


class _AdmissionRecorder:
    def __init__(self):
        self.cancelled = []
        self.enqueued = []

    def enqueue(self, request):
        self.enqueued.append(request)

    def cancel(self, request_id):
        self.cancelled.append(request_id)


class _Allocator:
    def __init__(self, available_tokens):
        self.available_tokens = available_tokens
        self._next_token = 1

    def available_size(self):
        return self.available_tokens

    def alloc(self, need_size):
        if need_size > self.available_tokens:
            return None
        result = list(range(self._next_token, self._next_token + need_size))
        self._next_token += need_size
        self.available_tokens -= need_size
        return result

    def free(self, indices):
        self.available_tokens += len(indices)


class _HostAllocator(_Allocator):
    def __init__(self, size, available_tokens, size_per_token):
        super().__init__(available_tokens)
        self.size = size
        self.size_per_token = size_per_token


class _Sender:
    def __init__(self):
        self.messages = []

    def send_pyobj(self, value):
        self.messages.append(value)


def test_frontier_feature_delta_initializes_active_invocations():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    invocation = SimpleNamespace(
        invocation_id="invocation",
        state=InvocationState.READY,
        active_tool_family=None,
    )
    frontier_model = SimpleNamespace(model_version="test-model")
    runtime.controller = SimpleNamespace(
        graph=SimpleNamespace(invocations={"invocation": invocation}),
        predictor=SimpleNamespace(frontier_model=frontier_model, features={}),
    )
    runtime.config = SimpleNamespace(
        predictive_risk_shadow_enabled=True,
        frontier_aware_retraction_shadow_enabled=False,
        frontier_aware_retraction_canary_limit=0,
    )
    runtime._last_frontier_features = {}
    runtime._last_frontier_predictions = {}
    runtime._frontier_feature_delta_initialized = False

    with mock.patch(
        "beliefkv.runtime.sglang_v052rc1.build_invocation_frontier_features",
        return_value={},
    ):
        sources, predictions, features, removed = runtime._frontier_feature_delta(
            (), now_ms=1.0
        )

    assert tuple(item.invocation_id for item in sources) == ("invocation",)
    assert features == {}
    assert predictions == {}
    assert removed == frozenset()
    assert runtime._frontier_feature_delta_initialized
    assert runtime._frontier_active_invocation_ids == {"invocation"}


def test_semantic_delta_preserves_physical_and_telemetry_cursors():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    event = RuntimeEvent(
        "tool-start",
        2.0,
        RuntimeEventKind.TOOL_START,
        "workflow",
        invocation_id="invocation",
        attributes={"tool_call_id": "tool-call", "tool_name": "shell"},
    )
    event_delta = SimpleNamespace(
        from_sequence=1,
        to_sequence=2,
        events=(event,),
        full_rebuild_required=False,
    )
    runtime.controller = SimpleNamespace(
        runtime_event_sequence=2,
        runtime_events_since=lambda revision: (
            event_delta if revision == 1 else None
        ),
        transfer_telemetry_sequence=19,
        graph=SimpleNamespace(graph_version=2),
        data_consumers=SimpleNamespace(version=2),
        action_frontier_observer=SimpleNamespace(revision=3),
        page_index=SimpleNamespace(revision=17, topology_revision=11),
    )
    runtime.config = SimpleNamespace(joint_policy_enabled=False)
    runtime.audit = _AuditRecorder()
    runtime._joint_shadow_counts = Counter()
    runtime._joint_predictive_counts = Counter()
    runtime.predictive_risk_worker = object()
    runtime._joint_shadow_predictive_risk_triggers = lambda _events: (
        ("prepare", "tool_start", "invocation", 0),
    )
    runtime._joint_shadow_timing_samples = {
        "safe_point_delta_capture_ms": deque(maxlen=16),
        "snapshot_enqueue_ms": deque(maxlen=16),
    }
    runtime._shadow_event_sequence = 1
    runtime._shadow_page_revision = 7
    runtime._shadow_topology_revision = 5
    runtime._shadow_telemetry_sequence = 13
    runtime._last_policy_runtime_runnable = ()
    runtime._last_policy_fairness_accounts = ()
    runtime._last_policy_external_workflow_charges = ()
    runtime._last_policy_control_state = {}
    runtime._last_policy_capabilities = CapabilityReport(
        runtime_name="test",
        runtime_version="test",
        supported_residency_actions=frozenset(),
        execution_order_control=True,
        admission_control=True,
        transfer_dependencies=True,
        native_identity_mapping=True,
    )
    runtime._last_policy_state_stamp = JointShadowStateStamp(
        graph_version=1,
        consumer_version=1,
        event_sequence=1,
        page_revision=7,
        topology_revision=5,
        fairness_revision=4,
        transfer_epoch=6,
        runnable_signature=(),
        hbm_used_bytes=100,
        host_free_bytes=200,
    )
    runtime._current_native_available_hbm_bytes = 800
    runtime._frontier_feature_delta = lambda *_args, **_kwargs: (
        (),
        {},
        {},
        frozenset(),
    )
    submitted = []
    worker = SimpleNamespace(
        submit_delta=lambda delta: (
            submitted.append(delta)
            or SimpleNamespace(
                sequence=1,
                enqueue_ms=0.01,
                replaced_sequence=None,
            )
        ),
        stats=lambda: SimpleNamespace(pending_count=0, busy=False),
    )
    observation = RuntimeResourceObservation(
        ts_ms=2.0,
        hbm_capacity_bytes=1_000,
        hbm_used_bytes=1_000,
        host_capacity_bytes=1_000,
        host_used_bytes=0,
        host_free_bytes=1_000,
    )

    assert runtime._publish_joint_semantic_delta(
        observation,
        worker,
        capture_started_ns=0,
    )

    delta = submitted[0]
    assert delta.event_from_sequence == 1
    assert delta.event_to_sequence == 2
    assert delta.page_delta.from_revision == 7
    assert delta.page_delta.to_revision == 7
    assert delta.page_delta.topology_revision == 5
    assert delta.transfer_telemetry == ()
    assert not delta.risk_evaluation_requested
    assert delta.risk_trigger_signature == (
        ("prepare", "tool_start", "invocation", 0),
    )
    assert runtime._pending_predictive_prepare_triggers == (
        ("prepare", "tool_start", "invocation", 0),
    )
    assert runtime._pending_predictive_prepare_event_sequence == 2
    assert runtime._pending_predictive_prepare_created_ts_ms == 2.0
    assert runtime._shadow_event_sequence == 2
    assert runtime._shadow_page_revision == 7
    assert runtime._shadow_topology_revision == 5
    assert runtime._shadow_telemetry_sequence == 13
    assert runtime._last_policy_state_stamp.event_sequence == 2
    assert delta.source_page_revision == 17
    assert delta.source_topology_revision == 11
    assert delta.observation.hbm_used_bytes == 1_000
    assert delta.observation.effective_hbm_used_bytes == 200
    assert delta.observation.policy_hbm_used_bytes == 200
    assert delta.stamp.hbm_used_bytes == 200

    reentry_event = RuntimeEvent(
        "tool-end",
        3.0,
        RuntimeEventKind.TOOL_END,
        "workflow",
        invocation_id="invocation",
        context_id="ctx",
        context_epoch=0,
        attributes={"tool_call_id": "tool-call", "tool_name": "shell"},
    )
    reentry_events = SimpleNamespace(
        from_sequence=2,
        to_sequence=3,
        events=(reentry_event,),
        full_rebuild_required=False,
    )
    reentry_overlay = ActionLocalPhysicalOverlayBatch(
        beneficiary_risk_signature=(),
        opportunity=None,
        overlays=(
            ActionLocalPhysicalOverlay(
                context_id="ctx",
                context_epoch=0,
                context_revision=4,
                page_revision=18,
                topology_revision=11,
                generation_fingerprint="reentry-generation",
                shape_fingerprint="reentry-prefetch:100:n1",
                exclusive_reclaimable_bytes=0,
                d2h_copy_bytes=0,
                extent_count=1,
                cross_context_bytes=0,
                locked_bytes=0,
                owner_context_ids=("ctx",),
                blocker_codes=(),
                native_loading=False,
                captured_ts_ms=3.0,
                h2d_copy_bytes=100,
                evidence_kind="prefetch_target_preview",
            ),
        ),
        reentry_context_ids=("ctx",),
    )
    runtime.controller.runtime_event_sequence = 3
    runtime.controller.runtime_events_since = lambda revision: (
        reentry_events if revision == 2 else None
    )
    runtime.controller.graph.graph_version = 3
    runtime._joint_shadow_predictive_risk_triggers = lambda _events: (
        ("reentry", "tool_end", "invocation", 0),
    )
    runtime._capture_reentry_action_local_physical_overlay_batch = (
        lambda _triggers, _observation: reentry_overlay
    )

    assert runtime._publish_joint_semantic_delta(
        replace(observation, ts_ms=3.0),
        worker,
        capture_started_ns=0,
    )

    reentry_delta = submitted[1]
    assert reentry_delta.risk_evaluation_requested
    assert reentry_delta.action_local_overlay_replaced
    assert reentry_delta.action_local_overlay_batch is reentry_overlay
    assert reentry_delta.page_delta.pages == ()


def test_bounded_seed_hint_change_publishes_one_lightweight_risk_delta():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    runnable = _predictive_beneficiary_runnable()
    runtime.__dict__.update(
        controller=SimpleNamespace(
            page_index=SimpleNamespace(
                revision=17,
                topology_revision=11,
                has_context=lambda context_id: context_id == "ctx-victim",
                context_epoch=lambda _context_id: 0,
                context_revision=lambda _context_id: 0,
            ),
            admission=SimpleNamespace(reserved_bytes=0),
        ),
        config=SimpleNamespace(
            kv_bytes_per_token=1,
            admission_prefill_quantum_tokens=16,
            admission_decode_quantum_tokens=16,
            joint_policy_enabled=False,
            reference_policy_hbm_bucket_bytes=64 << 20,
        ),
        scheduler=SimpleNamespace(
            running_batch=SimpleNamespace(reqs=()),
            chunked_req=None,
            max_running_requests=32,
        ),
        audit=_AuditRecorder(),
        _joint_shadow_counts=Counter(),
        _joint_predictive_counts=Counter(),
        _joint_shadow_timing_samples={
            "safe_point_delta_capture_ms": deque(maxlen=16),
            "snapshot_enqueue_ms": deque(maxlen=16),
        },
        _shadow_event_sequence=2,
        _shadow_page_revision=7,
        _shadow_topology_revision=5,
        _last_policy_state_stamp=JointShadowStateStamp(
            graph_version=2, consumer_version=2, event_sequence=2,
            page_revision=7, topology_revision=5, fairness_revision=4,
            transfer_epoch=6, runnable_signature=(), hbm_used_bytes=100,
            host_free_bytes=200,
        ),
        _latest_observed_seed_beneficiary=ObservedSeedBeneficiaryHint(
            plan_id="seed-plan", request_id=runnable.request_id,
            invocation_id=runnable.invocation_id, context_id=runnable.context_id,
            context_epoch=runnable.context_epoch,
            startup_bytes=runnable.admission_startup_bytes,
            growth_bytes=runnable.admission_growth_bytes,
            seed_generation=7, created_ts_ms=4.0,
        ),
        _observed_seed_hint_publication_initialized=False,
        _last_published_observed_seed_hint_signature=None,
        _latest_bounded_seed_runnable=(runnable,),
        _last_policy_runtime_runnable=(),
        _last_policy_fairness_accounts=(),
        _last_policy_external_workflow_charges=(),
        _last_policy_control_state={},
        _last_policy_capabilities=CapabilityReport(
            runtime_name="test", runtime_version="test",
            supported_residency_actions=frozenset(), execution_order_control=True,
            admission_control=True, transfer_dependencies=True,
            native_identity_mapping=True,
        ),
        _last_frontier_model_version="frontier-test",
        predictive_risk_worker=object(),
        _current_native_available_hbm_bytes=800,
    )
    runtime._capture_action_local_physical_overlay_batch = (
        lambda hint, observation, **_kwargs: ActionLocalPhysicalOverlayBatch(
            beneficiary_risk_signature=hint.risk_signature,
            opportunity=BeneficiaryOpportunityProbe(
                beneficiary_request_id=hint.request_id,
                beneficiary_context_id=hint.context_id,
                beneficiary_context_epoch=hint.context_epoch,
                required_bytes=hint.startup_bytes + hint.growth_bytes,
                hbm_available_bytes=0,
                hbm_risk_margin_bytes=1,
                projected_running_growth_bytes=1,
                projected_hbm_available_bytes=0,
                predicted_block_time_ms=0.0,
                predicted_deficit_bytes=hint.startup_bytes + hint.growth_bytes,
                running_request_count=0,
                max_running_requests=32,
                beneficiary_slot_blocked=False,
                beneficiary_hbm_blocked=True,
                beneficiary_slot_then_hbm_blocked=False,
                hbm_opportunity_possible=True,
                captured_ts_ms=observation.ts_ms,
            ),
            overlays=(
                ActionLocalPhysicalOverlay(
                    context_id="ctx-victim",
                    context_epoch=0,
                    context_revision=0,
                    page_revision=17,
                    topology_revision=11,
                    generation_fingerprint="generation-victim",
                    shape_fingerprint="shape-victim",
                    exclusive_reclaimable_bytes=1,
                    d2h_copy_bytes=1,
                    extent_count=1,
                    cross_context_bytes=0,
                    locked_bytes=0,
                    owner_context_ids=("ctx-victim",),
                    blocker_codes=(),
                    native_loading=False,
                    captured_ts_ms=observation.ts_ms,
                ),
            ),
            selection_reason=None,
            capture_ms=0.02,
        )
    )
    observation = RuntimeResourceObservation(
        ts_ms=5.0, hbm_capacity_bytes=1_000, hbm_used_bytes=950,
        host_capacity_bytes=1_000, host_used_bytes=0, host_free_bytes=1_000,
    )
    runtime._runtime_resource_observation = lambda: observation
    submitted = []
    worker = SimpleNamespace(
        submit_delta=lambda delta: submitted.append(delta) or SimpleNamespace(
            sequence=len(submitted), enqueue_ms=0.01, replaced_sequence=None
        ),
        stats=lambda: SimpleNamespace(pending_count=0, busy=False),
    )
    assert runtime._maybe_publish_observed_seed_hint_delta(
        worker,
        observation=observation,
    )
    assert not runtime._maybe_publish_observed_seed_hint_delta(worker)
    assert len(submitted) == 1
    assert submitted[0].risk_evaluation_requested
    assert submitted[0].runtime_events == ()
    assert submitted[0].page_delta.pages == ()
    assert submitted[0].observed_seed_beneficiary.published_ts_ms == 5.0
    assert submitted[0].source_page_revision == 17
    assert submitted[0].observation.hbm_used_bytes == 950
    assert submitted[0].observation.effective_hbm_used_bytes == 200
    assert submitted[0].observation.policy_hbm_used_bytes == 200
    assert submitted[0].stamp.hbm_used_bytes == 200

    prepare_trigger = (("prepare", "tool_start", "victim", 0),)
    runtime._pending_predictive_prepare_triggers = prepare_trigger
    runtime._pending_predictive_prepare_event_sequence = 2
    runtime._pending_predictive_prepare_created_ts_ms = 4.0
    runtime._last_published_predictive_prepare_event_sequence = 0
    assert runtime._maybe_publish_observed_seed_hint_delta(worker)
    assert len(submitted) == 2
    assert submitted[1].risk_evaluation_requested
    assert submitted[1].risk_trigger_signature == prepare_trigger
    assert submitted[1].action_local_overlay_replaced
    assert runtime._last_published_predictive_prepare_event_sequence == 2
    assert runtime._pending_predictive_prepare_triggers == ()
    assert not runtime._maybe_publish_observed_seed_hint_delta(worker)

    runtime._latest_observed_seed_beneficiary = replace(
        runtime._latest_observed_seed_beneficiary,
        seed_generation=8,
        created_ts_ms=6.0,
        published_ts_ms=None,
    )
    assert not runtime._maybe_publish_observed_seed_hint_delta(worker)
    assert len(submitted) == 2

    runtime._latest_observed_seed_beneficiary = replace(
        runtime._latest_observed_seed_beneficiary,
        growth_bytes=runtime._latest_observed_seed_beneficiary.growth_bytes + 1,
        seed_generation=9,
        created_ts_ms=7.0,
        published_ts_ms=None,
    )
    assert runtime._maybe_publish_observed_seed_hint_delta(worker)
    assert len(submitted) == 3
    assert submitted[2].risk_evaluation_requested
    assert submitted[2].action_local_overlay_replaced

    combined = coalesce_joint_shadow_deltas((submitted[0], submitted[2]))
    assert combined.action_local_overlay_replaced
    assert combined.action_local_overlay_batch is submitted[2].action_local_overlay_batch

    runtime._latest_observed_seed_beneficiary = None
    assert runtime._maybe_publish_observed_seed_hint_delta(worker)
    assert len(submitted) == 4
    assert not submitted[3].risk_evaluation_requested
    assert submitted[3].observed_seed_beneficiary is None
    assert submitted[3].action_local_overlay_replaced
    assert runtime._joint_predictive_counts == Counter(
        {
            "hint_risk_published": 3,
            "hint_clear_published": 1,
            "beneficiary_opportunity:hbm_blocked": 3,
            "overlay_victim_count": 3,
            "prepare_event_aligned_hint_published": 1,
            "prepare_event_aligned_risk_published": 1,
        }
    )


def test_predictive_prepare_micro_gate_injects_one_live_evidence_intent():
    controller_config = BeliefKVConfig(
        hbm_capacity_bytes=2_000,
        host_capacity_bytes=4_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
    )
    controller = BeliefKVController(controller_config)
    controller.process_runtime_events(
        (
            RuntimeEvent(
                "wf-start-micro-prepare",
                1.0,
                RuntimeEventKind.WORKFLOW_START,
                "wf-micro-prepare",
            ),
            RuntimeEvent(
                "victim-create-micro-prepare",
                2.0,
                RuntimeEventKind.INVOCATION_CREATE,
                "wf-micro-prepare",
                invocation_id="inv-victim",
                context_id="ctx-victim",
                context_epoch=0,
            ),
            RuntimeEvent(
                "victim-tool-micro-prepare",
                3.0,
                RuntimeEventKind.TOOL_START,
                "wf-micro-prepare",
                invocation_id="inv-victim",
                context_id="ctx-victim",
                context_epoch=0,
                attributes={"tool_family": "shell"},
            ),
            RuntimeEvent(
                "beneficiary-create-micro-prepare",
                4.0,
                RuntimeEventKind.INVOCATION_CREATE,
                "wf-micro-prepare",
                invocation_id="inv-beneficiary",
                context_id="ctx-beneficiary",
                context_epoch=0,
            ),
        )
    )
    controller.service_curve = SimpleNamespace(
        estimate=lambda *_args, **_kwargs: SimpleNamespace(
            estimated_completion_p90_ms=10.0,
            estimated_unhidden_stall_p90_ms=2.0,
            shape_supported=True,
            source="micro_gate_test_curve",
        )
    )
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    runtime.config = BeliefKVConfig(
        hbm_capacity_bytes=2_000,
        host_capacity_bytes=4_000,
        reserve_hbm_bytes=0,
        predictor_model_path="/tmp/frontier.json",
        gpu_service_model_path="/tmp/service.json",
        joint_policy_enabled=True,
        predictive_risk_shadow_enabled=True,
        predictive_joint_overlay_enabled=True,
        predictive_prepare_host_canary_limit=1,
        predictive_prepare_micro_gate_enabled=True,
        predictive_prepare_micro_gate_min_private_bytes=100,
    )
    runtime.controller = controller
    runtime.audit = _AuditRecorder()
    runtime._joint_predictive_counts = Counter()
    runtime._latest_predictive_intent = None
    runtime._last_frontier_model_version = "frontier-v1"
    runtime._current_online_joint_decision = object()
    runtime._last_joint_decision_plan_id = "old-plan"
    runtime._predictive_prepare_micro_gate_state = {
        "enabled": True,
        "gate_id": "p6-prepare-mechanism-v1",
        "stage": "armed",
        "evidence_kind": "injected_mechanism_gate",
    }
    hint = ObservedSeedBeneficiaryHint(
        plan_id="seed-plan",
        request_id="request-beneficiary",
        invocation_id="inv-beneficiary",
        context_id="ctx-beneficiary",
        context_epoch=0,
        startup_bytes=50,
        growth_bytes=50,
        seed_generation=1,
        created_ts_ms=5.0,
        published_ts_ms=6.0,
    )
    opportunity = BeneficiaryOpportunityProbe(
        beneficiary_request_id=hint.request_id,
        beneficiary_context_id=hint.context_id,
        beneficiary_context_epoch=hint.context_epoch,
        required_bytes=100,
        hbm_available_bytes=1_000,
        hbm_risk_margin_bytes=0,
        projected_running_growth_bytes=0,
        projected_hbm_available_bytes=1_000,
        predicted_block_time_ms=None,
        predicted_deficit_bytes=0,
        running_request_count=1,
        max_running_requests=32,
        beneficiary_slot_blocked=False,
        beneficiary_hbm_blocked=False,
        beneficiary_slot_then_hbm_blocked=False,
        hbm_opportunity_possible=False,
        captured_ts_ms=6.0,
        immediate_admission_fit=True,
        block_time_source="unavailable",
    )
    overlay = ActionLocalPhysicalOverlay(
        context_id="ctx-victim",
        context_epoch=0,
        context_revision=7,
        page_revision=11,
        topology_revision=3,
        generation_fingerprint="generation-victim",
        shape_fingerprint="summary:300:n1",
        exclusive_reclaimable_bytes=300,
        d2h_copy_bytes=300,
        extent_count=1,
        cross_context_bytes=0,
        locked_bytes=0,
        owner_context_ids=("ctx-victim",),
        blocker_codes=(),
        native_loading=False,
        captured_ts_ms=6.0,
        evidence_kind="context_summary_upper_bound",
    )
    overlay_batch = ActionLocalPhysicalOverlayBatch(
        beneficiary_risk_signature=hint.risk_signature,
        opportunity=opportunity,
        overlays=(overlay,),
        selection_reason="mechanism_gate_forced_capture",
        mechanism_capture_forced=True,
    )
    observation = RuntimeResourceObservation(
        ts_ms=6.0,
        hbm_capacity_bytes=2_000,
        hbm_used_bytes=1_900,
        host_capacity_bytes=4_000,
        host_used_bytes=0,
        host_free_bytes=4_000,
    )

    assert runtime._maybe_inject_predictive_prepare_micro_gate(
        hint, overlay_batch, observation
    )
    intent = runtime._latest_predictive_intent
    assert intent is not None
    assert intent.evidence_kind == "injected_mechanism_gate"
    assert intent.beneficiary_request_id == hint.request_id
    assert intent.context_id == overlay.context_id
    assert runtime._current_online_joint_decision is None
    assert runtime._last_joint_decision_plan_id is None
    assert runtime._predictive_prepare_micro_gate_state["stage"] == "intent_published"
    assert runtime._predictive_prepare_micro_gate_state["attempt_count"] == 1
    assert intent.intent_id.endswith(":a1")
    assert runtime._predictive_prepare_micro_gate_holds_intent(intent)
    assert not runtime._maybe_inject_predictive_prepare_micro_gate(
        hint, overlay_batch, observation
    )
    assert runtime._joint_predictive_counts[
        "prepare_micro_gate_intent_published"
    ] == 1


def test_predictive_prepare_micro_gate_retries_only_before_queue():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    runtime.config = SimpleNamespace(
        predictive_prepare_micro_gate_enabled=True,
    )
    runtime.audit = _AuditRecorder()
    runtime._predictive_prepare_micro_gate_last_probe_signature = ("old",)
    runtime._predictive_prepare_micro_gate_state = {
        "stage": "intent_published",
        "intent_id": "intent-1",
        "attempt_count": 1,
    }

    runtime._update_predictive_prepare_micro_gate(
        "rejected",
        now_ms=10.0,
        intent_id="intent-1",
        rejection_reasons=["beneficiary_missing"],
    )

    state = runtime._predictive_prepare_micro_gate_state
    assert state["stage"] == "armed"
    assert state["intent_id"] is None
    assert state["rejected_attempt_count"] == 1
    assert state["last_rejected_intent_id"] == "intent-1"
    assert runtime._predictive_prepare_micro_gate_last_probe_signature is None

    runtime._update_predictive_prepare_micro_gate(
        "intent_published",
        now_ms=11.0,
        intent_id="intent-2",
    )
    runtime._update_predictive_prepare_micro_gate(
        "queued",
        now_ms=12.0,
        intent_id="intent-2",
        command_id="command-2",
    )
    runtime._update_predictive_prepare_micro_gate(
        "rejected",
        now_ms=13.0,
        intent_id="intent-2",
        rejection_reasons=["backend_rejected"],
    )

    state = runtime._predictive_prepare_micro_gate_state
    assert state["stage"] == "rejected"
    assert state["intent_id"] == "intent-2"
    assert state["attempt_count"] == 2
    assert state["rejected_attempt_count"] == 1


def test_action_local_overlay_force_is_scoped_to_mechanism_capture():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    runtime.scheduler = SimpleNamespace(
        running_batch=SimpleNamespace(reqs=()),
        chunked_req=None,
        max_running_requests=32,
    )
    context = SimpleNamespace(epoch=0)
    invocation = SimpleNamespace(
        context_id="victim-context",
        state=InvocationState.WAIT_TOOL,
    )
    summary = SimpleNamespace(
        context_id="victim-context",
        context_epoch=0,
        gpu_bytes=300,
        cpu_bytes=0,
        exclusive_reclaimable_upper_bound_bytes=300,
        d2h_copy_upper_bound_bytes=300,
        d2h_extent_count_upper_bound=3,
        locked_bytes=0,
        last_access_ms=1.0,
    )
    runtime.controller = SimpleNamespace(
        admission=SimpleNamespace(reserved_bytes=0),
        graph=SimpleNamespace(
            graph_version=3,
            invocations={"victim": invocation},
            contexts={"victim-context": context},
        ),
        page_index=SimpleNamespace(
            revision=7,
            topology_revision=5,
            has_context=lambda context_id: context_id == "victim-context",
            context_page_count=lambda _context_id: 3,
            context_epoch=lambda _context_id: 0,
            context_revision=lambda _context_id: 2,
            context_physical_summary=lambda _context_id: summary,
        ),
    )
    runtime.config = SimpleNamespace(
        reference_policy_hbm_bucket_bytes=64,
        predictive_beneficiary_projection_horizon_ms=2_000.0,
        kv_bytes_per_token=1,
        admission_prefill_quantum_tokens=16,
        admission_decode_quantum_tokens=16,
    )
    hint = ObservedSeedBeneficiaryHint(
        "seed",
        "beneficiary",
        "beneficiary-invocation",
        "beneficiary-context",
        0,
        64,
        32,
    )
    observation = RuntimeResourceObservation(
        ts_ms=5.0,
        hbm_capacity_bytes=1_000,
        hbm_used_bytes=0,
        host_capacity_bytes=1_000,
        host_used_bytes=0,
        host_free_bytes=1_000,
    )

    normal = runtime._capture_action_local_physical_overlay_batch(
        hint, observation
    )
    forced = runtime._capture_action_local_physical_overlay_batch(
        hint,
        observation,
        opportunity=normal.opportunity,
        force_mechanism_capture=True,
    )

    assert normal.overlays == ()
    assert not normal.mechanism_capture_forced
    assert tuple(item.context_id for item in forced.overlays) == (
        "victim-context",
    )
    assert forced.mechanism_capture_forced
    assert forced.selection_reason == "mechanism_gate_forced_capture"
    assert not forced.opportunity.hbm_opportunity_possible


def test_predictive_prepare_micro_gate_probe_retries_on_graph_revision():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    graph = SimpleNamespace(graph_version=1)
    runtime.controller = SimpleNamespace(
        graph=graph,
        page_index=SimpleNamespace(topology_revision=2),
    )
    runtime.config = SimpleNamespace(
        predictive_prepare_micro_gate_enabled=True,
    )
    runtime._predictive_prepare_micro_gate_state = {"stage": "armed"}
    runtime._joint_predictive_counts = Counter()
    runtime._joint_shadow_timing_samples = {}
    runtime.audit = _AuditRecorder()
    hint = ObservedSeedBeneficiaryHint(
        "seed", "beneficiary", "invocation", "context", 0, 64, 32
    )
    opportunity = BeneficiaryOpportunityProbe(
        beneficiary_request_id=hint.request_id,
        beneficiary_context_id=hint.context_id,
        beneficiary_context_epoch=hint.context_epoch,
        required_bytes=96,
        hbm_available_bytes=1_000,
        hbm_risk_margin_bytes=0,
        projected_running_growth_bytes=0,
        projected_hbm_available_bytes=1_000,
        predicted_block_time_ms=None,
        predicted_deficit_bytes=0,
        running_request_count=1,
        max_running_requests=32,
        beneficiary_slot_blocked=False,
        beneficiary_hbm_blocked=False,
        beneficiary_slot_then_hbm_blocked=False,
        hbm_opportunity_possible=False,
        captured_ts_ms=5.0,
        immediate_admission_fit=True,
        block_time_source="unavailable",
    )
    capture_calls = []
    injection_calls = []
    runtime._capture_action_local_physical_overlay_batch = (
        lambda *args, **kwargs: (
            capture_calls.append(kwargs)
            or ActionLocalPhysicalOverlayBatch(
                beneficiary_risk_signature=hint.risk_signature,
                opportunity=opportunity,
                mechanism_capture_forced=True,
            )
        )
    )
    runtime._maybe_inject_predictive_prepare_micro_gate = (
        lambda *args: injection_calls.append(args) or False
    )
    observation = RuntimeResourceObservation(
        ts_ms=5.0,
        hbm_capacity_bytes=1_000,
        hbm_used_bytes=0,
        host_capacity_bytes=1_000,
        host_used_bytes=0,
        host_free_bytes=1_000,
    )

    assert not runtime._maybe_probe_predictive_prepare_micro_gate(
        hint, opportunity, observation
    )
    assert not runtime._maybe_probe_predictive_prepare_micro_gate(
        hint, opportunity, observation
    )
    graph.graph_version = 2
    assert not runtime._maybe_probe_predictive_prepare_micro_gate(
        hint, opportunity, observation
    )

    assert len(capture_calls) == 2
    assert all(item["force_mechanism_capture"] for item in capture_calls)
    assert len(injection_calls) == 2
    assert runtime._joint_predictive_counts == Counter(
        prepare_micro_gate_overlay_probe=2
    )


def test_action_local_probe_filters_slot_only_beneficiary_before_graph_scan():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    requests = tuple(SimpleNamespace(rid=f"running-{index}") for index in range(32))
    runtime.scheduler = SimpleNamespace(
        running_batch=SimpleNamespace(reqs=requests),
        chunked_req=None,
        max_running_requests=32,
    )
    runtime.controller = SimpleNamespace(
        admission=SimpleNamespace(reserved_bytes=0)
    )
    runtime.config = SimpleNamespace(
        reference_policy_hbm_bucket_bytes=64,
        predictive_beneficiary_projection_horizon_ms=2000.0,
        kv_bytes_per_token=1,
        admission_prefill_quantum_tokens=16,
    )
    hint = ObservedSeedBeneficiaryHint(
        "seed",
        "beneficiary",
        "beneficiary-invocation",
        "beneficiary-context",
        0,
        64,
        32,
    )
    observation = RuntimeResourceObservation(
        ts_ms=5.0,
        hbm_capacity_bytes=1_000,
        hbm_used_bytes=0,
        host_capacity_bytes=1_000,
        host_used_bytes=0,
        host_free_bytes=1_000,
    )

    batch = runtime._capture_action_local_physical_overlay_batch(
        hint, observation
    )

    assert batch.selection_reason == "beneficiary_slot_only"
    assert batch.overlays == ()
    assert batch.opportunity.beneficiary_slot_blocked
    assert not batch.opportunity.beneficiary_hbm_blocked
    assert not batch.opportunity.hbm_opportunity_possible


def test_action_local_overlay_bounds_summary_scan_and_captures_two_victims():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    runtime.scheduler = SimpleNamespace(
        running_batch=SimpleNamespace(reqs=()),
        chunked_req=None,
        max_running_requests=32,
    )
    context_ids = tuple(f"victim-{index:02d}" for index in range(12))
    contexts = {
        context_id: SimpleNamespace(epoch=0) for context_id in context_ids
    }
    invocations = {
        context_id: SimpleNamespace(
            context_id=context_id, state=InvocationState.WAIT_TOOL
        )
        for context_id in contexts
    }
    summaries = {
        context_id: SimpleNamespace(
            context_id=context_id,
            context_epoch=0,
            gpu_bytes=300 - index,
            cpu_bytes=0,
            exclusive_reclaimable_upper_bound_bytes=300 - index,
            d2h_copy_upper_bound_bytes=300 - index,
            d2h_extent_count_upper_bound=3,
            locked_bytes=0,
            last_access_ms=float(index),
        )
        for index, context_id in enumerate(context_ids)
    }
    calls = Counter()

    def context_physical_summary(context_id):
        calls["summary"] += 1
        return summaries[context_id]

    page_index = SimpleNamespace(
        revision=17,
        topology_revision=11,
        has_context=lambda context_id: context_id in summaries,
        context_page_count=lambda context_id: 100 - int(context_id[-2:]),
        context_epoch=lambda _context_id: 0,
        context_physical_summary=context_physical_summary,
        context_revision=lambda _context_id: 3,
    )

    runtime.controller = SimpleNamespace(
        admission=SimpleNamespace(reserved_bytes=0),
        graph=SimpleNamespace(invocations=invocations, contexts=contexts),
        page_index=page_index,
    )
    runtime.config = SimpleNamespace(
        reference_policy_hbm_bucket_bytes=64,
        predictive_beneficiary_projection_horizon_ms=2000.0,
        kv_bytes_per_token=1,
        admission_prefill_quantum_tokens=16,
    )
    hint = ObservedSeedBeneficiaryHint(
        "seed", "beneficiary", "beneficiary-invocation",
        "beneficiary-context", 0, 64, 32,
    )
    observation = RuntimeResourceObservation(
        ts_ms=5.0, hbm_capacity_bytes=1_000, hbm_used_bytes=950,
        host_capacity_bytes=1_000, host_used_bytes=0, host_free_bytes=1_000,
    )

    batch = runtime._capture_action_local_physical_overlay_batch(
        hint, observation
    )

    assert batch.selection_reason is None
    assert tuple(item.context_id for item in batch.overlays) == (
        "victim-00", "victim-01"
    )
    assert batch.opportunity.beneficiary_hbm_blocked
    assert all(item.page_revision == 17 for item in batch.overlays)
    assert all(
        item.evidence_kind == "context_summary_upper_bound"
        for item in batch.overlays
    )
    repeated = runtime._capture_action_local_physical_overlay_batch(
        hint, replace(observation, ts_ms=6.0)
    )
    assert tuple(item.context_id for item in repeated.overlays) == (
        "victim-00", "victim-01"
    )
    assert batch.parked_context_count == 12
    assert batch.summarized_context_count == 8
    assert calls == Counter(summary=16)


def test_action_local_overlay_prefetches_parked_cpu_context_not_beneficiary():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    runtime.scheduler = SimpleNamespace(
        running_batch=SimpleNamespace(reqs=()),
        chunked_req=None,
        max_running_requests=32,
    )
    contexts = {
        "target-context": SimpleNamespace(epoch=2),
        "victim-context": SimpleNamespace(epoch=1),
    }
    invocations = {
        "target": SimpleNamespace(
            context_id="target-context", state=InvocationState.WAIT_JOIN
        ),
        "victim": SimpleNamespace(
            context_id="victim-context", state=InvocationState.WAIT_TOOL
        ),
    }
    summaries = {
        "target-context": SimpleNamespace(
            context_id="target-context",
            context_epoch=2,
            extent_count=2,
            gpu_bytes=0,
            cpu_bytes=400,
            locked_bytes=0,
            exclusive_reclaimable_upper_bound_bytes=0,
            d2h_copy_upper_bound_bytes=0,
            d2h_extent_count_upper_bound=0,
            last_access_ms=10.0,
        ),
        "victim-context": SimpleNamespace(
            context_id="victim-context",
            context_epoch=1,
            extent_count=3,
            gpu_bytes=500,
            cpu_bytes=0,
            locked_bytes=0,
            exclusive_reclaimable_upper_bound_bytes=500,
            d2h_copy_upper_bound_bytes=500,
            d2h_extent_count_upper_bound=3,
            last_access_ms=5.0,
        ),
    }
    target_preview = SimpleNamespace(
        eligible=True,
        copy_bytes=400,
        page_actions=(object(), object()),
        blockers=(),
        bundle=SimpleNamespace(
            bundle_id="target-prefetch",
            generation_fingerprint="target-generation",
            cross_context_action_bytes=0,
            locked_bytes=0,
            owner_context_ids=("target-context",),
        ),
    )
    page_index = SimpleNamespace(
        revision=19,
        topology_revision=13,
        has_context=lambda context_id: context_id in summaries,
        context_page_count=lambda context_id: summaries[context_id].extent_count,
        context_epoch=lambda context_id: summaries[context_id].context_epoch,
        context_revision=lambda _context_id: 4,
        context_physical_summary=lambda context_id: summaries[context_id],
    )
    runtime.controller = SimpleNamespace(
        admission=SimpleNamespace(reserved_bytes=0),
        graph=SimpleNamespace(
            graph_version=7,
            invocations=invocations,
            contexts=contexts,
        ),
        page_index=page_index,
        arbiter=SimpleNamespace(
            bundle_builder=SimpleNamespace(
                previews_for_context=lambda _kind, context_id, *_args, **_kwargs: (
                    (target_preview,) if context_id == "target-context" else ()
                )
            )
        ),
    )
    runtime.config = SimpleNamespace(
        reference_policy_hbm_bucket_bytes=64,
        predictive_beneficiary_projection_horizon_ms=2_000.0,
        kv_bytes_per_token=1,
        admission_prefill_quantum_tokens=16,
        admission_decode_quantum_tokens=16,
    )
    hint = ObservedSeedBeneficiaryHint(
        "seed",
        "beneficiary",
        "beneficiary-invocation",
        "beneficiary-context",
        0,
        64,
        32,
    )
    observation = RuntimeResourceObservation(
        ts_ms=20.0,
        hbm_capacity_bytes=1_000,
        hbm_used_bytes=950,
        host_capacity_bytes=2_000,
        host_used_bytes=400,
        host_free_bytes=1_600,
    )

    batch = runtime._capture_action_local_physical_overlay_batch(
        hint, observation
    )

    assert tuple(
        (item.context_id, item.evidence_kind) for item in batch.overlays
    ) == (
        ("target-context", "prefetch_target_preview"),
        ("victim-context", "context_summary_upper_bound"),
    )
    assert batch.overlays[0].h2d_copy_bytes == 400
    assert batch.victim_count == 1

    reentry_batch = runtime._capture_reentry_action_local_physical_overlay_batch(
        (("reentry", "join_satisfied", "target", 2),),
        observation,
    )
    assert reentry_batch is not None
    assert reentry_batch.beneficiary_risk_signature == ()
    assert reentry_batch.opportunity is None
    assert reentry_batch.reentry_context_ids == ("target-context",)
    assert len(reentry_batch.overlays) == 1
    assert reentry_batch.overlays[0].context_id == "target-context"
    assert reentry_batch.overlays[0].h2d_copy_bytes == 400
    assert reentry_batch.victim_count == 0
    summaries["victim-context"].cpu_bytes = 500
    summaries["victim-context"].d2h_copy_upper_bound_bytes = 0
    funded_reentry = (
        runtime._capture_reentry_action_local_physical_overlay_batch(
            (("reentry", "predicted_latest_start", "target", 2),),
            observation,
        )
    )
    assert funded_reentry is not None
    assert tuple(item.context_id for item in funded_reentry.overlays) == (
        "target-context",
        "victim-context",
    )
    assert funded_reentry.overlays[1].evidence_kind == "commit_ready_summary"
    assert funded_reentry.overlays[1].exclusive_reclaimable_bytes == 500


def test_live_prepare_certificate_defers_physical_revision_to_commit():
    current = {"present": True, "epoch": 2, "revision": 7}
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    runtime._joint_predictive_counts = Counter()
    runtime.controller = SimpleNamespace(
        page_index=SimpleNamespace(
            has_context=lambda _context_id: current["present"],
            context_epoch=lambda _context_id: current["epoch"],
            context_revision=lambda _context_id: current["revision"],
        )
    )
    source = SimpleNamespace(
        optional_metadata={
            "beliefkv_action_local_physical_overlay": SimpleNamespace(
                value={
                    "overlays": [
                        {
                            "context_id": "victim",
                            "context_epoch": 2,
                            "context_revision": 7,
                        }
                    ]
                }
            )
        }
    )
    certificate = {
        "action": "prepare_host",
        "target_context_id": "victim",
        "required_host_free_bytes": 100,
    }
    observation = RuntimeResourceObservation(
        ts_ms=5.0,
        hbm_capacity_bytes=1_000,
        hbm_used_bytes=0,
        host_capacity_bytes=1_000,
        host_used_bytes=0,
        host_free_bytes=1_000,
    )

    assert runtime._predictive_live_prepare_certificate_reasons(
        certificate, source, observation
    ) == ()
    current["revision"] = 8
    assert runtime._predictive_live_prepare_certificate_reasons(
        certificate, source, observation
    ) == ()
    assert runtime._joint_predictive_counts[
        "action_overlay_revision_drift"
    ] == 1


def test_live_prefetch_certificate_uses_live_rccg_not_compact_snapshot():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    live_graph = object()
    runtime.controller = SimpleNamespace(graph=live_graph)
    runtime._last_frontier_model_version = "frontier-v1"
    certificate = {
        "action": PredictiveActionKind.PREFETCH_GPU.value,
        "model_version": "frontier-v1",
    }

    with mock.patch(
        "beliefkv.runtime.sglang_v052rc1.validate_predictive_causal_certificate",
        return_value=(),
    ) as validate:
        assert runtime._predictive_live_prefetch_certificate_reasons(
            certificate
        ) == ()

    validate.assert_called_once_with(
        certificate,
        live_graph,
        current_model_version="frontier-v1",
    )
    certificate["action"] = PredictiveActionKind.PREPARE_HOST.value
    assert runtime._predictive_live_prefetch_certificate_reasons(
        certificate
    ) == ("unsupported_action_local_certificate",)


def test_action_local_overlay_revision_is_context_scoped():
    current = {"present": True, "epoch": 2, "revision": 7}
    page_index = SimpleNamespace(
        has_context=lambda _context_id: current["present"],
        context_epoch=lambda _context_id: current["epoch"],
        context_revision=lambda _context_id: current["revision"],
    )
    batch = SimpleNamespace(
        overlays=(
            SimpleNamespace(
                context_id="victim",
                context_epoch=2,
                context_revision=7,
            ),
        )
    )

    assert not EmbeddedSGLangRuntime._action_local_overlay_revision_changed(
        batch, page_index
    )
    current["revision"] = 8
    assert EmbeddedSGLangRuntime._action_local_overlay_revision_changed(
        batch, page_index
    )
    current.update(revision=7, epoch=3)
    assert EmbeddedSGLangRuntime._action_local_overlay_revision_changed(
        batch, page_index
    )
    current.update(epoch=2, present=False)
    assert EmbeddedSGLangRuntime._action_local_overlay_revision_changed(
        batch, page_index
    )
def test_predictive_risk_triggers_follow_park_and_reentry_boundaries():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    runtime.controller = BeliefKVController()
    initial = (
        RuntimeEvent(
            "workflow-start",
            1.0,
            RuntimeEventKind.WORKFLOW_START,
            "workflow",
        ),
        RuntimeEvent(
            "root-create",
            2.0,
            RuntimeEventKind.INVOCATION_CREATE,
            "workflow",
            invocation_id="root",
            context_id="ctx-root",
            context_epoch=0,
        ),
    )
    runtime.controller.process_runtime_events(initial)

    tool_start = RuntimeEvent(
        "tool-start",
        3.0,
        RuntimeEventKind.TOOL_START,
        "workflow",
        invocation_id="root",
        context_id="ctx-root",
        context_epoch=0,
        attributes={"tool_family": "shell"},
    )
    runtime.controller.process_runtime_event(tool_start)
    assert runtime._joint_shadow_predictive_risk_triggers((tool_start,)) == (
        ("prepare", "tool_start", "root", 0),
    )

    tool_end = RuntimeEvent(
        "tool-end",
        4.0,
        RuntimeEventKind.TOOL_END,
        "workflow",
        invocation_id="root",
        context_id="ctx-root",
        context_epoch=0,
    )
    runtime.controller.process_runtime_event(tool_end)
    assert runtime._joint_shadow_predictive_risk_triggers((tool_end,)) == (
        ("reentry", "tool_end", "root", 0),
    )

    child_create = RuntimeEvent(
        "child-create",
        5.0,
        RuntimeEventKind.INVOCATION_CREATE,
        "workflow",
        invocation_id="child",
        context_id="ctx-child",
        context_epoch=0,
        parent_invocation_id="root",
        parent_context_id="ctx-root",
    )
    spawn = RuntimeEvent(
        "spawn",
        6.0,
        RuntimeEventKind.SPAWN,
        "workflow",
        invocation_id="root",
        target_invocation_id="child",
        execution_mode=ExecutionMode.BACKGROUND,
    )
    join_create = RuntimeEvent(
        "join-create",
        7.0,
        RuntimeEventKind.JOIN_CREATE,
        "workflow",
        join_id="join",
        member_invocation_ids=("child",),
        attributes={"mode": "all"},
    )
    join_wait = RuntimeEvent(
        "join-wait",
        8.0,
        RuntimeEventKind.JOIN_WAIT,
        "workflow",
        invocation_id="root",
        join_id="join",
    )
    runtime.controller.process_runtime_events(
        (child_create, spawn, join_create, join_wait)
    )
    assert runtime._joint_shadow_predictive_risk_triggers((join_wait,)) == (
        ("prepare", "join_wait", "root", 0),
    )

    child_return = RuntimeEvent(
        "child-return",
        9.0,
        RuntimeEventKind.RETURN,
        "workflow",
        invocation_id="child",
        context_id="ctx-child",
        context_epoch=0,
        return_target_id="root",
    )
    runtime.controller.process_runtime_event(child_return)
    assert runtime._joint_shadow_predictive_risk_triggers((child_return,)) == (
        ("reentry", "return", "root", 0),
    )


def test_sglang_abort_result_is_openai_schema_complete_and_idempotent():
    from sglang.srt.managers.io_struct import AbortReq
    from sglang.srt.managers.tokenizer_manager import TokenizerManager

    manager = object.__new__(TokenizerManager)
    manager.server_args = SimpleNamespace(
        tokenizer_worker_num=1,
        weight_version="beliefkv-test-version",
    )
    state = SimpleNamespace(
        finished=False,
        finished_time=None,
        out_list=[],
        event=threading.Event(),
    )
    manager.rid_to_state = {"request-1": state}

    abort = AbortReq(rid="request-1")
    manager._handle_abort_req(abort)

    assert state.finished is True
    assert state.finished_time is not None
    assert state.event.is_set()
    assert "request-1" not in manager.rid_to_state
    assert state.out_list == [
        {
            "text": "",
            "output_ids": [],
            "meta_info": {
                "id": "request-1",
                "finish_reason": {
                    "type": "abort",
                    "message": "Abort before prefill",
                },
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "weight_version": "beliefkv-test-version",
            },
        }
    ]

    manager._handle_abort_req(abort)
    assert len(state.out_list) == 1


class _AbortScheduler:
    def __init__(self):
        self.runtime = None
        self.requests = []
        self.send_to_tokenizer = _Sender()

    def abort_request(self, request):
        self.requests.append(request)
        self.runtime.on_abort_request(request)


class _CloseRecorder:
    def __init__(self):
        self.close_count = 0
        self.events = []

    def close(self):
        self.close_count += 1

    def emit(self, event, ts_ms, **fields):
        self.events.append((event, ts_ms, fields))


class _AuditRecorder:
    def __init__(self):
        self.events = []

    def emit(self, event, ts_ms, **fields):
        self.events.append((event, ts_ms, fields))


def _predictive_causal_certificate(controller, model_version):
    snapshot = controller.graph.snapshot()
    return {
        "context_epochs": [
            [context_id, context["epoch"]]
            for context_id, context in sorted(snapshot["contexts"].items())
        ],
        "invocation_evidence": [
            [
                invocation_id,
                invocation["state"],
                invocation["updated_ts_ms"],
                invocation["join_id"],
            ]
            for invocation_id, invocation in sorted(
                snapshot["invocations"].items()
            )
        ],
        "join_evidence": [
            [join_id, join["mode"], join["satisfied"], join["completed"]]
            for join_id, join in sorted(snapshot["joins"].items())
        ],
        "communication_evidence": [
            [
                edge["source_invocation_id"],
                edge["target_invocation_id"],
                edge["count"],
                edge["last_ts_ms"],
            ]
            for edge in snapshot["communication_edges"]
        ],
        "model_version": model_version,
    }


def _predictive_beneficiary_evidence():
    return {
        "beneficiary_request_id": "request-beneficiary",
        "beneficiary_invocation_id": "inv-beneficiary",
        "beneficiary_context_id": "ctx-beneficiary",
        "beneficiary_context_epoch": 0,
        "beneficiary_startup_bytes": 50,
        "beneficiary_growth_bytes": 50,
        "predicted_block_time_ms": 500.0,
        "predicted_deficit_bytes": 50,
        "causal_package_generation": (
            "request-beneficiary:ctx-beneficiary:c0:50:50"
        ),
    }


def _predictive_beneficiary_runnable():
    return RunnableInvocation(
        "request-beneficiary", "wf-beneficiary", "inv-beneficiary",
        "ctx-beneficiary", 0, 1.0, 50, 50, 50,
        causal_class="engine_waiting:slot",
    )


def test_bounded_seed_hint_uses_first_deferred_engine_waiting_request():
    runnable = (
        RunnableInvocation(
            "running", "wf", "inv-running", "ctx-running", 0, 1.0, 64,
            64, 32,
        ),
        RunnableInvocation(
            "deferred", "wf", "inv-deferred", "ctx-deferred", 2, 2.0, 128,
            96, 48, causal_class="engine_waiting:ready",
        ),
    )
    view = OnlineJointPlanView(
        plan_id="bounded-seed",
        ordered_request_ids=("running",),
        immediate_request_ids=("running",),
        restore_requirements=(),
        deferred_request_ids=("deferred",),
        residency_intent_indices=(),
    )

    hint = EmbeddedSGLangRuntime._observed_seed_beneficiary_hint(
        view,
        runnable,
        priority_request_ids=("deferred", "running"),
        seed_generation=7,
        created_ts_ms=123.0,
    )

    assert hint is not None
    assert hint.plan_id == "bounded-seed"
    assert hint.request_id == "deferred"
    assert hint.context_epoch == 2
    assert hint.startup_bytes == 96
    assert hint.growth_bytes == 48
    assert hint.seed_generation == 7
    assert hint.created_ts_ms == 123.0



def test_bounded_seed_hints_keep_four_priority_ordered_candidates():
    runnable = tuple(
        RunnableInvocation(
            request_id=f"deferred-{index}",
            workflow_id="wf",
            invocation_id=f"inv-{index}",
            context_id=f"ctx-{index}",
            context_epoch=0,
            submitted_ts_ms=float(index),
            startup_bytes=64,
            admission_startup_bytes=32,
            admission_growth_bytes=16,
            causal_class="engine_waiting:ready",
            remaining_prefill_tokens=100 + index,
            remaining_output_tokens=200 + index,
            predicted_remaining_decode_tokens=20 + index,
            prediction_support_level="exact",
        )
        for index in range(5)
    )
    view = OnlineJointPlanView(
        plan_id="bounded-seed",
        ordered_request_ids=(),
        immediate_request_ids=(),
        restore_requirements=(),
        deferred_request_ids=tuple(item.request_id for item in runnable),
        residency_intent_indices=(),
    )

    hints = EmbeddedSGLangRuntime._observed_seed_beneficiary_hints(
        view,
        runnable,
        priority_request_ids=(
            "deferred-3", "deferred-1", "deferred-4", "deferred-2", "deferred-0"
        ),
        seed_generation=7,
        created_ts_ms=123.0,
        kv_bytes_per_token=2,
    )

    assert tuple(item.request_id for item in hints) == (
        "deferred-3", "deferred-1", "deferred-4", "deferred-2"
    )
    assert hints[0].remaining_prefill_bytes == 206
    assert hints[0].predicted_output_bytes == 46
    assert hints[0].prediction_support_level == "exact"


def test_bounded_seed_refreshes_schema_v5_prediction_without_retraction_flags():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    invocation = SimpleNamespace(
        invocation_id="inv-deferred",
        state=InvocationState.READY,
        agent_definition_id="coder",
        active_tool_family=None,
        active_tool_start_ms=None,
    )
    online = SimpleNamespace(
        boundary_history=[],
        context_tokens=4096,
        generated_tokens=0,
        tool_backend_class="unknown",
        tool_command_class="unknown",
    )
    prediction_payload = {
        "support_level": "backoff",
        "ood_reasons": [],
        "remaining_decode_tokens": {
            "values": [64.0],
            "probability_mass": [1.0],
        },
        "next_output_tokens": {
            "values": [32.0],
            "probability_mass": [1.0],
        },
    }
    frontier_model = SimpleNamespace(
        model_version="schema-v5",
        predict=mock.Mock(
            return_value=SimpleNamespace(
                to_dict=lambda: dict(prediction_payload)
            )
        ),
    )
    runtime.controller = SimpleNamespace(
        graph=SimpleNamespace(invocations={"inv-deferred": invocation}),
        predictor=SimpleNamespace(
            frontier_model=frontier_model,
            features={"inv-deferred": online},
        ),
    )
    runtime.config = SimpleNamespace(predictive_risk_shadow_enabled=True)
    runtime._last_frontier_features = {}
    runtime._last_frontier_predictions = {}
    runtime._last_frontier_model_version = None
    runtime._bounded_seed_prediction_signature_by_invocation = {}
    runtime._joint_predictive_counts = Counter()
    runtime._joint_shadow_timing_samples = {}
    runnable = (
        RunnableInvocation(
            request_id="deferred",
            workflow_id="wf",
            invocation_id="inv-deferred",
            context_id="ctx-deferred",
            context_epoch=0,
            submitted_ts_ms=1.0,
            startup_bytes=4096,
            admission_startup_bytes=1024,
            admission_growth_bytes=512,
            causal_class="engine_waiting:ready",
            remaining_prefill_tokens=128,
            remaining_output_tokens=4096,
            prediction_support_level="unavailable",
        ),
    )
    view = OnlineJointPlanView(
        plan_id="bounded-seed",
        ordered_request_ids=(),
        immediate_request_ids=(),
        restore_requirements=(),
        deferred_request_ids=("deferred",),
        residency_intent_indices=(),
    )
    feature = SimpleNamespace(to_dict=lambda: {"invocation_id": "inv-deferred"})

    with mock.patch(
        "beliefkv.runtime.sglang_v052rc1.build_invocation_frontier_features",
        return_value={"inv-deferred": feature},
    ) as build_features:
        refreshed = runtime._refresh_bounded_seed_frontier_predictions(
            view,
            runnable,
            priority_request_ids=("deferred",),
            now_ms=100.0,
        )
        cached = runtime._refresh_bounded_seed_frontier_predictions(
            view,
            runnable,
            priority_request_ids=("deferred",),
            now_ms=101.0,
        )

    assert refreshed[0].predicted_remaining_decode_tokens == 64.0
    assert refreshed[0].predicted_next_output_tokens == 32.0
    assert refreshed[0].prediction_support_level == "backoff"
    assert cached[0].predicted_remaining_decode_tokens == 64.0
    assert frontier_model.predict.call_count == 1
    assert build_features.call_count == 1
    assert runtime._joint_predictive_counts[
        "bounded_seed_prediction_inferred"
    ] == 1
    assert runtime._joint_predictive_counts[
        "bounded_seed_prediction_cache_hit"
    ] == 1
class _EventBatchRecorder:
    def __init__(self):
        self.events = []

    def emit_batch(self, events):
        self.events.extend(events)


class _NoBooleanSequence:
    def __init__(self, length):
        self.length = length

    def __len__(self):
        return self.length

    def __bool__(self):
        raise RuntimeError("sequence truth value is undefined")


class _ForwardMode:
    def __init__(self, name):
        self.name = name

    def is_decode(self):
        return self.name == "DECODE"

    def is_extend(self):
        return self.name in {"EXTEND", "MIXED"}

    def is_mixed(self):
        return self.name == "MIXED"

    def is_idle(self):
        return self.name == "IDLE"

    def is_dummy_first(self):
        return self.name == "DUMMY_FIRST"


class _TransferTickBridge:
    def __init__(self, transfer, *, admission=None, acks=(), telemetry=()):
        self.transfer = transfer
        self.admission = admission
        self.acks = list(acks)
        self.telemetry = list(telemetry)

    def drain_acks(self):
        result = self.acks
        self.acks = []
        return result

    def drain_transfer_telemetry(self):
        result = self.telemetry
        self.telemetry = []
        return result

    def scheduler_step(
        self,
        now_ms,
        *,
        drain_acks,
        allow_reactive_transfer=True,
    ):
        self.drain_acks_argument = drain_acks
        self.allow_reactive_transfer = allow_reactive_transfer
        return SimpleNamespace(
            now_ms=now_ms,
            admission=self.admission,
            transfer=self.transfer,
            local_acks=(),
        )


@dataclass
class _AbortRequest:
    rid: str = ""
    abort_all: bool = False


def resolved(kind, handle, action):
    command = ControlCommand(
        command_id=f"cmd-{kind.value}",
        kind=kind,
        created_ts_ms=1,
        context_id="ctx",
        context_epoch=0,
        target_bytes=400,
    )
    page_action = ResolvedPageAction(handle, action, 400)
    return ResolvedCommand(command, (page_action,), 400, "resolved")


def resolved_bundle(kind, actions, *, closure_handles=None):
    page_actions = tuple(
        ResolvedPageAction(handle, action, size_bytes)
        for handle, action, size_bytes in actions
    )
    handles = tuple(
        sorted(
            closure_handles
            if closure_handles is not None
            else (item.handle for item in page_actions)
        )
    )
    intent = PhysicalBundleIntent(
        bundle_id="bundle-test",
        closure_handles=handles,
        page_actions=page_actions,
        generation_fingerprint="bundle-generation-test",
        closure_bytes=sum(item.size_bytes for item in page_actions),
        expected_reclaimable_bytes=(
            sum(item.size_bytes for item in page_actions)
            if kind == CommandKind.OFFLOAD_CONTEXT
            else 0
        ),
    )
    command = ControlCommand(
        command_id=f"cmd-bundle-{kind.value}",
        kind=kind,
        created_ts_ms=1,
        context_id="ctx",
        context_epoch=0,
        target_bytes=intent.closure_bytes,
        physical_bundle=intent,
    )
    return ResolvedCommand(
        command,
        page_actions,
        intent.closure_bytes,
        "physical_bundle_resolved",
        closure_fingerprint=intent.generation_fingerprint,
    )


class SGLangBackendTest(unittest.TestCase):

    def test_predictive_bundle_envelope_rejects_each_value_bound(self):
        base_intent = PredictiveIntent(
            intent_id="intent-envelope",
            source_joint_plan_id="source-plan",
            source_snapshot_id="snapshot",
            package_id="package-envelope",
            model_version="frontier-v1",
            action=PredictiveActionKind.PREPARE_HOST,
            invocation_id="inv",
            expected_invocation_state="wait_tool",
            context_id="ctx",
            context_epoch=0,
            generated_ts_ms=1.0,
            remaining_window_low_ms=1_000.0,
            transfer_p95_ms=10.0,
            target_bytes_hint=300,
            min_reclaimable_bytes=300,
            max_cross_context_bytes=0,
            max_copy_bytes=300,
            causal_certificate={"model_version": "frontier-v1"},
            required_prediction_heads=("remaining_window",),
            prediction_head_support=(("remaining_window", "exact"),),
            calibration_coverage=0.95,
            future_hbm_feasibility_probability=1.0,
            expected_benefit_ms=5.0,
            shape_fingerprint="shape-v1",
            predicted_extent_count=1,
            maximum_transfer_ms=12.0,
            maximum_stall_ms=10.0,
            morphology_slack_ms=100.0,
            **_predictive_beneficiary_evidence(),
        )
        valid_preview = SimpleNamespace(
            copy_bytes=300,
            bundle=SimpleNamespace(
                cross_context_action_bytes=0,
                exclusive_action_bytes=300,
            ),
        )
        cases = (
            (
                replace(base_intent, target_bytes_hint=299, max_copy_bytes=299),
                valid_preview,
                "copy_bytes_exceed_envelope",
            ),
            (
                base_intent,
                SimpleNamespace(
                    copy_bytes=300,
                    bundle=SimpleNamespace(
                        cross_context_action_bytes=1,
                        exclusive_action_bytes=300,
                    ),
                ),
                "cross_context_bytes_exceed_envelope",
            ),
            (
                replace(base_intent, min_reclaimable_bytes=301),
                valid_preview,
                "exclusive_reclaim_below_envelope",
            ),
        )
        for intent, preview, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    _predictive_bundle_envelope_reasons(intent, preview),
                    (expected,),
                )

    def test_empty_or_rejected_predictive_result_preserves_observed_plan(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            host_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            predictor_model_path="/tmp/frontier.json",
            gpu_service_model_path="/tmp/service.json",
            joint_policy_enabled=True,
            predictive_risk_shadow_enabled=True,
            predictive_joint_overlay_enabled=True,
        )
        runtime.audit = _AuditRecorder()
        runtime._last_predictive_risk_result_sequence = 0
        runtime._joint_shadow_timing_samples = {}
        runtime._joint_predictive_counts = Counter()
        runtime._last_joint_decision_plan_id = "observed-plan"
        observed_decision = object()
        runtime._current_online_joint_decision = observed_decision
        runtime._latest_predictive_intent = None
        runtime._maybe_persist_predictive_candidate_snapshot = (
            lambda *_args, **_kwargs: None
        )
        observation = RuntimeResourceObservation(
            ts_ms=10.0,
            hbm_capacity_bytes=1_000,
            hbm_used_bytes=0,
            host_capacity_bytes=1_000,
            host_used_bytes=0,
            host_free_bytes=1_000,
        )

        candidate = PredictiveIntent(
            intent_id="intent-rejected",
            source_joint_plan_id="source-plan",
            source_snapshot_id="snapshot",
            package_id="package-rejected",
            model_version="frontier-v1",
            action=PredictiveActionKind.PREPARE_HOST,
            invocation_id="inv",
            expected_invocation_state="wait_tool",
            context_id="ctx",
            context_epoch=0,
            generated_ts_ms=1.0,
            remaining_window_low_ms=1_000.0,
            transfer_p95_ms=10.0,
            target_bytes_hint=100,
            min_reclaimable_bytes=100,
            max_cross_context_bytes=0,
            max_copy_bytes=100,
            causal_certificate={"model_version": "frontier-v1"},
            required_prediction_heads=("remaining_window",),
            prediction_head_support=(("remaining_window", "exact"),),
            calibration_coverage=0.95,
            future_hbm_feasibility_probability=1.0,
            expected_benefit_ms=5.0,
            shape_fingerprint="shape-v1",
            predicted_extent_count=1,
            maximum_transfer_ms=12.0,
            maximum_stall_ms=10.0,
            morphology_slack_ms=100.0,
            **_predictive_beneficiary_evidence(),
        )
        for sequence, predictive_intent in enumerate((None, candidate), start=1):
            shadow = SimpleNamespace(
                predictive_intent=predictive_intent,
                to_dict=lambda: {
                    "selected_action": "observed_baseline",
                    "candidate_summaries": [],
                    "belief_compose_ms": 0.0,
                    "candidate_generation_ms": 0.0,
                    "deterministic_preflight_ms": 0.0,
                    "scenario_risk_ms": 0.0,
                },
            )
            result = SimpleNamespace(
                sequence=sequence,
                queue_wait_ms=0.0,
                compute_ms=1.0,
                shadow=shadow,
                submitted_monotonic_ms=0.0,
                source_joint_sequence=sequence,
                eligibility_ms=0.1,
                error=None,
            )
            runtime.predictive_risk_worker = SimpleNamespace(
                latest=lambda **_kwargs: result
            )

            runtime._drain_predictive_risk_result(
                observation,
                current_policy_input=None,
                current_transfer_epoch=0,
            )

            self.assertIs(runtime._current_online_joint_decision, observed_decision)
            self.assertEqual(runtime._last_joint_decision_plan_id, "observed-plan")

        progress = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "predictive_risk_progress"
        ]
        self.assertEqual(len(progress), 2)
        self.assertEqual(progress[-1]["candidate_count"], 0)
        self.assertEqual(progress[-1]["fresh_positive_package_count"], 0)
        self.assertEqual(progress[-1]["hbm_pressure"], 0.0)

        runtime._latest_predictive_intent = candidate
        shadow = SimpleNamespace(
            predictive_intent=candidate,
            to_dict=lambda: {
                "selected_action": "prepare_host",
                "candidate_summaries": [],
                "belief_compose_ms": 0.0,
                "candidate_generation_ms": 0.0,
                "deterministic_preflight_ms": 0.0,
                "scenario_risk_ms": 0.0,
            },
        )
        result = SimpleNamespace(
            sequence=3,
            queue_wait_ms=0.0,
            compute_ms=1.0,
            shadow=shadow,
            submitted_monotonic_ms=0.0,
            source_joint_sequence=3,
            eligibility_ms=0.1,
            error=None,
        )
        runtime.predictive_risk_worker = SimpleNamespace(
            latest=lambda **_kwargs: result
        )
        runtime.controller = SimpleNamespace(graph=object())
        with mock.patch(
            "beliefkv.runtime.sglang_v052rc1.validate_predictive_causal_certificate",
            return_value=(),
        ):
            runtime._drain_predictive_risk_result(
                observation,
                current_policy_input=SimpleNamespace(
                    runtime_graph=SimpleNamespace(state={})
                ),
                current_transfer_epoch=0,
            )

        self.assertIs(runtime._current_online_joint_decision, observed_decision)
        self.assertEqual(runtime._last_joint_decision_plan_id, "observed-plan")
        self.assertEqual(
            runtime._joint_predictive_counts["semantic_intent_refresh_unchanged"],
            1,
        )

    def test_config_uses_authoritative_host_allocator_capacity(self):
        scheduler = SimpleNamespace(
            max_total_num_tokens=100,
            tree_cache=SimpleNamespace(
                token_to_kv_pool_host=SimpleNamespace(size=320)
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "beliefkv.json"
            path.write_text(
                json.dumps(
                    {
                        "hbm_capacity_bytes": 1000,
                        "host_capacity_bytes": 9999,
                        "reserve_hbm_bytes": 100,
                        "kv_bytes_per_token": 10,
                    }
                ),
                encoding="utf-8",
            )
            config = EmbeddedSGLangRuntime._load_config(scheduler, str(path))

        self.assertEqual(config.host_capacity_bytes, 3200)

    def test_native_hicache_telemetry_is_attributed_without_explicit_duplication(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(kv_bytes_per_token=10)
        runtime.controller = BeliefKVController(runtime.config)
        runtime.controller.process_runtime_events(
            (
                RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
                RuntimeEvent(
                    "create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="child",
                    context_id="ctx-child",
                    context_epoch=0,
                ),
            )
        )
        runtime.registry = SGLangNodeRegistry()
        node = _Node(7)
        handle = runtime.registry.register(node)
        runtime.controller.page_index.register_page(handle, size_bytes=40)
        runtime.controller.page_index.bind_pages("ctx-child", 0, (handle,))
        runtime.audit = _AuditRecorder()
        runtime.transfer_telemetry_log = None
        runtime._now_ms = lambda: 30.0
        record = {
            "operation_id": "h2d-7-1",
            "backend_operation_id": 7,
            "direction": "h2d",
            "source": "native_demand_load",
            "submit_ts_ms": 10.0,
            "complete_ts_ms": 20.0,
            "token_count": 4,
            "node_ids": (7,),
            "reason": "",
            "host_copy_state": "present",
            "pinned_host": True,
            "allocator_submit_ms": 0.25,
            "native_inflight_operation_count": 2,
            "native_inflight_token_count": 8,
        }

        runtime.on_hicache_transfer_completed(record)
        runtime.on_hicache_transfer_completed({**record, "source": "explicit"})

        self.assertEqual(len(runtime.audit.events), 1)
        event, _, fields = runtime.audit.events[0]
        self.assertEqual(event, "transfer_telemetry")
        self.assertEqual(fields["actual_bytes"], 40)
        self.assertEqual(fields["direction"], "h2d")
        self.assertEqual(fields["command_kind"], "native_demand_load")
        self.assertEqual(fields["telemetry_origin"], "native_hicache_callback")
        self.assertEqual(fields["context_id"], "ctx-child")
        self.assertEqual(fields["owner_context_ids"], ("ctx-child",))
        self.assertEqual(fields["host_copy_state"], "present")
        self.assertIs(fields["pinned_host"], True)
        self.assertEqual(fields["native_concurrent_bytes"], 80)
        self.assertEqual(fields["extent_count"], 1)
        self.assertEqual(fields["extent_bytes_min"], 40)
        self.assertEqual(fields["extent_bytes_p50"], 40)
        self.assertEqual(fields["extent_bytes_max"], 40)
        self.assertEqual(fields["small_extent_ratio"], 1.0)
        self.assertEqual(fields["allocator_submit_ms"], 0.25)
        self.assertEqual(fields["callback_overhead_ms"], 10.0)
        self.assertEqual(
            fields["native_inflight_operation_count_at_submit"], 2
        )
        self.assertFalse(fields["start_timestamp_observed"])

    def test_native_hicache_telemetry_preserves_submit_time_ownership(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(kv_bytes_per_token=10)
        runtime.controller = BeliefKVController(runtime.config)
        runtime.controller.process_runtime_events(
            (
                RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
                RuntimeEvent(
                    "create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="child",
                    context_id="ctx-child",
                    context_epoch=0,
                ),
            )
        )
        runtime.registry = SGLangNodeRegistry()
        node = _Node(7)
        handle = runtime.registry.register(node)
        runtime.controller.page_index.register_page(handle, size_bytes=40)
        runtime.controller.page_index.bind_pages("ctx-child", 0, (handle,))
        runtime.audit = _AuditRecorder()
        runtime.transfer_telemetry_log = None
        runtime._now_ms = lambda: 30.0
        record = {
            "operation_id": "d2h-7-1",
            "backend_operation_id": 7,
            "direction": "d2h",
            "source": "native_write_back",
            "submit_ts_ms": 10.0,
            "complete_ts_ms": 20.0,
            "token_count": 4,
            "node_ids": (7,),
        }

        attribution = runtime.on_hicache_transfer_submitted(record)
        self.assertEqual(
            attribution["page_handles"],
            ((handle.page_id, handle.allocation_generation),),
        )
        replacement_handle = runtime.registry.register(_Node(7))
        self.assertNotEqual(replacement_handle, handle)
        runtime.controller.page_index.unbind_context("ctx-child")
        runtime.on_hicache_transfer_completed({**record, **attribution})

        _, _, fields = runtime.audit.events[0]
        self.assertEqual(fields["owner_context_ids"], ("ctx-child",))
        self.assertEqual(fields["owner_context_epochs"], (("ctx-child", 0),))
        self.assertEqual(fields["context_epoch"], 0)
        self.assertEqual(fields["extent_count"], 1)
        self.assertEqual(fields["extent_bytes_min"], 40)
        self.assertEqual(fields["ownership_attribution_semantics"], "submit_snapshot")
        self.assertEqual(
            fields["radix_page_handles"],
            ((handle.page_id, handle.allocation_generation),),
        )
        self.assertEqual(
            fields["ownership_revision"], attribution["ownership_revision"]
        )

    def test_online_joint_plan_requires_shadow_observed_worker(self):
        scheduler = SimpleNamespace(
            enable_hierarchical_cache=True,
            tree_cache=_TreeCache(),
        )

        with self.assertRaisesRegex(
            SGLangBackendError,
            "online observed JointPlan requires its validated shadow worker",
        ):
            EmbeddedSGLangRuntime(
                scheduler,
                config=BeliefKVConfig(
                    joint_policy_enabled=True,
                    joint_policy_shadow_mode=False,
                ),
            )

    def test_gpu_service_observer_pairs_overlap_launch_and_completion(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            queue_service_observer_enabled=True,
            queue_service_observer_max_samples=10,
        )
        runtime.audit = _AuditRecorder()
        runtime.scheduler = SimpleNamespace(
            server_args=SimpleNamespace(num_continuous_decode_steps=1)
        )
        runtime._gpu_service_launches = deque()
        runtime._gpu_service_sequence = 0
        runtime._gpu_service_sample_count = 0
        runtime._gpu_service_previous_completion_ms = None
        runtime._now_ms = lambda: 12.5
        requests = [
            SimpleNamespace(
                rid=f"request-{index}",
                output_ids=[],
                beliefkv_metadata=BeliefKVRequestMetadata(
                    f"service-calibration:train:decode-b1-r0-i{index}",
                    f"inv-{index}",
                    f"ctx-{index}",
                    0,
                ),
            )
            for index in range(2)
        ]
        batch = SimpleNamespace(
            reqs=requests,
            forward_mode=_ForwardMode("DECODE"),
        )

        runtime._observe_gpu_batch_launch(batch, 10.0)
        for request in requests:
            request.output_ids.append(1)
        runtime.on_batch_completed(batch)

        event, _, fields = runtime.audit.events[-1]
        self.assertEqual(event, "gpu_service_sample")
        self.assertEqual(fields["phase"], "decode")
        self.assertEqual(fields["tokens"], 2)
        self.assertEqual(fields["batch_size"], 2)
        self.assertEqual(fields["split"], "train")
        self.assertEqual(fields["calibration_kind"], "decode")
        self.assertEqual(fields["episode_id"], "train:decode-b1-r0")
        self.assertEqual(fields["elapsed_ms"], 2.5)
        self.assertEqual(fields["launch_to_completion_ms"], 2.5)
        self.assertEqual(
            [item["request_id"] for item in fields["request_samples"]],
            ["request-0", "request-1"],
        )
        self.assertEqual(
            [item["token_delta"] for item in fields["request_samples"]],
            [1, 1],
        )
        self.assertTrue(
            all(
                item["token_delta_semantics"] == "observed_output_ids_delta"
                for item in fields["request_samples"]
            )
        )
        self.assertEqual(len(runtime._gpu_service_launches), 0)

    def test_gpu_service_performance_mode_aggregates_without_sample_event(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            performance_mode=True,
            queue_service_observer_enabled=True,
            queue_service_observer_include_runtime_batches=True,
        )
        runtime.audit = _AuditRecorder()
        runtime.scheduler = SimpleNamespace(
            server_args=SimpleNamespace(num_continuous_decode_steps=1)
        )
        runtime._gpu_service_launches = deque()
        runtime._gpu_service_sequence = 0
        runtime._gpu_service_sample_count = 0
        runtime._gpu_service_previous_completion_ms = None
        runtime._gpu_service_observer_timing_samples = deque(maxlen=16)
        runtime._gpu_service_performance_aggregates = {
            phase: {
                "sample_count": 0,
                "tokens": 0,
                "elapsed_ms": 0.0,
                "batch_histogram": Counter(),
            }
            for phase in ("prefill", "decode")
        }
        runtime._now_ms = lambda: 12.5
        requests = [
            SimpleNamespace(
                rid=f"request-{index}",
                output_ids=[],
                beliefkv_metadata=BeliefKVRequestMetadata(
                    "workflow",
                    f"inv-{index}",
                    f"ctx-{index}",
                    0,
                ),
            )
            for index in range(2)
        ]
        batch = SimpleNamespace(
            reqs=requests,
            forward_mode=_ForwardMode("DECODE"),
        )

        runtime._observe_gpu_batch_launch(batch, 10.0)
        runtime.on_batch_completed(batch)

        self.assertEqual(runtime.audit.events, [])
        aggregate = runtime._gpu_service_performance_aggregates["decode"]
        self.assertEqual(aggregate["sample_count"], 1)
        self.assertEqual(aggregate["tokens"], 2)
        self.assertEqual(aggregate["elapsed_ms"], 2.5)
        self.assertEqual(aggregate["batch_histogram"], Counter({2: 1}))

    def test_gpu_service_observer_removes_overlap_queue_time(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(queue_service_observer_enabled=True)
        runtime.audit = _AuditRecorder()
        runtime.scheduler = SimpleNamespace(
            server_args=SimpleNamespace(num_continuous_decode_steps=1)
        )
        runtime._gpu_service_launches = deque()
        runtime._gpu_service_sequence = 0
        runtime._gpu_service_sample_count = 0
        runtime._gpu_service_previous_completion_ms = 10.0
        runtime._now_ms = lambda: 15.0
        request = SimpleNamespace(
            rid="request-0",
            beliefkv_metadata=BeliefKVRequestMetadata(
                "service-calibration:holdout:decode-b1-r0-i0",
                "inv-0",
                "ctx-0",
                0,
            ),
        )
        batch = SimpleNamespace(
            reqs=[request],
            forward_mode=_ForwardMode("DECODE"),
        )

        runtime._observe_gpu_batch_launch(batch, 2.0)
        runtime.on_batch_completed(batch)

        _, _, fields = runtime.audit.events[-1]
        self.assertEqual(fields["service_start_ts_ms"], 10.0)
        self.assertEqual(fields["service_elapsed_ms"], 5.0)
        self.assertEqual(fields["launch_to_completion_ms"], 13.0)
        self.assertEqual(fields["elapsed_ms"], 5.0)

    def test_gpu_service_observer_excludes_wrong_calibration_phase(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(queue_service_observer_enabled=True)
        runtime.audit = _AuditRecorder()
        runtime.scheduler = SimpleNamespace(
            server_args=SimpleNamespace(num_continuous_decode_steps=1)
        )
        runtime._gpu_service_launches = deque()
        runtime._gpu_service_sequence = 0
        runtime._gpu_service_sample_count = 0
        runtime._gpu_service_previous_completion_ms = None
        runtime._now_ms = lambda: 2.0
        request = SimpleNamespace(
            rid="prefill-case-output-token",
            beliefkv_metadata=BeliefKVRequestMetadata(
                "service-calibration:train:prefill-512-0",
                "inv-0",
                "ctx-0",
                0,
            ),
        )
        batch = SimpleNamespace(
            reqs=[request],
            forward_mode=_ForwardMode("DECODE"),
        )

        runtime._observe_gpu_batch_launch(batch, 1.0)
        runtime.on_batch_completed(batch)

        self.assertEqual(runtime.audit.events, [])
        self.assertEqual(runtime._gpu_service_sample_count, 0)

    def test_gpu_service_observer_excludes_non_calibration_batches(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(queue_service_observer_enabled=True)
        runtime.audit = _AuditRecorder()
        runtime.scheduler = SimpleNamespace(server_args=SimpleNamespace())
        runtime._gpu_service_launches = deque()
        runtime._gpu_service_sequence = 0
        runtime._gpu_service_sample_count = 0
        runtime._gpu_service_previous_completion_ms = None
        runtime._now_ms = lambda: 2.0
        batch = SimpleNamespace(
            reqs=[SimpleNamespace(rid="native", beliefkv_metadata=None)],
            forward_mode=_ForwardMode("EXTEND"),
            extend_num_tokens=100,
        )

        runtime._observe_gpu_batch_launch(batch, 1.0)
        runtime.on_batch_completed(batch)

        self.assertEqual(runtime.audit.events, [])
        self.assertEqual(runtime._gpu_service_sample_count, 0)

    def test_gpu_service_observer_records_tagged_runtime_batch_context(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            queue_service_observer_enabled=True,
            queue_service_observer_include_runtime_batches=True,
        )
        runtime.audit = _AuditRecorder()
        runtime.scheduler = SimpleNamespace(
            server_args=SimpleNamespace(num_continuous_decode_steps=1)
        )
        runtime._gpu_service_launches = deque()
        runtime._gpu_service_sequence = 0
        runtime._gpu_service_sample_count = 0
        runtime._gpu_service_previous_completion_ms = None
        runtime._now_ms = lambda: 4.0
        request = SimpleNamespace(
            rid="runtime-request",
            fill_ids=list(range(4096)),
            origin_input_ids=list(range(4096)),
            output_ids=[1, 2],
            beliefkv_metadata=BeliefKVRequestMetadata(
                "workflow",
                "invocation",
                "context",
                0,
            ),
        )
        batch = SimpleNamespace(
            reqs=[request],
            forward_mode=_ForwardMode("DECODE"),
        )

        runtime._observe_gpu_batch_launch(batch, 2.0)
        runtime.on_batch_completed(batch)

        event, _, fields = runtime.audit.events[-1]
        self.assertEqual(event, "gpu_service_sample")
        self.assertEqual(fields["observation_scope"], "runtime")
        self.assertEqual(fields["phase"], "decode")
        self.assertEqual(fields["sequence_tokens_before"], [4096])
        self.assertEqual(fields["max_sequence_tokens_before"], 4096)
        self.assertEqual(fields["effective_sequence_tokens_before"], [4098])
        self.assertEqual(fields["max_effective_sequence_tokens_before"], 4098)
        self.assertEqual(
            fields["request_samples"][0]["effective_sequence_tokens_before"],
            4098,
        )
        self.assertEqual(fields["workflow_ids"], ["workflow"])

    def test_request_physical_checkpoint_separates_allocator_and_radix_growth(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=10_000,
            host_capacity_bytes=10_000,
            reserve_hbm_bytes=0,
            kv_bytes_per_token=10,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.controller.page_index.register_context("ctx", "wf", 0)
        first = PageHandle(1, 0)
        runtime.controller.page_index.register_page(first, size_bytes=100)
        runtime.controller.page_index.bind_pages("ctx", 0, (first,))
        runtime.scheduler = SimpleNamespace(
            page_size=1,
            token_to_kv_pool_allocator=SimpleNamespace(page_size=1),
        )
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 20.0
        runtime._tree_dirty = False
        runtime._request_physical_start_by_id = {}
        runtime._pending_request_physical_finish_by_id = {}
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(
            rid="request",
            origin_input_ids=_NoBooleanSequence(10),
            prefix_indices=_NoBooleanSequence(5),
        )

        runtime._capture_request_physical_start(request, metadata, 10.0)
        second = PageHandle(2, 0)
        runtime.controller.page_index.register_page(second, size_bytes=40)
        runtime.controller.page_index.bind_pages("ctx", 0, (second,))
        runtime._queue_request_physical_finish(
            request,
            metadata,
            output_tokens=2,
            cache_commit_tokens=11,
        )
        runtime._flush_request_physical_finishes()

        event, _, fields = runtime.audit.events[-1]
        self.assertEqual(event, "request_physical_delta")
        self.assertEqual(fields["context_path_bytes_before"], 100)
        self.assertEqual(fields["context_path_bytes_after"], 140)
        self.assertEqual(fields["context_path_growth_bytes"], 40)
        self.assertEqual(fields["cache_commit_tokens"], 11)
        self.assertEqual(fields["allocator_growth_bytes_upper_bound"], 60)
        self.assertTrue(fields["allocator_growth_exact"])
        self.assertEqual(fields["new_extent_bytes"], 40)

    def test_abort_race_preserves_physical_start_until_late_finish(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=10_000,
            host_capacity_bytes=10_000,
            reserve_hbm_bytes=0,
            kv_bytes_per_token=10,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.controller.page_index.register_context("ctx", "wf", 0)
        first = PageHandle(1, 0)
        runtime.controller.page_index.register_page(first, size_bytes=100)
        runtime.controller.page_index.bind_pages("ctx", 0, (first,))
        runtime.scheduler = SimpleNamespace(
            page_size=1,
            token_to_kv_pool_allocator=SimpleNamespace(page_size=1),
        )
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 20.0
        runtime._tree_dirty = False
        runtime._request_physical_start_by_id = {}
        runtime._aborted_request_physical_start_by_id = {}
        runtime._pending_request_physical_finish_by_id = {}
        runtime._request_submitted_ts_by_id = {"request": 1.0}
        runtime._queue_timeout_request_ids = set()
        runtime._execution_timeout_request_ids = set()
        runtime._retracted_engine_request_ids = set()
        runtime._pending_selective_retraction_ids = set()
        runtime._retraction_cooldown_until_by_request = {}
        runtime._ordinary_restore_capacity_waiters = set()
        runtime._ordinary_native_fallback_signature_by_request = {
            "request": ("ctx", 0, ("page:1:0",))
        }
        runtime._ordinary_fallback_blocked_capacity = {
            "request": (10, 10.0)
        }
        runtime._current_ordinary_starvation_priority = ("request",)
        runtime._finish_restore_obligation = lambda *args, **kwargs: None
        runtime._cancel_restore_service_grace = lambda *args, **kwargs: None
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        runtime._request_metadata_by_id = {"request": metadata}
        request = SimpleNamespace(
            rid="request",
            origin_input_ids=_NoBooleanSequence(10),
            prefix_indices=_NoBooleanSequence(5),
        )

        runtime._capture_request_physical_start(request, metadata, 10.0)
        runtime.on_abort_request(SimpleNamespace(abort_all=False, rid="request"))
        self.assertFalse(runtime._ordinary_native_fallback_signature_by_request)
        self.assertFalse(runtime._ordinary_fallback_blocked_capacity)
        self.assertEqual(runtime._current_ordinary_starvation_priority, ())
        second = PageHandle(2, 0)
        runtime.controller.page_index.register_page(second, size_bytes=40)
        runtime.controller.page_index.bind_pages("ctx", 0, (second,))
        runtime._queue_request_physical_finish(
            request,
            metadata,
            output_tokens=2,
            cache_commit_tokens=11,
        )
        runtime._flush_request_physical_finishes()

        event, _, fields = runtime.audit.events[-1]
        self.assertEqual(event, "request_physical_delta")
        self.assertTrue(fields["request_aborted_during_finish"])
        self.assertEqual(fields["request_abort_reason"], "request_aborted")
        self.assertEqual(fields["context_path_growth_bytes"], 40)
        self.assertNotIn(
            "request", runtime._aborted_request_physical_start_by_id
        )

    def test_execution_timeout_falls_back_to_physical_start_without_ledger(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        metadata = BeliefKVRequestMetadata(
            "wf",
            "inv",
            "ctx",
            0,
            execution_timeout_s=2.0,
        )
        aborted = []
        runtime.scheduler = SimpleNamespace(
            abort_request=lambda request: aborted.append(request)
        )
        runtime.audit = _AuditRecorder()
        runtime._request_metadata_by_id = {"request": metadata}
        runtime._request_submitted_ts_by_id = {"request": -100_000.0}
        runtime._request_physical_start_by_id = {
            "request": {"checkpoint_ts_ms": 1_000.0}
        }
        runtime._execution_timeout_request_ids = set()
        runtime._terminal_cancelled_request_ids = set()
        runtime._ordinary_native_fallback_signature_by_request = {
            "request": ("ctx", 0, ("page:1:0",))
        }
        runtime._ordinary_fallback_blocked_capacity = {
            "request": (10, 2_000.0)
        }
        runtime._current_ordinary_starvation_priority = ("request",)

        runtime._enforce_execution_timeouts(now_ms=2_999.0)
        self.assertEqual(aborted, [])

        runtime._enforce_execution_timeouts(now_ms=3_001.0)

        self.assertEqual(len(aborted), 1)
        self.assertEqual(aborted[0].rid, "request")
        self.assertIn("request", runtime._terminal_cancelled_request_ids)
        self.assertFalse(runtime._ordinary_native_fallback_signature_by_request)
        self.assertFalse(runtime._ordinary_fallback_blocked_capacity)
        self.assertEqual(runtime._current_ordinary_starvation_priority, ())
        timeout_events = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "request_execution_timeout"
        ]
        self.assertEqual(timeout_events[0]["execution_started_ts_ms"], 1_000.0)
        self.assertEqual(
            timeout_events[0]["timeout_scope"],
            "physical_start_to_scheduler_abort_fallback",
        )

    def test_execution_timeout_uses_last_completed_gpu_service(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        metadata = BeliefKVRequestMetadata(
            "wf",
            "inv",
            "ctx",
            0,
            execution_timeout_s=2.0,
        )
        aborted = []
        runtime.scheduler = SimpleNamespace(
            abort_request=lambda request: aborted.append(request)
        )
        runtime.audit = _AuditRecorder()
        runtime._request_metadata_by_id = {"request": metadata}
        runtime._request_physical_start_by_id = {
            "request": {"checkpoint_ts_ms": 1_000.0}
        }
        runtime._execution_timeout_request_ids = set()
        runtime._terminal_cancelled_request_ids = set()
        runtime._lock_service_ledger = RequestServiceLedger()
        runtime._lock_service_ledger.observe_selected(
            request_id="request",
            workflow_id="wf",
            invocation_id="inv",
            context_id="ctx",
            ts_ms=1_000.0,
        )
        runtime._lock_service_ledger.observe_completed(
            "request",
            ts_ms=2_500.0,
            phase="decode",
        )

        runtime._enforce_execution_timeouts(now_ms=3_001.0)
        self.assertEqual(aborted, [])

        runtime._enforce_execution_timeouts(now_ms=4_501.0)

        self.assertEqual(len(aborted), 1)
        timeout_events = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "request_execution_timeout"
        ]
        self.assertEqual(timeout_events[0]["execution_elapsed_ms"], 3_501.0)
        self.assertEqual(timeout_events[0]["gpu_service_no_progress_ms"], 2_001.0)
        self.assertEqual(timeout_events[0]["last_gpu_service_ts_ms"], 2_500.0)
        self.assertEqual(timeout_events[0]["completed_gpu_service_count"], 1)
        self.assertEqual(
            timeout_events[0]["timeout_scope"],
            "last_completed_gpu_service_to_scheduler_abort",
        )

    def test_queue_timeout_excludes_requests_that_started_execution(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(request_queue_timeout_ms=2_000.0)
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        aborted = []
        runtime.scheduler = SimpleNamespace(
            abort_request=lambda request: aborted.append(request)
        )
        runtime.audit = _AuditRecorder()
        runtime._request_metadata_by_id = {
            "queued": metadata,
            "started": metadata,
        }
        runtime._request_submitted_ts_by_id = {
            "queued": 1_000.0,
            "started": 1_000.0,
        }
        runtime._request_physical_start_by_id = {
            "started": {"checkpoint_ts_ms": 1_500.0}
        }
        runtime._queue_timeout_request_ids = set()
        runtime._terminal_cancelled_request_ids = set()
        runtime._ordinary_native_fallback_signature_by_request = {
            "queued": ("ctx", 0, ("page:1:0",)),
            "started": ("ctx", 0, ("page:2:0",)),
        }
        runtime._ordinary_fallback_blocked_capacity = {
            "queued": (10, 2_000.0),
            "started": (10, 2_000.0),
        }
        runtime._current_ordinary_starvation_priority = ("queued",)

        runtime._enforce_queue_timeouts(now_ms=3_001.0)

        self.assertEqual([item.rid for item in aborted], ["queued"])
        self.assertIn("queued", runtime._terminal_cancelled_request_ids)
        self.assertNotIn("started", runtime._terminal_cancelled_request_ids)
        self.assertNotIn(
            "queued", runtime._ordinary_native_fallback_signature_by_request
        )
        self.assertNotIn(
            "queued", runtime._ordinary_fallback_blocked_capacity
        )
        self.assertEqual(runtime._current_ordinary_starvation_priority, ())
        self.assertIn(
            "started", runtime._ordinary_native_fallback_signature_by_request
        )

    def test_final_runtime_summary_exposes_joint_correctness_gates(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.scheduler = SimpleNamespace(
            waiting_queue=(),
            running_batch=None,
            chunked_req=None,
        )
        runtime.controller = BeliefKVController()
        runtime.audit = _AuditRecorder()
        runtime._shutdown_state = "acknowledged"
        runtime._online_joint_counts = Counter()
        runtime._joint_shadow_counts = Counter({"risk_only_result_published": 2})
        runtime._joint_predictive_counts = Counter({"funnel_no_beneficiary_hint": 3})
        runtime._running_retraction_counts = Counter()
        runtime._pending_online_joint_residency = None
        runtime._pending_running_retraction_transaction = None
        runtime._current_online_joint_view = None

        payload = runtime._runtime_summary_payload(now_ms=10.0, final=True)

        self.assertEqual(
            payload["joint_control"]["shadow_counts"],
            {"risk_only_result_published": 2},
        )
        self.assertEqual(
            payload["joint_control"]["predictive_counts"],
            {"funnel_no_beneficiary_hint": 3},
        )
        self.assertTrue(
            {
                "predictive_beneficiary_hint_published",
                "predictive_risk_enqueued",
                "predictive_risk_funnel",
                "predictive_risk_eligibility",
                "predictive_risk_shadow_failed",
                "predictive_prefetch_service_lease_registered",
                "predictive_prefetch_service_lease_released",
                "predictive_action_outcome",
            }.issubset(_PERFORMANCE_METRIC_EVENTS)
        )
        self.assertTrue(
            payload["correctness_gates"][
                "all_online_actions_have_source_joint_plan_id"
            ]
        )
        self.assertTrue(
            payload["correctness_gates"]["no_pending_transactions"]
        )
        self.assertTrue(
            payload["correctness_gates"]["shutdown_summary_complete"]
        )
        self.assertTrue(
            payload["correctness_gates"]
            ["all_non_user_cancelled_obligations_satisfied"]
        )
        self.assertTrue(
            payload["correctness_gates"]
            ["shutdown_cleanup_did_not_mask_unresolved_transactions"]
        )

        runtime._shutdown_prepare_transaction_snapshot = {
            "active_obligation_ids": ["restore-1"],
            "inflight_command_ids": ["command-inflight"],
            "queued_command_ids": ["command-queued"],
        }
        payload = runtime._runtime_summary_payload(now_ms=11.0, final=True)
        self.assertFalse(
            payload["correctness_gates"]
            ["shutdown_cleanup_did_not_mask_unresolved_transactions"]
        )
        runtime._shutdown_terminal_transaction_ids = {"restore-1"}
        runtime._shutdown_terminal_command_outcomes = {
            "command-inflight": "cancelled",
            "command-queued": "cancelled",
        }
        payload = runtime._runtime_summary_payload(now_ms=12.0, final=True)
        self.assertTrue(
            payload["correctness_gates"]
            ["shutdown_cleanup_did_not_mask_unresolved_transactions"]
        )

    def test_sparse_policy_snapshot_capture_is_replay_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
            runtime.config = BeliefKVConfig(
                hbm_capacity_bytes=1_000,
                host_capacity_bytes=1_000,
                reserve_hbm_bytes=0,
                kv_bytes_per_token=10,
                predictor_enabled=False,
                shadow_enabled=False,
                reference_policy_snapshot_min_interval_ms=1_000,
            )
            runtime.controller = BeliefKVController(runtime.config)
            runtime.scheduler = SimpleNamespace(waiting_queue=[])
            runtime.backend = SimpleNamespace(
                capabilities=SimpleNamespace(operation_merge=False)
            )
            runtime.audit = _AuditRecorder()
            path = Path(temporary) / "policy.jsonl.gz"
            runtime.policy_snapshot_log = PolicySnapshotLog(
                path,
                trace_id="trace-runtime",
                trace_sensitivity="timing_sensitive",
            )
            runtime._last_policy_snapshot_structural_signature = None
            runtime._last_policy_snapshot_physical_signature = None
            runtime._last_policy_snapshot_hbm_bucket = None
            runtime._last_policy_snapshot_ms = None
            first = RuntimeResourceObservation(
                ts_ms=10,
                hbm_capacity_bytes=1_000,
                hbm_used_bytes=0,
                host_capacity_bytes=1_000,
                host_used_bytes=0,
                host_free_bytes=1_000,
            )

            runtime._maybe_record_policy_snapshot(first)
            runtime._maybe_record_policy_snapshot(first)
            runtime.controller.process_runtime_event(
                RuntimeEvent(
                    "workflow-start",
                    11,
                    RuntimeEventKind.WORKFLOW_START,
                    "workflow",
                )
            )
            runtime._maybe_record_policy_snapshot(replace(first, ts_ms=11))
            runtime.policy_snapshot_log.close()

            snapshots = load_replay_trace(path)
            self.assertEqual(len(snapshots), 2)
            self.assertEqual(snapshots[1].policy_input.runtime_graph.graph_version, 1)
            recorded = [
                item for item in runtime.audit.events
                if item[0] == "policy_snapshot_recorded"
            ]
            self.assertEqual(len(recorded), 2)

    def test_positive_predictive_candidate_persists_exact_snapshot_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
            runtime.controller = BeliefKVController(
                BeliefKVConfig(
                    hbm_capacity_bytes=1_000,
                    host_capacity_bytes=1_000,
                    reserve_hbm_bytes=0,
                    predictor_enabled=False,
                )
            )
            runtime.audit = _AuditRecorder()
            runtime._joint_predictive_counts = Counter()
            runtime._predictive_candidate_snapshot_signatures = {}
            path = Path(temporary) / "predictive-candidate.jsonl.gz"
            runtime.policy_snapshot_log = PolicySnapshotLog(
                path,
                trace_id="predictive-candidate",
                trace_sensitivity="timing_sensitive",
            )
            policy_input = runtime.controller.build_policy_input(
                RuntimeResourceObservation(
                    ts_ms=10,
                    hbm_capacity_bytes=1_000,
                    hbm_used_bytes=0,
                    host_capacity_bytes=1_000,
                    host_used_bytes=0,
                    host_free_bytes=1_000,
                )
            )
            result = SimpleNamespace(
                sequence=7,
                policy_input=policy_input,
            )
            payload = {
                "candidate_summaries": [
                    {
                        "action": "prefetch_gpu",
                        "expected_benefit_ms": 12.0,
                        "eligible": False,
                        "reasons": ["future_hbm_chance_constraint"],
                        "action_certificate": {
                            "target_context_id": "ctx-parent",
                            "required_hbm_free_bytes": 310 << 20,
                            "required_host_free_bytes": 0,
                        },
                    }
                ]
            }
            observation = RuntimeResourceObservation(
                ts_ms=11,
                hbm_capacity_bytes=1_000,
                hbm_used_bytes=0,
                host_capacity_bytes=1_000,
                host_used_bytes=0,
                host_free_bytes=1_000,
            )

            runtime._maybe_persist_predictive_candidate_snapshot(
                result, payload, observation=observation
            )
            runtime._maybe_persist_predictive_candidate_snapshot(
                result, payload, observation=replace(observation, ts_ms=12)
            )
            runtime.policy_snapshot_log.close()

            snapshots = load_replay_trace(path)
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(
                snapshots[0].policy_input.snapshot_id,
                policy_input.snapshot_id,
            )
            self.assertEqual(
                runtime._joint_predictive_counts[
                    "candidate_snapshot_persisted"
                ],
                1,
            )
            self.assertEqual(
                runtime._joint_predictive_counts[
                    "candidate_snapshot_deduplicated"
                ],
                1,
            )

    def test_high_pressure_beneficiary_snapshots_are_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
            runtime.controller = BeliefKVController(
                BeliefKVConfig(
                    hbm_capacity_bytes=1_000,
                    host_capacity_bytes=1_000,
                    reserve_hbm_bytes=0,
                    predictor_enabled=False,
                )
            )
            runtime.audit = _AuditRecorder()
            runtime._joint_predictive_counts = Counter()
            runtime._predictive_candidate_snapshot_signatures = {}
            path = Path(temporary) / "predictive-high-pressure.jsonl.gz"
            runtime.policy_snapshot_log = PolicySnapshotLog(
                None,
                trace_id="periodic-disabled",
                trace_sensitivity="timing_sensitive",
            )
            runtime.predictive_candidate_snapshot_log = PolicySnapshotLog(
                path,
                trace_id="predictive-high-pressure",
                trace_sensitivity="timing_sensitive",
                max_pending=32,
            )
            policy_input = runtime.controller.build_policy_input(
                RuntimeResourceObservation(
                    ts_ms=10,
                    hbm_capacity_bytes=1_000,
                    hbm_used_bytes=900,
                    host_capacity_bytes=1_000,
                    host_used_bytes=0,
                    host_free_bytes=1_000,
                )
            )
            result = SimpleNamespace(sequence=7, policy_input=policy_input)
            observation = RuntimeResourceObservation(
                ts_ms=11,
                hbm_capacity_bytes=1_000,
                hbm_used_bytes=900,
                host_capacity_bytes=1_000,
                host_used_bytes=0,
                host_free_bytes=1_000,
            )

            for index in range(25):
                payload = {
                    "candidate_summaries": [
                        {
                            "action": "prepare_host",
                            "beneficiary_request_id": f"beneficiary-{index}",
                            "expected_benefit_ms": -1.0,
                            "eligible": False,
                            "reasons": ["insufficient_expected_benefit"],
                            "action_certificate": {
                                "target_context_id": f"victim-{index}",
                                "required_hbm_free_bytes": 0,
                                "required_host_free_bytes": 128,
                            },
                        }
                    ]
                }
                runtime._maybe_persist_predictive_candidate_snapshot(
                    result,
                    payload,
                    observation=replace(observation, ts_ms=11 + index),
                )
            runtime.predictive_candidate_snapshot_log.close()
            runtime.policy_snapshot_log.close()

            self.assertEqual(len(load_replay_trace(path)), 20)
            self.assertEqual(
                runtime._joint_predictive_counts[
                    "high_pressure_candidate_snapshot_persisted"
                ],
                20,
            )
            self.assertEqual(
                runtime._joint_predictive_counts[
                    "high_pressure_candidate_snapshot_limit_reached"
                ],
                5,
            )

    def test_joint_shadow_safe_point_validates_without_applying_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
            runtime.config = BeliefKVConfig(
                hbm_capacity_bytes=1_000,
                host_capacity_bytes=1_000,
                reserve_hbm_bytes=0,
                kv_bytes_per_token=10,
                predictor_enabled=False,
                shadow_enabled=False,
                reference_policy_snapshot_min_interval_ms=1_000,
            )
            runtime.controller = BeliefKVController(runtime.config)
            runtime.controller.process_runtime_events(
                (
                    RuntimeEvent(
                        "workflow-start",
                        1,
                        RuntimeEventKind.WORKFLOW_START,
                        "workflow",
                    ),
                    RuntimeEvent(
                        "root-create",
                        2,
                        RuntimeEventKind.INVOCATION_CREATE,
                        "workflow",
                        invocation_id="root",
                        context_id="ctx-root",
                        context_epoch=0,
                    ),
                )
            )
            runtime.controller.submit_request(
                AdmissionRequest(
                    request_id="request-root",
                    workflow_id="workflow",
                    invocation_id="root",
                    context_id="ctx-root",
                    context_epoch=0,
                    submitted_ts_ms=3,
                    uncached_prompt_tokens=2,
                    expected_output_tokens=2,
                    kv_bytes_per_token=10,
                )
            )
            runtime.scheduler = SimpleNamespace(waiting_queue=[])
            runtime.backend = SimpleNamespace(
                capabilities=SimpleNamespace(operation_merge=False)
            )
            runtime.audit = _AuditRecorder()
            runtime.policy_snapshot_log = PolicySnapshotLog(
                Path(temporary) / "joint-policy.jsonl.gz",
                trace_id="trace-joint-runtime",
                trace_sensitivity="timing_sensitive",
            )
            runtime._last_policy_snapshot_structural_signature = None
            runtime._last_policy_snapshot_physical_signature = None
            runtime._last_policy_snapshot_hbm_bucket = None
            runtime._last_policy_snapshot_ms = None
            runtime._last_joint_shadow_result_sequence = 0
            runtime._joint_shadow_counts = Counter()
            runtime._joint_shadow_strict_stale_reasons = Counter()
            runtime._joint_shadow_readset_stale_reasons = Counter()
            runtime._joint_shadow_timing_samples = {
                name: deque(maxlen=128)
                for name in (
                    "snapshot_build_ms",
                    "snapshot_trace_enqueue_ms",
                    "snapshot_enqueue_ms",
                    "plan_queue_wait_ms",
                    "plan_compute_ms",
                    "plan_publish_to_safe_point_ms",
                    "validation_ms",
                    "plan_age_ms",
                )
            }
            runtime.joint_shadow_worker = LatestWinsJointPlanWorker(
                ObservedJointPlanner(
                    JointPlannerConfig(max_planning_budget_ms=100)
                )
            )
            observation = RuntimeResourceObservation(
                ts_ms=10,
                hbm_capacity_bytes=1_000,
                hbm_used_bytes=0,
                host_capacity_bytes=1_000,
                host_used_bytes=0,
                host_free_bytes=1_000,
            )
            pending_before = runtime.controller.admission.pending_requests()
            history_before = tuple(runtime.controller.command_history)

            runtime._maybe_record_policy_snapshot(observation)
            for _ in range(100):
                if runtime.joint_shadow_worker.latest() is not None:
                    break
                threading.Event().wait(0.01)
            runtime.controller.fairness.charge_service("workflow", 1.0)
            runtime._maybe_record_policy_snapshot(observation)

            would_apply = [
                item
                for item in runtime.audit.events
                if item[0] == "joint_plan_would_apply"
            ]
            self.assertEqual(len(would_apply), 1)
            self.assertTrue(
                would_apply[0][2]["validation_skipped_seed_only"]
            )
            self.assertEqual(would_apply[0][2]["validation_ms"], 0.0)
            self.assertEqual(
                runtime.controller.admission.pending_requests(), pending_before
            )
            self.assertEqual(tuple(runtime.controller.command_history), history_before)
            self.assertEqual(runtime.controller.page_index.gpu_bytes, 0)
            self.assertEqual(runtime.policy_snapshot_log.count, 1)
            self.assertEqual(
                runtime.joint_shadow_worker.stats().submitted_count, 1
            )

            self.assertTrue(runtime.joint_shadow_worker.close())
            runtime.joint_shadow_worker = None
            runtime.policy_snapshot_log.close()

    def test_incremental_joint_shadow_builds_policy_input_off_safe_point(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
            runtime.config = BeliefKVConfig(
                hbm_capacity_bytes=1_000,
                host_capacity_bytes=1_000,
                reserve_hbm_bytes=0,
                kv_bytes_per_token=10,
                predictor_enabled=False,
                shadow_enabled=False,
                reference_policy_snapshot_min_interval_ms=1_000,
            )
            runtime.controller = BeliefKVController(runtime.config)
            runtime.controller.process_runtime_events(
                (
                    RuntimeEvent(
                        "workflow-start",
                        1,
                        RuntimeEventKind.WORKFLOW_START,
                        "workflow",
                    ),
                    RuntimeEvent(
                        "root-create",
                        2,
                        RuntimeEventKind.INVOCATION_CREATE,
                        "workflow",
                        invocation_id="root",
                        context_id="ctx-root",
                        context_epoch=0,
                    ),
                )
            )
            runtime.scheduler = SimpleNamespace(waiting_queue=[])
            runtime.backend = SimpleNamespace(
                capabilities=SimpleNamespace(operation_merge=False)
            )
            runtime.audit = _AuditRecorder()
            runtime.policy_snapshot_log = PolicySnapshotLog(
                Path(temporary) / "incremental-joint-policy.jsonl.gz",
                trace_id="trace-incremental-joint-runtime",
                trace_sensitivity="timing_sensitive",
            )
            runtime.predictive_candidate_snapshot_log = PolicySnapshotLog(
                Path(temporary) / "predictive-only.jsonl.gz",
                trace_id="trace-predictive-only",
                trace_sensitivity="timing_sensitive",
            )
            runtime._last_policy_snapshot_structural_signature = None
            runtime._last_policy_snapshot_physical_signature = None
            runtime._last_policy_snapshot_hbm_bucket = None
            runtime._last_policy_snapshot_ms = None
            runtime._shadow_event_sequence = 0
            runtime._shadow_page_revision = 0
            runtime._shadow_telemetry_sequence = 0
            runtime._last_joint_shadow_result_sequence = 0
            runtime._joint_shadow_counts = Counter()
            runtime._joint_shadow_strict_stale_reasons = Counter()
            runtime._joint_shadow_readset_stale_reasons = Counter()
            runtime._joint_shadow_timing_samples = {
                name: deque(maxlen=128)
                for name in (
                    "snapshot_build_ms",
                    "safe_point_delta_capture_ms",
                    "snapshot_trace_enqueue_ms",
                    "snapshot_enqueue_ms",
                    "plan_queue_wait_ms",
                    "plan_compute_ms",
                    "plan_publish_to_safe_point_ms",
                    "validation_ms",
                    "plan_age_ms",
                )
            }
            runtime.joint_shadow_worker = LatestWinsJointPlanWorker(
                ObservedJointPlanner(
                    JointPlannerConfig(max_planning_budget_ms=100)
                ),
                assembler=IncrementalPolicyInputAssembler(runtime.config),
            )
            observation = RuntimeResourceObservation(
                ts_ms=10,
                hbm_capacity_bytes=1_000,
                hbm_used_bytes=0,
                host_capacity_bytes=1_000,
                host_used_bytes=0,
                host_free_bytes=1_000,
            )

            def forbidden_build(*_args, **_kwargs):
                raise AssertionError("live controller builder reached safe point")

            runtime.controller.build_policy_input = forbidden_build
            runtime._maybe_record_policy_snapshot(observation)
            for _ in range(100):
                if runtime.joint_shadow_worker.latest() is not None:
                    break
                threading.Event().wait(0.01)
            runtime.controller.fairness.charge_service("workflow", 1.0)
            runtime._maybe_record_policy_snapshot(observation)

            self.assertEqual(runtime.policy_snapshot_log.count, 1)
            self.assertEqual(
                runtime.predictive_candidate_snapshot_log.count,
                0,
            )
            self.assertEqual(
                runtime.joint_shadow_worker.stats().submitted_count, 1
            )
            delta_events = [
                item for item in runtime.audit.events
                if item[0] == "joint_plan_shadow_delta_enqueued"
            ]
            self.assertEqual(len(delta_events), 1)
            self.assertEqual(delta_events[0][2]["event_count"], 2)
            recorded = [
                item for item in runtime.audit.events
                if item[0] == "policy_snapshot_recorded"
            ]
            self.assertEqual(recorded[0][2]["safe_point_build_ms"], 0.0)

            self.assertTrue(runtime.joint_shadow_worker.close())
            runtime.joint_shadow_worker = None
            runtime.policy_snapshot_log.close()
            runtime.predictive_candidate_snapshot_log.close()

    def test_joint_shadow_mirror_failure_prepares_full_resync(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            host_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            predictor_enabled=False,
            shadow_enabled=False,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.audit = _AuditRecorder()
        runtime._joint_shadow_counts = Counter()
        runtime._joint_shadow_timing_samples = {
            name: deque(maxlen=8)
            for name in (
                "plan_queue_wait_ms",
                "plan_compute_ms",
                "plan_publish_to_safe_point_ms",
            )
        }
        runtime._shadow_event_sequence = 9
        runtime._shadow_page_revision = 11
        runtime._shadow_topology_revision = 7
        runtime._shadow_telemetry_sequence = 3
        runtime._frontier_feature_delta_initialized = True
        runtime._frontier_active_invocation_ids = {"root"}
        runtime._last_frontier_features = {"root": {"state": "ready"}}
        runtime._last_frontier_predictions = {"root": {"support": "exact"}}
        runtime._latest_observed_policy_input = object()
        runtime._latest_action_local_overlay_batch = object()
        runtime._latest_predictive_intent = object()
        runtime._observed_seed_hint_publication_initialized = True
        runtime._last_published_observed_seed_hint_signature = ("root",)
        runtime._observed_seed_hint_risk_initialized = True
        runtime._last_observed_seed_hint_risk_signature = ("root",)
        runtime._online_joint_result = object()
        runtime._online_joint_source = object()
        runtime._online_joint_validation = object()
        runtime._current_online_joint_view = object()
        runtime._current_online_joint_decision = object()
        runtime._last_policy_state_stamp = object()
        runtime._last_joint_shadow_result_sequence = 0
        worker = mock.Mock()
        worker.reset_incremental_mirror.return_value = True
        result = JointShadowResult(
            sequence=4,
            snapshot_id="failed-shadow-work-00000004",
            submitted_monotonic_ms=1.0,
            started_monotonic_ms=2.0,
            completed_monotonic_ms=3.0,
            plan=None,
            error="RuntimeError: expected mirror failure",
        )
        observation = RuntimeResourceObservation(
            ts_ms=10.0,
            hbm_capacity_bytes=1_000,
            hbm_used_bytes=0,
            host_capacity_bytes=1_000,
            host_used_bytes=0,
            host_free_bytes=1_000,
        )

        self.assertTrue(
            runtime._recover_joint_shadow_mirror(observation, worker, result)
        )

        worker.reset_incremental_mirror.assert_called_once_with()
        self.assertEqual(runtime._last_joint_shadow_result_sequence, 4)
        self.assertEqual(runtime._shadow_event_sequence, 0)
        self.assertEqual(runtime._shadow_page_revision, 0)
        self.assertFalse(runtime._frontier_feature_delta_initialized)
        self.assertEqual(runtime._frontier_active_invocation_ids, set())
        self.assertIsNone(runtime._latest_predictive_intent)
        self.assertTrue(runtime._joint_shadow_mirror_resync_pending)
        self.assertEqual(runtime._joint_shadow_counts["mirror_resync_prepared"], 1)
        self.assertTrue(
            any(
                event == "joint_plan_shadow_mirror_resync_prepared"
                for event, _, _ in runtime.audit.events
            )
        )

    def test_request_restore_dependency_uses_only_matched_radix_path(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = BeliefKVController()
        runtime.registry = SGLangNodeRegistry()
        runtime.tree_cache = _TreeCache()
        runtime.controller.page_index.register_context("ctx", "wf", 0)

        matched = _Node(1)
        matched.parent = runtime.tree_cache.root_node
        unrelated = _Node(2)
        unrelated.parent = runtime.tree_cache.root_node
        matched_handle = runtime.registry.register(matched)
        unrelated_handle = runtime.registry.register(unrelated)
        for handle in (matched_handle, unrelated_handle):
            runtime.controller.page_index.register_page(
                handle,
                size_bytes=100,
                residency=PhysicalResidency.CPU_ONLY,
            )
        runtime.controller.page_index.bind_pages(
            "ctx", 0, (matched_handle, unrelated_handle)
        )
        request = SimpleNamespace(last_node=matched)

        dependencies = runtime._request_restore_bundle_ids(request, "ctx")

        self.assertEqual(
            dependencies,
            (
                f"page:{matched_handle.page_id}:"
                f"{matched_handle.allocation_generation}",
            ),
        )

    def test_policy_runtime_runnable_does_not_test_tensor_truth_value(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(kv_bytes_per_token=10)
        runtime.controller = BeliefKVController(runtime.config)
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[
                SimpleNamespace(
                    rid="request",
                    beliefkv_metadata=metadata,
                    sampling_params=SimpleNamespace(max_new_tokens=20),
                    output_ids=_NoBooleanSequence(3),
                    origin_input_ids=_NoBooleanSequence(10),
                    prefix_indices=_NoBooleanSequence(5),
                )
            ],
            running_batch=SimpleNamespace(
                reqs=[
                    SimpleNamespace(
                        rid="running-request",
                        beliefkv_metadata=BeliefKVRequestMetadata(
                            "wf", "running-inv", "running-ctx", 0
                        ),
                        sampling_params=SimpleNamespace(max_new_tokens=20),
                        output_ids=_NoBooleanSequence(4),
                        origin_input_ids=_NoBooleanSequence(8),
                        prefix_indices=_NoBooleanSequence(8),
                    )
                ]
            ),
        )

        runnable = runtime._policy_runtime_runnable(100.0)

        self.assertEqual(len(runnable), 2)
        by_request = {item.request_id: item for item in runnable}
        self.assertEqual(by_request["request"].startup_bytes, 220)
        self.assertTrue(
            by_request["request"].causal_class.startswith("engine_waiting:")
        )
        self.assertEqual(by_request["running-request"].startup_bytes, 160)
        self.assertTrue(
            by_request["running-request"].causal_class.startswith(
                "engine_running:"
            )
        )

    def test_tree_sync_defers_generation_changes_until_transfer_ack(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._tree_dirty = True
        runtime.controller = SimpleNamespace(inflight_command_ids=("transfer-1",))

        runtime.sync_tree()

        self.assertTrue(runtime._tree_dirty)

    def test_tree_sync_applies_local_residency_insert_and_remove_deltas(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(kv_bytes_per_token=10)
        runtime.controller = BeliefKVController(runtime.config)
        runtime.registry = SGLangNodeRegistry()
        runtime.tree_cache = _TreeCache()
        runtime._terminal_node_by_context = {}
        runtime._tree_dirty = True
        runtime._tree_full_rebuild_required = True
        runtime._dirty_radix_nodes = {}
        runtime._removed_radix_nodes = {}
        runtime._dirty_context_ids = set()
        runtime._tree_sync_timing_samples = deque(maxlen=100)
        node = _Node(1)
        node.key = [1, 2]
        node.last_access_time = 0.0
        node.parent = runtime.tree_cache.root_node
        runtime.tree_cache.root_node.children[1] = node

        runtime.sync_tree(force=True)
        handle = runtime.registry.current_handle(1)
        self.assertEqual(
            runtime.controller.page_index.pages[handle].residency.value,
            "gpu_only",
        )

        node.host_value = [10, 11]
        runtime.on_radix_mutation((node,), False, False)
        runtime.sync_tree()
        self.assertEqual(
            runtime.controller.page_index.pages[handle].residency.value,
            "dual_clean",
        )
        self.assertEqual(runtime._tree_sync_timing_samples[-1][0], "incremental")

        child = _Node(2)
        child.key = [3]
        child.last_access_time = 0.0
        child.parent = node
        node.children[3] = child
        runtime.on_radix_mutation((child,), True, False)
        runtime.sync_tree()
        child_handle = runtime.registry.current_handle(2)
        self.assertEqual(
            runtime.controller.page_index.pages[child_handle].parent,
            handle,
        )

        del node.children[3]
        runtime.on_radix_mutation((child,), True, True)
        runtime.sync_tree()
        self.assertEqual(
            runtime.controller.page_index.pages[child_handle].residency.value,
            "dead",
        )

    def test_resource_snapshot_uses_real_allocator_state_and_marks_missing_utilization(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=1600,
            host_capacity_bytes=3200,
            reserve_hbm_bytes=100,
            kv_bytes_per_token=16,
        )
        runtime._now_ms = lambda: 50.0
        runtime._last_resource_telemetry_ms = None
        runtime.audit = _AuditRecorder()
        runtime.scheduler = SimpleNamespace(
            token_to_kv_pool_allocator=_Allocator(25),
            max_total_num_tokens=100,
        )
        runtime.tree_cache = SimpleNamespace(
            token_to_kv_pool_host=_HostAllocator(200, 150, 16)
        )
        runtime.controller = SimpleNamespace(
            page_index=SimpleNamespace(gpu_bytes=700, cpu_bytes=600),
            inflight_command_ids=(),
            signals=SimpleNamespace(
                pcie_utilization=0.0,
                gpu_compute_utilization=0.0,
            ),
            _engine_request_count=2,
            _running_request_count=1,
        )

        runtime._emit_resource_snapshot(force=True)

        event, ts_ms, fields = runtime.audit.events[0]
        self.assertEqual((event, ts_ms), ("resource_snapshot", 50.0))
        self.assertEqual(fields["hbm_capacity_bytes"], 1600)
        self.assertEqual(fields["configured_hbm_capacity_bytes"], 1600)
        self.assertEqual(fields["hbm_used_bytes"], 1200)
        self.assertEqual(fields["host_used_bytes"], 800)
        self.assertEqual(fields["untracked_allocator_delta_bytes"], 500)
        self.assertIsNone(fields["engine_locked_gpu_bytes"])
        self.assertEqual(fields["kv_state_breakdown_scope"], "unavailable")
        self.assertIsNone(fields["pcie_utilization"])
        self.assertIsNone(fields["copy_engine_utilization"])
        self.assertIsNone(fields["gpu_compute_utilization"])

    def test_resource_snapshot_exposes_configured_allocator_capacity_mismatch(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2000,
            host_capacity_bytes=3200,
            reserve_hbm_bytes=100,
            kv_bytes_per_token=16,
        )
        runtime._now_ms = lambda: 50.0
        runtime._last_resource_telemetry_ms = None
        runtime.audit = _AuditRecorder()
        runtime.scheduler = SimpleNamespace(
            token_to_kv_pool_allocator=_Allocator(25),
            max_total_num_tokens=100,
        )
        runtime.tree_cache = SimpleNamespace(
            token_to_kv_pool_host=_HostAllocator(200, 150, 16)
        )
        runtime.controller = SimpleNamespace(
            page_index=SimpleNamespace(gpu_bytes=700, cpu_bytes=600),
            inflight_command_ids=(),
            signals=SimpleNamespace(
                pcie_utilization=0.0,
                gpu_compute_utilization=0.0,
            ),
            _engine_request_count=2,
            _running_request_count=1,
        )

        runtime._emit_resource_snapshot(force=True)

        fields = runtime.audit.events[0][2]
        self.assertEqual(fields["hbm_capacity_bytes"], 1600)
        self.assertEqual(fields["configured_hbm_capacity_bytes"], 2000)
        self.assertEqual(fields["hbm_used_bytes"], 1200)

    def test_resource_snapshot_exposes_closure_aware_physical_kv_states(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=1600,
            host_capacity_bytes=3200,
            reserve_hbm_bytes=100,
            kv_bytes_per_token=16,
        )
        runtime._now_ms = lambda: 50.0
        runtime._last_resource_telemetry_ms = None
        runtime.audit = _AuditRecorder()
        runtime.scheduler = SimpleNamespace(
            token_to_kv_pool_allocator=_Allocator(25),
            max_total_num_tokens=100,
        )
        runtime.tree_cache = SimpleNamespace(
            token_to_kv_pool_host=_HostAllocator(200, 150, 16)
        )
        page_index = PageOwnershipIndex()
        parent = PageHandle(1, 0)
        child = PageHandle(2, 0)
        page_index.register_page(parent, size_bytes=100, radix_depth=1)
        page_index.register_page(
            child, size_bytes=200, radix_depth=2, parent=parent
        )
        page_index.set_engine_lock(child, 1)
        runtime.controller = SimpleNamespace(
            page_index=page_index,
            inflight_command_ids=(),
            signals=SimpleNamespace(
                pcie_utilization=0.0,
                gpu_compute_utilization=0.0,
            ),
            _engine_request_count=1,
            _running_request_count=1,
        )

        runtime._emit_resource_snapshot(force=True)

        fields = runtime.audit.events[0][2]
        self.assertEqual(fields["page_index_gpu_bytes"], 300)
        self.assertEqual(fields["untracked_allocator_delta_bytes"], 900)
        self.assertEqual(fields["engine_locked_gpu_bytes"], 200)
        self.assertEqual(fields["closure_blocked_gpu_bytes"], 100)
        self.assertEqual(fields["migratable_gpu_bytes"], 0)
        self.assertEqual(fields["dual_resident_gpu_bytes"], 0)
        self.assertEqual(
            fields["kv_state_breakdown_scope"], "physical_radix_closure"
        )

    def test_resource_snapshot_attributes_stale_locks_to_running_request_path(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=1600,
            host_capacity_bytes=3200,
            reserve_hbm_bytes=100,
            kv_bytes_per_token=16,
            queue_service_observer_enabled=False,
        )
        runtime.audit = _AuditRecorder()
        runtime._last_resource_telemetry_ms = None
        tree = _TreeCache()
        tree.token_to_kv_pool_host = _HostAllocator(200, 150, 16)
        parent_node = _Node(1)
        child_node = _Node(2)
        parent_node.parent = tree.root_node
        child_node.parent = parent_node
        tree.root_node.children = {1: parent_node}
        parent_node.children = {2: child_node}
        parent_node.lock_ref = 1
        child_node.lock_ref = 1
        runtime.tree_cache = tree
        runtime.registry = SGLangNodeRegistry()
        parent = runtime.registry.register(parent_node)
        child = runtime.registry.register(child_node)
        page_index = PageOwnershipIndex()
        page_index.register_page(parent, size_bytes=100, radix_depth=1)
        page_index.register_page(
            child, size_bytes=200, radix_depth=2, parent=parent
        )
        page_index.set_engine_lock(parent, 1)
        page_index.set_engine_lock(child, 1)
        runtime.controller = BeliefKVController(runtime.config)
        runtime.controller.page_index = page_index
        runtime._active_request_ids = {"request"}
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(
            rid="request",
            beliefkv_metadata=metadata,
            last_node=child_node,
        )
        batch = SimpleNamespace(
            reqs=[request],
            forward_mode=_ForwardMode("DECODE"),
        )
        runtime.scheduler = SimpleNamespace(
            token_to_kv_pool_allocator=_Allocator(25),
            max_total_num_tokens=100,
            running_batch=SimpleNamespace(reqs=[request]),
            chunked_req=None,
        )
        runtime._observe_request_selected_for_lock_service(
            request,
            metadata,
            now_ms=0.0,
        )

        runtime._now_ms = lambda: 600.0
        runtime._emit_resource_snapshot(force=True)
        stale_fields = runtime.audit.events[-1][2]
        self.assertEqual(stale_fields["engine_lock_ref_gpu_bytes"], 300)
        self.assertEqual(stale_fields["engine_lock_fully_attributed_gpu_bytes"], 300)
        self.assertEqual(
            stale_fields["locked_but_not_served_gpu_bytes_100ms"], 300
        )
        self.assertEqual(
            stale_fields["locked_but_not_served_gpu_bytes_500ms"], 300
        )
        self.assertEqual(stale_fields["engine_lock_request_path_error_count"], 0)

        runtime._now_ms = lambda: 650.0
        runtime.on_batch_completed(batch)
        runtime._now_ms = lambda: 700.0
        runtime._emit_resource_snapshot(force=True)
        served_fields = runtime.audit.events[-1][2]
        self.assertEqual(served_fields["lock_recently_served_gpu_bytes_100ms"], 300)
        self.assertEqual(
            served_fields["locked_but_not_served_gpu_bytes_100ms"], 0
        )
        self.assertEqual(getattr(runtime, "_gpu_service_sample_count", 0), 0)

        runtime._active_request_ids.clear()
        runtime._now_ms = lambda: 750.0
        runtime.on_batch_completed(batch)
        self.assertFalse(runtime._lock_service_ledger.tracks("request"))

    def test_visible_waiting_request_does_not_block_h2d(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(rid="request", beliefkv_metadata=metadata)
        runtime._request_metadata_by_id = {"request": metadata}
        runtime._active_request_ids = set()
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[request],
            running_batch=SimpleNamespace(reqs=[]),
            chunked_req=None,
        )

        self.assertFalse(runtime._context_has_engine_request("ctx"))
        runtime._active_request_ids.add("request")
        self.assertFalse(runtime._context_has_engine_request("ctx"))
        request.req_pool_idx = 7
        # Native ownership is immutable inside an epoch; the next safe point sees it.
        runtime._finish_physical_safe_point()
        self.assertTrue(runtime._context_has_engine_request("ctx"))

    def test_native_and_explicit_load_ownership_are_both_visible(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(
            rid="request",
            beliefkv_metadata=metadata,
            load_operation_id="native-load-1",
        )
        runtime._request_metadata_by_id = {"request": metadata}
        runtime._request_submitted_ts_by_id = {"request": 1.0}
        runtime._h2d_context_by_command = {
            "explicit-h2d-1": ("ctx", ("page:1:0",))
        }
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[request],
            running_batch=SimpleNamespace(reqs=[]),
            chunked_req=None,
        )

        snapshot = runtime._context_physical_snapshots("ctx")[0]

        self.assertEqual(snapshot.native_load_operation_id, "native-load-1")
        self.assertEqual(snapshot.explicit_transfer_ids, ("explicit-h2d-1",))
        self.assertTrue(runtime._context_has_engine_request("ctx"))

    def test_native_snapshot_is_built_lazily_once_per_capture_epoch(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        first_metadata = BeliefKVRequestMetadata("wf", "inv-1", "ctx-1", 0)
        second_metadata = BeliefKVRequestMetadata("wf", "inv-2", "ctx-2", 0)
        first = SimpleNamespace(rid="request-1", req_pool_idx=1)
        second = SimpleNamespace(rid="request-2")
        runtime._request_metadata_by_id = {
            "request-1": first_metadata,
            "request-2": second_metadata,
        }
        runtime._request_submitted_ts_by_id = {
            "request-1": 1.0,
            "request-2": 2.0,
        }
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[first, second],
            running_batch=SimpleNamespace(reqs=[]),
            chunked_req=None,
        )
        runtime._begin_physical_safe_point_apply_events()
        runtime._begin_physical_safe_point_capture_and_plan()

        self.assertEqual(len(runtime._context_physical_snapshots("ctx-1")), 1)
        self.assertEqual(len(runtime._context_physical_snapshots("ctx-2")), 1)
        self.assertEqual(runtime._native_physical_snapshot_counts["call_count"], 1)
        self.assertEqual(
            runtime._native_physical_snapshot_counts["cache_hit_count"], 1
        )

    def test_native_snapshot_commit_rejects_changed_request_ownership(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(rid="request")
        runtime._request_metadata_by_id = {"request": metadata}
        runtime._request_submitted_ts_by_id = {"request": 1.0}
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[request],
            running_batch=SimpleNamespace(reqs=[]),
            chunked_req=None,
        )
        runtime._begin_physical_safe_point_apply_events()
        runtime._begin_physical_safe_point_capture_and_plan()
        runtime._context_physical_snapshots("ctx")
        captured_epoch = runtime._safe_point_physical_epoch_sequence

        request.req_pool_idx = 9
        self.assertFalse(runtime._begin_physical_transactional_commit("ctx"))
        self.assertGreater(runtime._safe_point_physical_epoch_sequence, captured_epoch)
        self.assertEqual(
            runtime._safe_point_physical_phase,
            SafePointPhysicalPhase.CAPTURE_AND_PLAN,
        )
        self.assertEqual(
            runtime._native_physical_snapshot_counts["commit_readset_stale"], 1
        )
        self.assertEqual(
            runtime._context_physical_snapshots("ctx")[0].req_pool_slot, 9
        )

    def test_native_snapshot_commit_rejects_request_id_reuse(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        request = SimpleNamespace(rid="request")
        runtime._request_metadata_by_id = {
            "request": BeliefKVRequestMetadata("wf", "old", "old-ctx", 0)
        }
        runtime._request_submitted_ts_by_id = {"request": 1.0}
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[request],
            running_batch=SimpleNamespace(reqs=[]),
            chunked_req=None,
        )
        runtime._context_physical_snapshots("old-ctx")

        runtime._request_metadata_by_id["request"] = BeliefKVRequestMetadata(
            "wf", "new", "new-ctx", 1
        )
        runtime._request_submitted_ts_by_id["request"] = 2.0

        self.assertFalse(
            runtime._begin_physical_transactional_commit("old-ctx")
        )

    def test_native_snapshot_commit_rejects_explicit_operation_change(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        runtime._request_metadata_by_id = {"request": metadata}
        runtime._request_submitted_ts_by_id = {"request": 1.0}
        runtime._h2d_context_by_command = {}
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[SimpleNamespace(rid="request")],
            running_batch=SimpleNamespace(reqs=[]),
            chunked_req=None,
        )
        runtime._context_physical_snapshots("ctx")
        runtime._h2d_context_by_command["h2d"] = ("ctx", ("page:1:0",))

        self.assertFalse(runtime._begin_physical_transactional_commit("ctx"))

    def test_native_snapshot_high_cardinality_index_is_complete(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        requests = [SimpleNamespace(rid=f"request-{index:03d}") for index in range(128)]
        runtime._request_metadata_by_id = {
            request.rid: BeliefKVRequestMetadata(
                f"wf-{index:03d}",
                f"inv-{index:03d}",
                f"ctx-{index:03d}",
                0,
            )
            for index, request in enumerate(requests)
        }
        runtime._request_submitted_ts_by_id = {
            request.rid: float(index) for index, request in enumerate(requests)
        }
        runtime.scheduler = SimpleNamespace(
            waiting_queue=requests[:64],
            running_batch=SimpleNamespace(reqs=requests[64:]),
            chunked_req=None,
        )

        for index in range(128):
            snapshots = runtime._context_physical_snapshots(f"ctx-{index:03d}")
            self.assertEqual(len(snapshots), 1)
        self.assertEqual(runtime._native_physical_snapshot_counts["call_count"], 1)
        self.assertEqual(
            runtime._native_physical_snapshot_counts["cache_hit_count"], 127
        )

    def test_planning_snapshot_is_forbidden_after_commit_starts(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._request_metadata_by_id = {}
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[],
            running_batch=SimpleNamespace(reqs=[]),
            chunked_req=None,
        )
        runtime._begin_physical_safe_point_apply_events()
        runtime._begin_physical_safe_point_capture_and_plan()
        self.assertTrue(runtime._begin_physical_transactional_commit())

        with self.assertRaisesRegex(RuntimeError, "after transactional commit"):
            runtime._context_physical_snapshots("ctx")

    def test_guard_blocked_restore_does_not_allocate_a_lease(self):
        config = BeliefKVConfig(kv_bytes_per_token=10)
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = BeliefKVController(config)
        runtime.audit = _AuditRecorder()
        runtime._restore_obligation_counts = Counter()
        runtime._restore_command_sequence = 0
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(rid="request", last_node=None)
        runtime._request_metadata_by_id = {"request": metadata}
        runtime._request_submitted_ts_by_id = {"request": 1.0}
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[request],
            running_batch=SimpleNamespace(reqs=[]),
            chunked_req=None,
        )
        obligation = runtime._restore_obligation_index().create(
            request_id="request",
            workflow_id="wf",
            invocation_id="inv",
            context_id="ctx",
            context_epoch=0,
            source_retraction_transaction_id="retraction-1",
            source_joint_plan_id="joint-1",
            created_ts_ms=1.0,
            path_extent_ids=("page:1:0",),
        )
        obligation.source_transaction_terminal = True
        obligation.requeued = True
        bundle = SimpleNamespace(
            generation_fingerprint="closure-1",
            scope=SimpleNamespace(value="exclusive_suffix"),
            bundle_id="bundle-1",
            closure_bytes=100,
            marginal_reclaimable_bytes=0,
            exclusive_action_bytes=100,
            cross_context_action_bytes=0,
            foreign_owner_context_ids=(),
        )
        preview = SimpleNamespace(
            eligible=True,
            copy_bytes=100,
            bundle=bundle,
            context_id="ctx",
            context_epoch=0,
            command_kind=CommandKind.PREFETCH_CONTEXT,
            blockers=(),
            intent=lambda: None,
        )
        runtime._refresh_restore_obligation = lambda *_args, **_kwargs: (
            request,
            ("page:1:0",),
        )
        runtime._restore_attempt_stamp = lambda: (1,)
        runtime._allocator_available_bytes = lambda: 1000
        runtime._restore_h2d_previews = lambda *_args, **_kwargs: (preview,)
        runtime.controller.preflight_control_command = lambda command: EnqueueOutcome(
            status=EnqueueStatus.RETRY_GUARD_BLOCKED,
            canonical_command_id=None,
            attempt_key=("ctx", 0, command.kind.value),
            blocker_codes=("engine_busy",),
            wake_conditions=("engine_owner_changed",),
        )
        runtime.controller.transfer_guard.generation_for = lambda _command: 1
        runtime._grant_restore_lease = mock.Mock()

        runtime._drive_restore_obligations(now_ms=2.0)

        runtime._grant_restore_lease.assert_not_called()
        self.assertEqual(obligation.blocker_codes, ("engine_busy",))

    def test_restore_enqueue_failure_rolls_back_prepared_lease_and_pin(self):
        config = BeliefKVConfig(kv_bytes_per_token=10)
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = BeliefKVController(config)
        runtime.audit = _AuditRecorder()
        runtime._restore_obligation_counts = Counter()
        runtime._restore_command_sequence = 0
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(rid="request", last_node=None)
        runtime._request_metadata_by_id = {"request": metadata}
        runtime._request_submitted_ts_by_id = {"request": 1.0}
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[request],
            running_batch=SimpleNamespace(reqs=[]),
            chunked_req=None,
        )
        obligation = runtime._restore_obligation_index().create(
            request_id="request",
            workflow_id="wf",
            invocation_id="inv",
            context_id="ctx",
            context_epoch=0,
            source_retraction_transaction_id="retraction-1",
            source_joint_plan_id="joint-1",
            created_ts_ms=1.0,
            path_extent_ids=("page:1:0",),
        )
        obligation.source_transaction_terminal = True
        obligation.requeued = True
        bundle = SimpleNamespace(
            generation_fingerprint="closure-1",
            scope=SimpleNamespace(value="exclusive_suffix"),
            bundle_id="bundle-1",
            closure_bytes=100,
            marginal_reclaimable_bytes=0,
            exclusive_action_bytes=100,
            cross_context_action_bytes=0,
            foreign_owner_context_ids=(),
        )
        preview = SimpleNamespace(
            eligible=True,
            copy_bytes=100,
            bundle=bundle,
            context_id="ctx",
            context_epoch=0,
            command_kind=CommandKind.PREFETCH_CONTEXT,
            blockers=(),
            intent=lambda: None,
        )
        runtime._refresh_restore_obligation = lambda *_args, **_kwargs: (
            request,
            ("page:1:0",),
        )
        runtime._restore_attempt_stamp = lambda: (1,)
        runtime._allocator_available_bytes = lambda: 1000
        runtime._restore_h2d_previews = lambda *_args, **_kwargs: (preview,)
        runtime.controller.preflight_control_command = lambda command: EnqueueOutcome(
            status=EnqueueStatus.ENQUEUED,
            canonical_command_id=command.command_id,
            attempt_key=("ctx", 0, command.kind.value),
        )
        runtime.controller.transfer_guard.generation_for = lambda _command: 0
        events = []
        lease = SimpleNamespace(lease_id="lease-1")
        runtime._grant_restore_lease = lambda *_args, **_kwargs: (
            events.append("reserve") or lease
        )
        runtime._pin_restore_lease_prefix = lambda *_args, **_kwargs: (
            events.append("pin") or True
        )
        runtime._queue_restore_obligation_command = lambda *_args, **_kwargs: (
            events.append("enqueue")
            or EnqueueOutcome(
                status=EnqueueStatus.CONTEXT_CONFLICT,
                canonical_command_id="other-command",
                attempt_key=("ctx", 0, "prefetch_context"),
                blocker_codes=("context_command_owned",),
                wake_conditions=("command_terminal:other-command",),
            )
        )
        runtime._release_restore_lease = lambda *_args, **_kwargs: events.append(
            "rollback"
        )

        runtime._drive_restore_obligations(now_ms=2.0)

        self.assertEqual(events, ["reserve", "pin", "enqueue", "rollback"])
        transaction = runtime._restore_transactions["request"]
        self.assertIsNone(transaction.capacity_reservation_id)
        self.assertIsNone(transaction.prefix_pin_token)
        self.assertIsNone(obligation.pending_command_id)

    def test_canonical_restore_ack_notifies_all_subscribers(self):
        config = BeliefKVConfig(restore_lease_enabled=False)
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.audit = _AuditRecorder()
        runtime._restore_obligation_counts = Counter()
        runtime._restore_command_to_request = {"canonical": {"first", "second"}}
        runtime._restore_funding_target_by_command = {}
        index = runtime._restore_obligation_index()
        for request_id in ("first", "second"):
            obligation = index.create(
                request_id=request_id,
                workflow_id=f"wf-{request_id}",
                invocation_id=f"inv-{request_id}",
                context_id="ctx",
                context_epoch=0,
                source_retraction_transaction_id=f"retraction-{request_id}",
                source_joint_plan_id=f"joint-{request_id}",
                created_ts_ms=1.0,
                path_extent_ids=("page:1:0",),
            )
            obligation.start_command(
                "canonical",
                CommandKind.PREFETCH_CONTEXT,
                now_ms=1.0,
                attempt_stamp=(1,),
            )

        runtime._advance_restore_obligations(
            (
                CommandAck(
                    "canonical",
                    CommandStatus.COMPLETED,
                    2.0,
                    actual_bytes=100,
                ),
            ),
            now_ms=2.0,
        )

        self.assertEqual(
            index.get("first").state, RestoreObligationState.RESTORE_ACKED
        )
        self.assertEqual(
            index.get("second").state, RestoreObligationState.RESTORE_ACKED
        )
        self.assertNotIn("canonical", runtime._restore_command_to_request)

    def test_noncomplete_restore_acks_park_transaction_for_external_event(self):
        for status in (
            CommandStatus.PARTIAL,
            CommandStatus.REJECTED,
            CommandStatus.STALE,
        ):
            with self.subTest(status=status.value):
                config = BeliefKVConfig(restore_lease_enabled=False)
                runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
                runtime.config = config
                runtime.audit = _AuditRecorder()
                runtime._restore_obligation_counts = Counter()
                runtime._restore_command_to_request = {
                    status.value: {"request"}
                }
                runtime._restore_funding_target_by_command = {}
                obligation = runtime._restore_obligation_index().create(
                    request_id="request",
                    workflow_id="wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                    source_retraction_transaction_id="retraction-1",
                    source_joint_plan_id="joint-1",
                    created_ts_ms=1.0,
                    path_extent_ids=("page:1:0",),
                )
                obligation.start_command(
                    status.value,
                    CommandKind.PREFETCH_CONTEXT,
                    now_ms=1.0,
                    attempt_stamp=(1,),
                )

                runtime._advance_restore_obligations(
                    (
                        CommandAck(
                            status.value,
                            status,
                            2.0,
                            actual_bytes=0,
                        ),
                    ),
                    now_ms=2.0,
                )

                transaction = runtime._restore_transactions["request"]
                self.assertEqual(
                    obligation.state, RestoreObligationState.PARKED_WAIT
                )
                self.assertEqual(
                    transaction.stage, RestoreTransactionStage.WAIT_EVENT
                )

    def test_restore_drain_acquires_and_releases_exclusive_authority(self):
        config = BeliefKVConfig()
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = BeliefKVController(config)
        runtime.audit = _AuditRecorder()
        obligation = runtime._restore_obligation_index().create(
            request_id="request",
            workflow_id="wf",
            invocation_id="inv",
            context_id="ctx",
            context_epoch=0,
            source_retraction_transaction_id="retraction-1",
            source_joint_plan_id="joint-1",
            created_ts_ms=1.0,
            path_extent_ids=(),
        )
        runtime._restore_authority_mode = (
            RestoreAuthorityMode.RESTORE_DRAIN_REQUESTED
        )
        runtime._restore_authority_request_id = "request"

        runtime._advance_restore_authority(now_ms=2.0)
        self.assertEqual(
            runtime._restore_authority_mode,
            RestoreAuthorityMode.RESTORE_DRAIN_ACTIVE,
        )

        obligation.finish(
            RestoreObligationState.CANCELLED,
            now_ms=3.0,
            reason="test_cancel",
        )
        runtime._advance_restore_authority(now_ms=3.0)
        self.assertEqual(
            runtime._restore_authority_mode, RestoreAuthorityMode.NORMAL_JOINT
        )

    def test_restore_authority_prioritizes_visible_owner(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._restore_authority_request_id = "owner"
        runtime._active_restore_lease = lambda: SimpleNamespace(
            request_id="owner"
        )
        entries = {
            "owner": SimpleNamespace(state=AdmissionSideState.VISIBLE_PENDING),
            "dependency": SimpleNamespace(
                state=AdmissionSideState.VISIBLE_PENDING
            ),
        }

        target, reason = runtime._restore_authority_admission_target(
            entries, ("owner", "dependency")
        )

        self.assertEqual(target, "owner")
        self.assertEqual(reason, "restore_owner")

    def test_restore_authority_lease_dependency_precedes_visible_owner(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._restore_authority_request_id = "owner"
        runtime._active_restore_lease = lambda: SimpleNamespace(
            request_id="dependency"
        )
        entries = {
            "owner": SimpleNamespace(state=AdmissionSideState.VISIBLE_PENDING),
            "dependency": SimpleNamespace(
                state=AdmissionSideState.VISIBLE_PENDING
            ),
        }

        target, reason = runtime._restore_authority_admission_target(
            entries, ("dependency",)
        )

        self.assertEqual(target, "dependency")
        self.assertEqual(reason, "restore_lease_dependency")

    def test_restore_authority_inherits_ready_lease_dependency(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._restore_authority_request_id = "owner"
        runtime._active_restore_lease = lambda: SimpleNamespace(
            request_id="dependency"
        )
        entries = {
            "owner": SimpleNamespace(state=AdmissionSideState.WAIT_RESTORE),
            "dependency": SimpleNamespace(
                state=AdmissionSideState.VISIBLE_PENDING
            ),
        }

        target, reason = runtime._restore_authority_admission_target(
            entries, ("dependency",)
        )

        self.assertEqual(target, "dependency")
        self.assertEqual(reason, "restore_lease_dependency")

    def test_restore_authority_does_not_bypass_to_unrelated_ready_debt(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._restore_authority_request_id = "owner"
        runtime._active_restore_lease = lambda: None
        entries = {
            "owner": SimpleNamespace(state=AdmissionSideState.WAIT_RESTORE),
            "other": SimpleNamespace(state=AdmissionSideState.VISIBLE_PENDING),
        }

        target, reason = runtime._restore_authority_admission_target(
            entries, ("other",)
        )

        self.assertIsNone(target)
        self.assertEqual(reason, "restore_owner_not_ready")

    def test_restore_authority_requires_a_ready_ticket_for_visible_owner(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._restore_authority_request_id = "owner"
        runtime._active_restore_lease = lambda: SimpleNamespace(
            request_id="owner"
        )
        entries = {
            "owner": SimpleNamespace(state=AdmissionSideState.VISIBLE_PENDING),
            "other": SimpleNamespace(
                state=AdmissionSideState.VISIBLE_PENDING
            ),
        }

        target, reason = runtime._restore_authority_admission_target(entries, ())

        self.assertIsNone(target)
        self.assertEqual(reason, "restore_owner_not_ready")

    def test_late_runtime_event_is_committed_at_workflow_watermark(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(runtime_event_max_lateness_ms=100.0)
        runtime.controller = BeliefKVController(runtime.config)
        runtime.audit = _AuditRecorder()
        runtime.event_log = _EventBatchRecorder()
        runtime._now_ms = lambda: 250.0
        start = RuntimeEvent("start", 100.0, RuntimeEventKind.WORKFLOW_START, "wf")
        create = RuntimeEvent(
            "create",
            200.0,
            RuntimeEventKind.INVOCATION_CREATE,
            "wf",
            invocation_id="inv",
            context_id="ctx",
            context_epoch=0,
            agent_definition_id="agent",
            agent_instance_id="agent-1",
        )
        late = RuntimeEvent(
            "late",
            150.0,
            RuntimeEventKind.CONTEXT_ADVANCE,
            "wf",
            invocation_id="inv",
            context_id="ctx",
            context_epoch=0,
        )

        runtime._process_events((start, create))
        runtime._process_events((late,))

        committed = runtime.event_log.events[-1]
        self.assertEqual(committed.ts_ms, 200.0)
        self.assertEqual(committed.attributes["beliefkv_source_ts_ms"], 150.0)
        self.assertEqual(committed.attributes["beliefkv_late_by_ms"], 50.0)
        self.assertEqual(runtime.controller.graph.timestamp_watermark("wf"), 200.0)
        event, _, fields = runtime.audit.events[-1]
        self.assertEqual(event, "runtime_event_time_adjusted")
        self.assertEqual(fields["late_by_ms"], 50.0)

    def test_runtime_event_beyond_lateness_limit_is_rejected(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(runtime_event_max_lateness_ms=10.0)
        runtime.controller = BeliefKVController(runtime.config)
        runtime.audit = _AuditRecorder()
        runtime.event_log = _EventBatchRecorder()
        runtime._now_ms = lambda: 250.0
        runtime._process_events(
            (RuntimeEvent("start", 100.0, RuntimeEventKind.WORKFLOW_START, "wf"),)
        )

        with self.assertRaisesRegex(SGLangBackendError, "50.000 ms late"):
            runtime._process_events(
                (RuntimeEvent("late", 50.0, RuntimeEventKind.WORKFLOW_END, "wf"),)
            )

    def test_visible_request_tracks_uncached_prompt_without_reservation(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = BeliefKVController()
        runtime.controller.process_runtime_events(
            (
                RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
                RuntimeEvent(
                    "create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        runtime.tree_cache = object()
        runtime.config = BeliefKVConfig(kv_bytes_per_token=16)
        runtime._request_metadata_by_id = {}
        runtime._request_submitted_ts_by_id = {}
        runtime._ensure_causal_identity = lambda metadata: None
        runtime._now_ms = lambda: 42.0
        runtime.audit = _AuditRecorder()
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)

        def initialize_prefix(req_tree_cache):
            self.assertIs(req_tree_cache, runtime.tree_cache)
            request.fill_ids = request.origin_input_ids
            request.prefix_indices = list(range(90))

        request = SimpleNamespace(
            rid="request-1",
            beliefkv_metadata=metadata,
            sampling_params=SimpleNamespace(max_new_tokens=10),
            origin_input_ids=list(range(100)),
            init_next_round_input=initialize_prefix,
        )

        self.assertTrue(runtime.register_visible_request(request))

        entry = runtime.controller.visible_admission.get("request-1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.request.uncached_prompt_tokens, 10)
        self.assertEqual(entry.request.estimated_incremental_bytes, 320)
        self.assertEqual(runtime.controller.visible_admission.reserved_bytes, 0)
        _, _, fields = runtime.audit.events[0]
        self.assertEqual(fields["estimated_cache_hit_tokens"], 90)
        self.assertEqual(fields["uncached_prompt_tokens"], 10)

    def test_scheduler_step_audits_resolved_transfer_bytes(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        transfer = resolved(
            CommandKind.OFFLOAD_CONTEXT,
            PageHandle(1, 0),
            PhysicalPageAction.START_D2H,
        )
        runtime.event_server = None
        runtime.bridge = _TransferTickBridge(transfer)
        runtime.sync_tree = lambda: None
        runtime._report_allocator_usage = lambda: None
        runtime._now_ms = lambda: 42.0
        runtime._last_admission_audit = None
        runtime.audit = _AuditRecorder()

        runtime.scheduler_step()

        self.assertFalse(runtime.bridge.drain_acks_argument)
        event, ts_ms, fields = runtime.audit.events[0]
        self.assertEqual((event, ts_ms), ("transfer_dispatched", 42.0))
        self.assertEqual(fields["selected_bytes"], transfer.resolved_bytes)
        self.assertEqual(fields["action_counts"], {"start_d2h": 1})
        self.assertEqual(len(runtime._scheduler_timing_samples), 1)

    def test_scheduler_step_audits_ack_before_performance_telemetry(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        ack = CommandAck(
            command_id="offload-1",
            status=CommandStatus.COMPLETED,
            completed_ts_ms=40.0,
            actual_bytes=400,
        )
        telemetry = TransferTelemetry(
            command_id="offload-1",
            submit_ts_ms=10.0,
            start_ts_ms=20.0,
            first_layer_ready_ts_ms=None,
            complete_ts_ms=40.0,
            compute_wait_ms=None,
            actual_bytes=400,
            closure_bytes=400,
            merged_operation_count=0,
            direction=TransferDirection.D2H,
            source_tier="gpu",
            target_tier="host",
            status=CommandStatus.COMPLETED,
        )
        runtime.event_server = None
        runtime.bridge = _TransferTickBridge(
            None, acks=(ack,), telemetry=(telemetry,)
        )
        runtime.sync_tree = lambda: None
        runtime._report_allocator_usage = lambda: None
        runtime._now_ms = lambda: 42.0
        runtime._last_admission_audit = None
        runtime.audit = _AuditRecorder()

        runtime.scheduler_step()

        self.assertEqual(
            [event for event, _, _ in runtime.audit.events],
            ["transfer_acknowledged", "transfer_telemetry"],
        )
        self.assertEqual(runtime.audit.events[1][1], 42.0)
        self.assertEqual(runtime.audit.events[1][2]["complete_ts_ms"], 40.0)
        timing = runtime._scheduler_timing_samples[-1]
        self.assertEqual(timing[2], 1)
        self.assertLessEqual(timing[1], timing[0])

    def test_predictive_transfer_telemetry_is_correctness_evidence(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.audit = _AuditRecorder()
        runtime.transfer_telemetry_log = None
        runtime._now_ms = lambda: 42.0
        runtime._pending_online_joint_residency = SimpleNamespace(
            command_id="predictive-command",
            predictive_intent_id="intent-1",
        )
        telemetry = TransferTelemetry(
            command_id="predictive-command",
            submit_ts_ms=10.0,
            start_ts_ms=20.0,
            first_layer_ready_ts_ms=None,
            complete_ts_ms=40.0,
            compute_wait_ms=1.0,
            actual_bytes=400,
            closure_bytes=400,
            merged_operation_count=0,
            direction=TransferDirection.D2H,
            source_tier="gpu",
            target_tier="host",
            status=CommandStatus.COMPLETED,
            extent_count=2,
        )

        runtime._emit_transfer_telemetry(telemetry)

        event, _, fields = runtime.audit.events[-1]
        self.assertEqual(event, "transfer_telemetry")
        self.assertEqual(fields["audit_level"], "correctness")
        self.assertEqual(fields["predictive_intent_id"], "intent-1")

    def test_h2d_waiter_holds_no_reservation_and_rematches_after_ack(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = BeliefKVController()
        runtime.controller.process_runtime_events(
            (
                RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
                RuntimeEvent(
                    "create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        runtime.tree_cache = object()
        runtime.config = BeliefKVConfig(kv_bytes_per_token=16)
        runtime._now_ms = lambda: 42.0
        runtime.audit = _AuditRecorder()
        runtime._request_metadata_by_id = {
            "request-1": BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        }
        runtime._request_submitted_ts_by_id = {"request-1": 10.0}
        runtime._pending_h2d_contexts = set()
        refresh_count = 0

        def refresh(_tree_cache):
            nonlocal refresh_count
            refresh_count += 1
            request.prefix_indices = _NoBooleanSequence(4)

        request = SimpleNamespace(
            rid="request-1",
            beliefkv_metadata=runtime._request_metadata_by_id["request-1"],
            origin_input_ids=_NoBooleanSequence(5),
            prefix_indices=_NoBooleanSequence(0),
            init_next_round_input=refresh,
        )
        runtime.scheduler = SimpleNamespace(waiting_queue=[request])
        runtime.controller.register_visible_request(
            AdmissionRequest(
                "request-1",
                "wf",
                "inv",
                "ctx",
                0,
                10.0,
                5,
                1,
                16,
                prompt_tokens=5,
            )
        )

        runtime._mark_context_wait_restore(
            "ctx", bundle_ids=("bundle",), reason="h2d_inflight"
        )
        waiting = runtime.controller.visible_admission.get("request-1")
        self.assertEqual(waiting.state, AdmissionSideState.WAIT_RESTORE)
        self.assertEqual(runtime.controller.visible_admission.reserved_bytes, 0)
        self.assertEqual(refresh_count, 0)

        runtime._release_h2d_waiters("ctx")
        self.assertEqual(refresh_count, 1)
        released = runtime.controller.visible_admission.get("request-1")
        self.assertEqual(released.state, AdmissionSideState.VISIBLE_PENDING)
        self.assertEqual(released.request.uncached_prompt_tokens, 1)

    def test_h2d_dependency_blocks_only_requests_whose_radix_path_intersects(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = BeliefKVController()
        runtime.controller.process_runtime_events(
            (
                RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
                RuntimeEvent(
                    "create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        runtime.registry = SGLangNodeRegistry()
        runtime.tree_cache = _TreeCache()
        runtime.config = BeliefKVConfig(kv_bytes_per_token=16)
        matched = _Node(11)
        matched.parent = runtime.tree_cache.root_node
        unrelated = _Node(12)
        unrelated.parent = runtime.tree_cache.root_node
        matched_handle = runtime.registry.register(matched)
        unrelated_handle = runtime.registry.register(unrelated)
        for handle in (matched_handle, unrelated_handle):
            runtime.controller.page_index.register_page(
                handle,
                size_bytes=100,
                residency=PhysicalResidency.CPU_ONLY,
            )
        runtime.controller.page_index.bind_pages(
            "ctx", 0, (matched_handle, unrelated_handle)
        )
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        requests = [
            SimpleNamespace(rid="matched", last_node=matched),
            SimpleNamespace(rid="unrelated", last_node=unrelated),
        ]
        runtime.scheduler = SimpleNamespace(waiting_queue=requests)
        for request in requests:
            runtime.controller.register_visible_request(
                AdmissionRequest(
                    request.rid,
                    "wf",
                    "inv",
                    "ctx",
                    0,
                    10.0,
                    1,
                    1,
                    16,
                )
            )

        runtime._mark_h2d_waiters(
            "ctx",
            restored_extent_ids=(
                f"page:{matched_handle.page_id}:"
                f"{matched_handle.allocation_generation}",
            ),
            reason="h2d_inflight",
        )

        matched_entry = runtime.controller.visible_admission.get("matched")
        unrelated_entry = runtime.controller.visible_admission.get("unrelated")
        self.assertEqual(matched_entry.state, AdmissionSideState.WAIT_RESTORE)
        self.assertEqual(
            unrelated_entry.state, AdmissionSideState.VISIBLE_PENDING
        )

    def test_online_residency_hysteresis_blocks_reclaim_after_recent_restore(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=1000,
            reserve_hbm_bytes=100,
            residency_hysteresis_ms=100.0,
            joint_emergency_hbm_ratio=0.98,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.controller.report_hbm_usage(900)
        runtime.audit = _AuditRecorder()
        runtime._online_joint_counts = Counter()
        runtime._online_joint_hysteresis_audit = set()
        runtime._online_joint_last_residency_action = {
            "bundle": (ResidencyAction.PREFETCH_GPU, 10.0)
        }

        blocked = runtime._online_residency_hysteresis_blocks(
            plan_id="plan",
            bundle_id="bundle",
            action=ResidencyAction.COMMIT_CPU,
            now_ms=50.0,
        )
        runtime.controller.report_hbm_usage(990)
        emergency = runtime._online_residency_hysteresis_blocks(
            plan_id="plan-2",
            bundle_id="bundle",
            action=ResidencyAction.COMMIT_CPU,
            now_ms=60.0,
        )

        self.assertTrue(blocked)
        self.assertFalse(emergency)

    def test_shutdown_prepare_suppresses_transaction_state_advancement(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._shutdown_state = "preparing"
        retraction = SimpleNamespace(stage="residency_pending")
        residency = SimpleNamespace(stage="queued")
        runtime._pending_running_retraction_transaction = retraction
        runtime._pending_online_joint_residency = residency
        runtime._restore_command_to_request = {"command": {"request"}}
        ack = CommandAck(
            "command",
            CommandStatus.COMPLETED,
            42.0,
            actual_bytes=100,
        )

        runtime._advance_retraction_transaction((ack,), now_ms=42.0)
        runtime._advance_online_joint_residency((ack,), now_ms=42.0)
        runtime._advance_restore_obligations((ack,), now_ms=42.0)

        self.assertEqual(retraction.stage, "residency_pending")
        self.assertEqual(residency.stage, "queued")
        self.assertEqual(
            runtime._restore_command_to_request, {"command": {"request"}}
        )

    def test_shutdown_drain_terminally_acks_queued_and_inflight_commands(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(shutdown_drain_timeout_ms=0.0)
        runtime._now_ms = lambda: 42.0
        runtime.audit = _AuditRecorder()
        runtime._shutdown_expected_command_ids = {"queued", "inflight"}
        runtime._shutdown_expected_transaction_ids = set()
        runtime._shutdown_terminal_command_outcomes = {}
        runtime._shutdown_terminal_transaction_ids = set()
        runtime._retire_h2d_commands = lambda _acks: ()
        runtime.sync_tree = lambda **_kwargs: None
        runtime._advance_host_cleanup = lambda *_args, **_kwargs: None
        runtime._advance_retraction_transaction = lambda *_args, **_kwargs: None
        runtime._advance_online_joint_residency = lambda *_args, **_kwargs: None
        runtime._advance_restore_obligations = lambda *_args, **_kwargs: None

        class Controller:
            inflight_command_ids = ("inflight",)
            command_queue = SimpleNamespace(pending_commands=lambda: ())

            def cancel_queued_commands(self, *, now_ms, reason):
                assert now_ms == 42.0
                assert reason == "runtime_shutdown_queued_cancelled"
                return (
                    CommandAck(
                        "queued",
                        CommandStatus.CANCELLED,
                        now_ms,
                        0,
                        reason=reason,
                    ),
                )

        controller = Controller()
        runtime.controller = controller

        class Bridge:
            @staticmethod
            def drain_acks():
                return ()

            @staticmethod
            def abort_all(*, reason):
                assert reason == "runtime_shutdown_drain_timeout"
                controller.inflight_command_ids = ()
                return (
                    CommandAck(
                        "inflight",
                        CommandStatus.CANCELLED,
                        42.0,
                        0,
                        reason=reason,
                    ),
                )

        runtime.bridge = Bridge()
        runtime._drain_shutdown_acks()

        self.assertEqual(
            runtime._shutdown_terminal_command_outcomes,
            {"queued": "cancelled", "inflight": "cancelled"},
        )
        self.assertEqual(controller.inflight_command_ids, ())
        terminal = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "transfer_acknowledged"
        ]
        self.assertEqual(
            {item["command_id"] for item in terminal},
            {"queued", "inflight"},
        )
        self.assertTrue(all(item["shutdown_drain"] for item in terminal))

    def test_close_emits_controller_timing_summary(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._closed = False
        runtime._now_ms = lambda: 42.0
        runtime.event_server = None
        runtime.event_log = None
        runtime.transfer_telemetry_log = _CloseRecorder()
        runtime.audit = _AuditRecorder()
        runtime.audit.close = lambda: None
        runtime._scheduler_timing_samples = deque(
            ((10.0, 0.2, 1), (20.0, 0.4, 2)), maxlen=65_536
        )

        runtime.close()

        event, _, fields = next(
            item
            for item in runtime.audit.events
            if item[0] == "controller_timing_summary"
        )
        self.assertEqual(event, "controller_timing_summary")
        self.assertEqual(fields["telemetry_event_count"], 3)
        self.assertAlmostEqual(fields["telemetry_event_overhead_ratio_p99"], 0.02)

    def test_embedded_runtime_close_is_idempotent(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._closed = False
        runtime._now_ms = lambda: 42.0
        runtime.event_server = _CloseRecorder()
        runtime.event_log = _CloseRecorder()
        runtime.audit = _CloseRecorder()
        poller = SimpleNamespace(unregister=mock.Mock())
        runtime.scheduler = SimpleNamespace(
            idle_sleeper=SimpleNamespace(poller=poller),
            _beliefkv_event_poll_fd=17,
        )

        runtime.close()
        runtime.close()

        self.assertTrue(runtime._closed)
        self.assertIsNone(runtime.event_server)
        self.assertIsNone(runtime.event_log)
        poller.unregister.assert_called_once_with(17)
        self.assertIsNone(runtime.scheduler._beliefkv_event_poll_fd)
        self.assertEqual(
            [item[0] for item in runtime.audit.events],
            ["shutdown_prepare", "shutdown_ack", "runtime_shutdown"],
        )
        self.assertEqual(runtime.audit.close_count, 1)

    def test_scheduler_shutdown_shields_runtime_close_from_repeated_signals(self):
        runtime = SimpleNamespace(close=mock.Mock())
        previous = {
            signal_number: object()
            for signal_number in (signal.SIGINT, signal.SIGTERM, signal.SIGQUIT)
        }

        with mock.patch(
            "beliefkv.runtime.sglang_v052rc1.signal.signal",
            side_effect=lambda signum, handler: previous[signum],
        ) as install:
            close_runtime_with_signal_shield(runtime)

        runtime.close.assert_called_once_with()
        self.assertEqual(install.call_count, 6)

    def test_scheduler_sigterm_handler_unwinds_through_finally(self):
        previous = object()
        with mock.patch(
            "beliefkv.runtime.sglang_v052rc1.signal.signal",
            return_value=previous,
        ) as install:
            self.assertIs(install_scheduler_shutdown_handler(), previous)

        signum, handler = install.call_args.args
        self.assertEqual(signum, signal.SIGTERM)
        with self.assertRaises(SystemExit) as caught:
            handler(signal.SIGTERM, None)
        self.assertEqual(caught.exception.code, 0)

    def test_shutdown_ack_is_written_after_audit_close(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._shutdown_state = "acknowledged"
        runtime.audit = _CloseRecorder()
        runtime.audit.run_id = "run"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime._runtime_scheduler_pid_path = root / "scheduler.pid.json"
            runtime._runtime_shutdown_ack_path = root / "shutdown_ack.json"
            runtime._runtime_summary_path = root / "latest_runtime_summary.json"

            runtime._write_scheduler_identity()
            runtime.audit.close()
            runtime._write_shutdown_ack()

            identity = json.loads(
                runtime._runtime_scheduler_pid_path.read_text(encoding="utf-8")
            )
            ack = json.loads(
                runtime._runtime_shutdown_ack_path.read_text(encoding="utf-8")
            )
        self.assertEqual(identity["pid"], ack["pid"])
        self.assertEqual(
            identity["linux_start_time_ticks"],
            ack["linux_start_time_ticks"],
        )
        self.assertEqual(ack["shutdown_state"], "acknowledged")
        self.assertEqual(runtime.audit.close_count, 1)

    def test_shadow_cancel_does_not_assume_submitted_dma_is_preemptible(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        node = _Node()
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)
        command = resolved(CommandKind.SHADOW_CONTEXT, handle, PhysicalPageAction.START_D2H)
        submission = backend.submit(command)
        backend.cancel(command.command.command_id)
        acks = backend.poll_acks()
        self.assertEqual(submission.started_handles, (handle,))
        self.assertEqual(acks[0].status.value, "completed")
        self.assertIn("nonpreemptible", acks[0].reason)
        self.assertFalse(node.evicted)
        self.assertTrue(node.backuped)

    def test_extent_split_after_d2h_records_dma_but_rejects_residency_commit(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        node = _Node()
        node.key = [1, 2, 3, 4]
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)

        backend.submit(
            resolved(
                CommandKind.OFFLOAD_CONTEXT,
                handle,
                PhysicalPageAction.START_D2H,
            )
        )
        node.key = node.key[2:]
        node.value = node.value[2:]

        acks = backend.poll_acks()
        telemetry = backend.poll_transfer_telemetry()

        self.assertEqual(acks[0].status, CommandStatus.REJECTED)
        self.assertEqual(acks[0].actual_bytes, 0)
        self.assertIn("Radix extent mutated", acks[0].reason)
        self.assertEqual(
            {item.code for item in acks[0].blockers},
            {TransferBlockerCode.EXTENT_MUTATED},
        )
        self.assertIsNotNone(node.value)
        self.assertEqual(telemetry[0].actual_bytes, 400)
        self.assertEqual(telemetry[0].status, CommandStatus.REJECTED)

    def test_urgent_d2h_evicts_only_after_backup_ack(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        node = _Node()
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)
        command = resolved(CommandKind.OFFLOAD_CONTEXT, handle, PhysicalPageAction.START_D2H)
        backend.submit(command)
        self.assertFalse(node.evicted)
        ack = backend.poll_acks()[0]
        telemetry = backend.poll_transfer_telemetry()[0]
        self.assertTrue(node.evicted)
        self.assertEqual(ack.actual_bytes, 400)
        self.assertEqual(telemetry.command_id, ack.command_id)
        self.assertEqual(telemetry.direction, TransferDirection.D2H)
        self.assertEqual(telemetry.actual_bytes, 400)
        self.assertEqual(telemetry.closure_bytes, 400)
        self.assertIsNone(telemetry.first_layer_ready_ts_ms)
        self.assertEqual(telemetry.host_copy_state, "missing")
        self.assertIs(telemetry.pinned_host, True)
        self.assertIsNotNone(telemetry.allocator_submit_ms)
        self.assertEqual(
            telemetry.start_timestamp_semantics, "hicache_api_submit_begin"
        )

    def test_atomic_bundle_preflight_has_no_side_effect_when_child_is_locked(self):
        tree = _LockPropagatingTreeCache()
        registry = SGLangNodeRegistry()
        parent = _Node(1)
        child = _Node(2)
        parent.parent = tree.root_node
        child.parent = parent
        parent.children["child"] = child
        child.lock_ref = 1
        parent_handle = registry.register(parent)
        child_handle = registry.register(child)
        command = resolved_bundle(
            CommandKind.OFFLOAD_CONTEXT,
            (
                (parent_handle, PhysicalPageAction.START_D2H, 400),
                (child_handle, PhysicalPageAction.START_D2H, 400),
            ),
        )
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)

        submission = backend.submit(command)
        ack = backend.poll_acks()[0]

        self.assertEqual(submission.started_handles, ())
        self.assertEqual(ack.status, CommandStatus.REJECTED)
        self.assertEqual(ack.actual_bytes, 0)
        self.assertEqual(
            {item.code for item in ack.blockers},
            {TransferBlockerCode.NODE_LOCKED},
        )
        self.assertEqual(tree.write_order, [])
        self.assertFalse(parent.backuped)
        self.assertFalse(child.backuped)
        self.assertFalse(parent.evicted)
        self.assertFalse(child.evicted)

    def test_atomic_d2h_submits_shallow_first_and_evicts_deep_first(self):
        tree = _LockPropagatingTreeCache()
        registry = SGLangNodeRegistry()
        parent = _Node(1)
        child = _Node(2)
        parent.parent = tree.root_node
        child.parent = parent
        parent.children["child"] = child
        parent_handle = registry.register(parent)
        child_handle = registry.register(child)
        command = resolved_bundle(
            CommandKind.OFFLOAD_CONTEXT,
            (
                (child_handle, PhysicalPageAction.START_D2H, 400),
                (parent_handle, PhysicalPageAction.START_D2H, 400),
            ),
        )
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)

        submission = backend.submit(command)
        self.assertEqual(tree.write_order, [parent.id, child.id])
        self.assertEqual(tree.batch_write_calls, [[parent.id, child.id]])
        self.assertFalse(parent.evicted)
        self.assertFalse(child.evicted)

        ack = backend.poll_acks()[0]
        telemetry = backend.poll_transfer_telemetry()[0]

        self.assertEqual(
            submission.started_handles, tuple(sorted((parent_handle, child_handle)))
        )
        self.assertEqual(ack.status, CommandStatus.COMPLETED)
        self.assertEqual(ack.actual_bytes, 800)
        self.assertEqual(tree.evict_order, [child.id, parent.id])
        self.assertTrue(parent.evicted)
        self.assertTrue(child.evicted)
        self.assertEqual(telemetry.actual_bytes, 800)
        self.assertEqual(telemetry.status, CommandStatus.COMPLETED)
        self.assertEqual(telemetry.extent_count, 2)
        self.assertEqual(telemetry.extent_bytes_min, 400)
        self.assertEqual(telemetry.extent_bytes_p50, 400)
        self.assertEqual(telemetry.extent_bytes_max, 400)
        self.assertEqual(telemetry.small_extent_ratio, 1.0)

    def test_atomic_drop_bundle_commits_deep_first_and_keeps_host_copies(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        parent = _Node(1)
        child = _Node(2)
        parent.parent = tree.root_node
        child.parent = parent
        parent.children["child"] = child
        parent.host_value = [10, 11, 12, 13]
        child.host_value = [20, 21, 22, 23]
        parent_handle = registry.register(parent)
        child_handle = registry.register(child)
        command = resolved_bundle(
            CommandKind.DROP_CONTEXT,
            (
                (parent_handle, PhysicalPageAction.DROP, 400),
                (child_handle, PhysicalPageAction.DROP, 400),
            ),
        )
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)

        submission = backend.submit(command)
        ack = backend.poll_acks()[0]

        self.assertEqual(
            submission.started_handles, tuple(sorted((parent_handle, child_handle)))
        )
        self.assertEqual(ack.status, CommandStatus.COMPLETED)
        self.assertEqual(ack.actual_bytes, 800)
        self.assertTrue(parent.evicted)
        self.assertTrue(child.evicted)
        self.assertTrue(parent.backuped)
        self.assertTrue(child.backuped)

    def test_atomic_h2d_native_closure_failure_has_no_partial_ancestor(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        parent = _Node(1)
        child = _Node(2)
        parent.parent = tree.root_node
        child.parent = parent
        parent.children["child"] = child
        for node in (parent, child):
            node.value = None
            node.host_value = [10, 11, 12, 13]
        parent_handle = registry.register(parent)
        child_handle = registry.register(child)
        tree.load_back = lambda _node, **_kwargs: None
        command = resolved_bundle(
            CommandKind.PREFETCH_CONTEXT,
            (
                (child_handle, PhysicalPageAction.START_H2D, 400),
                (parent_handle, PhysicalPageAction.START_H2D, 400),
            ),
        )
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)

        submission = backend.submit(command)
        ack = backend.poll_acks()[0]
        telemetry = backend.poll_transfer_telemetry()[0]

        self.assertEqual(submission.started_handles, ())
        self.assertEqual(ack.status, CommandStatus.REJECTED)
        self.assertEqual(ack.actual_bytes, 0)
        self.assertTrue(parent.evicted)
        self.assertTrue(child.evicted)
        self.assertEqual(telemetry.actual_bytes, 0)
        self.assertEqual(telemetry.status, CommandStatus.REJECTED)

    def test_atomic_h2d_uses_one_native_operation_for_ancestor_chain(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        parent = _Node(1)
        child = _Node(2)
        parent.parent = tree.root_node
        child.parent = parent
        parent.children["child"] = child
        for node in (parent, child):
            node.value = None
            node.host_value = [10, 11, 12, 13]
        parent_handle = registry.register(parent)
        child_handle = registry.register(child)
        load_back = tree.load_back
        loaded_node_ids = []

        def traced_load(node, **kwargs):
            loaded_node_ids.append(node.id)
            return load_back(node, **kwargs)

        tree.load_back = traced_load
        command = resolved_bundle(
            CommandKind.PREFETCH_CONTEXT,
            (
                (parent_handle, PhysicalPageAction.START_H2D, 400),
                (child_handle, PhysicalPageAction.START_H2D, 400),
            ),
        )
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)

        submission = backend.submit(command)
        ack = backend.poll_acks()[0]

        self.assertEqual(loaded_node_ids, [child.id])
        self.assertEqual(
            submission.started_handles, tuple(sorted((parent_handle, child_handle)))
        )
        self.assertEqual(ack.status, CommandStatus.COMPLETED)
        self.assertEqual(ack.actual_bytes, 800)
        self.assertFalse(parent.evicted)
        self.assertFalse(child.evicted)

    def test_atomic_h2d_allows_concurrent_child_link_on_same_extent(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        node = _Node(1)
        node.parent = tree.root_node
        node.value = None
        node.host_value = [10, 11, 12, 13]
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)

        submission = backend.submit(
            resolved_bundle(
                CommandKind.PREFETCH_CONTEXT,
                ((handle, PhysicalPageAction.START_H2D, 400),),
            )
        )
        branch = _Node(2)
        branch.parent = node
        node.children["concurrent-branch"] = branch
        ack = backend.poll_acks()[0]

        self.assertEqual(submission.started_handles, (handle,))
        self.assertEqual(ack.status, CommandStatus.COMPLETED)
        self.assertFalse(node.evicted)

    def test_atomic_h2d_forces_tiny_closure_without_native_eviction(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        node = _Node(1)
        node.parent = tree.root_node
        node.value = None
        node.host_value = [10, 11, 12, 13]
        handle = registry.register(node)
        command = resolved_bundle(
            CommandKind.PREFETCH_CONTEXT,
            ((handle, PhysicalPageAction.START_H2D, 400),),
        )
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)

        submission = backend.submit(command)
        ack = backend.poll_acks()[0]

        self.assertEqual(submission.started_handles, (handle,))
        self.assertEqual(ack.status, CommandStatus.COMPLETED)
        self.assertEqual(
            tree.load_back_calls,
            [{"node_id": 1, "force": True, "allow_eviction": False}],
        )

    def test_atomic_h2d_rejects_before_load_when_allocator_cannot_fit_closure(self):
        tree = _TreeCache()
        tree.token_to_kv_pool_allocator.available_tokens = 3
        registry = SGLangNodeRegistry()
        node = _Node(1)
        node.parent = tree.root_node
        node.value = None
        node.host_value = [10, 11, 12, 13]
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)

        submission = backend.submit(
            resolved_bundle(
                CommandKind.PREFETCH_CONTEXT,
                ((handle, PhysicalPageAction.START_H2D, 400),),
            )
        )
        ack = backend.poll_acks()[0]

        self.assertEqual(submission.started_handles, ())
        self.assertEqual(tree.load_back_calls, [])
        self.assertEqual(ack.status, CommandStatus.REJECTED)
        self.assertEqual(
            {item.code for item in ack.blockers},
            {TransferBlockerCode.DEVICE_CAPACITY},
        )

    def test_h2d_rejects_divergent_tree_and_controller_allocators(self):
        tree = _TreeCache()
        tree.cache_controller.mem_pool_device_allocator = _Allocator(1_000_000)
        registry = SGLangNodeRegistry()
        node = _Node(1)
        node.parent = tree.root_node
        node.value = None
        node.host_value = [10, 11, 12, 13]
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)

        submission = backend.submit(
            resolved_bundle(
                CommandKind.PREFETCH_CONTEXT,
                ((handle, PhysicalPageAction.START_H2D, 400),),
            )
        )
        ack = backend.poll_acks()[0]

        self.assertEqual(submission.started_handles, ())
        self.assertEqual(ack.status, CommandStatus.REJECTED)
        self.assertEqual(
            {item.code for item in ack.blockers},
            {TransferBlockerCode.UNKNOWN_BACKEND},
        )

    def test_h2d_rejects_context_that_is_already_engine_visible(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        node = _Node(1)
        node.parent = tree.root_node
        node.value = None
        node.host_value = [10, 11, 12, 13]
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(
            tree,
            registry,
            now_ms=lambda: 2,
            h2d_context_is_busy=lambda context_id: context_id == "ctx",
        )

        submission = backend.submit(
            resolved_bundle(
                CommandKind.PREFETCH_CONTEXT,
                ((handle, PhysicalPageAction.START_H2D, 400),),
            )
        )
        ack = backend.poll_acks()[0]

        self.assertEqual(submission.started_handles, ())
        self.assertEqual(ack.status, CommandStatus.REJECTED)
        self.assertEqual(
            {item.code for item in ack.blockers},
            {TransferBlockerCode.ENGINE_BUSY},
        )

    def test_h2d_callback_failure_rolls_back_atomic_gpu_residency(self):
        tree = _TreeCache()
        initial_available = tree.token_to_kv_pool_allocator.available_size()
        registry = SGLangNodeRegistry()
        node = _Node(1)
        node.parent = tree.root_node
        node.value = None
        node.host_value = [10, 11, 12, 13]
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)
        command = resolved_bundle(
            CommandKind.PREFETCH_CONTEXT,
            ((handle, PhysicalPageAction.START_H2D, 400),),
        )

        backend.submit(command)
        tree.callback_errors.append(
            {
                "direction": "h2d",
                "operation_id": node.id,
                "error": "RuntimeError: malformed completion chain",
            }
        )
        ack = backend.poll_acks()[0]

        self.assertEqual(ack.status, CommandStatus.REJECTED)
        self.assertEqual(
            {item.code for item in ack.blockers},
            {TransferBlockerCode.UNKNOWN_BACKEND},
        )
        self.assertTrue(node.evicted)
        self.assertEqual(
            tree.token_to_kv_pool_allocator.available_size(), initial_available
        )
        self.assertEqual(len(backend.poll_callback_errors()), 1)

    def test_allocator_reconciliation_claims_live_radix_indices(self):
        import torch

        class AllocatorWithPages:
            page_size = 1

            def __init__(self):
                self.free_pages = torch.tensor([1, 2, 4, 5])
                self.release_pages = torch.tensor([3])

            def available_size(self):
                return len(self.free_pages) + len(self.release_pages)

        tree = _TreeCache()
        node = _Node(1)
        node.parent = tree.root_node
        node.value = torch.tensor([2, 3])
        tree.root_node.children["node"] = node
        tree.evictable_tokens = 2
        allocator = AllocatorWithPages()
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.tree_cache = tree
        runtime.scheduler = SimpleNamespace(
            token_to_kv_pool_allocator=allocator,
            max_total_num_tokens=5,
        )
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 10.0

        runtime._ensure_allocator_radix_consistency(reason="test")

        self.assertEqual(allocator.free_pages.tolist(), [1, 4, 5])
        self.assertEqual(allocator.release_pages.tolist(), [])
        self.assertEqual(allocator.available_size() + tree.evictable_size(), 5)
        event, _, fields = runtime.audit.events[0]
        self.assertEqual(event, "allocator_radix_resynchronized")
        self.assertEqual(fields["overlap_tokens"], 2)
        self.assertEqual(fields["duplicate_free_tokens"], 0)

    def test_allocator_reconciliation_protects_live_engine_private_indices(self):
        import torch

        class AllocatorWithPages:
            page_size = 1

            def __init__(self):
                self.free_pages = torch.tensor([1, 2, 4, 5])
                self.release_pages = torch.empty((0,), dtype=torch.int64)

            def available_size(self):
                return len(self.free_pages) + len(self.release_pages)

        tree = _TreeCache()
        node = _Node(1)
        node.parent = tree.root_node
        node.value = torch.tensor([3])
        tree.root_node.children["node"] = node
        tree.evictable_tokens = 1
        allocator = AllocatorWithPages()
        request = SimpleNamespace(req_pool_idx=0, seqlen=1)
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.tree_cache = tree
        runtime.scheduler = SimpleNamespace(
            token_to_kv_pool_allocator=allocator,
            max_total_num_tokens=5,
            req_to_token_pool=SimpleNamespace(
                req_to_token=torch.tensor([[2]], dtype=torch.int64)
            ),
            running_batch=SimpleNamespace(
                reqs=[request], seq_lens=torch.tensor([1], dtype=torch.int64)
            ),
            last_batch=None,
            chunked_req=None,
        )
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 10.0

        runtime._ensure_allocator_radix_consistency(
            reason="test_retraction",
            force=True,
            include_engine_ownership=True,
        )

        self.assertEqual(allocator.free_pages.tolist(), [1, 4, 5])
        event, _, fields = runtime.audit.events[0]
        self.assertEqual(event, "allocator_radix_resynchronized")
        self.assertEqual(fields["overlap_tokens"], 1)
        self.assertEqual(fields["engine_live_tokens"], 1)

    def test_allocator_reconciliation_deduplicates_free_pools(self):
        import torch

        class AllocatorWithPages:
            page_size = 1

            def __init__(self):
                self.free_pages = torch.tensor([1, 2, 2, 4])
                self.release_pages = torch.tensor([3, 3])

            def available_size(self):
                return len(self.free_pages) + len(self.release_pages)

        tree = _TreeCache()
        node = _Node(1)
        node.parent = tree.root_node
        node.value = torch.tensor([5])
        tree.root_node.children["node"] = node
        tree.evictable_tokens = 1
        allocator = AllocatorWithPages()
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.tree_cache = tree
        runtime.scheduler = SimpleNamespace(
            token_to_kv_pool_allocator=allocator,
            max_total_num_tokens=5,
        )
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 10.0

        runtime._ensure_allocator_radix_consistency(reason="test_duplicate_free")

        self.assertEqual(allocator.free_pages.tolist(), [1, 2, 3, 4])
        self.assertEqual(allocator.release_pages.tolist(), [])
        event, _, fields = runtime.audit.events[0]
        self.assertEqual(event, "allocator_radix_resynchronized")
        self.assertEqual(fields["overlap_tokens"], 0)
        self.assertEqual(fields["duplicate_free_tokens"], 2)

    def test_atomic_h2d_allows_new_sibling_on_non_action_gpu_anchor(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        anchor = _Node(1)
        child = _Node(2)
        anchor.parent = tree.root_node
        child.parent = anchor
        anchor.children["child"] = child
        child.value = None
        child.host_value = [10, 11, 12, 13]
        anchor_handle = registry.register(anchor)
        child_handle = registry.register(child)
        command = resolved_bundle(
            CommandKind.PREFETCH_CONTEXT,
            ((child_handle, PhysicalPageAction.START_H2D, 400),),
            closure_handles=(anchor_handle, child_handle),
        )
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)

        submission = backend.submit(command)
        sibling = _Node(3)
        sibling.parent = anchor
        anchor.children["sibling"] = sibling
        ack = backend.poll_acks()[0]

        self.assertEqual(submission.started_handles, (child_handle,))
        self.assertEqual(ack.status, CommandStatus.COMPLETED)
        self.assertEqual(ack.actual_bytes, 400)
        self.assertFalse(child.evicted)

    def test_backend_tracks_one_h2d_and_one_d2h_concurrently(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()

        d2h_node = _Node(1)
        d2h_node.parent = tree.root_node
        tree.root_node.children["d2h"] = d2h_node
        d2h_handle = registry.register(d2h_node)

        h2d_node = _Node(2)
        h2d_node.parent = tree.root_node
        h2d_node.value = None
        h2d_node.host_value = [30, 31, 32, 33]
        tree.root_node.children["h2d"] = h2d_node
        h2d_handle = registry.register(h2d_node)

        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)
        d2h_submission = backend.submit(
            resolved(
                CommandKind.OFFLOAD_CONTEXT,
                d2h_handle,
                PhysicalPageAction.START_D2H,
            )
        )
        h2d_submission = backend.submit(
            resolved(
                CommandKind.PREFETCH_CONTEXT,
                h2d_handle,
                PhysicalPageAction.START_H2D,
            )
        )

        self.assertEqual(d2h_submission.started_handles, (d2h_handle,))
        self.assertEqual(h2d_submission.started_handles, (h2d_handle,))
        self.assertEqual(len(backend._pending), 2)

        acknowledgements = backend.poll_acks()

        self.assertEqual(
            {item.command_id for item in acknowledgements},
            {"cmd-offload_context", "cmd-prefetch_context"},
        )
        self.assertTrue(
            all(item.status == CommandStatus.COMPLETED for item in acknowledgements)
        )
        self.assertEqual(backend._pending, {})
    def test_pinned_hicache_capabilities_do_not_claim_unobservable_features(self):
        tree = _TreeCache()
        tree.token_to_kv_pool_host = SimpleNamespace(layout="page_first")
        backend = HiCacheNodeCommandBackend(tree, SGLangNodeRegistry())

        capabilities = backend.capabilities

        self.assertFalse(capabilities.operation_merge)
        self.assertFalse(capabilities.layer_completion_events)
        self.assertTrue(capabilities.page_first_host_layout)
        self.assertTrue(capabilities.proactive_load_trigger)
        self.assertEqual(capabilities.max_inflight_operations, 1)
        self.assertEqual(capabilities.physical_unit, "node_extent")

    def test_registry_rejects_handle_after_cache_reset(self):
        registry = SGLangNodeRegistry()
        handle = registry.register(_Node())
        registry.reset()
        with self.assertRaisesRegex(RuntimeError, "stale"):
            registry.resolve(handle)

    def test_h2d_rejects_an_unselected_evicted_ancestor(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        parent = _Node(1)
        parent.parent = tree.root_node
        parent.value = None
        parent.host_value = [1]
        child = _Node(2)
        child.parent = parent
        child.value = None
        child.host_value = [2]
        handle = registry.register(child)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)
        command = resolved(
            CommandKind.PREFETCH_CONTEXT,
            handle,
            PhysicalPageAction.START_H2D,
        )
        submission = backend.submit(command)
        self.assertEqual(submission.started_handles, ())
        self.assertEqual(tree.load_ready_calls, 0)
        ack = backend.poll_acks()[0]
        telemetry = backend.poll_transfer_telemetry()[0]
        self.assertEqual(ack.status.value, "rejected")
        self.assertEqual(
            {item.code for item in ack.blockers},
            {TransferBlockerCode.ANCESTOR_CLOSURE},
        )
        self.assertEqual(telemetry.status.value, "rejected")
        self.assertEqual(telemetry.direction, TransferDirection.H2D)
        self.assertIsNone(telemetry.start_ts_ms)
        self.assertEqual(telemetry.actual_bytes, 0)
        self.assertEqual(telemetry.closure_bytes, 400)

    def test_proactive_h2d_explicitly_starts_hicache_load_queue(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        node = _Node(1)
        node.parent = tree.root_node
        node.value = None
        node.host_value = [1, 2, 3, 4]
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)
        command = resolved(
            CommandKind.PREFETCH_CONTEXT,
            handle,
            PhysicalPageAction.START_H2D,
        )

        submission = backend.submit(command)

        self.assertEqual(submission.started_handles, (handle,))
        self.assertEqual(tree.load_ready_calls, 1)
        ack = backend.poll_acks()[0]
        self.assertEqual(ack.status.value, "completed")
        self.assertEqual(ack.actual_bytes, 400)
        self.assertFalse(node.loading)

    def test_h2d_device_allocation_failure_is_structured(self):
        tree = _TreeCache()
        tree.load_back = lambda _node, **_kwargs: None
        registry = SGLangNodeRegistry()
        node = _Node(1)
        node.parent = tree.root_node
        node.value = None
        node.host_value = [1, 2, 3, 4]
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)

        submission = backend.submit(
            resolved(
                CommandKind.PREFETCH_CONTEXT,
                handle,
                PhysicalPageAction.START_H2D,
            )
        )
        ack = backend.poll_acks()[0]

        self.assertEqual(submission.started_handles, ())
        self.assertEqual(ack.status, CommandStatus.REJECTED)
        self.assertEqual(
            {item.code for item in ack.blockers},
            {TransferBlockerCode.DEVICE_CAPACITY},
        )

    def test_d2h_rejects_a_gpu_node_below_an_evicted_ancestor(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        parent = _Node(1)
        parent.parent = tree.root_node
        parent.value = None
        parent.host_value = [1]
        child = _Node(2)
        child.parent = parent
        parent.children["child"] = child
        handle = registry.register(child)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)
        command = resolved(
            CommandKind.OFFLOAD_CONTEXT,
            handle,
            PhysicalPageAction.START_D2H,
        )

        submission = backend.submit(command)
        ack = backend.poll_acks()[0]

        self.assertEqual(submission.started_handles, ())
        self.assertEqual(ack.status.value, "rejected")
        self.assertIn("evicted Radix ancestor", ack.reason)
        self.assertEqual(
            {item.code for item in ack.blockers},
            {TransferBlockerCode.ANCESTOR_CLOSURE},
        )
        self.assertFalse(child.backuped)

    def test_d2h_completion_refuses_to_evict_below_gpu_descendant(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        parent = _Node(1)
        parent.parent = tree.root_node
        child = _Node(2)
        child.parent = parent
        parent.children["child"] = child
        handle = registry.register(parent)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)
        command = resolved(
            CommandKind.OFFLOAD_CONTEXT,
            handle,
            PhysicalPageAction.START_D2H,
        )

        submission = backend.submit(command)
        ack = backend.poll_acks()[0]
        telemetry = backend.poll_transfer_telemetry()[0]

        self.assertEqual(submission.started_handles, (handle,))
        self.assertEqual(ack.status.value, "rejected")
        self.assertEqual(ack.actual_bytes, 0)
        self.assertEqual(telemetry.actual_bytes, 400)
        self.assertEqual(telemetry.status.value, "rejected")
        self.assertIn("descendants off device", ack.reason)
        self.assertEqual(
            {item.code for item in ack.blockers},
            {TransferBlockerCode.DESCENDANT_CLOSURE},
        )
        self.assertFalse(parent.evicted)
        self.assertTrue(parent.backuped)

    def test_drop_dual_clean_node_keeps_the_cpu_radix_extent(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        node = _Node(1)
        node.parent = tree.root_node
        node.host_value = [10, 11, 12, 13]
        tree.root_node.children["node"] = node
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)
        command = resolved(CommandKind.DROP_UNOWNED, handle, PhysicalPageAction.DROP)

        submission = backend.submit(command)
        ack = backend.poll_acks()[0]

        self.assertEqual(submission.started_handles, (handle,))
        self.assertEqual(ack.status.value, "completed")
        self.assertTrue(node.evicted)
        self.assertTrue(node.backuped)
        self.assertIs(tree.root_node.children["node"], node)

    def test_drop_host_copy_keeps_dual_clean_gpu_extent(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        node = _Node(1)
        node.parent = tree.root_node
        node.host_value = [10, 11, 12, 13]
        tree.root_node.children["node"] = node
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)
        command = resolved(
            CommandKind.DROP_TERMINAL_PRIVATE,
            handle,
            PhysicalPageAction.DROP_HOST,
        )

        submission = backend.submit(command)
        ack = backend.poll_acks()[0]

        self.assertEqual(submission.started_handles, (handle,))
        self.assertEqual(ack.status, CommandStatus.COMPLETED)
        self.assertFalse(node.evicted)
        self.assertFalse(node.backuped)
        self.assertIs(tree.root_node.children["node"], node)

    def test_drop_host_only_extent_removes_cpu_radix_leaf(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        node = _Node(1)
        node.parent = tree.root_node
        node.value = None
        node.host_value = [10, 11, 12, 13]
        tree.root_node.children["node"] = node
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)
        command = resolved(
            CommandKind.DROP_TERMINAL_PRIVATE,
            handle,
            PhysicalPageAction.DROP_HOST,
        )

        submission = backend.submit(command)
        ack = backend.poll_acks()[0]

        self.assertEqual(submission.started_handles, (handle,))
        self.assertEqual(ack.status, CommandStatus.COMPLETED)
        self.assertNotIn("node", tree.root_node.children)
        self.assertFalse(node.backuped)

    def test_cache_reset_aborts_pending_backend_commands(self):
        tree = _TreeCache()
        registry = SGLangNodeRegistry()
        node = _Node()
        handle = registry.register(node)
        backend = HiCacheNodeCommandBackend(tree, registry, now_ms=lambda: 2)
        command = resolved(
            CommandKind.SHADOW_CONTEXT,
            handle,
            PhysicalPageAction.START_D2H,
        )
        backend.submit(command)
        acks = backend.abort_all(reason="cache_reset")
        self.assertEqual(acks[0].status.value, "cancelled")
        self.assertEqual(acks[0].actual_bytes, 0)
        self.assertEqual(backend.poll_acks(), [])

    def test_abort_bridge_drops_visible_side_state_only(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = BeliefKVController()
        runtime.controller.process_runtime_events(
            (
                RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
                RuntimeEvent(
                    "create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        runtime.controller.register_visible_request(
            AdmissionRequest(
                "req-visible", "wf", "inv", "ctx", 0, 2.0, 1, 1, 16
            )
        )
        runtime._request_metadata_by_id = {
            "req-visible": BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        }
        runtime._request_submitted_ts_by_id = {"req-visible": 2.0}

        removed = runtime.on_abort_request(_AbortRequest(rid="req-"))
        self.assertEqual(removed, 1)
        self.assertIsNone(
            runtime.controller.visible_admission.get("req-visible")
        )
        self.assertEqual(runtime._request_metadata_by_id, {})

    def test_return_cancels_a_visible_request_before_next_prefill_epoch(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig()
        runtime.controller = BeliefKVController(runtime.config)
        runtime.audit = _AuditRecorder()
        runtime.event_log = _EventBatchRecorder()
        runtime._now_ms = lambda: 30.0
        runtime._active_request_ids = set()
        runtime._terminal_cancelled_request_ids = set()
        runtime._request_metadata_by_id = {}
        scheduler = _AbortScheduler()
        scheduler.runtime = runtime
        runtime.scheduler = scheduler
        runtime._process_events(
            (
                RuntimeEvent(
                    "start", 10.0, RuntimeEventKind.WORKFLOW_START, "wf"
                ),
                RuntimeEvent(
                    "create",
                    11.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(rid="request", beliefkv_metadata=metadata)
        runtime.controller.register_visible_request(
            AdmissionRequest(
                request_id="request",
                workflow_id="wf",
                invocation_id="inv",
                context_id="ctx",
                context_epoch=0,
                submitted_ts_ms=12.0,
                uncached_prompt_tokens=1,
                expected_output_tokens=1,
                kv_bytes_per_token=16,
            )
        )
        runtime._request_metadata_by_id[request.rid] = metadata

        runtime._process_events(
            (
                RuntimeEvent(
                    "return",
                    20.0,
                    RuntimeEventKind.RETURN,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                ),
            )
        )

        self.assertIsNone(runtime.controller.visible_admission.get("request"))
        self.assertEqual([item.rid for item in scheduler.requests], ["request"])
        cancelled = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "terminal_request_cancelled"
        ]
        self.assertEqual(cancelled[0]["phase"], "visible_pending")

    def test_request_arriving_after_return_is_rejected_without_admission(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = BeliefKVController()
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 30.0
        runtime._request_metadata_by_id = {}
        runtime.scheduler = SimpleNamespace(send_to_tokenizer=_Sender())
        runtime.controller.process_runtime_events(
            (
                RuntimeEvent(
                    "start", 10.0, RuntimeEventKind.WORKFLOW_START, "wf"
                ),
                RuntimeEvent(
                    "create",
                    11.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
                RuntimeEvent(
                    "return",
                    12.0,
                    RuntimeEventKind.RETURN,
                    "wf",
                    invocation_id="inv",
                ),
            )
        )
        request = SimpleNamespace(
            rid="late-request",
            beliefkv_metadata=BeliefKVRequestMetadata("wf", "inv", "ctx", 0),
        )

        self.assertFalse(runtime.register_visible_request(request))
        self.assertEqual(len(runtime.controller.visible_admission.entries()), 0)
        self.assertEqual(
            runtime.scheduler.send_to_tokenizer.messages[0].rid,
            "late-request",
        )
        self.assertEqual(runtime.audit.events[0][0], "terminal_request_rejected")

    def test_return_marks_an_engine_owned_request_terminal(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig()
        runtime.controller = BeliefKVController(runtime.config)
        runtime.audit = _AuditRecorder()
        runtime.event_log = _EventBatchRecorder()
        runtime._now_ms = lambda: 30.0
        runtime._active_request_ids = set()
        runtime._terminal_cancelled_request_ids = set()
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        runtime._request_metadata_by_id = {"request": metadata}
        scheduler = _AbortScheduler()
        scheduler.runtime = runtime
        runtime.scheduler = scheduler
        runtime._process_events(
            (
                RuntimeEvent(
                    "start", 10.0, RuntimeEventKind.WORKFLOW_START, "wf"
                ),
                RuntimeEvent(
                    "create",
                    11.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
                RuntimeEvent(
                    "return",
                    20.0,
                    RuntimeEventKind.RETURN,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                ),
            )
        )

        self.assertEqual(runtime._terminal_cancelled_request_ids, {"request"})
        cancelled = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "terminal_request_cancelled"
        ]
        self.assertEqual(cancelled[0]["phase"], "engine_owned")

    def test_cache_finish_suppresses_a_late_terminal_llm_result(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(
            rid="request",
            beliefkv_metadata=metadata,
            output_ids=[1, 2],
        )
        runtime._ensure_allocator_radix_consistency = lambda **_kwargs: None
        runtime._match_terminal_node = lambda _token_ids: None
        runtime._metadata_scope_is_terminal = lambda _metadata: True
        runtime._tree_dirty = False
        runtime._active_request_ids = {"request"}
        runtime._terminal_cancelled_request_ids = set()
        runtime._request_metadata_by_id = {"request": metadata}
        runtime._request_submitted_ts_by_id = {"request": 1.0}
        runtime._request_physical_start_by_id = {"request": {}}
        runtime._pending_request_physical_finish_by_id = {"request": {}}
        runtime._terminal_node_by_context = {}
        runtime.controller = BeliefKVController()
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 30.0
        runtime._emit = lambda *_args, **_kwargs: self.fail(
            "a terminal cache callback must not emit LLM_RESULT"
        )

        runtime.on_cache_finished(request, [1, 2, 3])

        self.assertNotIn("request", runtime._active_request_ids)
        self.assertNotIn("request", runtime._request_metadata_by_id)
        terminal = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "terminal_request_abort_finished"
        ]
        self.assertEqual(len(terminal), 1)
        self.assertFalse(terminal[0]["terminal_marker"])
        self.assertTrue(terminal[0]["logical_scope_terminal"])

    def test_dynamic_working_set_uses_native_reclaimable_pressure(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=1000,
            reserve_hbm_bytes=0,
            kv_bytes_per_token=10,
            dynamic_working_set_enabled=True,
        )
        controller = BeliefKVController(config)
        controller.report_hbm_usage(1000)
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        captured = {}

        def decide(_candidates, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                active_workflow_ids=(),
                mode="steady",
                hbm_pressure=(
                    kwargs["hbm_used_bytes"] / kwargs["hbm_capacity_bytes"]
                ),
                gross_kv_pressure=(
                    kwargs["gross_hbm_used_bytes"]
                    / kwargs["hbm_capacity_bytes"]
                ),
                target_running_requests=32,
                native_running_requests=kwargs["native_running_requests"],
                admission_slots=32,
                target_ready_requests=0,
                selected_ready_requests=0,
                pressure_actions_enabled=False,
                epoch=kwargs["epoch"],
            )

        runtime._dynamic_working_set_scheduler = lambda: SimpleNamespace(
            decide=decide
        )

        decision = runtime._dynamic_working_set_for_tagged(
            [],
            entries={},
            fair_order=[],
            frontier_candidates={},
            now_ms=100.0,
            native_request_slots=32,
            native_available_hbm_bytes=800,
        )

        self.assertEqual(controller.actual_hbm_used_bytes, 1000)
        self.assertEqual(captured["hbm_used_bytes"], 200)
        self.assertEqual(runtime._current_native_available_hbm_bytes, 800)
        self.assertEqual(runtime._current_effective_hbm_used_bytes, 200)
        self.assertAlmostEqual(decision.hbm_pressure, 0.2)
        self.assertFalse(runtime._pressure_actions_enabled())
        observation = RuntimeResourceObservation(
            ts_ms=100.0,
            hbm_capacity_bytes=1000,
            hbm_used_bytes=1000,
            host_capacity_bytes=1000,
            host_used_bytes=0,
            host_free_bytes=1000,
        )
        self.assertEqual(
            runtime._joint_shadow_effective_hbm_used_bytes(observation), 200
        )
        effective_observation = runtime._joint_shadow_effective_observation(
            observation
        )
        self.assertEqual(effective_observation.hbm_used_bytes, 1000)
        self.assertEqual(effective_observation.effective_hbm_used_bytes, 200)
        self.assertEqual(effective_observation.policy_hbm_used_bytes, 200)
        self.assertEqual(effective_observation.hbm_capacity_bytes, 1000)
        self.assertEqual(observation.hbm_used_bytes, 1000)
        self.assertFalse(
            runtime._joint_shadow_causal_event_requires_full_plan(
                critical_event_pending=True, pressure_now=False
            )
        )
        self.assertTrue(
            runtime._joint_shadow_causal_event_requires_full_plan(
                critical_event_pending=True, pressure_now=True
            )
        )
        runtime.predictive_risk_worker = object()
        self.assertFalse(
            runtime._joint_shadow_causal_event_requires_full_plan(
                critical_event_pending=True, pressure_now=False
            )
        )
        changed = next(
            fields
            for event, _, fields in runtime.audit.events
            if event == "dynamic_working_set_changed"
        )
        self.assertEqual(changed["gross_hbm_used_bytes"], 1000)
        self.assertEqual(changed["effective_hbm_used_bytes"], 200)

    def test_ticket_epoch_does_not_mutate_native_queue_or_cap_workflow(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=1000,
            reserve_hbm_bytes=100,
            joint_policy_enabled=True,
            joint_workflow_active_window=2,
            dynamic_working_set_enabled=True,
            dynamic_working_set_min_hold_epochs=0,
        )
        controller = BeliefKVController(config)
        for workflow_id in ("wf-a", "wf-b"):
            controller.process_runtime_event(
                RuntimeEvent(
                    event_id=f"{workflow_id}-start",
                    ts_ms=0,
                    kind=RuntimeEventKind.WORKFLOW_START,
                    workflow_id=workflow_id,
                )
            )
            for suffix in ("1", "2"):
                controller.process_runtime_event(
                    RuntimeEvent(
                        event_id=f"{workflow_id}-{suffix}",
                        ts_ms=float(suffix),
                        kind=RuntimeEventKind.INVOCATION_CREATE,
                        workflow_id=workflow_id,
                        invocation_id=f"{workflow_id}-inv-{suffix}",
                        context_id=f"{workflow_id}-ctx-{suffix}",
                        context_epoch=0,
                    )
                )
        controller.fairness.charge_service("wf-a", 100)
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = controller
        runtime.config = controller.config
        runtime._now_ms = lambda: 10.0
        runtime.audit = _AuditRecorder()
        runtime._admission_epoch = 0
        runtime._current_ticket_epoch = None
        runtime._current_tickets_by_request = {}
        runtime._ticket_attempted_request_ids = set()
        runtime._ticket_selected_request_ids = set()
        runtime._ticket_skip_audit = set()
        runtime._ticket_selection_details = {}
        runtime._ticket_native_rejections = {}
        runtime._pending_h2d_contexts = set()
        runtime._request_metadata_by_id = {}
        runtime._request_submitted_ts_by_id = {}
        runtime._online_joint_counts = Counter()
        runtime._online_joint_epoch_sequence = 0
        untagged = SimpleNamespace(rid="untagged", beliefkv_metadata=None)

        def req(workflow_id, suffix):
            metadata = BeliefKVRequestMetadata(
                workflow_id,
                f"{workflow_id}-inv-{suffix}",
                f"{workflow_id}-ctx-{suffix}",
                0,
            )
            request = SimpleNamespace(
                rid=f"{workflow_id}-{suffix}",
                beliefkv_metadata=metadata,
                origin_input_ids=_NoBooleanSequence(1),
                prefix_indices=_NoBooleanSequence(0),
            )
            runtime._request_metadata_by_id[request.rid] = metadata
            runtime._request_submitted_ts_by_id[request.rid] = float(suffix)
            controller.register_visible_request(
                AdmissionRequest(
                    request.rid,
                    workflow_id,
                    metadata.invocation_id,
                    metadata.context_id,
                    0,
                    float(suffix),
                    1,
                    1,
                    10,
                )
            )
            return request

        queue = [untagged, req("wf-a", "1"), req("wf-a", "2"), req("wf-b", "1"), req("wf-b", "2")]
        request_ids = tuple(item.rid for item in queue if item is not untagged)
        semantic = compile_bounded_seed_epoch(
            ordered_request_ids=(request_ids[0],),
            visible_request_ids=request_ids,
            epoch_sequence=1,
        )
        runtime._current_joint_plan_epoch = semantic.epoch
        runtime._online_joint_admission_decision = lambda **_kwargs: semantic
        original = list(queue)
        candidate_view = runtime.begin_prefill_epoch(
            queue,
            SimpleNamespace(
                rem_input_tokens=100,
                rem_chunk_tokens=None,
                rem_total_tokens=100,
            ),
            max_requests=4,
        )
        self.assertEqual(queue, original)
        self.assertIs(candidate_view[0], untagged)
        self.assertEqual(len(runtime._current_ticket_epoch.tickets), 4)
        self.assertEqual(
            runtime._current_online_joint_view.plan_id,
            semantic.view.plan_id,
        )
        self.assertEqual(
            runtime._current_ticket_epoch.source,
            "joint_epoch+dynamic_batch_fill",
        )
        self.assertEqual(
            Counter(
                ticket.workflow_id
                for ticket in runtime._current_ticket_epoch.tickets
            ),
            {"wf-a": 2, "wf-b": 2},
        )
        selected = []
        for request in candidate_view:
            if request is untagged:
                continue
            self.assertTrue(runtime.admission_ticket_allows(request))
            self.assertTrue(
                runtime.validate_admission_ticket_after_prefix(request)
            )
            runtime.on_prefill_candidate_result(
                request, admitted=True, result="CONTINUE"
            )
            selected.append(request)
        runtime.end_prefill_epoch(selected)
        self.assertEqual(len(controller.visible_admission.entries()), 0)

    def test_admission_bundle_generation_ignores_runtime_lock_and_owner_churn(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = BeliefKVController()
        page_index = runtime.controller.page_index
        handle = PageHandle(7, 2)
        page_index.register_context("ctx-a", "wf-a", 0)
        page_index.register_page(handle, size_bytes=100)
        page_index.bind_pages("ctx-a", 0, (handle,))
        before = runtime._context_bundle_generations("ctx-a")

        page_index.set_engine_lock(handle, 2)
        page_index.set_active_readers(handle, 1)
        page_index.register_context("ctx-b", "wf-b", 0)
        page_index.bind_pages("ctx-b", 0, (handle,))

        self.assertEqual(runtime._context_bundle_generations("ctx-a"), before)

    def test_after_prefix_validation_accepts_a_smaller_uncached_suffix(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.audit = _AuditRecorder()
        runtime._admission_epoch = 4
        runtime._ticket_skip_audit = set()
        runtime._ticket_selection_details = {}
        runtime._current_ticket_hbm_budget_bytes = 1_000
        runtime._current_ticket_prefill_budget_tokens = 10
        runtime._current_ticket_rematched_hbm_bytes = {}
        runtime._current_ticket_rematched_prefill_tokens = {}
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(
            rid="request",
            beliefkv_metadata=metadata,
            origin_input_ids=_NoBooleanSequence(6),
            prefix_indices=_NoBooleanSequence(3),
        )
        runtime.controller.visible_admission.register(
            AdmissionRequest(
                "request", "wf", "inv", "ctx", 0, 0.0, 6, 1, 10
            )
        )
        epoch = runtime.controller.admission_ticket_compiler.compile(
            epoch=4,
            now_ms=1.0,
            ordered_request_ids=("request",),
            entries={
                "request": runtime.controller.visible_admission.get("request")
            },
            budget=AdmissionCompileBudget(
                max_prefill_tokens=10,
                max_requests=1,
                max_candidates=1,
                available_hbm_bytes=1_000,
            ),
            source="test",
            reason="prefix_rematch",
        )
        runtime._current_ticket_epoch = epoch
        runtime._current_tickets_by_request = dict(epoch.by_request_id)

        self.assertTrue(runtime.admission_ticket_allows(request))
        self.assertTrue(runtime.validate_admission_ticket_after_prefix(request))
        self.assertEqual(
            runtime.controller.visible_admission.get(
                "request"
            ).request.uncached_prompt_tokens,
            3,
        )
        self.assertEqual(
            runtime._current_ticket_rematched_prefill_tokens,
            {"request": 3},
        )

    def test_after_prefix_validation_recertifies_larger_suffix_within_budget(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            kv_bytes_per_token=10,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 1_000.0
        runtime._admission_epoch = 4
        runtime._ticket_skip_audit = set()
        runtime._ticket_selection_details = {}
        runtime._current_ticket_hbm_budget_bytes = 1_000
        runtime._current_ticket_prefill_budget_tokens = 10
        runtime._current_ticket_rematched_hbm_bytes = {}
        runtime._current_ticket_rematched_prefill_tokens = {}
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(
            rid="request",
            beliefkv_metadata=metadata,
            origin_input_ids=_NoBooleanSequence(6),
            prefix_indices=_NoBooleanSequence(1),
        )
        runtime.controller.visible_admission.register(
            AdmissionRequest(
                "request", "wf", "inv", "ctx", 0, 0.0, 1, 1, 10
            )
        )
        epoch = runtime.controller.admission_ticket_compiler.compile(
            epoch=4,
            now_ms=1.0,
            ordered_request_ids=("request",),
            entries={
                "request": runtime.controller.visible_admission.get("request")
            },
            budget=AdmissionCompileBudget(
                max_prefill_tokens=10,
                max_requests=1,
                max_candidates=1,
                available_hbm_bytes=1_000,
            ),
            source="test",
            reason="prefix_rematch",
        )
        original_ticket = epoch.tickets[0]
        runtime._current_ticket_epoch = epoch
        runtime._current_tickets_by_request = dict(epoch.by_request_id)

        self.assertTrue(runtime.admission_ticket_allows(request))
        self.assertTrue(runtime.validate_admission_ticket_after_prefix(request))

        refreshed = runtime.controller.visible_admission.get("request")
        recertified = runtime._current_tickets_by_request["request"]
        self.assertEqual(refreshed.request.uncached_prompt_tokens, 5)
        self.assertEqual(recertified.estimated_prefill_tokens, 5)
        self.assertEqual(recertified.version, refreshed.version)
        self.assertNotEqual(recertified.version, original_ticket.version)
        self.assertTrue(
            any(
                event == "admission_ticket_recertified_after_prefix"
                for event, _, _ in runtime.audit.events
            )
        )

    def test_after_prefix_growth_updates_demand_and_emits_hbm_requirement(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            kv_bytes_per_token=10,
            admission_force_progress_timeout_ms=10_000.0,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 1_000.0
        runtime._admission_epoch = 4
        runtime._ticket_skip_audit = set()
        runtime._ticket_selection_details = {}
        runtime._current_ticket_hbm_budget_bytes = 50
        runtime._current_ticket_prefill_budget_tokens = 10
        runtime._current_ticket_rematched_hbm_bytes = {}
        runtime._current_ticket_rematched_prefill_tokens = {}
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(
            rid="request",
            beliefkv_metadata=metadata,
            origin_input_ids=_NoBooleanSequence(6),
            prefix_indices=_NoBooleanSequence(1),
        )
        runtime.controller.visible_admission.register(
            AdmissionRequest(
                "request", "wf", "inv", "ctx", 0, 0.0, 1, 1, 10
            )
        )
        epoch = runtime.controller.admission_ticket_compiler.compile(
            epoch=4,
            now_ms=1.0,
            ordered_request_ids=("request",),
            entries={
                "request": runtime.controller.visible_admission.get("request")
            },
            budget=AdmissionCompileBudget(
                max_prefill_tokens=10,
                max_requests=1,
                max_candidates=1,
                available_hbm_bytes=1_000,
            ),
            source="test",
            reason="prefix_rematch",
        )
        runtime._current_ticket_epoch = epoch
        runtime._current_tickets_by_request = dict(epoch.by_request_id)

        self.assertFalse(runtime.validate_admission_ticket_after_prefix(request))

        refreshed = runtime.controller.visible_admission.get("request")
        requirement = runtime._reclaim_requirements["request"]
        self.assertEqual(refreshed.request.uncached_prompt_tokens, 5)
        self.assertEqual(
            requirement.skip_reason,
            "prefix_rematch_bounded_hbm_budget",
        )
        self.assertEqual(
            runtime._current_tickets_by_request["request"].estimated_prefill_tokens,
            1,
        )
        self.assertTrue(
            any(
                event == "admission_prefix_rematch_recertification_deferred"
                and fields["reason"] == "batch_hbm_certificate"
                for event, _, fields in runtime.audit.events
            )
        )

    def test_prefix_growth_batch_makes_progress_and_recompiles_deferred_demand(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            reserve_hbm_bytes=0,
            kv_bytes_per_token=10,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 1_000.0
        runtime._admission_epoch = 4
        runtime._ticket_skip_audit = set()
        runtime._ticket_selection_details = {}
        runtime._current_ticket_hbm_budget_bytes = 2_000
        runtime._current_ticket_prefill_budget_tokens = 6
        runtime._current_ticket_rematched_hbm_bytes = {}
        runtime._current_ticket_rematched_prefill_tokens = {}
        entries = {}
        requests = {}
        for request_id in ("a", "b"):
            metadata = BeliefKVRequestMetadata(
                f"wf-{request_id}",
                f"inv-{request_id}",
                f"ctx-{request_id}",
                0,
            )
            requests[request_id] = SimpleNamespace(
                rid=request_id,
                beliefkv_metadata=metadata,
                origin_input_ids=_NoBooleanSequence(6),
                prefix_indices=_NoBooleanSequence(1),
            )
            entries[request_id] = runtime.controller.visible_admission.register(
                AdmissionRequest(
                    request_id,
                    f"wf-{request_id}",
                    f"inv-{request_id}",
                    f"ctx-{request_id}",
                    0,
                    0.0,
                    1,
                    1,
                    10,
                )
            )
        epoch = runtime.controller.admission_ticket_compiler.compile(
            epoch=4,
            now_ms=1.0,
            ordered_request_ids=("a", "b"),
            entries=entries,
            budget=AdmissionCompileBudget(
                max_prefill_tokens=10,
                max_requests=2,
                max_candidates=2,
                available_hbm_bytes=2_000,
            ),
            source="test",
            reason="prefix_rematch",
        )
        runtime._current_ticket_epoch = epoch
        runtime._current_tickets_by_request = dict(epoch.by_request_id)

        self.assertTrue(
            runtime.validate_admission_ticket_after_prefix(requests["a"])
        )
        self.assertFalse(
            runtime.validate_admission_ticket_after_prefix(requests["b"])
        )
        self.assertEqual(
            runtime._current_ticket_rematched_prefill_tokens,
            {"a": 5},
        )
        self.assertEqual(
            runtime.controller.visible_admission.get(
                "b"
            ).request.uncached_prompt_tokens,
            5,
        )
        self.assertNotIn("b", getattr(runtime, "_reclaim_requirements", {}))
        self.assertTrue(
            any(
                event == "admission_prefix_rematch_recertification_deferred"
                and fields["reason"] == "batch_prefill_certificate"
                for event, _, fields in runtime.audit.events
            )
        )

        runtime.controller.visible_admission.cancel("a")
        next_entry = runtime.controller.visible_admission.get("b")
        next_epoch = runtime.controller.admission_ticket_compiler.compile(
            epoch=5,
            now_ms=1_001.0,
            ordered_request_ids=("b",),
            entries={"b": next_entry},
            budget=AdmissionCompileBudget(
                max_prefill_tokens=6,
                max_requests=1,
                max_candidates=1,
                available_hbm_bytes=2_000,
            ),
            source="test",
            reason="prefix_rematch_recompile",
        )
        self.assertEqual(len(next_epoch.tickets), 1)
        self.assertEqual(next_epoch.tickets[0].estimated_prefill_tokens, 5)

    def test_admission_rescue_is_single_bounded_and_allocator_backed(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            kv_bytes_per_token=10,
            admission_force_progress_timeout_ms=1_000.0,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.audit = _AuditRecorder()
        runtime._online_joint_counts = Counter()
        runtime._active_admission_rescue = None
        runtime._request_metadata_by_id = {
            "blocked": BeliefKVRequestMetadata("wf", "inv", "ctx", 0),
            "other": BeliefKVRequestMetadata("wf-2", "inv-2", "ctx-2", 0),
        }
        runtime.controller.visible_admission.register(
            AdmissionRequest(
                "blocked", "wf", "inv", "ctx", 0, 0.0, 20, 8, 10
            )
        )
        runtime.controller.visible_admission.register(
            AdmissionRequest(
                "other", "wf-2", "inv-2", "ctx-2", 0, 0.0, 20, 8, 10
            )
        )
        allocator = _Allocator(100)
        runtime.scheduler = SimpleNamespace(
            token_to_kv_pool_allocator=allocator
        )
        requirement = ReclaimRequirement(
            beneficiary_request_id="blocked",
            required_startup_bytes=20,
            required_growth_bytes=280,
            current_prefix_bytes=500,
            waited_ms=1_001.0,
            skip_reason="bounded_hbm_budget",
        )

        runtime._maybe_start_admission_rescue(requirement, now_ms=1_001.0)
        runtime._maybe_start_admission_rescue(
            replace(requirement, beneficiary_request_id="other"),
            now_ms=1_002.0,
        )

        rescue = runtime._active_admission_rescue
        self.assertIsNotNone(rescue)
        self.assertEqual(rescue.request_id, "blocked")
        runtime._reserve_admission_rescue_capacity(
            request_id="blocked",
            reclaimed_bytes=300,
            now_ms=1_003.0,
        )
        self.assertEqual(runtime._admission_rescue_credit_bytes(), {"blocked": 300})
        self.assertEqual(runtime.allocator_backed_reservation_tokens(), 30)
        released = runtime._release_admission_rescue_capacity(
            rescue,
            now_ms=1_004.0,
            reason="native_admission_attempt",
        )
        rescue.released_for_admission_tokens = released
        self.assertEqual(released, 30)
        self.assertTrue(
            runtime._reacquire_admission_rescue_capacity(
                rescue, tokens=released, now_ms=1_005.0
            )
        )
        runtime._finish_admission_rescue(
            "blocked", now_ms=1_006.0, reason="gpu_service_completed", success=True
        )
        self.assertIsNone(runtime._active_admission_rescue)
        self.assertEqual(allocator.available_size(), 100)

    def test_joint_active_window_defers_but_keeps_inactive_workflow_visible(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            joint_policy_enabled=True,
            joint_workflow_active_window=1,
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = BeliefKVController(config)
        runtime.controller.fairness.register("wf-a")
        runtime.controller.fairness.register("wf-b")
        runtime.controller.fairness.charge_service("wf-a", 100.0)
        requests = (
            RunnableInvocation(
                request_id="request-a",
                workflow_id="wf-a",
                invocation_id="inv-a",
                context_id="ctx-a",
                context_epoch=0,
                submitted_ts_ms=1.0,
                startup_bytes=10,
            ),
            RunnableInvocation(
                request_id="request-b",
                workflow_id="wf-b",
                invocation_id="inv-b",
                context_id="ctx-b",
                context_epoch=0,
                submitted_ts_ms=1.0,
                startup_bytes=10,
            ),
        )
        runtime._policy_runtime_runnable = lambda _now_ms: requests
        runtime._current_online_joint_decision = None
        runtime._current_joint_plan_epoch = None
        runtime._online_joint_epoch_sequence = 0
        runtime._online_joint_counts = Counter()

        decision = runtime._safe_point_seed_decision(now_ms=10.0)

        self.assertEqual(decision.view.ordered_request_ids, ("request-b",))
        self.assertEqual(decision.view.deferred_request_ids, ("request-a",))
        self.assertEqual(
            set(runtime.controller.fairness.accounts), {"wf-a", "wf-b"}
        )

    def test_empty_predictive_overlay_skips_safe_point_budget_accounting(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            joint_policy_enabled=True,
            predictor_model_path="/tmp/frontier.json",
            gpu_service_model_path="/tmp/service.json",
            predictive_risk_shadow_enabled=True,
            predictive_joint_overlay_enabled=True,
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = BeliefKVController(
            BeliefKVConfig(
                hbm_capacity_bytes=1_000,
                reserve_hbm_bytes=0,
                predictor_enabled=False,
            )
        )
        runtime._policy_runtime_runnable = lambda _now_ms: ()
        runtime._current_online_joint_decision = None
        runtime._current_joint_plan_epoch = None
        runtime._online_joint_epoch_sequence = 0
        runtime._online_joint_counts = Counter()
        runtime._joint_predictive_counts = Counter()
        runtime._joint_shadow_timing_samples = {}
        runtime._latest_predictive_intent = None

        decision = runtime._safe_point_seed_decision(now_ms=10.0)

        self.assertIsNotNone(decision.view)
        self.assertNotIn(
            "predictive_safe_point_commit_ms",
            runtime._joint_shadow_timing_samples,
        )
        self.assertFalse(runtime._joint_predictive_counts)

    def test_predictive_prefetch_waits_for_latest_start_before_materialization(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            host_capacity_bytes=2_000,
            reserve_hbm_bytes=0,
            joint_policy_enabled=True,
            predictor_model_path="/tmp/frontier.json",
            gpu_service_model_path="/tmp/service.json",
            predictive_risk_shadow_enabled=True,
            predictive_joint_overlay_enabled=True,
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = BeliefKVController(
            BeliefKVConfig(
                hbm_capacity_bytes=1_000,
                host_capacity_bytes=2_000,
                reserve_hbm_bytes=0,
                predictor_enabled=False,
            )
        )
        request = RunnableInvocation(
            request_id="request",
            workflow_id="workflow",
            invocation_id="invocation",
            context_id="context",
            context_epoch=1,
            submitted_ts_ms=1.0,
            startup_bytes=10,
        )
        runtime._policy_runtime_runnable = lambda _now_ms: (request,)
        runtime._refresh_bounded_seed_frontier_predictions = (
            lambda _view, runnable, **_kwargs: runnable
        )
        runtime._observed_seed_beneficiary_hints = lambda *_args, **_kwargs: ()
        runtime._current_online_joint_decision = None
        runtime._current_joint_plan_epoch = None
        runtime._online_joint_epoch_sequence = 0
        runtime._online_joint_counts = Counter()
        runtime._joint_predictive_counts = Counter()
        runtime._joint_shadow_timing_samples = {}
        runtime._latest_predictive_intent = SimpleNamespace(intent_id="intent")
        runtime._current_predictive_residency_commit = None
        runtime._predictive_prefetch_retry_not_before_ms = 1_000.0
        runtime._physical_commit_predictive_intent = mock.Mock(
            side_effect=lambda _plan, decision, **_kwargs: decision
        )

        runtime._safe_point_seed_decision(now_ms=100.0)

        runtime._physical_commit_predictive_intent.assert_not_called()
        self.assertEqual(
            runtime._predictive_prefetch_retry_not_before_ms,
            1_000.0,
        )

        runtime._safe_point_seed_decision(now_ms=1_000.0)

        runtime._physical_commit_predictive_intent.assert_called_once()

    def test_predictive_schedule_atomically_reorders_and_admits(self):
        controller = BeliefKVController(
            BeliefKVConfig(
                hbm_capacity_bytes=2_000,
                host_capacity_bytes=4_000,
                reserve_hbm_bytes=0,
                predictor_enabled=False,
            )
        )
        controller.process_runtime_events(
            (
                RuntimeEvent(
                    "wf-schedule",
                    1.0,
                    RuntimeEventKind.WORKFLOW_START,
                    "wf-schedule",
                ),
                RuntimeEvent(
                    "inv-a",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf-schedule",
                    invocation_id="inv-a",
                    context_id="ctx-a",
                    context_epoch=0,
                ),
                RuntimeEvent(
                    "inv-b",
                    3.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf-schedule",
                    invocation_id="inv-b",
                    context_id="ctx-b",
                    context_epoch=0,
                ),
            )
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            predictor_model_path="/tmp/frontier.json",
            gpu_service_model_path="/tmp/service.json",
            joint_policy_enabled=True,
            predictive_risk_shadow_enabled=True,
            predictive_joint_overlay_enabled=True,
        )
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        runtime._joint_predictive_counts = Counter()
        runtime._current_predictive_residency_commit = None
        runtime._restore_service_grace_by_request = {}
        runtime._last_frontier_model_version = "frontier-v1"
        runtime._reclaim_requirements = {}
        request_a = RunnableInvocation(
            request_id="request-a",
            workflow_id="wf-schedule",
            invocation_id="inv-a",
            context_id="ctx-a",
            context_epoch=0,
            submitted_ts_ms=1.0,
            startup_bytes=50,
            admission_startup_bytes=50,
            admission_growth_bytes=50,
            causal_class="engine_running:decode",
        )
        request_b = RunnableInvocation(
            request_id="request-b",
            workflow_id="wf-schedule",
            invocation_id="inv-b",
            context_id="ctx-b",
            context_epoch=0,
            submitted_ts_ms=2.0,
            startup_bytes=50,
            admission_startup_bytes=50,
            admission_growth_bytes=50,
            causal_class="engine_waiting:ready",
        )
        runtime._latest_predictive_intent = PredictiveIntent(
            intent_id="intent-schedule",
            source_joint_plan_id="source-plan",
            source_snapshot_id="snapshot",
            package_id="package-schedule",
            model_version="frontier-v1",
            action=PredictiveActionKind.SCHEDULE,
            invocation_id="inv-b",
            expected_invocation_state=(
                controller.graph.invocations["inv-b"].state.value
            ),
            context_id="ctx-b",
            context_epoch=0,
            generated_ts_ms=100.0,
            remaining_window_low_ms=1_000.0,
            transfer_p95_ms=0.0,
            target_bytes_hint=0,
            min_reclaimable_bytes=0,
            max_cross_context_bytes=0,
            max_copy_bytes=0,
            causal_certificate=_predictive_causal_certificate(
                controller, "frontier-v1"
            ),
            required_prediction_heads=("boundary",),
            prediction_head_support=(("boundary", "exact"),),
            calibration_coverage=0.95,
            future_hbm_feasibility_probability=1.0,
            expected_benefit_ms=5.0,
            shape_fingerprint="not_applicable",
            predicted_extent_count=0,
            maximum_transfer_ms=0.0,
            maximum_stall_ms=0.0,
            morphology_slack_ms=0.0,
            causal_slack_probability=1.0,
            beneficiary_request_id="request-b",
            beneficiary_invocation_id="inv-b",
            beneficiary_context_id="ctx-b",
            beneficiary_context_epoch=0,
            beneficiary_startup_bytes=50,
            beneficiary_growth_bytes=50,
            causal_package_generation="request-b:ctx-b:c0:50:50",
            execution_order_request_ids=("request-b", "request-a"),
            admit_request_ids=("request-b",),
            execution_request_evidence=(
                ("request-b", "inv-b", "ctx-b", 0),
                ("request-a", "inv-a", "ctx-a", 0),
            ),
        )
        decision = compile_bounded_seed_epoch(
            ordered_request_ids=("request-a",),
            visible_request_ids=("request-a", "request-b"),
            epoch_sequence=1,
        )
        plan = SimpleNamespace(
            plan_id=decision.view.plan_id,
            residency=(),
            semantic_residency=(),
        )

        committed = runtime._physical_commit_predictive_intent(
            plan,
            decision,
            now_ms=110.0,
            current_runnable=(request_a, request_b),
        )

        self.assertEqual(
            committed.view.ordered_request_ids,
            ("request-b", "request-a"),
        )
        self.assertEqual(
            committed.view.immediate_request_ids,
            ("request-b", "request-a"),
        )
        self.assertEqual(committed.view.deferred_request_ids, ())
        self.assertIsNone(runtime._current_predictive_residency_commit)
        self.assertIsNone(runtime._latest_predictive_intent)
        group = committed.epoch.action_groups[-1]
        self.assertEqual(group.atomicity, ActionGroupAtomicity.ALL_OR_NOTHING)
        self.assertEqual(
            tuple(item.kind for item in group.actions),
            ("predictive_execution", "predictive_admission"),
        )
        self.assertEqual(len(group.dependency_dag), 1)
        self.assertEqual(runtime._joint_predictive_counts["schedule_committed"], 1)
        events = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "predictive_joint_package_committed"
        ]
        self.assertEqual(events[-1]["beneficiary_request_id"], "request-b")

    def test_predictive_swap_waits_for_commit_ack_before_prefetch(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            predictor_enabled=False,
        )
        controller = BeliefKVController(config)
        controller.process_runtime_events(
            (
                RuntimeEvent(
                    "wf-swap",
                    1.0,
                    RuntimeEventKind.WORKFLOW_START,
                    "wf-swap",
                ),
                RuntimeEvent(
                    "target-create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf-swap",
                    invocation_id="inv-target",
                    context_id="ctx-target",
                    context_epoch=0,
                ),
            )
        )
        target_handle = PageHandle(8801, 0)
        controller.page_index.register_page(
            target_handle,
            size_bytes=200,
            residency=PhysicalResidency.CPU_ONLY,
            radix_depth=1,
        )
        controller.page_index.bind_pages("ctx-target", 0, (target_handle,))
        preview = next(
            item
            for item in controller.arbiter.bundle_builder.previews_for_context(
                CommandKind.PREFETCH_CONTEXT,
                "ctx-target",
                0,
                now_ms=10.0,
                device_available_bytes=200,
            )
            if item.eligible
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        runtime._shutdown_state = "running"
        runtime._joint_predictive_counts = Counter()
        runtime._online_joint_counts = Counter()
        runtime._online_joint_last_residency_action = {}
        runtime._online_joint_last_context_residency_action = {}
        runtime._prefetch_service_leases = {}
        runtime._register_prefetch_service_lease = mock.Mock()
        expired = _OnlineJointResidencyTransaction(
            transaction_id="swap-expired",
            plan_id="plan-expired",
            intent_index=-1,
            source_bundle_id="predictive:intent-expired",
            action=ResidencyAction.COMMIT_CPU,
            command_id="swap-expired-commit-command",
            command_kind=CommandKind.OFFLOAD_CONTEXT,
            context_id="ctx-victim",
            context_epoch=0,
            physical_bundle_id="victim-bundle",
            created_ts_ms=9.0,
            predictive_intent_id="intent-expired",
            swap_target_preview=preview,
            swap_target_deadline_ms=10.0,
            swap_victim_reclaim_bytes=200,
        )
        self.assertFalse(
            runtime._queue_predictive_swap_target(expired, now_ms=10.0)
        )
        self.assertEqual(
            expired.failure_reason, "target_prefetch_deadline_expired"
        )
        self.assertEqual(
            runtime._joint_predictive_counts["swap_prefetch_deadline_expired"],
            1,
        )
        self.assertEqual(controller.command_queue.pending_commands(), ())
        capacity_blocked = replace(
            expired,
            transaction_id="swap-capacity-blocked",
            swap_target_deadline_ms=1_000.0,
            failure_reason=None,
        )
        controller.signals = replace(controller.signals, hbm_pressure=1.0)
        self.assertFalse(
            runtime._queue_predictive_swap_target(
                capacity_blocked, now_ms=11.0
            )
        )
        self.assertEqual(
            capacity_blocked.failure_reason,
            "target_prefetch_live_capacity_unavailable",
        )
        self.assertEqual(controller.command_queue.pending_commands(), ())
        controller.signals = replace(controller.signals, hbm_pressure=0.0)
        transaction = _OnlineJointResidencyTransaction(
            transaction_id="swap-1",
            plan_id="plan-1",
            intent_index=-1,
            source_bundle_id="predictive:intent-1",
            action=ResidencyAction.COMMIT_CPU,
            command_id="swap-1-commit-command",
            command_kind=CommandKind.OFFLOAD_CONTEXT,
            context_id="ctx-victim",
            context_epoch=0,
            physical_bundle_id="victim-bundle",
            created_ts_ms=10.0,
            predictive_intent_id="intent-1",
            beneficiary_request_id="request-target",
            beneficiary_context_id="ctx-target",
            beneficiary_context_epoch=0,
            causal_package_id="package-1",
            target_invocation_id="inv-target",
            target_reentry_context_epoch=0,
            swap_target_preview=preview,
            swap_target_deadline_ms=1_000.0,
            swap_victim_reclaim_bytes=200,
        )
        runtime._pending_online_joint_residency = transaction
        runtime._online_joint_residency_history = deque((transaction,))

        runtime._advance_online_joint_residency(
            (
                CommandAck(
                    transaction.command_id,
                    CommandStatus.COMPLETED,
                    11.0,
                    actual_bytes=200,
                ),
            ),
            now_ms=11.0,
        )

        queued = controller.command_queue.pop()
        self.assertIsNotNone(queued)
        self.assertEqual(queued.kind, CommandKind.PREFETCH_CONTEXT)
        self.assertEqual(queued.context_id, "ctx-target")
        self.assertIs(runtime._pending_online_joint_residency, transaction)
        self.assertEqual(transaction.stage, "target_prefetch_queued")
        self.assertEqual(transaction.swap_reclaimed_bytes, 200)

        runtime._advance_online_joint_residency(
            (
                CommandAck(
                    queued.command_id,
                    CommandStatus.COMPLETED,
                    12.0,
                    actual_bytes=200,
                ),
            ),
            now_ms=12.0,
        )

        self.assertIsNone(runtime._pending_online_joint_residency)
        self.assertEqual(transaction.stage, "completed")
        runtime._register_prefetch_service_lease.assert_called_once_with(
            transaction,
            now_ms=12.0,
        )

    def test_predictive_swap_rematerializes_victim_and_target_together(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=300,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            predictor_enabled=False,
        )
        runtime_config = replace(
            config,
            predictor_model_path="/tmp/frontier.json",
            gpu_service_model_path="/tmp/service.json",
            joint_policy_enabled=True,
            predictive_risk_shadow_enabled=True,
            predictive_joint_overlay_enabled=True,
            predictive_prefetch_canary_enabled=True,
        )
        controller = BeliefKVController(config)
        events = [
            RuntimeEvent(
                "wf-swap-safe-point",
                1.0,
                RuntimeEventKind.WORKFLOW_START,
                "wf-swap",
            )
        ]
        for offset, suffix in enumerate(("target", "victim")):
            events.extend(
                (
                    RuntimeEvent(
                        f"create-{suffix}",
                        2.0 + 2.0 * offset,
                        RuntimeEventKind.INVOCATION_CREATE,
                        "wf-swap",
                        invocation_id=f"inv-{suffix}",
                        context_id=f"ctx-{suffix}",
                        context_epoch=0,
                    ),
                    RuntimeEvent(
                        f"tool-{suffix}",
                        3.0 + 2.0 * offset,
                        RuntimeEventKind.TOOL_START,
                        "wf-swap",
                        invocation_id=f"inv-{suffix}",
                        context_id=f"ctx-{suffix}",
                        context_epoch=0,
                        attributes={"tool_family": "shell"},
                    ),
                )
            )
        controller.process_runtime_events(tuple(events))
        target_handle = PageHandle(8811, 0)
        victim_handle = PageHandle(8812, 0)
        controller.page_index.register_page(
            target_handle,
            size_bytes=200,
            residency=PhysicalResidency.CPU_ONLY,
            radix_depth=1,
        )
        controller.page_index.register_page(
            victim_handle,
            size_bytes=200,
            residency=PhysicalResidency.DUAL_CLEAN,
            radix_depth=1,
        )
        controller.page_index.bind_pages("ctx-target", 0, (target_handle,))
        controller.page_index.bind_pages("ctx-victim", 0, (victim_handle,))
        controller.service_curve = SimpleNamespace(
            estimate=lambda *_args, **_kwargs: SimpleNamespace(
                estimated_completion_p90_ms=10.0,
                estimated_unhidden_stall_p90_ms=0.0,
                shape_supported=True,
                source="test_curve",
            ),
            snapshot=lambda: {},
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = runtime_config
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        runtime._joint_predictive_counts = Counter()
        runtime._online_joint_counts = Counter()
        runtime._pending_online_joint_residency = None
        runtime._current_semantic_residency_commit = None
        runtime._current_predictive_residency_commit = None
        runtime._restore_service_grace_by_request = {}
        runtime._prefetch_service_leases = {}
        runtime._last_frontier_model_version = "frontier-v1"
        runtime._reclaim_requirements = {}
        beneficiary = RunnableInvocation(
            request_id="request-target",
            workflow_id="wf-swap",
            invocation_id="inv-target",
            context_id="ctx-target",
            context_epoch=0,
            submitted_ts_ms=1.0,
            startup_bytes=200,
            admission_startup_bytes=200,
            admission_growth_bytes=0,
            causal_class="engine_waiting:ready",
        )
        victim_revision = controller.page_index.context_revision("ctx-victim")
        runtime._latest_predictive_intent = PredictiveIntent(
            intent_id="intent-swap",
            source_joint_plan_id="source-plan",
            source_snapshot_id="snapshot",
            package_id="package-swap",
            model_version="frontier-v1",
            action=PredictiveActionKind.RECLAIM_AND_PREFETCH,
            invocation_id="inv-target",
            expected_invocation_state=(
                controller.graph.invocations["inv-target"].state.value
            ),
            context_id="ctx-target",
            context_epoch=0,
            generated_ts_ms=100.0,
            remaining_window_low_ms=100.0,
            transfer_p95_ms=10.0,
            target_bytes_hint=200,
            min_reclaimable_bytes=0,
            max_cross_context_bytes=0,
            max_copy_bytes=200,
            causal_certificate=_predictive_causal_certificate(
                controller, "frontier-v1"
            ),
            required_prediction_heads=("tool_wait_slack",),
            prediction_head_support=(("tool_wait_slack", "exact"),),
            calibration_coverage=0.95,
            future_hbm_feasibility_probability=1.0,
            expected_benefit_ms=5.0,
            shape_fingerprint="target-prefix",
            predicted_extent_count=1,
            maximum_transfer_ms=12.0,
            maximum_stall_ms=0.0,
            morphology_slack_ms=0.0,
            causal_slack_probability=0.95,
            beneficiary_request_id=beneficiary.request_id,
            beneficiary_invocation_id=beneficiary.invocation_id,
            beneficiary_context_id=beneficiary.context_id,
            beneficiary_context_epoch=beneficiary.context_epoch,
            beneficiary_startup_bytes=200,
            beneficiary_growth_bytes=0,
            predicted_deficit_bytes=100,
            causal_package_generation="request-target:ctx-target:c0:200:0",
            target_reentry_context_epoch=0,
            victim_invocation_id="inv-victim",
            expected_victim_invocation_state=(
                controller.graph.invocations["inv-victim"].state.value
            ),
            victim_context_id="ctx-victim",
            victim_context_epoch=0,
            victim_generation_fingerprint=(
                f"summary:ctx-victim:e0:r{victim_revision}"
            ),
            victim_reclaim_bytes=200,
        )
        decision = compile_bounded_seed_epoch(
            ordered_request_ids=(beneficiary.request_id,),
            visible_request_ids=(beneficiary.request_id,),
            epoch_sequence=1,
        )
        plan = SimpleNamespace(
            plan_id=decision.view.plan_id,
            residency=(),
            semantic_residency=(),
        )

        committed = runtime._physical_commit_predictive_intent(
            plan,
            decision,
            now_ms=110.0,
            current_runnable=(beneficiary,),
        )

        materialized = runtime._current_predictive_residency_commit
        self.assertIsNotNone(materialized)
        self.assertEqual(materialized.preview.context_id, "ctx-target")
        self.assertEqual(materialized.preview.copy_bytes, 200)
        self.assertEqual(materialized.victim_preview.context_id, "ctx-victim")
        self.assertEqual(materialized.victim_preview.copy_bytes, 0)
        group = committed.epoch.action_groups[-1]
        self.assertEqual(
            tuple(item.kind for item in group.actions),
            ("predictive_reclaim", "predictive_residency"),
        )
        self.assertEqual(
            group.atomicity, ActionGroupAtomicity.PREFIX_COMMITTABLE
        )
        self.assertEqual(group.resource_certificate.required_hbm_bytes, 200)
        self.assertEqual(group.resource_certificate.planned_reclaim_bytes, 200)

    def test_predictive_prepare_is_rematerialized_into_joint_epoch(self):
        controller_config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            predictor_enabled=False,
        )
        controller = BeliefKVController(controller_config)
        controller.process_runtime_events(
            (
                RuntimeEvent(
                    "wf-start-predictive",
                    1.0,
                    RuntimeEventKind.WORKFLOW_START,
                    "wf-predictive",
                ),
                RuntimeEvent(
                    "inv-create-predictive",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf-predictive",
                    invocation_id="inv-predictive",
                    context_id="ctx-predictive",
                    context_epoch=0,
                ),
                RuntimeEvent(
                    "tool-start-predictive",
                    3.0,
                    RuntimeEventKind.TOOL_START,
                    "wf-predictive",
                    invocation_id="inv-predictive",
                    context_id="ctx-predictive",
                    context_epoch=0,
                    attributes={"tool_family": "shell"},
                ),
            )
        )
        small_handle = PageHandle(901, 0)
        matching_handle = PageHandle(903, 0)
        controller.page_index.register_page(small_handle, size_bytes=100)
        controller.page_index.register_page(matching_handle, size_bytes=300)
        controller.page_index.bind_pages(
            "ctx-predictive",
            0,
            (small_handle, matching_handle),
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            predictor_model_path="/tmp/frontier.json",
            gpu_service_model_path="/tmp/service.json",
            joint_policy_enabled=True,
            predictive_risk_shadow_enabled=True,
            predictive_joint_overlay_enabled=True,
            predictive_prepare_host_canary_limit=1,
        )
        runtime.controller = controller
        controller.service_curve = SimpleNamespace(
            estimate=lambda *_args, **_kwargs: SimpleNamespace(
                estimated_completion_p90_ms=10.0,
                estimated_unhidden_stall_p90_ms=None,
                shape_supported=True,
                source="unified_test_curve",
            ),
            snapshot=lambda: {},
        )
        runtime.audit = _AuditRecorder()
        runtime._joint_predictive_counts = Counter()
        runtime._online_joint_counts = Counter()
        runtime._pending_online_joint_residency = None
        runtime._online_joint_residency_sequence = 0
        runtime._online_joint_residency_history = deque()
        runtime._online_joint_last_residency_action = {}
        runtime._current_semantic_residency_commit = None
        runtime._current_predictive_residency_commit = None
        runtime._restore_service_grace_by_request = {}
        runtime._last_frontier_model_version = "frontier-v1"
        beneficiary = _predictive_beneficiary_runnable()
        runtime._policy_runtime_runnable = mock.Mock(
            side_effect=AssertionError("runnable frontier must be reused")
        )
        runtime._reclaim_requirements = {}
        runtime._prepared_causal_bindings = {}
        runtime._prepared_causal_binding_revision = 0
        runtime._request_metadata_by_id = {
            beneficiary.request_id: BeliefKVRequestMetadata(
                beneficiary.workflow_id,
                beneficiary.invocation_id,
                beneficiary.context_id,
                beneficiary.context_epoch,
            )
        }
        runtime._latest_predictive_intent = PredictiveIntent(
            intent_id="intent-prepare",
            source_joint_plan_id="source-plan",
            source_snapshot_id="snapshot",
            package_id="package-prepare",
            model_version="frontier-v1",
            action=PredictiveActionKind.PREPARE_HOST,
            invocation_id="inv-predictive",
            expected_invocation_state="wait_tool",
            context_id="ctx-predictive",
            context_epoch=0,
            generated_ts_ms=100.0,
            remaining_window_low_ms=1_000.0,
            transfer_p95_ms=10.0,
            target_bytes_hint=300,
            min_reclaimable_bytes=300,
            max_cross_context_bytes=0,
            max_copy_bytes=300,
            causal_certificate=_predictive_causal_certificate(
                controller, "frontier-v1"
            ),
            required_prediction_heads=("remaining_window",),
            prediction_head_support=(("remaining_window", "exact"),),
            calibration_coverage=0.95,
            future_hbm_feasibility_probability=0.0,
            expected_benefit_ms=5.0,
            shape_fingerprint="summary:300:n1",
            predicted_extent_count=1,
            maximum_transfer_ms=12.0,
            maximum_stall_ms=10.0,
            morphology_slack_ms=100.0,
            **_predictive_beneficiary_evidence(),
        )
        original_intent = runtime._latest_predictive_intent
        decision = compile_bounded_seed_epoch(
            ordered_request_ids=("request",),
            visible_request_ids=("request",),
            epoch_sequence=1,
        )
        plan = SimpleNamespace(
            plan_id=decision.view.plan_id,
            residency=(),
            semantic_residency=(),
        )

        with mock.patch.object(
            controller.arbiter.bundle_builder,
            "previews_for_context",
            side_effect=AssertionError("PREPARE must use the bounded preview"),
        ):
            committed = runtime._physical_commit_predictive_intent(
                plan,
                decision,
                now_ms=110.0,
                current_runnable=(beneficiary,),
            )
        runtime._policy_runtime_runnable.assert_not_called()
        runtime._policy_runtime_runnable = lambda _now_ms: (beneficiary,)

        self.assertIsNotNone(runtime._current_predictive_residency_commit)
        self.assertEqual(
            runtime._current_predictive_residency_commit.intent.source_joint_plan_id,
            plan.plan_id,
        )
        self.assertEqual(
            committed.epoch.action_slices[-1].kind,
            "predictive_residency",
        )
        group = committed.epoch.action_groups[-1]
        self.assertTrue(group.committed)
        self.assertEqual(group.resource_certificate.required_hbm_bytes, 0)
        self.assertEqual(group.resource_certificate.required_host_bytes, 300)
        self.assertTrue(group.resource_certificate.finite_future_risk_bound)
        self.assertTrue(
            runtime._finalize_predictive_safe_point_commit(
                now_ms=110.0,
                wall_ms=0.5,
                cpu_ms=0.4,
                counter_prefix="test_safe_point",
            )
        )
        self.assertTrue(
            runtime._queue_predictive_joint_residency(
                plan.plan_id,
                now_ms=111.0,
            )
        )
        queued = controller.command_queue.pop()
        self.assertIsNotNone(queued)
        self.assertEqual(queued.kind, CommandKind.SHADOW_CONTEXT)
        self.assertEqual(
            queued.metadata["predictive_intent_id"], "intent-prepare"
        )
        self.assertEqual(
            queued.metadata["predictive_transfer_model"],
            "extent_count_aware",
        )
        predictive_queued = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "online_joint_residency_queued"
            and fields.get("predictive_intent_id") == "intent-prepare"
        ]
        self.assertEqual(predictive_queued[-1]["predicted_extent_count"], 1)
        self.assertEqual(predictive_queued[-1]["live_extent_count"], 1)
        self.assertTrue(predictive_queued[-1]["live_shape_fingerprint"])
        self.assertEqual(
            predictive_queued[-1]["transfer_model"], "extent_count_aware"
        )
        predictive_committed = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "predictive_semantic_intent_committed"
        ]
        self.assertEqual(predictive_committed[-1]["live_stall_p90_ms"], 10.0)
        self.assertEqual(
            predictive_committed[-1]["live_stall_source"],
            "intent_certified_interference_envelope",
        )
        self.assertEqual(predictive_committed[-1]["audit_level"], "correctness")
        self.assertEqual(
            runtime._pending_online_joint_residency.plan_id,
            decision.view.plan_id,
        )
        runtime._advance_online_joint_residency(
            (
                CommandAck(
                    queued.command_id,
                    CommandStatus.COMPLETED,
                    112.0,
                    actual_bytes=300,
                ),
            ),
            now_ms=112.0,
        )
        self.assertIsNone(runtime._pending_online_joint_residency)
        self.assertEqual(
            runtime._online_joint_residency_history[-1].stage,
            "completed",
        )
        predictive_terminal = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "online_joint_residency_terminal"
        ]
        self.assertEqual(predictive_terminal[-1]["actual_bytes"], 300)
        binding = runtime._prepared_causal_bindings[beneficiary.request_id]
        self.assertEqual(binding.victim_context_id, "ctx-predictive")
        self.assertEqual(binding.reclaimable_bytes, 300)
        self.assertEqual(binding.required_reclaim_bytes, 50)
        self.assertEqual(binding.causal_package_id, "package-prepare")
        self.assertEqual(
            runtime._joint_predictive_counts[
                "prepared_causal_binding_registered"
            ],
            1,
        )
        control_state = runtime._prepared_causal_bindings_control_state()
        self.assertEqual(control_state["revision"], 1)
        self.assertEqual(
            control_state["bindings"][0]["beneficiary_request_id"],
            beneficiary.request_id,
        )
        runtime._release_reclaim_requirement(
            beneficiary.request_id,
            now_ms=113.0,
            reason="gpu_service_completed",
        )
        self.assertNotIn(
            beneficiary.request_id, runtime._prepared_causal_bindings
        )

        runtime.config = replace(
            runtime.config,
            predictive_prepare_host_canary_limit=3,
        )
        runtime._latest_predictive_intent = replace(
            original_intent,
            intent_id="intent-stale-generation",
            causal_package_generation=(
                "request-beneficiary:ctx-beneficiary:c0:50:51"
            ),
        )
        runtime._physical_commit_predictive_intent(
            plan,
            decision,
            now_ms=113.0,
        )
        rejection = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "predictive_semantic_intent_rejected"
            and fields.get("intent_id") == "intent-stale-generation"
        ][-1]
        self.assertIn(
            "beneficiary_causal_generation_changed",
            rejection["reasons"],
        )

        runtime._latest_predictive_intent = replace(
            original_intent,
            intent_id="intent-reactive-beneficiary",
        )
        runtime._reclaim_requirements = {
            "request-beneficiary": ReclaimRequirement(
                beneficiary_request_id="request-beneficiary",
                required_startup_bytes=50,
                required_growth_bytes=50,
                current_prefix_bytes=0,
                waited_ms=1.0,
                skip_reason="test_observed_deficit",
            )
        }
        runtime._physical_commit_predictive_intent(
            plan,
            decision,
            now_ms=114.0,
        )
        rejection = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "predictive_semantic_intent_rejected"
            and fields.get("intent_id") == "intent-reactive-beneficiary"
        ][-1]
        self.assertIn(
            "beneficiary_reactive_requirement_active",
            rejection["reasons"],
        )

    def test_predictive_commit_budget_uses_cpu_and_latest_start(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = SimpleNamespace(
            joint_physical_action_commit_budget_ms=5.0,
            predictive_physical_action_commit_budget_ms=20.0,
        )
        runtime.audit = _AuditRecorder()
        runtime._joint_predictive_counts = Counter()
        intent = SimpleNamespace(intent_id="intent")
        runtime._latest_predictive_intent = intent
        runtime._current_predictive_residency_commit = SimpleNamespace(
            plan_id="plan",
            intent=intent,
            target=SimpleNamespace(deadline_ms=200.0),
            audit_fields={
                "plan_id": "plan",
                "intent_id": "intent",
                "action": "prepare_host",
            },
        )

        self.assertTrue(
            runtime._finalize_predictive_safe_point_commit(
                now_ms=100.0,
                wall_ms=22.0,
                cpu_ms=1.0,
                counter_prefix="seed_safe_point",
            )
        )
        self.assertEqual(
            runtime._joint_predictive_counts[
                "seed_safe_point_wall_budget_exceeded_accepted"
            ],
            1,
        )
        committed = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "predictive_semantic_intent_committed"
        ]
        self.assertEqual(committed[-1]["validation_wall_ms"], 22.0)
        self.assertEqual(committed[-1]["validation_cpu_ms"], 1.0)

        runtime._current_predictive_residency_commit = SimpleNamespace(
            plan_id="plan-2",
            intent=intent,
            target=SimpleNamespace(deadline_ms=200.0),
            audit_fields={
                "validation_phase_cpu_ms": {"physical_rematerialization": 19.0}
            },
        )
        self.assertFalse(
            runtime._finalize_predictive_safe_point_commit(
                now_ms=120.0,
                wall_ms=2.0,
                cpu_ms=21.0,
                counter_prefix="seed_safe_point",
            )
        )
        self.assertIsNone(runtime._current_predictive_residency_commit)
        self.assertIsNone(runtime._latest_predictive_intent)
        self.assertEqual(
            runtime._joint_predictive_counts[
                "seed_safe_point_cpu_budget_exceeded"
            ],
            1,
        )
        fallback = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "predictive_safe_point_fallback"
        ][-1]
        self.assertEqual(fallback["budget_ms"], 20.0)
        self.assertEqual(
            fallback["validation_phase_cpu_ms"]["physical_rematerialization"],
            19.0,
        )

        runtime._latest_predictive_intent = intent
        runtime._current_predictive_residency_commit = SimpleNamespace(
            plan_id="plan-3",
            intent=intent,
            target=SimpleNamespace(deadline_ms=130.0),
            audit_fields={},
        )
        self.assertFalse(
            runtime._finalize_predictive_safe_point_commit(
                now_ms=120.0,
                wall_ms=10.0,
                cpu_ms=1.0,
                counter_prefix="seed_safe_point",
            )
        )
        self.assertEqual(
            runtime._joint_predictive_counts[
                "seed_safe_point_latest_start_expired_during_commit"
            ],
            1,
        )

    def test_semantic_replacement_binds_reclaim_to_visible_beneficiary(self):
        controller = BeliefKVController(
            BeliefKVConfig(
                hbm_capacity_bytes=2_000,
                host_capacity_bytes=4_000,
                reserve_hbm_bytes=0,
                predictor_enabled=False,
            )
        )
        controller.process_runtime_events(
            (
                RuntimeEvent("wf-v", 1.0, RuntimeEventKind.WORKFLOW_START, "wf-v"),
                RuntimeEvent(
                    "inv-v",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf-v",
                    invocation_id="inv-v",
                    context_id="ctx-v",
                    context_epoch=0,
                ),
                RuntimeEvent(
                    "tool-v",
                    3.0,
                    RuntimeEventKind.TOOL_START,
                    "wf-v",
                    invocation_id="inv-v",
                    context_id="ctx-v",
                    context_epoch=0,
                ),
            )
        )
        handle = PageHandle(904, 0)
        controller.page_index.register_page(handle, size_bytes=300)
        controller.page_index.bind_pages("ctx-v", 0, (handle,))
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            joint_policy_enabled=True,
        )
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        runtime._online_joint_counts = Counter()
        runtime._pending_online_joint_residency = None
        runtime._online_joint_residency_sequence = 0
        runtime._online_joint_residency_history = deque()
        runtime._online_joint_last_residency_action = {}
        runtime._current_semantic_residency_commit = None
        runtime._current_predictive_residency_commit = None
        runtime._restore_service_grace_by_request = {}
        runtime._replacement_priorities = {}
        runtime._reclaim_requirements = {}
        runtime._reclaim_requirement_revision = 0
        runtime._request_metadata_by_id = {}
        runtime._lock_service_ledger = RequestServiceLedger()
        runtime._active_request_ids = set()
        beneficiary = RunnableInvocation(
            "req-beneficiary",
            "wf-b",
            "inv-b",
            "ctx-b",
            0,
            10.0,
            250,
            causal_class="engine_waiting:ready",
        )
        beneficiary_metadata = BeliefKVRequestMetadata(
            beneficiary.workflow_id,
            beneficiary.invocation_id,
            beneficiary.context_id,
            beneficiary.context_epoch,
        )
        runtime._request_metadata_by_id[beneficiary.request_id] = (
            beneficiary_metadata
        )
        runtime._reclaim_requirements[beneficiary.request_id] = (
            ReclaimRequirement(
                beneficiary_request_id=beneficiary.request_id,
                required_startup_bytes=50,
                required_growth_bytes=250,
                current_prefix_bytes=100,
                waited_ms=5_000.0,
                skip_reason="bounded_hbm_budget",
            )
        )
        runtime._reclaim_requirement_revision = 1
        controller._reported_hbm_used_bytes = 1_900
        runtime._policy_runtime_runnable = lambda _now_ms: (beneficiary,)
        decision = compile_bounded_seed_epoch(
            ordered_request_ids=(beneficiary.request_id,),
            visible_request_ids=(beneficiary.request_id,),
            epoch_sequence=1,
        )
        target = SemanticResidencyTarget(
            context_id="ctx-v",
            context_epoch=0,
            action=ResidencyAction.COMMIT_CPU,
            target_bytes_hint=300,
            deadline_ms=100.0,
            reason="beneficiary-bound HBM reclaim from a causal package",
            beneficiary_request_id=beneficiary.request_id,
            required_reclaim_bytes=250,
            causal_package_id="causal:req-beneficiary:ctx-v:0",
            expected_unlock_boundary="first_gpu_service:req-beneficiary",
            estimated_transfer_cost_ms=1.0,
            estimated_saved_stall_ms=5_000.0,
            estimated_net_benefit_ms=4_999.0,
            restore_cost_included=True,
        )
        plan = SimpleNamespace(
            plan_id=decision.view.plan_id,
            residency=(),
            semantic_residency=(target,),
        )

        negative_target = replace(
            target,
            estimated_saved_stall_ms=0.001,
            estimated_net_benefit_ms=0.0,
        )
        negative_plan = SimpleNamespace(
            plan_id=decision.view.plan_id,
            residency=(),
            semantic_residency=(negative_target,),
        )
        rejected = runtime._physical_commit_semantic_residency(
            negative_plan,
            decision,
            now_ms=100.0,
        )
        self.assertIsNone(runtime._current_semantic_residency_commit)
        rejected_group = next(
            group
            for group in rejected.epoch.action_groups
            if any(action.kind == "semantic_residency" for action in group.actions)
        )
        self.assertIn(
            "physical:causal_package_nonpositive_after_rematerialization",
            rejected_group.reasons,
        )

        committed = runtime._physical_commit_semantic_residency(
            plan,
            decision,
            now_ms=100.0,
        )

        self.assertIsNotNone(runtime._current_semantic_residency_commit)
        replacement_group = next(
            group
            for group in committed.epoch.action_groups
            if any(
                action.kind == "semantic_residency"
                for action in group.actions
            )
        )
        self.assertTrue(replacement_group.committed)
        self.assertEqual(len(replacement_group.actions), 2)
        self.assertEqual(
            replacement_group.resource_certificate.planned_reclaim_bytes,
            300,
        )
        runtime._queue_semantic_joint_residency(
            plan,
            committed.view,
            now_ms=101.0,
        )
        queued = controller.command_queue.pop()
        self.assertIsNotNone(queued)
        self.assertEqual(
            queued.metadata["joint_beneficiary_request_id"],
            beneficiary.request_id,
        )
        self.assertEqual(
            queued.metadata["causal_package_id"],
            target.causal_package_id,
        )
        self.assertGreater(queued.metadata["estimated_net_benefit_ms"], 0)
        runtime._advance_online_joint_residency(
            (
                CommandAck(
                    queued.command_id,
                    CommandStatus.COMPLETED,
                    102.0,
                    actual_bytes=300,
                ),
            ),
            now_ms=102.0,
        )
        self.assertIn(beneficiary.request_id, runtime._replacement_priorities)

        # Ticket generation/native admission are not service evidence. The
        # beneficiary must remain prioritized across subsequent safe points.
        runtime._current_tickets_by_request = {
            beneficiary.request_id: object()
        }
        visible_entry = SimpleNamespace(state=AdmissionSideState.VISIBLE_PENDING)
        self.assertEqual(
            runtime._refresh_replacement_priorities(
                {beneficiary.request_id: visible_entry}, now_ms=103.0
            ),
            (beneficiary.request_id,),
        )
        self.assertIn(
            beneficiary.request_id, runtime._current_tickets_by_request
        )
        self.assertIn(beneficiary.request_id, runtime._replacement_priorities)

        request = SimpleNamespace(
            rid=beneficiary.request_id,
            beliefkv_metadata=beneficiary_metadata,
        )
        runtime._lock_service_ledger.observe_selected(
            request_id=beneficiary.request_id,
            workflow_id=beneficiary.workflow_id,
            invocation_id=beneficiary.invocation_id,
            context_id=beneficiary.context_id,
            ts_ms=104.0,
        )
        runtime._active_request_ids.add(beneficiary.request_id)
        runtime._observe_request_service_completed(
            SimpleNamespace(reqs=(request,)),
            now_ms=105.0,
            phase="prefill",
        )
        self.assertNotIn(beneficiary.request_id, runtime._replacement_priorities)
        self.assertNotIn(
            beneficiary.request_id, runtime._reclaim_requirements
        )

        runtime._online_joint_last_context_residency_action = {}
        runtime._record_context_residency_direction(
            context_id="ctx-reverse",
            action=ResidencyAction.COMMIT_CPU,
            now_ms=200.0,
            transaction_id="offload",
        )
        runtime._record_context_residency_direction(
            context_id="ctx-reverse",
            action=ResidencyAction.PREFETCH_GPU,
            now_ms=1_000.0,
            transaction_id="restore",
        )
        self.assertEqual(
            runtime._online_joint_counts["residency_short_reverse"], 1
        )

    def test_semantic_replacement_rejects_disappeared_beneficiary(self):
        controller = BeliefKVController(
            BeliefKVConfig(
                hbm_capacity_bytes=2_000,
                host_capacity_bytes=4_000,
                reserve_hbm_bytes=0,
                predictor_enabled=False,
            )
        )
        controller.process_runtime_events(
            (
                RuntimeEvent("wf-v", 1.0, RuntimeEventKind.WORKFLOW_START, "wf-v"),
                RuntimeEvent(
                    "inv-v",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf-v",
                    invocation_id="inv-v",
                    context_id="ctx-v",
                    context_epoch=0,
                ),
                RuntimeEvent(
                    "tool-v",
                    3.0,
                    RuntimeEventKind.TOOL_START,
                    "wf-v",
                    invocation_id="inv-v",
                    context_id="ctx-v",
                    context_epoch=0,
                ),
            )
        )
        handle = PageHandle(905, 0)
        controller.page_index.register_page(handle, size_bytes=300)
        controller.page_index.bind_pages("ctx-v", 0, (handle,))
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            joint_policy_enabled=True,
        )
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        runtime._online_joint_counts = Counter()
        runtime._current_semantic_residency_commit = None
        runtime._current_predictive_residency_commit = None
        runtime._policy_runtime_runnable = lambda _now_ms: ()
        decision = compile_bounded_seed_epoch(
            ordered_request_ids=("req-gone",),
            visible_request_ids=("req-gone",),
            epoch_sequence=1,
        )
        target = SemanticResidencyTarget(
            context_id="ctx-v",
            context_epoch=0,
            action=ResidencyAction.COMMIT_CPU,
            target_bytes_hint=300,
            deadline_ms=100.0,
            reason="replacement test",
            beneficiary_request_id="req-gone",
            required_reclaim_bytes=250,
        )
        plan = SimpleNamespace(
            plan_id=decision.view.plan_id,
            residency=(),
            semantic_residency=(target,),
        )

        rejected = runtime._physical_commit_semantic_residency(
            plan,
            decision,
            now_ms=100.0,
        )

        self.assertIsNone(runtime._current_semantic_residency_commit)
        group = next(
            group
            for group in rejected.epoch.action_groups
            if any(
                action.kind == "semantic_residency"
                for action in group.actions
            )
        )
        self.assertFalse(group.committed)
        self.assertIn("replacement_beneficiary_not_visible", group.reasons)

    def test_predictive_prefetch_confidence_rejection_preserves_observed_epoch(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            predictor_model_path="/tmp/frontier.json",
            gpu_service_model_path="/tmp/service.json",
            joint_policy_enabled=True,
            predictive_risk_shadow_enabled=True,
            predictive_joint_overlay_enabled=True,
            predictive_prefetch_canary_enabled=True,
        )
        controller = BeliefKVController(
            BeliefKVConfig(
                hbm_capacity_bytes=2_000,
                host_capacity_bytes=4_000,
                reserve_hbm_bytes=0,
                predictor_enabled=False,
            )
        )
        controller.process_runtime_events(
            (
                RuntimeEvent("wf-prefetch", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
                RuntimeEvent(
                    "inv-prefetch",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
                RuntimeEvent(
                    "tool-prefetch",
                    3.0,
                    RuntimeEventKind.TOOL_START,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        handle = PageHandle(902, 0)
        controller.page_index.register_page(
            handle,
            size_bytes=200,
            residency=PhysicalResidency.CPU_ONLY,
        )
        controller.page_index.bind_pages("ctx", 0, (handle,))
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        runtime._joint_predictive_counts = Counter()
        runtime._online_joint_counts = Counter()
        runtime._pending_online_joint_residency = None
        runtime._current_semantic_residency_commit = None
        runtime._current_predictive_residency_commit = None
        runtime._restore_service_grace_by_request = {}
        runtime._last_frontier_model_version = "frontier-v1"
        runtime._latest_predictive_intent = PredictiveIntent(
            intent_id="intent-prefetch",
            source_joint_plan_id="source-plan",
            source_snapshot_id="snapshot",
            package_id="package-prefetch",
            model_version="frontier-v1",
            action=PredictiveActionKind.PREFETCH_GPU,
            invocation_id="inv",
            expected_invocation_state="wait_tool",
            context_id="ctx",
            context_epoch=0,
            generated_ts_ms=100.0,
            remaining_window_low_ms=100.0,
            transfer_p95_ms=20.0,
            target_bytes_hint=200,
            min_reclaimable_bytes=0,
            max_cross_context_bytes=0,
            max_copy_bytes=200,
            causal_certificate=_predictive_causal_certificate(
                controller, "frontier-v1"
            ),
            required_prediction_heads=("future_kv_growth", "reentry_window"),
            prediction_head_support=(
                ("future_kv_growth", "exact"),
                ("reentry_window", "exact"),
            ),
            calibration_coverage=0.95,
            future_hbm_feasibility_probability=0.90,
            expected_benefit_ms=5.0,
            shape_fingerprint="prefetch-not-shape-certified",
            predicted_extent_count=0,
            maximum_transfer_ms=24.0,
            maximum_stall_ms=0.0,
            morphology_slack_ms=0.0,
            target_reentry_context_epoch=1,
        )
        decision = compile_bounded_seed_epoch(
            ordered_request_ids=("request",),
            visible_request_ids=("request",),
            epoch_sequence=1,
        )
        plan = SimpleNamespace(
            plan_id=decision.view.plan_id,
            residency=(),
            semantic_residency=(),
        )

        unchanged = runtime._physical_commit_predictive_intent(
            plan,
            decision,
            now_ms=110.0,
        )

        self.assertEqual(unchanged, decision)
        self.assertIsNone(runtime._current_predictive_residency_commit)
        rejected = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "predictive_semantic_intent_rejected"
        ]
        self.assertIn("future_hbm_confidence", rejected[-1]["reasons"])

        runtime._latest_predictive_intent = replace(
            runtime._latest_predictive_intent
            or PredictiveIntent(
                intent_id="intent-prefetch-early",
                source_joint_plan_id="source-plan",
                source_snapshot_id="snapshot",
                package_id="package-prefetch-early",
                model_version="frontier-v1",
                action=PredictiveActionKind.PREFETCH_GPU,
                invocation_id="inv",
                expected_invocation_state="wait_tool",
                context_id="ctx",
                context_epoch=0,
                generated_ts_ms=100.0,
                remaining_window_low_ms=10_000.0,
                transfer_p95_ms=20.0,
                target_bytes_hint=200,
                min_reclaimable_bytes=0,
                max_cross_context_bytes=0,
                max_copy_bytes=200,
                causal_certificate=_predictive_causal_certificate(
                    controller, "frontier-v1"
                ),
                required_prediction_heads=("reentry_window",),
                prediction_head_support=(("reentry_window", "exact"),),
                calibration_coverage=0.95,
                future_hbm_feasibility_probability=1.0,
                expected_benefit_ms=5.0,
                shape_fingerprint="prefetch-not-shape-certified",
                predicted_extent_count=0,
                maximum_transfer_ms=24.0,
                maximum_stall_ms=0.0,
                morphology_slack_ms=0.0,
                target_reentry_context_epoch=1,
            ),
            future_hbm_feasibility_probability=1.0,
        )
        runtime._predictive_prefetch_retry_not_before_ms = None
        unchanged = runtime._physical_commit_predictive_intent(
            plan,
            decision,
            now_ms=110.0,
        )

        self.assertEqual(unchanged, decision)
        self.assertIsNotNone(runtime._latest_predictive_intent)
        self.assertGreater(
            runtime._predictive_prefetch_retry_not_before_ms,
            110.0,
        )
        deferred = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "predictive_semantic_intent_deferred"
        ]
        self.assertEqual(deferred[-1]["intent_id"], "intent-prefetch-early")
        self.assertNotIn(
            "prefetch_too_early",
            [
                reason
                for item in runtime.audit.events
                if item[0] == "predictive_semantic_intent_rejected"
                for reason in item[2].get("reasons", ())
                if item[2].get("intent_id") == "intent-prefetch-early"
            ],
        )

    def test_physical_device_capacity_uses_native_allocator_authority(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            kv_bytes_per_token=10,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.controller.report_hbm_usage(1_900)
        runtime.scheduler = SimpleNamespace(
            token_to_kv_pool_allocator=SimpleNamespace(
                full_available_size=lambda: 100,
                swa_available_size=lambda: 80,
            )
        )

        self.assertEqual(runtime._physical_device_available_bytes(), 800)
        self.assertEqual(
            runtime._last_physical_device_capacity_source,
            "native_allocator",
        )

    def test_prefetch_service_lease_prioritizes_until_first_service(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            resident_service_window_ms=5_000.0,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.controller.process_runtime_events(
            (
                RuntimeEvent(
                    "workflow-start",
                    1.0,
                    RuntimeEventKind.WORKFLOW_START,
                    "wf",
                ),
                RuntimeEvent(
                    "invocation-create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        runtime.audit = _AuditRecorder()
        runtime._joint_predictive_counts = Counter()
        runtime._prefetch_service_leases = {}
        runtime._context_completed_service_epoch_by_id = {}
        runtime._request_metadata_by_id = {
            "request": BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        }
        runtime._lock_service_ledger = RequestServiceLedger()
        runtime._lock_service_ledger.observe_selected(
            request_id="request",
            workflow_id="wf",
            invocation_id="inv",
            context_id="ctx",
            ts_ms=90.0,
        )
        transaction = SimpleNamespace(
            beneficiary_request_id="request",
            predictive_intent_id="intent",
            context_id="ctx",
            context_epoch=0,
            transaction_id="transaction",
            actual_bytes=256,
            target_invocation_id="inv",
            target_reentry_context_epoch=0,
        )

        self.assertTrue(
            runtime._register_prefetch_service_lease(
                transaction,
                now_ms=100.0,
            )
        )
        entries = {
            "request": SimpleNamespace(
                state=AdmissionSideState.VISIBLE_PENDING
            )
        }
        self.assertEqual(
            runtime._refresh_prefetch_service_leases(
                entries,
                now_ms=101.0,
            ),
            ("request",),
        )
        self.assertTrue(
            runtime._context_has_prefetch_service_lease(
                "ctx",
                0,
                now_ms=101.0,
            )
        )

        runtime._lock_service_ledger.observe_completed(
            "request",
            ts_ms=102.0,
            phase="decode",
        )
        self.assertEqual(
            runtime._refresh_prefetch_service_leases(
                entries,
                now_ms=103.0,
            ),
            (),
        )
        self.assertEqual(runtime._prefetch_service_leases, {})

    def test_prefetch_service_lease_expires_without_service(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            resident_service_window_ms=5_000.0,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.controller.process_runtime_events(
            (
                RuntimeEvent(
                    "workflow-start",
                    1.0,
                    RuntimeEventKind.WORKFLOW_START,
                    "wf",
                ),
                RuntimeEvent(
                    "invocation-create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        runtime.audit = _AuditRecorder()
        runtime._joint_predictive_counts = Counter()
        runtime._prefetch_service_leases = {}
        runtime._context_completed_service_epoch_by_id = {}
        runtime._request_metadata_by_id = {
            "request": BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        }
        runtime._lock_service_ledger = RequestServiceLedger()
        transaction = SimpleNamespace(
            beneficiary_request_id="request",
            predictive_intent_id="intent",
            context_id="ctx",
            context_epoch=0,
            transaction_id="transaction",
            actual_bytes=256,
            target_invocation_id="inv",
            target_reentry_context_epoch=0,
        )
        self.assertTrue(
            runtime._register_prefetch_service_lease(
                transaction,
                now_ms=100.0,
            )
        )

        self.assertFalse(
            runtime._context_has_prefetch_service_lease(
                "ctx",
                0,
                now_ms=5_101.0,
            )
        )
        self.assertEqual(runtime._prefetch_service_leases, {})
        self.assertEqual(
            runtime._joint_predictive_counts[
                "prefetch_service_lease_released:service_window_expired"
            ],
            1,
        )

    def test_prefetch_service_funding_and_attribution_end_on_first_service(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=1 << 30,
            host_capacity_bytes=1 << 30,
            reserve_hbm_bytes=0,
            kv_bytes_per_token=1,
            admission_allocator_guard_tokens=1,
            admission_prefill_quantum_tokens=8,
            admission_decode_quantum_tokens=4,
        )
        allocator = _Allocator(128)
        allocator.page_size = 1
        runtime.scheduler = SimpleNamespace(
            token_to_kv_pool_allocator=allocator
        )
        request = SimpleNamespace(
            estimated_incremental_bytes=64,
            submitted_ts_ms=90.0,
        )
        entry = SimpleNamespace(
            state=AdmissionSideState.VISIBLE_PENDING,
            request=request,
        )
        runtime.controller = SimpleNamespace(
            visible_admission=SimpleNamespace(get=lambda request_id: entry)
        )
        runtime.audit = _AuditRecorder()
        runtime._joint_predictive_counts = Counter()
        runtime._online_joint_counts = Counter()
        runtime._active_admission_rescue = None
        runtime._request_metadata_by_id = {
            "request": BeliefKVRequestMetadata("wf", "inv", "ctx", 1)
        }
        runtime._lock_service_ledger = RequestServiceLedger()
        lease = SimpleNamespace(
            request_id="request",
            context_id="ctx",
            context_epoch=0,
            target_invocation_id="inv",
            target_reentry_context_epoch=1,
            created_ts_ms=100.0,
            expires_ts_ms=10_000.0,
            baseline_completed_service_count=0,
            source_transaction_id="transaction",
            predictive_intent_id="intent",
            actual_bytes=256,
        )
        runtime._prefetch_service_leases = {"ctx": lease}
        ledger = runtime._predictive_action_ledger()
        ledger.register(
            intent_id="intent",
            action=PredictiveActionKind.PREFETCH_GPU.value,
            context_id="ctx",
            context_epoch=0,
            command_id="command",
            now_ms=99.0,
        )
        ledger.transfer_terminal(
            "intent",
            completed=True,
            actual_bytes=256,
            now_ms=100.0,
        )

        self.assertTrue(
            runtime._maybe_reserve_prefetch_service_funding(
                lease,
                now_ms=101.0,
            )
        )
        self.assertEqual(
            runtime._active_admission_rescue.source,
            "predictive_prefetch",
        )
        self.assertEqual(runtime._active_admission_rescue.reserved_tokens, 13)
        self.assertEqual(allocator.available_tokens, 115)

        self.assertTrue(
            runtime._release_prefetch_service_lease(
                "request",
                now_ms=102.0,
                reason="gpu_service_completed",
            )
        )
        self.assertIsNone(runtime._active_admission_rescue)
        self.assertEqual(allocator.available_tokens, 128)
        outcome = ledger.outcomes()[0]
        self.assertEqual(outcome.state, "useful")
        self.assertEqual(outcome.reason, "prefetch_first_gpu_service")
        self.assertEqual(
            runtime._joint_predictive_counts[
                "prefetch_attribution_consumed"
            ],
            1,
        )

    def test_context_prefetch_lease_binds_next_reentry_request(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            resident_service_window_ms=5_000.0,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.controller.process_runtime_events(
            (
                RuntimeEvent(
                    "workflow-start",
                    1.0,
                    RuntimeEventKind.WORKFLOW_START,
                    "wf",
                ),
                RuntimeEvent(
                    "invocation-create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
                RuntimeEvent(
                    "tool-start",
                    3.0,
                    RuntimeEventKind.TOOL_START,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                    attributes={"tool_family": "shell"},
                ),
            )
        )
        runtime.audit = _AuditRecorder()
        runtime._joint_predictive_counts = Counter()
        runtime._prefetch_service_leases = {}
        runtime._context_completed_service_epoch_by_id = {}
        runtime._request_metadata_by_id = {}
        runtime._lock_service_ledger = RequestServiceLedger()
        transaction = SimpleNamespace(
            beneficiary_request_id=None,
            predictive_intent_id="intent",
            context_id="ctx",
            context_epoch=0,
            transaction_id="transaction",
            actual_bytes=256,
            target_invocation_id="inv",
            target_reentry_context_epoch=1,
        )

        self.assertTrue(
            runtime._register_prefetch_service_lease(
                transaction,
                now_ms=100.0,
            )
        )
        self.assertEqual(
            runtime._refresh_prefetch_service_leases({}, now_ms=101.0),
            (),
        )
        runtime.controller.process_runtime_events(
            (
                RuntimeEvent(
                    "context-advance",
                    102.0,
                    RuntimeEventKind.CONTEXT_ADVANCE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=1,
                ),
            )
        )
        runtime._request_metadata_by_id["request-next"] = (
            BeliefKVRequestMetadata("wf", "inv", "ctx", 1)
        )
        runtime._lock_service_ledger.observe_selected(
            request_id="request-next",
            workflow_id="wf",
            invocation_id="inv",
            context_id="ctx",
            ts_ms=103.0,
        )
        entries = {
            "request-next": SimpleNamespace(
                state=AdmissionSideState.VISIBLE_PENDING
            )
        }

        self.assertEqual(
            runtime._refresh_prefetch_service_leases(
                entries,
                now_ms=104.0,
            ),
            ("request-next",),
        )
        lease = runtime._prefetch_service_leases["ctx"]
        self.assertEqual(lease.request_id, "request-next")
        self.assertEqual(lease.context_epoch, 0)
        self.assertEqual(lease.target_reentry_context_epoch, 1)
        runtime._lock_service_ledger.observe_completed(
            "request-next",
            ts_ms=105.0,
            phase="prefill",
        )
        self.assertEqual(
            runtime._refresh_prefetch_service_leases(
                entries,
                now_ms=106.0,
            ),
            (),
        )
        self.assertEqual(runtime._prefetch_service_leases, {})

    def test_context_prefetch_lease_rejects_late_ack_after_target_service(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            resident_service_window_ms=5_000.0,
        )
        runtime.controller = BeliefKVController(runtime.config)
        runtime.controller.process_runtime_events(
            (
                RuntimeEvent(
                    "workflow-start",
                    1.0,
                    RuntimeEventKind.WORKFLOW_START,
                    "wf",
                ),
                RuntimeEvent(
                    "invocation-create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
                RuntimeEvent(
                    "context-advance",
                    3.0,
                    RuntimeEventKind.CONTEXT_ADVANCE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=1,
                ),
            )
        )
        runtime.audit = _AuditRecorder()
        runtime._joint_predictive_counts = Counter()
        runtime._prefetch_service_leases = {}
        runtime._context_completed_service_epoch_by_id = {"ctx": 1}
        runtime._request_metadata_by_id = {}
        runtime._lock_service_ledger = RequestServiceLedger()
        transaction = SimpleNamespace(
            beneficiary_request_id=None,
            predictive_intent_id="intent",
            context_id="ctx",
            context_epoch=0,
            transaction_id="transaction",
            actual_bytes=256,
            target_invocation_id="inv",
            target_reentry_context_epoch=1,
        )

        self.assertFalse(
            runtime._register_prefetch_service_lease(
                transaction,
                now_ms=100.0,
            )
        )
        self.assertEqual(runtime._prefetch_service_leases, {})
        self.assertEqual(
            runtime._joint_predictive_counts[
                "prefetch_service_lease_registration_late"
            ],
            1,
        )

    def test_predictive_causal_change_before_safe_point_preserves_observed_epoch(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=2_000,
            host_capacity_bytes=4_000,
            reserve_hbm_bytes=0,
            predictor_model_path="/tmp/frontier.json",
            gpu_service_model_path="/tmp/service.json",
            joint_policy_enabled=True,
            predictive_risk_shadow_enabled=True,
            predictive_joint_overlay_enabled=True,
        )
        controller = BeliefKVController(
            BeliefKVConfig(
                hbm_capacity_bytes=2_000,
                host_capacity_bytes=4_000,
                reserve_hbm_bytes=0,
                predictor_enabled=False,
            )
        )
        controller.process_runtime_events(
            (
                RuntimeEvent("wf-causal", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
                RuntimeEvent(
                    "inv-causal",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
                RuntimeEvent(
                    "tool-causal",
                    3.0,
                    RuntimeEventKind.TOOL_START,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        runtime._joint_predictive_counts = Counter()
        runtime._pending_online_joint_residency = None
        runtime._current_semantic_residency_commit = None
        runtime._current_predictive_residency_commit = None
        runtime._restore_service_grace_by_request = {}
        runtime._last_frontier_model_version = "frontier-v1"
        runtime._policy_runtime_runnable = (
            lambda _now_ms: (_predictive_beneficiary_runnable(),)
        )
        runtime._reclaim_requirements = {}
        runtime._latest_predictive_intent = PredictiveIntent(
            intent_id="intent-causal",
            source_joint_plan_id="source-plan",
            source_snapshot_id="snapshot",
            package_id="package-causal",
            model_version="frontier-v1",
            action=PredictiveActionKind.PREPARE_HOST,
            invocation_id="inv",
            expected_invocation_state="wait_tool",
            context_id="ctx",
            context_epoch=0,
            generated_ts_ms=100.0,
            remaining_window_low_ms=1_000.0,
            transfer_p95_ms=10.0,
            target_bytes_hint=100,
            min_reclaimable_bytes=100,
            max_cross_context_bytes=0,
            max_copy_bytes=100,
            causal_certificate=_predictive_causal_certificate(
                controller, "frontier-v1"
            ),
            required_prediction_heads=("remaining_window",),
            prediction_head_support=(("remaining_window", "exact"),),
            calibration_coverage=0.95,
            future_hbm_feasibility_probability=1.0,
            expected_benefit_ms=5.0,
            shape_fingerprint="shape-v1",
            predicted_extent_count=1,
            maximum_transfer_ms=12.0,
            maximum_stall_ms=10.0,
            morphology_slack_ms=100.0,
            **_predictive_beneficiary_evidence(),
        )
        # The prediction was published, then an RCCG event changed its read set
        # before the next scheduler safe point.
        controller.graph.invocations["inv"].updated_ts_ms += 1.0
        decision = compile_bounded_seed_epoch(
            ordered_request_ids=("request",),
            visible_request_ids=("request",),
            epoch_sequence=1,
        )
        plan = SimpleNamespace(
            plan_id=decision.view.plan_id,
            residency=(),
            semantic_residency=(),
        )

        unchanged = runtime._physical_commit_predictive_intent(
            plan,
            decision,
            now_ms=110.0,
        )

        self.assertEqual(unchanged, decision)
        self.assertIsNone(runtime._current_predictive_residency_commit)
        rejected = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "predictive_semantic_intent_rejected"
        ]
        self.assertTrue(
            any(
                reason.startswith("causal:invocation_revision:inv")
                for reason in rejected[-1]["reasons"]
            )
        )

    def test_observed_admission_holds_new_tickets_at_active_kv_watermark(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=1000,
            reserve_hbm_bytes=100,
            kv_bytes_per_token=10,
            observed_admission_scheduling_enabled=True,
            observed_admission_active_kv_high_watermark_ratio=0.8,
            observed_admission_min_active_requests=1,
        )
        controller = BeliefKVController(config)
        controller.process_runtime_event(
            RuntimeEvent(
                event_id="wf-start",
                ts_ms=0,
                kind=RuntimeEventKind.WORKFLOW_START,
                workflow_id="wf",
            )
        )
        for suffix in ("a", "b"):
            controller.process_runtime_event(
                RuntimeEvent(
                    event_id=f"inv-{suffix}",
                    ts_ms=1,
                    kind=RuntimeEventKind.INVOCATION_CREATE,
                    workflow_id="wf",
                    invocation_id=f"inv-{suffix}",
                    context_id=f"ctx-{suffix}",
                    context_epoch=0,
                )
            )
        locked = PageHandle(99, 0)
        controller.page_index.register_page(locked, size_bytes=800)
        controller.page_index.set_engine_lock(locked, 1)

        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = controller
        runtime.config = config
        runtime._now_ms = lambda: 10.0
        runtime.audit = _AuditRecorder()
        runtime._admission_epoch = 0
        runtime._current_ticket_epoch = None
        runtime._current_tickets_by_request = {}
        runtime._ticket_attempted_request_ids = set()
        runtime._ticket_selected_request_ids = set()
        runtime._ticket_skip_audit = set()
        runtime._ticket_selection_details = {}
        runtime._ticket_native_rejections = {}
        runtime._pending_h2d_contexts = set()
        runtime._request_metadata_by_id = {}
        runtime._request_submitted_ts_by_id = {}
        running = SimpleNamespace(
            rid="native-running",
            fill_ids=(),
            prefix_indices=(),
            origin_input_ids=(),
            output_ids=(),
        )
        runtime.scheduler = SimpleNamespace(
            running_batch=SimpleNamespace(reqs=[running]),
            chunked_req=None,
        )

        def request(suffix):
            metadata = BeliefKVRequestMetadata(
                "wf", f"inv-{suffix}", f"ctx-{suffix}", 0
            )
            item = SimpleNamespace(
                rid=f"request-{suffix}",
                beliefkv_metadata=metadata,
                origin_input_ids=_NoBooleanSequence(1),
                prefix_indices=_NoBooleanSequence(0),
            )
            runtime._request_metadata_by_id[item.rid] = metadata
            controller.register_visible_request(
                AdmissionRequest(
                    item.rid,
                    "wf",
                    metadata.invocation_id,
                    metadata.context_id,
                    0,
                    1.0,
                    1,
                    1,
                    10,
                )
            )
            return item

        queue = [request("a"), request("b")]
        adder = SimpleNamespace(
            rem_input_tokens=100,
            rem_chunk_tokens=None,
            rem_total_tokens=100,
        )
        runtime.begin_prefill_epoch(queue, adder, max_requests=2)

        self.assertEqual(runtime._current_ticket_epoch.source, "observed_active_set")
        self.assertEqual(runtime._current_ticket_epoch.tickets, ())
        self.assertEqual(
            runtime._current_observed_admission_window.mode,
            "active_kv_pressure_hold",
        )
        started = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "admission_ticket_epoch_started"
        ][-1]
        self.assertEqual(
            started["observed_admission_window"]["active_kv_footprint_bytes"],
            800,
        )
        runtime.end_prefill_epoch(())

        controller.page_index.set_engine_lock(locked, 0)
        runtime.begin_prefill_epoch(queue, adder, max_requests=2)

        self.assertEqual(
            [
                ticket.request_id
                for ticket in runtime._current_ticket_epoch.tickets
            ],
            ["request-a", "request-b"],
        )
        self.assertEqual(
            runtime._current_observed_admission_window.mode,
            "active_kv_bounded",
        )
        runtime.end_prefill_epoch(())

        def fail_snapshot(**_):
            raise RuntimeError("synthetic observer failure")

        runtime._observed_admission_snapshot = fail_snapshot
        runtime.begin_prefill_epoch(queue, adder, max_requests=2)
        self.assertEqual(
            runtime._current_ticket_epoch.source,
            "observed_active_set_fallback",
        )
        self.assertEqual(len(runtime._current_ticket_epoch.tickets), 2)
        self.assertIn(
            "observed_admission_fallback",
            [event for event, _, _ in runtime.audit.events],
        )
        runtime.end_prefill_epoch(())

    def test_selective_retraction_requeue_stays_blocked_until_cooldown(self):
        config = BeliefKVConfig(
            observed_admission_scheduling_enabled=True,
            running_batch_retraction_enabled=True,
        )
        controller = BeliefKVController(config)
        controller.process_runtime_event(
            RuntimeEvent(
                event_id="wf-start-retraction",
                ts_ms=0,
                kind=RuntimeEventKind.WORKFLOW_START,
                workflow_id="wf-retraction",
            )
        )
        controller.process_runtime_event(
            RuntimeEvent(
                event_id="inv-start-retraction",
                ts_ms=1,
                kind=RuntimeEventKind.INVOCATION_CREATE,
                workflow_id="wf-retraction",
                invocation_id="inv-retraction",
                context_id="ctx-retraction",
                context_epoch=0,
            )
        )
        metadata = BeliefKVRequestMetadata(
            "wf-retraction", "inv-retraction", "ctx-retraction", 0
        )
        now_ms = [1000.0]
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = controller
        runtime.config = config
        runtime.tree_cache = _TreeCache()
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: now_ms[0]
        runtime._pending_h2d_contexts = set()
        runtime._request_metadata_by_id = {"victim": metadata}
        runtime._request_submitted_ts_by_id = {"victim": 10.0}
        runtime._pending_selective_retraction_ids = {"victim"}
        runtime._retraction_cooldown_until_by_request = {"victim": 2000.0}
        request = SimpleNamespace(
            rid="victim",
            beliefkv_metadata=metadata,
            origin_input_ids=(1, 2),
            output_ids=(3,),
            prefix_indices=(1,),
            sampling_params=SimpleNamespace(max_new_tokens=8),
            last_node=None,
            init_next_round_input=lambda _cache: None,
        )

        runtime.on_requests_requeued((request,), is_retracted=True)
        entry = controller.visible_admission.get("victim")
        self.assertEqual(entry.state, AdmissionSideState.POLICY_BLOCKED)
        self.assertEqual(entry.blocker_reason, "retraction_cooldown")

        now_ms[0] = 2001.0
        runtime._sync_visible_gate_state("victim", metadata, req=request)
        entry = controller.visible_admission.get("victim")
        self.assertEqual(entry.state, AdmissionSideState.VISIBLE_PENDING)

    def test_restore_obligation_supersedes_retraction_cooldown_on_requeue(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=2000,
            reserve_hbm_bytes=100,
            kv_bytes_per_token=10,
            observed_admission_scheduling_enabled=True,
            running_batch_retraction_enabled=True,
        )
        controller = BeliefKVController(config)
        controller.process_runtime_events(
            (
                RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
                RuntimeEvent(
                    "create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        tree_cache = _TreeCache()
        node = _Node(17)
        node.parent = tree_cache.root_node
        registry = SGLangNodeRegistry()
        handle = registry.register(node)
        controller.page_index.register_page(
            handle,
            size_bytes=100,
            residency=PhysicalResidency.CPU_ONLY,
            radix_depth=1,
        )
        controller.page_index.bind_pages("ctx", 0, (handle,))
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.tree_cache = tree_cache
        runtime.registry = registry
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 1000.0
        runtime._request_metadata_by_id = {"victim": metadata}
        runtime._request_submitted_ts_by_id = {"victim": 10.0}
        runtime._pending_selective_retraction_ids = {"victim"}
        runtime._retraction_cooldown_until_by_request = {"victim": 5000.0}
        obligation = runtime._restore_obligation_index().create(
            request_id="victim",
            workflow_id="wf",
            invocation_id="inv",
            context_id="ctx",
            context_epoch=0,
            source_retraction_transaction_id="retraction-1",
            source_joint_plan_id="joint-1",
            created_ts_ms=900.0,
            path_extent_ids=(f"page:{handle.page_id}:0",),
        )
        obligation.source_transaction_terminal = True
        request = SimpleNamespace(
            rid="victim",
            beliefkv_metadata=metadata,
            origin_input_ids=(1, 2),
            output_ids=(3,),
            prefix_indices=(),
            sampling_params=SimpleNamespace(max_new_tokens=8),
            last_node=node,
            init_next_round_input=lambda _cache: None,
        )

        runtime.on_requests_requeued((request,), is_retracted=True)

        entry = controller.visible_admission.get("victim")
        self.assertEqual(entry.state, AdmissionSideState.WAIT_RESTORE)
        self.assertEqual(entry.blocker_reason, "restore_obligation_pending")
        self.assertEqual(
            entry.restore_bundle_ids,
            (f"page:{handle.page_id}:0",),
        )
        self.assertTrue(obligation.requeued)

    def test_restore_debt_bypass_is_idle_bounded_and_smallest_first(self):
        config = BeliefKVConfig(
            kv_bytes_per_token=10,
            restore_obligation_escalation_ms=1000.0,
            restore_obligation_max_blocked_ms=2000.0,
            restore_lease_max_bypass_admissions=1,
        )
        controller = BeliefKVController(config)
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        runtime.scheduler = SimpleNamespace(
            running_batch=SimpleNamespace(reqs=[]),
            chunked_req=None,
        )
        runtime._metadata_scope_is_terminal = lambda _metadata: False
        runtime._request_restore_bundle_ids = lambda _req, _context_id: ()

        obligation = runtime._restore_obligation_index().create(
            request_id="restore-target",
            workflow_id="wf-restore",
            invocation_id="inv-restore",
            context_id="ctx-restore",
            context_epoch=0,
            source_retraction_transaction_id="retraction-restore",
            source_joint_plan_id="joint-restore",
            created_ts_ms=0.0,
            path_extent_ids=(),
        )
        requests = []
        for request_id, estimated_tokens in (("large", 10), ("small", 2)):
            metadata = BeliefKVRequestMetadata(
                f"wf-{request_id}",
                f"inv-{request_id}",
                f"ctx-{request_id}",
                0,
            )
            request = SimpleNamespace(
                rid=request_id,
                beliefkv_metadata=metadata,
            )
            requests.append(request)
            controller.process_runtime_events(
                (
                    RuntimeEvent(
                        f"start-{request_id}",
                        1.0,
                        RuntimeEventKind.WORKFLOW_START,
                        metadata.root_workflow_id,
                    ),
                    RuntimeEvent(
                        f"create-{request_id}",
                        2.0,
                        RuntimeEventKind.INVOCATION_CREATE,
                        metadata.root_workflow_id,
                        invocation_id=metadata.invocation_id,
                        context_id=metadata.context_id,
                        context_epoch=0,
                    ),
                )
            )
            controller.register_visible_request(
                AdmissionRequest(
                    request_id,
                    metadata.root_workflow_id,
                    metadata.invocation_id,
                    metadata.context_id,
                    0,
                    10.0,
                    estimated_tokens,
                    0,
                    10,
                )
            )

        self.assertEqual(
            runtime._select_restore_bypass_request(requests, now_ms=1001.0),
            "small",
        )
        runtime.scheduler.running_batch.reqs.append(requests[0])
        self.assertIsNone(
            runtime._select_restore_bypass_request(requests, now_ms=1002.0)
        )
        runtime.scheduler.running_batch.reqs.clear()
        obligation.bypass_count = 1
        self.assertIsNone(
            runtime._select_restore_bypass_request(requests, now_ms=1003.0)
        )

    def test_unready_restore_debt_does_not_block_unrelated_admission(self):
        config = BeliefKVConfig(
            kv_bytes_per_token=10,
            restore_obligation_escalation_ms=1000.0,
            restore_obligation_max_blocked_ms=2000.0,
        )
        controller = BeliefKVController(config)
        metadata = BeliefKVRequestMetadata(
            "wf-ordinary", "inv-ordinary", "ctx-ordinary", 0
        )
        controller.process_runtime_events(
            (
                RuntimeEvent(
                    "start-ordinary",
                    1.0,
                    RuntimeEventKind.WORKFLOW_START,
                    metadata.root_workflow_id,
                ),
                RuntimeEvent(
                    "create-ordinary",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    metadata.root_workflow_id,
                    invocation_id=metadata.invocation_id,
                    context_id=metadata.context_id,
                    context_epoch=0,
                ),
            )
        )
        controller.register_visible_request(
            AdmissionRequest(
                "ordinary",
                metadata.root_workflow_id,
                metadata.invocation_id,
                metadata.context_id,
                0,
                10.0,
                1,
                1,
                10,
            )
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 2000.0
        runtime._retraction_cooldown_until_by_request = {}
        runtime._pending_h2d_contexts = set()
        runtime._restore_bypass_request_id = None
        runtime._context_bundle_generations = lambda _context_id: ()
        runtime._request_restore_bundle_ids = lambda _req, _context_id: ()
        runtime._metadata_scope_is_terminal = lambda _metadata: False
        runtime._workflow_transition_state = lambda _workflow_id: (0, False)
        obligation = runtime._restore_obligation_index().create(
            request_id="restore-target",
            workflow_id="wf-restore",
            invocation_id="inv-restore",
            context_id="ctx-restore",
            context_epoch=0,
            source_retraction_transaction_id="retraction-restore",
            source_joint_plan_id="joint-restore",
            created_ts_ms=0.0,
            path_extent_ids=(),
        )
        runtime._restore_lease_index().grant(
            obligation=obligation,
            granted_ts_ms=1000.0,
            reserved_tokens=1,
            reserved_bytes=10,
            h2d_bytes=0,
        )

        runtime._sync_visible_gate_state(
            "ordinary", metadata, req=SimpleNamespace(rid="ordinary")
        )

        entry = controller.visible_admission.get("ordinary")
        self.assertEqual(entry.state, AdmissionSideState.VISIBLE_PENDING)
        self.assertIsNone(entry.blocker_reason)

    def test_ticket_ready_restore_reopens_when_cpu_path_reappears(self):
        config = BeliefKVConfig(kv_bytes_per_token=10)
        controller = BeliefKVController(config)
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        controller.process_runtime_events(
            (
                RuntimeEvent(
                    "start",
                    1.0,
                    RuntimeEventKind.WORKFLOW_START,
                    metadata.root_workflow_id,
                ),
                RuntimeEvent(
                    "create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    metadata.root_workflow_id,
                    invocation_id=metadata.invocation_id,
                    context_id=metadata.context_id,
                    context_epoch=0,
                ),
            )
        )
        controller.register_visible_request(
            AdmissionRequest(
                "restore",
                metadata.root_workflow_id,
                metadata.invocation_id,
                metadata.context_id,
                0,
                10.0,
                1,
                1,
                10,
            )
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 1000.0
        runtime._retraction_cooldown_until_by_request = {}
        runtime._pending_h2d_contexts = set()
        runtime._context_bundle_generations = lambda _context_id: ()
        runtime._workflow_transition_state = lambda _workflow_id: (0, False)
        runtime._request_path_extent_ids = (
            lambda _req, _context_id, *, cpu_only: ("page:7:0",)
        )
        runtime._extent_ids_bytes = lambda _extent_ids: 4096
        runtime._restore_lease_pins = {"restore": (object(), None, False)}
        runtime._unpin_restore_lease_prefix = mock.Mock()
        runtime._restore_obligation_counts = Counter()
        obligation = runtime._restore_obligation_index().create(
            request_id="restore",
            workflow_id=metadata.root_workflow_id,
            invocation_id=metadata.invocation_id,
            context_id=metadata.context_id,
            context_epoch=0,
            source_retraction_transaction_id="retraction-1",
            source_joint_plan_id="joint-1",
            created_ts_ms=1.0,
            path_extent_ids=("page:1:0",),
        )
        obligation.source_transaction_terminal = True
        obligation.requeued = True
        obligation.mark_ticket_ready(now_ms=900.0)

        runtime._sync_visible_gate_state(
            "restore", metadata, req=SimpleNamespace(rid="restore")
        )

        entry = controller.visible_admission.get("restore")
        transaction = runtime._restore_transactions["restore"]
        self.assertEqual(obligation.state, RestoreObligationState.PARKED_WAIT)
        self.assertEqual(obligation.required_extent_ids, ("page:7:0",))
        self.assertEqual(entry.state, AdmissionSideState.WAIT_RESTORE)
        self.assertEqual(transaction.stage, RestoreTransactionStage.WAIT_FEASIBILITY)
        self.assertIsNone(transaction.prefix_pin_token)
        runtime._unpin_restore_lease_prefix.assert_called_once()
        self.assertEqual(runtime._restore_obligation_counts["ticket_invalidated"], 1)

    def test_restore_service_grace_counts_decode_completion_not_wall_time(self):
        config = BeliefKVConfig(restore_service_grace_decode_tokens=4)
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.audit = _AuditRecorder()
        runtime.scheduler = SimpleNamespace(
            server_args=SimpleNamespace(num_continuous_decode_steps=1)
        )
        runtime._restore_obligation_counts = Counter()
        runtime._restore_service_grace_by_request = {}
        obligation = runtime._restore_obligation_index().create(
            request_id="restored",
            workflow_id="wf",
            invocation_id="inv",
            context_id="ctx",
            context_epoch=0,
            source_retraction_transaction_id="retraction-1",
            source_joint_plan_id="joint-1",
            created_ts_ms=0.0,
            path_extent_ids=(),
        )
        runtime._start_restore_service_grace(obligation, now_ms=1000.0)
        request = SimpleNamespace(rid="restored", output_ids=[])
        batch = SimpleNamespace(reqs=[request])

        request.output_ids.append(1)
        runtime._observe_restore_service_grace(
            batch, now_ms=5000.0, phase="prefill"
        )
        self.assertIn("restored", runtime._restore_service_grace_by_request)
        for offset in range(3):
            request.output_ids.append(offset + 2)
            runtime._observe_restore_service_grace(
                batch, now_ms=6000.0 + offset, phase="decode"
            )
        self.assertIn("restored", runtime._restore_service_grace_by_request)
        request.output_ids.append(5)
        runtime._observe_restore_service_grace(
            batch, now_ms=7000.0, phase="decode"
        )
        self.assertNotIn("restored", runtime._restore_service_grace_by_request)

    def test_short_completed_restore_closes_service_grace_transaction(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(restore_service_grace_decode_tokens=32)
        runtime.audit = _AuditRecorder()
        runtime._restore_obligation_counts = Counter()
        runtime._restore_service_grace_by_request = {}
        obligation = runtime._restore_obligation_index().create(
            request_id="short-response",
            workflow_id="wf",
            invocation_id="inv",
            context_id="ctx",
            context_epoch=0,
            source_retraction_transaction_id="retraction-1",
            source_joint_plan_id="joint-1",
            created_ts_ms=0.0,
            path_extent_ids=(),
        )
        runtime._start_restore_service_grace(obligation, now_ms=1.0)
        obligation.finish(
            RestoreObligationState.SATISFIED,
            now_ms=2.0,
            reason="gpu_service_resumed",
        )

        runtime._cancel_restore_service_grace(
            obligation.request_id,
            now_ms=3.0,
            reason="request_finished",
        )

        transaction = runtime._ensure_restore_transaction(obligation)
        self.assertEqual(transaction.stage, RestoreTransactionStage.SATISFIED)
        self.assertNotIn(
            obligation.request_id, runtime._restore_service_grace_by_request
        )
        terminal = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "restore_service_grace_terminal"
        ]
        self.assertEqual(terminal[-1]["restore_transaction_stage"], "satisfied")

    def test_restore_obligation_funds_h2d_then_releases_ticket(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=2000,
            host_capacity_bytes=4000,
            reserve_hbm_bytes=100,
            kv_bytes_per_token=10,
            observed_admission_scheduling_enabled=True,
            running_batch_retraction_enabled=True,
        )
        controller = BeliefKVController(config)
        events = [
            RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf")
        ]
        for suffix in ("target", "victim"):
            events.append(
                RuntimeEvent(
                    f"create-{suffix}",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id=f"inv-{suffix}",
                    context_id=f"ctx-{suffix}",
                    context_epoch=0,
                )
            )
        controller.process_runtime_events(tuple(events))
        tree_cache = _TreeCache()
        target_node = _Node(21)
        target_node.parent = tree_cache.root_node
        registry = SGLangNodeRegistry()
        target_handle = registry.register(target_node)
        victim_handle = PageHandle(22, 0)
        controller.page_index.register_page(
            target_handle,
            size_bytes=200,
            residency=PhysicalResidency.CPU_ONLY,
            radix_depth=1,
        )
        controller.page_index.register_page(
            victim_handle,
            size_bytes=200,
            residency=PhysicalResidency.DUAL_CLEAN,
            radix_depth=1,
        )
        controller.page_index.bind_pages("ctx-target", 0, (target_handle,))
        controller.page_index.bind_pages("ctx-victim", 0, (victim_handle,))
        metadata = BeliefKVRequestMetadata("wf", "inv-target", "ctx-target", 0)
        request = SimpleNamespace(
            rid="target",
            beliefkv_metadata=metadata,
            origin_input_ids=(1, 2),
            output_ids=(),
            prefix_indices=(),
            sampling_params=SimpleNamespace(max_new_tokens=8),
            last_node=target_node,
            init_next_round_input=lambda _cache: None,
        )
        allocator = _Allocator(10)
        scheduler = SimpleNamespace(
            waiting_queue=[request],
            token_to_kv_pool_allocator=allocator,
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.scheduler = scheduler
        runtime.tree_cache = tree_cache
        runtime.registry = registry
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 1000.0
        runtime._request_metadata_by_id = {"target": metadata}
        runtime._request_submitted_ts_by_id = {"target": 10.0}
        runtime._retraction_cooldown_until_by_request = {}
        runtime._pending_h2d_contexts = set()
        runtime._runtime_resource_observation = lambda **_kwargs: SimpleNamespace(
            host_free_bytes=4000
        )
        controller.register_visible_request(
            AdmissionRequest(
                "target", "wf", "inv-target", "ctx-target", 0, 10.0, 1, 1, 10
            )
        )
        obligation = runtime._restore_obligation_index().create(
            request_id="target",
            workflow_id="wf",
            invocation_id="inv-target",
            context_id="ctx-target",
            context_epoch=0,
            source_retraction_transaction_id="retraction-1",
            source_joint_plan_id="joint-1",
            created_ts_ms=900.0,
            path_extent_ids=(f"page:{target_handle.page_id}:0",),
        )
        obligation.source_transaction_terminal = True
        obligation.requeued = True

        runtime._drive_restore_obligations(now_ms=1000.0)
        funding = controller.command_queue.pop()
        self.assertIsNotNone(funding, runtime.audit.events)
        self.assertEqual(funding.kind, CommandKind.OFFLOAD_CONTEXT)
        self.assertEqual(funding.context_id, "ctx-victim")
        controller._queued_by_context.pop("ctx-victim", None)
        controller.page_index.commit_cpu(victim_handle)
        allocator.available_tokens = 30
        runtime._advance_restore_obligations(
            (
                CommandAck(
                    funding.command_id,
                    CommandStatus.COMPLETED,
                    1001.0,
                    actual_bytes=200,
                    page_handles=(victim_handle,),
                ),
            ),
            now_ms=1001.0,
        )
        self.assertEqual(allocator.available_tokens, 18)
        self.assertEqual(obligation.funding_reserved_tokens, 12)
        self.assertEqual(obligation.funding_reserved_bytes, 120)

        runtime._drive_restore_obligations(now_ms=1002.0)
        restore = controller.command_queue.pop()
        self.assertIsNotNone(restore)
        self.assertEqual(restore.kind, CommandKind.PREFETCH_CONTEXT)
        self.assertEqual(restore.context_id, "ctx-target")
        self.assertEqual(allocator.available_tokens, 28)
        self.assertEqual(obligation.funding_reserved_tokens, 0)
        self.assertEqual(obligation.funding_reserved_bytes, 0)
        lease = runtime._restore_lease_index().get("target")
        self.assertIsNotNone(lease)
        self.assertEqual(lease.reserved_tokens, 2)
        # Model the H2D allocator claim followed by concurrent decode growth.
        # The competing requests can consume every unreserved token, but not
        # the two-token restore admission lease.
        self.assertIsNotNone(allocator.alloc(20))
        self.assertIsNotNone(allocator.alloc(8))
        self.assertIsNone(allocator.alloc(1))
        controller._queued_by_context.pop("ctx-target", None)
        controller.page_index.begin_transfer(target_handle, TransferDirection.H2D)
        controller.page_index.complete_transfer(
            target_handle, TransferDirection.H2D
        )
        runtime._advance_restore_obligations(
            (
                CommandAck(
                    restore.command_id,
                    CommandStatus.COMPLETED,
                    1003.0,
                    actual_bytes=200,
                    page_handles=(target_handle,),
                ),
            ),
            now_ms=1003.0,
        )
        runtime._drive_restore_obligations(now_ms=1003.1)
        runtime._sync_visible_gate_state("target", metadata, req=request)

        entry = controller.visible_admission.get("target")
        self.assertEqual(entry.state, AdmissionSideState.VISIBLE_PENDING)
        self.assertEqual(obligation.state, RestoreObligationState.TICKET_READY)
        self.assertEqual(lease.state, RestoreLeaseState.RESTORED_RESERVED)
        self.assertEqual(target_node.lock_ref, 1)

        self.assertTrue(
            runtime._begin_restore_lease_admission(obligation, now_ms=1003.5)
        )
        self.assertEqual(allocator.available_tokens, 2)
        runtime._reject_restore_lease_admission(
            obligation, now_ms=1003.6, native_result="NO_TOKEN"
        )
        self.assertEqual(allocator.available_tokens, 0)
        self.assertEqual(lease.state, RestoreLeaseState.RESTORED_RESERVED)

        self.assertTrue(
            runtime._begin_restore_lease_admission(obligation, now_ms=1003.7)
        )
        runtime._commit_restore_lease_admission(obligation, now_ms=1003.8)
        self.assertEqual(allocator.available_tokens, 2)
        self.assertEqual(lease.state, RestoreLeaseState.ADMITTED)
        self.assertEqual(target_node.lock_ref, 0)
        runtime._finish_restore_obligation(
            "target",
            RestoreObligationState.SATISFIED,
            now_ms=1004.0,
            reason="gpu_service_resumed",
        )
        self.assertEqual(obligation.state, RestoreObligationState.SATISFIED)
        self.assertEqual(lease.state, RestoreLeaseState.RELEASED)

    def test_admission_only_restore_reclaims_funding_before_granting_lease(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=2000,
            host_capacity_bytes=4000,
            reserve_hbm_bytes=100,
            kv_bytes_per_token=10,
            observed_admission_scheduling_enabled=True,
        )
        controller = BeliefKVController(config)
        events = [RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf")]
        for suffix in ("target", "victim"):
            events.append(
                RuntimeEvent(
                    f"create-{suffix}",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id=f"inv-{suffix}",
                    context_id=f"ctx-{suffix}",
                    context_epoch=0,
                )
            )
        controller.process_runtime_events(tuple(events))
        tree_cache = _TreeCache()
        target_node = _Node(41)
        target_node.parent = tree_cache.root_node
        registry = SGLangNodeRegistry()
        target_handle = registry.register(target_node)
        victim_handle = PageHandle(42, 0)
        controller.page_index.register_page(
            target_handle,
            size_bytes=200,
            residency=PhysicalResidency.GPU_ONLY,
            radix_depth=1,
        )
        controller.page_index.register_page(
            victim_handle,
            size_bytes=200,
            residency=PhysicalResidency.DUAL_CLEAN,
            radix_depth=1,
        )
        controller.page_index.bind_pages("ctx-target", 0, (target_handle,))
        controller.page_index.bind_pages("ctx-victim", 0, (victim_handle,))
        metadata = BeliefKVRequestMetadata("wf", "inv-target", "ctx-target", 0)
        request = SimpleNamespace(
            rid="target",
            beliefkv_metadata=metadata,
            origin_input_ids=(1, 2),
            output_ids=(),
            prefix_indices=(),
            sampling_params=SimpleNamespace(max_new_tokens=8),
            last_node=target_node,
            init_next_round_input=lambda _cache: None,
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[request],
            token_to_kv_pool_allocator=_Allocator(1),
        )
        runtime.tree_cache = tree_cache
        runtime.registry = registry
        runtime.audit = _AuditRecorder()
        runtime._request_metadata_by_id = {"target": metadata}
        runtime._request_submitted_ts_by_id = {"target": 10.0}
        runtime._retraction_cooldown_until_by_request = {}
        runtime._pending_h2d_contexts = set()
        runtime._runtime_resource_observation = lambda **_kwargs: SimpleNamespace(
            host_free_bytes=4000
        )
        controller.register_visible_request(
            AdmissionRequest(
                "target", "wf", "inv-target", "ctx-target", 0, 10.0, 1, 1, 10
            )
        )
        obligation = runtime._restore_obligation_index().create(
            request_id="target",
            workflow_id="wf",
            invocation_id="inv-target",
            context_id="ctx-target",
            context_epoch=0,
            source_retraction_transaction_id="retraction-target",
            source_joint_plan_id="joint-target",
            created_ts_ms=900.0,
            path_extent_ids=(f"page:{target_handle.page_id}:0",),
        )
        obligation.source_transaction_terminal = True
        obligation.requeued = True

        runtime._drive_restore_obligations(now_ms=1000.0)

        funding = controller.command_queue.pop()
        self.assertIsNotNone(funding, runtime.audit.events)
        self.assertEqual(funding.kind, CommandKind.OFFLOAD_CONTEXT)
        self.assertEqual(funding.context_id, "ctx-victim")
        self.assertEqual(
            funding.metadata["reason"], "restore_obligation_funding"
        )
        self.assertEqual(
            obligation.state, RestoreObligationState.EVICT_FOR_RESTORE
        )
        self.assertIsNone(runtime._restore_lease_index().get("target"))

    def test_allocator_backed_reservations_include_funding_and_lease_slots(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._restore_funding_allocations = {
            "request-a": [[1, 2, 3], [4, 5]],
        }
        runtime._restore_lease_allocations = {
            "request-b": [[6, 7, 8, 9]],
        }

        self.assertEqual(runtime.allocator_backed_reservation_tokens(), 9)

    def test_full_restore_lease_table_does_not_churn_funding_capacity(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            kv_bytes_per_token=10,
            restore_lease_enabled=True,
            restore_lease_max_active=1,
        )
        runtime.audit = _AuditRecorder()
        allocator = _Allocator(100)
        runtime.scheduler = SimpleNamespace(
            token_to_kv_pool_allocator=allocator,
        )
        obligations = runtime._restore_obligation_index()
        first = obligations.create(
            request_id="first",
            workflow_id="wf-first",
            invocation_id="inv-first",
            context_id="ctx-first",
            context_epoch=0,
            source_retraction_transaction_id="retraction-first",
            source_joint_plan_id="joint-first",
            created_ts_ms=1.0,
            path_extent_ids=(),
        )
        second = obligations.create(
            request_id="second",
            workflow_id="wf-second",
            invocation_id="inv-second",
            context_id="ctx-second",
            context_epoch=0,
            source_retraction_transaction_id="retraction-second",
            source_joint_plan_id="joint-second",
            created_ts_ms=2.0,
            path_extent_ids=(),
        )
        runtime._restore_lease_index().grant(
            obligation=first,
            granted_ts_ms=3.0,
            reserved_tokens=1,
            reserved_bytes=10,
            h2d_bytes=0,
        )
        funding = allocator.alloc(12)
        self.assertIsNotNone(funding)
        runtime._set_restore_funding_reservation(second, [funding])
        available_before = allocator.available_tokens

        lease = runtime._grant_restore_lease(
            second,
            h2d_bytes=0,
            now_ms=4.0,
        )

        self.assertIsNone(lease)
        self.assertEqual(allocator.available_tokens, available_before)
        self.assertEqual(runtime._restore_funding_reserved_tokens("second"), 12)
        self.assertFalse(
            any(
                event == "restore_funding_capacity_released"
                for event, _, _ in runtime.audit.events
            )
        )

    def test_failed_restore_lease_grant_waits_for_active_lease(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(
            kv_bytes_per_token=10,
            restore_lease_enabled=True,
            restore_lease_max_active=1,
        )
        obligations = runtime._restore_obligation_index()
        active = obligations.create(
            request_id="active",
            workflow_id="wf-active",
            invocation_id="inv-active",
            context_id="ctx-active",
            context_epoch=0,
            source_retraction_transaction_id="retraction-active",
            source_joint_plan_id="joint-active",
            created_ts_ms=1.0,
            path_extent_ids=(),
        )
        blocked = obligations.create(
            request_id="blocked",
            workflow_id="wf-blocked",
            invocation_id="inv-blocked",
            context_id="ctx-blocked",
            context_epoch=0,
            source_retraction_transaction_id="retraction-blocked",
            source_joint_plan_id="joint-blocked",
            created_ts_ms=2.0,
            path_extent_ids=("page:1:0",),
        )
        runtime._restore_lease_index().grant(
            obligation=active,
            granted_ts_ms=3.0,
            reserved_tokens=1,
            reserved_bytes=10,
            h2d_bytes=10,
        )
        runtime._try_queue_restore_lease_funding = lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(AssertionError("must not reclaim for a busy lease"))
        )

        queued, blockers, wake_conditions = (
            runtime._recover_failed_restore_lease_grant(
                blocked,
                now_ms=4.0,
                attempt_stamp=(1,),
                h2d_bytes=20,
            )
        )

        self.assertFalse(queued)
        self.assertEqual(blockers, ("restore_lease_busy",))
        self.assertEqual(wake_conditions, ("restore_lease_terminal",))

    def test_failed_h2d_restore_lease_grant_reclaims_debt_owned_funding(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = BeliefKVConfig(kv_bytes_per_token=10)
        obligation = runtime._restore_obligation_index().create(
            request_id="target",
            workflow_id="wf",
            invocation_id="inv",
            context_id="ctx",
            context_epoch=0,
            source_retraction_transaction_id="retraction",
            source_joint_plan_id="joint",
            created_ts_ms=1.0,
            path_extent_ids=("page:1:0",),
        )
        calls = []
        runtime._try_queue_restore_lease_funding = (
            lambda candidate, **kwargs: (
                calls.append((candidate.request_id, kwargs["h2d_bytes"])) or True,
                (),
            )
        )

        queued, blockers, wake_conditions = (
            runtime._recover_failed_restore_lease_grant(
                obligation,
                now_ms=2.0,
                attempt_stamp=(1,),
                h2d_bytes=200,
            )
        )

        self.assertTrue(queued)
        self.assertEqual(blockers, ())
        self.assertEqual(calls, [("target", 200)])
        self.assertIn("restore_funding_terminal", wake_conditions)

    def test_restore_prefix_pin_waits_for_host_only_h2d(self):
        config = BeliefKVConfig(kv_bytes_per_token=10)
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.tree_cache = _TreeCache()
        runtime.audit = _AuditRecorder()
        runtime._restore_obligation_counts = Counter()
        runtime._restore_lease_pins = {}
        obligation = runtime._restore_obligation_index().create(
            request_id="request",
            workflow_id="wf",
            invocation_id="inv",
            context_id="ctx",
            context_epoch=0,
            source_retraction_transaction_id="retraction-1",
            source_joint_plan_id="joint-1",
            created_ts_ms=1.0,
            path_extent_ids=("page:1:0",),
        )
        runtime._restore_lease_index().grant(
            obligation=obligation,
            granted_ts_ms=2.0,
            reserved_tokens=4,
            reserved_bytes=40,
            h2d_bytes=40,
        )
        node = _Node(1)
        node.parent = runtime.tree_cache.root_node
        node.host_value = [10, 11, 12, 13]
        node.value = None
        request = SimpleNamespace(rid="request", last_node=node)

        self.assertTrue(
            runtime._pin_restore_lease_prefix(
                obligation,
                request,
                now_ms=3.0,
                allow_unmaterialized=True,
            )
        )
        self.assertNotIn("request", runtime._restore_lease_pins)
        self.assertFalse(
            runtime._pin_restore_lease_prefix(
                obligation,
                request,
                now_ms=4.0,
            )
        )

        node.value = [1, 2, 3, 4]
        self.assertTrue(
            runtime._pin_restore_lease_prefix(
                obligation,
                request,
                now_ms=5.0,
            )
        )
        self.assertIn("request", runtime._restore_lease_pins)
        self.assertEqual(node.lock_ref, 1)

    def test_blocked_restore_head_does_not_starve_later_obligation(self):
        config = BeliefKVConfig(
            kv_bytes_per_token=10,
            restore_obligation_max_active=2,
            restore_lease_enabled=True,
        )
        controller = BeliefKVController(config)
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        runtime._request_metadata_by_id = {}
        obligations = runtime._restore_obligation_index()
        head = obligations.create(
            request_id="head",
            workflow_id="wf-head",
            invocation_id="inv-head",
            context_id="ctx-head",
            context_epoch=0,
            source_retraction_transaction_id="retraction-head",
            source_joint_plan_id="joint-head",
            created_ts_ms=1.0,
            path_extent_ids=(),
        )
        tail = obligations.create(
            request_id="tail",
            workflow_id="wf-tail",
            invocation_id="inv-tail",
            context_id="ctx-tail",
            context_epoch=0,
            source_retraction_transaction_id="retraction-tail",
            source_joint_plan_id="joint-tail",
            created_ts_ms=2.0,
            path_extent_ids=(),
        )
        for obligation in (head, tail):
            obligation.source_transaction_terminal = True
            obligation.requeued = True
        runtime._refresh_restore_obligation = (
            lambda obligation, **_kwargs: (
                SimpleNamespace(rid=obligation.request_id),
                (),
            )
        )
        runtime._restore_attempt_stamp = lambda: (1, 1, 0, 0)
        tail_lease = SimpleNamespace(mark_restored=lambda: None)
        runtime._grant_restore_lease = (
            lambda obligation, **_kwargs: (
                None if obligation.request_id == "head" else tail_lease
            )
        )
        funding_attempts = []
        runtime._try_queue_restore_lease_funding = (
            lambda obligation, **_kwargs: (
                funding_attempts.append(obligation.request_id) or False,
                ("restore_lease_capacity", "no_funding_bundle"),
            )
        )
        runtime._pin_restore_lease_prefix = lambda *_args, **_kwargs: True
        runtime._sync_visible_gate_state = lambda *_args, **_kwargs: None

        runtime._drive_restore_obligations(now_ms=1000.0)

        self.assertEqual(head.state, RestoreObligationState.PARKED_WAIT)
        self.assertEqual(
            head.blocker_codes,
            ("no_funding_bundle", "restore_lease_capacity"),
        )
        self.assertEqual(tail.state, RestoreObligationState.TICKET_READY)
        self.assertEqual(funding_attempts, ["head"])
        self.assertTrue(
            any(
                event == "restore_obligation_ticket_ready"
                and fields["request_id"] == "tail"
                for event, _, fields in runtime.audit.events
            )
        )

        runtime._drive_restore_obligations(now_ms=1001.0)

        self.assertEqual(funding_attempts, ["head"])

    def test_ordinary_waiting_cpu_prefix_uses_native_admission_without_debt(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=2000,
            host_capacity_bytes=4000,
            reserve_hbm_bytes=100,
            kv_bytes_per_token=10,
            observed_admission_scheduling_enabled=True,
        )
        controller = BeliefKVController(config)
        controller.process_runtime_events(
            (
                RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
                RuntimeEvent(
                    "create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        tree_cache = _TreeCache()
        node = _Node(31)
        node.parent = tree_cache.root_node
        registry = SGLangNodeRegistry()
        handle = registry.register(node)
        controller.page_index.register_page(
            handle,
            size_bytes=200,
            residency=PhysicalResidency.CPU_ONLY,
            radix_depth=1,
        )
        controller.page_index.bind_pages("ctx", 0, (handle,))
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(
            rid="waiting",
            beliefkv_metadata=metadata,
            origin_input_ids=(1, 2),
            output_ids=(),
            prefix_indices=(),
            sampling_params=SimpleNamespace(max_new_tokens=8),
            last_node=node,
            init_next_round_input=lambda _cache: None,
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[request],
            token_to_kv_pool_allocator=_Allocator(100),
        )
        runtime.tree_cache = tree_cache
        runtime.registry = registry
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 1000.0
        runtime._request_metadata_by_id = {"waiting": metadata}
        runtime._request_submitted_ts_by_id = {"waiting": 10.0}
        runtime._retraction_cooldown_until_by_request = {}
        runtime._pending_h2d_contexts = set()
        controller.register_visible_request(
            AdmissionRequest(
                "waiting", "wf", "inv", "ctx", 0, 10.0, 1, 1, 10
            )
        )

        runtime._sync_visible_gate_state("waiting", metadata, req=request)

        self.assertIsNone(runtime._restore_obligation_index().get("waiting"))
        self.assertEqual(getattr(runtime, "_restore_transactions", {}), {})
        entry = controller.visible_admission.get("waiting")
        self.assertEqual(entry.state, AdmissionSideState.VISIBLE_PENDING)
        self.assertIsNone(controller.command_queue.pop())
        delegated = [
            fields
            for event, _, fields in runtime.audit.events
            if event == "ordinary_waiting_prefix_delegated_to_native"
        ]
        self.assertEqual(len(delegated), 1)
        self.assertFalse(delegated[0]["durable_obligation_created"])
        self.assertFalse(delegated[0]["restore_priority_created"])
        self.assertEqual(
            delegated[0]["capacity_authority"], "sglang_prefill_adder"
        )
        runtime._sync_visible_gate_state("waiting", metadata, req=request)
        self.assertEqual(
            sum(
                event == "ordinary_waiting_prefix_delegated_to_native"
                for event, _, _ in runtime.audit.events
            ),
            1,
        )

    def test_ordinary_native_fallback_rebinds_current_request_path(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=2000,
            host_capacity_bytes=4000,
            reserve_hbm_bytes=100,
            kv_bytes_per_token=10,
            observed_admission_scheduling_enabled=True,
        )
        controller = BeliefKVController(config)
        controller.process_runtime_events(
            (
                RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
                RuntimeEvent(
                    "create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        tree_cache = _TreeCache()
        stale_node = _Node(31)
        stale_node.parent = tree_cache.root_node
        current_node = _Node(32)
        current_node.parent = tree_cache.root_node
        registry = SGLangNodeRegistry()
        stale_handle = registry.register(stale_node)
        current_handle = registry.register(current_node)
        for handle in (stale_handle, current_handle):
            controller.page_index.register_page(
                handle,
                size_bytes=200,
                residency=PhysicalResidency.CPU_ONLY,
                radix_depth=1,
            )
        controller.page_index.bind_pages("ctx", 0, (stale_handle,))
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(
            rid="waiting",
            beliefkv_metadata=metadata,
            origin_input_ids=(1, 2),
            output_ids=(),
            prefix_indices=(),
            sampling_params=SimpleNamespace(max_new_tokens=8),
            last_node=current_node,
            init_next_round_input=lambda _cache: None,
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[request],
            token_to_kv_pool_allocator=_Allocator(100),
        )
        runtime.tree_cache = tree_cache
        runtime.registry = registry
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 1000.0
        runtime._request_metadata_by_id = {"waiting": metadata}
        runtime._request_submitted_ts_by_id = {"waiting": 10.0}
        runtime._retraction_cooldown_until_by_request = {}
        runtime._pending_h2d_contexts = set()
        controller.register_visible_request(
            AdmissionRequest(
                "waiting", "wf", "inv", "ctx", 0, 10.0, 1, 1, 10
            )
        )

        runtime._sync_visible_gate_state("waiting", metadata, req=request)

        owned_handles = {
            page.handle for page in controller.page_index.context_pages("ctx")
        }
        self.assertEqual(owned_handles, {current_handle})
        self.assertNotIn(
            "ctx", controller.page_index.pages[stale_handle].owner_contexts
        )
        self.assertIsNone(runtime._restore_obligation_index().get("waiting"))
        self.assertIsNone(controller.command_queue.pop())
        self.assertTrue(
            any(
                event == "waiting_request_path_rebound"
                for event, _, _ in runtime.audit.events
            )
        )

    def test_ordinary_native_fallback_does_not_consume_restore_capacity(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=2000,
            host_capacity_bytes=4000,
            reserve_hbm_bytes=100,
            kv_bytes_per_token=10,
            observed_admission_scheduling_enabled=True,
        )
        controller = BeliefKVController(config)
        controller.process_runtime_events(
            (
                RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
                RuntimeEvent(
                    "create",
                    2.0,
                    RuntimeEventKind.INVOCATION_CREATE,
                    "wf",
                    invocation_id="inv",
                    context_id="ctx",
                    context_epoch=0,
                ),
            )
        )
        tree_cache = _TreeCache()
        node = _Node(31)
        node.parent = tree_cache.root_node
        registry = SGLangNodeRegistry()
        handle = registry.register(node)
        controller.page_index.register_page(
            handle,
            size_bytes=200,
            residency=PhysicalResidency.CPU_ONLY,
            radix_depth=1,
        )
        controller.page_index.bind_pages("ctx", 0, (handle,))
        controller.arbiter.bundle_builder = SimpleNamespace(
            previews_for_context=lambda *_args, **_kwargs: ()
        )
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        request = SimpleNamespace(
            rid="waiting",
            beliefkv_metadata=metadata,
            origin_input_ids=(1, 2),
            output_ids=(),
            prefix_indices=(),
            sampling_params=SimpleNamespace(max_new_tokens=8),
            last_node=node,
            init_next_round_input=lambda _cache: None,
        )
        allocator = _Allocator(100)
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.scheduler = SimpleNamespace(
            waiting_queue=[request],
            token_to_kv_pool_allocator=allocator,
        )
        runtime.tree_cache = tree_cache
        runtime.registry = registry
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 1000.0
        runtime._request_metadata_by_id = {"waiting": metadata}
        runtime._request_submitted_ts_by_id = {"waiting": 10.0}
        runtime._retraction_cooldown_until_by_request = {}
        runtime._pending_h2d_contexts = set()
        runtime._restore_h2d_previews = lambda *_args, **_kwargs: (
            SimpleNamespace(
                eligible=False,
                blockers=(
                    SimpleNamespace(code=TransferBlockerCode.DEVICE_CAPACITY),
                ),
            ),
        )
        controller.register_visible_request(
            AdmissionRequest(
                "waiting", "wf", "inv", "ctx", 0, 10.0, 1, 1, 10
            )
        )

        runtime._sync_visible_gate_state("waiting", metadata, req=request)
        obligation = runtime._restore_obligation_index().get("waiting")
        lease = runtime._restore_lease_index().get("waiting")
        self.assertIsNone(obligation)
        self.assertIsNone(lease)
        self.assertIsNone(controller.command_queue.pop())
        self.assertEqual(
            controller.visible_admission.get("waiting").state,
            AdmissionSideState.VISIBLE_PENDING,
        )
        fallback_event = next(
            fields
            for event, _, fields in runtime.audit.events
            if event == "ordinary_waiting_prefix_delegated_to_native"
        )
        self.assertEqual(fallback_event["required_extent_count"], 1)
        self.assertEqual(fallback_event["restore_bytes"], 200)
        self.assertEqual(
            fallback_event["capacity_authority"], "sglang_prefill_adder"
        )
        self.assertFalse(fallback_event["durable_obligation_created"])
        for index in range(8):
            runtime._restore_obligation_index().create(
                request_id=f"retracted-{index}",
                workflow_id=f"wf-{index}",
                invocation_id=f"inv-{index}",
                context_id=f"ctx-{index}",
                context_epoch=0,
                source_retraction_transaction_id=f"retraction-{index}",
                source_joint_plan_id=f"joint-{index}",
                created_ts_ms=float(index),
                path_extent_ids=(),
            )
        self.assertEqual(len(runtime._restore_obligation_index().active()), 8)

    def test_ordinary_restore_debt_never_becomes_global_barrier(self):
        config = BeliefKVConfig(
            restore_obligation_escalation_ms=1000.0,
        )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config

        ordinary = runtime._restore_obligation_index().create(
            request_id="ordinary",
            workflow_id="wf-ordinary",
            invocation_id="inv-ordinary",
            context_id="ctx-ordinary",
            context_epoch=0,
            source_retraction_transaction_id="ordinary-waiting:ordinary",
            source_joint_plan_id="joint-ordinary",
            created_ts_ms=0.0,
            path_extent_ids=(),
            cause=RestoreObligationCause.ORDINARY_WAITING_PREFIX,
        )

        self.assertIsNone(
            runtime._overdue_restore_obligation(now_ms=2000.0)
        )

        retracted = runtime._restore_obligation_index().create(
            request_id="retracted",
            workflow_id="wf-retracted",
            invocation_id="inv-retracted",
            context_id="ctx-retracted",
            context_epoch=0,
            source_retraction_transaction_id="retraction-1",
            source_joint_plan_id="joint-retracted",
            created_ts_ms=100.0,
            path_extent_ids=(),
        )
        self.assertIs(
            runtime._overdue_restore_obligation(now_ms=2000.0),
            retracted,
        )
        self.assertEqual(
            ordinary.cause, RestoreObligationCause.ORDINARY_WAITING_PREFIX
        )

    def test_restore_and_native_fallback_liveness_override_stale_joint_order(self):
        config = BeliefKVConfig(
            hbm_capacity_bytes=1000,
            reserve_hbm_bytes=0,
            kv_bytes_per_token=10,
            joint_policy_enabled=True,
            restore_lease_enabled=False,
        )
        controller = BeliefKVController(config)
        for suffix in ("old", "new"):
            controller.process_runtime_events(
                (
                    RuntimeEvent(
                        f"start-{suffix}",
                        1.0,
                        RuntimeEventKind.WORKFLOW_START,
                        f"wf-{suffix}",
                    ),
                    RuntimeEvent(
                        f"create-{suffix}",
                        2.0,
                        RuntimeEventKind.INVOCATION_CREATE,
                        f"wf-{suffix}",
                        invocation_id=f"inv-{suffix}",
                        context_id=f"ctx-{suffix}",
                        context_epoch=0,
                    ),
                )
            )
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.config = config
        runtime.controller = controller
        runtime.audit = _AuditRecorder()
        runtime._now_ms = lambda: 100.0
        runtime._admission_epoch = 0
        runtime._current_ticket_epoch = None
        runtime._current_tickets_by_request = {}
        runtime._ticket_attempted_request_ids = set()
        runtime._ticket_selected_request_ids = set()
        runtime._ticket_skip_audit = set()
        runtime._ticket_selection_details = {}
        runtime._ticket_native_rejections = {}
        runtime._pending_h2d_contexts = set()
        runtime._request_metadata_by_id = {}
        runtime._request_submitted_ts_by_id = {}
        runtime._current_online_joint_decision = None
        runtime._online_joint_result = None
        runtime._current_joint_plan_epoch = None
        runtime._online_joint_counts = Counter()
        runtime._online_joint_epoch_sequence = 0
        runtime._restore_obligations = RestoreObligationIndex(max_active=2)
        obligation = runtime._restore_obligations.create(
            request_id="oldest",
            workflow_id="wf-old",
            invocation_id="inv-old",
            context_id="ctx-old",
            context_epoch=0,
            source_retraction_transaction_id="ordinary-waiting:oldest",
            source_joint_plan_id="joint-old",
            created_ts_ms=1.0,
            path_extent_ids=("page:1:0",),
            cause=RestoreObligationCause.ORDINARY_WAITING_PREFIX,
        )
        obligation.source_transaction_terminal = True
        obligation.requeued = True
        obligation.mark_ticket_ready(now_ms=2.0)
        requests = []
        for request_id, suffix, submitted_ts_ms in (
            ("oldest", "old", 1.0),
            ("newer", "new", 2.0),
        ):
            metadata = BeliefKVRequestMetadata(
                f"wf-{suffix}", f"inv-{suffix}", f"ctx-{suffix}", 0
            )
            request = SimpleNamespace(
                rid=request_id,
                beliefkv_metadata=metadata,
                origin_input_ids=(1,),
                prefix_indices=(),
                last_node=None,
            )
            requests.append(request)
            runtime._request_metadata_by_id[request_id] = metadata
            runtime._request_submitted_ts_by_id[request_id] = submitted_ts_ms
            controller.register_visible_request(
                AdmissionRequest(
                    request_id,
                    f"wf-{suffix}",
                    f"inv-{suffix}",
                    f"ctx-{suffix}",
                    0,
                    submitted_ts_ms,
                    1,
                    1,
                    10,
                )
            )
        stale_view = OnlineJointPlanView(
            plan_id="stale-plan",
            ordered_request_ids=("newer",),
            immediate_request_ids=("newer",),
            restore_requirements=(),
            deferred_request_ids=("oldest",),
            residency_intent_indices=(),
        )
        runtime._online_joint_admission_decision = lambda **_kwargs: (
            OnlineJointPlanDecision(stale_view, "applicable")
        )

        runtime.begin_prefill_epoch(
            requests,
            SimpleNamespace(
                rem_input_tokens=10,
                rem_chunk_tokens=None,
                rem_total_tokens=100,
            ),
            max_requests=2,
        )

        self.assertEqual(
            [
                ticket.request_id
                for ticket in runtime._current_ticket_epoch.tickets
            ],
            ["oldest", "newer"],
        )
        self.assertIn(
            "restore_liveness", runtime._current_ticket_epoch.source
        )
        self.assertNotEqual(
            runtime._current_online_joint_view.plan_id, "stale-plan"
        )
        self.assertEqual(
            runtime._current_online_joint_view.immediate_request_ids,
            ("oldest", "newer"),
        )

        runtime.end_prefill_epoch(())
        obligation.use_native_admission_fallback(now_ms=101.0)
        runtime.begin_prefill_epoch(
            requests,
            SimpleNamespace(
                rem_input_tokens=10,
                rem_chunk_tokens=None,
                rem_total_tokens=100,
            ),
            max_requests=2,
        )

        self.assertEqual(
            [
                ticket.request_id
                for ticket in runtime._current_ticket_epoch.tickets
            ],
            ["newer"],
        )
        self.assertNotIn(
            "restore_liveness", runtime._current_ticket_epoch.source
        )

        runtime.end_prefill_epoch(())
        runtime._ordinary_native_fallback_signature_by_request = {
            "oldest": ("ctx-old", 0, ("page:1:0",)),
            "newer": ("ctx-new", 0, ("page:2:0",)),
        }
        obligation_count_before_promotion = len(
            runtime._restore_obligations.all()
        )
        available_tokens = [100]
        runtime.scheduler = SimpleNamespace(
            token_to_kv_pool_allocator=SimpleNamespace(
                available_size=lambda: available_tokens[0],
            )
        )
        now_ms = [40_050.0]
        runtime._now_ms = lambda: now_ms[0]

        runtime.begin_prefill_epoch(
            requests,
            SimpleNamespace(
                rem_input_tokens=10,
                rem_chunk_tokens=None,
                rem_total_tokens=100,
            ),
            max_requests=1,
        )
        self.assertEqual(
            [
                ticket.request_id
                for ticket in runtime._current_ticket_epoch.tickets
            ],
            ["oldest"],
        )
        self.assertIn(
            "ordinary_starvation", runtime._current_ticket_epoch.source
        )
        self.assertEqual(
            runtime._current_ordinary_starvation_priority, ("oldest",)
        )
        self.assertEqual(
            len(runtime._restore_obligations.all()),
            obligation_count_before_promotion,
        )
        self.assertEqual(runtime._restore_lease_index().active(), ())
        self.assertEqual(
            getattr(runtime, "_restore_funding_allocations", {}), {}
        )
        self.assertEqual(
            getattr(
                runtime,
                "_restore_authority_mode",
                RestoreAuthorityMode.NORMAL_JOINT,
            ),
            RestoreAuthorityMode.NORMAL_JOINT,
        )
        runtime.on_prefill_candidate_result(
            requests[0], admitted=False, result="NO_TOKEN"
        )
        runtime.end_prefill_epoch(())

        runtime.begin_prefill_epoch(
            requests,
            SimpleNamespace(
                rem_input_tokens=10,
                rem_chunk_tokens=None,
                rem_total_tokens=100,
            ),
            max_requests=1,
        )
        self.assertEqual(
            [
                ticket.request_id
                for ticket in runtime._current_ticket_epoch.tickets
            ],
            ["newer"],
        )
        runtime.on_prefill_candidate_result(
            requests[1], admitted=False, result="NO_TOKEN"
        )
        runtime.end_prefill_epoch(())

        available_tokens[0] = 101
        runtime.begin_prefill_epoch(
            requests,
            SimpleNamespace(
                rem_input_tokens=10,
                rem_chunk_tokens=None,
                rem_total_tokens=100,
            ),
            max_requests=1,
        )
        self.assertEqual(
            [
                ticket.request_id
                for ticket in runtime._current_ticket_epoch.tickets
            ],
            ["oldest"],
        )
        runtime.end_prefill_epoch(())

        now_ms[0] += config.admission_liveness_timeout_ms + 1.0
        runtime.begin_prefill_epoch(
            requests,
            SimpleNamespace(
                rem_input_tokens=10,
                rem_chunk_tokens=None,
                rem_total_tokens=100,
            ),
            max_requests=1,
        )
        self.assertEqual(
            [
                ticket.request_id
                for ticket in runtime._current_ticket_epoch.tickets
            ],
            ["oldest"],
        )
        runtime.on_prefill_candidate_result(
            requests[0], admitted=True, result="ADDED"
        )
        self.assertNotIn(
            "oldest", runtime._ordinary_native_fallback_signature_by_request
        )
        self.assertNotIn(
            "oldest", runtime._ordinary_fallback_blocked_capacity
        )
        self.assertEqual(runtime._current_ordinary_starvation_priority, ())
        runtime.end_prefill_epoch(())

        runtime._request_metadata_by_id["newer"] = BeliefKVRequestMetadata(
            "wf-new", "inv-new", "ctx-new", 1
        )
        runtime.begin_prefill_epoch(
            requests,
            SimpleNamespace(
                rem_input_tokens=10,
                rem_chunk_tokens=None,
                rem_total_tokens=100,
            ),
            max_requests=1,
        )
        self.assertNotIn(
            "newer", runtime._ordinary_native_fallback_signature_by_request
        )
        self.assertNotIn(
            "newer", runtime._ordinary_fallback_blocked_capacity
        )
        runtime.end_prefill_epoch(())

    def test_batch_time_is_charged_proportionally_to_root_workflows(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = BeliefKVController()
        runtime._last_batch_selected_ms = 10.0
        runtime._last_batch_workflow_counts = {"wf-a": 1, "wf-b": 3}
        runtime._charge_previous_batch(30.0)
        self.assertEqual(
            runtime.controller.fairness.accounts["wf-a"].attained_service_ms,
            5.0,
        )
        self.assertEqual(
            runtime.controller.fairness.accounts["wf-b"].attained_service_ms,
            15.0,
        )
        runtime._charge_previous_batch(40.0)
        self.assertEqual(
            runtime.controller.fairness.accounts["wf-b"].attained_service_ms,
            15.0,
        )
        self.assertIsNone(runtime._last_batch_selected_ms)
        self.assertEqual(runtime._last_batch_workflow_counts, {})

    def test_existing_context_epoch_advances_before_admission(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = BeliefKVController()
        runtime._identity_metadata = {}
        runtime._linked_invocations = set()
        runtime._event_sequence = 0
        runtime._now_ms = lambda: 10.0
        runtime.event_log = None
        runtime.audit = _AuditRecorder()
        first = BeliefKVRequestMetadata("wf", "inv", "ctx", 0)
        runtime._ensure_causal_identity(first)

        resumed = BeliefKVRequestMetadata(
            "wf", "inv", "ctx", 3, context_mode="resume"
        )
        runtime._ensure_causal_identity(resumed)

        self.assertEqual(runtime.controller.graph.contexts["ctx"].epoch, 3)
        self.assertEqual(runtime.controller.page_index.context_epoch("ctx"), 3)
        self.assertIn(
            "context_epoch_advanced",
            [event for event, _, _ in runtime.audit.events],
        )
        stale = BeliefKVRequestMetadata("wf", "inv", "ctx", 2)
        with self.assertRaisesRegex(RuntimeError, "stale context epoch"):
            runtime._ensure_causal_identity(stale)

    def test_metadata_does_not_duplicate_runtime_declared_spawn(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime.controller = BeliefKVController()
        runtime._identity_metadata = {}
        runtime._linked_invocations = set()
        runtime._event_sequence = 0
        runtime._now_ms = lambda: 10.0
        runtime.event_log = None
        runtime.audit = _AuditRecorder()
        root = BeliefKVRequestMetadata("wf", "root", "ctx-root", 0)
        runtime._ensure_causal_identity(root)
        runtime._process_events(
            (
                RuntimeEvent(
                    event_id="child-create",
                    ts_ms=10.0,
                    kind=RuntimeEventKind.INVOCATION_CREATE,
                    workflow_id="wf",
                    invocation_id="child",
                    context_id="ctx-child",
                    context_epoch=0,
                    parent_invocation_id="root",
                    parent_context_id="ctx-root",
                    relation_type=RelationType.SPAWN,
                ),
                RuntimeEvent(
                    event_id="child-spawn",
                    ts_ms=10.0,
                    kind=RuntimeEventKind.SPAWN,
                    workflow_id="wf",
                    invocation_id="root",
                    target_invocation_id="child",
                    execution_mode=ExecutionMode.BACKGROUND,
                ),
            )
        )

        child = BeliefKVRequestMetadata(
            "wf",
            "child",
            "ctx-child",
            0,
            parent_invocation_id="root",
            parent_context_id="ctx-root",
            relation_type="spawn",
            execution_mode="background",
        )
        runtime._ensure_causal_identity(child)

        self.assertIn("child", runtime._linked_invocations)
        self.assertEqual(
            runtime.controller.graph.invocations["root"].child_invocation_ids,
            {"child"},
        )

    def test_host_pressure_cleanup_distinguishes_shadow_and_recompute(self):
        for residency, expected, needs_recompute in (
            (
                PhysicalResidency.DUAL_CLEAN,
                PhysicalResidency.GPU_ONLY,
                False,
            ),
            (
                PhysicalResidency.CPU_ONLY,
                PhysicalResidency.DEAD,
                True,
            ),
        ):
            with self.subTest(residency=residency.value):
                config = BeliefKVConfig(
                    hbm_capacity_bytes=2000,
                    host_capacity_bytes=1000,
                    reserve_hbm_bytes=100,
                    predictor_enabled=False,
                    host_cleanup_chunk_bytes=100,
                )
                controller = BeliefKVController(config)
                controller.process_runtime_events(
                    (
                        RuntimeEvent(
                            "start",
                            1.0,
                            RuntimeEventKind.WORKFLOW_START,
                            "wf",
                        ),
                        RuntimeEvent(
                            "create",
                            2.0,
                            RuntimeEventKind.INVOCATION_CREATE,
                            "wf",
                            invocation_id="inv",
                            context_id="ctx",
                            context_epoch=0,
                        ),
                        RuntimeEvent(
                            "wait",
                            3.0,
                            RuntimeEventKind.TOOL_START,
                            "wf",
                            invocation_id="inv",
                            context_id="ctx",
                            context_epoch=0,
                        ),
                    )
                )
                handle = PageHandle(71, 0)
                controller.page_index.register_page(
                    handle,
                    size_bytes=960,
                    residency=residency,
                )
                controller.page_index.bind_pages("ctx", 0, (handle,))
                runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
                runtime.config = config
                runtime.controller = controller
                runtime.audit = _AuditRecorder()
                runtime._full_prompt_replay_contexts = {("ctx", 0)}

                runtime._maybe_queue_host_cleanup(now_ms=4.0)
                tick = controller.tick(4.0, allow_reactive_transfer=False)

                self.assertIsNotNone(tick.transfer)
                self.assertEqual(
                    tick.transfer.command.kind,
                    CommandKind.DROP_HOST_CONTEXT,
                )
                self.assertEqual(
                    tick.transfer.page_actions[0].action,
                    PhysicalPageAction.DROP_HOST,
                )
                ack = CommandAck(
                    command_id=tick.transfer.command.command_id,
                    status=CommandStatus.COMPLETED,
                    completed_ts_ms=5.0,
                    actual_bytes=960,
                    page_handles=(handle,),
                )
                controller.acknowledge_command(ack)
                runtime._advance_host_cleanup((ack,), now_ms=5.0)

                self.assertEqual(controller.page_index.pages[handle].residency, expected)
                self.assertEqual(
                    ("ctx", 0) in runtime._recompute_required_contexts,
                    needs_recompute,
                )
                controller.update_signals(host_free_bytes=1000)
                runtime._maybe_queue_host_cleanup(now_ms=6.0)
                self.assertFalse(runtime._host_cleanup_active)


    def test_predictive_shadow_aggregate_preserves_action_diagnostics(self):
        runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
        runtime._predictive_shadow_aggregate_counts = Counter()
        runtime._predictive_shadow_aggregate_samples = {}

        runtime._record_predictive_shadow_aggregate(
            {
                "status": "evaluated",
                "selected_action": "observed_baseline",
                "support_level": "backoff",
                "candidate_count": 1,
                "calibration_coverage": 0.95,
                "blocked_reasons": ["prepare_host:tool_wait_unavailable"],
                "ood_reasons": ["tool_wait_unavailable"],
                "candidate_summaries": [
                    {
                        "action": "prepare_host",
                        "expected_benefit_ms": 12.0,
                        "causal_slack_probability": 0.8,
                        "required_wait_ms": 275.0,
                        "future_hbm_feasibility_probability": 1.0,
                        "expected_recourse_credit_ms": 7.5,
                        "prepare_recourse_failure_counts": {
                            "pressure_unavailable": 3,
                            "eligible": 1,
                        },
                        "timing_semantics": "release_after_transfer",
                        "eligible": False,
                        "reasons": [
                            "insufficient_causal_slack_probability"
                        ],
                        "prediction_head_support": [
                            ["tool_wait_slack", "backoff"]
                        ],
                    }
                ],
            },
            certificate_count=1,
            fresh_count=0,
            stale_count=1,
            stale_reasons={"page_revision": 1},
        )

        counts = runtime._predictive_shadow_aggregate_counts
        self.assertEqual(counts["result_count"], 1)
        self.assertEqual(counts["positive_benefit:prepare_host"], 1)
        self.assertEqual(
            counts[
                "candidate_reason:prepare_host:"
                "insufficient_causal_slack_probability"
            ],
            1,
        )
        self.assertEqual(
            counts[
                "head_support:prepare_host:tool_wait_slack:backoff"
            ],
            1,
        )
        self.assertEqual(
            counts[
                "recourse_failure:prepare_host:pressure_unavailable"
            ],
            3,
        )
        self.assertEqual(
            counts["recourse_failure:prepare_host:eligible"],
            1,
        )
        summary = runtime._predictive_shadow_sample_summary(
            runtime._predictive_shadow_aggregate_samples
        )
        self.assertEqual(
            summary["required_wait_ms:prepare_host"]["p50"],
            275.0,
        )
        self.assertEqual(
            summary["expected_recourse_credit_ms:prepare_host"]["p50"],
            7.5,
        )


class SGLangContractTest(unittest.TestCase):
    def test_metadata_wire_roundtrip(self):
        metadata = BeliefKVRequestMetadata("wf", "inv", "ctx", 3, "coder", "coder-1")
        self.assertEqual(BeliefKVRequestMetadata.from_wire(metadata.to_wire()), metadata)

    def test_metadata_rejects_non_boolean_replay_guarantee(self):
        payload = BeliefKVRequestMetadata("wf", "inv", "ctx", 0).to_wire()
        payload["full_prompt_replay_guaranteed"] = "false"

        with self.assertRaisesRegex(ValueError, "must be a bool"):
            BeliefKVRequestMetadata.from_wire(payload)

    def test_exact_version_guard(self):
        assert_supported_sglang_version(BASE_SGLANG_VERSION)
        with self.assertRaises(RuntimeError):
            assert_supported_sglang_version("0.5.2")

    def test_source_contract_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = SGLangSourceContract().check(Path(temporary))
        self.assertFalse(report.compatible)
        self.assertTrue(report.failures)

    def test_vendored_sglang_checkout_satisfies_runtime_contract(self):
        source_root = (
            Path(__file__).resolve().parents[1] / "third_party" / "sglang"
        )
        if not source_root.is_dir():
            self.skipTest("vendored SGLang checkout is unavailable")

        report = SGLangSourceContract().check(source_root)

        self.assertTrue(
            report.compatible,
            msg="; ".join(
                f"{item.file}:{item.symbol}: {item.reason}"
                for item in report.failures
            ),
        )

    def test_checkout_contract_script_imports_local_package(self):
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [
                    sys.executable,
                    str(repository_root / "scripts" / "check_sglang_contract.py"),
                    temporary,
                ],
                cwd=repository_root,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["compatible"])
        self.assertNotIn("ModuleNotFoundError", result.stderr)

    def test_idle_memory_check_accounts_for_beliefkv_allocator_reservations(self):
        from sglang.srt.managers.scheduler import Scheduler

        scheduler = object.__new__(Scheduler)
        scheduler.is_hybrid = False
        scheduler.max_total_num_tokens = 100
        scheduler.enable_hierarchical_cache = True
        scheduler.tree_cache = SimpleNamespace(protected_size=lambda: 0)
        scheduler._get_token_info = lambda: (20, 0.2, 10, 70)
        scheduler.beliefkv_runtime = SimpleNamespace(
            allocator_backed_reservation_tokens=lambda: 20
        )
        scheduler.disaggregation_mode = None
        scheduler.req_to_token_pool = SimpleNamespace(size=1, free_slots=[0])
        scheduler.enable_metrics = False
        scheduler._publish_kv_events = lambda: None

        Scheduler.check_memory(scheduler)

        scheduler.beliefkv_runtime = SimpleNamespace(
            allocator_backed_reservation_tokens=lambda: 19
        )
        with self.assertRaisesRegex(ValueError, "memory leak detected"):
            Scheduler.check_memory(scheduler)


def test_action_local_probe_projects_running_growth_into_hbm_deficit():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    runtime.scheduler = SimpleNamespace(
        running_batch=SimpleNamespace(reqs=(SimpleNamespace(rid="running"),)),
        chunked_req=None,
        max_running_requests=32,
    )
    runtime.controller = SimpleNamespace(admission=SimpleNamespace(reserved_bytes=0))
    runtime.config = SimpleNamespace(
        reference_policy_hbm_bucket_bytes=64,
        predictive_beneficiary_projection_horizon_ms=2000.0,
        kv_bytes_per_token=1,
        admission_prefill_quantum_tokens=16,
    )
    hint = ObservedSeedBeneficiaryHint(
        "seed",
        "beneficiary",
        "beneficiary-invocation",
        "beneficiary-context",
        0,
        64,
        32,
        remaining_prefill_bytes=100,
        predicted_output_bytes=100,
        prediction_support_level="exact",
    )
    observation = RuntimeResourceObservation(
        ts_ms=5.0,
        hbm_capacity_bytes=1_000,
        hbm_used_bytes=850,
        host_capacity_bytes=1_000,
        host_used_bytes=0,
        host_free_bytes=1_000,
    )

    probe = runtime._predictive_beneficiary_opportunity_probe(hint, observation)

    assert not probe.beneficiary_hbm_blocked
    assert probe.hbm_opportunity_possible
    assert probe.classification == "near_hbm_risk"
    assert probe.projected_running_growth_bytes == 16
    assert probe.projected_hbm_available_bytes == 134
    assert probe.predicted_deficit_bytes == 130
    assert probe.predicted_block_time_ms is None
    assert probe.block_time_source == "gpu_service_scenario"


def test_beneficiary_probes_share_running_growth_but_preserve_chunked_exclusion():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    chunked = SimpleNamespace(
        rid="chunked",
        fill_ids=list(range(100)),
        origin_input_ids=list(range(60)),
        output_ids=list(range(40)),
        prefix_indices=list(range(20)),
    )
    runtime.scheduler = SimpleNamespace(
        running_batch=SimpleNamespace(reqs=()),
        chunked_req=chunked,
        max_running_requests=32,
    )
    runtime.controller = SimpleNamespace(admission=SimpleNamespace(reserved_bytes=0))
    runtime.config = SimpleNamespace(
        kv_bytes_per_token=1,
        admission_decode_quantum_tokens=16,
    )
    observation = RuntimeResourceObservation(
        ts_ms=5.0,
        hbm_capacity_bytes=1_000,
        hbm_used_bytes=500,
        host_capacity_bytes=1_000,
        host_used_bytes=0,
        host_free_bytes=1_000,
    )
    environment = runtime._predictive_beneficiary_probe_environment(observation)

    chunked_probe = runtime._predictive_beneficiary_opportunity_probe(
        ObservedSeedBeneficiaryHint(
            "seed", "chunked", "invocation-a", "context-a", 0, 64, 32
        ),
        observation,
        environment=environment,
    )
    other_probe = runtime._predictive_beneficiary_opportunity_probe(
        ObservedSeedBeneficiaryHint(
            "seed", "other", "invocation-b", "context-b", 0, 64, 32
        ),
        observation,
        environment=environment,
    )

    assert chunked_probe.projected_running_growth_bytes == 16
    assert other_probe.projected_running_growth_bytes == 96


def test_predictive_reentry_watch_lifecycle_is_independent_of_beneficiary():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    runtime._predictive_reentry_watch_invocation_ids = set()

    runtime._update_predictive_reentry_watches(
        (("prepare", "tool_start", "invocation", 3),)
    )
    assert runtime._predictive_reentry_watch_invocation_ids == {"invocation"}

    runtime._update_predictive_reentry_watches(
        (("reentry", "tool_end", "invocation", 3),)
    )
    assert runtime._predictive_reentry_watch_invocation_ids == set()


def test_predicted_reentry_publishes_one_bounded_risk_delta_without_beneficiary():
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    invocation = SimpleNamespace(
        invocation_id="invocation",
        context_id="context",
        state=InvocationState.WAIT_TOOL,
        updated_ts_ms=100.0,
    )
    context = SimpleNamespace(epoch=3)
    summary = SimpleNamespace(
        physical_unique_bytes=400,
        gpu_bytes=0,
        cpu_bytes=400,
        extent_count=2,
    )
    page_index = SimpleNamespace(
        revision=19,
        topology_revision=7,
        has_context=lambda context_id: context_id == "context",
        context_epoch=lambda _context_id: 3,
        context_revision=lambda _context_id: 11,
        context_physical_summary=lambda _context_id: summary,
    )
    timing = SimpleNamespace(
        informative=True,
        decision_threshold=0.5,
        favorable_probability=0.8,
    )
    prediction = SimpleNamespace(
        action_timing=lambda action, _tau: (
            timing if action == "prefetch_gpu" else None
        ),
        to_dict=lambda: {"support_level": "exact"},
    )
    features = SimpleNamespace(to_dict=lambda: {"state": "wait_tool"})
    frontier_model = SimpleNamespace(
        model_version="frontier-test",
        predict=lambda _features: prediction,
    )
    overlay = ActionLocalPhysicalOverlay(
        context_id="context",
        context_epoch=3,
        context_revision=11,
        page_revision=19,
        topology_revision=7,
        generation_fingerprint="generation",
        shape_fingerprint="prefetch:400:n2",
        exclusive_reclaimable_bytes=0,
        d2h_copy_bytes=0,
        extent_count=2,
        cross_context_bytes=0,
        locked_bytes=0,
        owner_context_ids=("context",),
        blocker_codes=(),
        native_loading=False,
        captured_ts_ms=1_000.0,
        h2d_copy_bytes=400,
        evidence_kind="prefetch_target_preview",
    )
    overlay_batch = ActionLocalPhysicalOverlayBatch(
        beneficiary_risk_signature=(),
        opportunity=None,
        overlays=(overlay,),
        reentry_context_ids=("context",),
    )
    runtime.controller = SimpleNamespace(
        graph=SimpleNamespace(
            invocations={"invocation": invocation},
            contexts={"context": context},
        ),
        page_index=page_index,
        predictor=SimpleNamespace(
            frontier_model=frontier_model,
            features={},
        ),
        service_curve=SimpleNamespace(
            estimate=lambda *_args, **_kwargs: SimpleNamespace(
                shape_supported=True,
                estimated_completion_p90_ms=200.0,
            )
        ),
    )
    runtime.config = SimpleNamespace(
        predictive_risk_shadow_enabled=True,
        predictive_commit_guard_ms=25.0,
        predictive_prefetch_desired_lead_ms=100.0,
        reference_policy_hbm_bucket_bytes=64,
        joint_policy_enabled=False,
    )
    runtime.predictive_risk_worker = object()
    runtime.audit = _AuditRecorder()
    runtime._joint_shadow_counts = Counter()
    runtime._joint_predictive_counts = Counter()
    runtime._joint_shadow_timing_samples = {
        "snapshot_enqueue_ms": deque(maxlen=16),
    }
    runtime._predictive_reentry_watch_invocation_ids = {"invocation"}
    runtime._last_predictive_reentry_watch_poll_ms = None
    runtime._last_predictive_reentry_risk_signature = None
    runtime._shadow_event_sequence = 5
    runtime._shadow_page_revision = 9
    runtime._shadow_topology_revision = 7
    runtime._last_policy_state_stamp = JointShadowStateStamp(
        graph_version=4,
        consumer_version=2,
        event_sequence=5,
        page_revision=9,
        topology_revision=7,
        fairness_revision=1,
        transfer_epoch=0,
        runnable_signature=(),
        hbm_used_bytes=800,
        host_free_bytes=1_000,
    )
    runtime._latest_bounded_seed_runnable = ()
    runtime._last_policy_runtime_runnable = ()
    runtime._last_policy_fairness_accounts = ()
    runtime._last_policy_external_workflow_charges = ()
    runtime._last_policy_control_state = {}
    runtime._last_policy_capabilities = CapabilityReport(
        runtime_name="test",
        runtime_version="test",
        supported_residency_actions=frozenset(),
        execution_order_control=True,
        admission_control=True,
        transfer_dependencies=True,
        native_identity_mapping=True,
    )
    runtime._latest_observed_seed_beneficiary = None
    runtime._last_frontier_features = {}
    runtime._last_frontier_predictions = {}
    runtime._current_native_available_hbm_bytes = 200
    runtime._capture_reentry_action_local_physical_overlay_batch = (
        lambda _triggers, _observation: overlay_batch
    )
    submitted = []
    worker = SimpleNamespace(
        submit_delta=lambda delta: (
            submitted.append(delta)
            or SimpleNamespace(
                sequence=1,
                enqueue_ms=0.01,
                replaced_sequence=None,
            )
        )
    )
    observation = RuntimeResourceObservation(
        ts_ms=1_000.0,
        hbm_capacity_bytes=1_000,
        hbm_used_bytes=800,
        host_capacity_bytes=2_000,
        host_used_bytes=400,
        host_free_bytes=1_600,
    )

    with mock.patch(
        "beliefkv.runtime.sglang_v052rc1.build_invocation_frontier_features",
        return_value={"invocation": features},
    ):
        assert runtime._maybe_publish_predicted_reentry_risk_delta(
            worker, observation=observation
        )
        assert not runtime._maybe_publish_predicted_reentry_risk_delta(
            worker, observation=replace(observation, ts_ms=1_050.0)
        )

    assert len(submitted) == 1
    delta = submitted[0]
    assert delta.risk_evaluation_requested
    assert delta.observed_seed_beneficiary is None
    assert delta.risk_trigger_signature == (
        ("reentry", "predicted_latest_start", "invocation", 3),
    )
    assert delta.action_local_overlay_batch is overlay_batch


if __name__ == "__main__":
    unittest.main()
