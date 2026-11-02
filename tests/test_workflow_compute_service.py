from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.runtime import workflow_compute_service as service
from tests.test_short_continuation_probe import case, full_native
from tests.test_workflow_work_oracle import add_serial_graphs, oracle_file, source
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "configs/scheduling/short_workflow_compute_reference_20261102.json"


def settings(**changes):
    return {
        **json.loads(PROFILE.read_bytes()),
        "configuration_sha256": hashlib.sha256(PROFILE.read_bytes()).hexdigest(),
        "configuration_path": str(PROFILE), **changes,
    }


def configured_file(tmp_path, **changes):
    path = tmp_path / "compute.json"
    raw = json.loads(PROFILE.read_bytes())
    raw.update(changes)
    path.write_text(json.dumps(raw))
    return {
        service.CONFIG_ENV: str(path),
        service.HASH_ENV: hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def test_default_register_is_inert_before_importing_native_hooks(monkeypatch):
    monkeypatch.delenv(service.CONFIG_ENV, raising=False)
    monkeypatch.delenv(service.HASH_ENV, raising=False)
    assert service.configuration() is None
    assert service.register() is None


@pytest.mark.parametrize("change", (
    {"schema_version": True}, {"maximum_checks": 9}, {"maximum_checks": True},
    {"soft_budget_ms": float("nan")}, {"minimum_work_ratio": 1.},
    {"burst_decode_steps": 17}, {"total_decode_steps": 129},
    {"burst_wall_ms": 501}, {"focus_wall_ms": 5001},
    {"burst_decode_steps": 8, "total_decode_steps": 4},
    {"burst_wall_ms": 200, "focus_wall_ms": 100},
    {"selection": "learned"}, {"unrecognized": 1},
))
def test_profile_rejects_unbounded_or_undeclared_controls(tmp_path, change):
    with pytest.raises(ValueError):
        service.configuration(configured_file(tmp_path, **change))


def test_profile_requires_its_exact_hash(tmp_path):
    environment = configured_file(tmp_path)
    environment[service.HASH_ENV] = "wrong"
    with pytest.raises(ValueError, match="changed"):
        service.configuration(environment)


def test_cached_model_work_does_not_scan_the_future_dag(tmp_path):
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path))
    before = dict(oracle.counts)
    assert oracle.cached_model_work("wf") == 47.
    assert oracle.counts == before
    assert not oracle._path_cache
    oracle.submit("wf", "started", "one", "child", "ctx", range(500))
    oracle.submit("wf", "generated", "one", 10)
    assert oracle.cached_model_work("wf") == 37.
    oracle.error = "unavailable"
    assert oracle.cached_model_work("wf") is None


@pytest.fixture
def component(case, monkeypatch):
    f = case
    clock = [10.]
    monkeypatch.setattr(service, "time", NS(
        monotonic=lambda: clock[0], thread_time=lambda: 0., time=lambda: 0.,
    ))
    controller = service.WorkflowComputeService(f.runtime, settings(soft_budget_ms=1.))
    scheduler = NS(
        beliefkv_runtime=f.runtime, tree_cache=f.cache,
        waiting_queue=f.queue, chunked_req=f.owner,
    )
    f.runtime._workflow_compute_service = controller
    f.runtime._opportunity_writer = Mock()
    f.controller, f.scheduler, f.clock = controller, scheduler, clock
    return f


def cofill(f):
    from sglang.srt.managers.schedule_policy import PrefillAdder

    return f.controller.cofill(
        PrefillAdder.add_chunked_req, f.adder, f.owner, f.scheduler, f.running,
    )


def unlock_short(f):
    if f.short.last_node is not None and f.short.lock_receipt is not None:
        f.cache.dec_lock_ref(f.short.last_node, f.short.lock_receipt)
        f.short.lock_receipt = None


def test_real_native_cofill_commits_once_preserves_owner_and_allocates_no_copy(component):
    f = component
    before_free = f.allocator.available_size()
    try:
        assert cofill(f) is f.owner
        assert f.adder.can_run_list == [f.short, f.owner]
        assert [req.extend_range.length for req in f.adder.can_run_list] == [2, 30]
        assert f.scheduler.waiting_queue == []
        assert f.adder.new_chunked_req is None
        assert f.controller.focus.request is f.short
        assert f.controller.counts["native_cofills_committed"] == 1
        assert not f.writes and not f.loads and not f.commits
        assert f.allocator.available_size() == before_free
        assert not f.controller.status()["future_information_used"]
    finally:
        unlock_short(f)


@pytest.mark.parametrize("change", ("cold", "inactive", "growth", "request_rows", "cpu_budget"))
def test_unsupported_cofill_falls_back_to_full_native_owner_budget(component, change, monkeypatch):
    f = component
    if change == "cold":
        f.leaf.component_data[0].host_value = f.leaf.component_data[0].value
        f.leaf.component_data[0].value = None
    elif change == "inactive":
        f.runtime._agent_admission.policy.programs["short"].active = False
    elif change == "growth":
        f.short.sampling_params.max_new_tokens = 32
    elif change == "request_rows":
        f.cache.req_to_token_pool.free_slots.clear()
    else:
        calls = iter((0., 1.))
        monkeypatch.setattr(service.time, "thread_time", lambda: next(calls))
    assert cofill(f) is f.owner
    assert f.adder.can_run_list == [f.owner]
    assert f.owner.extend_range.length == 32
    assert f.scheduler.waiting_queue == [f.short]
    assert f.controller.focus is None
    assert not f.writes and not f.loads


def test_actual_queue_admission_cannot_be_repeated_after_early_commit(component):
    f = component
    try:
        cofill(f)
        assert f.short not in f.scheduler.waiting_queue
        assert sum(req is f.short for req in f.adder.can_run_list) == 1
        assert f.short.lock_receipt is not None
    finally:
        unlock_short(f)


def test_decode_deferral_is_bounded_and_owner_prefill_rearms_it(component):
    f = component
    try:
        cofill(f)
        running = NS(reqs=[f.short], is_prefill_only=False)
        f.controller.before_selection(running)
        for _ in range(f.controller.settings["burst_decode_steps"]):
            assert f.controller.defer_prefill(f.scheduler)
        assert not f.controller.defer_prefill(f.scheduler)
        f.controller.prefill_selected(NS(reqs=[f.owner]))
        assert f.controller.defer_prefill(f.scheduler)
        assert f.controller.focus.total_deferrals == 9
        assert running.reqs == [f.short]
    finally:
        unlock_short(f)


def test_empty_unrelated_or_prefill_only_running_batch_is_never_forced_to_decode(component):
    f = component
    try:
        cofill(f)
        for running in (
            NS(reqs=[], is_prefill_only=False),
            NS(reqs=[f.owner], is_prefill_only=False),
            NS(reqs=[f.short], is_prefill_only=True),
        ):
            f.controller.before_selection(running)
            assert not f.controller.defer_prefill(f.scheduler)
        assert f.controller.focus.total_deferrals == 0
    finally:
        unlock_short(f)


@pytest.mark.parametrize("change", ("finished", "session", "inactive", "deadline", "total_budget", "no_prefill"))
def test_decode_focus_ends_or_stops_deferral_on_liveness_and_budget_changes(component, change):
    f = component
    try:
        cofill(f)
        f.controller.before_selection(NS(reqs=[f.short], is_prefill_only=False))
        if change == "finished":
            f.short.to_finish = object()
        elif change == "session":
            f.short.session_generation += 1
        elif change == "inactive":
            f.runtime._agent_admission.policy.programs["short"].active = False
        elif change == "deadline":
            f.clock[0] += 3.
        elif change == "total_budget":
            f.controller.focus.total_deferrals = 64
        else:
            f.scheduler.chunked_req = None
        assert not f.controller.defer_prefill(f.scheduler)
    finally:
        unlock_short(f)


def test_burst_wall_deadline_requires_prefill_progress_before_more_deferrals(component):
    f = component
    try:
        cofill(f)
        f.controller.before_selection(NS(reqs=[f.short], is_prefill_only=False))
        f.clock[0] += .21
        assert not f.controller.defer_prefill(f.scheduler)
        f.controller.prefill_selected(NS(reqs=[f.owner]))
        assert f.controller.defer_prefill(f.scheduler)
    finally:
        unlock_short(f)


def test_ranked_selection_reads_only_cached_work_and_preserves_join_override(component):
    f = component
    owner_key = f.runtime.visible["long"]
    short_key = replace(f.runtime.visible["short"], root_workflow_id="other")
    f.runtime.visible["short"] = short_key
    f.runtime._agent_admission._work = NS(cached_model_work=Mock(side_effect=lambda wf: 100. if wf == "wf" else 10.))
    controller = service.WorkflowComputeService(f.runtime, settings(selection="oracle_model_work"))
    row = {
        "key": asdict(short_key), "observed_ready_join_parent": False,
    }
    assert controller.choose(f.owner, [row]) == (row, "shorter_workflow")
    assert controller.counts["cached_model_work_reads"] == 2
    controller.work.cached_model_work.side_effect = lambda _wf: None
    assert controller.choose(f.owner, [row])[0] is None
    row["observed_ready_join_parent"] = True
    assert controller.choose(f.owner, [row]) == (row, "protected_join")
    assert f.runtime.visible["long"] == owner_key


def test_chunk_hook_scope_is_cleared_after_native_exception():
    controller = NS(prefill_selected=Mock())
    scheduler = NS(beliefkv_runtime=NS(_workflow_compute_service=controller))
    original = Mock(side_effect=RuntimeError("native failure"))
    from unittest.mock import patch
    with patch.object(service, "_controller", return_value=controller):
        with pytest.raises(RuntimeError, match="native failure"):
            service.raw_prefill(original, scheduler, running_batch=object())
    assert service.SCOPE.get() is None


def test_controller_rejects_paper_or_missing_oracle(component):
    f = component
    f.runtime._paper_baseline = object()
    with pytest.raises(ValueError, match="Full"):
        service.WorkflowComputeService(f.runtime, settings())
    f.runtime._paper_baseline = None
    with pytest.raises(ValueError, match="oracle"):
        service.WorkflowComputeService(f.runtime, settings(selection="oracle_model_work"))


def test_actual_native_scheduler_selects_whole_decode_batch_then_returns_to_prefill(component, monkeypatch):
    from types import MethodType
    from tests.test_native_prefill_decode_cadence import native_scheduler

    f = component
    try:
        cofill(f)
        scheduler, decode, prefill = native_scheduler(interval=0, running_empty=False, chunk=f.owner)
        scheduler.stash_chunked_request = Mock()
        peer = object()
        decode.reqs = [f.short, peer]
        scheduler.waiting_queue = []
        scheduler._beliefkv_before_batch_selection = lambda running, _last: (
            f.controller.before_selection(running) or False
        )
        scheduler._should_defer_prefill = MethodType(
            lambda current: service.should_defer_prefill(lambda _scheduler: False, current),
            scheduler,
        )
        monkeypatch.setattr(service, "_controller", lambda _scheduler: f.controller)
        for _ in range(f.controller.settings["burst_decode_steps"]):
            result = scheduler.get_next_batch_to_run(decode, None)
            assert result.batch_to_run is decode
            assert result.batch_to_run.reqs == [f.short, peer]
        assert scheduler.get_new_batch_prefill.call_count == 0
        assert scheduler.get_next_batch_to_run(decode, None).batch_to_run is prefill
        assert scheduler.get_new_batch_prefill.call_count == 1
        assert scheduler.chunked_req is f.owner
    finally:
        unlock_short(f)


def test_real_plugin_hook_wraps_the_native_chunk_admission_once(component, monkeypatch):
    from collections import defaultdict
    from sglang.srt.managers.schedule_policy import PrefillAdder
    from sglang.srt.managers.scheduler import Scheduler
    from sglang.srt.plugins.hook_registry import HookRegistry

    f = component
    monkeypatch.setenv(service.CONFIG_ENV, str(PROFILE))
    monkeypatch.setenv(service.HASH_ENV, hashlib.sha256(PROFILE.read_bytes()).hexdigest())
    monkeypatch.setattr(HookRegistry, "_hooks", defaultdict(list))
    monkeypatch.setattr(HookRegistry, "_patched", set())
    for cls, name in (
        (PrefillAdder, "add_chunked_req"), (Scheduler, "_beliefkv_before_batch_selection"),
        (Scheduler, "_should_defer_prefill"), (Scheduler, "_get_new_batch_prefill_raw"),
    ):
        monkeypatch.setattr(cls, name, getattr(cls, name))
    service.register()
    assert len(HookRegistry._hooks) == 4
    HookRegistry.apply_hooks()

    def native_raw(scheduler, *, running_batch):
        assert running_batch is f.running
        assert f.adder.add_chunked_req(scheduler.chunked_req) is f.owner
        return NS(reqs=list(f.adder.can_run_list)), running_batch

    try:
        batch, _running = service.raw_prefill(native_raw, f.scheduler, running_batch=f.running)
        assert batch.reqs == [f.short, f.owner]
        assert f.scheduler.waiting_queue == []
        assert service.SCOPE.get() is None
        assert f.controller.counts["native_cofills_committed"] == 1
    finally:
        unlock_short(f)


def test_protected_join_owner_keeps_its_full_chunk_budget(component, monkeypatch):
    f = component
    monkeypatch.setattr(f.runtime, "_ready_join_parent", lambda req, _key: req is f.owner)
    assert cofill(f) is f.owner
    assert f.adder.can_run_list == [f.owner]
    assert f.owner.extend_range.length == 32
    assert f.scheduler.waiting_queue == [f.short]


def test_fresh_native_refusal_keeps_the_short_request_queued_and_owner_progress(component, monkeypatch):
    from sglang.srt.managers.schedule_policy import AddReqResult

    f = component
    monkeypatch.setattr(f.adder, "add_one_req", Mock(return_value=AddReqResult.NO_TOKEN))
    assert cofill(f) is f.owner
    assert f.adder.can_run_list == [f.owner]
    assert f.owner.extend_range.length == 32
    assert f.scheduler.waiting_queue == [f.short]
    assert f.controller.focus is None


def test_ranked_native_cofill_uses_real_oracle_cache_for_another_workflow(component, tmp_path):
    from array import array
    from beliefkv.core.events import RuntimeEventKind as Kind
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.sampling.sampling_params import SamplingParams
    from test_beliefkv_terminal_capacity import insert_session
    from tests.test_sglang_v0520_runtime import event

    f = component
    # Replace the unused fixture member without increasing the real CPU pool.
    f.cache.release_radix_session(f.short.session_id)
    retired = f.cache.retire_beliefkv_child_session(
        session_id=f.short.session_id, session_generation=f.short.session_generation,
    )
    assert retired["freed_device_units"] == {0: 8}
    tokens = list(range(2001, 2011))
    leaf, generation = insert_session(f.cache, tokens[:8], "other")
    f.runtime.on_events((
        replace(event(4, Kind.WORKFLOW_START), workflow_id="other-wf"),
        replace(event(5, Kind.INVOCATION_CREATE,
                      invocation_id="other", context_id="other-ctx"), workflow_id="other-wf"),
    ))
    req = Req(
        rid="other", origin_input_text="", origin_input_ids=array("q", tokens),
        sampling_params=SamplingParams(max_new_tokens=2), session_id="other",
        beliefkv_metadata={
            "root_workflow_id": "other-wf", "invocation_id": "other",
            "context_id": "other-ctx", "context_epoch": 0,
        },
    )
    req.session_generation = generation
    assert f.runtime.register_visible_request(req)
    raw = source()
    raw["workflows"] = {"wf": {"total_work_units": 456, "requests": 1},
                        "other-wf": {"total_work_units": 210, "requests": 1}}
    raw["requests"] = {
        "long": {
            "workflow_id": "wf", "invocation_id": "long", "context_id": "ctx-long",
            "prompt_tokens": 256, "output_tokens": 2, "input_increment": 256,
            "predecessor_request_id": None,
        },
        "other": {
            "workflow_id": "other-wf", "invocation_id": "other", "context_id": "other-ctx",
            "prompt_tokens": 10, "output_tokens": 2, "input_increment": 10,
            "predecessor_request_id": None,
        },
    }
    add_serial_graphs(raw)
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    f.runtime._agent_admission._work = oracle
    f.controller = service.WorkflowComputeService(
        f.runtime, settings(selection="oracle_model_work", soft_budget_ms=1.),
    )
    f.scheduler.waiting_queue = [req]
    f.short, f.leaf = req, leaf
    try:
        assert cofill(f) is f.owner
        assert f.adder.can_run_list == [req, f.owner], dict(f.controller.counts)
        assert f.controller.focus.key.root_workflow_id == "other-wf"
        assert f.controller.status()["future_information_used"]
        assert not oracle.counts["path_evaluations"]
    finally:
        unlock_short(f)
        oracle.close()


@pytest.mark.parametrize("mode", ("full", "scheduler_only", "kv_only", "native_matched", "join_priority_only"))
def test_actual_experiment_environment_keeps_compute_service_full_only(tmp_path, mode):
    from beliefkv.experiments.internal_comparison import arm_environment
    from tests.test_workflow_work_oracle import configured

    common = {
        **configured(tmp_path), "MODEL_CACHE_LAYOUT": "full_only",
        **configured_file(tmp_path, selection="oracle_model_work"),
    }
    environment = arm_environment(common, mode)
    assert bool(environment[service.CONFIG_ENV]) is (mode == "full")
    assert bool(environment[service.HASH_ENV]) is (mode == "full")
    if mode != "full":
        assert environment["WORKFLOW_COMPUTE_SERVICE_CONFIG"] == ""
        assert environment["WORKFLOW_COMPUTE_SERVICE_CONFIG_SHA256"] == ""


def test_ranked_environment_requires_explicit_oracle_and_full_layout(tmp_path):
    from beliefkv.experiments.internal_comparison import arm_environment
    from tests.test_workflow_work_oracle import configured

    common = {
        **configured(tmp_path), "MODEL_CACHE_LAYOUT": "full_only",
        **configured_file(tmp_path, selection="oracle_model_work"),
    }
    for change in ({"MODEL_CACHE_LAYOUT": "hybrid"}, {"WORKFLOW_WORK_SOURCE": "learned"}):
        with pytest.raises(ValueError, match="compute service"):
            arm_environment({**common, **change}, "full")
