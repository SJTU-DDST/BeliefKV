import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.experiments.paper_baselines import DISABLED_POLICIES, isolated_environment
from beliefkv.experiments.source_provenance import sha256_file
from scripts import qualify_agentix_runtime as qualifier


def document():
    return json.loads((
        qualifier.ROOT / "configs/baselines/agentix_native_qualification_20261025.json"
    ).read_bytes())


def runtime_state():
    config = document()
    return {
        **dict.fromkeys(DISABLED_POLICIES, False),
        "physical_disabled": False, "agent_admission": None,
        "internal_comparison_mode": "scheduler_only",
        "paper_baseline": {
            "policy": "agentix_atlas_paper_native", "program_identity": "root_workflow_id",
            "future_information_used": False, "native_capacity_authority_preserved": True,
            "declared_config": {
                key: value for key, value in config["parameters"].items()
                if key not in ("context_limit", "running_limit", "decode_growth_tokens")
            },
            **{key: config["parameters"][key] for key in (
                "context_limit", "running_limit", "decode_growth_tokens",
            )},
            "qualification": {"purpose": config["purpose"], "max_requests": 4},
            "complete_baseline": False, "gpu_llm_qualified": False,
            "calls": {
                "native_calls": 0, "terminal_calls_waiting_for_executor": 0,
                "arrivals_waiting_for_program_completion": 0,
                "counts": {"registered": 4, "completed": 4},
            },
            "policy_state": {"pending_calls": 0, "pending_executor_launches": 0},
            "execution": {
                "worker_forward_hook_installed": True, "native_scheduler_bound": True,
                "worker_forward_failed": False, "counts": {"native_worker_forwards": 32},
                "executor": {
                    "pending_launches": 0, "open_forward": False,
                    "counts": {"launched": 32, "accounted": 32}, "measured_executor_ms": 100.,
                    "gpu_synchronization_for_accounting": False,
                    "dropped_or_estimated_executor_intervals": 0,
                },
            },
            "swap": {
                "pending_requests": 0, "paused_host_pins": 0,
                "counts": {"paused_requests": 2, "resumed_requests": 2, "demoted_full_tokens": 64},
            },
            "native_scheduling": {
                "native_selection_hook_installed": True,
                "preemption": {"pending_selection": None, "counts": {"committed_rounds": 2}},
            },
        },
    }


def test_bounded_runtime_requires_observed_native_execution_and_lossless_resumption():
    qualifier.validate_runtime(runtime_state(), document())


@pytest.mark.parametrize("path,value", (
    (("physical_disabled",), True),
    (("resident_first",), True),
    (("paper_baseline", "complete_baseline"), True),
    (("paper_baseline", "gpu_llm_qualified"), True),
    (("paper_baseline", "future_information_used"), True),
    (("paper_baseline", "declared_config", "decode_steps_per_schedule"), 16),
    (("paper_baseline", "running_limit"), 24),
    (("paper_baseline", "calls", "counts", "completed"), 3),
    (("paper_baseline", "calls", "counts", "cancelled"), 1),
    (("paper_baseline", "calls", "native_calls"), 1),
    (("paper_baseline", "execution", "native_scheduler_bound"), False),
    (("paper_baseline", "execution", "executor", "counts", "accounted"), 31),
    (("paper_baseline", "execution", "executor", "measured_executor_ms"), float("nan")),
    (("paper_baseline", "execution", "executor", "gpu_synchronization_for_accounting"), True),
    (("paper_baseline", "swap", "counts", "resumed_requests"), 0),
    (("paper_baseline", "swap", "counts", "demoted_full_tokens"), 0),
    (("paper_baseline", "swap", "paused_host_pins"), 1),
    (("paper_baseline", "native_scheduling", "preemption", "counts", "committed_rounds"), 0),
))
def test_incomplete_misconfigured_or_overclaimed_runtime_cannot_qualify(path, value):
    state = runtime_state()
    current = state
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    with pytest.raises(ValueError, match="not verified"):
        qualifier.validate_runtime(state, document())


def swap_report():
    return {
        "status": "native_kv_swap_interface_qualified",
        "native_engine": {"actual_tree": "tree", "sha256": "patch"},
        **dict.fromkeys(qualifier.SWAP_PROPERTIES, True),
        "native_prefill_consumer": {
            "native_prefill_admitted": True, "native_extend_allocation_tokens": 1,
            "checkpoint_tokens": 18, "uncomputed_fixture_tail_returned": True,
        },
        "whole_llm_scheduler_or_baseline_qualified": False, "performance_accepted": False,
        "source_sha256": {
            name: sha256_file(qualifier.ROOT / name) for name in (
                "scripts/qualify_agentix_swap.py", "beliefkv/runtime/agentix_swap.py",
                "beliefkv/runtime/paper_prefix_transfers.py",
            )
        },
    }


def test_native_byte_evidence_is_bound_to_source_and_never_a_performance_result():
    qualifier.validate_swap(swap_report(), {"engine_tree": "tree", "native_patch_sha256": "patch"})


@pytest.mark.parametrize("field", qualifier.SWAP_PROPERTIES)
def test_each_native_byte_and_owner_property_is_required(field):
    report = swap_report()
    del report[field]
    with pytest.raises(ValueError, match="missing, stale or overclaimed"):
        qualifier.validate_swap(report, {"engine_tree": "tree", "native_patch_sha256": "patch"})


def test_plain_swap_evidence_cannot_qualify_native_session_transport():
    evidence = swap_report()
    registration = {"engine_tree": "tree", "native_patch_sha256": "patch"}
    with pytest.raises(ValueError, match="missing, stale or overclaimed"):
        qualifier.validate_swap(evidence, registration, require_native_sessions=True)
    evidence.update(
        request_transport="full_prompt_native_radix",
        native_session_generation_preserved=True, native_session_anchors_preserved=True,
    )
    qualifier.validate_swap(evidence, registration, require_native_sessions=True)


@pytest.mark.parametrize("field", (
    "native_prefill_admitted", "native_extend_allocation_tokens",
    "checkpoint_tokens", "uncomputed_fixture_tail_returned",
))
def test_manual_lock_or_incomplete_native_consumer_cannot_qualify_swap(field):
    evidence = swap_report()
    del evidence["native_prefill_consumer"][field]
    with pytest.raises(ValueError, match="missing, stale or overclaimed"):
        qualifier.validate_swap(evidence, {"engine_tree": "tree", "native_patch_sha256": "patch"})


def test_probe_selection_has_enough_decode_work_for_actual_preemption():
    index = {
        "too_short": {"prompt_tokens": 100000, "output_tokens": 2},
        "too_long": {"prompt_tokens": 100000, "output_tokens": 1000},
        "valid": {"prompt_tokens": 100000, "output_tokens": 18},
        "more_output": {"prompt_tokens": 100000, "output_tokens": 20},
    }
    assert qualifier.select_probe(index) == "valid"
    with pytest.raises(ValueError, match="no bounded"):
        qualifier.select_probe({"only": index["too_short"]})


def test_qualification_environment_keeps_policy_isolation_and_declares_only_smaller_running_limit(tmp_path):
    common = {
        "MAX_RUNNING_REQUESTS": "24", "SERVER_RANDOM_SEED": "21", "SAMPLING_SEED": "21",
        "PYTHONHASHSEED": "21", "HICACHE_SIZE_GB": "200", "HOST_SPLIT": "auto",
        "MODEL_CONTEXT_TOKENS": "262144", "JOIN_PREFETCH_TRIGGER": "observed_eos",
        "PREFILL_SCHEDULING_POLICY": "workflow", "TOOL_RESTORE_MODE": "execution",
        "RESTORE_TIERS": "complete", "EXECUTION_FRONTIER_RESTORE": "0",
        "MODEL_CACHE_LAYOUT": "full_only", "MODEL_PATH": "model",
    }
    registration = {
        "reference_environment": common,
        "policy_relative_path": "configs/baselines/agentix_native_qualification_20261025.json",
        "policy_document": document(), "engine_checkout": str(tmp_path / "engine"), "port": 19931,
    }
    settings, flags, socket = qualifier.qualification_environment(registration, tmp_path)
    assert settings["MAX_RUNNING_REQUESTS"] == "1"
    assert common["MAX_RUNNING_REQUESTS"] == "24"
    assert settings["BELIEFKV_PAPER_BASELINE"] == "agentix_atlas_native_qualification"
    assert settings["BELIEFKV_AGENTIX_QUALIFICATION_ONLY"] == "1"
    assert settings["BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY"] == "0"
    assert settings["BELIEFKV_ENABLE_RESIDENT_FIRST"] == "0"
    assert "--enable-beliefkv-admission" in flags and socket is not None
    assert "BELIEFKV_PAPER_BASELINE" not in isolated_environment({
        **common, "BELIEFKV_PAPER_BASELINE": "unrelated_method",
    })


@dataclass
class Captured:
    request_id: str = "source"
    identity: tuple = ("original", "agent", "context", 0)
    extra_key: str = "original"
    cache_salt: str = "original"


@pytest.mark.parametrize("fail", (False, True))
def test_probe_submits_three_concurrent_contexts_and_drains_every_task(tmp_path, monkeypatch, fail):
    state = NS(active=0, peak=0, drained=False, namespaces=set())

    class Corpus:
        def __init__(self, *_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, _request):
            return Captured()

    class Backend:
        def __init__(self, corpus, _client, emit, *, observe_request, **_kwargs):
            self.corpus, self.emit_events, self.observe = corpus, emit, observe_request
            self.all_started = asyncio.Event()

        async def emit(self, event):
            self.emit_events((event,))

        async def execute(self, request, submit):
            captured = self.corpus.get(request)
            assert captured.identity[0] == captured.extra_key == captured.cache_salt
            state.namespaces.add(captured.extra_key)
            state.active += 1
            state.peak = max(state.peak, state.active)
            try:
                if "parallel_" in request:
                    if state.active == 3:
                        self.all_started.set()
                    await self.all_started.wait()
                    if fail and request.endswith("_1"):
                        raise RuntimeError("fixture request failed")
                await asyncio.sleep(0)
                self.observe({"request_id": request, "exact_tokens_verified": True})
            finally:
                state.active -= 1

        async def drain(self):
            state.drained = True

    monkeypatch.setattr(qualifier, "AgentXTokenCorpus", Corpus)
    monkeypatch.setattr(qualifier, "CausalHTTPBackend", Backend)
    registration = {"tokens": ".", "prepared": ".", "probe_request": "source", "port": 19931}
    if fail:
        with pytest.raises(RuntimeError, match="fixture request"):
            asyncio.run(qualifier.probe(registration, tmp_path))
    else:
        result = asyncio.run(qualifier.probe(registration, tmp_path))
        assert result["requests"] == 4 and result["token_contract_verified"]
        assert result["model_kv_numerical_equivalence_verified"] is False
    assert state.peak == 3 and state.active == 0 and state.drained
    assert len(state.namespaces) == 3


def test_watch_never_queries_resources_or_starts_gpu_work_while_predecessor_is_live(tmp_path, monkeypatch):
    class StoppedBeforeGPU(Exception):
        pass

    run = tmp_path / "run"
    run.mkdir()
    registration = {
        "run_root": str(run), "port": 19931, "after_run_root": str(tmp_path / "earlier"),
        "source_sha256": {},
    }
    path = run / "registration.json"
    path.write_text(json.dumps(registration))
    cleanup = []
    monkeypatch.setattr(qualifier, "check_registration", lambda _reg: None)
    monkeypatch.setattr(qualifier, "predecessor_finished", lambda _run: False)
    monkeypatch.setattr(qualifier.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(qualifier.controls.fresh, "check_hold", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(qualifier.controls, "owned_processes", lambda _rows: [])
    monkeypatch.setattr(qualifier.controls, "cleanup", lambda _run, rows, _reason: cleanup.append(rows))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GPU work must wait for actual predecessor exit and cleanup")

    monkeypatch.setattr(qualifier.controls.qualified, "resources", forbidden)
    monkeypatch.setattr(qualifier.subprocess, "Popen", forbidden)

    def stop_wait(_duration):
        raise StoppedBeforeGPU()

    monkeypatch.setattr(qualifier.time, "sleep", stop_wait)
    qualifier.watch(path)
    summary = json.loads((run / "summary.json").read_bytes())
    assert summary["error_type"] == "StoppedBeforeGPU"
    assert summary["performance_accepted"] is False and cleanup == [[]]


@pytest.mark.parametrize("change", ("none", "manifest", "source", "input", "predecessor", "identity"))
def test_registration_pins_source_inputs_and_the_exact_predecessor_process(tmp_path, monkeypatch, change):
    config = document()
    root, prepared, tokens, predecessor = (
        tmp_path / name for name in ("root", "prepared", "tokens", "predecessor")
    )
    for path in (root, prepared, tokens, predecessor):
        path.mkdir()
    (root / "source.py").write_text("immutable source")
    (root / "policy.json").write_text(json.dumps(config))
    (prepared / "preparation.json").write_text("{}")
    (tokens / "manifest.json").write_text("{}")
    (predecessor / "registration.json").write_text("{}")
    identity = {"pid": 1, "start_ticks": "123"}
    (predecessor / "supervisor.json").write_text(json.dumps(identity))
    registration = {
        "policy_relative_path": "policy.json", "policy_document": config,
        "source_sha256": {
            name: sha256_file(root / name) for name in ("source.py", "policy.json")
        },
        "prepared": str(prepared), "tokens": str(tokens), "after_run_root": str(predecessor),
        "preparation_sha256": sha256_file(prepared / "preparation.json"),
        "token_manifest_sha256": sha256_file(tokens / "manifest.json"),
        "predecessor_registration_sha256": sha256_file(predecessor / "registration.json"),
        "predecessor_supervisor": identity,
    }
    monkeypatch.setattr(qualifier, "ROOT", root)
    monkeypatch.setattr(qualifier, "SOURCES", ("source.py",))
    if change == "manifest":
        registration["source_sha256"].pop("source.py")
    elif change == "source":
        (root / "source.py").write_text("changed")
    elif change == "input":
        (tokens / "manifest.json").write_text('{"changed":true}')
    elif change == "predecessor":
        (predecessor / "registration.json").write_text('{"changed":true}')
    elif change == "identity":
        (predecessor / "supervisor.json").write_text('{"pid":2,"start_ticks":"123"}')
    if change == "none":
        qualifier.check_registration(registration)
    else:
        with pytest.raises(ValueError, match="changed|incomplete|differs"):
            qualifier.check_registration(registration)
