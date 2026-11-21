from dataclasses import replace
from types import SimpleNamespace as NS
import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import ExecutionMode, RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.subagent_materialization import observe_materialization_headroom
from tests.test_full_only_native_lifecycle import full_native
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from scripts.prepare_subagent_materialization import configurations


class _CacheNode(NS):
    __eq__ = object.__eq__
    __hash__ = object.__hash__


def node(identity, size, *, device=False, host=True, refs=1, host_lock=0, pending=False):
    return _CacheNode(
        id=identity, component_data={0: NS(
            value=tuple(range(size)) if device else None,
            host_value=tuple(range(size)) if host else None,
            session_ref=refs, host_lock_ref=host_lock, lock_ref=0,
        )},
        write_through_pending_id=identity if pending else None, load_back_pending_id=None,
        children={}, parent=None,
    )


def cache(*nodes, host_capacity=1000, host_free=0, device_free=0):
    pool = NS(size=host_capacity, size_per_token=64, available_size=lambda: host_free)
    root = node(0, 0)
    tree = NS(
        root_node=root, _node_arena={row.id: row for row in (root, *nodes)},
        full_host_duplicates={
            row.id: row for row in nodes if row.component_data[0].value is not None
            and row.component_data[0].host_value is not None
        },
        evictable_host_leaves=set(),
        _can_reclaim_full_host_duplicate=lambda row: row.component_data[0].host_lock_ref == 0,
    )
    return NS(
        tree_components=(0,), is_write_back=True, host_memory_mode="cache",
        tree_core=tree, host_pool_group=NS(entry_map={"kv": NS(host_pool=pool)}),
        token_to_kv_pool_allocator=NS(size_full=10000, available_size=lambda: device_free),
        req_to_token_pool=NS(available_size=lambda: 24),
    )


def test_census_distinguishes_unique_useful_duplicate_inflight_and_untracked_slots():
    native = cache(
        node(1, 500), node(2, 100, refs=0), node(3, 100, device=True),
        node(4, 80, device=True, host_lock=1), node(5, 70, device=True, pending=True),
        host_free=50, device_free=100,
    )
    result = observe_materialization_headroom(native)
    assert result.observable
    assert result.useful_host_only_tokens == 500
    assert result.reclaimable_host_tokens == 200
    assert result.reclaimable_host_only_tokens == 100
    assert result.pinned_host_tokens == 150
    assert result.untracked_host_tokens == 100
    assert result.available_tokens == 350
    assert result.inspected_nodes == 6
    assert native.tree_core._node_arena[1].component_data[0].host_value is not None


@pytest.mark.parametrize("change,reason", (
    ("hybrid", "requires_full_only"), ("buffer", "requires_full_only"),
    ("mismatch", "native_host_accounting_mismatch"), ("geometry", "invalid_pool_geometry"),
    ("arena", "invalid_native_node_census"), ("absent", "native_observation_unavailable"),
))
def test_invalid_or_unsupported_census_is_unknown(change, reason):
    native = cache(node(1, 1000))
    if change == "hybrid":
        native.tree_components = (0, 2)
    elif change == "buffer":
        native.host_memory_mode = "buffer_only"
    elif change == "mismatch":
        native.host_pool_group.entry_map["kv"].host_pool.available_size = lambda: 1
    elif change == "geometry":
        native.token_to_kv_pool_allocator.available_size = lambda: -1
    elif change == "arena":
        native.tree_core._node_arena = list(native.tree_core._node_arena.values())
    else:
        del native.tree_core._node_arena
    result = observe_materialization_headroom(native)
    assert not result.observable and result.reason.startswith(reason)


def test_census_bound_credits_only_native_indexed_headroom():
    native = cache(
        node(1, 650), node(2, 200, device=True), node(3, 60, refs=0), node(4, 40, refs=0),
        host_free=50, device_free=100,
    )
    tree = native.tree_core
    arena = tree._node_arena
    tree.evictable_host_leaves.add(arena[3])
    for identity in range(len(arena), 8192):
        arena[identity] = node(identity, 2 if identity == 8191 else 1, device=True, host=False)

    complete = observe_materialization_headroom(native)
    assert complete.observable and complete.census_complete
    assert complete.inspected_nodes == 8192
    assert complete.useful_host_only_tokens == 650
    assert complete.reclaimable_host_tokens == 300
    assert complete.reclaimable_host_only_tokens == 100
    assert complete.available_tokens == 450

    # Split a device-only extent without changing either pool's free count.
    donor = arena[8191].component_data[0]
    split = node(8192, 1, device=True, host=False)
    split.component_data[0].value = donor.value[1:]
    donor.value = donor.value[:1]
    arena[split.id] = split
    bounded = observe_materialization_headroom(native)
    assert bounded.observable and not bounded.census_complete
    assert bounded.reason == "native_node_census_bound"
    assert bounded.host_capacity_tokens == 1000
    assert bounded.host_free_tokens == 50 and bounded.device_free_tokens == 100
    assert bounded.reclaimable_host_tokens == 260
    assert bounded.reclaimable_host_only_tokens == 60
    assert bounded.useful_host_only_tokens is None
    assert bounded.available_tokens == 410
    assert 0 < bounded.inspected_nodes <= 8192

    raw = observe_materialization_headroom(native, max_nodes=0)
    assert raw.observable and not raw.census_complete and raw.reason == bounded.reason
    assert raw.host_free_tokens == 50 and raw.device_free_tokens == 100
    assert raw.reclaimable_host_tokens == raw.inspected_nodes == 0
    assert raw.available_tokens == 150


@pytest.mark.parametrize("device", (True, False), ids=("mirror", "host-only"))
@pytest.mark.parametrize("case", (
    "stale", "protected", "host_lock", "device_lock", "writing", "loading", "shape",
))
def test_census_bound_revalidates_indexed_extents(device, case):
    good = node(1, 17, device=device, refs=int(device))
    suspect = node(2, 29, device=device, refs=0)
    sibling = node(3, 1)
    native = cache(good, suspect, sibling)
    tree = native.tree_core
    if not device:
        tree.evictable_host_leaves.update((good, suspect, sibling))
    full = suspect.component_data[0]
    protected = ()
    if case == "stale":
        tree._node_arena[suspect.id] = node(suspect.id, 29, device=device, refs=0)
    elif case == "protected":
        protected = (suspect.id,)
    elif case == "host_lock":
        full.host_lock_ref = 1
    elif case == "device_lock":
        full.lock_ref = 1
    elif case == "writing":
        suspect.write_through_pending_id = suspect.id
    elif case == "loading":
        suspect.load_back_pending_id = suspect.id
    elif device:
        full.value = (0,)
    else:
        suspect.children[sibling.id] = sibling
        sibling.parent = suspect

    bounded = observe_materialization_headroom(native, max_nodes=3, protected_host_nodes=protected)
    assert bounded.observable and bounded.reason == "native_node_census_bound"
    assert bounded.reclaimable_host_tokens == (46 if device and case == "device_lock" else 17)


@pytest.mark.parametrize("protected,expected", ((False, 32), (True, 5)))
def test_census_bound_counts_host_parent_only_after_all_children(protected, expected):
    parent, left, right = node(1, 20, refs=0), node(2, 5, refs=0), node(3, 7, refs=0)
    native = cache(parent, left, right, node(4, 1, device=True, host=False))
    parent.parent = native.tree_core.root_node
    for leaf in (left, right):
        parent.children[leaf.id] = leaf
        leaf.parent = parent
    native.tree_core.evictable_host_leaves.update((left, right))
    dense = observe_materialization_headroom(
        native, max_nodes=4, protected_host_nodes=(right.id,) if protected else (),
    )
    assert dense.reclaimable_host_tokens == expected
    assert dense.inspected_nodes <= 4
    limited = observe_materialization_headroom(native, max_nodes=2)
    assert 0 < limited.reclaimable_host_tokens <= 12
    assert limited.inspected_nodes <= 2


def test_census_bound_shares_actual_iteration_budget_across_indexes():
    read = []

    class DuplicateIndex(dict):
        def values(self):
            for entry in super().values():
                read.append(entry)
                yield entry

    class HostLeafIndex(set):
        def __iter__(self):
            for entry in super().__iter__():
                read.append(entry)
                yield entry

    mirror = node(1, 11, device=True)
    leaves = [node(identity, 7, refs=0) for identity in (2, 3, 4)]
    native = cache(mirror, *leaves)
    tree = native.tree_core
    tree.full_host_duplicates = DuplicateIndex(tree.full_host_duplicates)
    tree.evictable_host_leaves = HostLeafIndex(leaves)
    raw = observe_materialization_headroom(native, max_nodes=0)
    assert raw.reclaimable_host_tokens == 0 and not read
    bounded = observe_materialization_headroom(native, max_nodes=2)
    assert bounded.reclaimable_host_tokens == 18
    assert bounded.inspected_nodes == len(read) == 2


def test_census_bound_discards_credit_that_exceeds_observed_host_usage():
    native = cache(node(1, 20, device=True), host_capacity=100, host_free=95, device_free=10)
    bounded = observe_materialization_headroom(native, max_nodes=1)
    assert bounded.observable and not bounded.census_complete
    assert bounded.reason == "native_node_census_bound"
    assert bounded.reclaimable_host_tokens == 0
    assert bounded.reclaimable_host_only_tokens == 0
    assert bounded.available_tokens == 105


def test_real_native_split_backup_ack_and_host_only_accounting(full_native):
    from test_beliefkv_terminal_capacity import insert_session

    native, allocator, writes, loads, commits = full_native
    pool = native.host_pool_group.entry_map["kv"].host_pool
    slots = {"used": 0}
    pool.available_size = lambda: pool.size - slots["used"]
    first, generation = insert_session(native, [1, 2, 3, 4], "parent")
    native.prepare_beliefkv_spawn_full(session_id="parent", session_generation=generation)
    slots["used"] = 4
    pending = observe_materialization_headroom(native)
    assert pending.observable and pending.pinned_host_tokens == 4
    native.writing_check(finish_count=1)
    settled = observe_materialization_headroom(native)
    assert settled.observable and settled.reclaimable_host_tokens == 4
    tail, _ = insert_session(native, list(range(1, 9)), "parent")
    native.prepare_beliefkv_spawn_full(session_id="parent", session_generation=generation)
    slots["used"] = 8
    native.writing_check(finish_count=1)
    native.beliefkv_cold_full_owner_validator = lambda _: True
    free = allocator.available_size()
    assert native.reclaim_beliefkv_handoff_capacity(
        full_tokens=free + 8, mamba_slots=0,
    ) == {0: 8}
    observed_state = observe_materialization_headroom(native)
    assert observed_state.observable and observed_state.useful_host_only_tokens == 8
    assert observed_state.reclaimable_host_tokens == 0
    assert first.component_data[0].value is tail.component_data[0].value is None
    assert len(writes) == 2 and not loads
    # The census only reads these native buffers.
    before = allocator.available_size()
    assert observe_materialization_headroom(native) == observed_state
    assert allocator.available_size() == before


@pytest.fixture
def runtime(monkeypatch, tmp_path, request):
    mode = getattr(request, "param", "pressure_v1")
    native_fixture = None
    if isinstance(mode, tuple):
        mode, native_fixture = mode
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "AGENT_FREE_CONTINUATION_ADMISSION": "1", "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
        "AGENT_CONTROL_INTERVAL_MS": "2000", "EXECUTION_FRONTIER_RESTORE": "0",
        "BELIEFKV_SUBAGENT_MATERIALIZATION": mode,
    }, "full")
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "materialization.sock"))
    native = request.getfixturevalue(native_fixture)[0] if native_fixture else cache(node(1, 1000))
    live.attach_native_cache(native)
    live.on_events((
        observed(0, Kind.WORKFLOW_START),
        observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="parent-context"),
    ))
    live._agent_admission.next_tick = float("inf")
    live._native_running_batch = NS(reqs=[])
    live._test_sequence = 1
    try:
        yield live
    finally:
        live.close()


def child(live, identity="child", *, parent="parent", tokens=100, workflow="wf"):
    live._test_sequence += 3
    seq = live._test_sequence
    live.on_events((
        replace(observed(
            seq, Kind.INVOCATION_CREATE, invocation_id=identity, context_id=identity + "-context",
            parent_invocation_id=parent, relation_type=RelationType.SPAWN,
        ), workflow_id=workflow),
        replace(observed(
            seq + 1, Kind.SPAWN, invocation_id=parent, target_invocation_id=identity,
            execution_mode=ExecutionMode.BACKGROUND,
        ), workflow_id=workflow),
    ))
    req = request(identity + "-request", identity, tokens=tokens, workflow=workflow)
    req.output_ids.clear()
    req.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
    req.finished = lambda: False
    live.register_visible_request(req)
    return req


def prepare(live, queue, *, running=(), now=100.):
    live._subagent_materialization.prepare(
        queue, running_batch=NS(reqs=list(running)), adder=None, now=now,
    )


def new_root(live, identity, *, tokens=100, publish=True):
    live._test_sequence += 3
    seq = live._test_sequence
    live.on_events((replace(observed(seq, Kind.WORKFLOW_START), workflow_id=identity),))
    create = replace(observed(
        seq + 1, Kind.INVOCATION_CREATE, invocation_id=identity,
        context_id=identity + "-context", relation_type=RelationType.ROOT,
    ), workflow_id=identity)
    if publish:
        live.on_events((create,))
    req = request(identity + "-request", identity, tokens=tokens, workflow=identity)
    req.beliefkv_metadata["relation_type"] = "root"
    req.output_ids.clear()
    req.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
    req.finished = lambda: False
    assert live.register_visible_request(req)
    return req, create


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
def test_resume_ranking_reuses_d3_samples_with_hysteresis(runtime):
    gate = runtime._subagent_materialization
    gate.adaptive_resume_ranking = True
    runtime._native_chunk_input_reserve = 64
    req, _ = new_root(runtime, "entry")
    running = (object(),)
    price = gate._price(gate.pending[req.rid])
    reserve = gate._reserve(NS(reqs=running), None)
    allocate = runtime._native_cache.token_to_kv_pool_allocator
    for now, margin, expected in (
        (100., 127, False), (103., 128, True), (106., 64, True), (109., 63, False),
    ):
        allocate.available_size = lambda: reserve + price + margin
        prepare(runtime, [req], running=running, now=now)
        assert gate.pending[req.rid].permitted
        assert gate.allow_resume_ranking(now=now) is expected
        assert gate.status()["resume_ranking"]["headroom_tokens"] == margin
    assert runtime.counts["subagent_materialization_resume_pressure_probes"] == 0
    assert runtime.counts["subagent_materialization_resume_ranking_enabled"] == 1
    assert runtime.counts["subagent_materialization_resume_ranking_disabled"] == 1
    # A permit consumes headroom before the request enters the native batch.
    allocate.available_size = lambda: reserve + price + 127
    assert not gate.allow_resume_ranking(now=111.)
    assert gate.status()["resume_ranking"]["headroom_tokens"] == 127
    allocate.available_size = lambda: reserve + price + 128
    assert gate.allow_resume_ranking(now=113.)


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
@pytest.mark.parametrize("staged", (False, True))
@pytest.mark.parametrize("initial_permit", (False, True))
def test_resume_pressure_probe_charges_native_selected_inputs(runtime, monkeypatch, staged, initial_permit):
    gate = runtime._subagent_materialization
    gate.adaptive_resume_ranking = True
    runtime._native_chunk_input_reserve = 64
    req, _ = new_root(runtime, "entry")
    if initial_permit:
        gate.pending[req.rid].permitted = True
    else:
        gate.admitted(req)
    req.prefix_indices = tuple(range(20))
    running = NS(reqs=[])
    adder = NS(can_run_list=[] if staged else [req])
    if staged:
        member = NS(key=runtime.visible[req.rid], request=req)
        monkeypatch.setattr(
            runtime, "_demand_prefill_pipeline", NS(pending=NS(members=[member]), close=lambda: None),
            raising=False,
        )
    reserve = gate._reserve(running, adder)
    outstanding = gate._input_price(req, len(req.prefix_indices))
    allocate = runtime._native_cache.token_to_kv_pool_allocator
    allocate.available_size = lambda: reserve + outstanding + 127
    gate.prepare([req], running_batch=running, adder=adder, now=100.)
    # Once selected, the same permit is already part of the native commitments.
    assert not gate.allow_resume_ranking(now=102.)
    assert gate.status()["resume_ranking"]["headroom_tokens"] == 127
    allocate.available_size = lambda: reserve + outstanding + 128
    assert not gate.allow_resume_ranking(now=103.)
    assert gate.allow_resume_ranking(now=104.)
    allocate.available_size = lambda: -1
    assert not gate.allow_resume_ranking(now=106.)
    assert gate.status()["resume_ranking"]["headroom_tokens"] is None
    assert runtime.counts["subagent_materialization_resume_pressure_probes"] == 3


@pytest.mark.parametrize("runtime", ("pressure_v1", "pressure_v2"), indirect=True)
def test_root_hold_requires_v2_and_releases_into_the_real_native_plan(runtime):
    req, create = new_root(runtime, "arrival")
    gate, a = runtime._subagent_materialization, runtime._agent_admission
    key = runtime.visible[req.rid]
    if not gate.include_root_entries:
        assert req.rid not in gate.pending and a.allows(key)
        return

    prepare(runtime, [req], running=[object()])
    assert gate.status()["held_root_request_ids"] == [req.rid]
    assert not a.allows(key) and not a._timeout_force_resume_allowed(key)
    assert not a._can_resume(a.policy.programs[key.invocation_id])
    assert gate.defer_start(req)

    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 201
    gate.capacity_changed()
    plan = runtime.plan_native_prefill([req], running_batch=NS(reqs=[]), adder=None)
    selection = select_native_prefill_candidates(
        [req], plan=plan, current_semantic_revision=runtime.semantic_revision,
    )
    assert selection.candidates == (req,)
    assert runtime.graph.workflows[create.workflow_id].start_ts_ms == create.ts_ms - 1
    assert not gate.defer_start(req)
    runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    assert gate.pending[req.rid].started and create.workflow_id in gate.started_workflows
    assert runtime.counts["subagent_materialization_root_started"] == 1


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
@pytest.mark.parametrize("wait", ("all", "any", "not_waiting", "call"))
def test_root_budget_prioritizes_actual_blocking_children_before_shorter_new_work(runtime, wait):
    root, _ = new_root(runtime, "arrival", tokens=10)
    unrelated = child(runtime, "other", tokens=100)
    blocking = child(runtime, "blocking", tokens=100)
    a, gate = runtime._agent_admission, runtime._subagent_materialization
    scores = {"arrival": 1., "other": .9, "blocking": .1}
    a._combination = NS(_work_weight=lambda key, _: scores[key.invocation_id])
    if wait == "call":
        runtime.on_events((observed(
            50, Kind.CALL, invocation_id="parent", target_invocation_id="blocking",
        ),))
    else:
        runtime.on_events((observed(
            50, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("blocking",),
            attributes={"mode": "any" if wait == "any" else "all"},
        ),))
        if wait != "not_waiting":
            runtime.on_events((observed(51, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 202
    prepare(runtime, (root, unrelated, blocking), running=[object()])
    expected = blocking if wait in ("all", "call") else unrelated
    assert gate.last_decision["allowed"] == [expected.rid]
    assert gate.last_decision["held_root_request_ids"] == [root.rid]
    assert gate.last_decision["available_before_tokens"] == 200


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
def test_blocking_siblings_share_budget_before_a_root_outside_the_candidate_window(runtime):
    root, _ = new_root(runtime, "arrival", tokens=10)
    left, right = child(runtime, "left"), child(runtime, "right")
    runtime.on_events((
        observed(50, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("left", "right")),
        observed(51, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),
    ))
    gate = runtime._subagent_materialization
    gate.max_candidates = 2
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 402
    prepare(runtime, (root, left, right), running=[object()])
    assert gate.last_decision["allowed"] == [left.rid, right.rid]
    assert gate.last_decision["held_root_request_ids"] == [root.rid]


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
def test_native_start_rechecks_root_permission_after_a_blocking_child_arrives(runtime):
    root, _ = new_root(runtime, "arrival")
    gate = runtime._subagent_materialization
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 202
    queue = [root]
    prepare(runtime, queue, running=[object()])
    assert gate.allows(runtime.visible[root.rid])
    blocking = child(runtime, "blocking")
    runtime.on_events((observed(
        50, Kind.CALL, invocation_id="parent", target_invocation_id="blocking",
    ),))
    queue.append(blocking)
    assert gate.defer_start(root)
    assert gate.last_decision["allowed"] == [blocking.rid]
    assert root.kv.req_pool_idx is None and root.kv.kv_allocated_len == 0


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
def test_root_ingress_before_matching_create_stays_held_until_ready(runtime):
    req, create = new_root(runtime, "arrival", publish=False)
    gate = runtime._subagent_materialization
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 1000
    prepare(runtime, (req,), running=[object()])
    assert gate.status()["held_root_request_ids"] == [req.rid]
    runtime.on_events((create,))
    prepare(runtime, (req,), running=[object()], now=101.)
    assert gate.allows(runtime.visible[req.rid])


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
@pytest.mark.parametrize("max_nodes", (8192, 1), ids=("complete-census", "census-bound"))
def test_idle_progress_can_start_a_root_if_no_existing_work_can_advance(runtime, max_nodes):
    oldest, _ = new_root(runtime, "older")
    later, _ = new_root(runtime, "later")
    gate = runtime._subagent_materialization
    gate.max_nodes = max_nodes
    prepare(runtime, (later, oldest))
    assert gate.last_decision["progress_exception"] == oldest.rid
    assert gate.last_decision["held_root_request_ids"] == [later.rid]
    prepare(runtime, (later, oldest), now=102.)
    assert gate.last_decision["allowed"] == [oldest.rid]
    assert gate.last_decision["progress_exception"] is None
    assert runtime.counts["subagent_materialization_progress_exceptions"] == 1


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
def test_unfittable_old_root_leaves_room_for_a_fittable_root_in_the_same_check(runtime):
    oldest, _ = new_root(runtime, "older", tokens=300)
    later, _ = new_root(runtime, "later")
    gate = runtime._subagent_materialization
    gate.max_candidates = 1
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 202
    prepare(runtime, (oldest, later), running=[object()])
    assert gate.last_decision["root_candidates"] == [later.rid]
    assert gate.last_decision["allowed"] == [later.rid]
    assert gate.last_decision["held_root_request_ids"] == [oldest.rid]


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
def test_unfittable_join_children_leave_room_for_a_fittable_root(runtime):
    root, _ = new_root(runtime, "arrival")
    left, right = child(runtime, "left", tokens=300), child(runtime, "right", tokens=300)
    runtime.on_events((
        observed(50, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("left", "right")),
        observed(51, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),
    ))
    gate = runtime._subagent_materialization
    gate.max_candidates = 2
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 202
    prepare(runtime, (root, left, right), running=[object()])
    assert gate.last_decision["root_candidates"] == [root.rid]
    assert gate.last_decision["allowed"] == [root.rid]
    assert set(gate.last_decision["held"]) == {left.rid, right.rid}
    assert gate.last_decision["progress_exception"] is None


def test_hold_is_applied_to_registration_periodic_free_sibling_and_timeout_grants(runtime):
    req = child(runtime)
    gate, a = runtime._subagent_materialization, runtime._agent_admission
    key, item = a.latest["child"], a.policy.programs["child"]
    assert req.rid in runtime.visible and item.pending and not item.active
    prepare(runtime, [req], running=[object()])
    assert not gate.allows(key) and not a.allows(key)
    assert not a._can_resume(item)
    assert a._candidate(req.rid, key, include_first=True) is None
    assert not a._admit_from_free_capacity(
        [item], event="test_grant", join_id=None,
    )
    assert not a._timeout_force_resume_allowed(key)
    a.next_tick = 0.
    a.tick()
    assert not item.active
    plan = runtime.plan_native_prefill([req], running_batch=NS(reqs=[object()]), adder=None)
    selected = select_native_prefill_candidates(
        [req], plan=plan, current_semantic_revision=runtime.semantic_revision,
    )
    assert not selected.candidates
    assert req.kv.req_pool_idx is None and req.kv.kv_allocated_len == 0
    assert req.origin_input_ids == list(range(100))


def test_ready_service_timeout_never_bypasses_first_materialization_hold(runtime):
    from beliefkv.runtime.ready_service_guard import ReadyServiceGuard

    req = child(runtime)
    a = runtime._agent_admission
    prepare(runtime, [req], running=[object()])
    key = a.latest["child"]
    a._ready_service_guard = ReadyServiceGuard(a)
    a._ready_service_guard.since["child"] = key, 0.
    a._ready_service_guard.target = key
    a._ready_service_guard.begin(1801.)
    assert a._ready_service_guard.target is None
    assert "child" not in a._ready_service_guard.since
    assert not a._timeout_force_resume_allowed(key)
    assert not a.policy.programs["child"].active
    assert req.kv.req_pool_idx is None and req.kv.kv_allocated_len == 0


def test_capacity_release_wakes_and_actual_native_plan_admits_the_child(runtime):
    req = child(runtime)
    gate, a = runtime._subagent_materialization, runtime._agent_admission
    prepare(runtime, [req], running=[object()])
    assert not gate.allows(a.latest["child"])
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 201
    gate.capacity_changed()
    plan = runtime.plan_native_prefill([req], running_batch=NS(reqs=[]), adder=None)
    actual = select_native_prefill_candidates(
        [req], plan=plan, current_semantic_revision=runtime.semantic_revision,
    )
    assert actual.candidates == (req,)
    assert a.policy.programs["child"].active
    assert req.kv.kv_allocated_len == 0
    runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    assert gate.pending[req.rid].started
    assert ("wf", "child-context") in gate.materialized
    assert runtime.counts["subagent_materialization_started"] == 1


def test_terminal_service_guard_composes_with_first_materialization_permission(runtime):
    from beliefkv.runtime.ready_service_guard import ReadyServiceGuard

    req = child(runtime)
    gate, a = runtime._subagent_materialization, runtime._agent_admission
    key = a.latest["child"]
    prepare(runtime, [req], running=[object()])
    a._ready_service_guard = guard = ReadyServiceGuard(a)
    guard.since["child"] = key, 0.
    guard.target = key
    guard.begin(1801.)
    assert guard.target is None and not a._timeout_force_resume_allowed(key)

    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 201
    gate.capacity_changed()
    plan = runtime.plan_native_prefill([req], running_batch=NS(reqs=[]), adder=None)
    selected = select_native_prefill_candidates(
        [req], plan=plan, current_semantic_revision=runtime.semantic_revision,
    )
    assert selected.candidates == (req,)
    runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    assert gate.pending[req.rid].started
    guard.since["child"] = key, 0.
    guard.target = key
    req.output_ids = [1]
    req.finished = lambda: True

    assert runtime.on_batch_completed(NS(reqs=[req])) == (req.rid,)
    assert req.rid not in gate.pending
    assert req.rid not in runtime.visible
    assert guard.target is None and a.counts["ready_service_guard_served"] == 1
    assert guard.since["child"][1] > 0.


def test_all_pending_initial_prompts_share_one_capacity_budget(runtime):
    first, second = child(runtime, "first"), child(runtime, "second")
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 301
    prepare(runtime, [first, second], running=[object()])
    gate, a = runtime._subagent_materialization, runtime._agent_admission
    assert gate.allows(a.latest["first"])
    assert not gate.allows(a.latest["second"])
    assert gate.last_decision["available_before_tokens"] == 299


def test_candidate_order_uses_current_continuous_work_not_request_cost(runtime):
    long = child(runtime, "long", tokens=10)
    short = child(runtime, "short", tokens=100)
    a = runtime._agent_admission
    a._combination = NS(
        _work_weight=lambda key, predictions: .9 if key.invocation_id == "short" else .1,
        defer=lambda key: None,
    )
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 251
    prepare(runtime, [long, short], running=[object()])
    gate = runtime._subagent_materialization
    assert gate.last_decision["allowed"] == [short.rid]
    assert gate.allows(a.latest["short"]) and not gate.allows(a.latest["long"])
    assert not a.policy.programs["short"].active


def test_no_pressure_permits_all_without_moving_the_workflow_arrival(runtime):
    first, second = child(runtime, "first"), child(runtime, "second")
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 1000
    start = runtime.graph.workflows["wf"].start_ts_ms
    prepare(runtime, [first, second], running=[object()])
    gate = runtime._subagent_materialization
    assert set(gate.last_decision["allowed"]) == {first.rid, second.rid}
    assert runtime.graph.workflows["wf"].start_ts_ms == start
    assert not runtime.physical_ledger.pending_count


def test_first_chunk_reserves_only_unallocated_suffix_and_cannot_be_held_again(runtime):
    req = child(runtime)
    gate = runtime._subagent_materialization
    prepare(runtime, [req])
    assert gate.last_decision["progress_exception"] == req.rid
    runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    req.kv.kv_allocated_len = 40
    req.kv.req_pool_idx = 1
    assert gate._price(gate.pending[req.rid]) == 160
    other = child(runtime, "other")
    prepare(runtime, [req, other], now=102.)
    assert gate.allows(runtime.visible[req.rid])
    assert gate.last_decision["outstanding_initial_tokens"] == 160
    gate.completed((req.rid,))
    assert req.rid not in gate.pending


def test_raw_free_capacity_fast_path_avoids_tree_census_and_marks_host_only_unknown():
    native = cache(node(1, 1000), device_free=1000)
    del native.tree_core._node_arena
    result = observe_materialization_headroom(native, required_free_tokens=500)
    assert result.observable and not result.census_complete
    assert result.inspected_nodes == 0 and result.useful_host_only_tokens is None
    assert result.reason == "raw_free_capacity_suffices"


def test_prefetch_checkpoint_cannot_be_credited_as_reclaimable_host_capacity():
    native = cache(node(1, 1000, device=True))
    result = observe_materialization_headroom(native, protected_host_nodes=(1,))
    assert result.observable and result.reclaimable_host_tokens == 0
    assert result.pinned_host_tokens == 1000


def test_progress_exception_skips_a_child_that_cannot_fit_its_own_logical_budget(runtime):
    huge = child(runtime, "huge", tokens=11000)
    small = child(runtime, "small")
    prepare(runtime, [huge, small])
    assert runtime._subagent_materialization.last_decision["progress_exception"] == small.rid


def test_progress_exception_survives_the_ordinary_two_second_admission_window(runtime):
    first, second = child(runtime, "first"), child(runtime, "second")
    gate = runtime._subagent_materialization
    for now in (100., 100.4, 101., 102.):
        prepare(runtime, [first, second], now=now)
        assert gate.allows(runtime.visible[first.rid])
        assert not gate.allows(runtime.visible[second.rid])
    assert runtime.counts["subagent_materialization_progress_exceptions"] == 1
    assert gate.progress_path == ["first"]


def test_existing_ready_request_prevents_speculative_pressure_exception(runtime):
    req = child(runtime)
    parent = request("parent-request", "parent", tokens=100, workflow="wf")
    parent.output_ids.clear()
    parent.finished = lambda: False
    runtime.register_visible_request(parent)
    prepare(runtime, [req, parent])
    assert not runtime._subagent_materialization.last_decision["allowed"]
    assert runtime._agent_admission.allows(runtime.visible[parent.rid])


def test_nested_foreground_child_can_unblock_the_single_progress_branch(runtime):
    first = child(runtime, "first")
    prepare(runtime, [first])
    gate = runtime._subagent_materialization
    runtime.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
    gate.completed((first.rid,))
    nested = child(runtime, "nested", parent="first")
    outsider = child(runtime, "other")
    runtime.on_events((observed(
        20, Kind.CALL, invocation_id="first", target_invocation_id="nested",
    ),))
    prepare(runtime, [outsider, nested], now=101.)
    assert gate.last_decision["progress_exception"] == nested.rid
    assert gate.progress_path == ["first", "nested"]
    assert gate.allows(runtime.visible[nested.rid])
    assert not gate.allows(runtime.visible[outsider.rid])


def test_progress_branch_waiting_for_real_tool_does_not_open_more_spill_slots(runtime):
    first = child(runtime, "first")
    prepare(runtime, [first])
    gate = runtime._subagent_materialization
    runtime.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
    gate.completed((first.rid,))
    runtime.graph.invocations["first"].state = InvocationState.WAIT_TOOL
    runtime.graph.invocations["first"].active_tool_calls["tool"] = ("execute", 0.)
    other = child(runtime, "other")
    prepare(runtime, [other], now=101.)
    assert not gate.allows(runtime.visible[other.rid])
    assert runtime.counts["subagent_materialization_progress_exceptions"] == 1


def test_observed_all_join_progress_advances_one_child_then_its_waiting_sibling(runtime):
    first = child(runtime, "first")
    prepare(runtime, [first])
    gate = runtime._subagent_materialization
    runtime.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
    gate.completed((first.rid,))
    nested = child(runtime, "nested", parent="first")
    sibling = child(runtime, "sibling", parent="first")
    outsider = child(runtime, "other")
    runtime.on_events((
        observed(
            25, Kind.JOIN_CREATE, join_id="nested-join",
            member_invocation_ids=("nested", "sibling"),
        ),
        observed(26, Kind.JOIN_WAIT, invocation_id="first", join_id="nested-join"),
    ))
    prepare(runtime, [outsider, nested, sibling], now=101.)
    assert gate.last_decision["progress_exception"] == nested.rid
    assert not gate.allows(runtime.visible[sibling.rid])
    runtime.on_events((observed(27, Kind.RETURN, invocation_id="nested"),))
    prepare(runtime, [outsider, sibling], now=102.)
    assert gate.last_decision["progress_exception"] == sibling.rid
    assert not gate.allows(runtime.visible[outsider.rid])


def test_native_attempt_rebind_preserves_hold_and_rejects_stale_permission(runtime):
    req = child(runtime)
    gate = runtime._subagent_materialization
    key = runtime.visible[req.rid]
    req.cache_request_handle.attempt_id += 1
    runtime.on_requests_requeued((req,), is_retracted=False)
    assert gate.pending[req.rid].key.attempt_id == 1
    assert not gate.allows(runtime.visible[req.rid])
    assert not gate.allows(key)


def test_terminal_child_releases_hold_and_cancellation_does_not_materialize(runtime):
    req = child(runtime)
    runtime.on_events((observed(
        10, Kind.INVOCATION_CANCEL, invocation_id="child", context_id="child-context",
    ),))
    assert not runtime._subagent_materialization.pending
    assert not runtime._subagent_materialization.materialized
    assert req.kv.kv_allocated_len == 0


def test_late_spawn_indexes_an_unstarted_first_request_without_resetting_arrival(runtime):
    runtime.on_events((observed(
        2, Kind.INVOCATION_CREATE, invocation_id="late", context_id="late-context",
    ),))
    req = request("late-request", "late", tokens=100, workflow="wf")
    req.output_ids.clear()
    req.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
    req.finished = lambda: False
    runtime.register_visible_request(req)
    original_arrival = runtime._visible_since[req.rid]
    assert req.rid not in runtime._subagent_materialization.pending
    runtime._agent_admission.policy.programs["late"].marked_for_pause = True
    runtime.on_events((observed(
        3, Kind.SPAWN, invocation_id="parent", target_invocation_id="late",
        execution_mode=ExecutionMode.BACKGROUND,
    ),))
    gate = runtime._subagent_materialization
    assert req.rid in gate.pending
    assert gate.pending[req.rid].arrived == original_arrival
    assert not runtime._agent_admission.policy.programs["late"].active
    assert not runtime._agent_admission.policy.programs["late"].marked_for_pause


def test_duplicate_registration_observation_keeps_wait_and_permission(runtime):
    req = child(runtime)
    gate = runtime._subagent_materialization
    prepare(runtime, [req])
    original = gate.pending[req.rid]
    gate.registered(req, runtime.visible[req.rid])
    assert gate.pending[req.rid] is original and original.permitted


def test_expensive_census_is_shared_across_repeated_prefill_plans(runtime):
    req = child(runtime)
    for now in (100., 100.01, 100.1, 100.399):
        prepare(runtime, [req], running=[object()], now=now)
    assert runtime.counts["subagent_materialization_checks"] == 1
    runtime._subagent_materialization.capacity_changed()
    prepare(runtime, [req], running=[object()], now=100.399)
    assert runtime.counts["subagent_materialization_checks"] == 2


@pytest.mark.parametrize("runtime", ("pressure_v1", "pressure_v2"), indirect=True)
def test_persistent_pressure_reuses_holds_without_using_old_census_for_new_grants(
    runtime, monkeypatch,
):
    req = child(runtime)
    gate = runtime._subagent_materialization
    original = gate._outstanding_inputs
    scans = []

    def track(*args, **kwargs):
        scans.append(kwargs["refresh"])
        return original(*args, **kwargs)

    monkeypatch.setattr(gate, "_outstanding_inputs", track)
    for now in (100., 100.5, 101., 101.5):
        prepare(runtime, [req], running=[object()], now=now)
    assert runtime.counts["subagent_materialization_checks"] == 4
    assert runtime.counts["subagent_materialization_complete_censuses"] == 1
    assert runtime.counts["subagent_materialization_pressure_backoff_checks"] == 3
    assert runtime.counts["subagent_materialization_pressure_backoff_before_input_scan"] == 3
    assert scans == [False]
    prepare(runtime, [req], running=[object()], now=102.)
    assert runtime.counts["subagent_materialization_complete_censuses"] == 2
    assert scans == [False, False]


def test_raw_free_release_can_open_a_hold_during_census_backoff(runtime):
    req = child(runtime)
    prepare(runtime, [req], running=[object()])
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 1000
    prepare(runtime, [req], running=[object()], now=100.5)
    assert runtime._subagent_materialization.allows(runtime.visible[req.rid])
    assert runtime.counts["subagent_materialization_complete_censuses"] == 1
    assert runtime.counts["subagent_materialization_pressure_backoff_before_input_scan"] == 0


def test_backoff_uses_full_input_budget_after_initial_capacity_fits(runtime, monkeypatch):
    req = child(runtime)
    gate = runtime._subagent_materialization
    prepare(runtime, [req], running=[object()])
    raw_free = gate.last_decision["reserved_tokens"] + gate._price(gate.pending[req.rid])
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: raw_free
    monkeypatch.setattr(gate, "_outstanding_inputs", lambda *args, **kwargs: (1, 0, 1))
    prepare(runtime, [req], running=[object()], now=100.5)
    assert not gate.allows(runtime.visible[req.rid])
    assert runtime.counts["subagent_materialization_pressure_backoff_checks"] == 1
    assert runtime.counts["subagent_materialization_pressure_backoff_before_input_scan"] == 0


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
@pytest.mark.parametrize("wake", ("running", "idle", "raw_free", "starting"))
def test_v2_coalesces_capacity_event_censuses_only_while_running(runtime, wake):
    req = child(runtime)
    gate = runtime._subagent_materialization
    key = runtime.visible[req.rid]
    prepare(runtime, [req], running=[object()])
    assert not gate.allows(key)

    # A finished backup becomes reclaimable while raw free remains at zero.
    tree = runtime._native_cache.tree_core
    duplicate = tree._node_arena[1]
    full = duplicate.component_data[0]
    full.value = full.host_value
    tree.full_host_duplicates[duplicate.id] = duplicate
    if wake == "raw_free":
        runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 1000
    gate.capacity_changed()
    gate.prepare(
        [req], running_batch=NS(reqs=[] if wake == "idle" else [object()]),
        adder=None, now=100.01, _starting=wake == "starting",
    )
    if wake == "running":
        assert not gate.allows(key)
        assert runtime.counts["subagent_materialization_complete_censuses"] == 1
        prepare(runtime, [req], running=[object()], now=100.4)
    assert gate.allows(key)
    assert runtime.counts["subagent_materialization_complete_censuses"] == (
        1 if wake == "raw_free" else 2
    )


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
def test_census_bound_holds_new_work_and_preserves_native_commitments(runtime):
    selected = child(runtime, "selected")
    native = runtime._native_cache
    gate = runtime._subagent_materialization
    gate.max_nodes = gate.max_candidates = 1
    native.token_to_kv_pool_allocator.available_size = lambda: 1000
    prepare(runtime, [selected], running=[object()])
    assert gate.allows(runtime.visible[selected.rid])

    first, second = child(runtime, "first"), child(runtime, "second")
    root, _ = new_root(runtime, "arrival")
    native.token_to_kv_pool_allocator.available_size = lambda: 0
    gate.prepare(
        [first, second, root], running_batch=NS(reqs=[]),
        adder=NS(can_run_list=[selected]), now=101.,
    )
    decision = gate.last_decision
    assert gate.last_observation.reason == "native_node_census_bound"
    assert gate.last_observation.available_tokens == 0
    assert [record["request_id"] for record in decision["candidates"]] == [first.rid]
    assert decision["committed_initial_request_ids"] == [selected.rid]
    assert decision["outstanding_input_tokens"] > 0
    assert decision["allowed"] == [selected.rid]
    assert set(decision["held"]) == {first.rid, second.rid, root.rid}
    assert decision["progress_exception"] is None
    assert runtime.counts["subagent_materialization_observation_fallbacks"] == 0


@pytest.mark.parametrize("runtime", (("pressure_v2", "full_native"),), indirect=True)
def test_real_native_growth_keeps_indexed_credit_available_for_admission(runtime, full_native):
    from test_beliefkv_terminal_capacity import insert_session

    native, allocator, writes, loads, _ = full_native
    pool = native.host_pool_group.entry_map["kv"].host_pool
    pool.size = 12
    occupied = 0
    pool.available_size = lambda: pool.size - occupied
    donor, generation = insert_session(native, list(range(8)), "mirror")
    native.prepare_beliefkv_spawn_full(session_id="mirror", session_generation=generation)
    native.writing_check(finish_count=1)
    occupied += 8
    cold, generation = insert_session(native, list(range(20, 24)), "cold")
    native.prepare_beliefkv_spawn_full(session_id="cold", session_generation=generation)
    native.writing_check(finish_count=1)
    occupied += 4
    native._demote(cold.id, {0: 0})
    native.release_radix_session("cold")
    tree = native.tree_core
    gate = runtime._subagent_materialization
    gate.max_nodes = 3
    assert observe_materialization_headroom(native, max_nodes=gate.max_nodes).census_complete
    for start in (100, 200, 300, 400):
        insert_session(native, list(range(start, start + 14)), f"device-{start}")
    assert len(tree._node_arena) > gate.max_nodes
    assert allocator.available_size() == 0
    assert tree.full_host_duplicates[donor.id] is donor
    assert cold in tree.evictable_host_leaves and cold.component_data[0].session_ref == 0
    full = donor.component_data[0]
    host_value, device_value = full.host_value, full.value
    cold_host = cold.component_data[0].host_value
    arena = dict(tree._node_arena)
    mirrors = dict(tree.full_host_duplicates)

    runtime._agent_admission.policy.buffer_tokens = 1
    req, _ = new_root(runtime, "arrival", tokens=8)
    prepare(runtime, [req], running=[object()])
    assert not gate.last_observation.census_complete
    assert gate.last_observation.reclaimable_host_tokens == 12
    assert gate.last_decision["progress_exception"] is None
    assert gate.allows(runtime.visible[req.rid])
    assert full.host_value is host_value and full.value is device_value
    assert cold.component_data[0].host_value is cold_host and cold.component_data[0].value is None
    assert tree._node_arena == arena and tree.full_host_duplicates == mirrors
    assert allocator.available_size() == pool.available_size() == 0
    assert req.kv.req_pool_idx is None and req.kv.kv_allocated_len == 0
    assert len(writes) == 2 and not loads


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
@pytest.mark.parametrize("reason", ("invalid_pool_geometry", "native_host_accounting_mismatch"))
def test_unknown_headroom_preserves_original_eligibility_including_outside_window(runtime, reason):
    first, second = child(runtime, "first"), child(runtime, "second")
    root, _ = new_root(runtime, "arrival")
    native = runtime._native_cache
    gate = runtime._subagent_materialization
    gate.max_candidates = 1
    if reason == "invalid_pool_geometry":
        native.token_to_kv_pool_allocator.available_size = lambda: -1
    else:
        native.host_pool_group.entry_map["kv"].host_pool.available_size = lambda: 1
    prepare(runtime, [first, second, root], running=[object()])
    assert len(gate.last_decision["candidates"]) == 1
    assert set(gate.last_decision["allowed"]) == {first.rid, second.rid, root.rid}
    assert not gate.last_observation.observable and gate.last_observation.reason == reason
    assert runtime.counts["subagent_materialization_observation_fallbacks"] == 1


def test_admitted_or_reused_context_is_never_reclassified_as_new(runtime):
    req = child(runtime)
    gate, a = runtime._subagent_materialization, runtime._agent_admission
    prepare(runtime, [req])
    runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    req.finished = lambda: True
    req.output_ids.append(7)
    runtime.on_batch_completed(NS(reqs=[req]))
    successor = request("next-request", "child", tokens=110, workflow="wf")
    successor.output_ids.clear()
    successor.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
    successor.finished = lambda: False
    runtime.register_visible_request(successor)
    assert successor.rid not in gate.pending
    assert a.policy.programs["child"].steps == 2
    assert gate.allows(runtime.visible[successor.rid])


def test_workflow_end_releases_all_materialization_metadata(runtime):
    req = child(runtime)
    prepare(runtime, [req])
    runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    runtime.on_events((observed(10, Kind.WORKFLOW_END),))
    gate = runtime._subagent_materialization
    assert not gate.pending and not gate.materialized and not gate.progress_path


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("policy", ("pressure_v1", "pressure_v2"))
def test_explicit_candidate_never_leaks_into_other_arms(mode, policy):
    result = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "BELIEFKV_SUBAGENT_MATERIALIZATION": policy,
    }, mode)
    assert result["BELIEFKV_SUBAGENT_MATERIALIZATION"] == (
        policy if mode == "full" else "off"
    )


@pytest.mark.parametrize("value", ("1", "unknown"))
def test_configuration_refuses_ambiguous_materialization_policy(value):
    with pytest.raises(ValueError, match="subagent materialization"):
        arm_environment({"BELIEFKV_SUBAGENT_MATERIALIZATION": value}, "full")


def test_default_and_paper_environment_keep_materialization_disabled():
    assert arm_environment({}, "full")["BELIEFKV_SUBAGENT_MATERIALIZATION"] == "off"
    for policy in ("pressure_v1", "pressure_v2"):
        assert paper_environment({
            "BELIEFKV_SUBAGENT_MATERIALIZATION": policy,
        }, "thunderagent_acting_decay")["BELIEFKV_SUBAGENT_MATERIALIZATION"] == "off"


@pytest.mark.parametrize("host_gb", (None, 200, 400))
def test_prepared_controls_only_change_materialization_and_match_the_same_capacity(host_gb):
    previous = {
        "order": ["full"], "common_environment": {
            "AGENT_ADMISSION": "thunderagent_reference", "EXECUTION_FRONTIER_RESTORE": "0",
            "HICACHE_SIZE_GB": "400", "HOST_NUMA_MEMORY_POLICY": "interleave",
            "MODEL_CACHE_LAYOUT": "full_only", "HOST_NUMA_MEMORY_NODES": "0,1",
            "HOST_NUMA_PLACEMENT_CHECK": "diagnostic",
        },
        "workflow_arrivals": {"unchanged": True},
        "private_analysis_cohort": {"unchanged": True},
    }
    result = configurations(previous, host_gb=host_gb)
    assert "BELIEFKV_SUBAGENT_MATERIALIZATION" not in previous["common_environment"]
    for label, policy in (("control", "off"), ("pressure", "pressure_v1")):
        config = result[label]
        assert config["common_environment"]["BELIEFKV_SUBAGENT_MATERIALIZATION"] == policy
        assert config["common_environment"]["HICACHE_SIZE_GB"] == str(host_gb or 400)
        assert config["workflow_arrivals"] == previous["workflow_arrivals"]
        assert config["private_analysis_cohort"] == previous["private_analysis_cohort"]
        assert not config["gpu_execution"]["registered"]


@pytest.mark.parametrize("host_gb", (True, 0, -1, 1025, "200"))
def test_invalid_pressure_configuration_cannot_be_prepared(host_gb):
    with pytest.raises(ValueError, match="Host capacity"):
        configurations({"order": ["full"], "common_environment": {}}, host_gb=host_gb)
